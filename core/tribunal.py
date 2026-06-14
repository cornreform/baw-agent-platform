"""
Tribunal — Multi-Model Consensus Engine for BAW

Replaces the concept of "MOA" (Mixture of Agents) with a
courtroom-inspired design: multiple "judges" (models) independently
evaluate a query, then a "chief justice" synthesises a unified verdict.

No affiliation with any external framework.
"""
from __future__ import annotations

import os
import json
import time
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Data structures ──────────────────────────────────────────────

@dataclass
class JudgeVerdict:
    """Single model's response + metadata."""
    judge_name: str          # e.g. "deepseek-v4-pro"
    provider: str            # e.g. "deepseek"
    content: str = ""
    confidence: float = 0.0   # 0-1 self-rated confidence
    latency_ms: float = 0.0
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0

@dataclass
class TribunalRuling:
    """Final synthesised output."""
    verdict: str = ""                      # Unified answer
    consensus_score: float = 0.0           # 0-1 agreement among judges
    minority_opinions: list[str] = field(default_factory=list)
    judge_count: int = 0
    dissent_count: int = 0
    synthesis_latency_ms: float = 0.0
    cost_estimate_usd: float = 0.0
    meta: dict = field(default_factory=dict)

# ── Default bench ──────────────────────────────────────────────

DEFAULT_BENCH: list[dict] = [
    {"name": "deepseek-v4-flash", "provider": "deepseek", "role": "analyst"},
    {"name": "MiniMax-M3",       "provider": "minimax",  "role": "creative"},
]

CHIEF_JUSTICE = {"name": "deepseek-v4-pro", "provider": "deepseek"}

# ── Low-level call wrapper ──────────────────────────────────────

def _call_model(provider: str, model: str, messages: list[dict],
                temperature: float = 0.3, timeout: float = 60) -> dict:
    """Make a real API call. Returns {"content", "tokens_in", "tokens_out", "error"}."""
    import requests

    cfg_path = Path.home() / ".baw" / "config.yaml"
    cfg = {}
    if cfg_path.exists():
        import yaml
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    api_key = ""
    base_url = ""
    pcfg = cfg.get("providers", {}).get(provider, {})
    api_key = os.getenv(pcfg.get("api_key_env", f"{provider.upper()}_API_KEY"), "")
    base_url = pcfg.get("base_url", "")

    if not api_key:
        return {"content": "", "error": f"No API key for {provider}", "tokens_in": 0, "tokens_out": 0}

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return {
            "content": content,
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
            "error": "",
        }
    except Exception as e:
        return {"content": "", "error": str(e)[:200], "tokens_in": 0, "tokens_out": 0}


# ── Judge invocation ──────────────────────────────────────────────

def _invoke_judge(judge_cfg: dict, prompt: str, system: str = "") -> JudgeVerdict:
    """Call a single judge model."""
    t0 = time.time()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = _call_model(judge_cfg["provider"], judge_cfg["name"], messages)
    latency = (time.time() - t0) * 1000

    # Extract self-rated confidence if judge includes it
    confidence = 0.7  # default
    content = result["content"]
    if "[confidence:" in content.lower():
        try:
            tag = content.lower().split("[confidence:")[1].split("]")[0].strip()
            confidence = float(tag)
            content = content.split("]")[1].strip() if "]" in content else content
        except Exception:
            pass

    return JudgeVerdict(
        judge_name=judge_cfg["name"],
        provider=judge_cfg["provider"],
        content=content,
        confidence=confidence,
        latency_ms=latency,
        error=result["error"],
        tokens_in=result["tokens_in"],
        tokens_out=result["tokens_out"],
    )


# ── Core Tribunal ──────────────────────────────────────────────

class Tribunal:
    """
    Multi-model consensus engine.

    Usage:
        t = Tribunal()
        ruling = t.deliberate("Is this code safe?", system="You are a security auditor.")
        print(ruling.verdict)
        print(ruling.consensus_score)
    """

    def __init__(self, bench: list[dict] | None = None,
                 chief: dict | None = None,
                 max_workers: int = 3):
        self.bench = bench or DEFAULT_BENCH
        self.chief = chief or CHIEF_JUSTICE
        self.max_workers = max_workers

    def deliberate(self, prompt: str, system: str = "",
                   temperature: float = 0.3) -> TribunalRuling:
        """
        Run all judges in parallel, then synthesise a unified verdict.
        """
        # ── Phase 1: Parallel judge hearings ──
        verdicts: list[JudgeVerdict] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(_invoke_judge, j, prompt, system): j
                for j in self.bench
            }
            for fut in as_completed(futures):
                try:
                    verdicts.append(fut.result())
                except Exception as e:
                    j = futures[fut]
                    verdicts.append(JudgeVerdict(
                        judge_name=j["name"], provider=j["provider"],
                        error=str(e)[:200]
                    ))

        # Filter out errors
        valid = [v for v in verdicts if not v.error]
        if not valid:
            return TribunalRuling(
                verdict="All judges failed. Check API keys / connectivity.",
                consensus_score=0.0,
                judge_count=len(verdicts),
                dissent_count=len(verdicts),
            )

        # ── Phase 2: Consensus scoring (naive semantic overlap) ──
        consensus = self._score_consensus(valid)

        # ── Phase 3: Chief Justice synthesis ──
        t0 = time.time()
        synthesis_prompt = self._build_synthesis_prompt(prompt, valid, consensus)
        chief_result = _call_model(self.chief["provider"], self.chief["name"],
                                   [{"role": "user", "content": synthesis_prompt}],
                                   temperature=temperature)
        synthesis_latency = (time.time() - t0) * 1000

        # Identify minority opinions (low confidence or outlier content)
        avg_conf = sum(v.confidence for v in valid) / len(valid)
        minority = [f"{v.judge_name}: {v.content[:200]}"
                    for v in valid if v.confidence < avg_conf * 0.7]

        # Cost estimate (very rough)
        total_tokens = sum(v.tokens_in + v.tokens_out for v in valid)
        total_tokens += chief_result.get("tokens_in", 0) + chief_result.get("tokens_out", 0)
        cost = total_tokens * 0.000002  # ~$2 per 1M tokens avg

        return TribunalRuling(
            verdict=chief_result.get("content", "").strip(),
            consensus_score=consensus,
            minority_opinions=minority,
            judge_count=len(valid),
            dissent_count=len(verdicts) - len(valid),
            synthesis_latency_ms=synthesis_latency,
            cost_estimate_usd=cost,
            meta={
                "judges": [v.judge_name for v in valid],
                "latencies_ms": {v.judge_name: v.latency_ms for v in valid},
                "chief_latency_ms": synthesis_latency,
            },
        )

    def _score_consensus(self, verdicts: list[JudgeVerdict]) -> float:
        """Simple consensus: if all answers mention same keywords, high consensus."""
        if len(verdicts) < 2:
            return 1.0

        # Extract keywords (nouns/verbs) from each answer
        import re
        def _keywords(text: str) -> set[str]:
            words = re.findall(r"[a-zA-Z\u4e00-\u9fff]{2,}", text.lower())
            # Filter out common stop words
            stops = {"the","a","an","is","are","was","were","be","been","being",
                     "have","has","had","do","does","did","will","would","could",
                     "should","may","might","must","shall","can","need","dare",
                     "ought","used","to","of","in","for","on","with","at","by",
                     "from","as","into","through","during","before","after",
                     "above","below","between","under","again","further","then",
                     "once","here","there","when","where","why","how","all","each",
                     "few","more","most","other","some","such","no","nor","not",
                     "only","own","same","so","than","too","very","just","and",
                     "but","if","or","because","until","while","this","that",
                     "these","those","i","me","my","myself","we","our","you",
                     "your","he","him","his","she","her","it","its","they",
                     "them","their","what","which","who","whom","am","it",
                     "\u662f","\u7684","\u4e86","\u5728","\u548c","\u6709","\u500b","\u6211","\u4e0d","\u4eba","\u4ed6","\u9019","\u4e2d","\u70ba","\u4e4b","\u8207","\u5927",
                     "\u4f86","\u4e0a","\u5230","\u8aaa","\u8981","\u5c31","\u90a3","\u4f46","\u5b83","\u5011","\u7d66","\u4e5f","\u53ef","\u80fd","\u53bb",
                     "\u4f55","\u91cc","\u51fa","\u6703","\u800c","\u5c0d","\u6216","\u90fd","\u9084","\u5f88","\u591a","\u597d","\u770b","\u5f97","\u7528",
                     "\u4e0b","\u628a","\u8b93","\u88ab","\u5f9e","\u8ddf","\u5462","\u5427","\u554a","\u54e6","\u54c8"}
            return {w for w in words if w not in stops and len(w) > 2}

        keyword_sets = [_keywords(v.content) for v in verdicts]
        if not keyword_sets:
            return 0.0

        # Jaccard-like overlap between all pairs
        overlaps = []
        for i in range(len(keyword_sets)):
            for j in range(i + 1, len(keyword_sets)):
                a, b = keyword_sets[i], keyword_sets[j]
                if not a or not b:
                    continue
                inter = len(a & b)
                union = len(a | b)
                overlaps.append(inter / union if union else 0)

        return sum(overlaps) / len(overlaps) if overlaps else 0.0

    def _build_synthesis_prompt(self, original: str,
                                verdicts: list[JudgeVerdict],
                                consensus: float) -> str:
        lines = [
            f"You are the Chief Justice. {len(verdicts)} judges have reviewed the following case.",
            f"Consensus score: {consensus:.0%}.",
            "",
            f"Case: {original}",
            "",
            "Individual opinions:",
        ]
        for v in verdicts:
            lines.append(f"\n--- {v.judge_name} (confidence: {v.confidence:.0%}) ---")
            lines.append(v.content[:800])

        lines.append("\n" + "=" * 40)
        lines.append(
            "Your task: Synthesise a unified verdict. "
            "If judges disagree, state the majority position and briefly note minority concerns. "
            "Be concise. Start with [VERDICT] then explain reasoning."
        )
        return "\n".join(lines)


# ── Telegram-friendly wrapper ──────────────────────────────────────

def tribunal_command(query: str) -> str:
    """Entry point for Telegram /tribunal command."""
    if not query.strip():
        return (
            "🏛️ **Tribunal** — Multi-Model Consensus Engine\n\n"
            "Usage: `/tribunal <question>`\n"
            "Example: `/tribunal Is this Python function thread-safe?`\n\n"
            "Multiple judges evaluate your question independently, "
            "then a Chief Justice synthesises the unified verdict."
        )

    t = Tribunal()
    ruling = t.deliberate(query)

    lines = [
        "🏛️ **Tribunal Ruling**",
        "",
        f"📋 **Verdict:**\n{ruling.verdict}",
        "",
        f"📊 Consensus: {ruling.consensus_score:.0%} ({ruling.judge_count} judges, {ruling.dissent_count} dissent)",
    ]

    if ruling.minority_opinions:
        lines.append("\n🔔 Minority concerns:")
        for op in ruling.minority_opinions[:3]:
            lines.append(f"  • {op[:120]}...")

    lines.append(f"\n⏱️ Latency: {ruling.synthesis_latency_ms:.0f}ms")
    lines.append(f"💰 Est. cost: ${ruling.cost_estimate_usd:.4f}")

    return "\n".join(lines)


# ── Court integration ──────────────────────────────────────────────

def court_tribunal_ruling(case_text: str, context: str = "") -> TribunalRuling:
    """Called by BAW Court when a case is escalated to Tier-2 (disputed/complex)."""
    system = (
        "You are a senior technical judge. Review the case carefully. "
        "Provide your reasoning and rate your confidence [confidence:0.xx]."
    )
    if context:
        case_text = f"Context:\n{context}\n\nCase:\n{case_text}"
    t = Tribunal()
    return t.deliberate(case_text, system=system)
