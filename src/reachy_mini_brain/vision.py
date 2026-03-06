"""Vision CLI tools for Reachy Mini.

Camera frames come via the SDK's WebRTC pipeline (port 8443).
The REST API doesn't expose camera frames — this is the one place
we still use the reachy-mini SDK.
"""

import os
import sys
import time
from pathlib import Path

import click

from reachy_mini_brain import robot

# Default save location: <project_root>/artifacts/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ARTIFACTS_DIR = _PROJECT_ROOT / "artifacts"


@click.group()
def cli():
    pass


@cli.command()
@click.option("--out", default=None, help="Output image path (default: artifacts/reachy_photo.jpg)")
@click.option("--retries", default=5, help="Frame grab retries (WebRTC needs warmup)")
def take_photo(out, retries):
    """Capture a camera frame and save as JPEG."""
    import cv2
    from reachy_mini import ReachyMini

    if out is None:
        _ARTIFACTS_DIR.mkdir(exist_ok=True)
        out = str(_ARTIFACTS_DIR / "reachy_photo.jpg")

    # Make sure daemon is running before SDK connects
    robot.ensure_ready()

    with ReachyMini() as mini:
        # WebRTC pipeline may need a moment to start streaming
        frame = None
        for i in range(retries):
            frame = mini.media.get_frame()
            if frame is not None:
                break
            print(f"  Waiting for camera frame ({i + 1}/{retries})...", file=sys.stderr)
            time.sleep(1)

        if frame is None:
            click.echo("Error: no frame from camera after retries", err=True)
            raise SystemExit(1)

        cv2.imwrite(out, frame)
        click.echo(out)


if __name__ == "__main__":
    cli()
