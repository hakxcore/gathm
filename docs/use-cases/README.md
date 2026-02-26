# Use Cases

Practical workflows showing where Gathm fits in real operations.

## 1) External Surface Triage (Authorized Targets)

Goal: quick first-pass view of domain exposure.

```bash
gathm run subdomains example.com
gathm run dns -t A example.com
gathm run httpprobe example.com
gathm run headersaudit example.com
gathm run robotsaudit example.com
gathm run wafdetect example.com
```

When ProjectDiscovery binaries are available:

```bash
gathm run pdchain -m passive example.com
```

## 2) IP Intelligence Enrichment

Goal: enrich an IP with ownership, network, and reputation context.

```bash
gathm run rdns 8.8.8.8
gathm run rdap 8.8.8.8
gathm run asn 8.8.8.8
gathm run tipcheck -s all 8.8.8.8
```

## 3) Web App Security Header Baseline

Goal: collect and compare baseline security posture for web endpoints.

```bash
gathm run httpprobe https://example.com
gathm run headersaudit https://example.com
gathm run certinfo example.com
gathm run dnssec example.com
```

## 4) Daily Operator Briefing

Goal: quick operational context in one command.

```bash
gathm plan "daily briefing"
gathm parallel 'weather, news, stocks AAPL'
```

## 5) Incident Notes + Action Tracking

Goal: keep local checklist while investigating.

```bash
gathm run todo add "Investigate suspicious host"
gathm run todo add "Check IP reputation in VT/AbuseIPDB"
gathm run todo list
```

## 6) Programmatic Integration via API

Goal: trigger scans from external systems.

```bash
curl -X POST http://127.0.0.1:8080/api/v1/tools/headersaudit/execute \
  -H "Content-Type: application/json" \
  -d '{"args":["example.com"]}'
```

## Guardrails

- Use only on systems you own or are explicitly authorized to test.
- Prefer defensive assessment and observability workflows.
- Configure API auth (`GATHM_API_KEY`) for shared environments.
