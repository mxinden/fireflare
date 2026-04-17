# fireflare

Automates Firefox Nightly running [speed.cloudflare.com](https://speed.cloudflare.com/) and saves the CSV results.

## Status

Phase 1 (current): **Direct baseline** (no proxy).

Planned next:
- Phase 2: HTTP CONNECT via Fastly proxy.
- Phase 3: MASQUE connect-udp via Fastly proxy.

## Requirements

- Linux x86_64
- [uv](https://docs.astral.sh/uv/)
- Python 3.11+

Firefox Nightly and geckodriver are downloaded on first run into `.cache/`.

## Run

```
uv run main.py
```

Output CSVs land in `results/`, named `direct-<utc-timestamp>-<original>.csv`.
