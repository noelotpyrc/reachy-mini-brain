# Runbook — bring up the reception robot

How to start the daemon and get reactions (greet / goodbye / wave) working, on
**m1max** (the brain computer). Two setup steps are easy to miss — see Gotchas.

All commands run on m1max: `ssh leon@100.127.86.67` (Tailscale). Robot daemon at
`192.168.1.165` (`REACHY_HOST`), control socket at `/tmp/reachy_mini_reception.sock`.

## 1. Start the daemon (must be from the `claude-test` tmux session)

The daemon shells out to `claude -p` for the brain, which needs keychain auth — that
only works from the GUI-rooted tmux session. **Don't launch it from a plain SSH shell.**

```bash
# attach the session:  tmux attach -t claude-test   (or send-keys into it)
cd ~/projects/reachy_mini && export REACHY_HOST=192.168.1.165 && \
  nohup caffeinate -dimsu .venv/bin/python -m reachy_mini_brain.reception serve \
    --perception --gestures --brain --brain-model haiku \
    --vision-interval 0.2 --save-turns > /tmp/reception_brain.log 2>&1 &
```

`caffeinate` keeps the Mac awake; `nohup` survives the SSH session closing. The daemon
prints a `run_id` and writes `artifacts/runs/run-<run_id>.json`.

## 2. Turn on the workers (serve comes up IDLE)

`serve` starts with **vision=off, voice=off** and recording off. Toggle what you need:

```bash
reception() { .venv/bin/python -m reachy_mini_brain.reception "$@"; }
reception vision on          # perception + gestures (required for any detection)
reception record on          # raw video  -> artifacts/video-<run_id>-NN.mkv   (needs vision on)
reception capture on         # per-frame tracks/events -> capture-<run_id>-NN.jsonl
reception audio-record on    # raw Cat-1 mic audio -> audio-<run_id>-NN.wav + .jsonl   (optional)
# voice turns on automatically when a wave starts a conversation — leave it off
```

## 3. Start the alert engine (SEPARATE process — this is what reacts)

The daemon only *detects* and logs events. A second process turns events into robot
reactions. **Without it, the robot sees you but never greets/waves back.**

```bash
nohup .venv/bin/python -m reachy_mini_brain.alert_engine --cooldown 5 \
  > /tmp/alert_engine.log 2>&1 &
```

Event → action: `approach → greet`, `depart → goodbye`, `wave → start a conversation`
(voice on + brain; be ready to talk). Restrict with `--types approach,depart` to skip the
live-conversation path.

## 4. Verify

```bash
reception status                              # run_id, vision/voice, session connected
tail -f /tmp/alert_engine.log                 # should print reactions as you approach/wave
grep <run_id> artifacts/events.jsonl | tail   # approach/depart/wave events being written
```

## 5. Teardown

```bash
reception shutdown          # finalizes record/capture/audio, removes the socket
pkill -f alert_engine       # stop the alert engine separately
```

## Gotchas (why this runbook exists)

1. **Launch the daemon from the `claude-test` tmux session**, not a plain SSH shell —
   `claude -p` needs keychain auth that only the GUI-rooted session has.
2. **The alert engine is a separate process.** `serve` + `vision on` makes the robot
   *detect* approaches/waves (events land in `events.jsonl`), but nothing reacts until
   `alert_engine` is running. Both of us missed this on the first try.
3. **One session only** — the daemon and the official Control app can't both hold the
   robot. Stop one before the other. If `serve` can't connect, check nothing else owns it.
