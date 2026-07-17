"""Диагностика старта: сводка конфигурации и проверка связи с внешними
сервисами (Groq, провайдеры фильмов). Всё не фатально — при сбое бот
всё равно поднимается, но в логах видно, что именно недоступно."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

import aiohttp

from src.config import Settings

logger = logging.getLogger("boot")

_GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
_TMDB_URL = "https://api.themoviedb.org/3/configuration"
_KINOPOISK_URL = "https://api.kinopoisk.dev/v1.4/movie/search"


def _db_label(database_url: str) -> str:
    """Ярлык БД без утечки пароля из строки подключения."""
    if database_url.startswith("sqlite"):
        return database_url.split("///")[-1] or "sqlite (in-memory)"
    return database_url.split("://", 1)[0] + "://…(скрыто)"


def _yesno(flag: bool, yes: str = "ВКЛ", no: str = "ВЫКЛ") -> str:
    return yes if flag else no


def backup_status(settings: Settings) -> str | None:
    """Почему автобэкапа не будет; None — будет.

    Отдельная функция, потому что молчаливое отсутствие бэкапов — худший из
    возможных сюрпризов: на Postgres SqliteBackupService просто отключается."""
    from src.infrastructure.db.backup import sqlite_path_from_url

    if sqlite_path_from_url(settings.database_url) is None:
        return "БД не SQLite — встроенный бэкап умеет только его, нужен pg_dump снаружи"
    if settings.backup_interval_hours <= 0 or settings.backup_keep <= 0:
        return "выключен настройками (BACKUP_INTERVAL_HOURS / BACKUP_KEEP = 0)"
    return None


def log_boot_summary(settings: Settings) -> None:
    """Печатает, что включилось, а что отключено и почему (пункт 1)."""
    ai_on = bool(settings.groq_api_key)
    cache_on = settings.music_prefetch_tracks > 0
    cookies_on = bool(settings.ytdlp_cookies_file or settings.ytdlp_cookies_from_browser)

    logger.info("── Конфигурация ─────────────────────────────")
    no_backup = backup_status(settings)
    if no_backup is None:
        logger.info(
            "БД       : %s · бэкап каждые %d ч (хранить %d)",
            _db_label(settings.database_url),
            settings.backup_interval_hours,
            settings.backup_keep,
        )
    else:
        # именно warning: обещать бэкапы, которых нет, опаснее чем не делать их
        logger.warning(
            "БД       : %s · АВТОБЭКАПА НЕТ: %s",
            _db_label(settings.database_url),
            no_backup,
        )
    if ai_on:
        logger.info(
            "AI       : ВКЛ · %s → фолбэк %s",
            settings.ai_model,
            settings.ai_fallback_model or "нет",
        )
    else:
        logger.info("AI       : ВЫКЛ (GROQ_API_KEY не задан) — общение и комментарии молчат")
    logger.info(
        "Киноклуб : provider=%s · ключи tmdb:%s kinopoisk:%s",
        settings.movie_provider,
        _yesno(bool(settings.tmdb_api_key), "есть", "нет"),
        _yesno(bool(settings.kinopoisk_api_key), "есть", "нет"),
    )
    logger.info(
        "Музыка   : аудио-кэш %s · cookies %s",
        f"ВКЛ ({settings.music_cache_max_mb} МБ, префетч {settings.music_prefetch_tracks})"
        if cache_on
        else "ВЫКЛ",
        _yesno(cookies_on, "заданы", "нет"),
    )
    logger.info(
        "Войс     : очки %s",
        f"ВКЛ ({settings.voice_points_per_hour}/час)"
        if settings.voice_points_per_hour > 0
        else "ВЫКЛ",
    )
    logger.info(
        "Находки  : интервал %d–%d ч · жизнь %d ч",
        settings.finds_min_interval_hours,
        settings.finds_max_interval_hours,
        settings.finds_lifetime_hours,
    )
    logger.info(
        "Логи     : уровень %s, формат %s, файл %s",
        settings.log_level,
        settings.log_format,
        settings.log_file or "—",
    )
    logger.info("─────────────────────────────────────────────")


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    detail: str


async def _probe(
    session_factory: Callable[[], aiohttp.ClientSession],
    name: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
) -> ProbeResult:
    """Одна проверка доступности эндпоинта. Никогда не бросает."""
    try:
        async with session_factory() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if 200 <= resp.status < 300:
                    return ProbeResult(name, True, f"HTTP {resp.status}")
                if resp.status in (401, 403):
                    return ProbeResult(
                        name, False, f"HTTP {resp.status} — ключ неверный/без доступа"
                    )
                return ProbeResult(name, False, f"HTTP {resp.status}")
    except (aiohttp.ClientError, TimeoutError) as exc:
        return ProbeResult(name, False, f"нет связи: {exc}")
    except Exception as exc:  # диагностика не должна ронять старт
        return ProbeResult(name, False, f"{type(exc).__name__}: {exc}")


async def probe_dependencies(
    settings: Settings,
    *,
    session_factory: Callable[[], aiohttp.ClientSession] | None = None,
    timeout_seconds: int = 8,
) -> list[ProbeResult]:
    """Проверяет связь с настроенными внешними сервисами и логирует результат
    (пункт «ошибки подключения»). Возвращает результаты — удобно для тестов."""
    if session_factory is None:

        def session_factory() -> aiohttp.ClientSession:
            return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds))

    probes: list[asyncio.Future] = []
    if settings.groq_api_key:
        probes.append(
            _probe(
                session_factory,
                "Groq (AI)",
                _GROQ_MODELS_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
        )
    if settings.tmdb_api_key:
        probes.append(
            _probe(
                session_factory,
                "TMDB",
                _TMDB_URL,
                params={"api_key": settings.tmdb_api_key},
            )
        )
    if settings.kinopoisk_api_key:
        probes.append(
            _probe(
                session_factory,
                "Кинопоиск",
                _KINOPOISK_URL,
                headers={"X-API-KEY": settings.kinopoisk_api_key},
                params={"query": "тест", "limit": "1"},
            )
        )

    if not probes:
        logger.info("Проверка связи: внешних сервисов не настроено — пропускаю")
        return []

    results: list[ProbeResult] = list(await asyncio.gather(*probes))
    for r in results:
        if r.ok:
            logger.info("Проверка связи: %s — ОК (%s)", r.name, r.detail)
        else:
            logger.warning("Проверка связи: %s — НЕ УДАЛОСЬ (%s)", r.name, r.detail)
    return results
