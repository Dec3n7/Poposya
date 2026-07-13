"""Чистые хелперы форматирования музыкального UI — без Discord и состояния."""

from src.domain.music.entities import RepeatMode

REPEAT_LABELS = {RepeatMode.OFF: "выкл", RepeatMode.ONE: "трек", RepeatMode.ALL: "все"}
PROGRESS_WIDTH = 14
EMBED_COLOR = 0x9B59B6


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "🔴 эфир"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(elapsed: int, total: int, width: int = PROGRESS_WIDTH) -> str:
    if total <= 0:
        return "▬" * width
    pos = min(width - 1, int(width * elapsed / total))
    return "".join("🔘" if i == pos else "▬" for i in range(width))


def trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fmt_count(n: int | None) -> str | None:
    """1234567 -> «1.2M», 15300 -> «15.3K». None -> None."""
    if not n:
        return None
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def block_index(blocks: list[tuple[float, list[str]]], position: float) -> int:
    """Индекс абзаца, играющего на позиции position; -1 — трек ещё не дошёл."""
    index = -1
    for i, (start, _) in enumerate(blocks):
        if position >= start:
            index = i
        else:
            break
    return index
