"""
灵犀墨语 多模态支持测试脚本
测试各模型是否支持本地图片输入。

注意：OpenAI-compatible chat.completions 的 image_url 字段不能直接读取本地路径，
所以这里会把本地图片转成 base64 data URL 后发送。
"""
import base64
import mimetypes
import os
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).with_name(".env"))

API_KEY = os.environ.get("LINGXIMOYU_API_KEY", "").strip()
BASE_URL = "https://api.lingximoyu.com/v1"

if not API_KEY:
    raise RuntimeError("请先在 .env 或环境变量中设置 LINGXIMOYU_API_KEY")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60, max_retries=0)

LOCAL_IMAGE_PATH = (
    Path(__file__).parent
    / "masp_bench/tasks/daily_life/housing/assets/dl-t6/kitchen_damage_photo_overview.png"
)
IMAGE_MAX_SIDE = 1280
IMAGE_JPEG_QUALITY = 85

MODELS = [
    "qwen3.5-plus",
    "gemini-3.1-pro-preview",
    "kimi-k2.6",
    "glm-5.1",
    "deepseek-v4-pro",
]


def local_image_to_data_url(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"找不到本地图片: {path}")

    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            width, height = image.size
            longest_side = max(width, height)
            if longest_side > IMAGE_MAX_SIDE:
                scale = IMAGE_MAX_SIDE / longest_side
                image = image.resize((int(width * scale), int(height * scale)))

            output = BytesIO()
            image.save(output, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
    except ImportError:
        mime, _ = mimetypes.guess_type(path.name)
        if not mime:
            mime = "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"


TEST_IMAGE_URL = local_image_to_data_url(LOCAL_IMAGE_PATH)


def short_error(error: Exception) -> str:
    err_msg = str(error)
    if len(err_msg) > 200:
        err_msg = err_msg[:200] + "..."
    return err_msg


def test_multimodal(model_name: str):
    print(f"\n{'='*50}")
    print(f"测试模型: {model_name}")
    print(f"{'='*50}")

    # --- Test 1: 纯文本 (baseline) ---
    try:
        r = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "回复OK两个字母即可"}],
            max_tokens=10,
        )
        print(f"  [纯文本]  ✅ 成功 → {r.choices[0].message.content.strip()}")
    except Exception as e:
        print(f"  [纯文本]  ❌ 失败 → {e}")

    # --- Test 2: 多模态 image_url ---
    try:
        r = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": TEST_IMAGE_URL},
                        },
                        {
                            "type": "text",
                            "text": "用一句话描述这张图片",
                        },
                    ],
                }
            ],
            max_tokens=100,
        )
        reply = r.choices[0].message.content.strip()
        print(f"  [图片]    ✅ 成功 → {reply[:80]}...")
    except Exception as e:
        print(f"  [图片]    ❌ 失败 → {short_error(e)}")

if __name__ == "__main__":
    print("灵犀墨语 本地图片多模态支持测试")
    print(f"Base URL: {BASE_URL}")
    print(f"API Key:  {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"Image Path: {LOCAL_IMAGE_PATH}")
    print(f"Image Payload: base64 data URL, max_side={IMAGE_MAX_SIDE}")

    for model in MODELS:
        test_multimodal(model)

    print(f"\n{'='*50}")
    print("测试完成。")
    print("[多模态] ✅ 说明该模型至少支持 base64 data URL 形式的本地图片输入。")
