# Examples

This page collects runnable examples across interfaces.

## Agent CLI Examples

### 1) Quick tool execution

```bash
gathm-agent run weather "San Francisco"
gathm-agent run dns -t MX gmail.com
gathm-agent run headersaudit example.com
```

### 2) Natural-language routing

```bash
gathm-agent ask "check reverse dns for 8.8.8.8"
gathm-agent ask "find CVE details for CVE-2024-3094"
gathm-agent ask "show latest tech headlines"
```

### 3) Chaining

```bash
gathm-agent chain 'geo -w | ipinfo'
```

### 4) Parallel execution

```bash
gathm-agent parallel 'weather Tokyo, news, cryptocurrency'
```

### 5) JSON mode for scripts

```bash
gathm-agent ask "dns txt openai.com" --json
gathm-agent list --json
gathm-agent health all --json
```

## API Examples

### 1) List tools

```bash
curl http://127.0.0.1:8080/api/v1/tools
```

### 2) Execute one tool

```bash
curl -X POST http://127.0.0.1:8080/api/v1/tools/urlscan/execute \
  -H "Content-Type: application/json" \
  -d '{"args":["example.com"]}'
```

### 3) Ask agent

```bash
curl -X POST http://127.0.0.1:8080/api/v1/agent/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"inspect certificate expiry for github.com"}'
```

### 4) Execute pipeline

```bash
curl -X POST http://127.0.0.1:8080/api/v1/agent/chain \
  -H "Content-Type: application/json" \
  -d '{"pipeline":"geo -w | ipinfo"}'
```

## Pilot Examples

After starting Pilot:

```text
weather in Delhi
check if alice@example.com was breached
find definition of resilience
/tools
/model
```

## Classic Launcher Example

```bash
gathm
```

Then select a tool from menu and provide args in the prompt dialog.
