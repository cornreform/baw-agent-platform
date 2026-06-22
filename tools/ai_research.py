"""BAW built-in: AI Research Agent — structured web research tool

Takes URL + research question, fetches page content via local web_extract,
then spawns a sub-agent for LLM analysis. Returns structured JSON.

Fully self-contained — no external APIs. Uses BAW's own tools internally.
"""


def ai_research(url: str, question: str) -> str:
    """Research a URL with a specific question and return structured findings.

    Uses local web_extract (html2text + BeautifulSoup) to fetch content,
    then a sub-agent to analyze and produce structured JSON output.

    Args:
        url: URL to research (article, documentation, repo, etc.)
        question: What you want to know about the content

    Returns:
        Structured findings in JSON format.
    """
    from tools.web_extract import web_extract

    # Step 1: Fetch page content using local extraction (0 external deps)
    content = web_extract(url, max_chars=0)

    if content.startswith("Error:") or content.startswith("No extractable"):
        return f'{{"error": "{content}"}}'

    # Step 2: Truncate to manageable size
    max_content = 20000
    if len(content) > max_content:
        content = content[:max_content] + "\n\n[... content truncated for analysis]"

    # Step 3: Build structured analysis prompt
    analysis_prompt = (
        f"Research URL: {url}\n"
        f"Question: {question}\n\n"
        f"Page content:\n{content}\n\n"
        f"---\n"
        f"Analyze the above content and return STRICT JSON only (no markdown wrappers):\n"
        f"{{\n"
        f'  "url": "{url}",\n'
        f'  "title": "page title if found",\n'
        f'  "summary": "2-3 sentence summary of findings",\n'
        f'  "key_findings": ["finding 1", "finding 2", ...],\n'
        f'  "citations": ["relevant quotes or data points"],\n'
        f'  "confidence_score": 0-100\n'
        f"}}\n"
    )

    # Step 4: Use delegate_task sub-agent for LLM analysis
    from tools.delegate_task import delegate_task as _delegate
    result = _delegate(
        goal="Analyze the provided web content and return structured JSON findings.",
        context=analysis_prompt,
    )

    return result


TOOL_DEF = {
    "name": "ai_research",
    "description": (
        "Research a URL with a specific question. "
        "Fetches the page using local web_extract (html2text, no external APIs), "
        "analyzes the content with a sub-agent, and returns structured JSON "
        "with summary, key findings, citations, and confidence score. "
        "Use for deep-dive research on articles, documentation, repos, etc."
    ),
    "handler": ai_research,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to research (article, documentation, repo, etc.)",
            },
            "question": {
                "type": "string",
                "description": "What you want to know about the content",
            },
        },
        "required": ["url", "question"],
    },
    "risk_level": "low",
}
