# Voice AI Research: Recommended Models & Stacks for Conversational Apps

**Date:** June 2026 (research synthesized from X.com posts and discussions, ~2025–mid-2026)  
**Focus:** Practical solutions for **customer-facing** voice conversation apps, especially **short chats and Q&A**.  
**Relevance to Reachy Mini:** This project currently uses local `faster-whisper` (STT) + `piper-tts` (TTS) with a custom continuous-listen buffer (see `docs/continuous-listen.md`) and WebRTC audio. The robot's reception use case (greeting, FAQ, directions, simple Q&A) is a classic short-form conversational scenario. Findings here can guide upgrades for lower latency, better naturalness, interruption handling, and cost at "production" volume with real humans.

## Two Dominant Architectures

1. **STT → LLM → TTS ("the sandwich")**  
   - Most flexible and model-agnostic.  
   - Easy to extend existing text agents/RAG/tools.  
   - Pain points: Managing streams, partial transcripts, latency at every hop, interruptions, state.  
   - Best when you need strong tool use, RAG, guardrails, or traceability (very relevant for clinic reception Q&A).

2. **Realtime / Speech-to-Speech / Full-duplex**  
   - Native audio models that listen while speaking.  
   - Superior natural turn-taking and lower perceived latency.  
   - Trade-offs: Often black-box, harder to insert tools/RAG reliably, variable instruction-following.

For short customer QnA the sandwich is frequently favored; full-duplex wins on conversational flow.

## Key Component Recommendations (from real builder discussions)

### TTS (time-to-first-audio is critical for "feels human")
- **Cartesia (Sonic series, e.g. Sonic-2 / Sonic-3.5)**: Dominant for realtime conversational use. Sub-150–200 ms (some claims ~40 ms), excellent naturalness + emotional range/prosody, multilingual (including Indian languages), instant voice cloning (3s audio). Frequently tops independent arenas for quality + speed. Integrated in LiveKit inference. Praised by investors and builders for making AI conversation feel human.
- Strong alternatives: Rime (very low latency), Deepgram Aura, ElevenLabs.
- Open-source/low-resource: Resemble AI **Chatterbox-Turbo** (350M params, sub-200 ms, runs on consumer GPU / CPU / Apple Silicon, native expression tags like `[laugh]`).

### STT (streaming + real-world/phone audio quality matters)
- **Deepgram (Nova-3 / Flux)**: Default pick for many production low-latency streaming agents.
- **AssemblyAI (Universal-3 / Voice Agent API)**: Strong on accuracy for phone/real audio, especially entities and alphanumerics (critical for QnA like account numbers, addresses, names). Used in robust LangChain + Cartesia demos. Lower missed-entity rates than some competitors on phone audio.
- Others: Cartesia Ink-2 (low latency + good WER), local `faster-whisper` variants (current Reachy baseline), NVIDIA Nemotron open multilingual streaming ASR.
- Real-world note: Accuracy on 8 kHz telephony, accents, noise, and overlapping speech is more important than lab WER for customer apps.

### LLM (for the middle of the sandwich)
- Prioritize **speed** (TTFT) over size for voice.
- Frequently praised: Gemini variants (latency, cost, native audio capabilities, strong phrase endpointing/turn detection).
- Cheap/fast options: Groq-powered models, DeepSeek (used in some pipelines for cost).
- OpenAI GPT-4o / 4o-mini: Powerful but audio pricing can be brutal.

### Full End-to-End / Full-Duplex Models
- **NVIDIA PersonaPlex-7B** (Moshi-based, open weights, MIT license): Major 2026 talking point. One 7B model replaces the entire ASR→LLM→TTS pipeline. Full-duplex (listens while speaking, no artificial turn-taking pauses). Reported ~170 ms turn-taking, 240 ms interruption handling. Higher dialog naturalness than Gemini in their evals. Runs on a single A100. Huge potential for self-hosted cost control vs per-minute APIs.
  - Caveats from practitioners: Very fluid but can be "stupid" on complex instructions or domain knowledge without fine-tuning; English-primary initially; better as a natural conversation layer than a drop-in replacement for precise tool-using QnA agents.
- OpenAI Realtime API: Technically impressive (tools + voice-to-voice) but repeatedly called out as **very expensive** (~$0.06/min input + $0.24/min output → ~$18/hour). Risky for customer-facing volume.
- Gemini native audio / Live: Positive mentions for latency and cost in agent loops.

## Popular Frameworks & Infra (the real "stack" for production)

- **LiveKit Agents** (open source + cloud): Mature, production-proven (enterprise examples). Excellent telephony (Twilio, SIP), open turn-detection model (13 langs, <25 ms CPU, better than pure VAD for natural flow), multi-agent orchestration, edge deployment, stateful load balancing. Strong for real-time + video. High activity. Cartesia integration highlighted.
- **Pipecat (+ Pipecat Cloud)**: Pipeline-oriented, clean Python developer experience. Easy modular STT/LLM/TTS swaps. "Heroku for voice agents" (push Docker). Good "Smart Turn" models + multi-layer turn detection (VAD + native audio model + LLM context + single-token tagging). Builders report clean DX for getting a voice agent running quickly.
- Comparison notes: LiveKit is tighter with rooms/media servers and scales well for telephony/concurrency. Pipecat is more flexible transport-wise and voice-pipeline focused. Many people compose them.

**Managed platforms** (fastest path to real customer use):
- Vapi: Frequently cited for actual phone agents in production (e.g., small businesses replacing admins for call answering/booking). Telephony-first, scaling stories. xAI/Grok voice partnership mentions.
- Bland: High-volume phone agents, "sounds human," millions of calls.
- PolyAI: Praised for emotionally intelligent phone handling (long pauses, not rushing, good with real people in hospitality/casinos).
- Others: Retell, plus the voice agent APIs from Deepgram/AssemblyAI.

**Volume/cost heuristic** (from builders): Under ~10k minutes/month → managed platforms win on speed/reliability. Over ~50k minutes/month → self-host or hybrid can save dramatically (up to 80%).

## Critical Practical Challenges (especially for short QnA)

- **Latency is everything**: Target sub-700 ms end-to-end (some aim tighter). STT latency directly eats "time to first word." >1 s and users talk over the agent. TTS speed (Cartesia) is a major lever. One builder example: Whisper ~80 ms + LLM + TTS ≈ 250 ms round-trip on local GPU.
- **Turn-taking & interruptions**: The #1 complaint about voice AI. Pure VAD is not enough.
  - Solutions: Specialized small audio turn models (LiveKit, Pipecat Smart Turn), LLM-native audio endpointing (Gemini strong and sometimes cheaper), multi-layer stacks (VAD short trigger + audio model + LLM prompt context with fast single-token decisions), or inherent full-duplex models.
  - Advanced tricks: In-context adjustment ("user is about to give a phone number — wait longer").
- **The full pipeline > any single model**: VAD + streaming STT + LLM orchestration (tools, memory, RAG) + TTS + interruption logic + guardrails + clean human handoff + logging/observability + audio enhancement for real environments.
- **Real audio & accuracy**: Phone quality, noise, accents, entities. Test with *your* audio. AssemblyAI often wins on phone-specific metrics.
- **Cost at scale**: Pure per-minute voice APIs (especially OpenAI Realtime) are unsustainable for high volume. Self-host (PersonaPlex + local STT/TTS or Whisper + fast LLM + Cartesia) or smart hybrids (voice only at the edges, thinking on cheap text LLM) win.
- **Customer-facing specifics (reception/QnA)**: Strong system prompts + RAG/knowledge base + tools (lookup, calendar, etc.). Deterministic guardrails + graceful "I don't know, let me get a human" handoff from day one. Observability and call-level visibility are launch requirements, not nice-to-haves. Prompt engineering + lots of testing for natural tone.

## Example Stacks Mentioned in Production Contexts

- LangChain demo: AssemblyAI (STT) + Cartesia (TTS) + LLM orchestration for a robust multimodal agent.
- Builder pipelines: Deepgram + fast LLM (sometimes DeepSeek) + Cartesia/Resemble.
- High-volume self-host direction: LiveKit or Pipecat + Cartesia (or open TTS) + Deepgram/AssemblyAI or local ASR + fast LLM + dedicated turn model. Or PersonaPlex for the voice layer.
- One hybrid pattern: "Voice only touches the edges. The thinking stays cheap (text rates)."

## Current Reachy Mini Stack vs. Research Opportunities

- **Today**: Local faster-whisper + piper-tts + continuous raw-audio buffer (no VAD yet) + Claude brain (via session server tools). Works for polled listening + speak + motion.
- **Opportunities highlighted by this research**:
  - Drop-in better TTS (Cartesia) for dramatically lower latency and naturalness.
  - Stronger streaming STT options or hybrid (keep local for privacy/low cost, add cloud for accuracy on hard audio).
  - Proper turn detection / VAD / streaming STT (currently a noted future item in continuous-listen.md).
  - Framework (LiveKit/Pipecat) if moving toward more autonomous daemon conversation loops.
  - Evaluation of PersonaPlex-style full-duplex for natural short interactions (with care around tool use / grounding).
  - Cost/latency numbers to set budgets against the current local baseline.
  - Guardrails + handoff patterns for unattended reception use.

## Sources & Further Reading (X posts)

Key discussions drawn from semantic and keyword searches on X (handles including @livekit, @kwindla / Pipecat, @LangChain, @saranormous, @aakashgupta, individual builders, etc.). Specific high-signal posts covered:
- Architecture trade-offs and Cartesia + AssemblyAI demos (LangChain).
- PersonaPlex-7B announcement and cost implications (NVIDIA / Hugging Face ecosystem posts + analysis).
- Turn detection deep dives (Pipecat + Gemini endpointing, LiveKit open turn model).
- Latency budgets, production war stories, and volume-based build-vs-buy decisions.
- TTS arena leadership and real measured round-trips.

The space moves extremely fast — new Sonic releases, open ASR models, framework updates, and pricing changes appear regularly. Always re-benchmark with actual robot audio (mic array + room acoustics + speaker playback) and real user interactions.

---

**Next steps suggestion for Reachy Mini** (not part of original query):  
Pick 1–2 concrete experiments (e.g., swap TTS to Cartesia via LiveKit or direct, add a proper turn model, or prototype a Pipecat/LiveKit-based voice loop) and measure against the current whisper+piper baseline on the actual hardware. Update `docs/continuous-listen.md`, `docs/plan-reception.md`, and this file with results.

*Research persisted from Grok X.com queries on 2026-06.*