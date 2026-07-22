"""Гвард каталога фраз (P4, план §6): в КОНВЕРТИРОВАННЫХ когах user-facing
текст не хардкодится — он идёт через persona.phrase()/render_block().

Ловим строковые литералы с кириллицей, переданные напрямую в говорящие вызовы
(.send / .send_message / Embed(...) / add_field / set_footer). Новая фраза в
конвертированном коге обязана появиться в PHRASE_SPECS, иначе каталог панели
протухнет. Файл конвертировали — добавь его в CONVERTED."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# коги, переведённые на каталог фраз (волны P4)
CONVERTED = [
    "src/infrastructure/discord/cogs/activity.py",
    "src/infrastructure/discord/cogs/ai_chat.py",
    "src/infrastructure/discord/cogs/cinema/cog.py",
    "src/infrastructure/discord/cogs/cinema/forum.py",
    "src/infrastructure/discord/cogs/cinema/service.py",
    "src/infrastructure/discord/cogs/cinema/views.py",
    "src/infrastructure/discord/cogs/finds.py",
    "src/infrastructure/discord/cogs/introduce.py",
    "src/infrastructure/discord/cogs/music/cog.py",
    "src/infrastructure/discord/cogs/music/lyrics.py",
    "src/infrastructure/discord/cogs/music/service.py",
    "src/infrastructure/discord/cogs/music/views.py",
    "src/infrastructure/discord/cogs/secret_room.py",
    "src/infrastructure/discord/cogs/tempvoice/cog.py",
    "src/infrastructure/discord/cogs/tempvoice/views.py",
]

# вызовы, в которых литералы = голос бота
_SEND_ATTRS = {"send", "send_message"}
_EMBED_PART_ATTRS = {"add_field", "set_footer"}
_EMBED_KWARGS = {"title", "description", "name", "value", "text", "content"}

_CYRILLIC = re.compile("[а-яА-ЯёЁ]")


def _literal_strings(node: ast.expr) -> list[str]:
    """Строковые литералы внутри выражения: Constant и куски f-строк.
    Вглубь вызовов (str(persona.phrase(...))) не заходим — там уже каталог."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ]
    if isinstance(node, ast.BinOp):  # "лит" + x + "лит"
        return _literal_strings(node.left) + _literal_strings(node.right)
    return []


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr in _SEND_ATTRS:
            candidates = list(node.args) + [kw.value for kw in node.keywords if kw.arg == "content"]
        elif attr in _EMBED_PART_ATTRS or attr == "Embed":
            candidates = list(node.args) + [
                kw.value for kw in node.keywords if kw.arg in _EMBED_KWARGS
            ]
        else:
            continue
        for arg in candidates:
            for text in _literal_strings(arg):
                if _CYRILLIC.search(text):
                    found.append(f"{path.name}:{node.lineno}: {text[:60]!r}")
    return found


def test_converted_cogs_have_no_hardcoded_voice():
    problems: list[str] = []
    for rel in CONVERTED:
        problems += _violations(ROOT / rel)
    assert not problems, (
        "Хардкод голоса в конвертированном коге — вынеси строку в PHRASE_SPECS "
        "и читай через persona.phrase()/render_block():\n" + "\n".join(problems)
    )


def test_converted_files_exist():
    for rel in CONVERTED:
        assert (ROOT / rel).is_file(), rel
