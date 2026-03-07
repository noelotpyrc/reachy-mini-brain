"""Persistent session for Reachy Mini.

Holds a single ReachyMini() SDK instance alive, keeping all channels
(vision, audio, motion) warm and accessible through one unified API.
Eliminates the 30-60s WebRTC cold start between interactions.

Two modes:
  1. In-process:  Session() used directly in Python
  2. Background:  `python -m reachy_mini_brain.session serve` keeps the
     session alive as a Unix-socket server.  Send commands with:
       `python -m reachy_mini_brain.session call speak "Hello"`
     or programmatically with send_command("speak", "Hello").
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import click
import numpy as np

from reachy_mini_brain import robot

# Apply macOS GStreamer patch before any ReachyMini() is created.
from reachy_mini_brain.audio import _patch_bin_add_check

_patch_bin_add_check()

SOCKET_PATH = "/tmp/reachy_mini_session.sock"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ARTIFACTS_DIR = _PROJECT_ROOT / "artifacts"


# ---------------------------------------------------------------------------
# Session (in-process)
# ---------------------------------------------------------------------------


class Session:
    """Persistent connection to Reachy Mini — all channels through one object."""

    def __init__(self, warmup_audio: bool = True, warmup_video: bool = True):
        self._warmup_audio = warmup_audio
        self._warmup_video = warmup_video
        self._mini = None
        self._push_lock = threading.Lock()

    # --- Lifecycle ---

    def start(self) -> None:
        """Create SDK instance and warm up all channels."""
        from reachy_mini import ReachyMini

        robot.ensure_ready()
        robot._session_active = True

        print("  Creating SDK connection...", file=sys.stderr)
        self._mini = ReachyMini()

        if self._warmup_audio:
            print("  Warming up audio pipeline...", file=sys.stderr)
            if not self._wait_for_audio(timeout=60):
                print("  Warning: audio pipeline did not start", file=sys.stderr)
            else:
                time.sleep(1)  # let send chain finish (per conversation app)
                print("  Audio ready", file=sys.stderr)

        if self._warmup_video:
            print("  Warming up video pipeline...", file=sys.stderr)
            if not self._wait_for_video(timeout=60):
                print("  Warning: video pipeline did not start", file=sys.stderr)
            else:
                print("  Video ready", file=sys.stderr)

        print("  Session started", file=sys.stderr)

    def stop(self) -> None:
        """Graceful shutdown — close media pipelines and disconnect."""
        if self._mini is None:
            return
        # Stop listener thread if running
        if hasattr(self, "_listen_stop"):
            self._listen_stop.set()
            if hasattr(self, "_listen_thread"):
                self._listen_thread.join(timeout=5)
        try:
            self._mini.media_manager.close()
        except Exception:
            pass
        try:
            self._mini.client.disconnect()
        except Exception:
            pass
        robot._session_active = False
        self._mini = None
        print("  Session stopped", file=sys.stderr)

    def status(self) -> dict:
        """Return health info."""
        return {
            "connected": self.is_connected,
            "audio_ready": self.is_audio_ready,
            "video_ready": self._mini is not None and self._mini.media.get_frame() is not None,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # --- Health ---

    @property
    def is_connected(self) -> bool:
        return self._mini is not None and self._mini.client.is_connected()

    @property
    def is_audio_ready(self) -> bool:
        if self._mini is None:
            return False
        appsrc = getattr(self._mini.media.audio, "_appsrc", None)
        return appsrc is not None

    # --- Vision ---

    def get_frame(self) -> np.ndarray | None:
        """Get the latest camera frame as a numpy array (BGR)."""
        self._check()
        return self._mini.media.get_frame()

    def take_photo(self, path: str = "") -> str:
        """Capture a frame and save as JPEG. Returns the path."""
        import cv2

        self._check()
        if not path:
            _ARTIFACTS_DIR.mkdir(exist_ok=True)
            path = str(_ARTIFACTS_DIR / "reachy_photo.jpg")
        frame = self._mini.media.get_frame()
        if frame is None:
            raise RuntimeError("No frame available from camera")
        cv2.imwrite(path, frame)
        return path

    # --- Audio ---

    def get_audio_sample(self) -> np.ndarray | None:
        """Get the latest audio sample from the robot mic."""
        self._check()
        return self._mini.media.get_audio_sample()

    def push_audio_sample(self, data: np.ndarray) -> None:
        """Push audio to the robot speaker (thread-safe)."""
        self._check()
        with self._push_lock:
            self._mini.media.push_audio_sample(data)

    def listen(
        self,
        duration: float = 5.0,
        model: str = "base",
        language: str = "en",
    ) -> str:
        """Record from robot mic and transcribe to text.

        Returns transcript string (empty if silence detected).
        """
        from reachy_mini_brain import stt

        self._check()

        chunks: list[np.ndarray] = []
        start = time.time()
        while time.time() - start < duration:
            sample = self._mini.media.get_audio_sample()
            if sample is not None:
                chunks.append(sample)
            time.sleep(0.01)

        if not chunks:
            return ""

        audio = np.concatenate(chunks)
        if audio.ndim > 1:
            audio = audio[:, 0]

        lang = None if language == "auto" else language
        return stt.transcribe_array(audio, sample_rate=16000, model_size=model, language=lang)

    def speak(self, text: str, voice: str = "en_US-lessac-medium") -> None:
        """Synthesize text and play through robot speaker."""
        from reachy_mini_brain import tts

        self._check()

        audio, sample_rate = tts.synthesize_array(text, voice=voice)
        if audio.size == 0:
            return

        # Resample to 16kHz
        if sample_rate != 16000:
            from scipy.signal import resample

            num_samples = int(len(audio) * 16000 / sample_rate)
            audio = resample(audio, num_samples).astype(np.float32)

        # Keep mono — MediaManager handles channel conversion
        if audio.ndim > 1:
            audio = audio[:, 0]

        # Flag so listener thread discards mic input during playback
        if hasattr(self, "_speaking"):
            self._speaking = True

        # Push in chunks, paced to real-time
        chunk_size = 320
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i : i + chunk_size]
            self.push_audio_sample(chunk)
            time.sleep(chunk_size / 16000 * 0.9)

        time.sleep(0.5)  # let final chunks play out

        if hasattr(self, "_speaking"):
            self._speaking = False

    # --- Continuous listening ---

    def listen_start(self, model: str = "base", language: str = "en") -> str:
        """Start continuous background listening.

        A daemon thread buffers raw audio from the mic.
        Call listen_read() to transcribe and retrieve what's been said.
        """
        self._check()
        if hasattr(self, "_listen_thread") and self._listen_thread.is_alive():
            return "already listening"

        self._listen_stop = threading.Event()
        self._listen_buffer: list[np.ndarray] = []
        self._listen_lock = threading.Lock()
        self._listen_model = model
        self._listen_language = language
        self._speaking = False  # set by speak() to flag own-voice audio
        self._listen_thread = threading.Thread(
            target=self._listen_loop, daemon=True,
        )
        self._listen_thread.start()
        return "listening"

    def listen_read(self) -> str:
        """Transcribe and return buffered audio, then clear the buffer.

        Returns transcript string (empty if silence/nothing buffered).
        """
        if not hasattr(self, "_listen_lock"):
            return ""

        with self._listen_lock:
            chunks = list(self._listen_buffer)
            self._listen_buffer.clear()

        if not chunks:
            return ""

        from reachy_mini_brain import stt

        audio = np.concatenate(chunks)
        if audio.ndim > 1:
            audio = audio[:, 0]

        lang = None if self._listen_language == "auto" else self._listen_language
        return stt.transcribe_array(
            audio, sample_rate=16000, model_size=self._listen_model, language=lang,
        )

    def listen_stop(self) -> str:
        """Stop continuous background listening."""
        if hasattr(self, "_listen_stop"):
            self._listen_stop.set()
            if hasattr(self, "_listen_thread"):
                self._listen_thread.join(timeout=10)
        return "stopped"

    def _listen_loop(self) -> None:
        """Background thread: buffer raw audio samples from the mic."""
        while not self._listen_stop.is_set():
            sample = self._mini.media.get_audio_sample()
            if sample is not None and not self._speaking:
                with self._listen_lock:
                    self._listen_buffer.append(sample)
            time.sleep(0.01)

    # --- Motion ---

    def move_head(
        self,
        pitch: float = 0.0,
        roll: float = 0.0,
        yaw: float = 0.0,
        duration: float = 1.0,
    ) -> None:
        """Move head to target orientation (degrees)."""
        robot.goto(pitch=pitch, roll=roll, yaw=yaw, duration=duration)

    def set_target(self, **kwargs) -> None:
        """Set pose immediately (no interpolation). Angles in degrees."""
        robot.set_target(**kwargs)

    def look(self, direction: str) -> None:
        """Look in a preset direction: left, right, up, down, center."""
        presets = {
            "left": dict(yaw=30),
            "right": dict(yaw=-30),
            "up": dict(pitch=-20),
            "down": dict(pitch=20),
            "center": dict(),
        }
        if direction not in presets:
            raise ValueError(f"Unknown direction: {direction}")
        robot.goto(**presets[direction], duration=0.8)

    def nod(self) -> None:
        """Nod the head (yes gesture)."""
        for _ in range(2):
            robot.goto(pitch=15, duration=0.3)
            robot.goto(pitch=0, duration=0.3)

    def shake(self) -> None:
        """Shake the head (no gesture)."""
        robot.goto(yaw=20, duration=0.3)
        robot.goto(yaw=-20, duration=0.3)
        robot.goto(yaw=20, duration=0.3)
        robot.goto(yaw=0, duration=0.3)

    def antennas(self, left: float, right: float) -> None:
        """Set antenna positions (degrees). Positive = up."""
        robot.set_target(antennas=(left, right))

    def rotate_body(self, angle: float, duration: float = 1.0) -> None:
        """Rotate body to angle (degrees)."""
        robot.goto(body_yaw=angle, duration=duration)

    # --- State ---

    def get_state(self) -> dict:
        """Get full robot state."""
        return robot.get_state()

    # --- Internal ---

    def _check(self) -> None:
        if self._mini is None:
            raise RuntimeError("Session not started — call start() first")

    def _wait_for_audio(self, timeout: float = 60.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            sample = self._mini.media.get_audio_sample()
            if sample is not None and sample.size > 0:
                return True
            time.sleep(0.5)
        return False

    def _wait_for_video(self, timeout: float = 60.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            frame = self._mini.media.get_frame()
            if frame is not None:
                return True
            time.sleep(1)
        return False


# ---------------------------------------------------------------------------
# Session server — keeps Session alive, accepts commands over Unix socket
# ---------------------------------------------------------------------------

# Methods safe to call remotely (no numpy args/returns needed).
_REMOTE_METHODS = {
    "speak", "listen", "nod", "shake", "look", "take_photo",
    "move_head", "rotate_body", "antennas", "get_state", "status",
    "listen_start", "listen_read", "listen_stop",
}

# Aliases for convenience
_METHOD_ALIASES = {
    "health": "status",
}


def _handle_connection(session: Session, conn: socket.socket) -> bool:
    """Handle one client connection. Returns False if server should stop."""
    try:
        data = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
            # Messages are newline-terminated JSON
            if b"\n" in data:
                break
        if not data:
            return True

        msg = json.loads(data.decode().strip())
        method = msg.get("method", "")
        args = msg.get("args", [])
        kwargs = msg.get("kwargs", {})

        if method == "stop":
            _send(conn, {"ok": True, "result": "stopping"})
            return False

        method = _METHOD_ALIASES.get(method, method)

        if method not in _REMOTE_METHODS:
            _send(conn, {"ok": False, "error": f"unknown method: {method}"})
            return True

        fn = getattr(session, method)
        result = fn(*args, **kwargs)

        # Make result JSON-serializable
        if isinstance(result, np.ndarray):
            result = f"<ndarray shape={result.shape}>"
        _send(conn, {"ok": True, "result": result})

    except Exception as e:
        try:
            _send(conn, {"ok": False, "error": str(e)})
        except Exception:
            pass
    finally:
        conn.close()
    return True


def _send(conn: socket.socket, obj: dict) -> None:
    conn.sendall((json.dumps(obj) + "\n").encode())


def serve_session() -> None:
    """Start a persistent session server on a Unix socket."""
    # Clean up stale socket
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    session = Session()
    session.start()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    server.settimeout(1.0)

    running = True

    def _sighandler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    print(f"Session server listening on {SOCKET_PATH}", file=sys.stderr)
    print("Send commands with: python -m reachy_mini_brain.session call <method> [args...]", file=sys.stderr)

    while running:
        try:
            conn, _ = server.accept()
            if not _handle_connection(session, conn):
                running = False
        except socket.timeout:
            continue
        except OSError:
            break

    session.stop()
    server.close()
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    print("Session server shut down", file=sys.stderr)


# ---------------------------------------------------------------------------
# Client — send a command to the running session server
# ---------------------------------------------------------------------------


def send_command(method: str, *args, timeout: float = 120.0, **kwargs) -> dict:
    """Send a command to the running session server.

    Returns {"ok": bool, "result": ..., "error": ...}.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(SOCKET_PATH)
    msg = json.dumps({"method": method, "args": list(args), "kwargs": kwargs}) + "\n"
    sock.sendall(msg.encode())

    data = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
        if b"\n" in data:
            break
    sock.close()
    return json.loads(data.decode().strip())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
def cli():
    """Reachy Mini persistent session."""
    pass


@cli.command()
def serve():
    """Start the persistent session server (blocks until stopped)."""
    serve_session()


@cli.command()
@click.argument("method")
@click.argument("args", nargs=-1)
def call(method, args):
    """Send a command to the running session server.

    Examples:
        call speak "Hello world"
        call listen 5
        call nod
        call look left
        call take_photo artifacts/pic.jpg
        call stop
    """
    parsed = []
    for a in args:
        try:
            parsed.append(float(a))
            # Keep as int if it's a whole number
            if parsed[-1] == int(parsed[-1]):
                parsed[-1] = int(parsed[-1])
        except ValueError:
            parsed.append(a)

    try:
        result = send_command(method, *parsed)
    except FileNotFoundError:
        click.echo("Error: session server not running. Start with: python -m reachy_mini_brain.session serve", err=True)
        raise SystemExit(1)
    except ConnectionRefusedError:
        click.echo("Error: session server not responding", err=True)
        raise SystemExit(1)

    if result.get("ok"):
        r = result.get("result")
        if r is not None:
            if isinstance(r, dict):
                click.echo(json.dumps(r, indent=2))
            else:
                click.echo(r)
    else:
        click.echo(f"Error: {result.get('error')}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
