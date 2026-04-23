"""Fireflare: Firefox Nightly runs @cloudflare/speedtest and we save its
structured results as JSON.

Direct (no proxy) baseline. HTTP CONNECT + MASQUE configs are future phases.
"""

from __future__ import annotations

import argparse
import json
import os
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
PROFILE = ROOT / "profile"

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
// Based on @cloudflare/speedtest's defaultConfig. Packet-loss entry dropped
// (needs a TURN server). Counts roughly doubled for a tighter distribution
// per bucket — ~2× total runtime.
// Source: cloudflare/speedtest src/config/defaultConfig.js
const h3 = new URLSearchParams(location.search).has('h3');
const apiBase = h3 ? 'https://bastion.h3.speed.cloudflare.com' : 'https://speed.cloudflare.com';
const endpointOverrides = h3 ? {
  downloadApiUrl: apiBase + '/__down',
  uploadApiUrl:   apiBase + '/__up',
} : {};

async function fetchTrace() {
  // cdn-cgi/trace returns plain text, one `key=value` per line. Gives us
  // client IP (as seen by Cloudflare), colo (edge PoP), country, HTTP
  // version, TLS version, SNI — context a MASQUE comparison needs.
  try {
    const r = await fetch(apiBase + '/cdn-cgi/trace');
    const txt = await r.text();
    return Object.fromEntries(
      txt.trim().split('\\n').filter(l => l.includes('=')).map(l => {
        const i = l.indexOf('=');
        return [l.slice(0, i), l.slice(i + 1)];
      })
    );
  } catch (e) {
    return { error: String((e && e.message) || e) };
  }
}
const st = new SpeedTest({
  ...endpointOverrides,
  loadedLatencyMaxPoints: 50,
  measurements: [
    { type: 'latency', numPackets: 1 },
    { type: 'download', bytes: 1e5, count: 1, bypassMinDuration: true },
    { type: 'latency', numPackets: 50 },
    { type: 'download', bytes: 1e5, count: 15 },
    { type: 'download', bytes: 1e6, count: 15 },
    { type: 'upload', bytes: 1e5, count: 15 },
    { type: 'upload', bytes: 1e6, count: 12 },
    { type: 'download', bytes: 1e7, count: 12 },
    { type: 'upload', bytes: 1e7, count: 8 },
    { type: 'download', bytes: 2.5e7, count: 8 },
    { type: 'upload', bytes: 2.5e7, count: 8 },
    { type: 'download', bytes: 1e8, count: 6 },
    { type: 'upload', bytes: 5e7, count: 6 },
    { type: 'download', bytes: 2.5e8, count: 4 },
  ],
});
st.onFinish = async (results) => {
  const trace = await fetchTrace();
  const out = {
    trace,
    userAgent: navigator.userAgent,
    h3Endpoint: h3,
    summary: results.getSummary(),
    downloadBandwidth: results.getDownloadBandwidth(),
    uploadBandwidth: results.getUploadBandwidth(),
    downloadBandwidthPoints: results.getDownloadBandwidthPoints(),
    uploadBandwidthPoints: results.getUploadBandwidthPoints(),
    unloadedLatencyPoints: results.getUnloadedLatencyPoints(),
    downLoadedLatencyPoints: results.getDownLoadedLatencyPoints(),
    upLoadedLatencyPoints: results.getUpLoadedLatencyPoints(),
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


def scrub_profile_test_stubs() -> None:
    """Remove test-stub prefs from the profile before Firefox launches.

    Past sessions can leave `{server}` / `%(server)s` placeholders in
    prefs.js (FxA auth.uri, telemetry, addons blocklist, …). Firefox caches
    these at startup, so clearing at runtime is too late — FxA keeps
    POSTing to `https://{server}/dummy/fxa/oauth/token`. We rewrite
    prefs.js up front so the cached values are sane from the start.
    """
    prefs = PROFILE / "prefs.js"
    if not prefs.exists():
        return
    original = prefs.read_text().splitlines(keepends=True)
    kept = [
        line for line in original
        if "{server}" not in line and "%(server)s" not in line
    ]
    if len(kept) != len(original):
        prefs.write_text("".join(kept))
        print(f"Scrubbed {len(original) - len(kept)} stub pref(s) from prefs.js")


def build_driver(firefox: Path, geckodriver: Path) -> webdriver.Firefox:
    options = Options()
    options.binary_location = str(firefox)
    # Headed so progress is visible during local development. Flip for CI.
    # Persist the Firefox profile under ./profile/ so state (prefs, caches,
    # any MASQUE config) carries across runs instead of being wiped with the
    # default throwaway profile.
    options.add_argument("-profile")
    options.add_argument(str(PROFILE))
    # Allow Marionette's chrome-context switch — needed to flip privileged
    # prefs at runtime (e.g. browser.ipProtection.userEnabled).
    options.add_argument("-remote-allow-system-access")
    # Disable Firefox's runtime-applied "recommended" WebDriver preferences.
    # Those stub real endpoints (e.g. identity.fxaccounts.auth.uri →
    # https://{server}/dummy/fxa) to isolate tests, which breaks anything
    # that actually needs to talk to Mozilla services — including IP
    # protection. See remote/shared/RecommendedPreferences.sys.mjs.
    options.set_preference("remote.prefs.recommended", False)
    # IP protection's channel filter excludes any request triggered from a
    # loopback origin — which is exactly what our local test page is. Add
    # an inclusion list so the Cloudflare speedtest endpoints are proxied
    # anyway.
    options.set_preference(
        "browser.ipProtection.inclusion.match_patterns",
        json.dumps([
            "*://speed.cloudflare.com/*",
            "*://bastion.h3.speed.cloudflare.com/*",
        ]),
    )
    # Clear LD_LIBRARY_PATH inherited from the parent shell: Firefox devs
    # often point it at a local ASAN build, which breaks the downloaded
    # Nightly's updater and makes Firefox exit 127 before Marionette comes up.
    service = Service(
        executable_path=str(geckodriver),
        log_output=str(CACHE / "geckodriver.log"),
        env={**os.environ, "LD_LIBRARY_PATH": ""},
    )
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


def set_ip_protection(driver: webdriver.Firefox, enabled: bool) -> None:
    """Turn the Firefox IP-protection (MASQUE VPN) proxy on or off at runtime.

    Flipping `browser.ipProtection.userEnabled` alone only changes the
    persisted UI toggle — the proxy doesn't actually start. We also call
    IPPProxyManager.start()/stop() the same way the panel UI does.
    Requires: profile already signed in + `browser.ipProtection.enabled=true`.
    """
    print(f"{'Starting' if enabled else 'Stopping'} IP protection proxy...")
    driver.set_script_timeout(60)
    with driver.context(driver.CONTEXT_CHROME):
        if enabled:
            result = driver.execute_async_script("""
                const done = arguments[arguments.length - 1];
                Services.prefs.setBoolPref('browser.ipProtection.userEnabled', true);
                const { IPPProxyManager } = ChromeUtils.importESModule(
                  'moz-src:///toolkit/components/ipprotection/IPPProxyManager.sys.mjs'
                );
                const failed = [];
                const observer = {
                  observe(subject) {
                    try {
                      const ch = subject.QueryInterface(Ci.nsIHttpChannel);
                      if (!Components.isSuccessCode(ch.status)) {
                        failed.push({
                          url: ch.URI.spec,
                          status: '0x' + (ch.status >>> 0).toString(16),
                        });
                      }
                    } catch (e) {}
                  },
                };
                Services.obs.addObserver(observer, 'http-on-stop-request');
                IPPProxyManager.start(true, false).then(
                  r => {
                    Services.obs.removeObserver(observer, 'http-on-stop-request');
                    done({
                      result: r || {},
                      failed,
                      state: IPPProxyManager.state,
                      isActive: IPPProxyManager.isActive,
                    });
                  },
                  e => {
                    Services.obs.removeObserver(observer, 'http-on-stop-request');
                    done({
                      result: { error: String(e) },
                      failed,
                      state: IPPProxyManager.state,
                      isActive: IPPProxyManager.isActive,
                    });
                  }
                );
            """)
            if result["result"].get("error"):
                if result["failed"]:
                    print("Failed HTTP channels during VPN start:")
                    for f in result["failed"]:
                        print(f"  {f['status']:>12}  {f['url']}")
                sys.exit(f"VPN start failed: {result['result']['error']}")
            if result.get("state") != "active":
                sys.exit(
                    f"VPN start returned without error but proxy is not "
                    f"active (state={result.get('state')!r})"
                )
        else:
            driver.execute_async_script("""
                const done = arguments[arguments.length - 1];
                Services.prefs.setBoolPref('browser.ipProtection.userEnabled', false);
                const { IPPProxyManager } = ChromeUtils.importESModule(
                  'moz-src:///toolkit/components/ipprotection/IPPProxyManager.sys.mjs'
                );
                IPPProxyManager.stop().then(() => done(null));
            """)


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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--h3", action="store_true",
        help="route the test through h3.speed.cloudflare.com (forces HTTP/3)",
    )
    parser.add_argument(
        "--vpn", action="store_true",
        help="enable Firefox's IP protection (MASQUE proxy) before measuring "
             "(requires the profile to already be signed in)",
    )
    args = parser.parse_args()

    require_linux_x86_64()
    CACHE.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    PROFILE.mkdir(exist_ok=True)

    firefox = ensure_firefox()
    geckodriver = ensure_geckodriver()
    print(f"Using {firefox_version(firefox)}")

    server, base_url = serve_page()
    url = base_url + ("?h3=1" if args.h3 else "")
    scrub_profile_test_stubs()
    driver = build_driver(firefox, geckodriver)
    try:
        print(f"Serving {url}")
        # Always flip the VPN toggle to the requested state — the persisted
        # profile may have left it on from a previous run.
        set_ip_protection(driver, args.vpn)
        print("Running speed test...")
        result = collect_results(driver, url)
    finally:
        driver.quit()
        server.shutdown()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = ["direct"]
    if args.vpn:
        parts.append("vpn")
    if args.h3:
        parts.append("h3")
    tag = "-".join(parts)
    out_path = RESULTS / f"{tag}-{ts}.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"Saved {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
