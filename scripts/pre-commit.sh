#!/bin/bash
# Pre-commit hook for Voice Input Framework
set -e

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$(cd "$(dirname "$0")/.." && pwd)")"
cd "$ROOT"

echo "🔍 Pre-commit checks..."

# ── Rust checks ──
if command -v cargo &> /dev/null && [ -f gui/src-tauri/Cargo.toml ]; then
    echo ""
    echo "📦 Rust checks..."

    if command -v rustfmt &> /dev/null; then
        echo "  • rustfmt..."
        (cd gui/src-tauri && cargo fmt --check) || {
            echo "  ⚠️  Rust formatting issues. Run: cd gui/src-tauri && cargo fmt"
            exit 1
        }
        echo "  ✅ rustfmt ok"
    fi

    if command -v cargo-clippy &> /dev/null; then
        echo "  • clippy..."
        (cd gui/src-tauri && cargo clippy --all-targets --all-features -- -A unused-imports -D warnings) || {
            echo "  ❌ Clippy violations found"
            exit 1
        }
        echo "  ✅ clippy ok"
    fi
fi

# ── Python checks ──
if command -v ruff &> /dev/null; then
    echo ""
    echo "🐍 Python checks..."
    echo "  • ruff check..."
    ruff check client/ server/ services/ --fix --exit-non-zero-on-fix || {
        echo "  ❌ Ruff violations found"
        exit 1
    }
    echo "  • ruff format..."
    ruff format --check client/ server/ services/ || {
        echo "  ⚠️  Ruff formatting issues. Run: ruff format client/ server/ services/"
        exit 1
    }
    echo "  ✅ ruff ok"
fi

echo ""
echo "✅ All checks passed!"
