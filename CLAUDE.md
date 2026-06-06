# CLAUDE.md

- Python venv: `.venv/bin/python` (managed by `uv`)
- Install: `uv pip install -e .` (add `".[audio]"` for audio deps)
- Run CLI: `.venv/bin/python -m reachy_mini_brain.<module> <command>`
- Tests: `.venv/bin/python -m pytest tests/ -v` (e2e tests need `-s`)
- **Robot/hardware commands:** Claude may run them (tests, audio, vision, motion — typically over ssh on the brain computer `m1max`), but **must confirm with the user before executing anything that talks to the robot**. For steps that can't be done programmatically (speaking near the robot, power button, physical repositioning/inspection), ask the user to do them.
- Full CLI reference: `docs/robot-guide.md`
