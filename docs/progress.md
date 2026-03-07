# Reachy Mini — Progress Log

## Phase 1: See + Move ✅ COMPLETE

### What was built
- **`robot.py`** — REST API client using `urllib` (no SDK, no WebSocket)
- **`motion.py`** — Click CLI: wake-up, sleep, move-head, look, rotate-body, antennas, nod, shake
- **`vision.py`** — take-photo (SDK WebRTC camera, saves to `artifacts/`)
- **`state.py`** — get-state (prints JSON)
- **`CLAUDE.md`** — instructions for Claude Code to use robot tools
- **Tests:** 14 integration tests (automated) + 12 motion e2e tests + 5 vision e2e tests (human-observable) + antenna calibration

### Key decisions & discoveries

**1. SDK WebSocket doesn't work → use REST API directly**

The `reachy-mini` Python SDK connects via WebSocket to `ws://host:8000/ws/sdk`, which returns 404 — the endpoint doesn't exist on the daemon's FastAPI server. Instead of debugging the SDK, we bypass it entirely and call the REST API directly with `urllib`. Zero extra dependencies needed.

**2. Daemon requires explicit startup**

The robot daemon starts in `not_initialized` state. You must:
1. POST `/api/daemon/start?wake_up=false`
2. Poll `/api/daemon/status` until `state == "running"` and `backend_status.ready == true`
3. POST `/api/motors/set_mode/enabled`

We handle all of this automatically in `ensure_ready()`, which caches the result for 10s so sequential commands (like nod = 4x goto) don't each re-check.

**3. goto is async**

`/api/move/goto` returns a UUID immediately — the movement runs in the background. To know when it's done, poll `/api/move/running`. Our `goto()` function defaults to `wait=True`: it sleeps for `duration + 0.3s`, then polls until no moves are running.

**4. Antenna mirror-mounting**

The two antennas are physically mirrored on the robot. In the raw API:
- Opposite-sign values `[+30, -30]` → both antennas go the SAME direction (both up)
- Same-sign values `[+30, +30]` → antennas go OPPOSITE directions (one up, one down)

We negate index 1 in `_antenna_to_api()` so the user-facing API is intuitive:
- `antennas = (left_degrees, right_degrees)`
- Positive = up for both
- `(30, -30)` → left up, right down ✓

**5. 503 errors on fresh start**

After daemon start, the backend takes a few seconds to initialize. During this window, API calls return 503. We handle this with retry logic (up to 2 retries with 3s delay).

**6. ensure_ready() caching matters**

Without caching, every `goto()` call triggers `ensure_ready()` which checks daemon status, motor status, etc. For nod (4x goto) this added ~8s of overhead. A 10s TTL cache on `_last_ready_at` fixes this.

**7. E2E tests need human observers**

Automated tests can only check CLI exit codes and stdout. They can't verify the robot actually moved. We wrote human-observable tests with a `confirm()` function that pauses and asks the operator to verify behavior.

**8. Camera uses SDK WebRTC, not REST API**

The daemon has no camera/snapshot endpoint. Camera frames come via the SDK's WebRTC pipeline (signalling server on port 8443). First connection is slow (~30-60s) due to GStreamer plugin scanning and WebRTC setup. Subsequent calls are fast. Vision e2e tests use a warmup call in the fixture to handle this.

**9. Camera resolution control**

The SDK supports `set_resolution()` via `CameraResolution` enum. Available on Reachy Mini Wireless: 720p@30fps (default), 1080p@30fps, 4K@10fps, 3840×2592@10fps (near-full-sensor). Must be called before the pipeline starts (before first `get_frame()`). Higher resolutions are slower to start but produce sharper images — useful for document capture/OCR.

### Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/reachy_mini_brain/robot.py` | ~265 | REST API client, daemon lifecycle, motion helpers |
| `src/reachy_mini_brain/motion.py` | ~97 | Click CLI for all motion commands |
| `src/reachy_mini_brain/vision.py` | ~85 | take-photo command (SDK WebRTC, resolution control) |
| `src/reachy_mini_brain/state.py` | ~23 | get-state command |
| `tests/test_integration.py` | ~160 | 14 automated tests |
| `tests/test_e2e.py` | ~150 | 12 motion/state/lifecycle e2e tests |
| `tests/test_e2e_vision.py` | ~180 | 5 vision e2e tests |
| `tests/test_antenna_manual.py` | ~100 | Antenna calibration diagnostic |

---

## Phase 1.5: Voice + Video CLI ✅ COMPLETE

### What was built
- **`stt.py`** — faster-whisper wrapper. `transcribe()` and `transcribe_array()`. Model caching, VAD filter, silence detection, auto-download.
- **`tts.py`** — piper-tts wrapper. `synthesize()` (WAV file) and `synthesize_array()` (numpy). Voice model auto-download from HuggingFace.
- **`audio.py`** — Click CLI: listen (mic → STT → transcript), speak (TTS → robot speaker), play-sound (WAV → speaker), doa (direction of arrival), diag (GStreamer pipeline diagnostic)
- **`video.py`** — Click CLI: record (get_frame loop + OpenCV VideoWriter → MP4)

### Key decisions & discoveries

**10. Audio goes through WebRTC (same as camera)**

The SDK's `GstWebRTCClient` handles both video and audio through one WebRTC connection. `start_recording()`/`stop_recording()`/`start_playing()`/`stop_playing()` are all **NO-OPs** for the WebRTC backend. Just call `get_audio_sample()` and `push_audio_sample()` directly. Audio format: float32, 16kHz, 2 channels (interleaved). Push mono data — MediaManager handles channel conversion.

**11. STT and TTS run locally on Mac**

faster-whisper (CTranslate2 backend) for STT, piper-tts (ONNX) for TTS. Both run on CPU. Voice models auto-download on first use (~60MB each). This keeps them independent of the SDK — they work with any audio input/output. Default to English language to avoid Whisper hallucination on silence.

**12. Piper needs voice model files**

Unlike faster-whisper which auto-downloads from HuggingFace, piper requires `.onnx` + `.onnx.json` files. We handle auto-download in `tts.py` from the rhasspy/piper-voices HuggingFace repo. Models cached in `~/.local/share/piper-voices/`.

**13. macOS GStreamer Bin.add() returns None instead of True**

On macOS with GStreamer 1.28.1, `Gst.Bin.add()` returns `None` instead of `True` (PyGObject binding issue). The SDK's `_setup_audio_send_chain()` checks `if not bin.add(elem)` which treats `None` as failure, aborting the audio send chain even though elements were actually added successfully. Diagnosed via `audio diag` command which dumps the full GStreamer pipeline hierarchy. Fix: monkey-patch the method to use `if bin.add(elem) is False` instead.

**14. WebRTC audio pipeline needs warmup**

The audio pipeline doesn't produce real samples until the WebRTC connection fully establishes (~5-10s). `_wait_for_audio()` polls `get_audio_sample()` until data flows. For speaker output, an additional 1s delay lets the send chain finish setup after the receive chain is ready.

**15. DoA requires local ReSpeaker USB**

Direction of Arrival uses the ReSpeaker 4-mic array which is connected to the RPi via USB. It's not accessible over WiFi/WebRTC — only works when running on the robot itself.

### Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/reachy_mini_brain/stt.py` | ~80 | faster-whisper wrapper, silence detection |
| `src/reachy_mini_brain/tts.py` | ~110 | piper-tts wrapper with auto-download |
| `src/reachy_mini_brain/audio.py` | ~330 | listen, speak, play-sound, doa, diag CLI + macOS GStreamer patch |
| `src/reachy_mini_brain/video.py` | ~110 | record CLI (get_frame + VideoWriter) |

---

## Phase 2: Persistent Session ✅ COMPLETE

### What was built
- **`session.py`** — `Session` class + Unix-socket server. Holds a single long-lived `ReachyMini()` SDK instance, exposing vision, audio, motion, and state through one unified API. Background server mode (`serve`) accepts commands from any process via `call`.
- **Continuous listening** — `listen_start`/`listen_read`/`listen_stop`. Background thread buffers raw mic audio; transcription (STT) runs on-demand when `listen_read` is called. Enables voice conversation without fixed recording durations.
- **Voice conversation flow** — Claude Code acts as the robot's brain, driving listen→think→speak→act loops by polling `listen_read` and issuing commands through the session server. See `docs/continuous-listen.md` for full design.

### Key decisions & discoveries

**16. In-process session + Unix socket server**

Studied the official conversation app (Pollen's reference). They use a single-process, thread-per-service architecture with one shared `ReachyMini()` instance. We follow the same pattern: `Session` is a Python class used directly in-process. Added a Unix-socket server layer so the session can stay alive in the background and accept commands from Claude Code or any other process.

**17. Manual lifecycle, not context manager on SDK**

`ReachyMini()` supports `with` but that auto-closes on exit. For persistent use, we create it directly and call `media_manager.close()` + `client.disconnect()` manually in `stop()`. Our `Session` itself supports `with` for convenience.

**18. Thread-safe audio push via lock**

The SDK's `push_audio_sample()` has no lock on the internal `_appsrc_pts` counter. Concurrent callers would corrupt the PTS timestamps. Session wraps it with a `threading.Lock`.

**19. All channels share one WebRTC connection**

Camera + audio both go through the same `GstWebRTCClient`. One `ReachyMini()` instance handles everything. Motion/state use REST API (already fast, no persistence needed). Session delegates motion to `robot.py` directly.

**20. Skip ensure_ready() during active session**

`robot.goto()` calls `ensure_ready()` which makes HTTP requests to check daemon/backend/motor status. With a 10s cache TTL, this re-triggers after the long WebRTC warmup in `Session.start()`, adding noticeable delay to every motion command. Fix: `robot._session_active` flag bypasses `ensure_ready()` entirely while a session is active, since the session already confirmed readiness at startup.

**21. Continuous listening — dumb buffer + on-demand STT**

Fixed-duration `listen(5)` means the mic is off while Claude thinks or the robot speaks — anything said is lost. Solution: a daemon thread that continuously calls `get_audio_sample()` and appends to a buffer. STT only runs when `listen_read` is called. The thread does zero processing — just accumulates raw samples. `speak()` sets a `_speaking` flag so the thread discards samples of the robot's own voice.

**22. Claude Code IS the brain — no separate conversation script**

Instead of building a `voice_conversation.py` script that calls the Claude API, Claude Code itself drives the robot through the session server's `call` interface. This means:
- No separate API key management or conversation history code
- Claude Code's full reasoning, tool use, and context window are the robot's intelligence
- The session server is just a tool — like a file editor or terminal, but for a robot
- Polling pattern: `sleep 5 && call listen_read` lets Claude Code autonomously check for voice input

### Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/reachy_mini_brain/session.py` | ~580 | Session class, continuous listening, Unix socket server/client, CLI (serve/call) |
| `docs/continuous-listen.md` | ~95 | Continuous listening design doc |

---

## Phase 3: Voice Conversation ✅ COMPLETE (via Claude Code)

Originally planned as a standalone `voice_conversation.py` script. Realized Claude Code itself is the better brain — it already has reasoning, conversation history, tool use, and multi-modal understanding. No separate script needed.

### How it works

1. Start session server: `python -m reachy_mini_brain.session serve`
2. Claude Code issues `call listen_start` to begin background mic buffering
3. Claude Code polls with `sleep 5 && call listen_read` to get transcripts
4. Based on what's heard, Claude Code responds via `call speak "..."`, executes actions (`call look left`, `call nod`, `call take_photo`), and describes what the camera sees
5. User says "stop" in chat or verbally → Claude Code calls `listen_stop`

### What's available for future improvement

- [ ] VAD (voice activity detection) — know when someone starts/stops talking
- [ ] Wake word detection — "hey reachy" trigger
- [ ] Echo cancellation — hardware-level filtering of robot's own voice
- [ ] Streaming STT — transcribe as audio arrives instead of in batches

---

## Phase 4: Scheduled Monitoring — NOT STARTED

- [ ] `scripts/cron_check.sh`
- [ ] Logging setup

---

## Phase 5: Polish — NOT STARTED

- [ ] Error handling, reconnection
- [ ] Personas, wake words
- [ ] Mockup sim tests
