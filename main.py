"""Fireflare: Firefox Nightly runs @cloudflare/speedtest and we save its
structured results as JSON.

Direct (no proxy) baseline. HTTP CONNECT + MASQUE configs are future phases.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

ROOT = Path(__file__).parent
CACHE = ROOT / ".cache"
RESULTS = ROOT / "results"

FIREFOX_NIGHTLY_URL = (
    "https://download.mozilla.org/?product=firefox-nightly-latest-ssl"
    "&os=linux64&lang=en-US"
)
GECKODRIVER_LATEST_API = (
    "https://api.github.com/repos/mozilla/geckodriver/releases/latest"
)

SPEEDTEST_HTML = """\
<!doctype html>
<html><head><meta charset="utf-8"><title>fireflare</title></head>
<body>
<pre id="log">running @cloudflare/speedtest...</pre>
<script type="module">
import SpeedTest from 'https://esm.sh/@cloudflare/speedtest';
const log = document.getElementById('log');
// Verbatim from @cloudflare/speedtest's defaultConfig, minus the packetLoss
// entry (which needs a TURN server we don't provide).
// Source: cloudflare/speedtest src/config/defaultConfig.js
const st = new SpeedTest({
  measurements: [
    { type: 'latency', numPackets: 1 },
    { type: 'download', bytes: 1e5, count: 1, bypassMinDuration: true },
    { type: 'latency', numPackets: 20 },
    { type: 'download', bytes: 1e5, count: 9 },
    { type: 'download', bytes: 1e6, count: 8 },
    { type: 'upload', bytes: 1e5, count: 8 },
    { type: 'upload', bytes: 1e6, count: 6 },
    { type: 'download', bytes: 1e7, count: 6 },
    { type: 'upload', bytes: 1e7, count: 4 },
    { type: 'download', bytes: 2.5e7, count: 4 },
    { type: 'upload', bytes: 2.5e7, count: 4 },
    { type: 'download', bytes: 1e8, count: 3 },
    { type: 'upload', bytes: 5e7, count: 3 },
    { type: 'download', bytes: 2.5e8, count: 2 },
  ],
});
st.onFinish = (results) => {
  const out = {
    summary: results.getSummary(),
    downloadBandwidth: results.getDownloadBandwidth(),
    uploadBandwidth: results.getUploadBandwidth(),
    downloadBandwidthPoints: results.getDownloadBandwidthPoints(),
    uploadBandwidthPoints: results.getUploadBandwidthPoints(),
  };
  window.__fireflare_result = out;
  log.textContent = JSON.stringify(out, null, 2);
};
st.onError = (err) => {
  window.__fireflare_error = String((err && err.message) || err);
  log.textContent = 'error: ' + window.__fireflare_error;
};
</script>
</body></html>
"""


def require_linux_x86_64() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        sys.exit(f"fireflare currently only supports Linux x86_64 "
                 f"(got {platform.system()} {platform.machine()})")


def download(url: str, dest: Path) -> None:
    print(f"  downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def ensure_firefox() -> Path:
    """Download Firefox Nightly if not cached. Return path to the binary."""
    install_dir = CACHE / "firefox"
    binary = install_dir / "firefox"
    if binary.exists():
        return binary

    print("Fetching Firefox Nightly...")
    req = urllib.request.Request(FIREFOX_NIGHTLY_URL, method="HEAD")
    with urllib.request.urlopen(req) as resp:
        final_url = resp.url
    suffix = ".tar.xz" if final_url.endswith(".tar.xz") else ".tar.bz2"
    archive = CACHE / f"firefox-nightly{suffix}"
    download(final_url, archive)

    print(f"  extracting to {install_dir}")
    if install_dir.exists():
        shutil.rmtree(install_dir)
    CACHE.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(CACHE)
    archive.unlink()

    if not binary.exists():
        sys.exit(f"expected {binary} after extracting Firefox Nightly")
    return binary


def ensure_geckodriver() -> Path:
    """Download the latest geckodriver release if not cached."""
    binary = CACHE / "geckodriver"
    if binary.exists():
        return binary

    print("Fetching latest geckodriver release metadata...")
    with urllib.request.urlopen(GECKODRIVER_LATEST_API) as resp:
        release = json.load(resp)
    asset = next(
        (a for a in release["assets"] if a["name"].endswith("linux64.tar.gz")),
        None,
    )
    if asset is None:
        sys.exit("could not find a linux64 geckodriver asset in latest release")

    archive = CACHE / asset["name"]
    download(asset["browser_download_url"], archive)
    with tarfile.open(archive) as tf:
        tf.extractall(CACHE)
    archive.unlink()

    binary.chmod(0o755)
    return binary


def firefox_version(firefox: Path) -> str:
    out = subprocess.run(
        [str(firefox), "--version"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def build_driver(firefox: Path, geckodriver: Path) -> webdriver.Firefox:
    options = Options()
    options.binary_location = str(firefox)
    # Headed so progress is visible during local development. Flip for CI.
    service = Service(executable_path=str(geckodriver))
    return webdriver.Firefox(service=service, options=options)


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(SPEEDTEST_HTML.encode("utf-8"))

    def log_message(self, *_args):
        pass


def serve_page() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/"


def collect_results(driver: webdriver.Firefox, url: str, timeout_s: int = 300) -> dict:
    driver.get(url)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        err = driver.execute_script("return window.__fireflare_error || null;")
        if err:
            sys.exit(f"speed test error: {err}")
        result = driver.execute_script("return window.__fireflare_result || null;")
        if result is not None:
            return result
        time.sleep(1)
    sys.exit(f"speed test did not complete within {timeout_s}s")


def main() -> None:
    require_linux_x86_64()
    CACHE.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)

    firefox = ensure_firefox()
    geckodriver = ensure_geckodriver()
    print(f"Using {firefox_version(firefox)}")

    server, url = serve_page()
    driver = build_driver(firefox, geckodriver)
    try:
        print(f"Serving {url}")
        print("Running speed test...")
        result = collect_results(driver, url)
    finally:
        driver.quit()
        server.shutdown()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS / f"direct-{ts}.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"Saved {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
