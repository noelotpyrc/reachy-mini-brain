"""Stage-1 semi-live test harness — replay a video through the perception pipeline.

Pumps a video file's frames through PerceptionPipeline exactly as the live vision
worker would, and reports the events (approach/depart) + an optional per-frame
trajectory. No robot, no daemon, no WebRTC — reproducible, fast, runnable in the
dev venv / CI. This is where approach & depart logic gets tuned and regression-tested
against labelled scenario clips, *before* spending a live robot session.

    reception-replay clip.mp4                       # report events
    reception-replay clip.mp4 --trace               # + per-frame track stats
    reception-replay approach.mp4 --expect-approach 1 --expect-depart 0   # assert (CI)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import click


@click.command()
@click.argument("video", type=click.Path(exists=True))
@click.option("--threshold", default=0.5, help="Detector confidence threshold.")
@click.option("--every", default=1, help="Process every Nth frame (subsample).")
@click.option("--trace/--no-trace", default=False, help="Print per-frame per-track stats.")
@click.option("--reverse", is_flag=True, help="Process frames in reverse — turns an approach clip into a depart test.")
@click.option("--from-frame", type=int, default=0, help="Skip frames before this index (e.g. feed ONLY the walk-away, no walk-up).")
@click.option("--expect-approach", type=int, default=None, help="Assert this many approach events.")
@click.option("--expect-depart", type=int, default=None, help="Assert this many depart events.")
def main(video, threshold, every, trace, reverse, from_frame, expect_approach, expect_depart):
    """Replay VIDEO through the perception pipeline and report/assert events."""
    import cv2

    from reachy_mini_brain.perception import PerceptionPipeline

    # isolated events log so we never touch the daemon's real artifacts/events.jsonl
    tmp = Path(tempfile.gettempdir()) / "reachy_replay_events.jsonl"
    pipe = PerceptionPipeline(events_path=tmp, threshold=threshold)

    cap = cv2.VideoCapture(video)
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if reverse:
        frames.reverse()
    if from_frame:
        frames = frames[from_frame:]

    counts = {"approach": 0, "depart": 0}
    processed = 0
    for i, frame in enumerate(frames):
        if i % every != 0:
            continue
        processed += 1
        events, n, dbg = pipe.process(frame, bgr=True)
        for ev in events:
            counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
            click.echo(f"  frame {i:4d}: {ev['kind'].upper()}  {ev}")
        if trace:
            for d in dbg:
                click.echo(f"    f{i:4d} id={d['id']} area={d['area']:.3f}")

    click.echo(f"=> {processed} frames processed | approach={counts['approach']} depart={counts['depart']}")

    expects = [("approach", expect_approach), ("depart", expect_depart)]
    asserted = [e for e in expects if e[1] is not None]
    if asserted:
        ok = all(counts[k] == v for k, v in asserted)
        for k, v in asserted:
            flag = "ok" if counts[k] == v else "FAIL"
            click.echo(f"  [{flag}] {k}: got {counts[k]}, expected {v}")
        click.echo("PASS" if ok else "FAIL")
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
