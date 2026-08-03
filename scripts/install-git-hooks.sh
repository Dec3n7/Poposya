#!/usr/bin/env bash
# Ставит git pre-commit хук: ruff (линт) + mypy (типы на весь src, files=["src"]
# в pyproject) из .venv. Коммит блокируется, если ruff или mypy не проходят.
#
# Запуск (разово, из корня репо):  bash scripts/install-git-hooks.sh
# Пропустить хук на один коммит:    git commit --no-verify
#
# Хук лежит в .git/hooks (не версионируется) — поэтому логика хранится здесь,
# в трекаемом инсталлере. Фреймворк pre-commit не используем: его system-хуки
# на Windows не находят .venv по относительному пути (WinError 2).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

hook=".git/hooks/pre-commit"
cat > "$hook" <<'EOF'
#!/usr/bin/env bash
# АВТОГЕНЕРАЦИЯ (scripts/install-git-hooks.sh). Гейт: ruff + mypy на весь src.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY=".venv/bin/python"
echo "pre-commit: ruff…"
"$PY" -m ruff check src
echo "pre-commit: mypy…"
"$PY" -m mypy
EOF
chmod +x "$hook"
echo "Хук установлен: $hook (ruff + mypy). Пропуск: git commit --no-verify"
