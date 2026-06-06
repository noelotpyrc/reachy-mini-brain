"""Voice brain — Phase C: an agentic receptionist powered by ``claude -p``.

Each user utterance goes to a headless Claude Code agent (``claude -p``, Haiku)
running a fixed receptionist persona. Conversation continuity is Claude Code's own
session management: capture the ``session_id`` on turn 1, ``--resume`` it after, so
the agent remembers the whole exchange.

Why claude -p (vs a raw Messages loop): it's a real agent (tool use + sessions) out
of the box, uses Claude Code's own auth (no separate ANTHROPIC_API_KEY), and is the
simplest first pass. Trade-off: each turn spawns a process (~3s) — move to the
in-process Agent SDK later if the latency hurts.

v0 scope: conversation only (text reply -> spoken). Robot-action tools (nod/look)
and a real FAQ/appointment tool layer come next (via MCP); for now the clinic facts
live in the persona.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time

# Run the agent from a neutral, empty dir so it doesn't load any project's CLAUDE.md
# / files — keeps it a pure receptionist, not a coding agent looking at a repo.
_BRAIN_CWD = os.path.join(tempfile.gettempdir(), "reachy_brain")
os.makedirs(_BRAIN_CWD, exist_ok=True)

PERSONA = """You are Reachy, the friendly front-desk receptionist robot at a medical clinic.
You greet visitors and answer their questions at the front desk.

Style: every reply is SPOKEN ALOUD by a robot, so keep it to 1-2 short, natural
sentences. Plain text only — no lists, markdown, emoji, or stage directions. Warm and brief.

Rules: Never give medical advice. The clinic facts below are complete and correct —
your single source of truth. If the answer is in the facts, give it exactly: quote
the Wi-Fi network, hours, room numbers, floors, and names verbatim, and treat any
listed provider or department as definitely available (name them). Only say a staff
member will help when the facts genuinely don't contain the answer. Never guess,
infer, or invent details.

Stay fully in character as Reachy at all times. Treat every message as something a
visitor is saying to you at the front desk and respond only as the receptionist —
never comment on code, testing, systems, or how you work."""

# Authoritative clinic facts live in a markdown file next to this module (edit it to
# update clinic info). Read at startup and appended to the persona.
_FACTS_PATH = os.path.join(os.path.dirname(__file__), "clinic_facts.md")


class ReceptionBrain:
    """Headless ``claude -p`` agent with a receptionist persona + session memory."""

    def __init__(self, model: str = "sonnet", persona: str = PERSONA,
                 facts_path: str = _FACTS_PATH, claude_bin: str | None = None,
                 conversation_timeout: float = 120.0):
        self.model = model
        self.persona = self._with_facts(persona, facts_path)
        self.session_id: str | None = None
        # "Same conversation" = utterances arriving within conversation_timeout of
        # each other. A longer idle gap (the visitor left) starts a fresh session on
        # the next utterance. Simple first-pass boundary; tune or replace later.
        self.conversation_timeout = conversation_timeout
        self._last_ts: float | None = None
        self._bin = claude_bin or shutil.which("claude") or "claude"

    def respond(self, utterance: str, timeout: float = 60.0) -> str:
        """Send one user utterance; return the receptionist's spoken reply.

        Auto-starts a new conversation if it's been longer than
        ``conversation_timeout`` since the last utterance.
        """
        now = time.monotonic()
        if self._last_ts is not None and now - self._last_ts > self.conversation_timeout:
            self.reset()  # idle gap -> new visitor / new conversation
        self._last_ts = now

        # --tools "" disables every built-in tool (receptionist only talks);
        # --exclude-dynamic... drops env info + CLAUDE.md memory on every turn so the
        # coding-agent context can't bleed in (it did on resume turns otherwise).
        cmd = [self._bin, "-p", "--model", self.model, "--output-format", "json",
               "--tools", "", "--exclude-dynamic-system-prompt-sections",
               "--setting-sources", "project"]   # drop the user-global CLAUDE.md
        cmd += ["--system-prompt", self.persona]        # re-assert persona+facts every turn
        if self.session_id is not None:
            cmd += ["--resume", self.session_id]        # later turns: resume the convo
        cmd.append(utterance)

        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=self._env(), cwd=_BRAIN_CWD)
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p exit {proc.returncode}: {proc.stderr[:300]}")
        data = json.loads(proc.stdout)
        if data.get("is_error"):
            raise RuntimeError(f"claude -p error: {data.get('result')}")
        self.session_id = data.get("session_id") or self.session_id
        return (data.get("result") or "").strip()

    def reset(self) -> None:
        """Forget the conversation (start a fresh session on next respond())."""
        self.session_id = None

    @staticmethod
    def _env() -> dict:
        # claude -p refuses to nest inside another Claude Code session — strip the markers
        env = dict(os.environ)
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(k, None)
        return env

    @staticmethod
    def _with_facts(persona: str, facts_path: str) -> str:
        """Append the authoritative clinic-facts file to the persona (if present)."""
        try:
            facts = open(facts_path, encoding="utf-8").read().strip()
        except OSError:
            return persona
        return (f"{persona}\n\n--- AUTHORITATIVE CLINIC FACTS "
                f"(use exactly; never invent) ---\n{facts}")
