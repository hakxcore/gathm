# Web GUI (`gui/`)

The GUI is a chat-style web client that talks to the Gathm API.

## Run Locally

Start API server:

```bash
python3 api/server.py --port 8080
```

Serve GUI files:

```bash
cd gui
python3 -m http.server 5173
```

Open:

```text
http://127.0.0.1:5173
```

## How It Works

- Frontend sends user prompts to `POST /api/v1/agent/ask`
- Displays assistant responses in message bubbles
- Polls `/api/v1/health` for online/offline status

## API Base URL

Default API base in `gui/app.js`:

```js
const API_BASE = window.GATHM_API_URL || 'http://127.0.0.1:8080';
```

You can override by setting `window.GATHM_API_URL` before loading `app.js`.

## Notes

- Voice playback UI elements are presentational unless wired to additional backend features.
- Keep API and GUI origins aligned with your CORS/security requirements.
