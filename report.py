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
        _, _, ts = p.stem.partition("-")
        runs.append({
            "label": ts or p.stem,
            "ts": ts,
            **json.loads(p.read_text()),
        })
    return runs


def fmt_bytes(n: float) -> str:
    for unit, scale in [("GB", 1e9), ("MB", 1e6), ("kB", 1e3)]:
        if n >= scale:
            return f"{n / scale:g} {unit}"
    return f"{int(n)} B"


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


def fig_bandwidth_boxes(runs: list[dict], points_key: str, title: str) -> go.Figure:
    """One boxplot per size bucket; runs overlay as colored traces."""
    # Preserve size ordering across runs (smallest → largest).
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
        fig.add_box(name=r["label"], x=xs, y=ys, boxpoints="all", jitter=0.3)
    fig.update_layout(
        title=title, yaxis_title="Mbps", boxmode="group",
        xaxis=dict(categoryorder="array", categoryarray=size_labels),
    )
    return fig


def fig_latency_boxes(runs: list[dict]) -> go.Figure:
    buckets = [
        ("unloaded",       "unloadedLatencyPoints"),
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
        fig.add_box(name=r["label"], x=xs, y=ys, boxpoints="all", jitter=0.3)
    fig.update_layout(
        title="Latency", yaxis_title="ms", boxmode="group",
        xaxis=dict(categoryorder="array",
                   categoryarray=[b[0] for b in buckets]),
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

    figs = [
        fig_bandwidth_boxes(runs, "downloadBandwidthPoints", "Download by transfer size"),
        fig_bandwidth_boxes(runs, "uploadBandwidthPoints",   "Upload by transfer size"),
        fig_latency_boxes(runs),
    ]
    parts = [figs[0].to_html(include_plotlyjs="inline", full_html=False)]
    for fig in figs[1:]:
        parts.append(fig.to_html(include_plotlyjs=False, full_html=False))

    html_out = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>fireflare report</title>"
        f"<style>{STYLE}</style></head><body>"
        f"<h1>fireflare — {len(runs)} run(s)</h1>"
        + summary_table(runs)
        + "".join(parts)
        + "</body></html>"
    )
    REPORT.write_text(html_out)
    print(f"wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
