import asyncio
import logging
import random
import time
from datetime import UTC, date, datetime

import discord
from discord.ext import commands

from src.application.activity.di import ActivityContainer
from src.application.ai_chat.mood import MoodTracker
from src.application.ai_chat.service import ChatService
from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.domain.shared.holidays import HolidayCalendar

logger = logging.getLogger(__name__)

_FALLBACK_WELCOME = "Добро пожаловать, {name}. Осмотрись, правила почитай. ✂️👁🖤"
_FALLBACK_FAREWELL = "{name} ушёл. Бывает."
_FALLBACK_ALBUM_CAPTIONS = [
    "В коллекцию. ✂️👁🖤",
    "Это стоило сохранить.",
    "Экспонат. Не благодарите.",
    "Редкий момент, когда вы меня не разочаровали.",
]

# чаще раза в 15 минут активность одного участника в БД не пишем
_TOUCH_THROTTLE_SECONDS = 900


class ActivityCog(commands.Cog):
    """Живость персоны: приветствия/прощания, «скучаю» в тихом канале,
    случайные мысли, реакция на возвращение участника."""

    def __init__(
        self,
        bot: commands.Bot,
        container: ActivityContainer,
        relationship: RelationshipContainer,
        chat_service: ChatService | None,
        settings: Settings,
        mood: MoodTracker,
        guild_settings=None,
    ):
        self.bot = bot
        self.container = container
        self.relationship = relationship
        self.chat = chat_service
        self.settings = settings
        self.mood = mood
        self.gs = guild_settings
        self.calendar = HolidayCalendar(settings.holidays)
        self._holiday_announced: set[tuple[int, str]] = set()
        self._snapshot_taken: set[tuple[int, str]] = set()  # (guild_id, UTC-дата)
        self._main_last_activity: dict[int, float] = {}  # guild_id -> monotonic
        self._lonely_notified: set[int] = set()
        self._touch_throttle: dict[tuple[int, int], float] = {}
        self._mood_bump_throttle: dict[int, float] = {}  # guild_id -> monotonic
        self._voice_minutes: dict[tuple[int, int], float] = {}  # накопленные минуты в войсе
        # почасовой счётчик сообщений (guild_id, UTC-дата, час) -> кол-во; копится
        # в памяти и доливается в БД пачкой — хитмап/сообщения-в-день на панели
        self._msg_counts: dict[tuple[int, date, int], int] = {}
        # человеко-секунды присутствия в войсе по (guild, дата, час UTC) — второй
        # хитмап; копится в voice-тике, доливается тем же flush-циклом
        self._voice_seconds: dict[tuple[int, date, int], int] = {}
        self._loops_started = False
        self._tasks: list[asyncio.Task] = []

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    def _feature(self, guild_id: int, sub: str) -> bool:
        """Подфункция «Активности» активна: мастер модуля И сам подтумблер
        (наследование). Выкл на сервере через вкладку «Модули» панели. Флаг,
        отсутствующий в настройках (тест-заглушки), считаем включённым."""

        def on(key: str) -> bool:
            default = getattr(self.settings, key, True)
            value = self.gs.get(guild_id, key, default) if self.gs is not None else default
            return bool(value)

        return on("activity_enabled") and on(sub)

    async def cog_unload(self) -> None:
        # доливаем накопленные буферы ДО отмены циклов — иначе при hot-reload/
        # выгрузке кога теряется последний неслитый интервал
        await self.flush_activity()
        for task in self._tasks:
            task.cancel()

    # --- каналы ---

    def _welcome_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        return discord.utils.get(guild.text_channels, name=self.settings.welcome_channel)

    def _main_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        return discord.utils.get(guild.text_channels, name=self.settings.main_channel)

    async def _send(self, channel: discord.TextChannel | None, text: str) -> None:
        if channel is None or not text:
            return
        try:
            await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Не удалось отправить сообщение активности", exc_info=True)

    # --- приветствия и прощания ---

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        # авто-роль новичку (AUTO_ROLE; пусто = выключено)
        if self.settings.auto_role:
            role = discord.utils.get(member.guild.roles, name=self.settings.auto_role)
            if role is not None:
                try:
                    await member.add_roles(role, reason="Авто-роль при входе")
                except discord.HTTPException:
                    logger.warning("Не удалось выдать авто-роль", exc_info=True)
            else:
                logger.warning(
                    "Роль AUTO_ROLE не найдена на сервере",
                    extra={"role": self.settings.auto_role, "guild_id": member.guild.id},
                )
        if not self._feature(member.guild.id, "activity_greetings"):
            return  # приветствия выключены на сервере (авто-роль выше — оставляем)
        channel = self._welcome_channel(member.guild)
        text = _FALLBACK_WELCOME.format(name=member.display_name)
        if self.chat is not None:
            try:
                text = await self.chat.freeform_remark(
                    member.guild.id,
                    f"На сервер пришёл новый участник — {member.display_name}. "
                    "Поприветствуй его в своём стиле: сдержанно, с лёгкой иронией, без сюсюканья.",
                    datetime.now(UTC),
                    mood=self.mood.get(member.guild.id),
                )
            except Exception:
                logger.warning("AI-приветствие не сгенерировалось", exc_info=True)
        await self._send(channel, text)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        if not self._feature(member.guild.id, "activity_greetings"):
            return
        channel = self._welcome_channel(member.guild)
        text = _FALLBACK_FAREWELL.format(name=member.display_name)
        if self.chat is not None:
            try:
                text = await self.chat.freeform_remark(
                    member.guild.id,
                    f"Участник {member.display_name} покинул сервер. "
                    "Попрощайся одной фразой в своём стиле — сухо, без драмы.",
                    datetime.now(UTC),
                    mood=self.mood.get(member.guild.id),
                )
            except Exception:
                logger.warning("AI-прощание не сгенерировалось", exc_info=True)
        await self._send(channel, text)

    # --- активность участников и главного канала ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        # почасовой учёт (агрегат, без пользователя/текста) — до любых throttle,
        # чтобы считать КАЖДОЕ сообщение; доливается в БД flush-циклом
        stamp = datetime.now(UTC)
        mkey = (message.guild.id, stamp.date(), stamp.hour)
        self._msg_counts[mkey] = self._msg_counts.get(mkey, 0) + 1

        # человеческая активность в главном канале сбрасывает «одиночество»
        # и поднимает настроение (+2, не чаще раза в минуту)
        if getattr(message.channel, "name", None) == self.settings.main_channel:
            now_mono_main = time.monotonic()
            self._main_last_activity[message.guild.id] = now_mono_main
            self._lonely_notified.discard(message.guild.id)
            if now_mono_main - self._mood_bump_throttle.get(message.guild.id, 0.0) > 60:
                self._mood_bump_throttle[message.guild.id] = now_mono_main
                self.mood.bump(message.guild.id, +2)

        # возвращение участника после долгого отсутствия
        key = (message.guild.id, message.author.id)
        now_mono = time.monotonic()
        if now_mono - self._touch_throttle.get(key, 0.0) < _TOUCH_THROTTLE_SECONDS:
            return
        self._touch_throttle[key] = now_mono

        try:
            touch = await self.container.touch_activity.execute(
                message.author.id, message.guild.id, datetime.now(UTC)
            )
        except Exception:
            logger.exception("Не удалось обновить активность участника")
            return

        if (
            touch.returned_after_absence
            and self.chat is not None
            and self._feature(message.guild.id, "activity_return_remarks")
        ):
            # «не беспокоить» из анкеты — возвращение не комментируем
            rank = await self.chat.get_rank(message.author.id, message.guild.id)
            if rank.survey.contact == "quiet":
                return
            try:
                text = await self.chat.freeform_remark(
                    message.guild.id,
                    f"Участник {message.author.display_name} впервые написал после "
                    f"{touch.days_absent} дней отсутствия. Отметь его возвращение одной "
                    "фразой в своём стиле: заметила, но без сцен.",
                    datetime.now(UTC),
                    mood=self.mood.get(message.guild.id),
                )
                await message.channel.send(
                    text[:2000], allowed_mentions=discord.AllowedMentions.none()
                )
            except Exception:
                logger.warning("Реплика о возвращении не сгенерировалась", exc_info=True)

    # --- «Альбом Попоси»: сообщения с реакциями попадают в канал-альбом ---

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        if not self._feature(guild.id, "activity_album"):
            return
        album = discord.utils.get(guild.text_channels, name=self.settings.album_channel)
        if album is None or payload.channel_id == album.id:
            return
        # фильтр по конкретному эмодзи, если задан
        if (
            self.settings.album_reaction_emoji
            and str(payload.emoji) != self.settings.album_reaction_emoji
        ):
            return

        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        if message.author.bot:
            return

        if self.settings.album_reaction_emoji:
            counts = [
                r.count
                for r in message.reactions
                if str(r.emoji) == self.settings.album_reaction_emoji
            ]
        else:
            counts = [r.count for r in message.reactions]
        if not counts or max(counts) < self.settings.album_reaction_threshold:
            return

        # дедупликация в БД: одно сообщение — одна публикация, рестарт не обнуляет
        if not await self.container.try_mark_album.execute(guild.id, message.id, datetime.now(UTC)):
            return

        caption = random.choice(_FALLBACK_ALBUM_CAPTIONS)
        if self.chat is not None:
            try:
                caption = await self.chat.freeform_remark(
                    guild.id,
                    f"Сообщение участника {message.author.display_name} набрало "
                    f"{max(counts)} реакций и попадает в твой «Альбом» — коллекцию лучших "
                    f"моментов сервера. Текст сообщения: «{message.content[:300]}». "
                    "Подпиши экспонат одной фразой в своём кураторском стиле.",
                    datetime.now(UTC),
                    mood=self.mood.get(guild.id),
                )
            except Exception:
                logger.warning("Подпись для альбома не сгенерировалась", exc_info=True)

        embed = discord.Embed(
            description=message.content[:3900] or "*(без текста)*",
            color=0x9B59B6,
            timestamp=message.created_at,
        )
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url,
        )
        image_set = False
        for attachment in message.attachments:
            if not image_set and (attachment.content_type or "").startswith("image/"):
                embed.set_image(url=attachment.url)
                image_set = True
        embed.add_field(name="​", value=f"[Перейти к сообщению]({message.jump_url})", inline=False)
        embed.set_footer(text=f"{max(counts)} реакций • #{channel.name}")
        try:
            await album.send(
                content=caption[:1900],
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Не удалось отправить в альбом", exc_info=True)

    # --- фоновые циклы: скука и случайные мысли ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            self._main_last_activity.setdefault(guild.id, time.monotonic())
        if self._loops_started:
            return
        self._loops_started = True
        started = ["дрейф настроения", "календарь/ДР"]
        self._tasks.append(asyncio.create_task(self._mood_drift_loop()))
        self._tasks.append(asyncio.create_task(self._calendar_loop()))
        self._tasks.append(asyncio.create_task(self._snapshot_loop()))
        started.append("снапшот метрик")
        self._tasks.append(asyncio.create_task(self._activity_flush_loop()))
        started.append("учёт сообщений")
        # войс-очки можно включать/выключать пер-сервер через /config, поэтому
        # цикл запускаем всегда; в тике гильдии с 0 пропускаются
        try:
            # недосиженные минуты переживают рестарт (макс. потеря — один тик)
            loaded = await self.container.load_voice_progress.execute()
            self._voice_minutes.update(loaded)
            logger.info("Активность: войс-прогресс загружен (%d записей)", len(loaded))
        except Exception:
            logger.exception("Не удалось загрузить войс-прогресс")
        self._tasks.append(asyncio.create_task(self._voice_points_loop()))
        started.append("войс-очки")
        if self.chat is not None:
            self._tasks.append(asyncio.create_task(self._lonely_loop()))
            self._tasks.append(asyncio.create_task(self._random_thought_loop()))
            started += ["скука", "случайные мысли"]
        logger.info("Активность: циклы запущены — %s", ", ".join(started))

    # --- праздники и дни рождения ---

    async def _calendar_loop(self) -> None:
        while True:
            try:
                await self._calendar_tick()
            except Exception:
                logger.exception("Ошибка календарного цикла")
            await asyncio.sleep(1800)

    async def _calendar_tick(self) -> None:
        now = datetime.now(UTC)

        # праздник: объявление раз в день + подъём настроения
        holiday = self.calendar.holiday_name(now.date())
        if holiday:
            for guild in self.bot.guilds:
                if not self._feature(guild.id, "activity_holidays"):
                    continue
                key = (guild.id, now.date().isoformat())
                if key in self._holiday_announced:
                    continue
                self._holiday_announced.add(key)
                self.mood.bump(guild.id, +15)
                text = f"Сегодня {holiday}. Так и быть — сегодня я добрее обычного. ✂️👁🖤"
                if self.chat is not None:
                    try:
                        text = await self.chat.freeform_remark(
                            guild.id,
                            f"Сегодня {holiday}. Объяви об этом серверу в своём стиле — "
                            "празднично, но без сюсюканья.",
                            now,
                            mood=self.mood.get(guild.id),
                        )
                    except Exception:
                        logger.warning("Праздничное объявление не сгенерировалось", exc_info=True)
                text += f"\n-# 🎉 Весь день очки идут ×{self.settings.holiday_points_multiplier}"
                await self._send(self._main_channel(guild), text)

        # мягкое угасание очков при долгой неактивности
        decay = await self.relationship.decay_points.execute(now)
        if decay.decayed:
            logger.info(
                "Угасание очков: %d профилей, передач титула: %d",
                decay.decayed,
                len(decay.transfers),
            )

        # дни рождения: напоминание за N дней и поздравление в сам день
        events = await self.relationship.birthday_tick.execute(now)
        for guild_id, user_id in events.remind:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            if not self._feature(guild_id, "activity_birthdays"):
                continue
            channel = self._main_channel(guild)
            if channel is None:
                continue
            try:
                await channel.send(
                    f"🎂 Через {self.settings.birthday_remind_days} дня — день рождения "
                    f"<@{user_id}>. Готовьтесь. Я — уже.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass
        for guild_id, user_id in events.congratulate:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            if not self._feature(guild_id, "activity_birthdays"):
                continue
            channel = self._main_channel(guild)
            if channel is None:
                continue
            member = guild.get_member(user_id)
            name = member.display_name if member else "именинник"
            text = (
                f"🎂 <@{user_id}> — с днём рождения. Сегодня можешь даже немного поныть. Один раз."
            )
            if self.chat is not None:
                try:
                    generated = await self.chat.freeform_remark(
                        guild_id,
                        f"Сегодня день рождения у участника {name}. Поздравь его в своём "
                        "стиле — тепло, но без пафоса и открыточных штампов.",
                        datetime.now(UTC),
                        mood=self.mood.get(guild_id),
                    )
                    text = f"🎂 <@{user_id}> — {generated}"
                except Exception:
                    logger.warning("Поздравление не сгенерировалось", exc_info=True)
            try:
                await channel.send(
                    text[:2000], allowed_mentions=discord.AllowedMentions(users=True)
                )
            except discord.HTTPException:
                pass

    # --- суточный снапшот метрик (тренды на панели) ---

    async def _snapshot_loop(self) -> None:
        # частый опрос, но запись — раз в UTC-день на гильдию (дедуп ниже).
        # Работает и на SQLite: снапшоты не требуют NOTIFY, тренды копятся в dev.
        while True:
            try:
                await self._snapshot_tick()
            except Exception:
                logger.exception("Ошибка снапшота метрик")
            await asyncio.sleep(3600)

    async def _snapshot_tick(self) -> None:
        now = datetime.now(UTC)
        today = now.date()
        key_date = today.isoformat()
        for guild in self.bot.guilds:
            key = (guild.id, key_date)
            if key in self._snapshot_taken:
                continue
            try:
                await self.container.record_snapshot.execute(
                    guild.id, today, extra={"members": float(guild.member_count or 0)}
                )
                self._snapshot_taken.add(key)
            except Exception:
                logger.exception("Снапшот метрик гильдии %s не записан", guild.id)

    # --- почасовой учёт сообщений (хитмап/сообщения-в-день на панели) ---

    async def _activity_flush_loop(self) -> None:
        # доливаем накопленное раз в 2 минуты: сглаживает нагрузку на БД против
        # записи на каждое сообщение; макс. потеря при падении — один интервал
        while True:
            await asyncio.sleep(120)
            try:
                await self.flush_activity()
            except Exception:
                logger.exception("Ошибка доливки счётчиков активности")

    async def flush_activity(self) -> None:
        """Слить оба буфера (сообщения + войс) в БД. Вызывается циклом, при
        выгрузке кога и при остановке бота — чтобы не терять последний интервал."""
        await self._flush_message_counts()
        await self._flush_voice_seconds()

    async def _flush_message_counts(self) -> None:
        if not self._msg_counts:
            return
        # атомарно снимаем накопленное; новые сообщения пойдут в свежий словарь
        pending, self._msg_counts = self._msg_counts, {}
        by_guild: dict[int, dict[tuple[date, int], int]] = {}
        for (guild_id, bucket_date, hour), count in pending.items():
            by_guild.setdefault(guild_id, {})[(bucket_date, hour)] = count
        for guild_id, buckets in by_guild.items():
            try:
                await self.container.record_message_activity.execute(guild_id, buckets)
            except Exception:
                # не теряем несохранённое — возвращаем корзины гильдии обратно
                logger.exception("Счётчики сообщений гильдии %s не записаны", guild_id)
                for (bucket_date, hour), count in buckets.items():
                    key = (guild_id, bucket_date, hour)
                    self._msg_counts[key] = self._msg_counts.get(key, 0) + count

    async def _flush_voice_seconds(self) -> None:
        if not self._voice_seconds:
            return
        pending, self._voice_seconds = self._voice_seconds, {}
        by_guild: dict[int, dict[tuple[date, int], int]] = {}
        for (guild_id, bucket_date, hour), secs in pending.items():
            by_guild.setdefault(guild_id, {})[(bucket_date, hour)] = secs
        for guild_id, buckets in by_guild.items():
            try:
                await self.container.record_voice_activity.execute(guild_id, buckets)
            except Exception:
                logger.exception("Войс-присутствие гильдии %s не записано", guild_id)
                for (bucket_date, hour), secs in buckets.items():
                    key = (guild_id, bucket_date, hour)
                    self._voice_seconds[key] = self._voice_seconds.get(key, 0) + secs

    # --- очки за голосовые каналы ---

    _VOICE_TICK_SECONDS = 300  # шаг учёта присутствия

    async def _voice_points_loop(self) -> None:
        """+voice_points_per_hour очков за каждый полный час в войсе.
        Начисление через AwardPointUseCase — общий дневной потолок,
        заморозка и праздничный множитель работают как для сообщений.
        AFK-канал и участники «в наушниках» (deaf) не считаются."""
        while True:
            await asyncio.sleep(self._VOICE_TICK_SECONDS)
            try:
                await self._voice_points_tick()
            except Exception:
                logger.exception("Ошибка начисления очков за войс")

    def _accumulate_voice_presence(self, now: datetime) -> None:
        """Копит человеко-секунды присутствия в войсе для хитмапа. Считается
        независимо от войс-очков (это «время на сервере», а не награда): все живые
        участники в не-AFK голосовых/stage-каналах, за минувший тик."""
        bucket = (now.date(), now.hour)
        for guild in self.bot.guilds:
            for channel in [*guild.voice_channels, *guild.stage_channels]:
                if guild.afk_channel is not None and channel.id == guild.afk_channel.id:
                    continue
                present = sum(1 for m in channel.members if not m.bot and m.voice is not None)
                if present:
                    key = (guild.id, *bucket)
                    self._voice_seconds[key] = (
                        self._voice_seconds.get(key, 0) + present * self._VOICE_TICK_SECONDS
                    )

    async def _voice_points_tick(self) -> None:
        now = datetime.now(UTC)
        self._accumulate_voice_presence(now)  # присутствие для хитмапа — до гейтов очков
        changed: dict[tuple[int, int], float] = {}
        for guild in self.bot.guilds:
            if not self._feature(guild.id, "activity_voice_points"):
                continue  # войс-очки выключены на сервере (вкладка «Модули»)
            per_hour = self._cfg(guild.id, "voice_points_per_hour")
            if per_hour <= 0:
                continue  # войс-очки выключены на этом сервере
            for channel in [*guild.voice_channels, *guild.stage_channels]:
                if guild.afk_channel is not None and channel.id == guild.afk_channel.id:
                    continue
                for member in channel.members:
                    if member.bot:
                        continue
                    state = member.voice
                    if state is None or state.deaf or state.self_deaf:
                        continue  # не слушает — не «сидит с нами»
                    key = (guild.id, member.id)
                    minutes = self._voice_minutes.get(key, 0.0) + self._VOICE_TICK_SECONDS / 60
                    if minutes >= 60:
                        minutes -= 60
                        await self.relationship.award_point.execute(
                            member.id,
                            guild.id,
                            0,
                            now,
                            base_amount=per_hour,
                        )
                    self._voice_minutes[key] = minutes
                    changed[key] = minutes
        try:
            # accrued: каждый из changed отсидел ровно тик — копим суммарное время
            await self.container.save_voice_progress.execute(
                changed, accrued_minutes=self._VOICE_TICK_SECONDS / 60
            )
        except Exception:
            logger.exception("Не удалось сохранить войс-прогресс")

    async def _mood_drift_loop(self) -> None:
        # тишина >2 часов тянет настроение к 20, активность — к 75
        while True:
            await asyncio.sleep(600)
            now = time.monotonic()
            for guild in self.bot.guilds:
                last = self._main_last_activity.get(guild.id)
                active = last is not None and now - last < 2 * 3600
                self.mood.drift(guild.id, active)

    async def _lonely_loop(self) -> None:
        while True:
            await asyncio.sleep(600)
            now = time.monotonic()
            for guild in self.bot.guilds:
                last = self._main_last_activity.get(guild.id)
                if last is None or guild.id in self._lonely_notified:
                    continue
                if not self._feature(guild.id, "activity_lonely"):
                    continue
                lonely_hours = self._cfg(guild.id, "lonely_hours")
                if now - last < lonely_hours * 3600:
                    continue
                self._lonely_notified.add(guild.id)
                try:
                    text = await self.chat.freeform_remark(
                        guild.id,
                        f"В канале уже больше {lonely_hours} часов никто не пишет. "
                        "Напиши одну реплику в пустоту в своём стиле — тебе слегка не хватает "
                        "этих людей, но признаваться в этом прямо ты не станешь.",
                        datetime.now(UTC),
                        mood=self.mood.get(guild.id),
                    )
                    await self._send(self._main_channel(guild), text)
                except Exception:
                    logger.warning("Реплика одиночества не сгенерировалась", exc_info=True)

    async def _random_thought_loop(self) -> None:
        while True:
            hours = random.uniform(
                self.settings.random_thought_min_hours,
                self.settings.random_thought_max_hours,
            )
            await asyncio.sleep(hours * 3600)
            for guild in self.bot.guilds:
                last = self._main_last_activity.get(guild.id)
                # только если в канале была активность за последний час
                if last is None or time.monotonic() - last > 3600:
                    continue
                if not self._feature(guild.id, "activity_random_thoughts"):
                    continue
                try:
                    text = await self.chat.freeform_remark(
                        guild.id,
                        "Напиши одну случайную мысль или наблюдение в своём характере — "
                        "про дождь, кофе, работу над артом, игры, Токио. Как будто просто "
                        "захотелось сказать вслух. Без обращения к кому-то конкретному.",
                        datetime.now(UTC),
                        mood=self.mood.get(guild.id),
                    )
                    await self._send(self._main_channel(guild), text)
                except Exception:
                    logger.warning("Случайная мысль не сгенерировалась", exc_info=True)
