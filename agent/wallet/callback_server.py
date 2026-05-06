"""Local HTTP callback server for web-based TON wallet connection.

Serves a tiny TON Connect UI page on GET / and receives wallet data
via POST /callback from the browser so the CLI agent can pick up the
connection without blocking the terminal UI.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


# Inline HTML page with TON Connect UI (loaded from CDN).
# The JS reads query params from the URL, shows a connect button,
# and POSTs wallet data back to /callback (or the provided callback_url).
_CONNECT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NOTPUNKS — Connect Wallet</title>
<script src="https://unpkg.com/@tonconnect/ui@2.0.0/dist/tonconnect-ui.min.js"></script>
<style>
  :root {
    --bg: #0f1010;
    --fg: #ffffff;
    --muted: rgba(255,255,255,.64);
    --soft: rgba(255,255,255,.08);
    --line: rgba(255,255,255,.12);
    --blue: #3f94fa;
    --magenta: #ff00e6;
    --green: #00c853;
  }
  * { box-sizing: border-box; }
  html { min-height: 100%; -webkit-font-smoothing: antialiased; }
  body {
    min-height: 100vh;
    margin: 0;
    color: var(--fg);
    font-family: "Geist", Inter, system-ui, -apple-system, sans-serif;
    background-color: var(--bg);
    background-image:
      radial-gradient(circle at 20% 0%, rgba(63,148,250,.10) 0%, transparent 34%),
      radial-gradient(circle at 82% 18%, rgba(255,0,230,.08) 0%, transparent 36%);
    background-attachment: fixed;
  }
  .page {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .card {
    width: min(440px, 100%);
    border: 1px solid var(--line);
    background: rgba(16,17,17,.78);
    backdrop-filter: blur(18px);
    box-shadow: 0 24px 80px rgba(0,0,0,.36);
    padding: 28px;
  }
  .label {
    margin: 0 0 10px;
    text-align: center;
    color: var(--muted);
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12px;
    letter-spacing: .12em;
    text-transform: uppercase;
  }
  h1 {
    margin: 0;
    text-align: center;
    font-size: 28px;
    line-height: 1.08;
    letter-spacing: 0;
  }
  .sub {
    margin: 14px auto 26px;
    max-width: 320px;
    text-align: center;
    color: var(--muted);
    line-height: 1.5;
    font-size: 15px;
  }
  #connect { min-height: 48px; display: flex; align-items: center; justify-content: center; }
  #connect button,
  #connect [role="button"] {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-height: 46px !important;
    width: 100% !important;
    border-radius: 999px !important;
    border: 1px solid rgba(255,255,255,.28) !important;
    background: rgba(255,255,255,.10) !important;
    color: var(--fg) !important;
    font-family: "Geist", Inter, system-ui, sans-serif !important;
    font-weight: 700 !important;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,.08) !important;
    transition: transform .15s linear, background .15s linear, border-color .15s linear !important;
  }
  #connect button:hover,
  #connect [role="button"]:hover {
    background: rgba(255,255,255,.16) !important;
    border-color: rgba(255,255,255,.42) !important;
  }
  #connect button *,
  #connect [role="button"] * {
    color: var(--fg) !important;
    text-align: center !important;
  }
  #connect img,
  #connect svg { filter: none !important; }
  #connect button:hover,
  #connect [role="button"]:hover { transform: translateY(-1px); }
  #status {
    margin-top: 16px;
    padding: 12px 14px;
    border: 1px solid var(--line);
    background: var(--soft);
    color: var(--muted);
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12px;
    line-height: 1.45;
    text-align: center;
    word-break: break-word;
  }
  #status.success {
    border-color: rgba(0,200,83,.42);
    background: rgba(0,200,83,.08);
    color: var(--green);
  }
  .error {
    border-color: rgba(255,0,230,.45) !important;
    background: rgba(255,0,230,.08) !important;
    color: #ff6bf1 !important;
  }
  .footnote {
    margin-top: 18px;
    color: var(--muted);
    text-align: center;
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12px;
  }
  @media (max-width: 520px) {
    .page { padding: 16px; }
    .card { padding: 24px 18px; }
    h1 { font-size: 25px; }
  }
</style>
</head>
<body>
<div class="page">
  <main class="card">
    <p class="label">NOTPUNKS AGENT</p>
    <h1>Connect wallet</h1>
    <p class="sub">Link your TON wallet to verify NFT access and unlock agent modes.</p>
    <div id="connect"></div>
    <div id="status">Waiting for wallet...</div>
    <div class="footnote">TON Connect · secure callback to your terminal</div>
  </main>
</div>
<script>
(function() {
  const params = new URLSearchParams(window.location.search);
  const callbackUrl = params.get('callback_url') || (window.location.origin + '/callback');
  const payload = params.get('payload') || 'notpunks-connect';
  const manifestUrl = params.get('manifest_url') || 'https://app.notpunks.com/tonconnect-manifest.json';

  const tonConnectUI = new TON_CONNECT_UI.TonConnectUI({
    manifestUrl: manifestUrl,
    buttonRootId: 'connect'
  });

  tonConnectUI.setConnectRequestParameters({ state: 'loading' });
  tonConnectUI.setConnectRequestParameters({
    state: 'ready',
    value: { tonProof: payload }
  });

  let sent = false;

  async function sendWallet(wallet) {
    if (!wallet) return;
    if (sent) return;
    sent = true;
    document.getElementById('status').innerText = 'Connected! Sending data to agent...';
    const data = {
      address: wallet.account.address,
      chain: wallet.account.chain,
      public_key: wallet.account.publicKey || '',
      wallet_state_init: wallet.account.walletStateInit || '',
      proof: wallet.connectItems?.tonProof?.proof || null,
      device_info: wallet.device || {},
      session_id: params.get('session_id') || ''
    };
    try {
      const res = await fetch(callbackUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const json = await res.json();
      document.getElementById('status').className = 'success';
      document.getElementById('status').innerText = '✓ Wallet linked. You can close this tab.';
    } catch (e) {
      sent = false;
      document.getElementById('status').className = 'error';
      document.getElementById('status').innerText = 'Error: ' + e.message;
    }
  }

  tonConnectUI.onStatusChange(
    wallet => { sendWallet(wallet); },
    err => {
      document.getElementById('status').className = 'error';
      document.getElementById('status').innerText = 'Wallet error: ' + (err?.message || err);
    }
  );

  tonConnectUI.connectionRestored.then(() => {
    sendWallet(tonConnectUI.wallet);
  });
})();
</script>
</body>
</html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handle GET / (serve connect page) and POST /callback (receive wallet data)."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_cors_headers(self) -> None:
        request_headers = self.headers.get("Access-Control-Request-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", request_headers or "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            body = _CONNECT_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self._send_cors_headers()
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/callback":
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            data = json.loads(post_data.decode("utf-8"))
            self.server.wallet_data = data
            self.server.received = True
        except Exception as e:
            self.server.wallet_error = str(e)
            self.send_response(400)
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(str(e).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(
            json.dumps({"status": "ok", "message": "Wallet connected"}).encode()
        )


class CallbackServer(HTTPServer):
    """Local HTTP server that serves the TON Connect page and receives callbacks.

    Usage:
        server = CallbackServer()
        url = server.start()
        # open browser with url as the entry point
        data = server.wait_for_connection(timeout=300.0)
        server.stop()
    """

    allow_reuse_address = True

    def __init__(self) -> None:
        self.wallet_data: dict[str, Any] | None = None
        self.received = False
        self.wallet_error: str | None = None
        super().__init__(("127.0.0.1", 0), _CallbackHandler)

    @property
    def callback_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}/callback"

    @property
    def page_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}/"

    def start(self) -> str:
        """Start the server in a background thread. Returns the page URL."""
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self.page_url

    def wait_for_connection(self, timeout: float = 300.0) -> dict[str, Any] | None:
        """Block until wallet data is received or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.received:
                return self.wallet_data
            if self.wallet_error:
                raise RuntimeError(f"Callback error: {self.wallet_error}")
            time.sleep(0.5)
        return None

    def stop(self) -> None:
        """Shut down the server."""
        self.shutdown()
        self._thread.join(timeout=5)
