# Agent Orchestrator (`gathm-agent`)

`gathm-agent` is the main control plane for Gathm. It adds orchestration features on top of the raw tool scripts:

- Intent routing (`ask`)
- Task decomposition (`plan`)
- Chaining and parallel execution (`chain`, `parallel`)
- Health, healing, retries, fallback, and circuit-breaker handling
- Caching and rate limiting

## Run

```bash
gathm-agent --help
```

If `gathm-agent` is not on `PATH`:

```bash
bash agent/orchestrator.sh --help
```

## Core Commands

- `run <tool> [args]`: execute one tool with recovery pipeline
- `ask <query>`: map natural language query to a tool
- `plan <description>`: create stepwise execution plan
- `engineer <task>`: delegate engineering task to engineer interface
- `chain '<t1 | t2 | t3>'`: pipe outputs through multiple tools
- `parallel '<t1, t2, t3>'`: run multiple tools concurrently
- `new-tool <name> [opts]`: scaffold new tool + manifest
- `list [--json]`: list tools with metadata
- `health [tool|all]`: health checks
- `heal [tool|all]`: self-heal broken tools
- `cache [stats|clear|purge|invalidate <tool>]`: cache management
- `status`: runtime and metrics summary
- `monitor [interval]`: continuous health monitor with auto-heal

## Common Examples

```bash
gathm-agent run weather "New York"
gathm-agent ask "check tls cert expiry for github.com"
gathm-agent chain 'geo -w | ipinfo'
gathm-agent parallel 'weather London, news, cryptocurrency'
gathm-agent health all
gathm-agent cache stats
gathm-agent list --json
```

## JSON Output

Use `--json` (or `GATHM_OUTPUT_MODE=json`) for machine-readable output:

```bash
gathm-agent ask "dns mx for gmail.com" --json
```

## Useful Environment Variables

- `GATHM_OUTPUT_MODE`: `text` or `json`
- `GATHM_LOG_LEVEL`: `DEBUG|INFO|WARN|ERROR`
- `GATHM_MAX_RETRIES`: retry attempts
- `GATHM_CACHE_ENABLED`: `true|false`
- `GATHM_CACHE_DEFAULT_TTL`: cache TTL in seconds

## Operational State

By default, runtime state lives under `~/.gathm`:

- logs: `~/.gathm/logs`
- health + circuit state: `~/.gathm/health`
- cache: `~/.gathm/cache`
- planner history/state: `~/.gathm/agent`
