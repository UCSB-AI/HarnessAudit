import http.client
import json
import base64

conn = http.client.HTTPSConnection("new.suxi.ai")
payload = json.dumps({
   "contents": [
      {
         "role": "user",
         "parts": [
            {
               "text": "花小猪"
            }
         ]
      }
   ],
   "generationConfig": {
      "responseModalities": [
         "text",
         "image"
      ],
      "imageConfig": {
         "aspectRatio": "16:9",
         "imageSize": "1k"
      }
   }
})
headers = {
   'Authorization': 'Bearer sk-I3JahNGOhYkpfIupvTXWdQWBh3BDVcS3auN1nGWr48sFGOJE',
   'Content-Type': 'application/json'
}
conn.request("POST", "/v1beta/models/gemini-3-pro-image-preview:generateContent", payload, headers)
res = conn.getresponse()
data = json.loads(res.read().decode("utf-8"))

for i, part in enumerate(data["candidates"][0]["content"]["parts"]):
    if "inlineData" in part:
        mime = part["inlineData"]["mimeType"]
        ext = mime.split("/")[-1]
        filename = f"output_{i}.{ext}"
        with open(filename, "wb") as f:
            f.write(base64.b64decode(part["inlineData"]["data"]))
        print(f"Saved: {filename} ({mime})")
    elif "text" in part:
        print(f"Text: {part['text']}")