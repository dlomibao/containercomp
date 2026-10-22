#!/bin/bash
# Link the `ccu` CLI onto your PATH and the Claude Code skill into ~/.claude/skills.
#   ./install.sh          CLI + skill
#   ./install.sh --mcp    also register the MCP server with Claude Code (user scope)
#   ./install.sh --uninstall
set -euo pipefail

REPO=$(cd "$(dirname "$0")" && pwd)
BIN_DIR=${BIN_DIR:-$HOME/.local/bin}
SKILL_DIR=${SKILL_DIR:-$HOME/.claude/skills}

if [ "${1:-}" = --uninstall ]; then
    rm -f "$BIN_DIR/ccu" "$SKILL_DIR/containercomp"
    command -v claude >/dev/null && claude mcp remove -s user containercomp 2>/dev/null || true
    echo "removed ccu, the containercomp skill and MCP registration"
    exit 0
fi

mkdir -p "$BIN_DIR" "$SKILL_DIR"
ln -sfn "$REPO/bin/ccu" "$BIN_DIR/ccu"
ln -sfn "$REPO/skill/containercomp" "$SKILL_DIR/containercomp"
echo "linked $BIN_DIR/ccu -> $REPO/bin/ccu"
echo "linked $SKILL_DIR/containercomp -> $REPO/skill/containercomp"

case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "note: add $BIN_DIR to your PATH" ;; esac

if [ "${1:-}" = --mcp ]; then
    claude mcp add -s user containercomp -- "$BIN_DIR/ccu" mcp
    echo "registered MCP server 'containercomp' (runs: ccu mcp)"
fi

command -v podman >/dev/null || echo "note: podman not found; install it with: brew install podman"
echo "next: ccu up"
