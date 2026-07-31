import logging
import time
from collections import deque

import discord
from discord.ext import commands

from src.application.di.root_container import RootContainer

logger = logging.getLogger(__name__)

_CONNECTION_WINDOW = 3600.0  # окно счётчиков разрывов/возобновлений, секунды


class PoposyaBot(commands.Bot):
    def __init__(self, container: RootContainer):
        intents = discord.Intents.default()
        # чтение текста сообщений для триггера общения (упоминание/реплай);
        # привилегированный интент — включить в Discord Developer Portal
        intents.message_content = True
        # приветствия/прощания (on_member_join/remove) — тоже привилегированный
        # интент (Server Members Intent в Developer Portal)
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.container = container
        self._was_connected = False  # для дедупликации логов разрыва связи
        # --- наблюдаемость соединения (метрики для WARDEN) ---
        # окно на час: важна не общая сумма разрывов с запуска, а то, штормит ли
        # соединение прямо сейчас
        self._disconnect_times: deque[float] = deque()
        self._resume_times: deque[float] = deque()
        self._ready_at: float | None = None
        # набор когов, поднявшихся при старте; часть подключается условно
        # (ai_chat — только с ключом Groq), поэтому эталон снимается фактом
        # успешной загрузки, а не константой
        self._expected_cogs: frozenset[str] = frozenset()

    async def close(self) -> None:
        # перед остановкой сливаем накопленные буферы активности (сообщения/войс):
        # close() НЕ вызывает cog_unload, поэтому доливаем здесь, пока БД жива —
        # иначе редеплой/остановка теряют последний неслитый интервал хитмапа
        cog = self.get_cog("ActivityCog")
        flush = getattr(cog, "flush_activity", None)
        if flush is not None:
            try:
                await flush()
            except Exception:
                logger.exception("Не удалось слить буферы активности при остановке")
        await super().close()

    async def setup_hook(self) -> None:
        from src.application.ai_chat.mood import MoodTracker
        from src.infrastructure.cinema.kinopoisk import KinopoiskClient
        from src.infrastructure.cinema.provider import FallbackMovieSearch
        from src.infrastructure.cinema.tmdb import TmdbClient
        from src.infrastructure.discord import accent
        from src.infrastructure.discord.cogs.activity import ActivityCog
        from src.infrastructure.discord.cogs.ai_chat import AIChatCog
        from src.infrastructure.discord.cogs.cinema import CinemaCog
        from src.infrastructure.discord.cogs.finds import FindsCog
        from src.infrastructure.discord.cogs.fun import FunCog
        from src.infrastructure.discord.cogs.moderation import ModerationCog
        from src.infrastructure.discord.cogs.music import MusicCog
        from src.infrastructure.discord.cogs.relationship import RelationshipCog
        from src.infrastructure.discord.error_handler import setup_error_handler
        from src.infrastructure.discord.role_sync import RoleSyncService

        # единая сеть безопасности для необработанных ошибок слеш-команд
        setup_error_handler(self, self.container.persona)

        # accent-цвет эмбедов — из персоны сервера (мягкая личность, P3)
        accent.set_persona_service(self.container.persona)

        role_sync = RoleSyncService(
            self,
            self.container.settings.relationship_role_names,
            self.container.guild_settings,
        )
        mood = MoodTracker()

        gs = self.container.guild_settings
        music_cog = MusicCog(self, self.container.music, gs, persona=self.container.persona)
        await self.add_cog(music_cog)
        # presence из персоны: строки статуса берутся из библиотек, а по NOTIFY
        # (панель сменила персону/присвоение) статус переустанавливается сразу
        persona = self.container.persona
        music_cog.presence.set_lines_provider(persona.presence_lines)
        persona.add_reload_hook(music_cog.presence.refresh)
        await self.add_cog(
            ModerationCog(
                self, self.container.moderation, self.container.settings, gs, persona=persona
            )
        )
        await self.add_cog(
            ActivityCog(
                self,
                self.container.activity,
                self.container.relationship,
                self.container.ai_chat.chat_service,
                self.container.settings,
                mood,
                gs,
                persona=persona,
            )
        )
        await self.add_cog(
            FunCog(
                self,
                self.container.activity,
                self.container.relationship,
                self.container.ai_chat.chat_service,
                self.container.settings,
                finds=self.container.finds,
                music=self.container.music,
                cinema=self.container.cinema,
                guild_settings=gs,
                persona=persona,
            )
        )
        await self.add_cog(
            RelationshipCog(
                self,
                self.container.relationship,
                role_sync,
                self.container.event_bus,
                self.container.settings,
                gs,
                persona=persona,
            )
        )
        await self.add_cog(
            FindsCog(
                self, self.container.finds, self.container.settings, mood, gs, persona=persona
            )
        )
        tmdb = TmdbClient(self.container.settings.tmdb_api_key)
        kinopoisk = KinopoiskClient(self.container.settings.kinopoisk_api_key)
        if self.container.settings.movie_provider == "kinopoisk":
            movie_search = FallbackMovieSearch(kinopoisk, tmdb)
        else:
            movie_search = FallbackMovieSearch(tmdb, kinopoisk)
        await self.add_cog(
            CinemaCog(
                self,
                self.container.cinema,
                self.container.relationship,
                self.container.ai_chat.chat_service,
                self.container.settings,
                mood,
                movie_search,
                gs,
                persona=persona,
            )
        )
        from src.infrastructure.discord.cogs.autorole import AutoRoleCog
        from src.infrastructure.discord.cogs.config import ConfigCog
        from src.infrastructure.discord.cogs.git import GitCog
        from src.infrastructure.discord.cogs.introduce import IntroduceCog
        from src.infrastructure.discord.cogs.steam import SteamCog
        from src.infrastructure.discord.cogs.log_relay import LogRelayCog
        from src.infrastructure.discord.cogs.role_mirror import RoleMirrorCog
        from src.infrastructure.discord.cogs.secret_room import SecretRoomCog
        from src.infrastructure.discord.cogs.staykick import StayKickCog
        from src.infrastructure.discord.cogs.tempvoice import TempVoiceCog

        await self.add_cog(LogRelayCog(self, self.container.settings))
        await self.add_cog(RoleMirrorCog(self, self.container.roles))
        await self.add_cog(AutoRoleCog(self, gs))
        await self.add_cog(ConfigCog(self, self.container.guild_settings, persona=persona))
        await self.add_cog(GitCog(self, self.container.repos, self.container.settings, gs, persona=persona))
        await self.add_cog(SteamCog(self, self.container.steam, self.container.settings, gs, persona=persona))
        await self.add_cog(
            StayKickCog(
                self, self.container.staykick, self.container.settings, gs, persona=persona
            )
        )
        await self.add_cog(
            TempVoiceCog(self, self.container.tempvoice, self.container.settings, gs, persona=persona)
        )

        await self.add_cog(
            IntroduceCog(
                self,
                self.container.relationship,
                role_sync,
                self.container.settings,
                gs,
                persona=persona,
            )
        )
        await self.add_cog(
            SecretRoomCog(
                self,
                self.container.relationship,
                self.container.settings,
                self.container.event_bus,
                gs,
                persona=persona,
            )
        )
        if self.container.ai_chat.chat_service is not None:
            await self.add_cog(
                AIChatCog(
                    self,
                    self.container.ai_chat.chat_service,
                    self.container.settings,
                    role_sync,
                    self.container.event_bus,
                    mood,
                    gs,
                    persona=persona,
                )
            )
        else:
            logger.warning("Ког ai_chat ПРОПУЩЕН: AI выключен (нет GROQ_API_KEY)")

        names = sorted(c.__class__.__name__.replace("Cog", "").lower() for c in self.cogs.values())
        logger.info("Коги подключены (%d): %s", len(self.cogs), ", ".join(names))
        # эталон для health-метрики: всё, что поднялось здесь, обязано остаться
        self._expected_cogs = frozenset(self.cogs.keys())

        dev_guild_id = self.container.settings.dev_guild_id
        if dev_guild_id:
            guild = discord.Object(id=dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(
                "Слеш-команды синхронизированы с dev-гильдией", extra={"guild_id": dev_guild_id}
            )
        else:
            await self.tree.sync()
            logger.info("Слеш-команды синхронизированы глобально (может занять до часа)")

    async def on_ready(self) -> None:
        self._was_connected = True
        self._ready_at = time.monotonic()
        logger.info(
            "Бот запущен как %s · серверов: %d",
            self.user,
            len(self.guilds),
            extra={"bot_user": str(self.user), "guilds": len(self.guilds)},
        )

    # --- жизненный цикл соединения с Discord ---

    async def on_connect(self) -> None:
        logger.info("Соединение с Discord установлено")

    async def on_disconnect(self) -> None:
        # on_disconnect дёргается и при штатных переподключениях; логируем
        # только первый разрыв, пока связь не восстановится (без спама).
        # Счётчик, в отличие от лога, считает все разрывы: частые «штатные»
        # переподключения — это и есть картина деградирующей связи
        self._disconnect_times.append(time.monotonic())
        self._trim_connection_windows()
        if self._was_connected:
            self._was_connected = False
            logger.warning("Связь с Discord потеряна — переподключаюсь…")

    async def on_resumed(self) -> None:
        self._was_connected = True
        self._resume_times.append(time.monotonic())
        self._trim_connection_windows()
        logger.info("Связь с Discord восстановлена (сессия возобновлена)")

    # --- метрики для внешнего мониторинга ---

    def _trim_connection_windows(self) -> None:
        cutoff = time.monotonic() - _CONNECTION_WINDOW
        for window in (self._disconnect_times, self._resume_times):
            while window and window[0] < cutoff:
                window.popleft()

    def gateway_stats(self) -> dict:
        """Состояние связи с Discord. `latency` — время подтверждения heartbeat
        шлюзом, главный признак деградации до того, как бот перестанет отвечать.
        NaN до первого heartbeat (discord.py отдаёт inf/nan) отдаём как None,
        иначе json_response выдаст невалидный JSON с литералом NaN."""
        self._trim_connection_windows()
        latency = self.latency
        latency_ms = round(latency * 1000, 1) if latency == latency and latency != float("inf") else None
        return {
            "ready": self.is_ready(),
            "latency_ms": latency_ms,
            "guilds": len(self.guilds),
            "voice_clients": len(self.voice_clients),
            "seconds_since_ready": (
                round(time.monotonic() - self._ready_at, 1) if self._ready_at is not None else None
            ),
            "disconnects_last_hour": len(self._disconnect_times),
            "resumes_last_hour": len(self._resume_times),
        }

    def cogs_stats(self) -> dict:
        """Расхождение с эталоном значит, что модуль отвалился на ходу: бот жив,
        а часть функций молча исчезла."""
        loaded = frozenset(self.cogs.keys())
        return {
            "loaded": len(loaded),
            "expected": len(self._expected_cogs),
            "missing": sorted(self._expected_cogs - loaded),
        }
