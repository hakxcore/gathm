# Termux latency measurements

Run in the Gathm checkout on the phone, using the same Python as Pilot:

```sh
pilot/venv/bin/python bench/termux_latency.py --repeat 3 --output "$TMPDIR/gathm-startup.json"
```

For pip installations, use that environment's Python. The harness itself uses
only the standard library; the Pilot import measurement needs Pilot's dependencies.

To include inference and full API chat:

```sh
pilot/venv/bin/python bench/termux_latency.py \
  --model-url http://127.0.0.1:8081/v1 \
  --api-url http://127.0.0.1:8080 \
  --repeat 3 --output "$TMPDIR/gathm-latency.json"
```

The servers must already be running. Run one benchmark at a time and avoid
interactive model requests while it runs. Keep the phone unlocked with Termux
visible, and record temperature and charging state when comparing runs.

The report separates CLI startup, Bash metadata discovery, fresh Python process
imports, model time to first content token, and full API greeting latency.
Reports are saved after every sample; errors are recorded and produce a nonzero
exit status. First samples can be much slower because file pages and imports
are cold. Report those separately from subsequent samples. No tool is executed.

`--prompt-cache` additionally sends four real Pilot prompts with changing tool
lists, generating one token each. A unique benchmark prefix excludes prompts
left in the server cache by earlier sessions. Keep the first sample separate:
it warms the constant prefix. These samples measure prompt processing, **not**
tool success or useful-answer latency. One-token generation throughput is not a
meaningful number. Prompt capture requires Pilot's dependencies and forces its
connectivity description to “online” without performing the network check.

Use `--root /path/to/other/checkout` to measure an isolated candidate checkout.
USB-forwarded HTTP timings include transport overhead; CLI/import timings always
belong to the machine running this script, so run it inside Termux for phone
measurements. Greeting and direct-model prompts differ; subtracting their times
does not isolate framework overhead.

The metadata parser optimization and measurements from September 24, 2026 are
documented in [the device report](../docs/performance/termux-2026-09-24.md).
