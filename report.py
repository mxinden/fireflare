"""Render an HTML report from the JSON runs in results/.

Usage: `uv run report.py` → writes `results/report.html`.

Layout: one explained section per run (what the run actually did, across the
three connection dimensions), then a compact metrics table, then the graphs.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go

ROOT = Path(__file__).parent
RESULTS = ROOT / "results"
REPORT = RESULTS / "report.html"


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


def pretty_ts(ts: str) -> str:
    """'20260608T182634Z' → '2026-06-08 18:26:34 UTC'."""
    try:
        return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").strftime(
            "%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return ts or "-"


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
        return {"proxied": False, "transport": "Direct (no proxy)",
                "tunnel": "-", "origin": origin, "host": None, "port": None}
    # A MASQUE proxy carries the origin either way: classic CONNECT tunnels TCP
    # (origin h1/h2); connect-udp/MASQUE tunnels QUIC (origin h3). Both share
    # proxyInfo.type == "masque", so we infer the tunnel from the origin proto.
    tunnel = "MASQUE connect-udp" if origin == "HTTP/3" else "CONNECT"
    return {"proxied": True, "transport": proxy.get("httpVersion") or "?",
            "tunnel": tunnel, "origin": origin,
            "host": proxy.get("host"), "port": proxy.get("port")}


def short_name(r: dict) -> str:
    """Compact, data-derived name used in the metrics table and graph legends."""
    f = r["facts"]
    if not f["proxied"]:
        return f"Direct · origin {f['origin']}"
    tunnel = "MASQUE" if "udp" in f["tunnel"] else "CONNECT"
    return f"Proxy {f['transport']} · {tunnel} · origin {f['origin']}"


def load_runs() -> list[dict]:
    runs = []
    for p in sorted(RESULTS.glob("*.json")):
        if p.name == "report.html":
            continue
        r = {"file": p.name, "ts": p.stem.rpartition("-")[2],
             **json.loads(p.read_text())}
        r["facts"] = run_facts(r)
        r["name"] = short_name(r)
        runs.append(r)
    return runs


def fmt_bytes(n: float) -> str:
    for unit, scale in [("GB", 1e9), ("MB", 1e6), ("kB", 1e3)]:
        if n >= scale:
            return f"{n / scale:g} {unit}"
    return f"{int(n)} B"


def run_sections(runs: list[dict]) -> str:
    """One explained card per run: the name, a plain-English description of the
    path, and the key facts (the three dimensions plus colo / client IP)."""
    out = []
    for r in runs:
        f = r["facts"]
        trace = r.get("trace") or {}
        if not f["proxied"]:
            expl = (f"Firefox connects straight to the origin with no proxy; "
                    f"the origin serves over {f['origin']}.")
            rows = [("Connection to proxy", "Direct (none)"),
                    ("Proxy tunnel", "n/a"),
                    ("HTTP to origin", f["origin"])]
        elif "udp" in f["tunnel"]:
            expl = (f"Firefox reaches the IP-protection proxy over "
                    f"<b>{f['transport']}</b>, then uses MASQUE "
                    f"<code>connect-udp</code> to tunnel QUIC all the way to the "
                    f"origin, so the origin connection is <b>{f['origin']}</b> "
                    f"end to end.")
            rows = [("Connection to proxy", f["transport"]),
                    ("Proxy tunnel", f["tunnel"]),
                    ("HTTP to origin", f["origin"]),
                    ("Proxy host", f"{f['host']}:{f['port']}")]
        else:
            expl = (f"Firefox reaches the IP-protection proxy over "
                    f"<b>{f['transport']}</b>, then opens a classic "
                    f"<code>CONNECT</code> (TCP) tunnel to the origin, so the "
                    f"origin connection is <b>{f['origin']}</b>.")
            rows = [("Connection to proxy", f["transport"]),
                    ("Proxy tunnel", f["tunnel"]),
                    ("HTTP to origin", f["origin"]),
                    ("Proxy host", f"{f['host']}:{f['port']}")]
        rows += [("Edge colo", trace.get("colo", "-")),
                 ("Client IP (seen by Cloudflare)", trace.get("ip", "-")),
                 ("Run time", pretty_ts(r.get("ts", ""))),
                 ("Result file", r["file"])]
        dl = "".join(
            f"<dt>{html.escape(k)}</dt><dd>{html.escape(str(v))}</dd>"
            for k, v in rows
        )
        out.append(
            f"<section class='run'><h3>{html.escape(r['name'])}</h3>"
            f"<p>{expl}</p><dl>{dl}</dl></section>"
        )
    return "".join(out)


def metrics_table(runs: list[dict]) -> str:
    """Compact metrics-only table; qualitative details live in the run sections."""
    def num(r: dict, key: str, scale: float = 1.0) -> str:
        v = (r.get("summary") or {}).get(key)
        return "-" if v is None else f"{v / scale:.1f}"
    cols = [
        ("Run", lambda r: r["name"]),
        ("Download (Mbps)", lambda r: num(r, "download", 1e6)),
        ("Upload (Mbps)",   lambda r: num(r, "upload", 1e6)),
        ("Latency idle (ms)",   lambda r: num(r, "latency")),
        ("Latency ↓load (ms)",  lambda r: num(r, "downLoadedLatency")),
        ("Latency ↑load (ms)",  lambda r: num(r, "upLoadedLatency")),
        ("Jitter (ms)",         lambda r: num(r, "jitter")),
    ]
    thead = "".join(f"<th>{html.escape(n)}</th>" for n, _ in cols)
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(fn(r))}</td>" for _, fn in cols) + "</tr>"
        for r in runs
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{rows}</tbody></table>"


def fig_bandwidth_boxes(runs: list[dict], points_key: str, title: str) -> go.Figure:
    """One boxplot per size bucket; runs overlay as colored traces."""
    sizes = sorted({p["bytes"] for r in runs for p in r.get(points_key) or []})
    size_labels = [fmt_bytes(s) for s in sizes]
    fig = go.Figure()
    for r in runs:
        by_size: dict[int, list[float]] = {s: [] for s in sizes}
        for p in r.get(points_key) or []:
            by_size[p["bytes"]].append(p["bps"] / 1e6)
        xs, ys = [], []
        for s in sizes:
            xs.extend([fmt_bytes(s)] * len(by_size[s]))
            ys.extend(by_size[s])
        fig.add_box(name=r["name"], x=xs, y=ys, boxpoints="all", jitter=0.3)
    fig.update_layout(
        title=title, yaxis_title="Mbps", boxmode="group",
        xaxis=dict(categoryorder="array", categoryarray=size_labels),
    )
    return fig


def fig_latency_boxes(runs: list[dict]) -> go.Figure:
    buckets = [
        ("unloaded",        "unloadedLatencyPoints"),
        ("during download", "downLoadedLatencyPoints"),
        ("during upload",   "upLoadedLatencyPoints"),
    ]
    fig = go.Figure()
    for r in runs:
        xs, ys = [], []
        for label, key in buckets:
            vals = r.get(key) or []
            xs.extend([label] * len(vals))
            ys.extend(vals)
        fig.add_box(name=r["name"], x=xs, y=ys, boxpoints="all", jitter=0.3)
    fig.update_layout(
        title="Latency", yaxis_title="ms", boxmode="group",
        xaxis=dict(categoryorder="array", categoryarray=[b[0] for b in buckets]),
    )
    return fig


STYLE = """
body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 2em auto;
       padding: 0 1em; line-height: 1.45; color: #222; }
h1 { margin-bottom: 0; }
h2 { border-bottom: 2px solid #eee; padding-bottom: .2em; margin-top: 1.6em; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.4em 0.8em; text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { background: #f4f4f4; }
section.run { border: 1px solid #dde; border-left: 4px solid #4a90d9;
              border-radius: 5px; padding: .4em 1.1em; margin: 1em 0;
              background: #fafcff; }
section.run h3 { margin: .4em 0 .2em; }
section.run p { margin: .2em 0 .6em; }
section.run dl { display: grid; grid-template-columns: max-content 1fr;
                gap: .15em 1.2em; margin: 0; }
section.run dt { font-weight: 600; color: #555; }
section.run dd { margin: 0; font-family: ui-monospace, monospace; font-size: .92em; }
code { background: #eef; padding: 0 .25em; border-radius: 3px; }
"""


def main() -> None:
    runs = load_runs()
    if not runs:
        raise SystemExit("no JSON runs in results/")

    figs = [
        fig_bandwidth_boxes(runs, "downloadBandwidthPoints", "Download by transfer size"),
        fig_bandwidth_boxes(runs, "uploadBandwidthPoints",   "Upload by transfer size"),
        fig_latency_boxes(runs),
    ]
    graphs = [figs[0].to_html(include_plotlyjs="inline", full_html=False)]
    graphs += [f.to_html(include_plotlyjs=False, full_html=False) for f in figs[1:]]

    html_out = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>fireflare report</title>"
        f"<style>{STYLE}</style></head><body>"
        "<h1>fireflare</h1>"
        "<p><a href='https://github.com/mxinden/fireflare'>fireflare</a> drives "
        "Firefox to run the "
        "<a href='https://github.com/cloudflare/speedtest'>@cloudflare/speedtest</a> "
        "library under different connection paths, both directly and through "
        "Firefox's in-browser IP protection proxy, and records "
        "throughput, latency, and jitter. Each run below is one measurement over "
        "one path; its section explains how Firefox reached the proxy "
        "(<a href='https://www.rfc-editor.org/rfc/rfc9113'>HTTP/2</a> vs "
        "<a href='https://www.rfc-editor.org/rfc/rfc9114'>HTTP/3</a>), how the "
        "proxy reached the origin "
        "(<a href='https://www.rfc-editor.org/rfc/rfc9110#section-9.3.6'>CONNECT</a> "
        "vs <a href='https://www.rfc-editor.org/rfc/rfc9298'>MASQUE connect-udp</a>), "
        "and the HTTP version negotiated with Cloudflare.</p>"
        f"<h2>Connection paths compared ({len(runs)})</h2>" + run_sections(runs)
        + metrics_table(runs)
        + "".join(graphs)
        + "</body></html>"
    )
    REPORT.write_text(html_out)
    print(f"wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
