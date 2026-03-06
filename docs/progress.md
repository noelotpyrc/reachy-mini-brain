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

### Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/reachy_mini_brain/robot.py` | ~265 | REST API client, daemon lifecycle, motion helpers |
| `src/reachy_mini_brain/motion.py` | ~97 | Click CLI for all motion commands |
| `src/reachy_mini_brain/vision.py` | ~50 | take-photo command (SDK WebRTC) |
| `src/reachy_mini_brain/state.py` | ~23 | get-state command |
| `tests/test_integration.py` | ~160 | 14 automated tests |
| `tests/test_e2e.py` | ~150 | 12 motion/state/lifecycle e2e tests |
| `tests/test_e2e_vision.py` | ~180 | 5 vision e2e tests |
| `tests/test_antenna_manual.py` | ~100 | Antenna calibration diagnostic |

---

## Phase 2: Hear + Speak — NOT STARTED

Next up:
- [ ] `stt.py` — faster-whisper wrapper
- [ ] `tts.py` — piper-tts wrapper
- [ ] `audio.py` — `listen`, `speak`, `doa` CLI commands
- [ ] Update CLAUDE.md with audio commands
- [ ] Tests for audio pipeline

---

## Phase 3: Voice Conversation — NOT STARTED

- [ ] `scripts/voice_conversation.py` — persistent mic + VAD + STT → Claude → TTS loop

---

## Phase 4: Scheduled Monitoring — NOT STARTED

- [ ] `scripts/cron_check.sh`
- [ ] Logging setup

---

## Phase 5: Polish — NOT STARTED

- [ ] Error handling, reconnection
- [ ] Personas, wake words
- [ ] Mockup sim tests
