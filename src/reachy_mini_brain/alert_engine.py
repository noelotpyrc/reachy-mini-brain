"""Alert engine — the SEPARATE "check & react" process.

Tails the perception event log (events.jsonl) and, on each new `approach` event,
applies alert policy (here: a global cooldown) and tells the reception daemon to
react — the robot greets the visitor. Decoupled from perception on purpose:
perception only observes & emits; this process decides what to do.

Run alongside the reception daemon:
    python -m reachy_mini_brain.alert_engine [--events PATH] [--cooldown SEC]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from reachy_mini_brain import reception
from reachy_mini_brain.perception import DEFAULT_EVENTS_PATH


def _tail(path: Path, from_end: bool = True):
    """Yield JSON objects from lines appended to `path` (like `tail -f`)."""
    while not path.exists():
        time.sleep(0.5)
    with path.open() as f:
        if from_end:
            f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.3)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def run(events_path: Path, cooldown: float = 15.0) -> None:
    import sys
    try:
        sys.stdout.reconfigure(line_buffering=True)  # flush each line (long-running process)
    except Exception:
        pass
    print(f"alert engine: watching {events_path} (cooldown {cooldown}s)")
    last_react = 0.0
    for ev in _tail(Path(events_path)):
        if ev.get("type") != "approach":
            continue
        now = time.time()
        if now - last_react < cooldown:
            print(f"  approach id={ev.get('id')} — within cooldown, skip")
            continue
        last_react = now
        print(f"  approach id={ev.get('id')} -> telling robot to react")
        try:
            res = reception._client("react")
            print("    daemon:", res.get("result") if res.get("ok") else f"ERROR {res.get('error')}")
        except (FileNotFoundError, ConnectionRefusedError):
            print("    react failed: reception daemon not running")
        except Exception as e:  # noqa: BLE001
            print(f"    react failed: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=str(DEFAULT_EVENTS_PATH), help="event log to tail")
    ap.add_argument("--cooldown", type=float, default=15.0, help="min seconds between reactions")
    args = ap.parse_args()
    run(Path(args.events), args.cooldown)
