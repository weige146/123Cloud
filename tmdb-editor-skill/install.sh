#!/usr/bin/env bash
# 把本目录安装为用户级 ZCode skill：~/.zcode/skills/tmdb-editor/
# 用法: ./install.sh          （在 tmdb-editor-skill/ 目录内执行）
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.zcode/skills/tmdb-editor"

mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SRC/SKILL.md" "$SRC/README.md" "$SRC/references" "$SRC/scripts" "$DEST/"

# 排除开发产物：venv / Chrome profile / pycache 不进 skill 安装目录
for junk in .venv chrome-profile scripts/__pycache__ scripts/.pytest_cache; do
  rm -rf "$DEST/${junk}"
done

# 运行环境（skill venv）：依赖 + playwright（浏览器用系统 Chrome，无需下载 chromium）
if [ ! -x "$DEST/.venv/bin/python" ]; then
  echo ">> 创建 skill 运行 venv …"
  python3 -m venv "$DEST/.venv"
  "$DEST/.venv/bin/pip" install -q --upgrade pip
  "$DEST/.venv/bin/pip" install -q -r "$SRC/requirements.txt"
fi

cat <<'EOF'
安装完成：
  skill 目录   ~/.zcode/skills/tmdb-editor
  运行 Python ~/.zcode/skills/tmdb-editor/.venv/bin/python
下一步：
  1) 首次登录：~/.zcode/skills/tmdb-editor/.venv/bin/python \
       ~/.zcode/skills/tmdb-editor/scripts/browser.py login
  2) 环境自检：… scripts/browser.py check
之后重启 ZCode 会话即可触发 tmdb-editor skill。
EOF
