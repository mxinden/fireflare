# fireflare

Drives Firefox Nightly to run the [`@cloudflare/speedtest`](https://github.com/cloudflare/speedtest) library (loaded from esm.sh via a local HTML page), records throughput, latency, jitter, plus per-measurement points, and saves structured JSON results. A separate `report.py` renders the JSON files as a self-contained HTML report (summary table + boxplots).

## Status

- **Direct baseline** — works (`uv run main.py`).
- **HTTP/3 variant** — works (`uv run main.py --h3`, points the library at `bastion.h3.speed.cloudflare.com`).
- **In-browser VPN (IP protection)** — works (`uv run main.py --vpn`), routes speedtest traffic through Firefox's IP protection / Fastly proxy. Two independent layers matter and the report surfaces both. (1) **Transport to the proxy** (Firefox to the Fastly server): HTTP/2 on Nightly, HTTP/3 with connect-udp negotiated under `--custom-firefox` (a try build). (2) **Destination tunnel method** (how the origin is reached through the proxy): classic CONNECT in both cases, so the origin negotiates h2. An h3 transport to the proxy does not imply MASQUE to the origin. End-to-end MASQUE (connect-udp to the origin, which would let the origin be h3) does not yet establish: Firefox attempts it but falls back to CONNECT.

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
uv run main.py --vpn          # route through IP protection (h2 CONNECT proxy hop)
uv run main.py --vpn --h3     # IP protection + h3 origin endpoint
uv run main.py --vpn --h3 --custom-firefox   # h3 transport to proxy, origin still CONNECT/h2 (try build)
uv run main.py --debug        # tiny measurement set; for plumbing changes
```

Output JSON files land in `results/`, named `<tag>-<utc-timestamp>.json`. The tag records, in order: `debug` (only with `--debug`); on `--vpn` runs `proxy-<v>` for the transport to the proxy (`h3` = QUIC with connect-udp negotiated to the proxy, `h2` = TCP CONNECT); and `origin-<v>` for the HTTP version negotiated with the origin, which reflects the destination tunnel method (`h2` = CONNECT, `h3` = connect-udp end to end).

## Report

```
uv run report.py
```

Writes `results/report.html` (self-contained, Plotly JS inlined). Summary table shows colo, client IP, origin HTTP version, proxy hop (for `--vpn` runs), throughput, latency, and jitter per run; boxplots show per-request bandwidth by transfer size and latency distributions.

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
