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

import click

SOCKET_PATH = "/tmp/reachy_mini_reception.sock"

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

    def listen_read(self):
        # Emit fake speech every 3rd read so the text path is visible.
        self._reads += 1
        if self._reads % 3 == 0:
            return {"text": "hello reachy", "buffer_duration": 2.0}
        return {"text": "", "buffer_duration": 2.0}

    def listen_stop(self):
        log.info("mock session: listen_stop")
        return "stopped"

    # react actions (greeting)
    def speak(self, text, voice="en_US-lessac-medium"):
        log.info("mock session: speak %r", text)

    def look(self, direction):
        log.info("mock session: look %s", direction)

    def antennas(self, left, right):
        log.info("mock session: antennas (%s, %s)", left, right)


# ---------------------------------------------------------------------------
# Reception daemon — the state machine
# ---------------------------------------------------------------------------


class ReceptionDaemon:
    """Owns one session and two independent, toggleable worker threads.

    Each toggle starts/stops ONLY its own worker; neither touches the other
    or the shared session lifecycle. Toggle operations are idempotent.
    """

    def __init__(self, session, vision_interval: float = 2.0,
                 voice_interval: float = 3.0, perception: bool = False,
                 threshold: float = 0.5,
                 greeting: str = "Hello! Welcome. Someone will be with you shortly.",
                 brain: bool = False, brain_model: str = "haiku"):
        self._session = session
        self._vision_interval = vision_interval
        self._voice_interval = voice_interval
        self._perception_enabled = perception
        self._threshold = threshold
        self._greeting = greeting
        self._brain_enabled = brain
        self._brain_model = brain_model
        self._lock = threading.Lock()

        self._vision_thread: threading.Thread | None = None
        self._vision_stop: threading.Event | None = None
        self._voice_thread: threading.Thread | None = None
        self._voice_stop: threading.Event | None = None

    # --- lifecycle ---

    def start(self):
        self._session.start()

    def stop(self):
        """Stop both workers, then the session. Workers first so they never
        call into a torn-down session."""
        self.vision_off()
        self.voice_off()
        self._session.stop()

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
            try:
                frame = self._session.get_frame()
                if frame is None:
                    log.info("vision: no frame yet")
                elif pipe is not None and hasattr(frame, "ndim"):
                    events, n = pipe.process(frame, bgr=True)
                    if events:
                        log.info("vision: %d person(s) | APPROACH %s", n, events)
                    else:
                        log.info("vision: %d person(s)", n)
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
            p = PerceptionPipeline(threshold=self._threshold)
            log.info("vision: perception ready")
            return p
        except Exception as e:  # noqa: BLE001
            log.warning("vision: perception unavailable (%s) — frame-log only", e)
            return None

    # --- voice toggle ---

    def voice_on(self) -> str:
        with self._lock:
            if _alive(self._voice_thread):
                return "voice already on"
            self._voice_stop = threading.Event()
            self._voice_thread = threading.Thread(
                target=self._voice_loop, args=(self._voice_stop,),
                name="voice", daemon=True,
            )
            self._voice_thread.start()
            return "voice on"

    def voice_off(self) -> str:
        with self._lock:
            if not _alive(self._voice_thread):
                return "voice already off"
            self._voice_stop.set()
            t = self._voice_thread
        t.join(timeout=15)
        return "voice off"

    def _voice_loop(self, stop: threading.Event):
        log.info("voice: worker started (interval=%ss, brain=%s)",
                 self._voice_interval, self._brain_enabled)
        brain = self._make_brain() if self._brain_enabled else None
        self._session.listen_start()
        try:
            while not stop.is_set():
                stop.wait(self._voice_interval)
                if stop.is_set():
                    break
                try:
                    res = self._session.listen_read()
                    dur = res.get("buffer_duration", 0.0)
                    text = res.get("text", "")
                    if not text:
                        log.info("voice: %.1fs buffered (silence)", dur)
                        continue
                    log.info("voice: heard %.1fs: %r", dur, text)
                    if brain is not None:
                        reply = brain.respond(text)
                        log.info("voice: reply: %r", reply)
                        self._session.speak(reply)
                except Exception as e:  # noqa: BLE001
                    log.warning("voice: error %s", e)
        finally:
            try:
                self._session.listen_stop()
            except Exception as e:  # noqa: BLE001
                log.warning("voice: listen_stop error %s", e)
            log.info("voice: worker stopped")

    def _make_brain(self):
        try:
            from reachy_mini_brain.brain import ReceptionBrain

            log.info("voice: loading brain (claude -p, model=%s)", self._brain_model)
            return ReceptionBrain(model=self._brain_model)
        except Exception as e:  # noqa: BLE001
            log.warning("voice: brain unavailable (%s) — transcript-log only", e)
            return None

    # --- status ---

    def status(self) -> dict:
        st = {
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
        """Robot greets an approaching visitor: glance + antenna flick + speak."""
        for action in (
            lambda: self._session.look("center"),
            lambda: self._session.antennas(20, 20),
            lambda: self._session.speak(self._greeting),
            lambda: self._session.antennas(0, 0),
        ):
            try:
                action()
            except Exception as e:  # noqa: BLE001
                log.warning("react: action error %s", e)
        log.info("react: greeted visitor")
        return "reacted"


def _alive(t: threading.Thread | None) -> bool:
    return t is not None and t.is_alive()


# ---------------------------------------------------------------------------
# Socket server — keeps the daemon alive, accepts control commands
# ---------------------------------------------------------------------------

# daemon methods callable over the socket
_COMMANDS = {"vision_on", "vision_off", "voice_on", "voice_off", "status", "react"}


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
                 brain: bool = False, brain_model: str = "haiku"):
    """Start the reception daemon + control socket (blocks until shutdown)."""
    if mock:
        session = MockSession()
    else:
        # Lazy import: only here do we pull in the SDK-heavy Session.
        from reachy_mini_brain.session import Session

        session = Session()

    daemon = ReceptionDaemon(
        session, vision_interval=vision_interval, voice_interval=voice_interval,
        perception=perception, threshold=threshold,
        brain=brain, brain_model=brain_model,
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
@click.option("--vision-interval", default=2.0, help="Seconds between frame grabs.")
@click.option("--voice-interval", default=3.0, help="Seconds between mic reads.")
@click.option("--perception/--no-perception", default=False,
              help="Run the RF-DETR person/approach pipeline in the vision worker.")
@click.option("--threshold", default=0.5, help="Detector confidence threshold.")
@click.option("--brain/--no-brain", default=False,
              help="Route heard speech to the claude -p receptionist brain and speak the reply.")
@click.option("--brain-model", default="haiku", help="Brain model (e.g. haiku, sonnet).")
def serve(mock, vision_interval, voice_interval, perception, threshold, brain, brain_model):
    """Run the reception daemon (blocks until `shutdown` or Ctrl-C)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(threadName)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    serve_daemon(mock, vision_interval, voice_interval, perception, threshold,
                 brain, brain_model)


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
def status():
    """Show vision/voice toggle state and session health."""
    _run_client("status")


@cli.command()
def shutdown():
    """Stop the reception daemon."""
    _run_client("shutdown")


if __name__ == "__main__":
    cli()
