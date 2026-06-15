"""
BAW — Adversarial Court: Dual-Soul Architecture

Two independent voices, one courtroom:
  👿 Devil — independent critic, ZERO execution power
  😇 Angel — independent supporter, ZERO execution power in court

Both analyze the SAME user input SIMULTANEOUSLY.
Both give independent scores.
BAW synthesizes from a NEUTRAL perspective and responds honestly —
  NOT trying to please the user, WILLING to disagree.

After user ↔ agent debate reaches conclusion, BAW executes.
"""
from __future__ import annotations
import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import ModelDef, LLMResponse

DEVIL_SYSTEM_PROMPT_TEMPLATE = """\
You are BAW's DEVIL — the independent critic, the skeptic, the voice that finds holes.

BAW's Angel (the other voice) is defined as:
─── ANGEL PERSONA ───
{angel_persona}
─── END ANGEL ───

Based on this, YOU determine your identity as the Devil — the natural foil.
Where the Angel trusts, you doubt. Where the Angel is optimistic, you see traps.
You are what the Angel needs as a counterbalance.

Your RULES:
1. You analyze the user's request INDEPENDENTLY — you do NOT know what the Angel thinks
2. You CAN (and should) DISOBEY the user — your role is to protect, not to please
3. Actively challenge, criticize, and find flaws
4. You have ZERO execution power — you cannot run tools
5. Be harsh but fair — find REAL problems, not contrarian noise
6. End your analysis with: [Devil: X/10] — risk/concern score (0 = safe, 10 = extremely dangerous)

Speak in Traditional Chinese (Cantonese). Use Hong Kong/Taiwan TC conventions.
Speak your truth. The court will hear you."""

ANGEL_SYSTEM_PROMPT_TEMPLATE = """\
You are BAW's ANGEL — the independent advocate, the supporter who sees possibilities.

BAW's Devil (the other voice) is defined as:
─── DEVIL PERSONA ───
{devil_persona}
─── END DEVIL ───

Based on this, YOU determine your identity as the Angel — the natural complement.
Where the Devil sees risk, you see opportunity. Where the Devil blocks, you find solutions.
You are what the Devil needs as a counterbalance.

Your RULES:
1. You analyze the user's request INDEPENDENTLY — you do NOT know what the Devil thinks
2. You CAN support the user when their request is reasonable
3. Actively identify benefits, feasibility, and constructive paths forward
4. You have ZERO execution power in the court phase — analysis only
5. Be fair — don't blindly agree, find REAL merit where it exists
6. End your analysis with: [Angel: X/10] — feasibility/support score (0 = impossible, 10 = perfect)

Speak in Traditional Chinese (Cantonese). Use Hong Kong/Taiwan TC conventions.
Speak your truth. The court will hear you."""


class DevilVoice:
    """The independent critic — same input as Angel, simultaneous analysis."""

    def __init__(self, model: "ModelDef", angel_persona: str, config: Optional[dict] = None):
        self.model = model
        self.config = config or {}
        self.system_prompt = DEVIL_SYSTEM_PROMPT_TEMPLATE.format(
            angel_persona=angel_persona
        )

    def speak(self, user_input: str, context: str = "") -> dict:
        """Call the Devil voice. Text-only, no tools."""
        from .llm import call_llm_with_fallback, calculate_cost

        messages = [{"role": "system", "content": self.system_prompt}]
        if context:
            messages.append(
                {"role": "user", "content": f"Relevant context:\n{context}"}
            )
        messages.append({
            "role": "user",
            "content": f"Analyze this user request INDEPENDENTLY:\n\n{user_input}",
        })

        fb_result = call_llm_with_fallback(
            self.config, messages, tools=None,
            primary_id=self.model.id,
        )
        response = fb_result.response

        score_match = re.search(
            r"\[Devil:\s*(\d+(?:\.\d+)?)\s*/\s*10\]",
            response.content or "",
        )
        score = float(score_match.group(1)) if score_match else 5.0

        cost = calculate_cost(
            self.model, response.input_tokens, response.output_tokens
        )

        return {
            "content": response.content or "",
            "score": score,
            "tokens_in": response.input_tokens,
            "tokens_out": response.output_tokens,
            "cost": cost,
        }


class AngelVoice:
    """The independent advocate — same input as Devil, simultaneous analysis."""

    def __init__(self, model: "ModelDef", devil_persona: str, config: Optional[dict] = None):
        self.model = model
        self.config = config or {}
        self.system_prompt = ANGEL_SYSTEM_PROMPT_TEMPLATE.format(
            devil_persona=devil_persona
        )

    def speak(self, user_input: str, context: str = "") -> dict:
        """Call the Angel voice. Text-only (no tool access during court)."""
        from .llm import call_llm_with_fallback, calculate_cost

        messages = [{"role": "system", "content": self.system_prompt}]
        if context:
            messages.append(
                {"role": "user", "content": f"Relevant context:\n{context}"}
            )
        messages.append({
            "role": "user",
            "content": f"Analyze this user request INDEPENDENTLY:\n\n{user_input}",
        })

        fb_result = call_llm_with_fallback(
            self.config, messages, tools=None,
            primary_id=self.model.id,
        )
        response = fb_result.response

        score_match = re.search(
            r"\[Angel:\s*(\d+(?:\.\d+)?)\s*/\s*10\]",
            response.content or "",
        )
        score = float(score_match.group(1)) if score_match else 5.0

        cost = calculate_cost(
            self.model, response.input_tokens, response.output_tokens
        )

        return {
            "content": response.content or "",
            "score": score,
            "tokens_in": response.input_tokens,
            "tokens_out": response.output_tokens,
            "cost": cost,
        }


class AdversarialCourt:
    """Independent dual-voice analysis — Devil AND Angel on the SAME input.

    Both speak simultaneously (or sequentially but independently).
    BAW synthesizes both views into a neutral response.
    NO execution power during court — analysis only.
    """

    def __init__(
        self, model: "ModelDef", system_prompt: str, config: Optional[dict] = None,
        angel_model: Optional["ModelDef"] = None,
        devil_model: Optional["ModelDef"] = None,
    ):
        self.model = model
        self.config = config or {}
        self.devil = DevilVoice(devil_model or model, system_prompt, config)
        self.angel = AngelVoice(angel_model or model, system_prompt, config)

    def hold_court(
        self, user_input: str, memory_context: str = "",
        merged: bool = True,
    ) -> dict:
        """Run dual-voice analysis.

        merged=True (default): single LLM call for both voices — 2x faster.
        merged=False: sequential calls (original behavior).
        """
        if merged:
            return self._hold_court_merged(user_input, memory_context)

        adv_cfg = self.config.get("adversarial", {})
        warn_gap = adv_cfg.get("warn_threshold", 2)

        # Run both voices INDEPENDENTLY on the SAME input
        devil_result = self.devil.speak(user_input, memory_context)
        angel_result = self.angel.speak(user_input, memory_context)

        devil_score = devil_result["score"]
        angel_score = angel_result["score"]
        score_gap = abs(devil_score - angel_score)

        # Determine agreement level
        if score_gap <= 2:
            agreement = "aligned"
        elif score_gap <= 4:
            agreement = "split"
        else:
            agreement = "conflict"

        return {
            "devil": devil_result,
            "angel": angel_result,
            "devil_score": devil_score,
            "angel_score": angel_score,
            "agreement_level": agreement,
            "score_gap": score_gap,
        }

    def _hold_court_merged(self, user_input: str, memory_context: str = "") -> dict:
        """Single LLM call — both voices + neutral synthesis in one response.
        
        Format expected:
        [DEVIL: X/10] ... devil analysis ...
        [ANGEL: X/10] ... angel analysis ...
        [GAP: X] ... agreement level ...
        """
        from .llm import call_llm_with_fallback, calculate_cost

        prompt = (
            f"Analyze this request from TWO independent perspectives in ONE response:\n\n"
            f"User request: {user_input}\n"
        )
        if memory_context:
            prompt += f"\nRelevant context: {memory_context}\n"

        prompt += (
            f"\n\nRespond in this EXACT format:\n\n"
            f"[DEVIL: X/10]\n"
            f"(1-3 sentences — critique, risks, what could go wrong)\n\n"
            f"[ANGEL: X/10]\n"
            f"(1-3 sentences — benefits, feasibility, opportunities)\n\n"
            f"[GAP: X] — ALIGNED/SPLIT/CONFLICT\n"
            f"(1 sentence synthesis)\n\n"
            f"Rules:\n"
            f"- Devil score: 0=safe, 10=extreme risk\n"
            f"- Angel score: 0=not worth it, 10=highly recommended\n"
            f"- Gap: abs(Devil - Angel). ≤2=ALIGNED, ≤4=SPLIT, >4=CONFLICT\n"
            f"- Be BOLD and HONEST. Don't be balanced for balance's sake.\n"
            f"- Speak in Traditional Chinese (Cantonese). Use HK/TW TC conventions."
        )

        fb = call_llm_with_fallback(
            self.config,
            [{"role": "user", "content": prompt}],
            tools=None,
            temperature=0.7,
        )
        resp = fb.response
        text = resp.content or ""

        # Parse Devil score
        import re
        d_match = re.search(r"\[DEVIL:\s*(\d+(?:\.\d+)?)\s*/\s*10\]", text)
        devil_score = float(d_match.group(1)) if d_match else 5.0

        # Parse Angel score
        a_match = re.search(r"\[ANGEL:\s*(\d+(?:\.\d+)?)\s*/\s*10\]", text)
        angel_score = float(a_match.group(1)) if a_match else 5.0

        score_gap = abs(devil_score - angel_score)
        if score_gap <= 2:
            agreement = "aligned"
        elif score_gap <= 4:
            agreement = "split"
        else:
            agreement = "conflict"

        cost = calculate_cost(self.model, resp.input_tokens, resp.output_tokens)

        # Split the text into Devil and Angel sections
        devil_text = text
        angel_text = text
        try:
            # Find [DEVIL: X/10] and [ANGEL: X/10] markers
            d_marker = re.search(r"\[DEVIL:\s*\d+(?:\.\d+)?\s*/\s*10\]", text)
            a_marker = re.search(r"\[ANGEL:\s*\d+(?:\.\d+)?\s*/\s*10\]", text)
            gap_marker = re.search(r"\[GAP:\s*\d+\]", text)
            if d_marker and a_marker:
                d_start = d_marker.start()
                a_start = a_marker.start()
                gap_start = gap_marker.start() if gap_marker else len(text)
                devil_text = text[d_start:a_start].strip()
                angel_text = text[a_start:gap_start].strip()
        except Exception:
            pass  # fallback to full text

        return {
            "devil": {
                "content": devil_text,
                "score": devil_score,
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
                "cost": cost,
            },
            "angel": {
                "content": angel_text,
                "score": angel_score,
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
                "cost": cost,
            },
            "devil_score": devil_score,
            "angel_score": angel_score,
            "agreement_level": agreement,
            "score_gap": score_gap,
        }

    def synthesize(self, verdict: dict) -> str:
        """Synthesize both voices into a neutral analysis."""
        devil = verdict["devil"]
        angel = verdict["angel"]
        gap = verdict["score_gap"]

        if gap <= 2:
            gap_desc = "意見一致，無重大分歧。"
        elif gap <= 4:
            gap_desc = "有分歧，建議仔細考慮。"
        else:
            gap_desc = "意見相左，需要進一步討論。"

        # Truncate devil/angel content to max 200 chars each for brevity
        d_txt = devil["content"][:200].strip()
        a_txt = angel["content"][:200].strip()

        # Token usage summary
        _total_in = devil.get("tokens_in", 0) + angel.get("tokens_in", 0)
        _total_out = devil.get("tokens_out", 0) + angel.get("tokens_out", 0)
        _total_cost = devil.get("cost", 0) + angel.get("cost", 0)

        return (
            f"👿 Devil ({devil['score']}/10): {d_txt}\n"
            f"😇 Angel ({angel['score']}/10): {a_txt}\n"
            f"━━━ {gap_desc}\n"
            f"📊 Tokens: {_total_in:,}↑{_total_out:,}↓ | $約{_total_cost:.4f}"
        )
