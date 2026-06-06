"""Approach + departure detection — Layer 3 of the reception vision pipeline.

From the DOMINANT (largest / closest) person's box-area envelope across a "visit",
emit two id-agnostic events:

  - approach (greet)  : Gate 1 — a NEW visitor is present (a visit starts);
                        Gate 2 — their box is GROWING (rising over recent frames +
                        grown from their entry size) AND has reached the desk area
                        (>= greet_floor, so we don't greet a distant speck).
  - depart  (goodbye) : a visitor who got near is now RECEDING — the dominant area
                        has dropped to <= depart_factor x their OWN visit peak.

Id-agnostic on purpose: ByteTrack reassigns ids when a person turns front->back to
leave, which fragments per-track state. The dominant-area envelope is robust to that.
Single-dominant-visitor model (a reception desk); multi-person is a later refinement.

Box area is a fraction of the frame (0..1) — a noisy, person-size-dependent proxy for
closeness — so triggers are RELATIVE to the visitor's own trajectory (entry/peak)
wherever possible. Depart needs no absolute size (it references the peak); greet needs
one small floor because there's no stable reference at the very start of a visit.
"""

from __future__ import annotations

import warnings

import supervision as sv


class ApproachTracker:
    def __init__(
        self,
        frame_wh: tuple[int, int],
        growth_factor: float = 1.3,
        greet_floor: float = 0.10,
        min_area_frac: float = 0.06,
        depart_factor: float = 0.6,
        present_frac: float = 0.03,
        reset_absent: int = 40,
        history: int = 30,
    ):
        self.W, self.H = frame_wh
        self.growth_factor = growth_factor  # greet Gate 2: area grown >= this x the visit's entry size
        self.greet_floor = greet_floor      # greet Gate 2: AND reached >= this (clearly in the area, not a speck)
        self.min_area_frac = min_area_frac  # depart: the visit must have peaked >= this to count as a real visitor
        self.depart_factor = depart_factor  # depart: fire when area drops to <= this x the visit peak
        self.present_frac = present_frac    # dominant area >= this => a visitor is present
        self.reset_absent = reset_absent    # frames absent before the visit resets (survives the close blind spot)
        self.history = history
        self.frame_debug: list[dict] = []
        # sv.ByteTrack is deprecated in supervision 0.28 (removed in 0.30) but works fine.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._tracker = sv.ByteTrack()
        self._reset_visit()

    def _reset_visit(self) -> None:
        self._visit_min = 0.0     # smallest dominant area this visit (entry size)
        self._visit_peak = 0.0    # largest dominant area this visit (closest)
        self._greet_fired = False
        self._depart_fired = False
        self._absent = 0
        self._dom_hist: list[float] = []

    def update(self, persons: sv.Detections) -> list[dict]:
        """One frame of person detections -> NEW events
        `{kind: "approach"|"depart", id, area, cx, cy}` (once per visit per kind)."""
        tracked = self._tracker.update_with_detections(persons)
        frame_area = float(self.W * self.H)
        frame_debug: list[dict] = []
        dom_area, dom = 0.0, None
        for i in range(len(tracked)):
            if tracked.tracker_id is None:
                continue
            tid = int(tracked.tracker_id[i])
            x1, y1, x2, y2 = tracked.xyxy[i]
            area = ((x2 - x1) * (y2 - y1)) / frame_area
            cx = ((x1 + x2) / 2) / self.W
            cy = ((y1 + y2) / 2) / self.H
            if area > dom_area:                       # the dominant (closest) person this frame
                dom_area, dom = area, (tid, area, cx, cy)
            frame_debug.append({"id": int(tid), "area": float(round(area, 3)),
                                "cx": float(round(cx, 2)), "cy": float(round(cy, 2))})
        self.frame_debug = frame_debug
        return self._update_visit(dom_area, dom)

    def _update_visit(self, dom_area: float, dom) -> list[dict]:
        events: list[dict] = []
        if dom_area >= self.present_frac:             # a visitor is present
            self._absent = 0
            self._dom_hist.append(dom_area)
            if len(self._dom_hist) > self.history:
                self._dom_hist.pop(0)
            self._visit_peak = max(self._visit_peak, dom_area)
            self._visit_min = dom_area if self._visit_min == 0.0 else min(self._visit_min, dom_area)

            # GREET — Gate 1: present & not yet greeted. Gate 2: growing AND in the area.
            if not self._greet_fired and dom is not None and dom_area >= self.greet_floor:
                grew = self._visit_min > 0 and dom_area / self._visit_min >= self.growth_factor
                rising = len(self._dom_hist) >= 3 and dom_area > self._dom_hist[-3]
                if grew and rising:
                    self._greet_fired = True
                    events.append(self._event("approach", *dom))

            # DEPART — a real visitor (peaked >= min_area) now receded to <= factor x their peak.
            if not self._depart_fired and self._visit_peak >= self.min_area_frac:
                thresh = self._visit_peak * self.depart_factor
                receding = len(self._dom_hist) >= 2 and all(a <= thresh for a in self._dom_hist[-2:])
                if receding:
                    self._depart_fired = True
                    events.append(self._event("depart", *dom))
        else:                                         # no visitor in view
            self._absent += 1
            if self._absent >= self.reset_absent:     # visitor truly gone -> next is a new visit
                self._reset_visit()
        return events

    @staticmethod
    def _event(kind: str, tid: int, area: float, cx: float, cy: float) -> dict:
        return {"kind": kind, "id": int(tid), "area": float(round(area, 3)),
                "cx": float(round(cx, 2)), "cy": float(round(cy, 2))}
