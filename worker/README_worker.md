# Rudra — Cloudflare Worker deployment

This repo now includes a Cloudflare Worker implementation at `worker/worker.js` that implements a manual-captcha ECI electoral search UI and backend logic.

Important: do NOT commit or expose your secret keys in the repository. Configure the following Cloudflare Worker bindings before deploying:

- CAPTCHA_KEY_B64 (secret binding) : base64-encoded 32-byte AES key used to decrypt captcha blob from ECI
- PUBKEY_B64 (secret binding) : base64-encoded DER public key used to encrypt the AES key
- (optional) KV binding named `KV` if you want caching

Quick deploy guide (Cloudflare Workers using Wrangler):
1. Install wrangler: npm i -g wrangler
2. Authenticate: wrangler login
3. Configure your worker in `wrangler.toml` (example):

   name = "rudra-eci-search"
   main = "./worker/worker.js"
   compatibility_date = "2026-09-04"

   [[kv_namespaces]]
   binding = "KV"
   id = "<your-kv-id>"

4. Publish secrets (do NOT commit to repo):
   wrangler secret put CAPTCHA_KEY_B64
   wrangler secret put PUBKEY_B64

   (Optionally) wrangler kv:namespace create "KV"
   Then reference KV in wrangler.toml as shown above.

5. Deploy:
   wrangler publish

How it works
- Visiting the Worker root (/) fetches a fresh captcha from ECI, decrypts it using CAPTCHA_KEY_B64, and renders a small HTML UI with the image and a form.
- User types the captcha code manually and submits. The Worker encrypts the search payload with AES-GCM + RSA-OAEP and calls the ECI search endpoint.
- If a result is found, the UI shows a formatted response and the full JSON.

Security notes
- The Worker expects CAPTCHA_KEY_B64 and PUBKEY_B64 to be provided as secrets via wrangler secret put (or via Cloudflare dashboard environment variables). The Worker will fail fast if they are missing.
- Do not store or log users' captcha answers.
- Respect upstream rate limits. If you get 429 responses, add exponential backoff or manual retry.

If you want, I can also:
- Add a `wrangler.toml` example and CI to auto-deploy via GitHub Actions.
- Convert the Worker to an Express/ FastAPI route if you prefer to host on Vercel / Render instead.
