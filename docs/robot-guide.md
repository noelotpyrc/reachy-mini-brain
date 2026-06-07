# Reachy Mini — Robot CLI Guide

Full reference for all CLI commands. All commands run from project root.

## Vision

```bash
# Default (720p, saves to artifacts/reachy_photo.jpg)
.venv/bin/python -m reachy_mini_brain.vision take-photo

# Higher resolution for documents/OCR
.venv/bin/python -m reachy_mini_brain.vision take-photo --resolution 1080p
.venv/bin/python -m reachy_mini_brain.vision take-photo --resolution 4k
.venv/bin/python -m reachy_mini_brain.vision take-photo --resolution max   # 3840x2592

# Custom output path
.venv/bin/python -m reachy_mini_brain.vision take-photo --out /tmp/photo.jpg
```

| Flag | Values | Default | Notes |
|------|--------|---------|-------|
| `--out` | file path | `artifacts/reachy_photo.jpg` | Output JPEG path |
| `--resolution` | `720p`, `1080p`, `4k`, `max` | `720p` | Higher = slower first frame |
| `--retries` | integer | `5` | Frame grab retries (WebRTC warmup) |

**Resolutions:**
- `720p` — 1280×720 @ 30fps (fast, default)
- `1080p` — 1920×1080 @ 30fps (good for general use)
- `4k` — 3840×2160 @ 10fps (high detail)
- `max` — 3840×2592 @ 10fps (near-full-sensor, highest)

## Motion

```bash
# Lifecycle
.venv/bin/python -m reachy_mini_brain.motion wake-up
.venv/bin/python -m reachy_mini_brain.motion sleep

# Gestures
.venv/bin/python -m reachy_mini_brain.motion nod        # Yes gesture
.venv/bin/python -m reachy_mini_brain.motion shake       # No gesture

# Head control (degrees)
.venv/bin/python -m reachy_mini_brain.motion move-head --pitch 10 --yaw 30
.venv/bin/python -m reachy_mini_brain.motion move-head --pitch 0 --roll 0 --yaw 0  # center

# Look presets
.venv/bin/python -m reachy_mini_brain.motion look --direction left
.venv/bin/python -m reachy_mini_brain.motion look --direction right
.venv/bin/python -m reachy_mini_brain.motion look --direction up
.venv/bin/python -m reachy_mini_brain.motion look --direction down
.venv/bin/python -m reachy_mini_brain.motion look --direction center

# Body rotation (degrees)
.venv/bin/python -m reachy_mini_brain.motion rotate-body --angle 90

# Antennas (degrees, positive = up)
.venv/bin/python -m reachy_mini_brain.motion antennas --left 30 --right -30
```

### Commands

| Command | Flags | Notes |
|---------|-------|-------|
| `wake-up` | — | Always run first |
| `sleep` | — | Run when done |
| `move-head` | `--pitch --roll --yaw --duration` | Degrees. Interpolated movement |
| `look` | `--direction left\|right\|up\|down\|center` | Preset positions |
| `rotate-body` | `--angle --duration` | Body yaw in degrees |
| `antennas` | `--left --right` | Degrees, positive = up |
| `nod` | — | Yes gesture (2× pitch cycle) |
| `shake` | — | No gesture (3× yaw cycle) |

### Angle Conventions

- **Pitch:** positive = look down, negative = look up
- **Yaw:** positive = look left, negative = look right
- **Roll:** positive = tilt right, negative = tilt left
- **Antennas:** positive = up, negative = down (both sides)
- **Body:** degrees, full 360° rotation supported

## Audio

```bash
# Listen (record from robot mic, transcribe with Whisper)
.venv/bin/python -m reachy_mini_brain.audio listen --duration 5
.venv/bin/python -m reachy_mini_brain.audio listen --duration 10 --model small --language en

# Speak (TTS through robot speaker)
.venv/bin/python -m reachy_mini_brain.audio speak "Hello, I am Reachy"

# Play a WAV file through robot speaker
.venv/bin/python -m reachy_mini_brain.audio play-sound path/to/file.wav

# Direction of arrival (mic array)
.venv/bin/python -m reachy_mini_brain.audio doa
```

### Commands

| Command | Flags | Notes |
|---------|-------|-------|
| `listen` | `--duration SEC --model tiny\|base\|small\|medium --language CODE --save-wav PATH` | Mic → STT → prints transcript |
| `speak` | `TEXT --voice NAME` | TTS → robot speaker. Default voice: `en_US-lessac-medium` |
| `play-sound` | `PATH` | Play a WAV file through robot speaker |
| `doa` | — | Direction of arrival JSON: `{angle_degrees, speech_detected}` |

### Notes

- Audio goes through SDK WebRTC (same connection as camera), so first call has ~30-60s warmup
- Mic records at 16kHz stereo; STT runs locally via faster-whisper
- TTS runs locally via piper-tts; audio is pushed to robot speaker via WebRTC
- Voice models auto-download on first use (~60MB for whisper-base, ~60MB for piper voice)
- Install audio deps: `uv pip install -e ".[audio]"`

## Video

```bash
# Record 10 seconds of video
.venv/bin/python -m reachy_mini_brain.video record --duration 10

# Custom output and resolution
.venv/bin/python -m reachy_mini_brain.video record --duration 5 --out artifacts/clip.mp4 --resolution 1080p
```

| Flag | Values | Default | Notes |
|------|--------|---------|-------|
| `--duration` | seconds | `10` | Recording length |
| `--out` | file path | `artifacts/reachy_video.mp4` | Output MP4 path |
| `--resolution` | `720p`, `1080p` | `720p` | Capture resolution |
| `--fps` | number | auto (30 or 10) | Target FPS |

## State

```bash
.venv/bin/python -m reachy_mini_brain.state get-state
```

Prints JSON with head pose matrix, antenna angles, and IMU data (if wireless).

## Reception daemon

The resident reception robot (see `docs/plan-reception.md`). One long-lived `serve` process
owns the single robot session; all other `reception` commands are thin clients that talk to it
over a Unix socket (`/tmp/reachy_mini_reception.sock`) — run them from any other shell. Prefix
everything with `.venv/bin/python -m reachy_mini_brain.reception` (or the `reception`
console-script after `pip install -e .`).

```bash
# Start the daemon (blocks). Workers boot OFF; toggle them from another shell.
reception serve --perception --gestures            # vision pipeline + wave detection
reception serve --perception --brain               # vision + claude -p voice brain (needs auth)

# Worker toggles + reactions (from another shell)
reception status                                   # vision/voice + session health (connected/audio/video)
reception vision on | off                          # RF-DETR person/approach pipeline
reception voice  on | off                          # mic → STT → (brain) → speak
reception react                                    # greeting   ("Welcome to Acu Genie!")
reception farewell                                 # goodbye    ("Goodbye! Have a nice day!")
reception wave                                     # wave ack   ("Hi there!" — distinct from greet)
reception reset                                    # head + body + antennas → neutral (no speech)

# Data capture (vision must be ON)
reception record  on | off                         # camera → artifacts/video-<ts>.mkv  (crash-resilient)
reception capture on | off                         # per-frame tracks/events → artifacts/capture-<ts>.jsonl
reception stream  on | off                         # live MJPEG on 127.0.0.1:8090 (view via ssh -L 8090:localhost:8090)

reception shutdown                                 # graceful stop — finalizes record/capture, removes socket

# Alert engine — SEPARATE process: tails artifacts/events.jsonl → fires robot reactions
python -m reachy_mini_brain.alert_engine --cooldown 5   # approach→react, depart→farewell, wave→wave_back
#  ([--types approach,depart,wave] restricts which event types fire)
```

### `serve` flags

| Flag | Default | Notes |
|------|---------|-------|
| `--perception / --no-perception` | off | Run RF-DETR person/approach pipeline in the vision worker |
| `--gestures / --no-gestures` | off | Also run MediaPipe wave detection (`Open_Palm`) — needs `mediapipe` |
| `--brain / --no-brain` | off | Route heard speech to the `claude -p` receptionist brain |
| `--brain-model` | `sonnet` | Brain model (`haiku` in practice) |
| `--vision-interval` | `2.0` | Seconds between frame grabs (post-processing wait; ~3 fps at 0.2) |
| `--voice-interval` | `3.0` | Seconds between mic reads |
| `--threshold` | `0.5` | Detector confidence threshold |
| `--mock` | — | Fake session (no SDK/robot) for plumbing tests |

### Notes

- **Durable log:** the daemon writes `artifacts/logs/reception-<ts>.log` (timestamped, survives
  restarts). Launch it detached with `nohup caffeinate -dimsu … &` on m1max so it doesn't sleep.
- **Recording is `.mkv`** (`mp4v` codec): a hard kill/battery-off keeps footage up to the crash
  (an `.mp4` would be unreadable without its trailing index). Graceful `shutdown`/`record off`
  finalizes cleanly either way.
- **One session only:** the daemon and the official Control app can't both hold the robot — stop
  one before the other.
- Offline replay/eval of recorded clips: `python -m reachy_mini_brain.replay <clip> [--trace]
  [--smooth N] [--annotate out.mkv] [--expect-approach N --expect-depart N]`.
