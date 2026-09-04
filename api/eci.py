"""
Vercel serverless API wrapper for the ECI electoral-search script.

Endpoints:
 - POST /api/search    { "epic": "ZJJ2263770" }
 - GET  /api/search?epic=ZJJ2263770

Environment:
 - GEMINI_KEY (required): Gemini API key
 - CAPTCHA_KEY_B64 (optional): base64 captcha key (defaults to embedded)
 - PUBKEY_B64 (optional): base64 DER public key (defaults to embedded)
"""
import base64
import json
import os
import re
import time
import io
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from PIL import Image, ImageEnhance

# Configuration
BASE = "https://gateway-voters.eci.gov.in"
ORIGIN = "https://electoralsearch.eci.gov.in"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"

# Optional: move these to env for production
CAPTCHA_KEY_B64 = os.environ.get("CAPTCHA_KEY_B64",
    "e855n97lc4tcPkj7WWsi38yNWpalLBLZzQdkqHWYbZ0=")
PUBKEY_B64 = os.environ.get("PUBKEY_B64", (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArb7++BxL/YN8OIln+6FL9"
    "Gnw5DNmQ/VFZXss+J+TuQyJc891JbqbijxYQNEin2c2u+CnpXpoGQ/1gUSzDMJeN"
    "S3sNSlIUykp2dt7xIm/cmV4sZ/c769vCxVRosMfRaZJnBAah+m1X26lEhnOo0wpAB"
    "9Txr8RIyBe6h7PiQWykeJeh6UacOBBX28kgkq7+vJhW8HgB38lt32XRocznRYwS9L"
    "qR7ZweFmQhTr1+EGrqiEKCOCxMYgHR2SQckb96hZ9kWzfzeun4bUO5oXKJciLkiS1"
    "IgKieADEvYLgu129ZIpn1H+8H+8ikNNVETqEDDMtqcQcQmWppJvcWHaXAs+f8QIDAQAB"
))
SAMPLES = 4
MAX_CAPTCHAS = 4
TIMEOUT = 10

# decode captcha key to bytes for AESGCM
CAPTCHA_KEY = base64.b64decode(CAPTCHA_KEY_B64)


def headers():
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "applicationName": "ELECTORAL-SEARCH",
        "channelidobo": "ELECTORAL-SEARCH",
        "appName": "ELECTORAL-SEARCH",
        "User-Agent": UA,
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
    }


def decrypt_blob(b64blob):
    blob = base64.b64decode(b64blob)
    return json.loads(AESGCM(CAPTCHA_KEY).decrypt(blob[:12], blob[12:], None).decode("utf-8"))


def get_captcha(sess, tries=3):
    sess.get("https://electoralsearch.eci.gov.in/", headers=headers(), timeout=TIMEOUT)
    for attempt in range(tries):
        r = sess.get(f"{BASE}/api/v1/captcha-service/getCaptcha/sir", headers=headers(), timeout=TIMEOUT)
        if r.status_code == 200 and r.text.lstrip().startswith("{"):
            try:
                return decrypt_blob(r.json()["data"])
            except Exception:
                pass
        bb = r.text.strip()[:120] or f"(empty body, status {r.status_code})"
        if attempt < tries - 1:
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"getCaptcha failed after {tries} tries: {bb}")


def preprocess(b64_image):
    """
    Pillow-based preprocessing (upscale 3x lanczos, grayscale, contrast/brightness).
    Returns base64-encoded PNG suitable for the Gemini upload inline_data.
    """
    raw = base64.b64decode(b64_image)
    try:
        im = Image.open(io.BytesIO(raw))
        w, h = im.size
        im = im.resize((w * 3, h * 3), resample=Image.LANCZOS).convert("L")
        im = ImageEnhance.Contrast(im).enhance(1.6)
        im = ImageEnhance.Brightness(im).enhance(1.02)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # fallback to raw if Pillow fails
        return b64_image


def ocr(b64_image, temperature=0.2):
    if not GEMINI_KEY:
        return ""
    payload = {
        "contents": [{
            "parts": [
                {"text": ("Exactly transcribe the captcha code in this image. It is 5-7 alphanumeric "
                          "characters with a diagonal line through it. Ignore the diagonal line and all "
                          "noise. Reply with ONLY the code, nothing else. Example: `ab12cd`.")},
                {"inline_data": {"mime_type": "image/png",
                                 "data": base64.b64encode(base64.b64decode(b64_image)).decode()}},
            ]
        }],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 16},
    }
    url = f"{GEMINI_API}?key={GEMINI_KEY}"
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=45)
            if r.status_code in (429, 500, 503) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            res = r.json()
            parts = res.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            return (parts[0].get("text", "") if parts else "").strip()
        except requests.HTTPError:
            return ""
        except Exception:
            return ""
    return ""


def encrypt_payload(payload):
    pub = serialization.load_der_public_key(base64.b64decode(PUBKEY_B64))
    key, nonce = os.urandom(32), os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload, separators=(",", ":")).encode(), None)
    wrapped = pub.encrypt(key, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return {"encryptedPayload": base64.b64encode(ct).decode(),
            "encryptedKey": base64.b64encode(wrapped).decode(),
            "iv": base64.b64encode(nonce).decode()}


def search_epic(sess, epic, captcha_id, answer):
    p = {"epicNumber": epic, "isPortal": True, "captchaId": captcha_id,
         "captchaData": answer, "securityKey": "na", "eSEARCHYNEFjd3S": "1021"}
    r = sess.post(f"{BASE}/api/v1/elastic/search-by-epic-from-national-display-v1",
                  data=json.dumps(encrypt_payload(p)).encode(), headers=headers(), timeout=TIMEOUT)
    if r.status_code == 200:
        try:
            return 200, r.json()
        except Exception:
            return 200, r.text
    return r.status_code, r.text[:120]


def summarize(rec):
    if not isinstance(rec, dict):
        return " " + str(rec)
    c = rec.get("content", rec)
    g = lambda k: c.get(k)
    return (f"\n  EPIC ........ {g('epicNumber')}\n"
            f"  Name ........ {g('fullName')}\n"
            f"  Age/Gender .. {g('age')} / {g('gender')}\n"
            f"  Relation .... {g('relativeFullName')} ({g('relationType')})\n"
            f"  Address ..... {g('buildingAddress')}\n"
            f"  Booth ....... {g('psBuildingName')} - Room {g('psRoomDetails')}\n"
            f"  Assembly .... {g('asmblyName')} (AC {g('acNumber')})\n"
            f"  Parliament .. {g('prlmntName')} (No. {g('prlmntNo')})\n"
            f"  District .... {g('districtValue')} ({g('districtCd')})\n"
            f"  State ....... {g('stateName')} ({g('stateCd')})\n"
            f"  Part ........ {g('partName')} # {g('partNumber')}\n"
            f"  EPIC issued . {g('epicDatetime')}")


def run(epic):
    t0 = time.time()
    sess = requests.Session()
    for cap_i in range(1, MAX_CAPTCHAS + 1):
        try:
            cap = get_captcha(sess)
        except RuntimeError:
            time.sleep(2)
            continue
        cid = cap.get("id")
        proc = preprocess(cap.get("captcha"))
        with ThreadPoolExecutor(max_workers=SAMPLES) as ex:
            raws = list(ex.map(lambda _: ocr(proc), range(SAMPLES)))
        codes = []
        for r in raws:
            m = re.search(r"[A-Za-z0-9]{4,8}", r or "")
            if m:
                codes.append(m.group(0))
        cands = [c for c, _ in Counter(codes).most_common()] if codes else []
        for a in cands:
            sco, body = search_epic(sess, epic, cid, a)
            if sco == 200 and isinstance(body, list) and body:
                # success
                return body
            if sco == 429:
                time.sleep(2.0)
        time.sleep(1.0)
    return None


# FastAPI app
app = FastAPI(title="ECI Electoral Search API")


class SearchRequest(BaseModel):
    epic: str


@app.post("/api/search")
def search_post(req: SearchRequest):
    if not req.epic:
        raise HTTPException(status_code=400, detail="epic is required")
    result = run(req.epic)
    if result:
        return result
    raise HTTPException(status_code=404, detail="No result found")


@app.get("/api/search")
def search_get(epic: str = Query(..., min_length=1)):
    result = run(epic)
    if result:
        return result
    raise HTTPException(status_code=404, detail="No result found")
