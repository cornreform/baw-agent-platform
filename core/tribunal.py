"""
Tribunal — Multi-Model Consensus Engine for BAW

Courtroom-inspired consensus: multiple "judges" (models) independently
evaluate a query, then a "chief justice" synthesises a unified verdict.

Model-agnostic: bench and chief are read from config.yaml. Users choose
their own judges.
"""
from __future__ import annotations

import os
import json
import time
import re
from pathlib import Path
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed


# ── Data structures ──────────────────────────────────────────────

@dataclass
class JudgeVerdict:
    """Single model's response + metadata."""
    judge_name: str
    provider: str
    content: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class TribunalRuling:
    """Final synthesised output."""
    verdict: str = ""
    consensus_score: float = 0.0
    minority_opinions: list[str] = field(default_factory=list)
    judge_count: int = 0
    dissent_count: int = 0
    synthesis_latency_ms: float = 0.0
    cost_estimate_usd: float = 0.0
    meta: dict = field(default_factory=dict)


# ── Config loading ───────────────────────────────────────────────

def _load_baw_config() -> dict:
    """Read config.yaml from ~/.baw/"""
    cfg_path = Path.home() / ".baw" / "config.yaml"
    if cfg_path.exists():
        import yaml
        try:
            return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    return {}


def _get_tribunal_config(cfg: dict | None = None) -> dict:
    """Extract tribunal section from config."""
    cfg = cfg or _load_baw_config()
    return cfg.get("tribunal", {})


def _get_default_bench(cfg: dict | None = None) -> list[dict]:
    """Default bench: use first 2 available models from config."""
    cfg = cfg or _load_baw_config()
    models_cfg = cfg.get("models", {})
    providers_cfg = cfg.get("providers", {})

    bench = []
    # Try default model + chat model as judges
    for model_id in {models_cfg.get("default"), models_cfg.get("chat")}:
        if not model_id:
            continue
        # Find which provider has this model
        for pname, pcfg in providers_cfg.items():
            for m in pcfg.get("models", []):
                if m.get("id") == model_id:
                    bench.append({"name": model_id, "provider": pname, "role": "judge"})
                    break
    return bench


def _get_default_chief(cfg: dict | None = None) -> dict:
    """Default chief: the highest-tier model available."""
    cfg = cfg or _load_baw_config()
    models_cfg = cfg.get("models", {})
    providers_cfg = cfg.get("providers", {})

    # Prefer "chat" or "default" model as chief
    chief_model = models_cfg.get("chat") or models_cfg.get("default", "")
    if chief_model:
        for pname, pcfg in providers_cfg.items():
            for m in pcfg.get("models", []):
                if m.get("id") == chief_model:
                    return {"name": chief_model, "provider": pname}
    return {"name": "", "provider": ""}


# ── Low-level API call ───────────────────────────────────────────

def _call_model(provider: str, model: str, messages: list[dict],
                temperature: float = 0.3, timeout: float = 60) -> dict:
    import requests

    cfg = _load_baw_config()
    pcfg = cfg.get("providers", {}).get(provider, {})
    api_key = os.getenv(pcfg.get("api_key_env", f"{provider.upper()}_API_KEY"), "")
    base_url = pcfg.get("base_url", "")

    if not api_key:
        return {"content": "", "error": f"No API key for {provider}", "tokens_in": 0, "tokens_out": 0}

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        r.raise_for_status()
        d = r.json()
        content = d.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = d.get("usage", {})
        return {
            "content": content,
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
            "error": "",
        }
    except Exception as e:
        return {"content": "", "error": str(e)[:200], "tokens_in": 0, "tokens_out": 0}


# ── Judge invocation ─────────────────────────────────────────────

def _invoke_judge(judge_cfg: dict, prompt: str, system: str = "") -> JudgeVerdict:
    t0 = time.time()

    # Build message list once
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})

    # Chain of attempts: primary → fallback(s)
    attempts = [(judge_cfg["provider"], judge_cfg["name"])]
    fb = judge_cfg.get("fallback")
    if fb:
        fbp = fb.get("provider", "")
        fbn = fb.get("name", "")
        if fbp and fbn:
            attempts.append((fbp, fbn))

    result = {"content": "", "error": "All models failed", "tokens_in": 0, "tokens_out": 0}
    used_provider, used_model = "", ""
    for provider, model in attempts:
        result = _call_model(provider, model, messages)
        if result["content"] and not result["error"]:
            used_provider, used_model = provider, model
            break

    latency = (time.time() - t0) * 1000

    confidence = 0.7
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
        provider=used_provider or judge_cfg["provider"],
        content=content,
        confidence=confidence,
        latency_ms=latency,
        error=result["error"],
        tokens_in=result["tokens_in"],
        tokens_out=result["tokens_out"],
    )


# ── Consensus scoring ────────────────────────────────────────────

def _score_consensus(verdicts: list[JudgeVerdict]) -> float:
    if len(verdicts) < 2:
        return 1.0

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
             "是","的","了","在","和","有","個","我","不","人","他","這","中","為","之","與","大",
             "來","上","到","說","要","就","那","但","它","們","給","也","可","能","去",
             "何","里","出","會","而","對","或","都","還","很","多","好","看","得","用",
             "下","把","讓","被","從","跟","呢","吧","啊","哦","哈"}

    def _keywords(text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z\u4e00-\u9fff]{2,}", text.lower())
        return {w for w in words if w not in stops and len(w) > 2}

    keyword_sets = [_keywords(v.content) for v in verdicts]
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


def _build_synthesis_prompt(original: str, verdicts: list[JudgeVerdict], consensus: float) -> str:
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


# ── Core Tribunal class ──────────────────────────────────────────

class Tribunal:
    """
    Multi-model consensus engine.

    bench and chief are read from config.yaml tribunal section,
    or auto-detected from available models.
    """

    def __init__(self, bench: list[dict] | None = None,
                 chief: dict | None = None,
                 max_workers: int = 3):
        cfg = _load_baw_config()
        tcfg = _get_tribunal_config(cfg)

        self.bench = bench or tcfg.get("bench") or _get_default_bench(cfg)
        self.chief = chief or tcfg.get("chief") or _get_default_chief(cfg)
        self.max_workers = max_workers

    def deliberate(self, prompt: str, system: str = "",
                   temperature: float = 0.3) -> TribunalRuling:
        if not self.bench:
            return TribunalRuling(
                verdict="No judges configured. Add models to config.yaml tribunal.bench",
                consensus_score=0.0,
                judge_count=0,
                dissent_count=0,
            )

        # Phase 1: Parallel judge hearings
        verdicts: list[JudgeVerdict] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(_invoke_judge, j, prompt, system): j for j in self.bench}
            for fut in as_completed(futures):
                try:
                    verdicts.append(fut.result())
                except Exception as e:
                    j = futures[fut]
                    verdicts.append(JudgeVerdict(
                        judge_name=j.get("name", "unknown"),
                        provider=j.get("provider", "unknown"),
                        error=str(e)[:200]
                    ))

        valid = [v for v in verdicts if not v.error]
        if not valid:
            errors = " | ".join(v.error for v in verdicts if v.error)[:200]
            return TribunalRuling(
                verdict=f"All judges failed: {errors}",
                consensus_score=0.0,
                judge_count=len(verdicts),
                dissent_count=len(verdicts),
            )

        # Phase 2: Consensus
        consensus = _score_consensus(valid)

        # Phase 3: Chief Justice synthesis (with fallback)
        t0 = time.time()
        if self.chief and self.chief.get("name"):
            synthesis_prompt = _build_synthesis_prompt(prompt, valid, consensus)
            # Chain: chief primary → chief fallback
            chief_attempts = [(self.chief["provider"], self.chief["name"])]
            cf = self.chief.get("fallback")
            if cf:
                cfp = cf.get("provider", "")
                cfn = cf.get("name", "")
                if cfp and cfn:
                    chief_attempts.append((cfp, cfn))
            chief_result = {"content": "", "tokens_in": 0, "tokens_out": 0}
            for cp_name, cm_name in chief_attempts:
                cr = _call_model(
                    cp_name, cm_name,
                    [{"role": "user", "content": synthesis_prompt}],
                    temperature=temperature
                )
                if cr.get("content", "").strip() and not cr.get("error"):
                    chief_result = cr
                    break
            verdict_text = chief_result.get("content", "").strip()
        else:
            # No chief — use highest-confidence judge as fallback
            best = max(valid, key=lambda v: v.confidence)
            verdict_text = f"[NO CHIEF CONFIGURED] Fallback to best judge ({best.judge_name}):\n\n{best.content}"
            chief_result = {"tokens_in": 0, "tokens_out": 0}

        synthesis_latency = (time.time() - t0) * 1000

        avg_conf = sum(v.confidence for v in valid) / len(valid)
        minority = [f"{v.judge_name}: {v.content[:200]}"
                    for v in valid if v.confidence < avg_conf * 0.7]

        total_tokens = sum(v.tokens_in + v.tokens_out for v in valid)
        total_tokens += chief_result.get("tokens_in", 0) + chief_result.get("tokens_out", 0)
        cost = total_tokens * 0.000002

        return TribunalRuling(
            verdict=verdict_text,
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


# ── Telegram interface ───────────────────────────────────────────

def tribunal_command(query: str) -> str:
    """Entry point for Telegram /tribunal command."""
    query = query.strip()

    # Config / bench inspection
    if query.lower() in ("bench", "judges", "config"):
        cfg = _load_baw_config()
        tcfg = _get_tribunal_config(cfg)
        bench = tcfg.get("bench") or _get_default_bench(cfg)
        chief = tcfg.get("chief") or _get_default_chief(cfg)

        lines = ["⚖️ **Tribunal Configuration**", ""]
        lines.append(f"**Chief Justice:** {chief.get('name', 'not set')} ({chief.get('provider', '')})")
        lines.append("")
        lines.append(f"**Bench ({len(bench)} judges):**")
        for i, j in enumerate(bench, 1):
            lines.append(f"  {i}. {j.get('name')} ({j.get('provider')}) — {j.get('role', 'judge')}")
        if not bench:
            lines.append("  (empty — add to config.yaml tribunal.bench)")
        lines.append("")
        lines.append("To customise, edit `~/.baw/config.yaml`:")
        lines.append("```yaml")
        lines.append("tribunal:")
        lines.append("  chief:")
        lines.append("    name: your-chief-model")
        lines.append("    provider: your-provider")
        lines.append("  bench:")
        lines.append("    - name: judge-model-1")
        lines.append("      provider: provider-1")
        lines.append("      role: analyst")
        lines.append("    - name: judge-model-2")
        lines.append("      provider: provider-2")
        lines.append("      role: creative")
        lines.append("```")
        return "\n".join(lines)

    if not query:
        return (
            "🏛️ **Tribunal** — Multi-Model Consensus Engine\n\n"
            "Usage:\n"
            "`/tribunal <question>` — Ask for a consensus ruling\n"
            "`/tribunal bench` — Show current judge configuration\n\n"
            "Customise judges in `~/.baw/config.yaml` under `tribunal:` section."
        )

    # Actual deliberation
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


# ── Court integration ────────────────────────────────────────────

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
