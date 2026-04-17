# fireflare

Drives Firefox Nightly to run the [`@cloudflare/speedtest`](https://github.com/cloudflare/speedtest) library (loaded from esm.sh via a local HTML page) and saves structured JSON results.

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

Output JSON files land in `results/`, named `direct-<utc-timestamp>.json`.

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
