"""The three connection dimensions of a run, and how to name them.

Shared by `main.py` (which needs a name for a run that is still in flight) and
`report.py` (which needs one for a run that has finished), so the vocabulary
appears once. Deliberately free of heavy imports: it sits on the measurement
path.

Two ways in:
  - `run_facts` reads the dimensions off a finished run's recorded data, so a
    report says what actually happened rather than what was asked for.
  - `expected_facts` predicts them from the flags `run_once` was called with,
    for the console header and the browser window while a run is in flight.
    A prediction, so callers should mark it as one; the matrix summary prints
    the `run_facts` version once the numbers are in.
"""

from __future__ import annotations

# A direct run to the non-h3 endpoint is served over whichever of HTTP/1.1 or
# HTTP/2 Cloudflare picks, and the same is true through a CONNECT tunnel. Only
# the measured result says which, so the prediction names both.
EITHER_H1_H2 = "HTTP/1.1 or HTTP/2"


def pretty_http(v: str | None) -> str:
    """Normalize a verbose HTTP-version label ('http/1.1', 'HTTP <= 1.1', …)."""
    if not v:
        return "-"
    s = v.lower()
    if "3" in s:
        return "HTTP/3"
    if "2" in s:
        return "HTTP/2"
    if "1" in s:
        return "HTTP/1.1"
    return v


def direct_facts(origin: str) -> dict:
    return {"proxied": False, "transport": "Direct (no proxy)", "tunnel": "-",
            "origin": origin, "host": None, "port": None}


def run_facts(r: dict) -> dict:
    """Derive the three connection dimensions from the run's recorded data, so
    the report reflects what actually happened rather than the file name:
      - transport: how Firefox reached the proxy (HTTP/2 vs HTTP/3), or direct
      - tunnel: how the proxy reached the origin (CONNECT vs connect-udp/MASQUE)
      - origin: the HTTP version negotiated with Cloudflare
    """
    origin = pretty_http((r.get("trace") or {}).get("http"))
    proxy = r.get("proxy")
    if not proxy:
        return direct_facts(origin)
    # A MASQUE proxy carries the origin either way: classic CONNECT tunnels TCP
    # (origin h1/h2); connect-udp/MASQUE tunnels QUIC (origin h3). Both share
    # proxyInfo.type == "masque", so we infer the tunnel from the origin proto.
    tunnel = "MASQUE connect-udp" if origin == "HTTP/3" else "CONNECT"
    return {"proxied": True, "transport": proxy.get("httpVersion") or "?",
            "tunnel": tunnel, "origin": origin,
            "host": proxy.get("host"), "port": proxy.get("port")}


def expected_facts(*, vpn: bool, h3: bool, disable_h3: bool = False) -> dict:
    """Predict the dimensions from the flags a run was started with.

    `h3` picks the QUIC-only origin endpoint, which is what decides the tunnel:
    a QUIC inner connection has to be carried by connect-udp, a TCP inner one by
    classic CONNECT. `disable_h3` clears network.http.http3.enable, which drops
    the proxy hop itself to HTTP/2.
    """
    if not vpn:
        return direct_facts("HTTP/3" if h3 else EITHER_H1_H2)
    return {"proxied": True,
            "transport": "HTTP/2" if disable_h3 else "HTTP/3",
            "tunnel": "MASQUE connect-udp" if h3 else "CONNECT",
            "origin": "HTTP/3" if h3 else EITHER_H1_H2,
            "host": None, "port": None}


def short_name(f: dict) -> str:
    """Compact name used in the metrics table, graph legends, and the live
    console header. Takes a facts dict from either entry point above."""
    if not f["proxied"]:
        return f"Direct · origin {f['origin']}"
    tunnel = "MASQUE" if "udp" in f["tunnel"] else "CONNECT"
    return f"Proxy {f['transport']} · {tunnel} · origin {f['origin']}"
