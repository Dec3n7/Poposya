import asyncio
import functools
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from typing import cast

import discord
from discord.ext import commands

from src.application.ai_chat.mood import MoodTracker
from src.application.ai_chat.service import ChatRequest, ChatService
from src.config import Settings
from src.domain.ai_chat.exceptions import AIProviderError
from src.domain.events.bus import IEventBus
from src.domain.music.events import TrackStarted
from src.infrastructure.discord.channels import is_designated_main
from src.infrastructure.discord.feature_flags import flag_on
from src.infrastructure.discord.role_sync import RoleSyncService
from src.infrastructure.discord.scheduler import DeferredScheduler
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

# как часто подчищать протухшие сессии диалогов и кулдауны комментариев
_SWEEP_INTERVAL_SECONDS = 600


class AIChatCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        service: ChatService,
        settings: Settings,
        role_sync: RoleSyncService,
        event_bus: IEventBus,
        mood: MoodTracker,
        guild_settings=None,
        persona=None,
    ):
        self.bot = bot
        self.service = service
        self.settings = settings
        self.gs = guild_settings
        self.role_sync = role_sync
        self.mood = mood
        # голос кога — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()
        self._event_cooldowns: dict[int, float] = {}  # channel_id -> monotonic
        self._background: set[asyncio.Task] = set()
        self._sweep_task: asyncio.Task | None = None
        # пассивное вклинивание: дебаунс по паузе (таймер на канал) + кулдаун
        self._chime_scheduler = DeferredScheduler("chime")
        self._chime_cooldowns: dict[int, float] = {}  # channel_id -> monotonic
        # subscribe ждёт Callable[[DomainEvent], ...]; хендлер сужен под своё
        # событие — диспетч по типу гарантирует правильный аргумент
        event_bus.subscribe(TrackStarted, self._on_track_started)  # type: ignore[arg-type]

    def _cfg(self, guild_id: int, key: str):
        """Пер-серверное значение AI-настройки (override /config или глобальный дефолт)."""
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    def _p(self, guild_id: int, key: str, **vars: object) -> str:
        """Строковая фраза каталога персоны сервера."""
        return str(self.persona.phrase(guild_id, key, **vars))

    async def _pick(self, guild_id: int, key: str) -> str:
        """Случайный элемент фразы-списка (через render_block: режим/random)."""
        return await self.persona.render_block(guild_id, key, None) or ""

    def _feature(self, guild_id: int, sub: str | None = None) -> bool:
        """Модуль «AI-чат» (мастер) и подфункция (вкладка «Модули»). Флаг,
        отсутствующий в настройках (тест-заглушки), считаем включённым."""
        if not flag_on(self.settings, self.gs, guild_id, "ai_chat_enabled"):
            return False
        return flag_on(self.settings, self.gs, guild_id, sub) if sub is not None else True

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        if self._sweep_task is not None:
            self._sweep_task.cancel()
        for task in self._background:
            task.cancel()
        self._chime_scheduler.cancel_all()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # один фоновый цикл на процесс: on_ready может прийти повторно при
        # переподключении к Discord — второй раз не запускаем
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._sweep_loop())

    async def _sweep_loop(self) -> None:
        """Бортит утечку памяти: протухшие сессии диалогов резюмируются и
        удаляются, истёкшие кулдауны комментариев к трекам выбрасываются."""
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            try:
                now = datetime.now(UTC)
                for guild_id, user_id, display, exchanges in self.service.evict_stale_sessions(now):
                    self._spawn(
                        self.service.summarize_dialog(guild_id, user_id, display, exchanges, now)
                    )
                self._prune_event_cooldowns()
            except Exception:
                logger.exception("Цикл чистки ai_chat упал на проходе")

    def _prune_event_cooldowns(self) -> None:
        horizon = self.settings.ai_event_comment_cooldown
        mono = time.monotonic()
        for channel_id in [cid for cid, ts in self._event_cooldowns.items() if mono - ts > horizon]:
            del self._event_cooldowns[channel_id]

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- реактивный триггер: упоминание или реплай ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not self._feature(message.guild.id):
            return  # модуль AI-чата выключен на сервере — молчим совсем
        if not self._is_addressed_to_bot(message):
            # не к боту — возможно, повод пассивно вклиниться в разговор
            # (пассив дополнительно гейтится подфлагом ai_passive_enabled внутри)
            self._consider_passive(message)
            return
        if not self._feature(message.guild.id, "ai_reactive"):
            return  # ответы на обращения выключены (пассив может работать отдельно)

        # оскорбление в адрес бота бьёт по настроению
        lowered = message.content.lower()
        if any(word in lowered for word in self.settings.bot_insult_words):
            self.mood.bump(message.guild.id, -5)

        now = datetime.now(UTC)
        request = ChatRequest(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            channel_name=getattr(message.channel, "name", "канал"),
            user_id=message.author.id,
            user_display=message.author.display_name,
            content=self._clean_content(message),
            history=await self._collect_history(message),
        )

        try:
            async with message.channel.typing():
                reply = await self.service.respond(
                    request, now, mood=self.mood.get(message.guild.id)
                )
        except AIProviderError:
            logger.warning("AI-провайдер не ответил", exc_info=True)
            error_reply = await self._pick(message.guild.id, "ai_chat.error_replies")
            if error_reply:
                await message.reply(
                    error_reply,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return
        except Exception:
            logger.exception("Ошибка обработки сообщения ai_chat")
            return

        await message.reply(reply.text[:2000], allowed_mentions=discord.AllowedMentions.none())
        self.mood.bump(message.guild.id, +1)  # ответ на упоминание

        # прошлый диалог закончился — сохраняем резюме в память
        if reply.stale_session:
            self._spawn(
                self.service.summarize_dialog(
                    message.guild.id,
                    message.author.id,
                    message.author.display_name,
                    reply.stale_session,
                    now,
                )
            )

        award = reply.award
        # сверка Discord-роли с очками при каждом сообщении (самовосстановление)
        self._spawn(self.role_sync.sync_member(message.guild, message.author.id, award.role_index))
        # периодическое обновление заметки о пользователе
        if (
            not reply.rate_limited
            and award.point_awarded
            and award.points % self._cfg(message.guild.id, "ai_notes_update_every") == 0
        ):
            self._spawn(self.service.refresh_notes(request, award, reply.text))

    def _is_addressed_to_bot(self, message: discord.Message) -> bool:
        if self.bot.user in message.mentions:
            return True
        ref = message.reference
        if ref is not None and isinstance(ref.resolved, discord.Message):
            return ref.resolved.author.id == cast(discord.ClientUser, self.bot.user).id
        return False

    def _clean_content(self, message: discord.Message) -> str:
        content = message.content
        uid = cast(discord.ClientUser, self.bot.user).id  # после ready bot.user есть
        for pattern in (f"<@{uid}>", f"<@!{uid}>"):
            content = content.replace(pattern, "")
        return content.strip()

    async def _collect_history(self, message: discord.Message) -> list[tuple[str, str]]:
        history: list[tuple[str, str]] = []
        try:
            async for msg in message.channel.history(
                limit=self._cfg(cast(discord.Guild, message.guild).id, "ai_context_messages"),
                before=message,
            ):
                text = msg.content.strip()
                if text:
                    history.append((msg.author.display_name, text[:300]))
        except discord.HTTPException:
            pass
        history.reverse()
        return history

    # --- пассивное вклинивание в разговоры (Попося сама решает встрять) ---

    def _consider_passive(self, message: discord.Message) -> None:
        """На обычное сообщение (не к боту) взводим/сдвигаем дебаунс-таймер:
        решение примем на паузе в разговоре, а не на каждой реплике."""
        guild = message.guild
        if guild is None:  # листенер: guild-only не гарантирован на уровне сигнатуры
            return
        if not self._cfg(guild.id, "ai_passive_enabled"):
            return
        if self._cfg(guild.id, "ai_passive_only_main_channel"):
            if not is_designated_main(
                message.channel,
                self._cfg(guild.id, "main_channel_id"),
                self.settings.main_channel,
            ):
                return
        channel_id = message.channel.id
        # в кулдауне — таймер не взводим (всё равно бы промолчала)
        cooldown = self._cfg(guild.id, "ai_passive_cooldown_minutes") * 60
        if time.monotonic() - self._chime_cooldowns.get(channel_id, 0.0) < cooldown:
            return
        when = datetime.now(UTC) + timedelta(seconds=self.settings.ai_passive_debounce_seconds)
        self._chime_scheduler.schedule(
            f"chime:{channel_id}",
            when,
            functools.partial(self._try_chime, guild.id, channel_id),
        )

    async def _try_chime(self, guild_id: int, channel_id: int) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        messageable = cast(discord.abc.Messageable, channel)
        history, users = await self._collect_passive_window(messageable)
        if not history or users < self._cfg(guild_id, "ai_passive_min_users"):
            return
        # повторная проверка кулдауна: пока ждали паузу, она могла заговорить
        cooldown = self._cfg(guild_id, "ai_passive_cooldown_minutes") * 60
        if time.monotonic() - self._chime_cooldowns.get(channel_id, 0.0) < cooldown:
            return
        text = await self.service.maybe_chime(
            guild_id,
            history,
            datetime.now(UTC),
            mood=self.mood.get(guild_id),
            min_confidence=self._cfg(guild_id, "ai_passive_confidence_min"),
        )
        if not text:
            return
        try:
            await messageable.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())
            self._chime_cooldowns[channel_id] = time.monotonic()
        except discord.HTTPException:
            logger.warning("Не удалось отправить пассивную реплику", exc_info=True)

    async def _collect_passive_window(
        self, channel: discord.abc.Messageable
    ) -> tuple[list[tuple[str, str]], int]:
        """Последние сообщения канала (без реплик бота) + число разных людей."""
        history: list[tuple[str, str]] = []
        authors: set[int] = set()
        try:
            async for msg in channel.history(limit=self.settings.ai_passive_max_messages):
                if msg.author.bot:
                    continue
                text = msg.content.strip()
                if text:
                    history.append((msg.author.display_name, text[:300]))
                    authors.add(msg.author.id)
        except discord.HTTPException:
            return [], 0
        history.reverse()
        return history, len(authors)

    # --- событийный триггер: комментарий к включённому треку ---

    async def _on_track_started(self, event: TrackStarted) -> None:
        if event.channel_id == 0:
            return
        if not self._feature(event.guild_id, "ai_event_comments"):
            return  # комментарии к трекам выключены на сервере
        # анкета: «не беспокоить» — молчим, «хочу внимания» — шанс удвоен
        rank = await self.service.get_rank(event.requested_by, event.guild_id)
        if rank.survey.contact == "quiet":
            return
        chance = self._cfg(event.guild_id, "ai_event_comment_chance")
        if rank.survey.contact == "attention":
            chance *= 2
        if random.random() > chance:
            return
        last = self._event_cooldowns.get(event.channel_id, 0.0)
        if time.monotonic() - last < self._cfg(event.guild_id, "ai_event_comment_cooldown"):
            return
        self._event_cooldowns[event.channel_id] = time.monotonic()

        channel = self.bot.get_channel(event.channel_id)
        if channel is None:
            return
        guild = getattr(channel, "guild", None)
        if guild is None:
            return
        member = guild.get_member(event.requested_by)
        display = member.display_name if member else "кто-то"
        try:
            comment = await self.service.comment_on_event(
                guild_id=event.guild_id,
                user_id=event.requested_by,
                user_display=display,
                instruction=self._p(
                    event.guild_id, "ai_chat.event_track", display=display, title=event.title
                ),
                now=datetime.now(UTC),
            )
            await cast(discord.abc.Messageable, channel).send(
                comment[:2000], allowed_mentions=discord.AllowedMentions.none()
            )
        except AIProviderError:
            logger.debug("Комментарий к треку не сгенерировался", exc_info=True)
        except Exception:
            logger.exception("Ошибка комментария к треку")
