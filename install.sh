#!/usr/bin/env bash
# BAW Installer — one-command setup
# Usage: curl -fsSL https://raw.githubusercontent.com/cornreform/baw-agent-platform/main/install.sh | bash
# Or:   bash install.sh

set -e

BAW_REPO="https://github.com/cornreform/baw-agent-platform.git"
BAW_DIR="$HOME/baw"
BAW_BIN="$HOME/.local/bin/baw"
PYTHON=""

# ── Find Python ──
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Python 3.11+ not found. Install it first."
    exit 1
fi

echo "🔍 Found: $($PYTHON --version)"

# ── Install pip deps if needed ──
if ! $PYTHON -c "import yaml" 2>/dev/null; then
    echo "📦 Installing core dependencies..."
    $PYTHON -m pip install pyyaml httpx duckduckgo-search 2>&1 | tail -1
fi

# ── Clone repo ──
if [ -d "$BAW_DIR" ]; then
    echo "📁 BAW already exists at $BAW_DIR — pulling latest..."
    cd "$BAW_DIR" && git pull
else
    echo "📁 Cloning BAW to $BAW_DIR..."
    git clone "$BAW_REPO" "$BAW_DIR"
fi

# ── Create CLI wrapper ──
mkdir -p "$HOME/.local/bin"
cat > "$BAW_BIN" << EOF
#!/usr/bin/env bash
# BAW CLI wrapper
cd "\$HOME/baw" && exec $PYTHON "\$HOME/baw/baw" "\$@"
EOF
chmod +x "$BAW_BIN"

# ── Check PATH ──
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "⚠️  Add ~/.local/bin to your PATH:"
    echo "   echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
    echo "   source ~/.bashrc"
fi

echo ""
echo "✅ BAW installed!"
echo ""
echo "Next steps:"
echo "  1. Set your API keys:"
echo "     echo 'DEEPSEEK_API_KEY=sk-your-key' >> ~/.baw/.env"
echo ""
echo "  2. Run the setup wizard:"
echo "     baw --setup"
echo ""
echo "  3. Try it:"
echo "     baw 'Hello, BAW!'"
echo "     baw --btw 'What time is it?'"
echo "     baw                      # Interactive chat mode"
echo ""
