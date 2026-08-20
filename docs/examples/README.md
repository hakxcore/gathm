# Examples

This page collects runnable examples across interfaces.

## Agent CLI Examples

### 1) Quick tool execution

```bash
gathm run weather "San Francisco"
gathm run dns -t MX gmail.com
gathm run headersaudit example.com
```

### 2) Natural-language routing

```bash
gathm ask "check reverse dns for 8.8.8.8"
gathm ask "find CVE details for CVE-2024-3094"
gathm ask "show latest tech headlines"
```

### 3) Chaining

```bash
gathm chain 'geo -w | ipinfo'
```

### 4) Parallel execution

```bash
gathm parallel 'weather Tokyo, news, cryptocurrency'
```

### 5) JSON mode for scripts

```bash
gathm ask "dns txt openai.com" --json
gathm list --json
gathm health all --json
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

## Launcher Example

```bash
gathm
```

Opens the GUI at `http://127.0.0.1:8080` in your browser and starts Pilot in the
terminal. Pilot only: `gathm tui`. GUI only: `gathm gui`. Shut the GUI down with
`gathm stop`.
