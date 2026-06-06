# Reachy Mini — Clinic Reception Robot Plan

> A standalone, always-on "reception guy" for a clinic front desk:
> toggleable vision (always-on monitoring + alerts) and toggleable voice
> (human-controlled conversation). This document is the design of record for
> that build. It supersedes the generic Phase 5–7 sketch in `plan.md` for
> the reception use case.

## Use case

The robot sits at a clinic front desk and does two **independent**, separately
toggled jobs:

- **Vision (toggle on/off):** when on, it continuously watches the scene,
  identifies people/objects, and a *separate* process decides whether to raise
  an alert (someone arrived, an unattended item, someone waiting too long…).
- **Voice (toggle on/off):** when on, a human switches it into conversation
  mode and it talks to real people — greeting, answering questions, directions,
  FAQ — using an agentic LLM as its brain, with expressive motion.

Both can be on at once (sees *and* talks) or independently.

---

## The core reframe

Phases 1–4 of this project were built on one assumption: **Claude Code is the
brain, with a human (or a spawned `cla` agent) in the loop.** The CLIs are
fire-and-forget — connect, act, exit — and the intelligence is a developer
typing, or a short-lived agent reacting to a trigger.

A reception robot is the opposite: **always-on, unattended, no developer at the
keyboard.** That flips the architecture from *"Claude Code drives the robot"* to
*"a resident daemon owns the robot and embeds the intelligence in its own
loop."*

The project was built in two halves, and only the top half changes:

- **Bottom half (hardware abstraction)** — `robot.py`, `session.py`, WebRTC
  capture, STT/TTS — was deliberately built SDK-independent and persistent. This
  is exactly the substrate an always-on daemon needs. **Heavy reuse.**
- **Top half (the brain)** — Claude-Code-as-driver and the `cla`-spawn meeting
  trigger — does not transfer (too slow: ~15–18s cold start; too costly:
  ~$0.13/turn; depends on the dev tool being present). **Replaced** by a
  standalone controller with an agentic LLM brain.

---

## Decisions (resolved)

| Decision | Choice |
|----------|--------|
| **Brain runtime** | Standalone resident daemon + an **agentic** LLM layer (not bare stateless API calls). Conversation needs durable state + tool use across turns. Target: Claude Agent SDK embedded in the daemon. |
| **Vision processing** | Tiered, **fully local**: a cheap detector runs continuously as a tripwire; escalates to a **small local VLM** on meaningful events. |
| **Compute host** | **Mac stays tethered.** Robot on the RPi 5; daemon, models, and brain on the Mac over the LAN. Nothing to port for now. |
| **Privacy posture** | Raw audio/video **stays local**. Only **text** (transcripts, scene descriptions) crosses to the cloud. The local VLM choice keeps frames in the building. |
| **Robot ↔ Mac network** | **Same LAN, deliberately** (see Networking). Tailscale is for remote *control* of the daemon, not for the media path. |

---

## Two "sessions" — do not conflate

The daemon must own **two** long-lived things. The existing code solves only one:

1. **Hardware session** — the live `ReachyMini()` / WebRTC connection (camera,
   mic, speaker). ✅ Already solved by `session.py` (one instance, channels kept
   warm, continuous-listen buffer, thread-safe audio push).
2. **Conversation session** — the agent's running message history + tool state,
   so turn 5 remembers turn 1 and can call "look up appointment." ❌ Does not
   exist yet. This is the "agentic AI api." A bare LLM endpoint is stateless and
   cannot *be* a conversation; the agent layer holds this.

The reception daemon owns both.

---

## Architecture (tethered Mac)

```
        Reachy Mini (RPi 5) ── camera · mic · speaker · motors
              ↕ WiFi  (REST :8000  +  WebRTC :8443)   [same LAN]
 ┌──────────────────── Mac (always on) ─────────────────────┐
 │ Reception daemon  ── owns both sessions, runs the loops   │
 │  ├─ Hardware session  (one ReachyMini(), WebRTC)   [reuse]│
 │  ├─ Control plane     (vision on/off · voice on/off FSM)  │
 │  │                                                        │
 │  ├─ VISION (when on)                                      │
 │  │    frames → local detector (continuous tripwire)       │
 │  │          → on event → local small VLM → events         │
 │  │    events ─▶ [separate] Alert engine → notify          │
 │  │                                                        │
 │  └─ VOICE (when on)                                       │
 │       listen → local STT → Agent (persistent convo        │
 │           + tools + scene text) → local TTS → speak       │
 │           + expressive motion                             │
 └───────────────────────────────────────────────────────────┘
              ↕  text only (transcripts, scene descriptions)
                          Claude API
```

Perception and alerting are **separate processes**: perception emits structured
events; the alert engine consumes them and owns the alert policy. Perception
does not know the policy.

---

## Networking — does the robot need the same WiFi as the Mac?

Two transports, two answers:

- **REST / motors / state (port 8000)** — *not* bound to same WiFi. `robot.py`
  reads `REACHY_HOST` (default `reachy-mini.local`, an **mDNS** name that only
  resolves on the same LAN). Point it at a raw IP / Tailscale name and REST works
  over anything routable.
- **Camera / mic / speaker — WebRTC (port 8443)** — *effectively* same-LAN
  today. Every `ReachyMini()` is constructed with **no hostname argument**
  (SDK default discovery), and WebRTC negotiates media over ICE host candidates
  that assume direct reachability. Off-LAN traversal is unconfigured and the SDK
  host isn't parameterized in our code.

**For this build:** keep robot and Mac on the **same LAN/subnet**. It's the
reliable, low-latency, all-local path — and it matches the "media stays local"
privacy choice.

- **Gotcha:** AP client isolation / guest WiFi blocks device-to-device traffic
  and breaks both mDNS and WebRTC. Use a non-isolated network/VLAN. (Mac on
  Ethernet + robot on WiFi of the *same router* = same subnet = fine.)
- **Tailscale:** great for **remote control** of the daemon (toggle vision/voice
  from a phone over the tailnet). Do **not** route the robot↔Mac media over it.

---

## Reuse map

| Existing | For the reception robot |
|----------|--------------------------|
| `robot.py` (REST, motors, lifecycle) | ✅ Keep as-is — the motor/daemon layer |
| `session.py` (live `ReachyMini()`, continuous listen, socket server, thread-safe audio) | ✅ The backbone — the hardware-session primitive |
| `vision.py` (WebRTC frame capture) | ✅ Keep the *capture*; new *processing* on top |
| `stt.py` / `tts.py` / `audio.py` | ✅ Reuse listen/speak; reconsider STT model (the "Reachy" mishear issue) |
| `motion.py` (look/nod/scan) | ✅ Reuse as a behavior library (greet, track speaker, idle scan) |
| `transcribe.py` (bg loop + trigger + `cla` dispatch) | ⚠️ Reuse the *pattern* (background perception loop); retire `cla` dispatch |
| Claude-Code-as-brain | ❌ Replaced by the standalone daemon + agentic LLM |

---

## Build plan

Each phase is independently demoable. B and C share only the daemon from A, so
they can proceed in parallel once A lands.

- **A — Reception daemon + control plane.** ✅ **DONE — validated on hardware
  2026-06-04** (see "Phase A — result" below). Promote the session into a resident
  daemon with two independent toggles as a state machine. Pure plumbing &
  lifecycle; replaces "Claude Code drives it." *(Detailed below.)*
- **B — Vision pipeline + alerting (two processes).** ✅ **code-complete; mock +
  real-video validated, live test pending** (see "Phase B — result"). Perception:
  frame → RF-DETR Nano person detector → ByteTrack + approach geometry → events.
  A **separate** alert engine consumes the events and tells the robot to greet.
- **C — Voice brain (standalone).** ✅ **code-complete; mock + text validated, live
  test pending** (see "Phase C — result"). Continuous listen → STT → `claude -p`
  agent (receptionist persona + session memory) → TTS → speak. Robot-expression
  tools + an authoritative FAQ tool deferred.
- **D — Fuse.** Vision events feed the voice brain (greet on approach,
  "you've been waiting — someone's coming").
- **E — Productionize.** Auto-restart, logging, privacy handling, monitoring,
  optional Tailscale control endpoint.

---

## Phase A — detailed

**Goal:** one resident process that owns the live hardware session and exposes
two independent toggles. No intelligence yet — prove the on/off plumbing and
lifecycle that B and C stand on.

**Layering** (keeps the architecture split):

- `session.py` stays the **hardware/transport primitive** — reused as-is.
- **New `reception.py`** = the **application control plane**. Holds a `Session`
  in-process and supervises two worker loops gated by toggles.

**Components:**

1. **`ReceptionDaemon`** — owns one `Session` + two flags (`vision_on`,
   `voice_on`) as an explicit state machine. Each toggle starts/stops **only its
   own** worker thread; never touches the other or tears down the shared session.
2. **Vision worker (stub in A)** — when `vision_on`, grab a frame every N s and
   log `frame ok WxH`. Detector/VLM come in Phase B.
3. **Voice worker (stub in A)** — when `voice_on`, drive the continuous-listen
   buffer and log a "listening tick." STT/brain come in Phase C.
4. **Control surface** — local Unix-socket + CLI extending the existing
   `serve`/`call` pattern: `status`, `vision on|off`, `voice on|off`,
   `shutdown`. (Network/Tailscale control endpoint deferred to E.)

**The risk Phase A exists to validate:** two loops sharing **one** WebRTC
session — grabbing camera frames and mic audio **concurrently** off a single
`ReachyMini()`. Continuous-listen already proves audio-while-busy; the new
combination is frame-grab + audio-grab at the same time. If stable, B and C are
unblocked.

**Deliverables:**

- `src/reachy_mini_brain/reception.py` (daemon + state machine + stub workers +
  control CLI)
- Socket-protocol extension for the toggle/status commands

**Test (run on the robot — hardware):**

1. `reception serve` → daemon up, both toggles off, session warm.
2. `vision on` → frame-ok logs; `voice on` → both ticking together (concurrency
   check).
3. `vision off` while voice stays on, and vice-versa → each stops independently,
   session stays alive.
4. `shutdown` → clean teardown (`media_manager.close()` + `disconnect()`).

---

## Decisions — resolved & still open

**Resolved:**
- **Local detector** → **RF-DETR Nano** (Apache-2.0; chosen over AGPL-licensed YOLO).
- **Alert channel** → **robot reacts on-device** (look + antenna flick + speak).
- **Agentic brain** → **`claude -p` headless agent, Haiku** (a real agent with
  built-in session management + Claude Code auth; over a raw Messages loop).
- **Tiering** → cheap detector continuous, VLM-on-event later; MVP is detector-only
  ("someone approaching" → greet).

**Still open:**
- **Local VLM** (tier-2 scene description) — small VLM (Florence-2 / Moondream) on
  events; not needed for the MVP. Phase B+.
- **FAQ knowledge** — currently facts-in-persona (Haiku drifts); add an authoritative
  FAQ tool. Phase C polish.
- **STT reliability** — still faster-whisper; the "Reachy" mishear issue stands.
- **Remote control** — Tailscale-exposed control endpoint for staff. Phase E.

---

## Phase A — result (validated on hardware, 2026-06-04)

Ran from the **local dev machine** (same LAN as the robot at `192.168.1.165`,
`REACHY_HOST` pinned to the IP). All green:

- **Lifecycle** — daemon starts, WebRTC warms up (~60s), `reception daemon ready`.
- **REST sanity** — `state get-state` returns live robot state.
- **vision** — `vision on` → real 1080p frames (`frame ok (1080,1920,3)`) every 2s.
- **voice** — `voice on` → mic captured 5s and STT transcribed it (`voice: heard 5.0s: '…'`).
- **Concurrency (the core risk)** — frames + mic reads ran simultaneously off ONE
  healthy WebRTC session, no conflict/crash. **Cleared.**
- **Independent toggles** — `vision off` while voice stayed on, then `voice off`.
- **Clean shutdown** — workers stopped, session closed, socket removed, exited ~1s.

Testing moved to the **local dev machine** (not m1max): same LAN, direct, no ssh.
m1max sleeping mid-test once dropped the WebRTC session (benign, but a lesson).

## Follow-ups discovered

- **Brain must not sleep** — when m1max slept, the session dropped (Phase A has no
  reconnect; that's Phase E). For deployment, disable sleep (`pmset`/`caffeinate`).
- **Use `REACHY_HOST=<ip>`** — mDNS `reachy-mini.local` was flaky on the LAN; the IP
  is reliable. (The SDK's `ReachyMini()` still resolves `reachy-mini.local` internally
  — worked here, but worth parameterizing if it flakes.) See [[robot-connectivity]].
- **Robot IP is DHCP** (`192.168.1.165`) — set a router reservation for stability.
- **Daemon discards audio** — add a `--save-wav`/debug-dump to the voice worker so we
  can audit what the mic hears (test transcription was garbled — couldn't inspect).
- **Camera defaults to `ReachyMiniLiteCamSpecs`** (we're Wireless) — revisit in the
  resolution/spec pass; frames are fine at 1080p.
- **GStreamer dylib-lookup noise** in the log (`libpython3.12.dylib … no such file`) —
  cosmetic; video came up fine. Tidy later.

---

## Phase B — result (code-complete; mock + real-video validated, 2026-06-05)

Tiered, fully local. On-robot live test pending.
- `detector.py` — RF-DETR Nano person detection. ~40-65ms/frame warm; the confidence
  threshold filters weak partials (0.5 dropped a low-confidence hand).
- `approach.py` — supervision **ByteTrack** + approach-vs-transit geometry (box-area
  growth + dwell, latched per track). Synthetic approacher fires; passer-by doesn't.
- `perception.py` — full pipeline → `artifacts/events.jsonl`. Whole stack ran on
  `reachy_video.mp4` (150 frames, 1080p) at **~23 fps**, no errors.
- `alert_engine.py` — separate process: tails the event log, applies the arrival rule
  + cooldown, sends `react` to the daemon. Mock loop: approach event → robot greets.
- `reception.py` — `serve --perception` runs the pipeline in the vision worker; a
  `react` command makes the robot greet (look + antenna flick + speak).

## Phase C — result (code-complete; mock + text validated, 2026-06-05)

`claude -p` agent, Haiku. On-robot live test pending.
- `brain.py` — `ReceptionBrain`: `claude -p --model haiku` with a receptionist persona.
  Clean-receptionist recipe: `--system-prompt <persona>` + `--exclude-dynamic-system-prompt-sections`
  (every turn) + `--tools ""` + a neutral cwd + an anchored persona — these fixed the
  coding-agent bleed. Continuity = capture `session_id` on turn 1, `--resume` after.
- **Conversation boundary** = idle window (`conversation_timeout`, default 120s): a
  longer silence starts a fresh session ("new visitor").
- Wired into the voice worker via `serve --brain`: `listen → brain.respond() → speak`.
  Mock full-loop validated. ~2-4s, ~$0.01 per turn (Haiku, cached).
- **TTS** = Piper, default voice `en_US-lessac-medium`. **STT** = faster-whisper.

## Combined live test (pending — needs the robot)

On the brain machine, robot up + same LAN, camera at the room:
`serve --perception --brain` + `alert_engine`; `vision on`, `voice on`; walk up →
robot greets (B); talk → it converses (C). Prereq on the brain machine:
`uv pip install -e ".[vision]"` (rfdetr + supervision) and the `claude` CLI authed.
