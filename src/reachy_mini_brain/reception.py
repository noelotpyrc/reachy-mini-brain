"""Reception daemon — Phase A: control plane + lifecycle.

A resident process that owns one live hardware Session and supervises two
INDEPENDENT worker loops, each gated by its own toggle:

  - vision : grab a frame every N seconds  (stub — detector/VLM land in Phase B)
  - voice  : continuous-listen buffer + periodic read
             (stub — agentic brain lands in Phase C)

This is the piece that replaces "Claude Code drives the robot": the daemon
stays alive, holds the WebRTC session warm, and flips vision/voice on and off
without tearing down the shared connection.

Control surface (Unix socket, same newline-JSON protocol as session.py):

    reception serve [--mock]      # run the daemon (blocks)
    reception status
    reception vision on|off
    reception voice  on|off
    reception shutdown

`--mock` swaps in a fake session (no SDK / no robot) so the state machine,
socket protocol, and lifecycle can be exercised on a dev machine. The real
Session is imported lazily and only when serving for real, so the client
commands and the mock never pull in the SDK.

Top-level imports are stdlib + click ONLY — keep it that way so this module
loads without the SDK present.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import click

SOCKET_PATH = "/tmp/reachy_mini_reception.sock"
ARTIFACTS = Path(__file__).resolve().parent.parent.parent / "artifacts"

log = logging.getLogger("reception")


# ---------------------------------------------------------------------------
# Mock session — stand-in for Session on a machine with no robot / no SDK
# ---------------------------------------------------------------------------


class _FakeFrame:
    """Minimal frame stand-in: just enough for `frame.shape` / truthiness."""

    def __init__(self, shape):
        self.shape = shape


class MockSession:
    """Logs instead of touching hardware. Mirrors the Session methods the
    reception workers use: start/stop/status, get_frame, listen_start/read/stop.
    """

    def __init__(self):
        self._reads = 0
        self._audio_record_path = "artifacts/audio-mock.wav"
        self._audio_record_meta = "artifacts/audio-mock.jsonl"
        self._audio_record_run_id = None

    # lifecycle
    def start(self):
        log.info("mock session: start")

    def stop(self):
        log.info("mock session: stop")

    def status(self):
        return {"mock": True, "connected": True}

    # vision
    def get_frame(self):
        return _FakeFrame((480, 640, 3))

    # voice
    def listen_start(self, model="base", language="en"):
        log.info("mock session: listen_start")
        return "listening"

    def listen_read(self, timeout: float = 1.0):
        # Emit fake speech every 3rd read so the text path is visible.
        time.sleep(min(timeout, 0.5))
        self._reads += 1
        if self._reads % 3 == 0:
            return {"text": "hello reachy", "buffer_duration": 2.0}
        return {"text": "", "buffer_duration": 2.0}

    def listen_stop(self):
        log.info("mock session: listen_stop")
        return "stopped"

    def audio_record_start(self, path=None, run_id=None):
        log.info("mock session: audio_record_start")
        if path:
            self._audio_record_path = path
            self._audio_record_meta = str(Path(path).with_suffix(".jsonl"))
        self._audio_record_run_id = run_id
        return {
            "recording": True,
            "path": self._audio_record_path,
            "metadata": self._audio_record_meta,
            "samples": 0,
            "duration": 0.0,
            "chunks": 0,
            "run_id": self._audio_record_run_id,
        }

    def audio_record_stop(self):
        log.info("mock session: audio_record_stop")
        return {
            "recording": False,
            "path": self._audio_record_path,
            "metadata": self._audio_record_meta,
            "samples": 0,
            "duration": 0.0,
            "chunks": 0,
            "run_id": self._audio_record_run_id,
        }

    # react actions (greeting)
    def speak(self, text, voice="en_US-lessac-medium"):
        log.info("mock session: speak %r", text)

    def prerender(self, text, voice="en_US-lessac-medium"):
        log.info("mock session: prerender %r", text)

    def look(self, direction):
        log.info("mock session: look %s", direction)

    def antennas(self, left, right):
        log.info("mock session: antennas (%s, %s)", left, right)

    def move_head(self, pitch=0.0, roll=0.0, yaw=0.0, duration=1.0):
        log.info("mock session: move_head pitch=%s roll=%s yaw=%s", pitch, roll, yaw)

    def rotate_body(self, angle, duration=1.0):
        log.info("mock session: rotate_body %s", angle)


# ---------------------------------------------------------------------------
# Reception daemon — the state machine
# ---------------------------------------------------------------------------


class ReceptionDaemon:
    """Owns one session and two independent, toggleable worker threads.

    Each toggle starts/stops ONLY its own worker; neither touches the other
    or the shared session lifecycle. Toggle operations are idempotent.
    """

    def __init__(self, session, vision_interval: float = 0.2,
                 voice_interval: float = 1.5, perception: bool = False,
                 threshold: float = 0.5, gestures: bool = False,
                 greeting: str = "Welcome to Acu Genie!",
                 farewell: str = "Goodbye! Have a nice day!",
                 wave_message: str = "Hi there!",
                 conversation_opener: str = "Hi! How can I help?",
                 conv_idle_timeout: float = 45.0, conv_max_duration: float = 480.0,
                 brain: bool = False, brain_model: str = "sonnet",
                 brain_backend: str = "claude", save_turns: bool = False,
                 run_id: str | None = None, log_path: Path | None = None):
        self._run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self._log_path = Path(log_path) if log_path else None
        self._session = session
        self._vision_interval = vision_interval
        self._voice_interval = voice_interval
        self._perception_enabled = perception
        self._threshold = threshold
        self._gestures = gestures
        self._greeting = greeting
        self._farewell = farewell
        self._wave_message = wave_message
        self._conversation_opener = conversation_opener
        self._conv_idle_timeout = conv_idle_timeout
        self._conv_max_duration = conv_max_duration
        self._conversation_mode = False
        self._brain_enabled = brain
        self._brain_backend = brain_backend
        self._brain_model_requested = brain_model
        self._brain_model = self._resolve_brain_model(brain_backend, brain_model)
        self._save_turns = save_turns
        self._turns_jsonl = None
        self._turns_manifest_idx: int | None = None
        self._turn_n = 0
        self._lock = threading.Lock()
        self._manifest_lock = threading.Lock()
        self._artifact_counts: dict[str, int] = {}
        self._manifest_path = ARTIFACTS / "runs" / f"run-{self._run_id}.json"
        self._manifest = {
            "run_id": self._run_id,
            "started_ts": round(time.time(), 3),
            "pid": os.getpid(),
            "config": {
                "vision_interval": self._vision_interval,
                "voice_interval": self._voice_interval,
                "perception": self._perception_enabled,
                "threshold": self._threshold,
                "gestures": self._gestures,
                "brain": self._brain_enabled,
                "brain_model": self._brain_model,
                "brain_model_requested": self._brain_model_requested,
                "brain_backend": self._brain_backend,
                "save_turns": self._save_turns,
            },
            "artifacts": {
                "log": ([{"path": str(self._log_path)}] if self._log_path else []),
                "events": [{
                    "path": str(ARTIFACTS / "events.jsonl"),
                    "run_id_field": True,
                    "mode": "append",
                }],
                "video": [],
                "capture": [],
                "audio": [],
                "turns": [],
            },
        }

        self._vision_thread: threading.Thread | None = None
        self._vision_stop: threading.Event | None = None
        self._voice_thread: threading.Thread | None = None
        self._voice_stop: threading.Event | None = None

        # debug capture (records per-frame vision data for a manual test run)
        self._capturing = False
        self._capture_path: Path | None = None
        self._capture_frames = 0
        self._capture_events = 0
        self._capture_manifest_idx: int | None = None

        # video recording (persist the camera frames the vision worker grabs)
        self._recording = False
        self._record_path: Path | None = None
        self._record_writer = None
        self._record_frames = 0
        self._record_manifest_idx: int | None = None

        # raw audio recording (Cat-1 mic signal, owned by Session)
        self._audio_record_manifest_idx: int | None = None

        # live MJPEG stream of the vision worker's frames (view via an ssh tunnel)
        self._streaming = False
        self._latest_frame = None
        self._stream_server = None

        self._write_manifest()

    # --- run manifest ---

    @staticmethod
    def _resolve_brain_model(backend: str, requested: str) -> str:
        """Return the actual model used by the selected brain backend."""
        if backend == "pydantic":
            from reachy_mini_brain.brain import default_openrouter_model

            return default_openrouter_model()
        return requested

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def manifest_path(self) -> Path:
        return self._manifest_path

    def _artifact_path(self, kind: str, suffix: str, *, directory: Path | None = None) -> Path:
        """Return a per-run artifact path with a stable counter: kind-run_id-01.ext."""
        self._artifact_counts[kind] = self._artifact_counts.get(kind, 0) + 1
        root = directory or ARTIFACTS
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{kind}-{self._run_id}-{self._artifact_counts[kind]:02d}{suffix}"

    def _write_manifest(self) -> None:
        """Persist the manifest; callers hold no lock so this can be used from workers."""
        with self._manifest_lock:
            self._manifest["updated_ts"] = round(time.time(), 3)
            self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._manifest_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._manifest, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
            tmp.replace(self._manifest_path)

    def _manifest_add_artifact(self, kind: str, **fields) -> int:
        with self._manifest_lock:
            items = self._manifest["artifacts"].setdefault(kind, [])
            rec = {"started_ts": round(time.time(), 3), **fields}
            items.append(rec)
            idx = len(items) - 1
        self._write_manifest()
        return idx

    def _manifest_update_artifact(self, kind: str, idx: int | None, **fields) -> None:
        if idx is None:
            return
        with self._manifest_lock:
            items = self._manifest["artifacts"].setdefault(kind, [])
            if idx >= len(items):
                return
            items[idx].update(fields)
            items[idx]["updated_ts"] = round(time.time(), 3)
        self._write_manifest()

    # --- lifecycle ---

    def start(self):
        log.info("run_id=%s manifest -> %s", self._run_id, self._manifest_path)
        self._session.start()
        # Warm the speech cache for the fixed lines so the first opener/greet/goodbye/wave has
        # no synthesis latency — cuts the wave->reaction startup lag.
        for line in (self._greeting, self._farewell, self._wave_message, self._conversation_opener):
            try:
                self._session.prerender(line)
            except Exception as e:  # noqa: BLE001
                log.warning("prerender failed: %s", e)

    def stop(self):
        """Stop both workers, then the session. Workers first so they never
        call into a torn-down session. Finalize record/capture AFTER the vision
        thread is joined (so no frame is mid-write) — a graceful shutdown must
        release the VideoWriter, or the mp4 is left unfinalized and unreadable."""
        self.vision_off()
        self.voice_off()
        self.audio_record_off()
        self.record_off()
        self.capture_off()
        self._manifest_update_artifact(
            "turns", self._turns_manifest_idx, status="closed",
            ended_ts=round(time.time(), 3), turns=self._turn_n,
        )
        self._session.stop()
        self._manifest["ended_ts"] = round(time.time(), 3)
        self._write_manifest()

    # --- vision toggle ---

    def vision_on(self) -> str:
        with self._lock:
            if _alive(self._vision_thread):
                return "vision already on"
            self._vision_stop = threading.Event()
            self._vision_thread = threading.Thread(
                target=self._vision_loop, args=(self._vision_stop,),
                name="vision", daemon=True,
            )
            self._vision_thread.start()
            return "vision on"

    def vision_off(self) -> str:
        with self._lock:
            if not _alive(self._vision_thread):
                return "vision already off"
            self._vision_stop.set()
            t = self._vision_thread
        t.join(timeout=10)
        return "vision off"

    def _vision_loop(self, stop: threading.Event):
        log.info("vision: worker started (interval=%ss, perception=%s)",
                 self._vision_interval, self._perception_enabled)
        pipe = self._make_perception() if self._perception_enabled else None
        while not stop.is_set():
            # Pause perception while the robot speaks — RF-DETR contends with the audio
            # push thread (CPU/GIL) and makes speech choppy. Vision idles for the few
            # seconds of a greeting/reply, then resumes. (First pass; the proper fix is
            # to run perception in its own OS process so it never has to pause.)
            if getattr(self._session, "_speaking", False):
                stop.wait(0.1)
                continue
            try:
                frame = self._session.get_frame()
                if frame is None:
                    log.info("vision: no frame yet")
                else:
                    self._latest_frame = frame  # published for the MJPEG stream
                    if self._recording:
                        self._write_video(frame)
                    if pipe is not None and hasattr(frame, "ndim"):
                        events, n, tracks = pipe.process(frame, bgr=True)
                        if events:
                            log.info("vision: %d person(s) | APPROACH %s", n, events)
                        else:
                            log.info("vision: %d person(s)", n)
                        if self._capturing:
                            self._write_capture(n, tracks, events)
                    else:
                        log.info("vision: frame ok %s", tuple(frame.shape))
            except Exception as e:  # noqa: BLE001 — keep the loop alive
                log.warning("vision: error %s", e)
            stop.wait(self._vision_interval)
        log.info("vision: worker stopped")

    def _make_perception(self):
        try:
            from reachy_mini_brain.perception import PerceptionPipeline

            log.info("vision: loading perception (RF-DETR)...")
            p = PerceptionPipeline(
                threshold=self._threshold,
                gestures=self._gestures,
                run_id=self._run_id,
            )
            log.info("vision: perception ready")
            return p
        except Exception as e:  # noqa: BLE001
            log.warning("vision: perception unavailable (%s) — frame-log only", e)
            return None

    # --- capture toggle (debug: record per-frame vision data for a test run) ---

    def capture_on(self) -> str:
        """Start recording every vision frame's tracks/decisions to a fresh file."""
        with self._lock:
            self._capture_path = self._artifact_path("capture", ".jsonl")
            self._capture_path.parent.mkdir(parents=True, exist_ok=True)
            self._capture_path.write_text("")
            self._capture_frames = 0
            self._capture_events = 0
            self._capturing = True
            self._capture_manifest_idx = self._manifest_add_artifact(
                "capture", path=str(self._capture_path), status="open"
            )
        log.info("capture: started -> %s", self._capture_path)
        return f"capturing -> {self._capture_path}"

    def capture_off(self) -> dict:
        """Stop recording; return where the file is and what it caught."""
        with self._lock:
            self._capturing = False
            summary = {
                "path": str(self._capture_path) if self._capture_path else None,
                "frames": self._capture_frames,
                "events": self._capture_events,
            }
        log.info("capture: stopped (%s frames, %s events)",
                 summary["frames"], summary["events"])
        self._manifest_update_artifact(
            "capture", self._capture_manifest_idx, status="closed",
            ended_ts=round(time.time(), 3), frames=summary["frames"],
            events=summary["events"],
        )
        self._capture_manifest_idx = None
        return summary

    def _write_capture(self, n: int, tracks: list, events: list):
        rec = {
            "run_id": self._run_id,
            "ts": round(time.time(), 2),
            "n": n,
            "tracks": tracks,
            "events": events,
        }
        try:
            with open(self._capture_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            self._capture_frames += 1
            self._capture_events += len(events)
        except Exception as e:  # noqa: BLE001
            log.warning("capture: write error %s", e)

    # --- record toggle (persist the camera frames to an mp4) ---

    def record_on(self) -> str:
        """Record the frames the vision worker grabs to an mkv (needs vision on).
        Matroska (not mp4) so a hard kill / battery-off keeps the footage up to the
        crash — mp4 needs a trailing moov index written at release() and is otherwise
        unreadable. Same mp4v codec, same size. Frame rate follows --vision-interval."""
        with self._lock:
            if self._recording:  # don't clobber an in-progress recording's writer
                return f"already recording -> {self._record_path} ({self._record_frames} frames so far)"
            self._record_path = self._artifact_path("video", ".mkv")
            self._record_path.parent.mkdir(parents=True, exist_ok=True)
            self._record_writer = None  # lazy-created on first frame (needs w/h)
            self._record_frames = 0
            self._recording = True
            fps = 1.0 / self._vision_interval if self._vision_interval else 5.0
            self._record_manifest_idx = self._manifest_add_artifact(
                "video", path=str(self._record_path), status="open", fps=round(fps, 2)
            )
        log.info("record: started -> %s (~%.1f fps)", self._record_path, fps)
        return f"recording -> {self._record_path}  (vision must be ON; ~{fps:.1f} fps)"

    def record_off(self) -> dict:
        with self._lock:
            self._recording = False
            writer, self._record_writer = self._record_writer, None
            summary = {"path": str(self._record_path) if self._record_path else None,
                       "frames": self._record_frames}
        if writer is not None:
            writer.release()
        log.info("record: stopped (%s frames) -> %s", summary["frames"], summary["path"])
        self._manifest_update_artifact(
            "video", self._record_manifest_idx, status="closed",
            ended_ts=round(time.time(), 3), frames=summary["frames"],
        )
        self._record_manifest_idx = None
        return summary

    def _write_video(self, frame):
        try:
            if self._record_writer is None:
                import cv2
                h, w = frame.shape[:2]
                fps = max(1.0, 1.0 / self._vision_interval) if self._vision_interval else 5.0
                self._record_writer = cv2.VideoWriter(
                    str(self._record_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            self._record_writer.write(frame)
            self._record_frames += 1
        except Exception as e:  # noqa: BLE001
            log.warning("record: write error %s", e)

    # --- live MJPEG stream (debug: view what vision sees, over an ssh tunnel) ---

    def stream_on(self) -> str:
        """Serve the vision worker's latest frame as MJPEG on localhost:8090 (needs vision on).
        View via tunnel: `ssh -L 8090:localhost:8090 <m1max>` then http://localhost:8090."""
        port = 8090
        with self._lock:
            if self._streaming:
                return f"already streaming on :{port}"
            import cv2
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            daemon = self

            class _Handler(BaseHTTPRequestHandler):
                def log_message(self, *a):  # keep the daemon log quiet
                    pass

                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    while daemon._streaming:
                        frame = daemon._latest_frame
                        ok = False
                        if frame is not None:
                            ok, jpg = cv2.imencode(".jpg", frame)
                        if not ok:
                            time.sleep(0.1)
                            continue
                        try:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                            self.wfile.write(jpg.tobytes())
                            self.wfile.write(b"\r\n")
                        except (BrokenPipeError, ConnectionResetError):
                            break
                        time.sleep(0.15)

            self._stream_server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            self._stream_server.daemon_threads = True
            self._streaming = True
            threading.Thread(target=self._stream_server.serve_forever, name="stream", daemon=True).start()
        log.info("stream: MJPEG on 127.0.0.1:%d", port)
        return f"streaming on :{port} — ssh -L {port}:localhost:{port} <m1max>, then http://localhost:{port}"

    def stream_off(self) -> str:
        with self._lock:
            self._streaming = False
            srv, self._stream_server = self._stream_server, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        log.info("stream: stopped")
        return "stream off"

    # --- voice toggle ---

    def voice_on(self, conversation: bool = False) -> str:
        with self._lock:
            if _alive(self._voice_thread):
                return "voice already on"
            self._conversation_mode = conversation
            self._voice_stop = threading.Event()
            self._voice_thread = threading.Thread(
                target=self._voice_loop, args=(self._voice_stop,),
                name="voice", daemon=True,
            )
            self._voice_thread.start()
            return "voice on" + (" (conversation)" if conversation else "")

    def voice_off(self) -> str:
        with self._lock:
            if not _alive(self._voice_thread):
                return "voice already off"
            self._voice_stop.set()
            t = self._voice_thread
        t.join(timeout=15)
        return "voice off"

    def _voice_loop(self, stop: threading.Event):
        log.info("voice: worker started (interval=%ss, brain=%s, conversation=%s)",
                 self._voice_interval, self._brain_enabled, self._conversation_mode)
        brain = self._make_brain() if self._brain_enabled else None
        if brain is not None:
            brain.prewarm()  # spawn the claude process now — it initializes while the visitor
            # speaks their first words, so the FIRST reply isn't slowed by process startup.
        self._session.listen_start()
        start_ts = last_heard = time.monotonic()
        try:
            while not stop.is_set():
                # Conversation auto-end: idle (talker silent) OR a hard max-duration cap.
                # FIRST PASS: idle resets on ANY transcript, so background noise heard-as-text
                # can hold it open — the max cap bounds that. Speaker-aware close (reset only
                # on the enrolled talker's voice) is the planned v2.
                if self._conversation_mode:
                    now = time.monotonic()
                    if now - last_heard > self._conv_idle_timeout:
                        log.info("voice: conversation ended (idle %.0fs)", now - last_heard)
                        break
                    if now - start_ts > self._conv_max_duration:
                        log.info("voice: conversation ended (max cap %.0fs)", now - start_ts)
                        break
                try:
                    # Blocks up to 1s for ONE complete VAD-endpointed utterance (not a
                    # time-slice). The 1s timeout keeps the idle/max checks above responsive.
                    res = self._session.listen_read(timeout=1.0)
                    text = res.get("text", "")
                    if not text:
                        continue
                    dur = res.get("buffer_duration", 0.0)
                    last_heard = time.monotonic()
                    log.info("voice: heard %.1fs: %r", dur, text)
                    if brain is not None:
                        think_stop = threading.Event()
                        threading.Thread(
                            target=self._think_animate, args=(think_stop,), daemon=True
                        ).start()
                        try:
                            reply = brain.respond(text)
                            log.info("voice: reply: %r", reply)
                            self._session.speak(reply)  # antennas auto-stop when voice starts
                            if self._save_turns:
                                self._save_turn(res.get("audio"), text, reply)
                        finally:
                            think_stop.set()
                except Exception as e:  # noqa: BLE001
                    log.warning("voice: error %s", e)
        finally:
            try:
                self._session.listen_stop()
            except Exception as e:  # noqa: BLE001
                log.warning("voice: listen_stop error %s", e)
            self._conversation_mode = False
            log.info("voice: worker stopped")

    def _make_brain(self):
        try:
            if self._brain_backend == "pydantic":
                from reachy_mini_brain.brain import PydanticBrain

                brain = PydanticBrain(model=self._brain_model)
                log.info("voice: loading brain (pydantic-ai/openrouter, model=%s)", brain.model)
                return brain
            from reachy_mini_brain.brain import ReceptionBrain

            log.info("voice: loading brain (claude -p, model=%s)", self._brain_model)
            return ReceptionBrain(model=self._brain_model)
        except Exception as e:  # noqa: BLE001
            log.warning("voice: brain unavailable (%s) — transcript-log only", e)
            return None

    def _save_turn(self, audio, heard: str, reply: str) -> None:
        """Debug capture (--save-turns): save each turn's utterance WAV + the heard STT text
        and the brain reply, so off replies can be attributed to STT vs brain (listen to the
        wav, compare to `heard`). Records to artifacts/turns/turns-<ts>.jsonl + per-turn wavs."""
        if audio is None:
            return
        try:
            import soundfile as sf

            d = ARTIFACTS / "turns"
            d.mkdir(parents=True, exist_ok=True)
            if self._turns_jsonl is None:
                self._turns_jsonl = d / f"turns-{self._run_id}.jsonl"
                self._turns_manifest_idx = self._manifest_add_artifact(
                    "turns", path=str(self._turns_jsonl), status="open"
                )
            self._turn_n += 1
            wav = d / f"turn-{self._run_id}-{self._turn_n:03d}.wav"
            sf.write(str(wav), audio, 16000)
            rec = {"ts": time.time(), "n": self._turn_n, "dur": round(len(audio) / 16000.0, 2),
                   "heard": heard, "reply": reply, "wav": wav.name, "run_id": self._run_id}
            with open(self._turns_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self._manifest_update_artifact(
                "turns", self._turns_manifest_idx, status="open",
                turns=self._turn_n, latest_wav=str(wav),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("save_turn error %s", e)

    # --- status ---

    def status(self) -> dict:
        st = {
            "run_id": self._run_id,
            "manifest": str(self._manifest_path),
            "vision": "on" if _alive(self._vision_thread) else "off",
            "voice": "on" if _alive(self._voice_thread) else "off",
        }
        try:
            st["session"] = self._session.status()
        except Exception as e:  # noqa: BLE001
            st["session"] = f"error: {e}"
        return st

    # --- react (the alert engine triggers this) ---

    def react(self) -> str:
        """Greet an approaching visitor."""
        if self._conversation_mode:
            log.info("react: suppressed (conversation active)")
            return "suppressed (in conversation)"
        return self._express(self._greeting, "react: greeted visitor", "reacted")

    def reset(self) -> str:
        """Reset head + body + antennas to a neutral 'home' pose (no speech/gesture)."""
        self._session.move_head(pitch=0.0, roll=0.0, yaw=0.0, duration=0.8)
        self._session.rotate_body(0.0, duration=0.8)
        self._session.antennas(0.0, 0.0)
        log.info("reset: head/body/antennas to neutral")
        return "reset: head + body + antennas neutral"

    def farewell(self) -> str:
        """Say goodbye to a departing visitor."""
        if self._conversation_mode:
            log.info("farewell: suppressed (conversation active)")
            return "suppressed (in conversation)"
        return self._express(self._farewell, "farewell: said goodbye", "farewelled")

    def wave_back(self) -> str:
        """Acknowledge a wave — a DISTINCT response from the approach greeting so the
        two are easy to tell apart when testing wave detection."""
        return self._express(self._wave_message, "wave_back: acknowledged a wave", "waved back")

    def start_conversation(self) -> str:
        """Wave-triggered: BEGIN a conversation — speak an opener, then start the voice/brain
        loop (which auto-ends on idle or the max-duration cap). Idempotent while one is active.
        Needs --brain + a keychain-authed context for claude -p (e.g. the daemon run from tmux)."""
        if _alive(self._voice_thread):
            return "already in conversation"
        self._express(self._conversation_opener, "conversation: opened", "opened")
        return self.voice_on(conversation=True)

    def _express(self, message: str, done_log: str, result: str) -> str:
        """Flick antennas, speak, reset antennas. Deliberately does NOT move the head:
        the camera rides on the head, so any glance would tilt/shift every video frame.
        Antennas are separate joints (not in the camera view) so they're safe to keep."""
        for action in (
            lambda: self._session.antennas(20, 20),
            lambda: self._session.speak(message, cache=True),
            lambda: self._session.antennas(0, 0),
        ):
            try:
                action()
            except Exception as e:  # noqa: BLE001
                log.warning("express: action error %s", e)
        log.info(done_log)
        return result

    def _think_animate(self, stop_evt: threading.Event) -> None:
        """Wiggle the antennas to signal 'thinking' during the heard->reply gap.

        Fills the dead time (brain call + TTS synth) so the robot doesn't look frozen.
        Stops the instant reply audio starts (session._speaking flips True) or stop_evt
        is set, then resets antennas to neutral. Antennas only — the camera rides on the
        head, so we never move it mid-turn."""
        poses = ((25, 10), (10, 25))  # gentle alternating sway
        i = 0
        while not stop_evt.is_set() and not getattr(self._session, "_speaking", False):
            try:
                self._session.antennas(*poses[i % len(poses)])
            except Exception:  # noqa: BLE001
                pass
            i += 1
            stop_evt.wait(0.3)
        try:
            self._session.antennas(0, 0)
        except Exception:  # noqa: BLE001
            pass

    # --- raw audio recording ---

    def audio_record_on(self) -> dict:
        """Start recording raw continuous mic audio (Cat-1) through the shared session mic loop."""
        if self._audio_record_manifest_idx is not None:
            return self._session.audio_record_start()
        path = self._artifact_path("audio", ".wav")
        self._audio_record_manifest_idx = self._manifest_add_artifact(
            "audio", path=str(path), metadata=str(path.with_suffix(".jsonl")), status="open"
        )
        summary = self._session.audio_record_start(str(path), run_id=self._run_id)
        log.info("audio_record: started -> %s", summary.get("path"))
        return summary

    def audio_record_off(self) -> dict:
        """Stop raw continuous mic audio recording."""
        summary = self._session.audio_record_stop()
        log.info("audio_record: stopped (%s samples) -> %s",
                 summary.get("samples"), summary.get("path"))
        self._manifest_update_artifact(
            "audio", self._audio_record_manifest_idx, status="closed",
            ended_ts=round(time.time(), 3), samples=summary.get("samples"),
            duration=summary.get("duration"), chunks=summary.get("chunks"),
        )
        self._audio_record_manifest_idx = None
        return summary


def _alive(t: threading.Thread | None) -> bool:
    return t is not None and t.is_alive()


# ---------------------------------------------------------------------------
# Socket server — keeps the daemon alive, accepts control commands
# ---------------------------------------------------------------------------

# daemon methods callable over the socket
_COMMANDS = {"vision_on", "vision_off", "voice_on", "voice_off", "status", "react",
             "farewell", "reset", "wave_back", "start_conversation",
             "capture_on", "capture_off", "record_on", "record_off",
             "stream_on", "stream_off", "audio_record_on", "audio_record_off"}


def _send(conn: socket.socket, obj: dict):
    conn.sendall((json.dumps(obj) + "\n").encode())


def _handle(daemon: ReceptionDaemon, conn: socket.socket) -> bool:
    """Handle one connection. Returns False if the server should stop."""
    try:
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        if not data:
            return True

        msg = json.loads(data.decode().strip())
        method = msg.get("method", "")

        if method == "shutdown":
            _send(conn, {"ok": True, "result": "shutting down"})
            return False
        if method not in _COMMANDS:
            _send(conn, {"ok": False, "error": f"unknown command: {method}"})
            return True

        result = getattr(daemon, method)()
        _send(conn, {"ok": True, "result": result})
    except Exception as e:  # noqa: BLE001
        try:
            _send(conn, {"ok": False, "error": str(e)})
        except Exception:
            pass
    finally:
        conn.close()
    return True


def serve_daemon(mock: bool, vision_interval: float, voice_interval: float,
                 perception: bool = False, threshold: float = 0.5,
                 gestures: bool = False,
                 brain: bool = False, brain_model: str = "sonnet",
                 brain_backend: str = "claude", save_turns: bool = False,
                 run_id: str | None = None, log_path: Path | None = None):
    """Start the reception daemon + control socket (blocks until shutdown)."""
    if mock:
        session = MockSession()
    else:
        # Lazy import: only here do we pull in the SDK-heavy Session.
        from reachy_mini_brain.session import Session

        session = Session()

    daemon = ReceptionDaemon(
        session, vision_interval=vision_interval, voice_interval=voice_interval,
        perception=perception, threshold=threshold, gestures=gestures,
        brain=brain, brain_model=brain_model, brain_backend=brain_backend,
        save_turns=save_turns,
        run_id=run_id,
        log_path=log_path,
    )

    log.info("starting session%s...", " (mock)" if mock else "")
    daemon.start()

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    server.settimeout(1.0)

    running = True

    def _sig(_s, _f):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log.info("reception daemon ready on %s (vision=off, voice=off)", SOCKET_PATH)

    while running:
        try:
            conn, _ = server.accept()
            if not _handle(daemon, conn):
                running = False
        except socket.timeout:
            continue
        except OSError:
            break

    log.info("shutting down...")
    daemon.stop()
    server.close()
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    log.info("reception daemon stopped")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _client(method: str, timeout: float = 30.0) -> dict:
    """Send one control command to the running daemon."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(SOCKET_PATH)
    sock.sendall((json.dumps({"method": method}) + "\n").encode())
    data = b""
    while b"\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    return json.loads(data.decode().strip())


def _run_client(method: str):
    try:
        result = _client(method)
    except (FileNotFoundError, ConnectionRefusedError):
        click.echo(
            "Error: reception daemon not running. "
            "Start it with: reception serve",
            err=True,
        )
        raise SystemExit(1)
    if result.get("ok"):
        r = result.get("result")
        click.echo(json.dumps(r, indent=2) if isinstance(r, dict) else r)
    else:
        click.echo(f"Error: {result.get('error')}", err=True)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
def cli():
    """Reachy Mini reception daemon (Phase A: control plane)."""
    pass


@cli.command()
@click.option("--mock", is_flag=True, help="Use a fake session (no SDK/robot).")
@click.option("--vision-interval", default=0.2,
              help="Seconds between frame grabs (~5 fps). The approach/depart geometry is "
                   "calibrated for this — 2.0 (0.5 fps) stretches reset_absent 8s->80s and breaks greet/goodbye.")
@click.option("--voice-interval", default=1.5,
              help="Seconds between mic reads — lower = faster turn-taking (VAD endpointing is the deeper fix).")
@click.option("--perception/--no-perception", default=False,
              help="Run the RF-DETR person/approach pipeline in the vision worker.")
@click.option("--threshold", default=0.5, help="Detector confidence threshold.")
@click.option("--gestures/--no-gestures", default=False,
              help="Also run MediaPipe wave detection (Open_Palm) in the vision worker.")
@click.option("--brain/--no-brain", default=False,
              help="Route heard speech to the claude -p receptionist brain and speak the reply.")
@click.option("--brain-model", default="sonnet", help="claude backend model (sonnet/haiku/opus).")
@click.option("--brain-backend", type=click.Choice(["claude", "pydantic"]), default="claude",
              help="Brain backend: claude -p (default) or pydantic-ai over OpenRouter.")
@click.option("--save-turns/--no-save-turns", default=False,
              help="Debug: save each turn's utterance WAV + heard/reply to artifacts/turns/ "
                   "(to attribute off replies to STT vs brain).")
def serve(mock, vision_interval, voice_interval, perception, threshold, gestures, brain,
          brain_model, brain_backend, save_turns):
    """Run the reception daemon (blocks until `shutdown` or Ctrl-C)."""
    # Durable log: the daemon owns a timestamped file under artifacts/logs/ (never /tmp,
    # which the OS cleans), in addition to stderr. Survives restarts; never overwritten.
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    logfile = ARTIFACTS / "logs" / f"reception-{run_id}.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(threadName)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(logfile)],
    )
    log.info("durable log -> %s", logfile)
    log.info("run_id -> %s", run_id)
    serve_daemon(mock, vision_interval, voice_interval, perception, threshold,
                 gestures, brain, brain_model, brain_backend, save_turns,
                 run_id=run_id, log_path=logfile)


@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]))
def vision(state):
    """Toggle the vision worker on or off."""
    _run_client(f"vision_{state}")


@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]))
def voice(state):
    """Toggle the voice worker on or off."""
    _run_client(f"voice_{state}")


@cli.command()
def react():
    """Trigger the robot's greeting reaction (normally called by the alert engine)."""
    _run_client("react")


@cli.command()
def reset():
    """Reset the robot pose: head + body + antennas to neutral (no speech)."""
    _run_client("reset")


@cli.command()
def farewell():
    """Trigger the robot's goodbye (normally the alert engine fires this on departure)."""
    _run_client("farewell")


@cli.command()
def wave():
    """Trigger the wave acknowledgment (manual; standalone "Hi there!", no conversation)."""
    _run_client("wave_back")


@cli.command()
def converse():
    """Begin a conversation (opener + voice loop) — what a wave now triggers."""
    _run_client("start_conversation")


@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]))
def capture(state):
    """Record per-frame vision data to artifacts/capture-*.jsonl for a test run."""
    _run_client(f"capture_{state}")


@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]))
def record(state):
    """Record the camera to artifacts/video-*.mkv (needs vision on)."""
    _run_client(f"record_{state}")


@cli.command("audio-record")
@click.argument("state", type=click.Choice(["on", "off"]))
def audio_record(state):
    """Record raw mic audio to artifacts/audio-*.wav + .jsonl sidecar."""
    _run_client(f"audio_record_{state}")


@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]))
def stream(state):
    """Toggle a live MJPEG camera stream on localhost:8090 (needs vision on)."""
    _run_client(f"stream_{state}")


@cli.command()
def status():
    """Show vision/voice toggle state and session health."""
    _run_client("status")


@cli.command()
def shutdown():
    """Stop the reception daemon."""
    _run_client("shutdown")


if __name__ == "__main__":
    cli()
