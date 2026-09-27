# Gathm in the Browser (`gui/`)

A voice-first personal AI assistant with chat alongside. Start a conversation,
work on a message, plan your day, or learn something new. Gathm can use tools
when a request needs current information or an action.

## Start

```bash
gathm gui
```

From a checkout, use `./gathm gui`. The launcher checks your setup, starts the
API server, and opens the interface at `http://127.0.0.1:8080`. To use another
port, run `gathm gui --port 9090`.

You can also start the API directly from the repository root:

```bash
python3 api/server.py --host 127.0.0.1 --port 8080
```

Open that address in your browser. The API serves the GUI itself; a separate
static-file server is unnecessary. The installed package needs the `gui`
extra (`pip install 'gathm[gui]'`) for the API dependencies.

## Talk or Type

- **Talk to Gathm** starts voice conversation. Your microphone stays on while
  Gathm listens for each turn. **End conversation** stops listening and playback.
- The **speaker** control enables or mutes spoken replies when speech output
  is available. Voice input can still work with replies shown only in chat.
- The **microphone** in the composer records a single message. Tap it again to
  transcribe and send the recording.
- Type a message at any time. **Enter** sends it; **Shift + Enter** adds a line.
- Conversation starters help with planning, writing, and learning.

Voice controls reflect the server's speech availability. Allow microphone
access when your browser asks. Use the interface on localhost, such as
`http://127.0.0.1:8080`, or HTTPS for browser microphone access. If voice is
unavailable, you can keep using text chat.

## Conversation Context

Recent turns are sent with each message so follow-up questions have context.
The conversation is saved in this browser's local storage and restored when
you reopen the same site. **Clear**, `/clear`, or `/reset` removes the saved
chat and starts a fresh conversation. It is browser-local conversation
history, not a separate long-term memory service.

## API Connection

The GUI uses its own origin by default, so a custom server port works without
editing the frontend. Keep the GUI and API on the same origin: the API rejects
cross-origin browser requests.

- `/api/v1/agent/chat` handles conversation and history through the configured model.
- `/api/v1/transcribe` converts recorded audio to text.
- `/api/v1/speech` generates audio for spoken replies.
- `/api/v1/ping` provides the lightweight connection check.

If the model cannot answer, the interface reports the failure. It does not
replace a conversation with keyword-based tool suggestions. See the
[API documentation](../api/README.md) for request formats and authentication.
