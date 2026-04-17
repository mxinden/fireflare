"""Render an HTML report from all JSON runs in results/.

Usage: `uv run report.py` → writes `results/report.html`.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import plotly.graph_objects as go

ROOT = Path(__file__).parent
RESULTS = ROOT / "results"
REPORT = RESULTS / "report.html"


def load_runs() -> list[dict]:
    runs = []
    for p in sorted(RESULTS.glob("*.json")):
        config, _, ts = p.stem.partition("-")
        runs.append({
            "label": f"{config} @ {ts}",
            "config": config,
            "ts": ts,
            **json.loads(p.read_text()),
        })
    return runs


def summary_table(runs: list[dict]) -> str:
    cols = [
        ("run", lambda r: r["label"]),
        ("download (Mbps)", lambda r: f"{r['summary']['download'] / 1e6:.1f}"),
        ("upload (Mbps)",   lambda r: f"{r['summary']['upload']   / 1e6:.1f}"),
        ("latency idle (ms)",     lambda r: f"{r['summary']['latency']:.1f}"),
        ("latency ↓load (ms)",    lambda r: f"{r['summary']['downLoadedLatency']:.1f}"),
        ("latency ↑load (ms)",    lambda r: f"{r['summary']['upLoadedLatency']:.1f}"),
        ("jitter idle (ms)",      lambda r: f"{r['summary']['jitter']:.1f}"),
        ("jitter ↓load (ms)",     lambda r: f"{r['summary']['downLoadedJitter']:.1f}"),
        ("jitter ↑load (ms)",     lambda r: f"{r['summary']['upLoadedJitter']:.1f}"),
    ]
    thead = "".join(f"<th>{html.escape(name)}</th>" for name, _ in cols)
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(fn(r))}</td>" for _, fn in cols) + "</tr>"
        for r in runs
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{rows}</tbody></table>"


def fig_bandwidth_points(runs: list[dict]) -> go.Figure:
    fig = go.Figure()
    for r in runs:
        for key, suffix in [("downloadBandwidthPoints", "down"),
                            ("uploadBandwidthPoints", "up")]:
            pts = r.get(key) or []
            if not pts:
                continue
            fig.add_scatter(
                name=f"{r['label']} {suffix}",
                x=[p["transferSize"] for p in pts],
                y=[p["bps"] / 1e6 for p in pts],
                mode="markers",
            )
    fig.update_layout(
        xaxis_type="log", xaxis_title="transfer size (bytes, log)",
        yaxis_title="Mbps",
        title="Per-request bandwidth vs. transfer size",
    )
    return fig


STYLE = """
body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 2em auto; padding: 0 1em; }
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #ccc; padding: 0.4em 0.8em; text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { background: #f4f4f4; }
"""


def main() -> None:
    runs = load_runs()
    if not runs:
        raise SystemExit("no JSON runs in results/")

    chart = fig_bandwidth_points(runs).to_html(
        include_plotlyjs="inline", full_html=False
    )

    html_out = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>fireflare report</title>"
        f"<style>{STYLE}</style></head><body>"
        f"<h1>fireflare — {len(runs)} run(s)</h1>"
        + summary_table(runs)
        + chart
        + "</body></html>"
    )
    REPORT.write_text(html_out)
    print(f"wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
