# fireflare

Drives Firefox Nightly to run the [`@cloudflare/speedtest`](https://github.com/cloudflare/speedtest) library (loaded from esm.sh via a local HTML page), records throughput, latency, jitter, plus per-measurement points, and saves structured JSON results. A separate `report.py` renders the JSON files as a self-contained HTML report (summary table + boxplots).

## Report

A rendered comparison report is checked in at [results/report.html](results/report.html): five connection paths side by side, direct HTTP/1.1, direct HTTP/3, and Firefox's in-browser IP-protection (VPN) proxy reached three ways (HTTP/2 CONNECT, HTTP/3 CONNECT, and HTTP/3 MASQUE connect-udp), with throughput, latency, and jitter per path. Generate the data with `uv run main.py --matrix`, then render with `uv run report.py`.

## Status

- **Direct baseline** works (`uv run main.py`).
- **HTTP/3 variant** works (`uv run main.py --h3`, points the library at `bastion.h3.speed.cloudflare.com`).
- **In-browser VPN (IP protection)** works (`uv run main.py --vpn`), routing speedtest traffic through Firefox's IP protection / Fastly proxy. Two layers matter and the report surfaces both: the transport to the proxy (HTTP/2 CONNECT, or HTTP/3 with `--custom-firefox`), and how the origin is reached through it (classic CONNECT, or MASQUE connect-udp). With `--custom-firefox` and Alt-Svc priming, end-to-end MASQUE works: the origin connection itself is HTTP/3 (connect-udp) through the proxy.

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
uv run main.py                # direct baseline (HTTP/1.1)
uv run main.py --h3           # direct HTTP/3 (bastion.h3.speed.cloudflare.com)
uv run main.py --vpn          # route through IP protection (HTTP/2 CONNECT proxy hop)
uv run main.py --vpn --h3 --custom-firefox   # end-to-end HTTP/3 over MASQUE (try build)
uv run main.py --matrix       # all five comparison paths, one result JSON each
uv run main.py --matrix --profile   # also capture a Firefox Profiler "Networking" profile per run
uv run main.py --debug        # tiny measurement set; for plumbing changes
```

Output JSON files land in `results/`, named `<tag>-<utc-timestamp>.json`. The tag records, in order: `debug` (only with `--debug`); on `--vpn` runs `proxy-<v>` for the transport to the proxy (`h3` = QUIC with connect-udp negotiated to the proxy, `h2` = TCP CONNECT); and `origin-<v>` for the HTTP version negotiated with the origin, which reflects the destination tunnel method (`h2` = CONNECT, `h3` = connect-udp end to end).

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
