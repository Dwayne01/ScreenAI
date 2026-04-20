# ScreenAI

A macOS background service that captures your screen with a global hotkey, sends the screenshot to an AI vision model (Claude or OpenAI), and streams the result to a chat-style web dashboard accessible from any device on your network.

## Features

- **Global hotkey** (`Cmd+Shift+S` by default) — works in any app, not just the browser
- **Fast capture** via `mss` — full-resolution PNG sent to AI, compressed JPEG sent to browser
- **AI analysis** — Claude (`claude-sonnet-4-6`) or OpenAI (`gpt-4o`) vision
- **Chat interface** — ask follow-up questions about the screenshot or anything else
- **Voice input** — browser Web Speech API (no cost) or OpenAI Whisper fallback
- **Screenshot attachment** — pin the last screenshot to your next chat message with 📎
- **Multi-client** — every open browser tab (including phone on same Wi-Fi) sees captures in real-time via WebSocket
- **Lightbox** — click any screenshot to view it full-size

## Quick start

### 1. Install dependencies

```bash
cd ScreenAI
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — add your API key and set AI_PROVIDER
```

Minimum required in `.env`:

```
AI_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Grant accessibility access (macOS)

`pynput` requires **Accessibility** permission to register global hotkeys.

1. Open **System Settings → Privacy & Security → Accessibility**
2. Add and enable your Terminal app (or the Python binary)

The app will still start without this, but the hotkey won't fire.

### 4. Run

```bash
python main.py
```

Open **http://localhost:8765** in your browser (or any device on your network using your Mac's IP).

Press `Cmd+Shift+S` (or your configured hotkey) to capture.

## Configuration

All options are set in `.env` (see `.env.example` for full reference):

| Variable | Default | Description |
|---|---|---|
| `AI_PROVIDER` | `claude` | `claude` or `openai` |
| `ANTHROPIC_API_KEY` | — | Required for Claude |
| `OPENAI_API_KEY` | — | Required for OpenAI; also enables Whisper transcription |
| `AI_PROMPT` | *(describe screenshot)* | Prompt sent with each screenshot |
| `HOTKEY` | `<cmd>+<shift>+s` | pynput hotkey format |
| `HOST` | `0.0.0.0` | Bind address (`0.0.0.0` = accessible on LAN) |
| `PORT` | `8765` | HTTP/WebSocket port |
| `MONITOR_INDEX` | `1` | `1` = primary monitor, `0` = all monitors |
| `MAX_DISPLAY_WIDTH` | `1920` | Max px width of JPEG sent to browser |

## Project structure

```
ScreenAI/
├── main.py          # Entry point — wires everything together
├── server.py        # FastAPI + WebSocket server
├── hotkey.py        # Global hotkey listener (pynput)
├── capture.py       # Screenshot capture (mss + Pillow)
├── ai_service.py    # Claude / OpenAI vision + chat abstraction
├── config.py        # .env loading
├── requirements.txt
├── .env.example
└── static/
    └── index.html   # Chat-style web dashboard (single file, no build step)
```

## Voice input

- **Chrome/Edge/Safari** — uses the browser's built-in Web Speech API (free, works offline, transcribes as you speak).
- **Firefox or other** — falls back to recording audio and sending it to `/transcribe`, which uses OpenAI Whisper. Requires `OPENAI_API_KEY`.

## Access from phone / other devices

Because the server binds to `0.0.0.0`, any device on your local network can open the dashboard. Find your Mac's local IP with `ipconfig getifaddr en0`, then open `http://<your-mac-ip>:8765` on your phone.
