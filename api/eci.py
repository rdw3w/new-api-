"""
Rudra — FastAPI server for manual-captcha ECI electoral search.

Endpoints:
 - GET /api/captcha       -> { id: str, captcha: base64-image }
 - POST /api/search       -> { ... }  (returns upstream JSON on success)
 - GET /                   -> serves the Rudra UI (index.html)

Environment variables (set on Vercel or locally):
 - CAPTCHA_KEY_B64  (required) : base64 AES-256-GCM key used to decrypt upstream captcha blob
 - PUBKEY_B64       (required) : base64 DER/SPKI RSA public key used to wrap AES key for upstream

Notes:
 - This app does NOT perform OCR. The UI shows the captcha image and the user types the code.
 - Do NOT commit secret keys to source control. Use environment variables on Vercel.
"""
import os
import base64
import json
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

# Configuration
BASE = "https://gateway-voters.eci.gov.in"
ORIGIN = "https://electoralsearch.eci.gov.in"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 10

# Secrets from environment
CAPTCHA_KEY_B64 = os.environ.get("CAPTCHA_KEY_B64")
PUBKEY_B64 = os.environ.get("PUBKEY_B64")

if not CAPTCHA_KEY_B64:
    # For local convenience only, you may set defaults in .env; in production require envs.
    # Do not commit real keys into the repo.
    CAPTCHA_KEY_B64 = os.environ.get("CAPTCHA_KEY_B64", None)

app = FastAPI(title="Rudra — ECI Electoral Search")

# Read UI file to serve at root
ROOT_HTML_PATH = os.path.join(os.path.dirname(__file__), '..', 'index.html')
try:
    with open(ROOT_HTML_PATH, 'r', encoding='utf-8') as f:
        INDEX_HTML = f.read()
except Exception:
    INDEX_HTML = "<html><body><h1>Rudra</h1><p>index.html missing</p></body></html>"


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


def decrypt_blob(b64blob: str) -> dict:
    if not CAPTCHA_KEY_B64:
        raise RuntimeError("CAPTCHA_KEY_B64 not configured")
    blob = base64.b64decode(b64blob)
    if len(blob) < 13:
        raise RuntimeError("Encrypted blob too short")
    iv = blob[:12]
    ciphertext = blob[12:]
    key = base64.b64decode(CAPTCHA_KEY_B64)
    if len(key) != 32:
        raise RuntimeError("CAPTCHA_KEY_B64 must decode to 32 bytes (AES-256 key)")
    try:
        plain = AESGCM(key).decrypt(iv, ciphertext, None)
        return json.loads(plain.decode('utf-8'))
    except Exception as e:
        raise RuntimeError(f"Failed to decrypt captcha blob: {e}")


def get_captcha_from_upstream(tries: int = 3) -> dict:
    sess = requests.Session()
    # Prime the session by hitting the electoralsearch homepage
    try:
        sess.get("https://electoralsearch.eci.gov.in/", headers=headers(), timeout=TIMEOUT)
    except Exception:
        pass
    last_err = None
    for attempt in range(tries):
        try:
            r = sess.get(f"{BASE}/api/v1/captcha-service/getCaptcha/sir", headers=headers(), timeout=TIMEOUT)
            if r.status_code == 200 and r.text.lstrip().startswith('{'):
                j = r.json()
                if 'data' in j:
                    return decrypt_blob(j['data'])
            last_err = f"unexpected upstream response (status {r.status_code})"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.5 + attempt)
    raise RuntimeError(f"getCaptcha failed: {last_err}")


def encrypt_payload(payload: dict) -> dict:
    if not PUBKEY_B64:
        raise RuntimeError("PUBKEY_B64 not configured")
    pub = serialization.load_der_public_key(base64.b64decode(PUBKEY_B64))
    key = os.urandom(32)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload, separators=(',', ':')).encode(), None)
    wrapped = pub.encrypt(key, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return {"encryptedPayload": base64.b64encode(ct).decode(),
            "encryptedKey": base64.b64encode(wrapped).decode(),
            "iv": base64.b64encode(nonce).decode()}


def search_epic_upstream(epic: str, captcha_id: str, answer: str):
    p = {"epicNumber": epic, "isPortal": True, "captchaId": captcha_id,
         "captchaData": answer, "securityKey": "na", "eSEARCHYNEFjd3S": "1021"}
    data = encrypt_payload(p)
    sess = requests.Session()
    r = sess.post(f"{BASE}/api/v1/elastic/search-by-epic-from-national-display-v1", headers=headers(), data=json.dumps(data).encode(), timeout=TIMEOUT)
    status = r.status_code
    try:
        body = r.json()
    except Exception:
        body = r.text
    return status, body


class SearchRequest(BaseModel):
    epic: str
    captchaId: str
    captchaData: str


@app.get('/', response_class=HTMLResponse)
async def root():
    return HTMLResponse(INDEX_HTML)


@app.get('/api/captcha')
async def api_get_captcha():
    try:
        cap = get_captcha_from_upstream()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    cid = cap.get('id')
    captcha_b64 = cap.get('captcha')
    if not cid or not captcha_b64:
        raise HTTPException(status_code=502, detail='Malformed captcha response from upstream')
    return JSONResponse({"id": cid, "captcha": captcha_b64})


@app.post('/api/search')
async def api_search(req: SearchRequest):
    if not req.epic:
        raise HTTPException(status_code=400, detail='epic is required')
    if not req.captchaId or not req.captchaData:
        raise HTTPException(status_code=400, detail='captchaId and captchaData are required')
    try:
        status, body = search_epic_upstream(req.epic, req.captchaId, req.captchaData)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if status == 200:
        return JSONResponse(body)
    if status == 429:
        raise HTTPException(status_code=429, detail='Upstream rate limit')
    raise HTTPException(status_code=502, detail=f'Upstream error: {str(body)[:300]}')
