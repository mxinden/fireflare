# fireflare

Drives Firefox Nightly to run the [`@cloudflare/speedtest`](https://github.com/cloudflare/speedtest) library (loaded from esm.sh via a local HTML page), records throughput, latency, jitter, plus per-measurement points, and saves structured JSON results. A separate `report.py` renders the JSON files as a self-contained HTML report (summary table + boxplots).

## Status

- **Direct baseline** — works (`uv run main.py`).
- **HTTP/3 variant** — works (`uv run main.py --h3`, points the library at `bastion.h3.speed.cloudflare.com`).
- **In-browser VPN (IP protection)** — works (`uv run main.py --vpn`), routes speedtest traffic through Firefox's IP protection / Fastly proxy. Today it's HTTP CONNECT over TCP, so `--vpn --h3` silently downgrades to HTTP/2 over the tunnel; actual MASQUE connect-udp support will come later.

## Requirements

- Linux x86_64
- [uv](https://docs.astral.sh/uv/)
- Python 3.11+

Firefox Nightly and geckodriver are downloaded on first run into `.cache/`.

## One-time setup for `--vpn`

The VPN runs need a Firefox profile that's already signed in and has the feature flipped on. Do this once:

```
env LD_LIBRARY_PATH='' ./.cache/firefox/firefox -profile ./profile
```

Then in that Firefox window:
1. Go to `about:config`, set `browser.ipProtection.enabled = true`.
2. Sign in to a Firefox Account.
3. Quit Firefox cleanly (releasing the profile lock).

The persistent profile lives at `./profile/` (gitignored) and is reused across runs. `--vpn` runs toggle the proxy on; runs without `--vpn` turn it off.

## Run

```
uv run main.py                # direct baseline
uv run main.py --h3           # force h3.speed.cloudflare.com endpoint
uv run main.py --vpn          # route through IP protection
uv run main.py --vpn --h3     # (currently downgrades to h2 over tunnel)
```

Output JSON files land in `results/`, named `<tag>-<utc-timestamp>.json` where `<tag>` composes `direct`, optionally `vpn`, and optionally `h3`.

## Report

```
uv run report.py
```

Writes `results/report.html` (self-contained, Plotly JS inlined). Summary table shows colo, client IP, throughput, latency, jitter per run; boxplots show per-request bandwidth by transfer size and latency distributions.

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
