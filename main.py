"""Fireflare: run speed.cloudflare.com in Firefox Nightly and save the CSV.

Direct (no proxy) baseline. HTTP CONNECT + MASQUE configs are future phases.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

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
    # Follow redirect to learn the actual filename (for tar.xz vs tar.bz2).
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
    # Tarball contains a top-level `firefox/` dir.
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


def build_driver(firefox: Path, geckodriver: Path, download_dir: Path) -> webdriver.Firefox:
    options = Options()
    options.binary_location = str(firefox)
    # Headed so progress is visible during local development. Flip for CI.

    # Force downloads to our results dir without a prompt.
    options.set_preference("browser.download.folderList", 2)
    options.set_preference("browser.download.dir", str(download_dir))
    options.set_preference("browser.download.useDownloadDir", True)
    options.set_preference("browser.download.manager.showWhenStarting", False)
    options.set_preference(
        "browser.helperApps.neverAsk.saveToDisk",
        "text/csv,application/csv,application/octet-stream,text/plain",
    )

    service = Service(executable_path=str(geckodriver))
    return webdriver.Firefox(service=service, options=options)


def run_speedtest(driver: webdriver.Firefox, download_dir: Path) -> Path:
    """Run speed.cloudflare.com and return the path to the saved CSV."""
    before = set(download_dir.glob("*"))

    driver.get("https://speed.cloudflare.com/")

    # A fresh profile shows a consent dialog with a "Start" button before the
    # test will run.
    start_wait = WebDriverWait(driver, 30)
    start_btn = start_wait.until(
        EC.element_to_be_clickable((
            By.XPATH, "//button[normalize-space()='Start']",
        ))
    )
    start_btn.click()
    print("Clicked Start, waiting for results...")

    # The test runs ~30–60s. The CSV export is an icon-only control: a
    # download-arrow SVG wrapped in a clickable div. We locate the SVG by
    # its path 'd' attribute (surrounding CSS classes are hashed and change
    # between deploys) and click the nearest enclosing div via JS so the
    # React handler fires regardless of which ancestor owns it.
    # The download icon's wrapping structure looks like:
    #   <div data-tooltip-id="single_tooltip_N">
    #     <div class="...">
    #       <div class="...">
    #         <svg><path d="M11.962 7.442..."/></svg>
    # The tooltip-id wrapper is where React attaches the click handler. We
    # match the path 'd' attribute because the surrounding CSS classes are
    # hashed and change between deploys.
    # Locate the CSV button via JS: find the download-arrow SVG path (its
    # 'd' attribute is stable; wrapping CSS classes are hashed) and return
    # the first child div of the data-tooltip-id wrapper — that's the
    # element carrying the React onClick.
    find_btn_js = """
        const paths = document.querySelectorAll('svg path');
        for (const p of paths) {
            const d = p.getAttribute('d');
            if (d && d.startsWith('M11.962 7.442')) {
                const wrapper = p.closest('[data-tooltip-id]');
                return wrapper ? wrapper.querySelector(':scope > div') : null;
            }
        }
        return null;
    """
    print("Waiting for CSV download button...")
    try:
        btn = WebDriverWait(driver, 300, poll_frequency=1).until(
            lambda d: d.execute_script(find_btn_js)
        )
    except TimeoutException:
        sys.exit(
            "Timed out waiting for the CSV download button on speed.cloudflare.com. "
            "The page UI may have changed — inspect it and adjust find_btn_js "
            "in run_speedtest()."
        )
    print("Clicking CSV download button...")
    driver.execute_script("arguments[0].click();", btn)

    # Wait for a new file to appear and for any .part to disappear.
    deadline = time.monotonic() + 30
    new_file: Path | None = None
    while time.monotonic() < deadline:
        current = set(download_dir.glob("*"))
        added = [p for p in current - before if not p.name.endswith(".part")]
        if added and not any(p.name.endswith(".part") for p in current):
            new_file = added[0]
            break
        time.sleep(0.5)
    if new_file is None:
        sys.exit("CSV download did not complete within 30s")

    # Rename to a timestamped, config-tagged filename.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = download_dir / f"direct-{ts}-{new_file.name}"
    new_file.rename(final)
    return final


def main() -> None:
    require_linux_x86_64()
    CACHE.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)

    firefox = ensure_firefox()
    geckodriver = ensure_geckodriver()
    print(f"Using {firefox_version(firefox)}")

    driver = build_driver(firefox, geckodriver, RESULTS.resolve())
    try:
        csv_path = run_speedtest(driver, RESULTS.resolve())
    finally:
        driver.quit()

    print(f"Saved {csv_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
