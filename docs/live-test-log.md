# Live test log

Running record of **on-robot (live) tests** — what we ran, what held up, what didn't.
Newest first. Live tests run on m1max + the real robot (dev machine is plumbing only).

Each entry uses three buckets:
- 🟢 **Good** — worked, met expectations.
- 🟡 **Ugly** — acceptable but needs refining (works after a workaround/fix, or rough edges).
- 🔴 **Bad** — clear issue, fails expectation; needs a fix or a decision.

---

## 2026-06-07 — wave detection LIVE-validated; recording/persistence hardened; greet/goodbye FP confirmed on record; Phase C auth pinned

**Setup:** m1max daemon (`serve --perception --gestures`), vision + stream on, alert engine.
Several restart cycles. Recording switched to `.mkv`. Head re-leveled each restart.

### 🟢 Good
- **Wave detection (Feature 2) works end-to-end live.** MediaPipe **Gesture Recognizer**
  (`Open_Palm`) → debounced `wave` event → alert engine (`wave → wave_back`) → robot says
  **"Hi there!"** (deliberately distinct from the greet). Detected at score **0.61–0.73**.
  New `gesture.py` + `perception --gestures` + `wave_back` command; mediapipe installed on
  m1max without breaking the RF-DETR/supervision stack (numpy 2 kept).
- **Recording persistence hardened (A/B):** `daemon.stop()` now finalizes record+capture, and
  the daemon writes a **durable** log to `artifacts/logs/` (not `/tmp`). Verified: stop *while
  recording* → 581-frame clip **READABLE** (pre-fix it was left corrupt).
- **`.mkv` recording (crash-resilient):** container mp4→**mkv**, same `mp4v` codec & size. A
  hard kill / battery-off now keeps footage up to the crash (mp4 = total loss — no `moov`).
  Proven empirically (truncation-survival test); per-frame extraction identical to mp4.
- **Head-stable greet/goodbye:** removed `look("center")` from `_express` — reactions no longer
  move the head, so the camera (which rides on the head) keeps a level frame.
- **First real eval dataset:** `video-153822.mkv` (1191 frames) + aligned `capture-153822.jsonl`
  — **4 approach / 4 depart / 11 wave**, with the misfires below baked in.
- **Phase C auth pinned + dev workaround works:** `claude -p` over plain SSH fails (keychain),
  but a GUI-rooted **tmux `claude-test`** runs it authenticated (verified `OK`/exit 0).

### 🟡 Ugly (acceptable / needs refining)
- **Wave needs a minimum distance** — MediaPipe needs the hand a min size in-frame; a wave from
  across the room won't register (scores hovered near the 0.5 floor). Characterize the working
  range / lower the threshold later.
- **Head roll mis-calibration (~8°)** — commanding "level" (roll 0) physically sits ~8° tilted.
  It's a *robot calibration offset*, not our code (motors fine, head responds; commanding
  roll ≈ **−5.7°** levels it). Matters because the camera is head-mounted (tilts every frame).
  Proper fix = recalibrate; **deferred**. `reset` currently commands true-zero, so it re-tilts.
- **New voice lines:** greet "Welcome to Acu Genie!", goodbye "Goodbye! Have a nice day!",
  wave "Hi there!".

### 🔴 Bad (clear issue)
- **Greet + goodbye misfire on a stationary, *interacting* person — confirmed on record.**
  During the wave test (same person, id=1, standing + waving):
  - false **approach** (area grew to 0.385 as you stepped in → "Welcome to Acu Genie!"),
  - false **depart** (area dropped to 0.284 ≈ 0.6 × peak from a **pose change / arm-raise**
    narrowing the box → "Goodbye!") — **you never left.**
  Same over-sensitive visit logic as the sitting-fidget FP. User: greet/goodbye were "messy —
  misfire *and* non-fire both happened." This is the priority correctness issue.
- The yesterday "fire-then-no-fire" bug wasn't re-diagnosed in isolation today — but we now have
  the **eval framework + real datasets** to dissect it with data instead of guessing.

### Next — the eval framework (agreed; see `plan-reception.md` → Testing strategy)
record → annotate → **auto-label (model proposes, human verifies)** → `score` → iterate. Build
`score` + auto-`label`; run on `video-153822.mkv`. Fix candidates (validate via the framework,
don't ship blind): **interaction gate** (suppress greet/goodbye while waving), **depart
robustness** (larger/sustained recession), **DetectionsSmoother**.

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

---

## 2026-06-06 (evening) — OPEN BUG: greet/goodbye "fire then no-fire"

**Symptom (corrected by the user):** greet + goodbye fire correctly for a stretch, then
**stop firing entirely** within the same session ("fired, then no fire"). Recurs. Last
real triggers were 15:20:01 in `events.jsonl`, then nothing for >1h. The **live stream
stayed up the whole time — video was NOT dead.**

### 🔴 Bad — the bug (unresolved; collect data tomorrow)
- `events.jsonl` *stops getting new events* → the **tracker stops emitting**, not the
  alert engine. In `approach.py`, greet/goodbye each fire **once per "visit,"** and a visit
  only re-arms after the person is **absent ≥ `reset_absent` (40 frames / 8s)**.
- **Theory (to confirm, NOT asserted):** if anything keeps a detection alive ≥
  `present_frac` (0.03) — a lingering person, a false-positive, or the head pointed at a
  mis-detected object — the visit **never resets**, both latches stay stuck, nothing fires
  again.

### 🟡 My process failures this session (do not repeat)
- **Misread `video_ready:false` as "video dead."** It's a single `try_pull_sample(20ms)`
  that *consumes* a frame from the GStreamer appsink → returns None routinely (between
  frames, or when the vision thread just popped it). NOT an aliveness check; the live
  stream proved frames were flowing.
- **Overwrote the logs** by `rm`-ing `/tmp/reception_live.log` on every restart → no trace
  left to diagnose. **Fix: restart with a timestamped log file, never `rm`.**

### Instrumented (loads on next restart)
- `approach.py` now logs `visit RESET …` on each re-arm + a throttled
  `visit: dom=… absent=… greet=… depart=…` every ~5s. The failure will show whether the
  visit is stuck (greet/depart=True while `absent` never climbs = something pinning a
  detection ≥ present_frac).

### Plan for tomorrow (user present)
1. Restart with a **timestamped log** + `capture on` (and A/B the stream on vs off per the
   user's suspicion).
2. Run cycles until "fire then no-fire" reproduces.
3. Read the `visit:` trace at the failure point → confirm/refute the stuck-latch theory and
   find *what* is pinning the detection. Then fix (candidates: re-arm on leave+return,
   raise `present_frac`, or a max-visit timeout) — validated on a recorded clip first.

> A stateless rewrite was attempted + then **rolled back** — it was a fix on an *unconfirmed*
> root cause and it overwrote the diagnostics. Code stays on the instrumented visit-based
> version until tomorrow's trace confirms the actual cause.

### Code review (no code changes) — two compounding bugs to verify with data

**Bug A — a walk-away can fire a phantom GREET, then goodbye.** Leaving the desk means
stepping back INTO frame from the close blind spot, so the box *grows* (more of the body
becomes visible) before it shrinks. The greet test is `area / visit_min ≥ growth_factor`,
and `visit_min` is the *smallest area this visit* = the tiny step-into-frame sliver. So
"grew 2.5×" isn't approaching — it's just becoming visible → false greet; then the recede
fires goodbye. (Also: `visit_min` is permanently poisoned by ANY single small/partial/noisy
detection.) The 3 recorded walk-away clips don't repro it — their step-in growth was ~1.23×
(< the 1.3× threshold); the live one started closer to the desk → bigger grow-in → crossed it.
- **Data check:** record a walk-away that STARTS right at the desk (through the blind spot);
  replay it — does it emit a spurious `approach`?

**Bug B — "no fire after" = the visit never resets** (separate bug; A just burns both latches
at once). A greet+goodbye sets BOTH `_greet_fired` and `_depart_fired`. They re-arm only via
`_reset_visit()`, called only when `_absent ≥ reset_absent` (40 frames = **8s of CONTINUOUS
no-detection**; any detected frame resets `_absent` to 0). So after the combo, nothing fires
again until 8s of clean absence. Two ways that never happens (need the trace to tell which):
  1. **cadence** — up/away/up/away keeps someone intermittently detected, so `_absent` never
     accumulates 8s.
  2. **phantom detection ≥ present_frac (0.03)** — e.g. greet/goodbye `look("center")` leaving
     the head on an object RF-DETR misreads as a person → `_absent` pinned at 0 → permanent lockup.
- **Data check:** read the instrumented `visit: … absent=N …` trace at the no-fire point.
  `absent` climbing toward 40 → it's #1 (cadence). `absent` stuck at 0 while you're gone → it's
  #2 (phantom — also watch the live stream for where the head/camera is pointed).
