# ScreenAI — Comprehensive Code & Product Review

**Reviewed:** 2026-04-19  
**Files:** `main.py`, `server.py`, `ai_service.py`, `capture.py`, `config.py`, `hotkey.py`, `static/index.html`

---

## Priority Key
- **P0** — Must fix. Broken, data-losing, or will crash in normal use.
- **P1** — Should fix before sharing or demoing. Quality/reliability issues.
- **P2** — Nice to have. Polish, future-proofing, UX improvements.

---

## 1. Bugs & Issues

### P0 — Critical

**[BUG-01] `main.py:183-186` — HTTPS server runs on a separate event loop; WebSocket sends cross loops**

The HTTP server owns `_loop` (line 160). The HTTPS server creates its own loop (`asyncio.new_event_loop()`). Both instances share the same `srv.manager` (a module-level singleton). When a client connects via HTTPS, its WebSocket lives on the HTTPS event loop. But `broadcast()` is always called from the HTTP loop (via `asyncio.run_coroutine_threadsafe(..., _loop)`). Awaiting `ws.send_text()` on a WebSocket that lives on a *different* loop causes undefined behavior — likely a silent hang or `RuntimeError`. Either share a single event loop for both servers, or use `asyncio.run_coroutine_threadsafe` correctly to dispatch sends to the right loop per connection.

**[BUG-02] `index.html:742` — New `setInterval` created on every reconnect, leaking timers**

`setInterval(() => { ws.send('ping'); }, 25000)` is inside `connect()`. Each WebSocket reconnection creates an additional ping interval. After 10 reconnects there are 10 concurrent intervals all firing pings (on the old `ws` references too, since intervals hold closures). Fix: move the interval outside `connect()`, or check whether it's already running.

**[BUG-03] `server.py:196-199` — Race condition: transcript chunk in-flight when `/listen/stop` fires**

`listen_stop` reads `listen_session.full_transcript` and then sets `active = False`. An async `/listen/chunk` handler running concurrently (as a separate asyncio task on the same loop) could append a chunk *after* the transcript was snapshotted for analysis. That final chunk is silently dropped. FastAPI processes requests concurrently on the same event loop, so two `await`-free mutations to `listen_session.chunks` can interleave if either path yields (the `await loop.run_in_executor(...)` call in `listen_chunk` yields control, allowing `listen_stop` to run mid-flight). Fix: record `chunk_count` *before* calling `run_in_executor`, and use an asyncio `Lock` around `chunks` mutations.

**[BUG-04] `capture.py:34-37` — Full-resolution PNG sent to AI with no size cap**

A 4K display (3840×2160) produces a PNG of ~8–18 MB. Claude's API has a 5 MB image limit for base64-encoded images; exceeding it returns a 400 error. The code never resizes `raw_png`. Either cap the AI image at ~2048px wide (matching what Vision models actually resolve) or encode it as JPEG for transmission. The display path correctly downscales; the AI path does not.

**[BUG-05] `server.py:134, 162` — New OpenAI client instantiated per request**

`openai.OpenAI(api_key=...)` is called inside `transcribe_audio` and `listen_chunk` on every request. The OpenAI SDK performs DNS resolution and creates an HTTP connection pool during construction. Under load (15-second chunks throughout a 30-minute interview = 120 chunk requests), this creates 120 separate HTTP client pools. Cache a module-level or per-service client.

---

### P1 — Should Fix Before Sharing

**[BUG-06] `server.py:113` — Circular import via runtime import of `handle_chat`**

`from main import handle_chat` is inside `_handle_chat_message`. This works today only because Python caches the module after first import, but it's a structural smell. `main.py` imports `server`, and `server.py` imports back from `main`. This will cause `ImportError` if module load order ever changes. Fix: pass `handle_chat` as a callback the same way `manual_capture_callback` is injected.

**[BUG-07] `server.py:55-69` — Sequential broadcast blocks slow clients from fast ones**

`broadcast` sends to each WebSocket one by one with `await ws.send_text(...)`. If one client has a full TCP buffer (slow reader), the `await` stalls, delaying all other clients. Replace with `asyncio.gather(*[ws.send_text(message) for ws in connections], return_exceptions=True)`.

**[BUG-08] `ai_service.py:183, 198` — `OpenAIService` methods may return `None`**

`response.choices[0].message.content` is typed as `Optional[str]` in the OpenAI SDK — it's `None` when `finish_reason` is `tool_calls` or `content_filter`. Both `chat()` and `analyze_screenshot()` return this value unguarded. Add `or ""` or raise a descriptive error.

**[BUG-09] `server.py:20-21` — Incorrect callback type hints**

```python
manual_capture_callback: Optional[Callable[[], Coroutine]] = None
analyze_interview_callback: Optional[Callable[[str], Coroutine]] = None
```
`manual_capture_callback` is `_handle_capture` which takes `Optional[str]`, not `()`. These hints are misleading and won't catch bugs at call sites.

**[BUG-10] `index.html:1029-1032` — Monkey-patching via variable reassignment is fragile**

```js
const _origHandleCapture = handleCapture;
handleCapture = function(msg) { setCaptureBtn(false); _origHandleCapture(msg); };
```
`dispatch()` (line 751) calls `handleCapture` by name, but function declarations are hoisted while this assignment is not. Any code that took a reference to the original `handleCapture` before line 1029 would still call the unpatched version. Use a proper wrapper pattern: just call `setCaptureBtn(false)` inside `handleCapture` and `handleError` directly.

**[BUG-11] `index.html:1246` — `/listen/stop` response not checked for errors**

```js
await fetch('/listen/stop', { method: 'POST' });
```
The response is never checked. If the server returns 400 ("No active listen session") or 500, the UI shows "Recording complete. Sending to interview coach…" and `showThinking('ai')` runs indefinitely. Add error handling and clear the thinking indicator on failure.

**[BUG-12] `config.py:15` — `HOST = "0.0.0.0"` exposes server on all interfaces by default**

Binding to `0.0.0.0` exposes the unauthenticated server to every device on the local network (and VPNs, etc.). With no auth layer, anyone on the network can trigger captures, access transcripts, and run AI queries. Default should be `127.0.0.1` for local use, with `0.0.0.0` as an explicit opt-in.

---

### P2 — Polish

**[BUG-13] `capture.py:31` — Unnecessary bytes() copy of mss screenshot**

`bytes(shot.bgra)` creates a full copy of the raw pixel data before `Image.frombytes`. `mss.ScreenShot.bgra` is already a `memoryview`/bytes-like object that `PIL.Image.frombytes` accepts directly. Drop the `bytes()` wrapper to save one full-frame allocation.

**[BUG-14] `server.py:168` — Silently skipping small chunks obscures silence periods**

When a 15-second window of silence produces a < 2000-byte blob, the server returns `{"text": "", "chunk_index": ...}` without logging or broadcasting. The client also silently skips in `uploadListenChunk`. Silence during an interview (thinking time, pauses) should probably not reset the chunk index without acknowledgment. At minimum, log it.

---

## 2. Code Quality

**[QUALITY-01] `server.py:129, 161-163` — Lazy imports inside request handlers**

`from config import OPENAI_API_KEY` and `import openai` inside `transcribe_audio` and `listen_chunk` run on every request. Move to module top-level (with a try/except for optional dependency) or check at startup.

**[QUALITY-02] No retry/backoff on AI API calls**

`ClaudeService.analyze_screenshot`, `chat`, and `analyze_interview` make blocking API calls with zero retry. Rate limit errors (429), transient 5xx errors, and network timeouts will all surface as unhandled exceptions and produce a bare error broadcast to the client. Add exponential backoff (e.g., `tenacity`) for at least one retry.

**[QUALITY-03] No conversation history / context**

Every `chat()` call sends a single-turn message with no history. The AI cannot reference prior exchanges ("what did you mean by X in your last screenshot?"). The `AIService` interface has no `history` parameter, and `_last_screenshot` is the only cross-turn state. Conversation history should be maintained per-session as a list of `(role, content)` pairs.

**[QUALITY-04] `ai_service.py:111` — Model ID hardcoded in class constant**

`MODEL = "claude-sonnet-4-6"` makes it impossible to override the model via environment variable without code changes. Expose as `AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-6")` in `config.py`.

**[QUALITY-05] `main.py:209` — Placeholder IP in log output**

```python
logger.info("  HTTPS : https://192.168.x.x:%d  (accept cert warning)", HTTPS_PORT)
```
`192.168.x.x` is a literal placeholder, not the machine's actual IP. Use `socket.gethostbyname(socket.gethostname())` or scan local interfaces.

**[QUALITY-06] `server.py:38` — Singleton `listen_session` is undocumented single-user constraint**

There's a comment nowhere stating this is a one-session-at-a-time design. A second browser tab calling `/listen/start` silently resets an in-progress session (via `listen_session.reset()`). Document this constraint or add a 409 guard.

**[QUALITY-07] Missing `__all__` / module-level docstrings**

None of the Python modules have docstrings or `__all__`. Minor, but important for any future packaging.

**[QUALITY-08] No type annotation on `_handle_chat_message`'s return value**

`server.py:111`: `async def _handle_chat_message(msg: dict, _ws: WebSocket) -> None` — the `_ws` parameter is unused (it's passed to `asyncio.create_task` but the function only calls `handle_chat`). The parameter should be removed.

---

## 3. UX / UI Improvements

**[UX-01] P0 — No copy button on code blocks**

The app is explicitly a developer tool. AI responses with code have no copy button. This is a daily-friction issue. A simple "Copy" overlay in the top-right of each `<pre>` block (via the `marked` renderer's `code()` hook) would be high-value, ~30 lines of JS.

**[UX-02] P1 — No streaming responses**

Claude and OpenAI both support streaming. Currently the full response is generated server-side before broadcast. A 500-token response at average speed takes 3–5 seconds of blank thinking dots. Streaming the response token-by-token (via `client.messages.stream()` and WebSocket) would feel dramatically faster.

**[UX-03] P1 — No clear / reset button**

There is no way to clear the chat thread. After several captures and a listen session, the thread becomes very long. A "Clear" button in the header (with a confirmation prompt) is an obvious fix.

**[UX-04] P1 — Capture button timeout is 30 seconds (`index.html:1021`)**

```js
setTimeout(() => setCaptureBtn(false), 30000);
```
If the WS event never arrives (disconnected), the button stays disabled for 30 seconds. This should be reduced to ~10 seconds or tied to the WebSocket reconnect event (re-enable button on reconnect).

**[UX-05] P1 — Listen Mode: no elapsed timer or chunk counter visible**

During a 30-minute interview, the user has no idea how much has been transcribed or how long they've been recording. A running `MM:SS` timer and chunk counter in the Listen button (or below it) would be reassuring.

**[UX-06] P1 — Interview analysis has no "copy" or "export" affordance**

The coaching report can be 1000+ words of structured markdown. There's no way to copy it to clipboard or save it. A "Copy Report" button in the analysis bubble would take ~10 lines.

**[UX-07] P1 — No visual indication of which screenshot an AI response refers to**

In a long chat session with multiple captures, it's unclear which screenshot a given AI response is analyzing. A subtle "re:screenshot at 2:34 PM" reference in the bubble meta would help.

**[UX-08] P2 — Mobile layout is not responsive**

`max-width: 900px` with no `@media` queries. On a phone:
- The header (logo + status + badge + hotkey + spacer + 2 buttons) overflows on small screens.
- The transcript/analysis bubbles use `max-width: 90–94%` which is fine, but the input bar doesn't adapt.
- Consider a hamburger menu or collapsing the header for `<640px`.

**[UX-09] P2 — Accessibility gaps**

- `attachBtn`, `micBtn`, `sendBtn` (`index.html:666-676`) have no `aria-label`. Screen readers announce emoji as their Unicode name.
- `msg-row` elements have no ARIA roles; the chat thread should be `role="log"` with `aria-live="polite"`.
- `screenshot-thumb` images have generic `alt="Screenshot"` — should include timestamp.
- Color-only status indication (the dot) needs a text fallback for colorblind users.

**[UX-10] P2 — No feedback when attachment button is disabled (no screenshot yet)**

The attach button starts disabled (`index.html:1439`) with no tooltip explaining why. A `title="Take a screenshot first"` change would be a 1-line fix.

**[UX-11] P2 — No keyboard shortcut to toggle Listen Mode**

The shortcut legend shows hotkeys for Capture, Send, and Attach — but not for Listen. A `L` or `Cmd+L` shortcut would fit naturally.

---

## 4. Feature Gaps

**[FEATURE-01] P0 (for multi-turn usefulness) — No conversation history**

This is the most important missing feature. Every chat is a fresh single-turn call. The user can't say "now explain the second error in more detail" or "now do it differently". This limits the product to single-shot Q&A, not a genuine AI assistant.

**[FEATURE-02] P1 — No custom prompt UI for captures**

The hotkey and Capture button always use the default `AI_PROMPT`. A small input overlay ("What should I focus on?") before capture — or a persistent "Focus" text box — would dramatically increase utility.

**[FEATURE-03] P1 — No persistence**

Page refresh loses everything. Server restart loses everything. Even lightweight localStorage persistence of the last N messages would be valuable.

**[FEATURE-04] P1 — Listen Mode requires HTTPS on mobile, but HTTPS setup is manual and fragile**

Mobile browsers (iOS Safari, Chrome Android) block `getUserMedia` on non-HTTPS origins. The HTTPS setup requires manual SSL cert generation and accepting an untrusted cert warning. This creates an invisible barrier for mobile use, which is exactly the most likely interview scenario.

**[FEATURE-05] P2 — No speaker diarization in Listen Mode**

The transcript is a flat stream of text with no speaker labels. The interview coach prompt assumes it can identify questions (from the interviewer) and answers (from the candidate), but without diarization the AI is guessing. Options: (a) use OpenAI's Whisper with `diarization: true` if available, (b) prompt the user to say "Question:" and "Answer:" aloud, (c) accept this limitation and document it.

**[FEATURE-06] P2 — Whisper context not passed between chunks**

Each 15-second audio chunk is transcribed with no context from prior chunks. Whisper's API accepts a `prompt` parameter (up to 224 tokens) as context for the current segment. Passing the last 1–2 sentences of the previous chunk would reduce truncation artifacts and improve recognition of proper nouns introduced earlier.

**[FEATURE-07] P2 — No language selection for transcription**

Whisper auto-detects language, which is fine for English-only users but breaks for non-English interviews. Expose `language` as a config option.

**[FEATURE-08] P2 — No pause/resume in Listen Mode**

Bathroom break, interruption, phone call — the user must stop and lose the session, or keep recording silence. A Pause button that stops the MediaRecorder but keeps the session alive server-side would be straightforward.

**[FEATURE-09] P2 — No way to re-run analysis on an existing transcript**

If the interview coach analysis is unsatisfactory, there's no "Re-analyze" button. The transcript is gone after the session resets.

**[FEATURE-10] P2 — No support for image uploads from disk**

Users might want to analyze a screenshot file they already have, not just live captures.

---

## 5. Performance

**[PERF-01] P0 — Full-resolution PNG sent to AI (overlaps with BUG-04)**

A 3840×2160 screenshot encodes to ~8–18 MB as PNG. Both Claude and GPT-4o internally resize images to ~2048×2048 for analysis. Sending full resolution adds 5–15 seconds of upload time on a slow connection and costs proportionally more tokens. Fix: cap `raw_png` at 2048px wide, or encode as JPEG (quality 90) before API submission.

**[PERF-02] P1 — Duplicate WebSocket ping timers on reconnect (overlaps with BUG-02)**

After N reconnects, there are N `setInterval` callbacks firing pings. On an unstable connection this is measurable load.

**[PERF-03] P1 — Sequential broadcast latency**

With 5 connected clients and one slow receiver, `broadcast()` in `server.py:55-69` sends to each in series. One 500ms stall delays everyone. Use `asyncio.gather` for parallel sends.

**[PERF-04] P2 — Audio MIME type detection is redundant**

The client detects the best supported MIME type (`index.html:1175-1176`) and passes it in the filename. The server re-detects it from `content_type` (`server.py:172`). The content type sent by browsers for `audio/webm;codecs=opus` is often just `audio/webm`, causing the server-side branch to behave differently than intended. Standardize on the filename-based approach the client already uses.

**[PERF-05] P2 — No AI response caching**

Identical screenshots (e.g., pressing hotkey twice for the same static screen) produce two full API calls at full cost. A simple SHA-256 hash of `raw_png` with a short-lived LRU cache would eliminate redundant calls.

---

## 6. Listen Mode — Detailed Assessment

### What works well
- The 15-second chunked recording loop (`startListenCycle` / `onstop` reinvocation) is clean and handles the recursive restart correctly.
- MIME type detection and Safari/Firefox fallback (`m4a` vs `webm`) is thoughtful.
- The `beforeunload` handler to call `/listen/stop` is a good defensive move.
- The live transcript bubble with growing text gives immediate feedback.
- The interview coach prompt (`ai_service.py:10-76`) is exceptionally detailed and well-structured — the most polished part of the codebase.

### Issues specific to Listen Mode

**[LISTEN-01] P0 — Race between in-flight chunk and stop (overlaps with BUG-03)**

The most dangerous bug for the feature. If chunk N is being processed by Whisper (awaiting executor thread) when the user presses Stop, chunk N is dropped from the analysis.

**[LISTEN-02] P1 — 15-second chunks are too long for user feedback**

The first transcript chunk doesn't appear until ~17–20 seconds in (15s chunk + Whisper latency + network). Users think the recording isn't working. Reduce to 6–8 seconds for a more responsive feel.

**[LISTEN-03] P1 — No context passed to Whisper between chunks**

Without the Whisper `prompt` parameter set to the tail of the previous chunk, proper nouns (company names, interviewers' names, technical terms) introduced in chunk 1 are likely to be misspelled or misrecognized in chunk 10. This directly degrades analysis quality.

**[LISTEN-04] P1 — Transcript visible but not copyable**

The live transcript bubble shows what was said but has no copy/select affordance. After a 30-minute interview, users often want the raw transcript for other purposes (preparing follow-ups, sending thank-you notes).

**[LISTEN-05] P1 — `stopListenMode` doesn't handle upload failure gracefully**

```js
await new Promise(resolve => {
  resolveListenStop = resolve;
  listenRecorder.stop();
});
```
If `uploadListenChunk` inside `onstop` throws (network error), `resolveListenStop` is never called, the Promise never resolves, and `stopListenMode` hangs indefinitely. Wrap `uploadListenChunk` with a timeout or ensure the Promise always resolves.

**[LISTEN-06] P1 — Analysis result not tied to the session that produced it**

If a user starts a second listen session while the first analysis is still running (unlikely but possible), the second session's `listen_session.reset()` clears chunks, but the first `analyze_interview_callback(transcript)` task still fires and broadcasts its result. The UI has no session ID to distinguish them.

**[LISTEN-07] P2 — No speaker separation**

Flat transcript makes question attribution imprecise. The prompt works around this, but accuracy would improve meaningfully with basic speaker labeling.

**[LISTEN-08] P2 — No elapsed timer in UI**

A simple counter showing `Recording: 2:34 | 11 chunks` would reduce user anxiety during a long session.

**[LISTEN-09] P2 — Analysis can exceed `max_tokens: 4096`**

For a 45-minute interview with 180 transcript chunks, the coaching report prompt plus transcript can easily push against the 4096-token output cap. Consider detecting truncation (`finish_reason == "max_tokens"`) and broadcasting a warning.

---

## 7. Architecture

**[ARCH-01] P0 — Two event loops, one shared ConnectionManager (overlaps with BUG-01)**

This is the most architecturally unsound decision. Either:
- (Preferred) Run both HTTP and HTTPS as uvicorn `Server` configs on the same event loop using `asyncio.gather(http.serve(), https.serve())`.
- Or remove HTTPS entirely for localhost use (use a reverse proxy like nginx or Caddy for HTTPS termination).

**[ARCH-02] P1 — All state is module-level globals**

`_last_screenshot`, `listen_session`, `manager`, `manual_capture_callback`, `analyze_interview_callback` are all module singletons. This makes unit testing impossible (you can't isolate a test without patching globals) and multi-user support impossible without a full rewrite. Move state into a dependency-injected `AppState` dataclass passed through FastAPI's `Depends()`.

**[ARCH-03] P1 — No authentication**

Any device on the network (or VPN) can:
- Trigger captures of the user's screen
- Read full transcript content via WebSocket
- Send arbitrary prompts to the AI at the user's cost

At minimum, a static token in `.env` (`APP_TOKEN`) checked as a query param or cookie on WebSocket connections would prevent this.

**[ARCH-04] P2 — No rate limiting**

`/listen/chunk` with no rate limiting means a malicious or buggy client could POST audio indefinitely, accumulating Whisper API charges and filling memory. FastAPI's `slowapi` integration is trivial to add.

**[ARCH-05] P2 — Multi-user requires significant rework**

`listen_session` is global; `_last_screenshot` is global; `manager.broadcast` sends to all clients regardless of which user triggered the capture. Adding user sessions would require:
1. Session IDs (cookie/token-based)
2. Per-session `ListenSession` and `_last_screenshot`
3. Per-session `send_to` instead of `broadcast`

**[ARCH-06] P2 — No persistence layer**

All chat, transcript, and screenshot data is in-memory and lost on restart. Even a SQLite backing store (via `aiosqlite`) would make this significantly more useful.

---

## 8. Quick Wins (< 1 hour each)

| # | File | Change | Impact |
|---|------|--------|--------|
| QW-01 | `index.html:742` | Move `setInterval` ping outside `connect()` | Fixes timer leak on reconnects |
| QW-02 | `index.html` | Add `aria-label` to all icon buttons | Accessibility baseline |
| QW-03 | `index.html:1021` | Reduce `setCaptureBtn` timeout from 30s to 10s | Less confusing stuck state |
| QW-04 | `index.html` | Add "Copy" overlay button to `<pre>` blocks in `marked` renderer | Developer UX (highest daily friction) |
| QW-05 | `index.html:1246` | Check `fetch('/listen/stop')` response and handle errors | Prevents indefinite spinner |
| QW-06 | `server.py:55-69` | Replace sequential broadcast with `asyncio.gather` | Faster multi-client sends |
| QW-07 | `capture.py:35-37` | Cap AI PNG at 2048px wide before API call | Reduces API latency and cost |
| QW-08 | `server.py:175-177` | Cache OpenAI client at module level | Reduces per-request overhead |
| QW-09 | `config.py:15` | Change `HOST` default to `"127.0.0.1"` | Security — don't expose by default |
| QW-10 | `index.html:1151` | Reduce `LISTEN_CHUNK_MS` from 15000 to 7000 | Faster live transcript feedback |
| QW-11 | `server.py:175` | Add `prompt=` to Whisper call with tail of last chunk | Better transcription continuity |
| QW-12 | `server.py:20-21` | Fix callback type hints | Type correctness |
| QW-13 | `ai_service.py:111` | Read model from `config.AI_MODEL` env var | Flexibility without code changes |
| QW-14 | `index.html` | Add "Clear chat" button in header | Basic housekeeping |
| QW-15 | `index.html` | Show elapsed `MM:SS` timer while listen mode is active | Reduces user anxiety |

---

## Summary Assessment

This is a genuinely well-conceived local dev tool with good bones: the async architecture is mostly sound, the dual-provider AI abstraction is clean, the UI design is polished for a prototype, and the interview coach prompt is excellent. The main areas to address before sharing or demoing:

1. **BUG-01** (dual event loops + shared manager) is a correctness time-bomb — fix it first.
2. **BUG-02** (ping timer leak) is a quick fix with visible reliability impact.
3. **BUG-03** (listen stop race) risks losing the last chunk of a real interview.
4. **PERF-01 / BUG-04** (full PNG to AI) will hit API limits on high-DPI displays.
5. **FEATURE-01** (no conversation history) is the biggest product gap — without it, the tool is single-shot Q&A, not an assistant.
6. **ARCH-03** (no auth, binds 0.0.0.0) is a security issue for networked use.

Quick wins QW-01 through QW-05 together take under an hour and address the most visible issues.
