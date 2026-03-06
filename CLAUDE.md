# Reachy Mini Robot

You have a Reachy Mini robot connected over WiFi. Use these Bash commands to interact with it.

All commands run from the project root: `/Users/lliao/work/reachy_mini`

## See

```bash
.venv/bin/python -m reachy_mini_brain.vision take-photo
```
Saves to `artifacts/reachy_photo.jpg` by default. Use `--out PATH` for a custom path.
Then use the Read tool on the saved file to see what the camera sees.

## Move

```bash
# Always wake up first
.venv/bin/python -m reachy_mini_brain.motion wake-up

# Gestures
.venv/bin/python -m reachy_mini_brain.motion nod        # Yes gesture
.venv/bin/python -m reachy_mini_brain.motion shake       # No gesture

# Head control (degrees)
.venv/bin/python -m reachy_mini_brain.motion move-head --pitch 10 --yaw 30
.venv/bin/python -m reachy_mini_brain.motion move-head --pitch 0 --roll 0 --yaw 0  # center

# Look presets
.venv/bin/python -m reachy_mini_brain.motion look --direction left
.venv/bin/python -m reachy_mini_brain.motion look --direction right
.venv/bin/python -m reachy_mini_brain.motion look --direction up
.venv/bin/python -m reachy_mini_brain.motion look --direction down
.venv/bin/python -m reachy_mini_brain.motion look --direction center

# Body rotation (degrees)
.venv/bin/python -m reachy_mini_brain.motion rotate-body --angle 90

# Antennas (degrees)
.venv/bin/python -m reachy_mini_brain.motion antennas --left 30 --right -30

# Sleep when done
.venv/bin/python -m reachy_mini_brain.motion sleep
```

## Check State

```bash
.venv/bin/python -m reachy_mini_brain.state get-state
```
Prints JSON with head pose matrix, antenna angles, and IMU data (if wireless).

## Tips

- Always `wake-up` before moving, `sleep` when done
- `take-photo` saves a JPEG — read the file to see the camera view
- Head pitch: positive = look down, negative = look up
- Head yaw: positive = look left, negative = look right
- Body angle: degrees, full 360 rotation supported
- Antenna angles: in degrees, each antenna independent
