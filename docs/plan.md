# Reachy Mini × Claude Code Brain — Plan

## Goal

Claude Code acts as the Reachy Mini's brain in a live interactive session — seeing, hearing, speaking, and acting through the robot, while also doing tasks on the local Mac.

**Key insight:** Claude Code IS already a live session with conversation memory, filesystem access, and Bash. We just need CLI tools that wrap the robot's REST API, and a CLAUDE.md that teaches Claude how to use them. The robot daemon (on RPi, port 8000) stays persistent — each CLI call connects over HTTP, does the action, exits.

**Hardware:** Reachy Mini Wireless (RPi 5, WiFi, 4-mic array, speaker, camera, 9-DOF motors)
**STT:** faster-whisper (local)  |  **TTS:** piper-tts (local)

---

## Architecture

```
User (terminal or voice)
    ↕
Claude Code CLI (live session, full context)
    ↕ Bash tool
Python CLI scripts (HTTP request → act → exit)
    ↕ REST API over WiFi (urllib, no SDK)
Reachy Mini Daemon (RPi, port 8000, always running)
    ↕ hardware
Camera | 4-Mic Array | Speaker | 9-DOF Motors
```

No middleware, no MCP, no session manager, no WebSocket. Pure REST.

---

## Project Structure

```
reachy_mini/
├── pyproject.toml
├── CLAUDE.md                    # Teaches Claude how to use robot tools
├── docs/
│   ├── plan.md                  # This file — architecture & design
│   └── progress.md              # Implementation progress & learnings
├── src/
│   └── reachy_mini_brain/
│       ├── __init__.py
│       ├── robot.py             # REST API client (urllib, no SDK)
│       ├── vision.py            # CLI: take-photo
│       ├── motion.py            # CLI: wake-up, sleep, move-head, look, nod, shake, antennas
│       ├── state.py             # CLI: get-state
│       ├── stt.py               # (Phase 2) faster-whisper wrapper
│       ├── tts.py               # (Phase 2) piper-tts wrapper
│       └── audio.py             # (Phase 2) CLI: listen, speak, doa
├── scripts/
│   ├── voice_conversation.py    # (Phase 3) persistent mic + VAD + STT → Claude → TTS
│   └── cron_check.sh            # (Phase 4) periodic environment checks
├── tests/
│   ├── test_integration.py      # Automated tests against live robot
│   ├── test_e2e.py              # Human-observable tests with confirm()
│   └── test_antenna_manual.py   # Antenna calibration diagnostic
└── .venv/                       # Python 3.12 via uv
```

---

## Phased Implementation

### Phase 1: See + Move
Scaffold the project and get Claude seeing through the camera and moving the robot.

- pyproject.toml, src layout, venv
- `robot.py` — REST API client with auto-daemon-start, retry, caching
- `vision.py` — `take-photo`
- `motion.py` — `wake-up`, `sleep`, `nod`, `shake`, `move-head`, `look`, `antennas`, `rotate-body`
- `state.py` — `get-state`
- `CLAUDE.md` — teaches Claude to use the above
- Integration + E2E tests

**Milestone:** In Claude Code, say "take a photo and describe what you see, then nod" → it works.

### Phase 2: Hear + Speak
Add audio: speech-to-text and text-to-speech through the robot.

- `stt.py` — faster-whisper wrapper
- `tts.py` — piper-tts wrapper
- `audio.py` — `listen`, `speak`, `doa`
- Update CLAUDE.md with audio commands

**Milestone:** "Listen for 5 seconds and tell me what you heard, then say it back" → works.

### Phase 3: Voice Conversation
Continuous hands-free conversation mode.

- `scripts/voice_conversation.py` — persistent mic stream + VAD + STT → Claude → TTS loop

```
Robot audio stream (persistent connection)
    ↓
VAD (silero-vad) — detects speech start/end
    ↓ on utterance end
faster-whisper STT → transcript
    ↓
claude --resume SESSION -p "User: {transcript}"
    ↓ Claude calls speak/move via Bash
piper TTS → robot speaker
    ↓
Loop — ready for next utterance
```

**Why persistent:** The mic stream must stay open. Closing and reopening per turn creates audible gaps and misses speech.

**Milestone:** Speak to robot freely, Claude responds via speaker, multi-turn conversation.

### Phase 4: Scheduled Monitoring
Periodic environment checks.

- `scripts/cron_check.sh` — cron wrapper calling `claude -p "..." --resume`
- Logging and alerting setup

**Milestone:** Robot takes a photo every 30 min, Claude logs observations.

### Phase 5: Polish
- Error handling, reconnection logic
- Configurable personas, wake words
- Tests with mockup sim

---

## REST API Reference

Endpoints on the Reachy Mini daemon (`http://reachy-mini.local:8000`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/daemon/status` | GET | Check daemon state |
| `/api/daemon/start` | POST | Start daemon |
| `/api/motors/status` | GET | Check motor mode |
| `/api/motors/set_mode/{mode}` | POST | Enable/disable motors |
| `/api/move/goto` | POST | Interpolated movement (async, returns UUID) |
| `/api/move/set_target` | POST | Immediate target (no interpolation) |
| `/api/move/running` | GET | List running moves |
| `/api/move/play/{animation}` | POST | Play canned animation (wake_up, goto_sleep) |
| `/api/state/full` | GET | Full robot state |
| `/api/state/present_head_pose` | GET | Current head XYZRPYPose (radians) |
| `/api/state/present_antenna_joint_positions` | GET | Current antenna positions (radians) |

---

## CLI Reference

All commands: `.venv/bin/python -m reachy_mini_brain.<module> <command>`

### motion.py
| Command | Args | Notes |
|---------|------|-------|
| `wake-up` | — | Always run first |
| `sleep` | — | Run when done |
| `move-head` | `--pitch --roll --yaw --duration` | Degrees. pitch: +down/-up, yaw: +left/-right |
| `look` | `--direction left\|right\|up\|down\|center` | Preset positions |
| `rotate-body` | `--angle --duration` | Body yaw in degrees |
| `antennas` | `--left --right` | Degrees, positive = up |
| `nod` | — | Yes gesture (2x pitch cycle) |
| `shake` | — | No gesture (3x yaw cycle) |

### vision.py
| Command | Args | Notes |
|---------|------|-------|
| `take-photo` | `--out PATH` | Default: `/tmp/reachy_photo.jpg` |

### state.py
| Command | Args | Notes |
|---------|------|-------|
| `get-state` | — | Prints full state as JSON |

### audio.py (Phase 2)
| Command | Args | Notes |
|---------|------|-------|
| `listen` | `--duration SEC` | STT, prints transcript |
| `speak` | `TEXT` | TTS through robot speaker |
| `doa` | — | Direction of arrival + speech detection |
