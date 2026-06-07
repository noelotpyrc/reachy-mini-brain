# Reachy Mini Brain

Claude Code as the brain for a [Reachy Mini](https://www.pollen-robotics.com/reachy-mini/) robot — seeing, hearing, speaking, moving, and acting through CLI tools that wrap the robot's REST API.

## Setup

```bash
uv venv && uv pip install -e .
uv pip install -e ".[audio]"   # for listen/speak commands
```

## Structure

```
src/reachy_mini_brain/
├── robot.py         # REST API client (urllib, no SDK)
├── motion.py        # CLI: wake-up, sleep, move-head, look, nod, shake, antennas
├── vision.py        # CLI: take-photo (SDK WebRTC camera)
├── audio.py         # CLI: listen, speak, play-sound, doa (SDK WebRTC audio)
├── video.py         # CLI: record (SDK WebRTC + OpenCV)
├── stt.py           # faster-whisper wrapper (local STT)
├── tts.py           # piper-tts wrapper (local TTS, en_US-lessac-medium)
├── state.py         # CLI: get-state
├── session.py       # persistent ReachyMini() session + Unix-socket server
│                     #  — reception robot (docs/plan-reception.md) —
├── reception.py     # resident daemon: vision/voice/brain toggles + control socket
├── detector.py      # RF-DETR Nano person detection (vision tier-1)
├── approach.py      # ByteTrack + approach-vs-transit geometry (tiers 2-3)
├── perception.py    # detect → track → approach (+ gestures) → events.jsonl
├── gesture.py       # MediaPipe wave detection (Open_Palm) — Feature 2
├── replay.py        # offline eval harness: replay clips (--annotate, --smooth, --expect)
├── alert_engine.py  # separate process: tails events → robot reacts (approach/depart/wave)
└── brain.py         # claude -p receptionist agent (voice brain)
```

## Usage

```bash
# Take a photo
.venv/bin/python -m reachy_mini_brain.vision take-photo

# Move the robot
.venv/bin/python -m reachy_mini_brain.motion wake-up
.venv/bin/python -m reachy_mini_brain.motion look --direction left
.venv/bin/python -m reachy_mini_brain.motion nod
.venv/bin/python -m reachy_mini_brain.motion sleep

# Listen and speak
.venv/bin/python -m reachy_mini_brain.audio listen --duration 5
.venv/bin/python -m reachy_mini_brain.audio speak "Hello, I am Reachy"

# Record video
.venv/bin/python -m reachy_mini_brain.video record --duration 10

# Read state
.venv/bin/python -m reachy_mini_brain.state get-state
```

### Reception robot (Phases A–C — see `docs/plan-reception.md`)

```bash
# Resident daemon: vision (approach detection) + voice (claude -p brain)
.venv/bin/python -m reachy_mini_brain.reception serve --perception --brain
# Toggle workers + greet, from another shell:
.venv/bin/python -m reachy_mini_brain.reception vision on
.venv/bin/python -m reachy_mini_brain.reception voice on
.venv/bin/python -m reachy_mini_brain.reception status
# Alert engine (separate process): approach → robot greets
.venv/bin/python -m reachy_mini_brain.alert_engine
```

See `docs/robot-guide.md` for the full CLI reference.

## Architecture

```
Claude Code CLI (live session)
    ↕ Bash
Python CLI scripts (HTTP → act → exit)
    ↕ REST API over WiFi
Reachy Mini Daemon (RPi 5, port 8000)
    ↕ hardware
Camera | 4-Mic Array | Speaker | 9-DOF Motors
```

- `robot.py` talks to the daemon REST API directly via `urllib` — no SDK, no WebSocket
- Camera and audio are the exceptions: they use the SDK's WebRTC pipeline (port 8443)
- STT (faster-whisper) and TTS (piper-tts) run locally on the Mac
- All user-facing angles are in degrees; conversion to radians happens in `robot.py`

## Conventions

- `robot.ensure_ready()` before any robot interaction (handles daemon startup + caching)
- Always `wake_up()` before motion, `go_to_sleep()` when done
- CLI modules use `click` with `@click.group()` + `@cli.command()` pattern
- Photos save to `artifacts/` by default (gitignored)

## Tests

```bash
# Automated integration tests
.venv/bin/python -m pytest tests/test_integration.py -v

# Human-observable e2e tests (requires -s for confirm prompts)
.venv/bin/python -m pytest tests/test_e2e.py -v -s
.venv/bin/python -m pytest tests/test_e2e_vision.py -v -s
```

## Docs

- `docs/plan-reception.md` — **reception robot design-of-record** (Phases A–C, decisions,
  testing strategy + eval framework, open items) — the living plan
- `docs/live-test-log.md` — **on-robot test log** (good / ugly / bad, newest first)
- `docs/robot-guide.md` — full CLI reference (per-module CLIs + the `reception` daemon)
- `docs/plan.md`, `docs/progress.md` — earlier generic roadmap + implementation log
  (superseded by `plan-reception.md` for the reception build; kept for history)
