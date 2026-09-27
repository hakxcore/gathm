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

## Notes

- Assistant chat uses the configured model and preserves the request's tool permissions.
- Tool execution is delegated to `agent/orchestrator.sh`.
- Cross-origin browser requests are rejected. Remote access requires an API key.
- For production usage, run behind a reverse proxy and enforce network controls.
