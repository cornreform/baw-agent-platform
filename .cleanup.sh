#!/bin/bash
# BAW repo cleanup script — remove Hermes/Sunny/Sticky references
# Run from repo root: cd /home/radxa/baw && bash .cleanup.sh

set -e

echo "=== Step 1: Remove personal/internal doc files from git ==="
FILES_TO_REMOVE=(
  "docs/audits/BAW_SYSTEM_AUDIT_P4_v2.md"
  "docs/audits/BAW_AUDIT_UX.md"
  "docs/audits/BAW_AUDIT_FABLE5_COURT_V2.md"
  "tests/conversation-tests-v5.md"
  "tests/conversation-tests-v6.md"
  "tests/edge-case-tests-v14.md"
  "tests/stress-tests-v14.md"
  "tests/test-results-v14-20260613.md"
  "knowledge/INDEX.md"
  "knowledge/SUMMARY_TW.md"
  "knowledge/VERSION-WORKFLOW.md"
  "data/petrestaurants_source.md"
)

for f in "${FILES_TO_REMOVE[@]}"; do
    if [ -f "$f" ]; then
        git rm --quiet "$f" 2>/dev/null && echo "  Removed: $f" || echo "  Skip (not in git): $f"
    fi
done

echo ""
echo "=== Step 2: Clean Hermes/Hermès references in code files ==="

# docker-compose.yml
sed -i 's/^# Fully isolated from Hermes.*/# Fully isolated — own Python, own env, own network namespace./' docker-compose.yml 2>/dev/null
sed -i 's/^# No Hermes paths. No host network. No privileged./# No host network. No privileged./' docker-compose.yml 2>/dev/null
echo "  Cleaned: docker-compose.yml"

# requirements.txt
sed -i 's/^# No Hermes venv dependency.*/# Self-contained — no external agent dependency./' requirements.txt
echo "  Cleaned: requirements.txt"

# tools/patch.py
sed -i 's/Like Hermes patch tool:/Like targeted find-and-replace:/' tools/patch.py
echo "  Cleaned: tools/patch.py"

# tools/http_fetch.py
sed -i 's/using a browser-render tool (Hermes/using a browser-render tool/' tools/http_fetch.py
echo "  Cleaned: tools/http_fetch.py"

# tools/knowledge_graph.py 
sed -i 's/(Sunny|Sticky|Robi|BAW|Hermes)/(developer|assistant|BAW)/' tools/knowledge_graph.py
echo "  Cleaned: tools/knowledge_graph.py"

# core/loop.py
sed -i 's/Hermes-style architectural enforcement/architectural enforcement/' core/loop.py
sed -i 's/Hermes framework prevents fabrication/BAW framework prevents fabrication/' core/loop.py
sed -i 's/Hermes\\\\n/\\n/' core/loop.py 2>/dev/null || true
echo "  Cleaned: core/loop.py"

# core/preflight.py
sed -i 's/"""Hermes exposes a """.*web_extract""""/"""BAW exposes a web_extract tool""" 2>/dev/null || true/p' core/preflight.py 2>/dev/null || true
# Just remove Hermes-specific lines
sed -i '/Hermes exposes/d' core/preflight.py
sed -i '/"hermes web_extract/d' core/preflight.py
echo "  Cleaned: core/preflight.py"

# core/learn.py
sed -i '/來自 Hermes/d' core/learn.py
echo "  Cleaned: core/learn.py"

# core/model_discovery.py
sed -i 's/r"\^lambda\/", r"\^hermes"/r"\^lambda\/"/' core/model_discovery.py
echo "  Cleaned: core/model_discovery.py"

# cli/main.py
sed -i 's/Like Hermes CLI but/BAW agent CLI/' cli/main.py
echo "  Cleaned: cli/main.py"

# cli/commands/tui_chat.py
sed -i 's/Like Hermes CLI but/BAW agent CLI/' cli/commands/tui_chat.py
sed -i 's/Never say "Hermes" or "Sticky".//* BAW identity */' cli/commands/tui_chat.py
echo "  Cleaned: cli/commands/tui_chat.py"

# cli/commands/chat.py
sed -i 's/Never say "Hermes" or "Sticky".//* BAW identity */' cli/commands/chat.py
echo "  Cleaned: cli/commands/chat.py"

# SOUL.default.md template - replace Sunny references with generic
sed -i 's/Sunny 可以直接改呢個 file/BAW 管理員可以直接改呢個 file/' SOUL.default.md
sed -i 's/我叫 \*\*BAW\*\*（Black And White）。我係 Sunny 嘅 Agent Platform。/我叫 **BAW**（Black And White）。我係一個通用 Agent Platform。/' SOUL.default.md
sed -i 's/我係 Sunny 嘅助理/我係一個通用 AI 助理/' SOUL.default.md
sed -i 's/我嘅 naming 來自 Sunny 兩隻黑白色嘅狗/我嘅 naming 來自「黑白分明」嘅設計哲學/' SOUL.default.md
sed -i 's/同 Sunny 一齊生活/同用戶一齊生活/' SOUL.default.md
echo "  Cleaned: SOUL.default.md"

# config.sample.yaml
sed -i 's/日常同 Sunny 吹水/日常對話/' config.sample.yaml
sed -i 's/# Sunny only/##/g' config.sample.yaml
echo "  Cleaned: config.sample.yaml"

# data/petrestaurants_source.md — already removed above

echo ""
echo "=== Step 3: Add docs/audits/ and tests/ patterns to .gitignore ==="
echo "" >> .gitignore
echo "# Internal docs — do not track in public repo" >> .gitignore
echo "docs/audits/" >> .gitignore
echo "tests/conversation-tests-*" >> .gitignore
echo "tests/edge-case-tests-*" >> .gitignore
echo "tests/stress-tests-*" >> .gitignore
echo "tests/test-results-*" >> .gitignore
echo "knowledge/SUMMARY_*" >> .gitignore
echo "BAW-PLAN.html" >> .gitignore
echo "BAW-INTRODUCTION.html" >> .gitignore
echo "docs/SELF_EVOLUTION_ROADMAP.html" >> .gitignore
echo "  Updated: .gitignore"

echo ""
echo "=== Done ==="
echo "Run 'git status' to verify changes."
