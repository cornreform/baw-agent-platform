# Why I Built BAW — An AI Agent Platform from Scratch

**No LangChain. No AutoGPT. Just a courtroom of angels and devils debating every agent call.**

I've been building with LLM agent frameworks for the past year. LangChain. CrewAI. AutoGen. OpenAI Agents SDK. They all share the same fundamental problems:

1. **Massive codebases doing too much** — LangChain alone is 500K+ LOC. You spend more time learning the framework than building your agent.
2. **Vendor lock-in disguised as convenience** — OpenAI Agents SDK forces you into their API. AutoGen wraps OpenAI. You can't just swap providers without rewriting your code.
3. **Zero execution governance** — When an agent says "I'll write to that file" or "I'll run that command," who's watching? Most frameworks let the LLM self-report. There's no independent verification.
4. **No cost transparency** — You run a complex agent pipeline and have no idea how many tokens each call consumed. The bill comes at the end of the month as a surprise.

So I built my own.

## The Angel/Devil Court

The core insight is simple: **before any action executes, two LLMs debate it.**

- ⚫ **Devil (opposition)** speaks first. Zero execution power. Reviews the plan for flaws, risks, and hallucinated promises. Assigns a score.
- 🤍 **Angel (executor)** listens to the Devil's critique, then acts — or recuses if the Devil's score is higher.

This isn't a gimmick. It catches real failures. The Devil routinely catches the Angel promising to "read a file" without specifying which file, or planning to use a tool that doesn't exist.

No other agent framework has this. Not LangChain. Not CrewAI. Not OpenAI.

## Built from Scratch

I didn't wrap LangChain. I didn't wrap anything. The entire platform is ~15K lines of Python — that's **30x smaller** than LangChain. Every line is ours.

This means:
- **Zero vendor lock-in** — Swap any LLM provider with a one-line config change. OpenAI, Anthropic, DeepSeek, MiniMax, any protocol.
- **Full cost transparency** — Every BAW response ends with `📊 N calls — total: X tokens`. You always know what you're paying for.
- **Self-healing** — If a dependency is missing, BAW auto-installs it and retries. Three layers of safety net: tool execution, connector import, startup initialization.

## Self-Evolution

BAW learns from its mistakes. It has:
- **Weekly self-curation** — Reviews its own behaviour logs, detects patterns, updates its own configuration
- **Self-learning skills** — Give BAW a URL or description, it analyzes and generates a reusable YAML skill
- **Health monitoring watchdog** — Auto-detects resource issues and cleans up

## What's Next

BAW is MIT-licensed and completely open source. It runs as a CLI, a Telegram bot, Discord bot, Slack bot, and Signal bot — all from the same codebase.

The agent platform space is crowded, but most frameworks are wrappers around wrappers, chasing complexity. BAW takes a different path: smaller, more transparent, and built on the simple idea that every action deserves an independent review.

**Try it:**
```bash
git clone https://github.com/cornreform/baw-agent-platform.git
cd baw-agent-platform
pip install -r requirements.txt
ln -sf $PWD/baw ~/.local/bin/baw
baw --setup
baw "Hello, BAW"
```

The courtroom is in session. 🖤🤍
