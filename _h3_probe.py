"""Diagnostic: bring the IP-protection MASQUE proxy up the same way main.py
does, then repeatedly load the h3 origin and dump Firefox's socket table +
HTTP connection table. Goal: confirm the outer connect-udp/h3 UDP socket is
present, and watch whether the inner origin connection upgrades to h3 via
Alt-Svc on a reconnect (no HTTPS RR is available since DNS is proxied).
"""
from __future__ import annotations

import json
import os
import time

from main import (
    CACHE,
    FIREFOX_CUSTOM_URL,
    build_driver,
    ensure_firefox,
    ensure_geckodriver,
    scrub_profile_test_stubs,
    set_ip_protection,
)

ORIGIN = "https://bastion.h3.speed.cloudflare.com/cdn-cgi/trace"
MOZLOG = CACHE / "mozlog"
# Try build with bc9bc791bad0 "Skip Alt-Svc validation to allow h3 outer VPN
# connection" (Bug 2005211): marks Alt-Svc h3 mappings validated immediately
# instead of via a speculative transaction that can't traverse the proxy
# CONNECT tunnel. Goal: get h3 inner (connect-udp to origin), not CONNECT/h2.
DEBUG_BUILD_URL = (
    "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/"
    "Y_qQJGw4S4Sda1ML9OIr7A/runs/0/artifacts/public/build/target.tar.xz"
)


def dump_tables(driver) -> dict:
    driver.set_script_timeout(15)
    with driver.context(driver.CONTEXT_CHROME):
        return driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const dashboard = Cc['@mozilla.org/network/dashboard;1']
              .getService(Ci.nsIDashboard);
            dashboard.requestSockets(sock => {
              dashboard.requestHttpConnections(http => {
                done({
                  sockets: (sock.sockets || []).map(s => ({
                    host: s.host, port: s.port, type: s.type, active: s.active,
                  })),
                  connections: (http.connections || []).map(c => ({
                    host: c.host, port: c.port, httpVersion: c.httpVersion,
                    active: (c.active || []).length, idle: (c.idle || []).length,
                  })),
                });
              });
            });
        """)


INTERESTING = ("fastly-masque", "cloudflare", "vpn.mozilla")


def show(label: str, tables: dict) -> None:
    print(f"\n===== {label} =====")
    udp = [s for s in tables["sockets"] if s["type"] == "UDP"]
    print(f"UDP sockets: {len(udp)}")
    for s in udp:
        print(f"  UDP  {s['host']}:{s['port']}  active={s['active']}")
    print("HTTP connections (interesting hosts):")
    for c in tables["connections"]:
        if any(k in (c["host"] or "") for k in INTERESTING) or (
            c["host"] == "*" and c["port"] == 0
        ):
            print(f"  {c['httpVersion']:10} {c['host']}:{c['port']} "
                  f"active={c['active']} idle={c['idle']}")


def trace_http(driver) -> str:
    driver.get(ORIGIN)
    time.sleep(2)
    body = driver.find_element("tag name", "body").text
    line = next((l for l in body.splitlines() if l.startswith("http=")), "http=?")
    return line


def disable_http_cache(driver) -> None:
    """Disable disk+memory HTTP cache so every load is a live network response
    (which runs the Alt-Svc processing path) and reflects the live connection,
    instead of being revalidated/served from the persistent profile's cache."""
    with driver.context(driver.CONTEXT_CHROME):
        driver.execute_script("""
            Services.prefs.setBoolPref('browser.cache.disk.enable', false);
            Services.prefs.setBoolPref('browser.cache.memory.enable', false);
        """)


def force_fresh_connections(driver) -> None:
    """Drop all live connections and clear the h3-excluded list so the next
    request opens a brand-new connection that can pick h3 (connect-udp) from a
    stored Alt-Svc mapping, instead of reusing the live h2 CONNECT tunnel.
    Same calls test_http3_proxy.js uses; reset clears mExcludedHttp3Origins."""
    with driver.context(driver.CONTEXT_CHROME):
        # TODO: second one needed? Happy eyeballs v3 doesn't use the exclusion list.
        driver.execute_script("""
            Services.obs.notifyObservers(null, 'net:cancel-all-connections');
            Services.obs.notifyObservers(null, 'network:reset-http3-excluded-list');
        """)


def main() -> None:
    # MOZ_LOG is read from the env build_driver passes through to Firefox.
    # nsHttp shows the CONNECT vs connect-udp request method to the proxy and
    # the per-destination conn-info; neqo shows QUIC connection setup/teardown.
    for stale in CACHE.glob("mozlog*"):
        stale.unlink()
    # Rust crate modules must use the `::*` form in MOZ_LOG, otherwise
    # LogModule::SetLevel's `strstr(name, "::")` gate never registers them and
    # neqo silently falls through to env_logger/RUST_LOG on stderr. trace(5)
    # needs a debug build (opt strips it via log/release_max_level_info).
    # neqo runs in the socket process, so its lines land in a per-PID child
    # MOZ_LOG_FILE (mozlog.child-*.moz_log), interleaved with that process's
    # nsHttp output.
    os.environ["MOZ_LOG"] = (
        "timestamp,sync,nsHttp:5,"
        "neqo_transport::*:5,neqo_http3::*:5,neqo_common::*:3"
    )
    os.environ["MOZ_LOG_FILE"] = str(MOZLOG)

    firefox = ensure_firefox(DEBUG_BUILD_URL)
    geckodriver = ensure_geckodriver()
    scrub_profile_test_stubs()
    driver = build_driver(firefox, geckodriver)
    try:
        set_ip_protection(driver, True)
        disable_http_cache(driver)
        # load #0 primes Alt-Svc over h2; before each later load, force fresh
        # connections so Firefox must reconnect and can choose h3 (connect-udp)
        # from the now-stored Alt-Svc instead of reusing the h2 CONNECT tunnel.
        for i in range(3):
            if i > 0:
                force_fresh_connections(driver)
                time.sleep(3)
            http = trace_http(driver)
            print(f"\nload #{i}: cloudflare reports {http}")
            show(f"after load #{i}", dump_tables(driver))
            time.sleep(2)
    finally:
        set_ip_protection(driver, False)
        driver.quit()


if __name__ == "__main__":
    main()
