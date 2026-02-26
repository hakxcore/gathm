# Use Cases

Practical workflows showing where Gathm fits in real operations.

## 1) External Surface Triage (Authorized Targets)

Goal: quick first-pass view of domain exposure.

```bash
gathm-agent run subdomains example.com
gathm-agent run dns -t A example.com
gathm-agent run httpprobe example.com
gathm-agent run headersaudit example.com
gathm-agent run robotsaudit example.com
gathm-agent run wafdetect example.com
```

When ProjectDiscovery binaries are available:

```bash
gathm-agent run pdchain -m passive example.com
```

## 2) IP Intelligence Enrichment

Goal: enrich an IP with ownership, network, and reputation context.

```bash
gathm-agent run rdns 8.8.8.8
gathm-agent run rdap 8.8.8.8
gathm-agent run asn 8.8.8.8
gathm-agent run tipcheck -s all 8.8.8.8
```

## 3) Web App Security Header Baseline

Goal: collect and compare baseline security posture for web endpoints.

```bash
gathm-agent run httpprobe https://example.com
gathm-agent run headersaudit https://example.com
gathm-agent run certinfo example.com
gathm-agent run dnssec example.com
```

## 4) Daily Operator Briefing

Goal: quick operational context in one command.

```bash
gathm-agent plan "daily briefing"
gathm-agent parallel 'weather, news, stocks AAPL'
```

## 5) Incident Notes + Action Tracking

Goal: keep local checklist while investigating.

```bash
gathm-agent run todo add "Investigate suspicious host"
gathm-agent run todo add "Check IP reputation in VT/AbuseIPDB"
gathm-agent run todo list
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
