import asyncio
import logging
import random
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from src.application.ai_chat.mood import MoodTracker
from src.application.ai_chat.service import ChatRequest, ChatService
from src.config import Settings
from src.domain.ai_chat.exceptions import AIProviderError
from src.domain.events.bus import IEventBus
from src.domain.music.events import TrackStarted
from src.infrastructure.discord.role_sync import RoleSyncService

logger = logging.getLogger(__name__)

_ERROR_REPLIES = [
    "Сегодня без разговоров. Не в настроении.",
    "Помолчим. Так тоже можно. 🖤",
]

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
    ):
        self.bot = bot
        self.service = service
        self.settings = settings
        self.role_sync = role_sync
        self.mood = mood
        self._event_cooldowns: dict[int, float] = {}  # channel_id -> monotonic
        self._background: set[asyncio.Task] = set()
        self._sweep_task: asyncio.Task | None = None
        event_bus.subscribe(TrackStarted, self._on_track_started)

    def cog_unload(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
        for task in self._background:
            task.cancel()

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
                now = datetime.now(timezone.utc)
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
        if not self._is_addressed_to_bot(message):
            return

        # оскорбление в адрес бота бьёт по настроению
        lowered = message.content.lower()
        if any(word in lowered for word in self.settings.bot_insult_words):
            self.mood.bump(message.guild.id, -5)

        now = datetime.now(timezone.utc)
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
            await message.reply(
                random.choice(_ERROR_REPLIES),
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
            and award.points % self.settings.ai_notes_update_every == 0
        ):
            self._spawn(self.service.refresh_notes(request, award, reply.text))

    def _is_addressed_to_bot(self, message: discord.Message) -> bool:
        if self.bot.user in message.mentions:
            return True
        ref = message.reference
        if ref is not None and isinstance(ref.resolved, discord.Message):
            return ref.resolved.author.id == self.bot.user.id
        return False

    def _clean_content(self, message: discord.Message) -> str:
        content = message.content
        for pattern in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
            content = content.replace(pattern, "")
        return content.strip()

    async def _collect_history(self, message: discord.Message) -> list[tuple[str, str]]:
        history: list[tuple[str, str]] = []
        try:
            async for msg in message.channel.history(
                limit=self.settings.ai_context_messages, before=message
            ):
                text = msg.content.strip()
                if text:
                    history.append((msg.author.display_name, text[:300]))
        except discord.HTTPException:
            pass
        history.reverse()
        return history

    # --- событийный триггер: комментарий к включённому треку ---

    async def _on_track_started(self, event: TrackStarted) -> None:
        if event.channel_id == 0:
            return
        # анкета: «не беспокоить» — молчим, «хочу внимания» — шанс удвоен
        rank = await self.service.get_rank(event.requested_by, event.guild_id)
        if rank.survey.contact == "quiet":
            return
        chance = self.settings.ai_event_comment_chance
        if rank.survey.contact == "attention":
            chance *= 2
        if random.random() > chance:
            return
        last = self._event_cooldowns.get(event.channel_id, 0.0)
        if time.monotonic() - last < self.settings.ai_event_comment_cooldown:
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
                instruction=f"{display} включил в голосовом канале трек «{event.title}».",
                now=datetime.now(timezone.utc),
            )
            await channel.send(comment[:2000], allowed_mentions=discord.AllowedMentions.none())
        except AIProviderError:
            logger.debug("Комментарий к треку не сгенерировался", exc_info=True)
        except Exception:
            logger.exception("Ошибка комментария к треку")
