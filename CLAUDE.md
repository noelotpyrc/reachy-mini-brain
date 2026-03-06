# CLAUDE.md

- Python venv: `.venv/bin/python` (managed by `uv`)
- Install: `uv pip install -e .` (add `".[audio]"` for audio deps)
- Run CLI: `.venv/bin/python -m reachy_mini_brain.<module> <command>`
- Tests: `.venv/bin/python -m pytest tests/ -v` (e2e tests need `-s`)
- **Robot/hardware tests must be run by the user** — never attempt to run e2e tests, audio, vision, motion, or any command that talks to the robot. Provide the command and let the user run it.
- Full CLI reference: `docs/robot-guide.md`
