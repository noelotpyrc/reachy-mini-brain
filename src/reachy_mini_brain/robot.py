"""Reachy Mini REST API client.

Talks directly to the daemon's HTTP API — no WebSocket SDK needed.
Automatically starts daemon + enables motors before any move command.

Env vars:
    REACHY_HOST: hostname/IP (default: "reachy-mini.local")
    REACHY_PORT: daemon port (default: 8000)
"""

import math
import os
import sys
import time
from http.client import HTTPResponse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json as _json


_TIMEOUT = 30  # seconds — goto blocks for the full movement duration
_RETRIES = 2
_READY_CACHE_TTL = 10  # seconds — skip re-checking if we confirmed ready recently
_last_ready_at = 0.0


def _base_url():
    host = os.environ.get("REACHY_HOST", "reachy-mini.local")
    port = os.environ.get("REACHY_PORT", "8000")
    return f"http://{host}:{port}"


def _request(method: str, path: str, json: dict | None = None, **params) -> dict:
    """HTTP request with retry on timeout/network errors."""
    url = f"{_base_url()}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if qs:
            url = f"{url}?{qs}"
    data = _json.dumps(json).encode() if json else None
    req = Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")

    last_err = None
    for attempt in range(_RETRIES + 1):
        try:
            with urlopen(req, timeout=_TIMEOUT) as resp:
                body = resp.read()
                return _json.loads(body) if body else {}
        except HTTPError as e:
            # Read error body for context
            err_body = e.read().decode() if e.fp else ""
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code == 503:
                # Service unavailable — daemon backend not ready
                if attempt < _RETRIES:
                    print(f"  503 on {path}, retrying in 3s...", file=sys.stderr)
                    time.sleep(3)
                    continue
            raise ConnectionError(f"{path}: {last_err}") from e
        except (URLError, TimeoutError, OSError) as e:
            last_err = str(e)
            if attempt < _RETRIES:
                print(f"  Network error on {path}, retrying in 2s...", file=sys.stderr)
                time.sleep(2)
    raise ConnectionError(f"Failed after {_RETRIES + 1} attempts on {path}: {last_err}")


def _get(path: str, **params) -> dict:
    return _request("GET", path, **params)


def _post(path: str, json: dict | None = None, **params) -> dict:
    return _request("POST", path, json=json, **params)


# --- Daemon lifecycle ---


def ensure_ready():
    """Ensure daemon is running, backend is ready, and motors are enabled.

    Call this before any move command. Idempotent — cached for 10s.
    """
    global _last_ready_at
    if time.time() - _last_ready_at < _READY_CACHE_TTL:
        return  # recently confirmed ready, skip
    status = _get("/api/daemon/status")

    # Step 1: Start daemon if not initialized
    if status.get("state") == "not_initialized":
        print("  Starting daemon...", file=sys.stderr)
        _post("/api/daemon/start", wake_up="false")
        for _ in range(15):
            time.sleep(1)
            status = _get("/api/daemon/status")
            if status.get("state") == "running":
                break
        if status.get("state") != "running":
            raise RuntimeError(f"Daemon failed to start: {status.get('state')}")

    # Step 2: Wait for backend to be ready
    backend = status.get("backend_status") or {}
    if not backend.get("ready"):
        print("  Waiting for backend...", file=sys.stderr)
        for _ in range(10):
            time.sleep(1)
            status = _get("/api/daemon/status")
            backend = status.get("backend_status") or {}
            if backend.get("ready"):
                break

    # Step 3: Enable motors if disabled
    try:
        motor_status = _get("/api/motors/status")
        if motor_status.get("mode") != "enabled":
            _post("/api/motors/set_mode/enabled")
            time.sleep(0.5)
    except ConnectionError:
        # motors/status may fail if backend not fully ready, try enabling anyway
        try:
            _post("/api/motors/set_mode/enabled")
            time.sleep(0.5)
        except ConnectionError:
            pass

    _last_ready_at = time.time()


# --- High-level helpers ---


def wake_up():
    """Wake up the robot (start daemon + enable motors + play wake_up)."""
    ensure_ready()
    _post("/api/move/play/wake_up")
    time.sleep(2.5)
    wait_for_moves()


def go_to_sleep():
    """Put the robot to sleep (play goto_sleep + disable motors)."""
    global _last_ready_at
    _post("/api/move/play/goto_sleep")
    time.sleep(2.5)
    wait_for_moves()
    _post("/api/motors/set_mode/disabled")
    _last_ready_at = 0  # invalidate cache — motors are off now


def wait_for_moves(timeout: float = 10.0):
    """Wait until all running moves complete."""
    start = time.time()
    while time.time() - start < timeout:
        running = _get("/api/move/running")
        if not running:
            return
        time.sleep(0.2)


def _antenna_to_api(antennas: tuple[float, float]) -> list[float]:
    """Convert user-facing antenna degrees to API radians.

    User convention: antennas = (left_degrees, right_degrees)
      - positive = up, negative = down for both
    API convention: target_antennas = [index_0_rad, index_1_rad]
      - antennas are mirror-mounted, so index 1 has inverted sign

    We negate index 1 so that positive = up for both from the user's POV.
    """
    return [math.radians(antennas[0]), -math.radians(antennas[1])]


def goto(
    *,
    pitch: float = 0.0,
    roll: float = 0.0,
    yaw: float = 0.0,
    body_yaw: float | None = None,
    antennas: tuple[float, float] | None = None,
    duration: float = 1.0,
    interpolation: str = "minjerk",
    wait: bool = True,
):
    """Move to a target pose. Angles in degrees.

    antennas: (left_degrees, right_degrees) — positive = up.
    The REST API is async (returns immediately). If wait=True (default),
    blocks until the movement completes.
    """
    ensure_ready()
    body = {"duration": duration, "interpolation": interpolation}
    body["head_pose"] = {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "roll": math.radians(roll),
        "pitch": math.radians(pitch),
        "yaw": math.radians(yaw),
    }
    if body_yaw is not None:
        body["body_yaw"] = math.radians(body_yaw)
    if antennas is not None:
        body["antennas"] = _antenna_to_api(antennas)
    result = _post("/api/move/goto", json=body)
    if wait:
        # Wait for at least the movement duration, then confirm no moves running
        time.sleep(duration + 0.3)
        wait_for_moves(timeout=duration + 5)
    return result


def set_target(
    *,
    pitch: float | None = None,
    roll: float | None = None,
    yaw: float | None = None,
    body_yaw: float | None = None,
    antennas: tuple[float, float] | None = None,
):
    """Set target immediately (no interpolation). Angles in degrees.

    antennas: (left_degrees, right_degrees) — positive = up.
    """
    ensure_ready()
    body = {}
    if any(v is not None for v in [pitch, roll, yaw]):
        body["target_head_pose"] = {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "roll": math.radians(roll or 0),
            "pitch": math.radians(pitch or 0),
            "yaw": math.radians(yaw or 0),
        }
    if body_yaw is not None:
        body["target_body_yaw"] = math.radians(body_yaw)
    if antennas is not None:
        body["target_antennas"] = _antenna_to_api(antennas)
    return _post("/api/move/set_target", json=body)


def set_motors(mode: str):
    """Set motor mode: 'enabled', 'disabled', or 'gravity_compensation'."""
    return _post(f"/api/motors/set_mode/{mode}")


def get_state() -> dict:
    """Get full robot state."""
    return _get("/api/state/full")


def get_head_pose() -> dict:
    """Get current head pose."""
    return _get("/api/state/present_head_pose")


def get_antennas() -> list[float]:
    """Get current antenna positions as [left_rad, right_rad].

    The API returns [index_0, index_1] in radians. Index 1 is negated
    to match user convention (positive = up for both).
    """
    raw = _get("/api/state/present_antenna_joint_positions")
    if isinstance(raw, list) and len(raw) == 2:
        return [raw[0], -raw[1]]
    return raw


def get_body_yaw() -> dict:
    """Get current body yaw."""
    return _get("/api/state/present_body_yaw")


def get_daemon_status() -> dict:
    """Get daemon status."""
    return _get("/api/daemon/status")
