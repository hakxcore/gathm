# REST API (`api/server.py`)

The API exposes Gathm tools and orchestrator actions over HTTP.

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

- `GET /api/v1/tools`
- `GET /api/v1/tools/{name}`
- `POST /api/v1/tools/{name}/execute`
- `GET /api/v1/health`
- `GET /api/v1/health/{tool}`
- `POST /api/v1/agent/ask`
- `POST /api/v1/agent/plan`
- `POST /api/v1/agent/engineer`
- `POST /api/v1/agent/chain`
- `POST /api/v1/agent/parallel`
- `GET /api/v1/agent/status`
- `POST /api/v1/agent/heal`

## Example Requests

```bash
curl http://127.0.0.1:8080/api/v1/tools

curl -X POST http://127.0.0.1:8080/api/v1/tools/dns/execute \
  -H "Content-Type: application/json" \
  -d '{"args":["-t","MX","gmail.com"]}'

curl -X POST http://127.0.0.1:8080/api/v1/agent/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"check robots.txt for example.com"}'
```

## Notes

- Tool execution is delegated to `agent/orchestrator.sh`.
- API uses CORS headers (`*`) by default.
- For production usage, run behind a reverse proxy and enforce network controls.
