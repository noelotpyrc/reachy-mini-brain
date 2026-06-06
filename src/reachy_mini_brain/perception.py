"""Perception pipeline — ties detector + approach + event emission together.

Per frame: RF-DETR persons -> ApproachTracker -> append "approach" events to a
JSONL log. This is what the reception daemon's vision worker runs while vision is
on. Perception only OBSERVES and emits events; alert policy (what to do about an
approach) lives in the separate alert engine, which tails the same log.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_EVENTS_PATH = Path(__file__).resolve().parent.parent.parent / "artifacts" / "events.jsonl"


class PerceptionPipeline:
    def __init__(self, events_path=DEFAULT_EVENTS_PATH, threshold: float = 0.5):
        from reachy_mini_brain.detector import PersonDetector

        self._detector = PersonDetector(threshold=threshold)
        self._approach = None  # built lazily once we know the frame size
        self._events_path = Path(events_path)
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-create the log so an alert engine that starts first (and seeks to end)
        # doesn't miss the very first event written when the file is created.
        self._events_path.touch(exist_ok=True)

    def process(self, frame, *, bgr: bool = True) -> tuple[list[dict], int, list[dict]]:
        """Run one frame through detect -> track -> approach, appending any new
        approach events to the JSONL log. Returns (new_events, n_persons,
        per_track_debug) — the debug list drives the capture/inspection flow."""
        from reachy_mini_brain.approach import ApproachTracker

        if self._approach is None:
            h, w = frame.shape[:2]
            self._approach = ApproachTracker((w, h))

        persons = self._detector.detect(frame, bgr=bgr)
        events = self._approach.update(persons)
        for ev in events:
            rec = {"type": ev["kind"], "ts": round(time.time(), 3),
                   **{k: v for k, v in ev.items() if k != "kind"}}
            with self._events_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        return events, len(persons), self._approach.frame_debug
