import time
from google.genai import errors
import os, json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone
from google import genai
from google.genai import types
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

OUT = Path("output")
OUT.mkdir(exist_ok=True)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def run(cmd):
    subprocess.run(cmd, check=True)

def get_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def wrap_lines(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            line = test
        else:
            if line: lines.append(line)
            line = word
    if line: lines.append(line)
    return lines

def make_scene(text, index, total, path):
    w, h = 1080, 1920
    im = Image.new("RGB", (w, h), (8, 13, 28))
    px = im.load()
    for y in range(h):
        t = y / h
        c = (int(8+10*t), int(13+8*t), int(28+20*t))
        for x in range(w):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((65, 90, 1015, 1830), radius=48, outline=(85, 104, 145), width=4)
    d.text((100, 140), "DARKFILES  /  MYSTERY & HISTORY", font=get_font(34, True), fill=(150, 185, 255))
    d.text((100, 300), f"CASE FILE  {index:02d}", font=get_font(40, True), fill=(245, 183, 75))
    font = get_font(64, True)
    lines = wrap_lines(d, text, font, 820)
    y = 610
    for line in lines[:9]:
        d.text((100, y), line, font=font, fill=(245, 247, 252), stroke_width=1, stroke_fill=(20, 25, 40))
        y += 92
    d.text((100, 1690), f"FOLLOW FOR MORE  •  {index}/{total}", font=get_font(32, True), fill=(160, 180, 215))
    im.save(path, quality=95)

def generate_content():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY GitHub Secret is missing.")
    client = genai.Client(api_key=api_key)
    prompt = """
Create an original, factual, engaging 45-60 second YouTube Shorts script for a US audience.
Topic area: lesser-known American history or a verified historical mystery.
Avoid unsolved-crime accusations, graphic content, conspiracy claims, fabricated facts, and fake quotes.
Return ONLY valid JSON with:
{"title":"under 70 characters","description":"2-3 sentences plus relevant hashtags",
"scenes":["6 short narration segments, each 8-18 words"],
"sources_to_verify":["2 credible source names or URLs"]}
Choose a specific topic and use cautious wording where facts are disputed.
"""
    for attempt in range(1, 5):
        try:
            print(f"Gemini API attempt {attempt}/4")
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7)
            )
            if not response.text:
                raise ValueError("Gemini returned an empty response.")
            data = json.loads(response.text)
            if len(data.get("scenes", [])) != 6 or not data.get("title") or not data.get("description"):
                raise ValueError("Gemini response is missing required fields.")
            return data
        except errors.APIError as e:
            status = getattr(e, "code", None)
            if status not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            wait_time = 10 * (2 ** (attempt - 1))
            print(f"Gemini temporary error {status}; retrying in {wait_time}s")
            time.sleep(wait_time)
    raise RuntimeError("Gemini content generation failed.")

def youtube_upload(video_path, title, description):
    token_raw = os.environ.get("YOUTUBE_TOKEN_JSON")
    if not token_raw:
        raise RuntimeError("YOUTUBE_TOKEN_JSON GitHub Secret is missing.")
    token_info = json.loads(token_raw)
    credentials = Credentials.from_authorized_user_info(token_info, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError("YouTube OAuth credentials are invalid or expired. Re-authorize the channel.")
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    body = {
        "snippet": {
            "title": title[:100],
            "description": (description + "\n\n#Shorts #DarkFiles")[:5000],
            "categoryId": "27",
            "tags": ["DarkFiles", "American history", "mystery", "history", "Shorts"]
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False
        }
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"YouTube upload progress: {int(status.progress() * 100)}%")
    print(f"Uploaded PRIVATE test video: https://www.youtube.com/watch?v={response['id']}")
    return response["id"]

def main():
    data = generate_content()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_title = re.sub(r"[^A-Za-z0-9_-]+", "-", data["title"]).strip("-")[:55]
    folder = OUT / f"{stamp}-{safe_title}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metadata.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    audio_files, image_files = [], []
    for i, scene in enumerate(data["scenes"], 1):
        audio = folder / f"audio-{i}.mp3"
        gTTS(text=scene, lang="en", tld="com").save(str(audio))
        audio_files.append(audio)
        image = folder / f"scene-{i}.png"
        make_scene(scene, i, len(data["scenes"]), image)
        image_files.append(image)
    segments = []
    for img, aud in zip(image_files, audio_files):
        seg = folder / f"segment-{len(segments)+1}.mp4"
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-i", str(aud),
             "-vf", "scale=1080:1920,format=yuv420p", "-c:v", "libx264",
             "-tune", "stillimage", "-c:a", "aac", "-b:a", "128k",
             "-shortest", "-pix_fmt", "yuv420p", str(seg)])
        segments.append(seg)
    concat_file = folder / "concat.txt"
    concat_file.write_text("".join(f"file '{p.resolve()}'\n" for p in segments), encoding="utf-8")
    final_video = folder / "darkfiles-short.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c", "copy", "-movflags", "+faststart", str(final_video)])
    (folder / "UPLOAD-INFO.txt").write_text(
        f"TITLE: {data['title']}\n\nDESCRIPTION:\n{data['description']}\n\n"
        "Source suggestions require independent verification before public release.\n",
        encoding="utf-8"
    )
    youtube_upload(final_video, data["title"], data["description"])
    print(f"Created and uploaded private test video: {final_video}")

if __name__ == "__main__":
    main()
