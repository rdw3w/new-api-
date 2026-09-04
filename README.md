# ECI Electoral Search — Vercel Serverless API

This repository wraps the ECI electoral-search script as a serverless HTTP API suitable for deployment to Vercel.

Features
- POST /api/search { "epic": "<EPIC>" } or GET /api/search?epic=<EPIC>
- Uses Google Gemini (Generative Language) for parallel OCR of captchas
- AES-GCM + RSA-OAEP wrapping for the ECI search payload (keeps original crypto logic)
- Pillow-based preprocessing (no ffmpeg required)

Important: Gemini key and other sensitive values must be provided via environment variables (see below).

Requirements (local)
- Python 3.10+
- pip

Files
- api/eci.py — FastAPI app and converted logic from your script
- requirements.txt — Python dependencies
- vercel.json — Vercel configuration
- .env.example — local env example

Local development
1. Create and activate virtualenv:
   python -m venv .venv
   source .venv/bin/activate   # on Windows: .venv\\Scripts\\activate

2. Install dependencies:
   pip install -r requirements.txt

3. Create a .env file (or export env vars). Example (Linux/macOS):
   export GEMINI_KEY="your_real_gemini_key"
   export CAPTCHA_KEY_B64="..."   # optional, recommended to override hard-coded key
   export PUBKEY_B64="..."        # optional

4. Run locally (uvicorn):
   uvicorn api.eci:app --reload --port 8000

5. Test:
   POST http://localhost:8000/api/search  with JSON body: { "epic": "ZJJ2263770" }
   or
   GET  http://localhost:8000/api/search?epic=ZJJ2263770

Deploy to Vercel
1. Install Vercel CLI and login:
   npm i -g vercel
   vercel login

2. Add environment variables on Vercel:
   - In the Vercel dashboard, open your Project → Settings → Environment Variables
     - Name: GEMINI_KEY
     - Value: <your_gemini_key>
     - Apply to: Production / Preview / Development as needed

   Or use the CLI:
   vercel env add GEMINI_KEY production
   (repeat for preview/development if you want)

3. Deploy:
   vercel --prod

Notes and troubleshooting
- ffmpeg is not used here because Vercel serverless doesn't provide system binaries. If you require ffmpeg, deploy on a platform that supports custom Docker images or apt packages.
- Gemini quotas and billing apply; keep your key secret.
- Move CAPTCHA_KEY_B64 and PUBKEY_B64 into env vars for security.
- If you see 429 responses from ECI endpoints, implement additional backoff or retries.

Security & legal reminders
- Use this responsibly and ensure you have the right to query and store voter data.
- Do not commit GEMINI_KEY or other secrets to the repo.
