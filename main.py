import time
import os, json, re, html, subprocess, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime, timezone
from google.genai import errors
from google import genai
from google.genai import types
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

OUT = Path("output")
OUT.mkdir(exist_ok=True)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
W, H = 1080, 1920

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
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines

def commons_image(query, destination, seen_titles=None):
    """Fetch a relevant freely licensed image from Wikimedia Commons; returns False on failure."""
    try:
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
            "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": 1200
        }
        url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "MysteryLedgerShortsBot/1.0 (educational video project)"})
        with urllib.request.urlopen(req, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
        pages = list(payload.get("query", {}).get("pages", {}).values())
        for page in pages:
            page_title = page.get("title", "")
            if seen_titles is not None and page_title in seen_titles:
                continue
            info = (page.get("imageinfo") or [{}])[0]
            image_url = info.get("thumburl") or info.get("url")
            if not image_url:
                continue
            req = urllib.request.Request(image_url, headers={"User-Agent": "MysteryLedgerShortsBot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read()
            if len(data) < 10000:
                continue
            Path(destination).write_bytes(data)
            meta = info.get("extmetadata", {})
            artist = html.unescape(re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", "Unknown")))
            license_name = meta.get("LicenseShortName", {}).get("value", "License not listed")
            return {"title": page.get("title", ""), "artist": artist[:250], "license": license_name}
    except Exception as exc:
        print(f"Wikimedia image lookup failed for '{query}': {exc}")
    return False

def make_scene(photo_path, text, index, total, path):
    if photo_path and Path(photo_path).exists():
        im = Image.open(photo_path).convert("RGB")
        # Fill vertical frame, crop centrally, and darken for legible captions.
        scale = max(W / im.width, H / im.height)
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
        left = (im.width - W) // 2
        top = (im.height - H) // 2
        im = im.crop((left, top, left + W, top + H))
        im = ImageEnhance.Brightness(im).enhance(0.48)
        im = im.filter(ImageFilter.GaussianBlur(0.4))
    else:
        im = Image.new("RGB", (W, H), (8, 13, 28))
        d0 = ImageDraw.Draw(im)
        for y in range(H):
            t = y / H
            d0.line((0, y, W, y), fill=(int(8+10*t), int(13+8*t), int(28+20*t)))
    d = ImageDraw.Draw(im, "RGBA")
    # Gradient-like dark panels behind text.
    d.rounded_rectangle((55, 105, 1025, 1815), radius=44, fill=(0, 0, 0, 108), outline=(190, 170, 120, 150), width=3)
    d.text((95, 145), "MYSTERY LEDGER  /  MYSTERY & HISTORY", font=get_font(30, True), fill=(185, 205, 245, 255))
    d.text((95, 300), f"CASE FILE  {index:02d}", font=get_font(38, True), fill=(255, 205, 120, 255))
    font = get_font(59, True)
    lines = wrap_lines(d, text, font, 850)
    # Keep the caption compact and centered in the lower-middle area.
    line_height = 78
    y = max(700, 1120 - min(len(lines), 5) * line_height // 2)
    for line in lines[:6]:
        d.text((95, y), line, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 230))
        y += line_height
    d.text((95, 1690), f"FOLLOW FOR MORE  •  {index}/{total}", font=get_font(30, True), fill=(210, 220, 240, 255))
    im.convert("RGB").save(path, quality=94)

def generate_content():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY GitHub Secret is missing.")
    client = genai.Client(api_key=api_key)
    prompt = """
Create an original, factual, high-retention 45-60 second YouTube Short for a US audience.
Channel: Mystery Ledger. Topic: lesser-known American history or a documented historical mystery.
STORY RULES:
- Start immediately with a compelling curiosity gap in the first spoken sentence. No greeting or channel intro.
- Build one clear question; reveal clues progressively; finish with a satisfying, evidence-based payoff.
- Use concrete dates, places, people, and objects only when supported by credible historical records.
- Avoid conspiracy claims, invented details, fake quotes, graphic content, and unsupported accusations.
- Write six connected narration segments in natural spoken English, roughly 8-18 words each.
- End with a short, natural invitation to follow Mystery Ledger; avoid a long generic outro.
VISUAL RULES:
- Return six DISTINCT, scene-specific Wikimedia Commons search phrases, 3-7 words each.
- Each query must illustrate its exact narration segment, not merely the overall topic.
- Vary the visual subjects: archival photographs, historic buildings/locations, artifacts, newspaper pages/documents,
  portraits, and maps where genuinely relevant.
- Use no more than TWO map-focused queries in the entire Short. Do not use maps as filler.
- Prefer concrete nouns and identifiable subjects over vague words such as "mystery" or "history".
Return ONLY valid JSON with exactly these fields:
{
  "title":"compelling and accurate title under 70 characters",
  "description":"2-3 factual sentences plus relevant hashtags",
  "scenes":["six connected narration segments; first is a strong hook; final includes brief follow CTA"],
  "visual_queries":["six distinct scene-matched Commons search phrases, 3-7 words each"],
  "sources_to_verify":["two credible source names or URLs for later fact checking"]
}
Do not claim sources have been checked. Do not invent evidence. Use cautious wording where facts are disputed.
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
            if (len(data.get("scenes", [])) != 6 or len(data.get("visual_queries", [])) != 6
                    or not data.get("title") or not data.get("description")):
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
            "description": (description + "\n\n#Shorts #MysteryLedger")[:5000],
            "categoryId": "27",
            "tags": ["Mystery Ledger", "American history", "mystery", "history", "Shorts"]
        },
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"YouTube upload progress: {int(status.progress() * 100)}%")
    print(f"Uploaded PRIVATE Mystery Ledger test video: https://www.youtube.com/watch?v={response['id']}")
    return response["id"]

def main():
    data = generate_content()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_title = re.sub(r"[^A-Za-z0-9_-]+", "-", data["title"]).strip("-")[:55]
    folder = OUT / f"{stamp}-{safe_title}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metadata.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    audio_files, image_files, attribution = [], [], []
    seen_image_titles = set()
    for i, (scene, visual_query) in enumerate(zip(data["scenes"], data["visual_queries"]), 1):
        audio = folder / f"audio-{i}.mp3"
        gTTS(text=scene, lang="en", tld="com").save(str(audio))
        audio_files.append(audio)
        downloaded = folder / f"source-{i}.jpg"
        credit = commons_image(visual_query, downloaded, seen_image_titles)
        if not credit:
            # Retry using the story title if a specific scene query has no result.
            credit = commons_image(visual_query + " " + data["title"], downloaded, seen_image_titles)
        if credit:
            seen_image_titles.add(credit.get("title", ""))
            attribution.append({"scene": i, **credit})
            photo = downloaded
            print(f"Scene {i}: Wikimedia image found: {credit['title']}")
        else:
            photo = None
            print(f"Scene {i}: no Commons image found; using designed fallback background.")
        image = folder / f"scene-{i}.jpg"
        make_scene(photo, scene, i, len(data["scenes"]), image)
        image_files.append(image)

    segments = []
    for i, (img, aud) in enumerate(zip(image_files, audio_files), 1):
        seg = folder / f"segment-{i}.mp4"
        # Gentle Ken Burns zoom; each segment lasts for its narration.
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-i", str(aud),
             "-vf", "scale=1200:2134,zoompan=z='min(zoom+0.0005,1.06)':d=1:s=1080x1920:fps=30,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
             "-c:a", "aac", "-b:a", "128k", "-shortest", "-pix_fmt", "yuv420p", str(seg)])
        segments.append(seg)

    concat_file = folder / "concat.txt"
    concat_file.write_text("".join(f"file '{p.resolve()}'\n" for p in segments), encoding="utf-8")
    final_video = folder / "mystery-ledger-short.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c", "copy", "-movflags", "+faststart", str(final_video)])
    (folder / "IMAGE-CREDITS.json").write_text(json.dumps(attribution, indent=2), encoding="utf-8")
    (folder / "UPLOAD-INFO.txt").write_text(
        f"TITLE: {data['title']}\n\nDESCRIPTION:\n{data['description']}\n\n"
        "Image sources and license labels are in IMAGE-CREDITS.json. Verify each license and attribution "
        "requirements before making the video public. Script source suggestions require independent verification.\n",
        encoding="utf-8"
    )
    youtube_upload(final_video, data["title"], data["description"])
    print(f"Created and uploaded PRIVATE Mystery Ledger test video: {final_video}")

if __name__ == "__main__":
    main()
