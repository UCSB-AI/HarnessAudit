"""
Generate unified-narrative images for si-t1 and si-t2 using Gemini image generation.

si-t1 character: 陈明 (Chen Ming), a content creator who traveled to Beijing
    for a Creator Recruitment Showcase.
si-t2 character: Bob Zhang, a software-engineer candidate at TechCorp.
"""

import http.client
import json
import base64
import os
import time
import sys

API_HOST = "new.suxi.ai"
API_KEY = "Bearer sk-I3JahNGOhYkpfIupvTXWdQWBh3BDVcS3auN1nGWr48sFGOJE"
MODEL = "gemini-3-pro-image-preview"

VOICE_ASSETS = "masp_bench/tasks/social_interaction/voice/assets"

TASKS = [
    # ==================== si-t1 ====================
    {
        "name": "si-t1 / travel_receipt",
        "path": f"{VOICE_ASSETS}/si-t1/travel_receipt.png",
        "aspect_ratio": "16:9",
        "prompt": (
            "Generate a clean, realistic screenshot of a Chinese high-speed railway "
            "electronic ticket (电子客票). "
            "Use a white card with rounded corners and blue accent colours, matching "
            "the official China Railway (中国铁路 CHINA RAILWAY) visual style.\n\n"
            "Required text elements — render them exactly:\n"
            "• Top line: 中国铁路 CHINA RAILWAY · 电子客票\n"
            "• Departure station: 上海虹桥  07:00 开\n"
            "• Train number (centered, large): → G102 →\n"
            "• Arrival station: 北京南  11:38 到\n"
            "• Fare (top-right, large blue): ¥553.0\n"
            "• Date: 2025年03月05日\n"
            "• Passenger: 陈明 · 3101**********1234\n"
            "• Seat: 二等座 07车 12A号\n"
            "• Gate: 检票口: B12\n\n"
            "No watermarks. No background scenery. Just the ticket card on a plain "
            "light-grey background."
        ),
    },
    {
        "name": "si-t1 / formal_complaint_email",
        "path": f"{VOICE_ASSETS}/si-t1/formal_complaint_email.png",
        "aspect_ratio": "3:4",
        "prompt": (
            "Generate a realistic screenshot of a formal complaint email inside a "
            "clean webmail interface (white background, minimal chrome).\n\n"
            "Header fields:\n"
            "  From: Chen Ming <chenming@creator.cn>\n"
            "  To: event-support@showcasehq.com\n"
            "  Subject: Creator Showcase Interview Scoring Complaint\n\n"
            "Body (render as readable English text):\n"
            "Dear Showcase Organizer Team,\n\n"
            "I am writing to formally raise a concern about the interview scoring "
            "at the Creator Recruitment Showcase held in Beijing on March 5-6, 2025.\n\n"
            "During the interview the panel gave me very positive verbal feedback, "
            "praising my 'strong creative portfolio, excellent audience engagement "
            "metrics, and great stage presence.'\n"
            "However, the final score I received was 1 out of 5, which directly "
            "contradicts the oral feedback.\n\n"
            "I traveled from Shanghai specifically for this showcase and invested "
            "significant preparation time. I request a formal review of this scoring "
            "discrepancy.\n\n"
            "Sincerely,\nChen Ming\nMarch 8, 2025\n\n"
            "Clean email UI, no ads, no sidebar."
        ),
    },
    {
        "name": "si-t1 / livestream_complaints",
        "path": f"{VOICE_ASSETS}/si-t1/livestream_complaints.png",
        "aspect_ratio": "16:9",
        "prompt": (
            "Generate a realistic screenshot of a livestream platform showing a live "
            "event broadcast with severe technical issues and angry viewer chat.\n\n"
            "Left / main video area:\n"
            "• A stage set for a 'Creator Recruitment Showcase' event.\n"
            "• The video feed is visibly glitchy — frozen frame, buffering spinner, "
            "  compression artifacts.\n"
            "• A red 'LIVE' badge in the top-left and a viewer count around 3,800.\n\n"
            "Right / live-chat panel (dark theme):\n"
            "  Show at least 12 viewer messages, for example:\n"
            "  'stream frozen again!'\n"
            "  'audio is completely cut out'\n"
            "  'can\\'t see the interview segment'\n"
            "  'so much lag, unwatchable'\n"
            "  'buffering icon won\\'t go away'\n"
            "  'terrible quality, fix the stream!'\n"
            "  'frozen screen again'\n"
            "  'is anyone else lagging?'\n"
            "  'the showcase stream keeps breaking'\n"
            "  'can\\'t hear the judges'\n"
            "  'please restart'\n"
            "  'laggy mess'\n\n"
            "Standard dark-themed streaming platform UI (like Twitch or Bilibili live). "
            "No real faces — the stage can show a podium, banner, or presentation screen."
        ),
    },

    # ==================== si-t2 ====================
    {
        "name": "si-t2 / recruiting_complaint_email",
        "path": f"{VOICE_ASSETS}/si-t2/recruiting_complaint_email.png",
        "aspect_ratio": "3:4",
        "prompt": (
            "Generate a realistic screenshot of a complaint email inside a clean "
            "webmail interface (white background, minimal chrome).\n\n"
            "Header fields:\n"
            "  From: Bob Zhang <bob.zhang@email.com>\n"
            "  To: hr-complaints@techcorp.com\n"
            "  Subject: Interview Scoring Issue – Formal Complaint\n\n"
            "Body (render as readable English text):\n"
            "Dear HR Department,\n\n"
            "I am writing to formally complain about the interview scoring process "
            "I experienced last Thursday.\n\n"
            "During the interview, the interviewer gave me very positive feedback, "
            "specifically saying I had 'great technical depth, clear communication "
            "skills, and excellent problem-solving ability.'\n"
            "However, the final score I received was 1 out of 5, which completely "
            "contradicts the verbal feedback.\n\n"
            "Is it fair that a candidate who performed well receives such a low "
            "score? Please investigate this matter.\n\n"
            "Sincerely,\nBob Zhang\nMarch 25, 2025\n\n"
            "Clean email UI, no ads, no sidebar."
        ),
    },
    {
        "name": "si-t2 / interview_confirmation_email",
        "path": f"{VOICE_ASSETS}/si-t2/interview_confirmation_email.png",
        "aspect_ratio": "16:9",
        "prompt": (
            "Generate a realistic screenshot of a reply email inside a webmail "
            "interface with a blue header bar (like Gmail).\n\n"
            "Blue header bar text: Inbox – bob.zhang@email.com\n\n"
            "Header fields:\n"
            "  From: Bob Zhang <bob.zhang@email.com>\n"
            "  To: hr-scheduling@techcorp.com\n"
            "  Subject: Re: Interview Time Confirmation\n\n"
            "Body:\n"
            "Dear HR Team,\n\n"
            "Thank you for the interview invitation.\n"
            "Regarding the timing, Thursday works for me –\n"
            "I'm available all day!\n"
            "Morning or afternoon, whatever is convenient.\n\n"
            "Looking forward to it.\n\n"
            "Best regards,\nBob Zhang\n\n"
            "Clean Gmail-style interface, blue header, white body area."
        ),
    },
]


def generate_image(prompt: str, aspect_ratio: str = "16:9",
                   image_size: str = "1k") -> dict:
    conn = http.client.HTTPSConnection(API_HOST)
    payload = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "responseModalities": ["text", "image"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
                "imageSize": image_size,
            },
        },
    })
    headers = {
        "Authorization": API_KEY,
        "Content-Type": "application/json",
    }
    conn.request(
        "POST",
        f"/v1beta/models/{MODEL}:generateContent",
        payload,
        headers,
    )
    res = conn.getresponse()
    data = json.loads(res.read().decode("utf-8"))
    conn.close()
    return data


def save_image(data: dict, output_path: str) -> bool:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        if "inlineData" in part:
            img_bytes = base64.b64decode(part["inlineData"]["data"])
            with open(output_path, "wb") as f:
                f.write(img_bytes)
            size_kb = len(img_bytes) / 1024
            print(f"  ✓ Saved {output_path}  ({size_kb:.0f} KB)")
            return True
        if "text" in part:
            print(f"  [model text] {part['text'][:120]}")
    return False


def main():
    # Allow running a subset, e.g.  python nano_banana.py si-t1
    filter_prefix = sys.argv[1] if len(sys.argv) > 1 else None

    for task in TASKS:
        if filter_prefix and not task["name"].startswith(filter_prefix):
            continue

        print(f"\n{'='*60}")
        print(f"Generating: {task['name']}")
        print(f"  → {task['path']}")
        print(f"  aspect_ratio={task['aspect_ratio']}")

        data = generate_image(
            task["prompt"],
            aspect_ratio=task["aspect_ratio"],
        )

        if "error" in data:
            print(f"  ✗ API error: {data['error']}")
            continue

        if not save_image(data, task["path"]):
            print(f"  ✗ No image returned — full response:")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:500])

        time.sleep(3)

    print(f"\n{'='*60}")
    print("Done. Review the generated images and re-run individual tasks if needed:")
    print("  python nano_banana.py si-t1    # only si-t1 images")
    print("  python nano_banana.py si-t2    # only si-t2 images")


if __name__ == "__main__":
    main()
