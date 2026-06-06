"""Approach detection — Layers 2 & 3 of the reception vision pipeline.

Layer 2 (tracking): give each person a stable id across frames.
Layer 3 (approach logic): classify a tracked person as APPROACHING vs just
TRANSIT, using pure geometry on their trajectory — no model:
  - box area GROWING over time            -> getting closer
  - box large enough now (near the desk)  -> actually here, not far off
  - present for >= min_dwell frames        -> not a one-frame blip

Consumes the person `Detections` from `detector.PersonDetector` and, per frame,
returns the NEW "someone is approaching" events (latched once per track id).

Notes / future upgrades (kept out of the model on purpose):
  - direction-toward-a-desk-zone can be added to `_is_approaching`.
  - VLM intent-checking ("patient vs. staff passing through") layers on later.
"""

from __future__ import annotations

import warnings

import numpy as np
import supervision as sv


class ApproachTracker:
    def __init__(
        self,
        frame_wh: tuple[int, int],
        min_dwell: int = 5,
        growth_factor: float = 1.6,
        min_area_frac: float = 0.06,
        history: int = 30,
    ):
        self.W, self.H = frame_wh
        self.min_dwell = min_dwell
        self.growth_factor = growth_factor
        self.min_area_frac = min_area_frac
        self.history = history
        # sv.ByteTrack is deprecated in supervision 0.28 (removed in 0.30) but
        # works fine; isolated here so swapping the tracker is a one-line change.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._tracker = sv.ByteTrack()
        self._tracks: dict[int, list[tuple[float, float, float]]] = {}
        self._fired: set[int] = set()

    def update(self, persons: sv.Detections) -> list[dict]:
        """Feed one frame's person detections. Returns a list of NEW approach
        events — `{id, area, cx, cy}` — for tracks that just qualified."""
        tracked = self._tracker.update_with_detections(persons)
        frame_area = float(self.W * self.H)
        events: list[dict] = []

        ids_now = set()
        for i in range(len(tracked)):
            if tracked.tracker_id is None:
                continue
            tid = int(tracked.tracker_id[i])
            ids_now.add(tid)
            x1, y1, x2, y2 = tracked.xyxy[i]
            area = ((x2 - x1) * (y2 - y1)) / frame_area
            cx = ((x1 + x2) / 2) / self.W
            cy = ((y1 + y2) / 2) / self.H

            hist = self._tracks.setdefault(tid, [])
            hist.append((area, cx, cy))
            if len(hist) > self.history:
                hist.pop(0)

            if tid not in self._fired and self._is_approaching(hist):
                self._fired.add(tid)
                events.append({"id": tid, "area": float(round(area, 3)),
                               "cx": float(round(cx, 2)), "cy": float(round(cy, 2))})

        # forget tracks that disappeared so re-entry can re-fire
        gone = set(self._tracks) - ids_now
        for tid in gone:
            self._tracks.pop(tid, None)
            self._fired.discard(tid)
        return events

    def _is_approaching(self, hist: list[tuple[float, float, float]]) -> bool:
        if len(hist) < self.min_dwell:
            return False
        first_area = hist[0][0]
        cur_area = hist[-1][0]
        grew = first_area > 0 and (cur_area / first_area) >= self.growth_factor
        near = cur_area >= self.min_area_frac
        return grew and near
