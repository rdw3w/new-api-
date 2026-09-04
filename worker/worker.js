// ============================================================
// ECI ELECTORAL SEARCH - MANUAL CAPTCHA SOLVE (Cloudflare Worker)
// Note: secrets are read from env bindings (do NOT hardcode keys in repo)
// BINDINGS REQUIRED:
// - CAPTCHA_KEY_B64  (required) : base64 AES-GCM key used to decrypt captcha blob
// - PUBKEY_B64       (required) : base64 DER RSA public key for wrapping AES key
// - KV (optional)    : KV namespace binding name for caching results
// - GEMINI_API_KEY   (optional) : NOT USED for manual flow; kept optional
// ============================================================

const BASE = "https://gateway-voters.eci.gov.in";
const ORIGIN = "https://electoralsearch.eci.gov.in";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

// Utility: base64 <-> Uint8Array
function base64ToBinary(base64) {
  try {
    // atob returns binary string
    const binaryString = atob(base64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
    return bytes;
  } catch (e) {
    return new Uint8Array(0);
  }
}

function binaryToBase64(bytes) {
  try {
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  } catch (e) {
    return '';
  }
}

// Decrypt AES-GCM blob produced by upstream: 12-byte IV + ciphertext
async function decryptBlob(b64blob, captchaKeyB64) {
  const blob = base64ToBinary(b64blob);
  if (blob.length < 13) throw new Error('Encrypted blob too short');
  const iv = blob.slice(0, 12);
  const ciphertext = blob.slice(12);
  const keyBytes = base64ToBinary(captchaKeyB64);
  if (keyBytes.length !== 32) throw new Error('CAPTCHA_KEY_B64 must decode to 32 bytes');

  const cryptoKey = await crypto.subtle.importKey('raw', keyBytes, { name: 'AES-GCM' }, false, ['decrypt']);
  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, cryptoKey, ciphertext);
  return JSON.parse(new TextDecoder().decode(plain));
}

// Encrypt payload: AES-GCM + RSA-OAEP wrap
async function encryptPayload(payload, pubkeyB64) {
  const pubKeyBytes = base64ToBinary(pubkeyB64);
  const publicKey = await crypto.subtle.importKey('spki', pubKeyBytes.buffer, { name: 'RSA-OAEP', hash: 'SHA-256' }, false, ['encrypt']);

  const aesKey = crypto.getRandomValues(new Uint8Array(32));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const aesCryptoKey = await crypto.subtle.importKey('raw', aesKey, { name: 'AES-GCM' }, false, ['encrypt']);

  const payloadStr = JSON.stringify(payload);
  const encrypted = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, aesCryptoKey, new TextEncoder().encode(payloadStr));
  const wrappedKey = await crypto.subtle.encrypt({ name: 'RSA-OAEP' }, publicKey, aesKey);

  return {
    encryptedPayload: binaryToBase64(new Uint8Array(encrypted)),
    encryptedKey: binaryToBase64(new Uint8Array(wrappedKey)),
    iv: binaryToBase64(iv)
  };
}

async function getCaptchaFromUpstream(captchaKeyB64) {
  // Fetch homepage to get cookies/session headers
  await fetch(ORIGIN, {
    method: 'GET',
    headers: {
      'User-Agent': UA,
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
  });

  const r = await fetch(`${BASE}/api/v1/captcha-service/getCaptcha/sir`, {
    method: 'GET',
    headers: {
      'Accept': 'application/json, text/plain, */*',
      'Content-Type': 'application/json',
      'applicationName': 'ELECTORAL-SEARCH',
      'channelidobo': 'ELECTORAL-SEARCH',
      'appName': 'ELECTORAL-SEARCH',
      'User-Agent': UA,
      'Origin': ORIGIN,
      'Referer': `${ORIGIN}/`
    }
  });

  if (!r.ok) throw new Error(`Upstream getCaptcha failed: ${r.status}`);
  const j = await r.json();
  if (!j.data) throw new Error('Upstream returned no data');
  return await decryptBlob(j.data, captchaKeyB64);
}

async function searchEpic(epicNumber, captchaId, captchaData, pubkeyB64) {
  const payload = {
    epicNumber: epicNumber,
    isPortal: true,
    captchaId: captchaId,
    captchaData: captchaData,
    securityKey: 'na',
    eSEARCHYNEFjd3S: '1021'
  };

  const encrypted = await encryptPayload(payload, pubkeyB64);
  const r = await fetch(`${BASE}/api/v1/elastic/search-by-epic-from-national-display-v1`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json, text/plain, */*',
      'Content-Type': 'application/json',
      'applicationName': 'ELECTORAL-SEARCH',
      'channelidobo': 'ELECTORAL-SEARCH',
      'appName': 'ELECTORAL-SEARCH',
      'User-Agent': UA,
      'Origin': ORIGIN,
      'Referer': `${ORIGIN}/`
    },
    body: JSON.stringify(encrypted)
  });

  const status = r.status;
  let body;
  try { body = await r.json(); } catch (e) { body = await r.text(); }
  return { status, body };
}

// HTML generation (home / result)
function generateHomeHTML(captchaId, captchaImage, epicNumber = '', envNotice = '') {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rudra — ECI Search</title>
  <style>body{font-family:Inter,system-ui;background:#071226;color:#e6eef8;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
  .card{background:linear-gradient(180deg,rgba(255,255,255,0.02),rgba(255,255,255,0.01));padding:24px;border-radius:12px;width:720px;max-width:95%}
  input,button{font-size:15px;padding:10px;border-radius:8px;border:1px solid rgba(255,255,255,0.06);background:transparent;color:inherit}
  button{background:#7c3aed;color:#fff;border:none}
  .muted{color:#94a3b8;font-size:13px}
  img.captcha{max-width:320px;border-radius:8px;background:#fff;padding:8px}
  </style></head><body><div class="card">
  <h2>Rudra — ECI Voter Search</h2><p class="muted">Manual captcha flow — enter EPIC, fetch captcha, type code, submit.</p>
  <form id="f" action="/" method="GET">
    <input type="hidden" name="action" value="search">
    <input type="hidden" name="captchaId" value="${captchaId}">
    <div style="margin:12px 0"><label class="muted">EPIC</label><input name="epic" placeholder="ZJJ2263770" value="${epicNumber}" required></div>
    <div style="margin:12px 0"><div><img class="captcha" src="data:image/jpeg;base64,${captchaImage}" alt="captcha"></div><div class="muted">Captcha ID: ${captchaId}</div></div>
    <div style="margin:12px 0"><label class="muted">Enter captcha</label><input name="answer" placeholder="Type code shown above" required autocomplete="off"></div>
    <div style="display:flex;gap:8px"><button type="submit">Search</button><button type="submit" name="action" value="home" style="background:#6b7280">Refresh Captcha</button></div>
  </form>
  <div style="margin-top:12px" class="muted">${envNotice}</div>
  </div></body></html>`;
}

function generateResultHTML(success, data, message, fullData) {
  const content = (data && (data.content || data)) || {};
  const detailBlock = success && data ? `
    <h3 style="color:#34d399">Voter Found</h3>
    <div style="background:#fff;padding:12px;border-radius:8px;color:#000;margin-bottom:12px">
      <div><strong>EPIC:</strong> ${content.epicNumber || 'N/A'}</div>
      <div><strong>Name:</strong> ${content.fullName || 'N/A'}</div>
      <div><strong>Age/Gender:</strong> ${content.age || 'N/A'} / ${content.gender || 'N/A'}</div>
      <div><strong>Address:</strong> ${content.buildingAddress || 'N/A'}</div>
    </div>
    ${fullData ? `<details><summary>View full JSON</summary><pre style="max-height:300px;overflow:auto;background:#fff;padding:12px;color:#000">${JSON.stringify(fullData,null,2)}</pre></details>` : ''}
  ` : '';

  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rudra — Result</title>
  <style>body{font-family:Inter,system-ui;background:#071226;color:#e6eef8;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}.card{background:linear-gradient(180deg,rgba(255,255,255,0.02),rgba(255,255,255,0.01));padding:24px;border-radius:12px;width:720px;max-width:95%}a.btn{display:inline-block;padding:10px 14px;border-radius:8px;background:#7c3aed;color:#fff;text-decoration:none}</style></head><body><div class="card"><h2>${success?'✅ Success':'❌ Failed'}</h2><p>${message}</p>${detailBlock}<div style="margin-top:12px"><a class="btn" href="/">New Search</a></div></div></body></html>`;
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const params = url.searchParams;
      const epicNumber = params.get('epic') || '';
      const captchaIdParam = params.get('captchaId') || '';
      const captchaAnswer = params.get('answer') || '';
      const action = params.get('action') || 'home';

      const CAPTCHA_KEY_B64 = env.CAPTCHA_KEY_B64;
      const PUBKEY_B64 = env.PUBKEY_B64;
      const envNotice = `Server keys: ${CAPTCHA_KEY_B64? 'present' : 'MISSING'} / ${PUBKEY_B64? 'present' : 'MISSING'}`;

      if (!CAPTCHA_KEY_B64 || !PUBKEY_B64) {
        // Fail fast for security — don't proceed without keys
        if (action !== 'home') {
          return new Response('Server misconfigured: missing CAPTCHA_KEY_B64 or PUBKEY_B64 bindings', { status: 500 });
        }
      }

      // POST/search action using manual captcha
      if (action === 'search' && epicNumber && captchaIdParam && captchaAnswer) {
        try {
          const { status, body } = await searchEpic(epicNumber, captchaIdParam, captchaAnswer, PUBKEY_B64);

          if (status === 200 && Array.isArray(body) && body.length > 0) {
            // Optionally cache in KV
            try { env.KV?.put && await env.KV.put(`search_${epicNumber}`, JSON.stringify(body), { expirationTtl: 3600 }); } catch (e) {}
            return new Response(generateResultHTML(true, body[0], `Found voter for EPIC ${epicNumber}`, body), { headers: { 'Content-Type': 'text/html' }, status: 200 });
          }

          return new Response(generateResultHTML(false, null, `Search failed (status ${status}). Try again with a new captcha.`, null), { headers: { 'Content-Type': 'text/html' }, status: 404 });

        } catch (err) {
          return new Response(generateResultHTML(false, null, `Error: ${err.message}`, null), { headers: { 'Content-Type': 'text/html' }, status: 500 });
        }
      }

      // Home: show captcha + form
      if (action === 'home' || action === '') {
        try {
          if (!CAPTCHA_KEY_B64) throw new Error('CAPTCHA_KEY_B64 binding missing');
          const captcha = await getCaptchaFromUpstream(CAPTCHA_KEY_B64);
          const captchaId = captcha.id;
          const captchaImage = captcha.captcha; // already base64
          return new Response(generateHomeHTML(captchaId, captchaImage, epicNumber, envNotice), { headers: { 'Content-Type': 'text/html' }, status: 200 });
        } catch (err) {
          return new Response(`<!doctype html><html><body><h1>Error</h1><p>${err.message}</p><p><a href="/">Retry</a></p></body></html>`, { headers: { 'Content-Type': 'text/html' }, status: 500 });
        }
      }

      return new Response('<html><body><h1>Rudra ECI Search</h1><p>Use ?epic=... or open root</p></body></html>', { headers: { 'Content-Type': 'text/html' }, status: 200 });

    } catch (e) {
      return new Response(`Unhandled error: ${e.message}`, { status: 500 });
    }
  }
};
