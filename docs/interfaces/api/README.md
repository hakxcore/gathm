# REST API (`api/server.py`)

The API connects chat and voice to Gathm, your personal AI assistant. Use it for
conversation, writing, planning, and learning, with tools available when a task
needs them. Direct tool and orchestrator endpoints remain available for scripts.

## Start Server

```bash
python3 api/server.py --host 127.0.0.1 --port 8080
```

## Authentication

Set `GATHM_API_KEY` to require bearer auth:

```bash
export GATHM_API_KEY="your-secret"
```

Send token:

```bash
Authorization: Bearer your-secret
```

## Key Endpoints

- `POST /api/v1/agent/chat` — assistant conversation with history
- `POST /api/v1/transcribe` — turn a recording into text
- `POST /api/v1/speech` — render text as spoken audio
- `GET /api/v1/transcribe/status` — recording transcription availability
- `GET /api/v1/speech/status` — spoken reply availability
- `GET /api/v1/tools`
- `GET /api/v1/tools/{name}`
- `POST /api/v1/tools/{name}/execute`
- `GET /api/v1/health`
- `GET /api/v1/health/{tool}`
- `POST /api/v1/agent/ask` — legacy keyword-based tool routing
- `POST /api/v1/agent/plan`
- `POST /api/v1/agent/engineer`
- `POST /api/v1/agent/chain`
- `POST /api/v1/agent/parallel`
- `GET /api/v1/agent/status`
- `POST /api/v1/agent/heal`

## Example Requests

```bash
curl -X POST http://127.0.0.1:8080/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"Help me plan a calmer morning","history":[]}'

curl http://127.0.0.1:8080/api/v1/tools

curl -X POST http://127.0.0.1:8080/api/v1/tools/dns/execute \
  -H "Content-Type: application/json" \
  -d '{"args":["-t","MX","gmail.com"]}'

curl -X POST http://127.0.0.1:8080/api/v1/agent/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"check robots.txt for example.com"}'
```

`/agent/chat` returns `{"reply":"...","backend":"...","model":"..."}` on
success or `{"error":"..."}` if the assistant cannot respond. Send prior turns
as `{"role":"user"|"assistant","content":"..."}` entries in `history`.
If the model is unavailable, show the error and let the user retry after fixing
their connection or model setup. Do not resend the message to `/agent/ask`:
that endpoint routes keywords to tools and is not a conversational fallback.

## Streaming Conversation

The same endpoint accepts `Accept: text/event-stream` to deliver text as it is
generated. Omit that header to keep the JSON response above. Add your bearer
authorization header when authentication is enabled.

```bash
curl -N http://127.0.0.1:8080/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"query":"Say hello in one short sentence","history":[]}'
```

Each SSE `data:` record contains a JSON object. `event` is a field inside that
object, not a separate SSE `event:` line. A successful stream can look like:

```text
: connected

data: {"event":"token","text":"Hello"}

data: {"event":"token","text":"!"}

data: {"event":"result","data":{"reply":"Hello!","backend":"llamacpp","model":"local-model"}}

```

A failed turn ends with `{"event":"result","data":{"error":"..."}}`.
Ignore comment records such as `: connected` and `: waiting`; they carry no
reply text. Token events are optional and contain provisional text. Require a
final `result` event, and use its `data.reply` as the authoritative answer or
display its `data.error`. A connection that ends without a result is incomplete.

Do not automatically resend a failed or disconnected turn: tools may already
have performed actions. The server does not replay failed worker requests.

By default the API reuses an assistant subprocess to avoid importing the model
runtime on every turn. Conversation history still comes entirely from each
request. Set `GATHM_CHAT_WORKER=0` before starting the API to use a new subprocess
for each turn; SSE remains available but sends only the final result in this
mode.

## System Controls

These endpoints require both `tool:execute` and `system:control`; the default
admin role has them. They do not grant raw-shell access. Explicit `control`
entries in `blocked_tools` or `requires_approval` deny this capability, as they
do for the other builtins. Cross-origin and remote authentication rules apply.

- `GET /api/v1/system/status` returns this host's supported controls.
- `POST /api/v1/system/prepare` accepts `{"command":"volume set 40"}`. Read-only
  commands return `{"ok":true,"output":"..."}`. Changes return `reply` plus an
  `approval` object containing `id`, `summary`, `warning`, `command`, `target`,
  and `expires_at` (Unix seconds).
- `POST /api/v1/system/confirm` accepts `{"id":"<approval-id>","approve":true}`
  to execute, or `false` to cancel. `approve` must be a JSON boolean. The response
  is `{"ok":true|false,"output":"..."}`. Do not retry an ambiguous failure.

Approvals last two minutes, are bound to the authenticated caller, and can be
consumed only once. The server stores the exact validated plan; a confirmation
cannot substitute a new command. Permissions and target identities are checked
again before execution. Server restart clears all pending approvals. A new chat
turn revokes that caller's previous proposals.

Chat/SSE final results may contain the same `approval` object. Render it outside
generated Markdown, and require a deliberate human UI action. Never put its id
in model messages, conversation history, or persistent browser storage. A chat
message saying “yes” is not a confirmation request.

See [commands, platform limits, and recovery](../../system-control.md).

## Notes

- Assistant chat uses the configured model and preserves the request's tool permissions.
- Tool execution is delegated to `agent/orchestrator.sh`.
- Cross-origin browser requests are rejected. Remote access requires an API key.
- For production usage, run behind a reverse proxy and enforce network controls.
