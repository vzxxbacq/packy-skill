#!/usr/bin/env bash
# 把本 skill 软链到 Claude Code 的 skills 目录，并提供 packyctl 命令。可重复执行。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$SKILLS_DIR" "$BIN_DIR"
ln -sfn "$HERE" "$SKILLS_DIR/packy"
cat > "$BIN_DIR/packyctl" <<EOF
#!/usr/bin/env bash
exec python3 "$HERE/scripts/packyctl.py" "\$@"
EOF
chmod +x "$BIN_DIR/packyctl" "$HERE/scripts/packyctl.py"

echo "skill   -> $SKILLS_DIR/packy"
echo "command -> $BIN_DIR/packyctl"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "提示：$BIN_DIR 不在 PATH 里，加到 shell 配置或直接用 python3 $HERE/scripts/packyctl.py" ;; esac
[ -f "$HOME/.config/packy/.env" ] || [ -f ./.env ] || echo "提示：还没有 .env，参考 $HERE/.env.example 放到 ~/.config/packy/.env"
