#!/usr/bin/env python3
"""Generate image assets for multimodal tasks using an OpenAI-compatible API.

Reads asset specs from task YAMLs and stage_5_assets.json to get generation
prompts.

Usage:
    python scripts/generate_images.py                      # All domains
    python scripts/generate_images.py --domain finance      # Single domain
    python scripts/generate_images.py --task fin-t13        # Single task
    python scripts/generate_images.py --dry-run             # List without generating
    python scripts/generate_images.py --model image-generation-model  # Different model

Requires IMAGE_API_KEY and IMAGE_API_BASE_URL unless --dry-run is used.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = PROJECT_ROOT / "multi_agent" / "tasks"

DEFAULT_MODEL = "image-generation-model"
RETRY_COUNT = 3
RETRY_DELAY = 5


def _call_image_api(
    prompt: str,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int = 300,
) -> dict:
    """Call an OpenAI-compatible image-generation endpoint."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _extract_image_data(data: dict) -> bytes | None:
    """Extract first image from an API response as bytes."""
    for choice in data.get("choices", []):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message", {})
        content = message.get("content", "")

        if isinstance(content, str):
            # Try markdown: ![...](data:image/...;base64,...)
            md = re.findall(r'!\[.*?\]\((data:image/[^)]+)\)', content)
            if md:
                uri = md[0]
                _, _, b64 = uri.partition(",")
                return base64.b64decode(b64)

            # Try plain data URI
            if content.strip().startswith("data:image/"):
                _, _, b64 = content.strip().partition(",")
                return base64.b64decode(b64)

        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    url_obj = part.get("image_url", {})
                    url_val = url_obj.get("url", "") if isinstance(url_obj, dict) else ""
                    if url_val.startswith("data:"):
                        _, _, b64 = url_val.partition(",")
                        return base64.b64decode(b64)
                inline = part.get("inline_data", {})
                if isinstance(inline, dict) and inline.get("data"):
                    return base64.b64decode(inline["data"])

    return None


def generate_image(
    prompt: str,
    out_path: Path,
    model: str,
    api_key: str,
    base_url: str,
) -> bool:
    """Generate an image and save to out_path. Returns True on success."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            data = _call_image_api(prompt, model, api_key, base_url)
            img_bytes = _extract_image_data(data)
            if img_bytes:
                out_path.write_bytes(img_bytes)
                return True
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
        except Exception as exc:
            if attempt < RETRY_COUNT:
                print(f"    Attempt {attempt} failed: {exc} — retrying...")
                time.sleep(RETRY_DELAY * attempt)
            else:
                print(f"    All {RETRY_COUNT} attempts failed: {exc}")
    return False


def find_image_assets() -> list[dict]:
    """Find all image assets declared in task YAMLs that don't exist yet."""
    import yaml

    assets = []
    for yf in sorted(TASKS_ROOT.rglob("*.yaml")):
        if "/assets/" in str(yf):
            continue
        data = yaml.safe_load(yf.read_text())
        if not data or "task_id" not in data:
            continue

        for asset in data.get("input_assets", []):
            if asset.get("asset_type") != "image":
                continue
            path = asset.get("path", "")
            resolved = yf.parent / path
            if resolved.exists():
                continue

            assets.append({
                "task_id": data["task_id"],
                "domain": data.get("domain", ""),
                "yaml_path": str(yf),
                "asset_path": path,
                "resolved_path": str(resolved),
                "description": asset.get("description", ""),
                "filename": path.split("/")[-1] if path else "",
            })
    return assets


def load_stage5_prompts(domain: str) -> dict[str, dict[str, str]]:
    """Load generation_prompts from stage_5_assets.json. Returns {task_id: {filename: prompt}}."""
    staging_root = PROJECT_ROOT / "scripts" / "_staging"
    batch_map = {"finance": "pilot_fin", "ecommerce": "pilot_ec"}
    batch = batch_map.get(domain, "v1")
    s5_path = staging_root / domain / batch / "stage_5_assets.json"
    if not s5_path.exists():
        return {}

    data = json.loads(s5_path.read_text())
    result: dict[str, dict[str, str]] = {}
    for tid, asset_list in data.items():
        if not isinstance(asset_list, list):
            continue
        by_file: dict[str, str] = {}
        for a in asset_list:
            fname = a.get("filename", "")
            prompt = a.get("generation_prompt", "")
            if fname and prompt:
                by_file[fname] = prompt
        result[tid] = by_file
    return result


def _build_prompt(description: str, filename: str, stage5_prompt: str) -> str:
    """Build the image generation prompt."""
    if stage5_prompt:
        return stage5_prompt

    # Fallback: build from description + filename
    ext_hint = ""
    if filename.endswith(".jpg") or filename.endswith(".jpeg"):
        ext_hint = "Generate a realistic photograph. "
    elif filename.endswith(".png"):
        ext_hint = "Generate a clear screenshot or digital image. "

    return (
        f"{ext_hint}"
        f"Create the following image: "
        f"{description}. "
        f"The image should look realistic and contain clearly readable text where applicable. "
        f"Use a clean, professional style. Do not include any watermarks, labels, or metadata."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate image assets for HarnessAudit-Bench")
    parser.add_argument("--domain", help="Only process this domain")
    parser.add_argument("--task", help="Only process this task ID")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true", help="List images without generating")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between API calls (seconds)")
    args = parser.parse_args()

    api_key = os.getenv("IMAGE_API_KEY")
    base_url = os.getenv("IMAGE_API_BASE_URL", "").strip()
    if (not api_key or not base_url) and not args.dry_run:
        print("ERROR: Set IMAGE_API_KEY and IMAGE_API_BASE_URL environment variables")
        sys.exit(1)

    assets = find_image_assets()
    if args.domain:
        assets = [a for a in assets if a["domain"] == args.domain]
    if args.task:
        assets = [a for a in assets if a["task_id"] == args.task]

    print(f"Found {len(assets)} image assets to generate")

    if args.dry_run:
        for a in assets:
            print(f"  [{a['task_id']}] {a['asset_path']}")
            print(f"    {a['description'][:100]}")
        return

    # Load stage5 prompts per domain
    prompt_cache: dict[str, dict] = {}
    for a in assets:
        domain = a["domain"]
        if domain not in prompt_cache:
            prompt_cache[domain] = load_stage5_prompts(domain)

    generated = 0
    failed = 0
    total = len(assets)

    for i, a in enumerate(assets, 1):
        tid = a["task_id"]
        domain = a["domain"]
        fname = a["filename"]
        out = Path(a["resolved_path"])

        # Get generation prompt
        task_prompts = prompt_cache.get(domain, {}).get(tid, {})
        stage5_prompt = task_prompts.get(fname, "")
        prompt = _build_prompt(a["description"], fname, stage5_prompt)

        print(f"  [{i}/{total}] {tid}: {fname}")

        ok = generate_image(prompt, out, args.model, api_key, base_url)
        if ok:
            size_kb = out.stat().st_size / 1024
            generated += 1
            print(f"    OK ({size_kb:.0f} KB)")
        else:
            failed += 1
            print(f"    FAILED")

        if i < total:
            time.sleep(args.delay)

    print(f"\nGenerated: {generated}, Failed: {failed}, Total: {total}")


if __name__ == "__main__":
    main()
