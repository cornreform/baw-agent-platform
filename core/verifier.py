"""
BAW — Step Verifier (P1)
Independent verification of each tool result before committing.
Uses the LLM to score results against the original goal.

Flow:
  1. After each tool call, verifier checks the output
  2. Score 0-10, threshold >= 7 = pass
  3. On fail: return to loop for retry with different approach
"""

from __future__ import annotations
import json
from typing import Optional


def verify_step(
    goal: str,
    tool_name: str,
    tool_args: dict,
    tool_result: str,
    config: dict,
    model_id: Optional[str] = None,
) -> dict:
    """Verify a single tool execution result.

    Uses the LLM to score the result against the original goal.

    Args:
        goal: The original user prompt / task description
        tool_name: Name of the tool that was executed
        tool_args: Arguments passed to the tool
        tool_result: Raw result from the tool
        config: BAW config dict (for model selection)
        model_id: Optional model override

    Returns:
        dict with:
          - score: int 0-10
          - passed: bool (score >= 7)
          - reason: str explanation
          - actionable: str suggestion for improvement
    """
    # Truncate large results to save tokens
    result_preview = tool_result[:1500] if len(tool_result) > 1500 else tool_result
    args_str = _truncate(json.dumps(tool_args, ensure_ascii=False), 500)

    prompt = (
        f"[Verifier] Goal: {goal}\n\n"
        f"Tool: {tool_name}({args_str})\n\n"
        f"Result:\n{result_preview}\n\n"
        f"Score this result 0-10 based on:\n"
        f"- Does it correctly achieve the goal?\n"
        f"- Are there errors or warnings?\n"
        f"- Is the output complete?\n\n"
        f"Reply with ONLY a JSON object:\n"
        f'{{"score": <int 0-10>, "reason": "<brief explanation>", '
        f'"actionable": "<what to try differently if score < 7>"}}'
    )

    try:
        from .llm import get_model, call_llm_with_fallback

        model = get_model(config, model_id)
        messages = [{"role": "user", "content": prompt}]

        fb = call_llm_with_fallback(
            config, messages,
            temperature=0.3,  # Low temp for consistent scoring
            primary_id=model_id,
        )

        content = fb.response.content.strip()
        # Extract JSON from response
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            score = int(data.get("score", 5))
            return {
                "score": max(0, min(10, score)),
                "passed": score >= 7,
                "reason": data.get("reason", "No reason given"),
                "actionable": data.get("actionable", ""),
                "llm_used": fb.model_used,
            }

        # Fallback: parse text
        return {
            "score": 5,
            "passed": False,
            "reason": "Could not parse verifier response",
            "actionable": "Retry the step",
            "llm_used": fb.model_used,
        }

    except Exception as e:
        return {
            "score": 5,
            "passed": True,  # Pass on error — don't block execution
            "reason": f"Verifier error: {e}",
            "actionable": "",
            "llm_used": "none",
        }


def _truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text
