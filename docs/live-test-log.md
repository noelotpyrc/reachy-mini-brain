# Live test log

Running record of **on-robot (live) tests** — what we ran, what held up, what didn't.
Newest first. Live tests run on m1max + the real robot (dev machine is plumbing only).

Each entry uses three buckets:
- 🟢 **Good** — worked, met expectations.
- 🟡 **Ugly** — acceptable but needs refining (works after a workaround/fix, or rough edges).
- 🔴 **Bad** — clear issue, fails expectation; needs a fix or a decision.

---

## 2026-06-06 — Phase B: vision → approach → greet (m1max + real robot)

**Setup:** m1max drives the daemon over SSH. `serve --perception --vision-interval 1`
+ `alert_engine`; robot on WiFi (192.168.1.165); m1max SDK aligned to robot daemon
**1.5.0**. Audio = piper TTS streamed to the robot speaker over WebRTC.

**What we did:** empty-room baseline → stand in view (presence) → walk up (approach) →
robot greets. Iterated hard on audio quality. Added a `capture on/off` debug recorder
and an A/B (vision on vs off) on the audio.

### 🟢 Good
- **Phase B works end-to-end on hardware:** real camera → RF-DETR person detect →
  ByteTrack → approach geometry → `events.jsonl` → alert engine → robot greets
  (look + antenna flick + speak).
- **Presence vs approach holds live:** standing still in view = detected but **no** greet;
  walking up = greet.
- **Clean detection:** zero false positives in an empty room; reliable 1-person detection.
- **Version alignment:** m1max SDK → 1.5.0 matched the robot; audio/video warm up healthy.
- **Daemon control plane solid:** serve / vision on·off / react / capture / shutdown all
  reliable on hardware.

### 🟡 Ugly (acceptable, needs refining)
- **Approach fires on the 2nd walk-up, not the 1st** ("twice"). Cause: growth measured
  from *first-seen* + ByteTrack keeping the track id alive across a step-out → stale
  baseline. **Fix applied** (baseline = track's farthest point / min-area; `growth_factor`
  1.6→1.3, `min_dwell` 5→3) — **needs one clean re-test.**
- **`react`'s `look("center")` re-aims the head**, undoing a head-tilt setup → camera
  loses the approach area. **Workaround:** orient the robot *body* at the path so
  center = path. Refine: configurable home-look, or don't re-center on greet.
- **Audio buffering was fragile** — start-clip ("he" of "Hello") and tail-drop
  ("…someone will") both happened and were **fixed** (0.3 s silence lead-in; prime+pace
  cushion ~1.0 s). Took many iterations; the "pause vision during speak" guard we added
  turned out to treat a **non-cause** (see Bad #1).
- **Version matched by downgrading m1max** to 1.5.0; the cleaner direction (upgrade the
  *robot* to latest via the Control app, then re-match the SDK) is deferred.

### 🔴 Bad (clear issue / failed expectation)
1. **Spoken greeting intermittently choppy / not continuous.** OPEN.
   - **A/B proved it's vision-independent** (vision on vs off: no difference) — so the
     "pause vision while speaking" guard is not the fix.
   - Measured WiFi jitter m1max→robot: 0 % loss but **3.8–37.6 ms, σ≈9.6 ms**.
   - The pipeline **buffers** our audio (over-pushing overflowed it → the tail-drop), so
     send-side pacing isn't the bottleneck → **leading suspect: the WebRTC-over-WiFi
     stream underrunning the robot's receive buffer.**
   - **Correct next isolation test (NOT yet done):** SSH to the robot
     (`ssh pollen@reachy-mini.local`, pw `root`) and play a WAV on the **robot's own
     audio device** (`aplay`), bypassing WebRTC. Smooth there → it's the stream/link;
     choppy there → the robot's audio device. *(The earlier "play on m1max" idea was
     useless — m1max's audio path was never in question.)*
2. **Phase C (voice brain) not tested at all** — blocked: m1max has no `claude` binary
   (no Node). Decision pending: install Claude Code + auth on m1max, **or** switch the
   brain to the Anthropic API + key.

### Carried into backlog
- **Separate-process architecture for perception** (own OS process fed frames via shared
  memory → `events.jsonl`; removes GIL/CPU contention by design, vision never pauses).
  Recorded, not done.
- Landed this session: `reception capture on/off` + per-frame approach debug
  (`area / min_area / growth / dwell / near / approaching / fired`); `reception`
  console-script entry point; `[vision]` pyproject extra.

### Update — same session, after fixing the capture tool
- 🟢 **"Twice" confirmed resolved** — repeated far→near runs detect approaches reliably
  (e.g. capture `092447`: a stationary person at area ~0.20 correctly never fired, while
  the approacher grew 0.018→0.063 and fired on crossing near). Capture tool's `float32`
  JSON-serialization bug found + fixed + verified.
- 🟡 **Greet timing feels slow** — you have to get close before it starts. Fires at ~6 %
  of frame but the react latency (look + TTS) makes it feel late. One-knob tune
  (`min_area_frac` and/or react latency).
- 🔴 **Sitting-fidget false positive** — a stationary person moving hands/head can trip
  the min-area-baseline growth (box wobble reads as "approach"). The min-baseline that
  fixed "twice" is the cause; needs a *sustained/smoothed* growth signal (or VLM later).
- 🔴 **Audio choppy persists even on manual `reception react`** — reconfirmed
  vision-independent. The robot-local `aplay` isolation is still the pending diagnostic.

### Method takeaway → new testing strategy
We spent this whole session hand-tuning approach logic *on the robot in real time*. Going
forward: **Stage 1 semi-live (video-driven) → Stage 2 live** — see the Testing-strategy
section in `plan-reception.md`. The false-positive and threshold cases above become
labelled scenario clips so they're reproducible and regression-tested off-robot.

---

## 2026-06-06 (semi-live, video harness) — Feature 1: departure → "Goodbye"

First real use of the Stage-1 harness (`python -m reachy_mini_brain.replay`). **No robot.**

### 🟢 Good
- **Harness works** — pumps a recorded clip through the real perception pipeline and
  reports approach/depart events. Flags: `--trace` (per-frame stats), `--reverse` (play
  backwards), `--expect-approach/-depart N` (CI asserts).
- **Approach** — `video-094157.mp4` forward → `approach=1, depart=0`.
- **Depart** — same clip `--reverse` (receding) → `approach=0, depart=1` (fired at area
  0.043, below half its peak). Clean separation, and we validated departure with **no
  "leaving" clip and no robot** by reversing an approach clip.
- **Reaction wired** — alert engine maps event type → action (`approach`→`react`,
  `depart`→`farewell` = "Goodbye, have a nice day!"), independent per-type cooldowns;
  daemon gained a `farewell` command + `reception farewell` CLI.

### ⏳ Pending
- New code (farewell + mapping) **not yet loaded** — deliberately did NOT restart the
  daemon (an hours-long user recording was running). Loads on next restart.
- **Live test** of depart→goodbye (walk away → robot says goodbye) needs a person present.
- Record real `leaving` + `waving` clips for the labelled scenario suite when back.

---

## 2026-06-06 (later) — Feature 1 departure: rebuilt id-agnostic + goodbye LIVE-validated

After the per-track depart proved fragile live (track id churns when you turn to leave →
peak reset → fired at the door / not at all), rebuilt departure **id-agnostic**: track the
dominant visitor's area envelope, fire when it drops to ~0.6× the visit peak (2 sustained
visible frames), survive the ~4s close-range **blind spot** (the camera loses you when
you're right at the desk — confirmed: the gap frame is an empty room).

### 🟢 Good
- Tuned + validated **offline** on 3 clean walk-away clips (`134128/146/202`): depart fires
  at 39–48% of peak, **approach never falsely fires** — proving departure is independent of
  walk-up (no walk-up in those clips).
- **Goodbye live-validated** on the robot (goodbye-only, no greet bundled): fires reliably
  on a real walk-away.
- New harness flags landed: `--reverse`, `--from-frame`; alert engine `--types` filter.

### 🟡 Ugly
- Goodbyes are **spaced ~15s** (visit re-arms after ~8s absence + 15s alert cooldown), so
  rapid back-to-back walk-aways get one goodbye. Fine for a desk; a "re-arm on every
  leave+return" refinement would remove the spacing need — needs a multi-cycle clip to
  validate offline first (didn't change it live).

### 🔴 Bad / finding
- **Close-range blind spot**: the camera can't see a person right at the desk (~4s gap).
  OK for greet+goodbye (both happen in view) and for a conversation (audio), but means the
  robot is vision-blind during the close interaction. Camera FOV/angle is the lever.

---

## 2026-06-06 (later still) — gated greet; greet + goodbye both LIVE-validated

Rebuilt the greet to mirror departure — one id-agnostic gated state machine on the
dominant visitor's area envelope:
- **Gate 1** — a new visitor is present (a visit starts).
- **Gate 2** — area rising + grown from entry **and** reached `greet_floor` (0.10), i.e.
  clearly approaching and in the area (not a distant speck). Greet has no stable
  reference like departure's peak, so it needs that one small floor; depart stays
  peak-relative (fires at ≤ `depart_factor` × the visit peak).

### 🟢 Good
- Offline on 4 walk-up + 3 walk-away clips: walk-ups fire **greet only** (~0.11–0.18),
  walk-aways fire **goodbye only** (~half peak). **No cross-firing.**
- **Live: both greet and goodbye work** (walk away → goodbye, walk back in → greet).

### 🟡 Ugly / to tune
- **Voice fires ~0.5–1s late** — a visitor could miss it. Likely the react chain:
  alert-engine poll (0.3s) + `look("center")` goto *before* the speak + TTS synth latency.
  Tuning ideas: **speak first / move concurrently**, faster alert poll, and
  **pre-synthesize** the fixed greeting/goodbye lines. Deferred.
- `greet_floor` (0.10) sets greet timing — one knob, lower = greet sooner/farther.
