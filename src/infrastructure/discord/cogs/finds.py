import asyncio
import logging
import random
import time
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from src.application.ai_chat.mood import MoodTracker
from src.application.finds.di import FindsContainer
from src.application.finds.use_cases import ClaimResult
from src.config import Settings
from src.domain.finds.catalog import RARITY_EMOJI, RARITY_LABELS, season_for_month
from src.domain.finds.entities import NightFind, Rarity

logger = logging.getLogger(__name__)

_EMBED_COLOR = 0x9B59B6
# «только если в основном канале была активность» — окно, в котором сервер
# считается живым для спавна находки
_ACTIVITY_WINDOW_SECONDS = 12 * 3600

_OPENERS = [
    "Я сегодня гуляла под мелким дождём около {place}. Заметила кое-что… интересное.",
    "Опять бродила ночью. Нашла кое-что, что может тебе понравиться.",
    "Иногда город сам подкидывает подарки. Сегодня был такой момент.",
    "Не удержалась. В старом районе кое-что блеснуло под фонарём.",
]
_FAIL_LINES = [
    "Пусто? Как неожиданно. Хотя… я же не говорила, что оно будет лежать и ждать именно тебя.",
    "Ничего? Жаль. В следующий раз смотри внимательнее.",
    "Пф. Пустые руки. Не расстраивайся, я тоже иногда возвращаюсь ни с чем. Хотя и реже.",
]
# тепло реакции растёт с уровнем отношений
_SUCCESS_LOW = "Хорошо. Ты его действительно забрал. Не ожидала."
_SUCCESS_MID = "Неплохо. Это даже мило с твоей стороны."
_SUCCESS_HIGH = "…Ты правда пошёл туда ради этого? Хм. Спасибо. ✂️👁🖤"
_SUCCESS_LEGENDARY = "…Стой. Ты нашёл ЭТО? Я запомню этот момент. Надолго. ✂️👁🖤"

_GIFT_LINES = {
    Rarity.COMMON: "Ты серьёзно решил мне это подарить? …Ладно. Я приму. И запомню.",
    Rarity.UNCOMMON: "Хм. У тебя неплохой вкус. Приму. И да — я запомню.",
    Rarity.RARE: "…Это правда мне? Такое я не забываю. Спасибо. 🖤",
    Rarity.LEGENDARY: "…Я не знаю, что сказать. Это со мной навсегда. Как и этот момент. ✂️👁🖤",
}

_WALK_SUCCESS = [
    "Прогулялась. Дождь, фонари, один подозрительный кот. И вот — держи.",
    "Сходила. Ради тебя, между прочим. Смотри, что принесла.",
]
_WALK_FAIL = [
    "Прогулялась впустую. Город сегодня жадный. Бывает.",
    "Ничего. Даже кот куда-то делся. Не мой вечер — и не твой.",
]


def _ts(dt: datetime) -> str:
    """Discord-таймштамп «через N часов»."""
    return f"<t:{int(dt.timestamp())}:R>"


class FindClaimView(discord.ui.View):
    """Persistent-кнопка «Сходить туда»: custom_id фиксированный, находка
    ищется по message_id в БД — кнопка переживает рестарт бота."""

    def __init__(self, cog: "FindsCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Сходить туда",
        emoji="👣",
        style=discord.ButtonStyle.primary,
        custom_id="poposya:nightfind:claim",
    )
    async def claim_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_claim(interaction)


class FindsCog(commands.Cog):
    """«Ночные находки Попоси»: она гуляет по Токио, находит предметы,
    пользователи ходят их забирать. Очки — те же очки отношений."""

    def __init__(
        self,
        bot: commands.Bot,
        container: FindsContainer,
        settings: Settings,
        mood: MoodTracker,
        guild_settings=None,
    ):
        self.bot = bot
        self.finds = container
        self.settings = settings
        self.mood = mood
        self.gs = guild_settings
        self._main_last_activity: dict[int, float] = {}  # guild_id -> monotonic
        self._expiry_tasks: dict[int, asyncio.Task] = {}  # find_id -> task
        self._next_spawn: dict[int, float] = {}  # guild_id -> monotonic след. спавна
        self._loops_started = False
        self._tasks: list[asyncio.Task] = []
        self._rng = random.Random()

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    def _roll_interval(self, guild_id: int) -> float:
        """Случайный интервал до следующей находки (секунды) по настройкам сервера."""
        lo = self._cfg(guild_id, "finds_min_interval_hours")
        hi = max(self._cfg(guild_id, "finds_max_interval_hours"), lo)
        return self._rng.uniform(lo, hi) * 3600

    async def cog_load(self) -> None:
        # одна глобальная регистрация view — кнопки всех анонсов, включая
        # отправленные до рестарта, попадают в handle_claim
        self.bot.add_view(FindClaimView(self))

    def cog_unload(self) -> None:
        for task in [*self._tasks, *self._expiry_tasks.values()]:
            task.cancel()

    # --- каналы и активность ---

    def _announce_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        # приоритет — канал, заданный на сервере через /config finds_channel_id
        cid = self._cfg(guild.id, "finds_channel_id")
        if cid:
            channel = guild.get_channel(cid)
            if isinstance(channel, discord.TextChannel):
                return channel
        name = self.settings.finds_channel or self.settings.main_channel
        return discord.utils.get(guild.text_channels, name=name)

    def _holiday_key(self, now: datetime) -> str | None:
        """ "ДД-ММ", если сегодня праздник из настроек, иначе None."""
        key = f"{now.day:02d}-{now.month:02d}"
        return key if key in self.settings.holidays else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if getattr(message.channel, "name", None) == self.settings.main_channel:
            self._main_last_activity[message.guild.id] = time.monotonic()

    # --- фоновый цикл спавна ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            self._main_last_activity.setdefault(guild.id, time.monotonic())
        if self._loops_started:
            return
        self._loops_started = True
        self._tasks.append(asyncio.create_task(self._spawn_loop()))
        self._tasks.append(asyncio.create_task(self._restore_live_finds()))
        logger.info(
            "Находки: цикл спавна запущен (интервал %d–%d ч) · "
            "условие «сервер живой» ОТКЛЮЧЕНО (тест)",
            self.settings.finds_min_interval_hours,
            self.settings.finds_max_interval_hours,
        )

    async def _restore_live_finds(self) -> None:
        """После рестарта: заново запланировать «протухание» живых находок."""
        try:
            live = await self.finds.list_live_finds.execute(datetime.now(UTC))
            for find in live:
                self._schedule_expiry(find)
        except Exception:
            logger.exception("Не удалось восстановить живые находки")

    async def _spawn_loop(self) -> None:
        # тик раз в минуту; у каждого сервера свой момент следующего спавна,
        # интервал берётся из его настроек (/config finds_min/max_interval_hours)
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for guild in self.bot.guilds:
                nxt = self._next_spawn.get(guild.id)
                if nxt is None:
                    self._next_spawn[guild.id] = now + self._roll_interval(guild.id)
                    continue
                if now < nxt:
                    continue
                self._next_spawn[guild.id] = now + self._roll_interval(guild.id)
                try:
                    await self._try_spawn(guild)
                except Exception:
                    logger.exception("Спавн находки упал", extra={"guild_id": guild.id})

    async def _try_spawn(self, guild: discord.Guild, force: bool = False) -> NightFind | None:
        # ВРЕМЕННО для визуальных тестов: условие «сервер живой» отключено,
        # чтобы находки спавнились даже в тихом канале. Вернуть, когда бот
        # заработает для всех:
        #   last = self._main_last_activity.get(guild.id)
        #   if last is None or time.monotonic() - last > _ACTIVITY_WINDOW_SECONDS:
        #       return  # сервер спит — Попося гуляет молча
        channel = self._announce_channel(guild)
        if channel is None:
            return None
        mood = self.mood.get(guild.id)
        # плохое настроение — может и не поделиться находкой (форс игнорирует)
        if not force and mood <= 30 and self._rng.random() < 0.5:
            return None
        now = datetime.now(UTC)
        holiday = self._holiday_key(now)
        result = await self.finds.spawn_find.execute(
            guild.id,
            now,
            season=season_for_month(now.month),
            # хорошее настроение или праздник — находки лучше
            boosted=mood >= 65 or holiday is not None,
            holiday=holiday,
        )
        if result is None:  # активная находка уже висит
            return None
        find, location, _item = result
        opener = self._rng.choice(_OPENERS).format(place=location.name)
        embed = discord.Embed(
            title="🌙 Ночная находка",
            description=(
                f"{opener}\n\n"
                "Не обещаю, что оно всё ещё там — Токио быстрый. "
                "Но если хочешь, можешь сходить посмотреть.\n\n"
                f"**Место:** {location.name}\n"
                f"-# {location.flavor}"
            ),
            color=_EMBED_COLOR,
        )
        embed.set_footer(text="Одна попытка на находку. Заберёт первый, кому повезёт.")
        try:
            message = await channel.send(
                embed=embed,
                view=FindClaimView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Не удалось отправить анонс находки", exc_info=True)
            return None
        await self.finds.register_find_message.execute(find.id, channel.id, message.id)
        find.channel_id, find.message_id = channel.id, message.id
        self._schedule_expiry(find)
        return find

    # --- протухание находки ---

    def _schedule_expiry(self, find: NightFind) -> None:
        if find.id is None or find.id in self._expiry_tasks:
            return
        task = asyncio.create_task(self._expire_later(find))
        self._expiry_tasks[find.id] = task
        task.add_done_callback(lambda _: self._expiry_tasks.pop(find.id, None))

    async def _expire_later(self, find: NightFind) -> None:
        delay = (find.expires_at - datetime.now(UTC)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        now = datetime.now(UTC)
        current = await self.finds.get_active_find.execute(find.guild_id, now)
        if current is not None and current.find.id == find.id:
            return  # ещё активна (не должна быть, но перестрахуемся)
        fresh = await self.finds.list_live_finds.execute(now)
        if any(f.id == find.id for f in fresh):
            return
        await self._close_announcement(find, "-# 🌙 Опоздали. Токио быстрый — находки не ждут.")

    async def _close_announcement(self, find: NightFind, note: str) -> None:
        """Убрать кнопку с анонса и дописать итог."""
        if not find.channel_id or not find.message_id:
            return
        channel = self.bot.get_channel(find.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(find.message_id)
            embed = message.embeds[0] if message.embeds else None
            if embed is not None:
                embed.description = f"{embed.description}\n\n{note}"
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    # --- кнопка «Сходить туда» ---

    async def handle_claim(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        result = await self.finds.claim_find.execute(
            interaction.guild_id, interaction.user.id, interaction.message.id, now
        )
        if result.status == "gone":
            await interaction.followup.send(
                "Там уже пусто. Или кто-то успел раньше, или Токио забрал своё.",
                ephemeral=True,
            )
            return
        if result.status == "already":
            await interaction.followup.send(
                "Ты уже ходил к этой находке. Второго шанса город не даёт.",
                ephemeral=True,
            )
            return
        if result.status == "cooldown":
            await interaction.followup.send(
                f"Ноги ещё гудят после прошлого похода. Возвращайся {_ts(result.retry_at)}.",
                ephemeral=True,
            )
            return
        if result.status == "fail":
            line = self._rng.choice(_FAIL_LINES)
            await interaction.followup.send(
                f"{line}\n-# {result.points_delta} очков. Сейчас у тебя {result.points_total}.",
                ephemeral=True,
            )
            return

        # success
        item = result.item
        await self._announce_claim(interaction, result)
        await interaction.followup.send(
            f"{item.emoji} **{item.name}** теперь в твоей коллекции "
            f"(`/collection`). +{result.points_delta} очков.",
            ephemeral=True,
        )

    async def _announce_claim(self, interaction: discord.Interaction, result: ClaimResult) -> None:
        item = result.item
        user = interaction.user
        # закрыть анонс
        try:
            message = interaction.message
            embed = message.embeds[0] if message.embeds else None
            if embed is not None:
                embed.description = (
                    f"{embed.description}\n\n-# ✅ Забрал {user.mention} — {item.emoji} {item.name}"
                )
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
        # публичная реакция Попоси
        if item.rarity is Rarity.LEGENDARY:
            line = _SUCCESS_LEGENDARY
        elif result.level >= 6:
            line = _SUCCESS_HIGH
        elif result.level >= 3:
            line = _SUCCESS_MID
        else:
            line = _SUCCESS_LOW
        embed = discord.Embed(
            title=f"{item.emoji} {item.name}",
            description=(
                f"-# {item.flavor}\n\n{line}\n\n"
                f"{user.mention} получает **+{result.points_delta}** очков "
                f"({RARITY_EMOJI[item.rarity]} {RARITY_LABELS[item.rarity]} находка)."
            ),
            color=_EMBED_COLOR,
        )
        try:
            await interaction.channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            logger.warning("Не удалось объявить об успехе находки", exc_info=True)

    # --- слеш-команды ---

    @app_commands.command(
        name="spawnfind",
        description="[тест] Форс-спавн ночной находки прямо сейчас (админ)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def spawn_find_command(self, interaction: discord.Interaction) -> None:
        # ВРЕМЕННАЯ команда для проверки цикла находок без ожидания интервала.
        guild = interaction.guild
        channel = self._announce_channel(guild)
        if channel is None:
            await interaction.response.send_message(
                "Не нашла канал для находок. Задай его: "
                "`/config set finds_channel_id #канал` — или создай канал «основной».",
                ephemeral=True,
            )
            return
        existing = await self.finds.get_active_find.execute(guild.id, datetime.now(UTC))
        if existing is not None:
            await interaction.response.send_message(
                "Активная находка уже висит — сначала заберите её.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        find = await self._try_spawn(guild, force=True)
        if find is not None:
            await interaction.followup.send(
                f"🌙 Находка заспавнена в {channel.mention}.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "Не вышло заспавнить — глянь логи бота.", ephemeral=True
            )

    @app_commands.command(name="finds", description="Активная ночная находка на сервере")
    @app_commands.guild_only()
    async def finds_command(self, interaction: discord.Interaction) -> None:
        view = await self.finds.get_active_find.execute(interaction.guild_id, datetime.now(UTC))
        if view is None:
            await interaction.response.send_message(
                "Сейчас находок нет. Я хожу гулять, когда сама захочу.", ephemeral=True
            )
            return
        jump = ""
        if view.find.channel_id and view.find.message_id:
            jump = (
                f"\n[К анонсу](https://discord.com/channels/"
                f"{interaction.guild_id}/{view.find.channel_id}/{view.find.message_id})"
            )
        embed = discord.Embed(
            title="🌙 Активная находка",
            description=(
                f"**Место:** {view.location.name}\n"
                f"-# {view.location.flavor}\n\n"
                f"Пропадёт {_ts(view.find.expires_at)}.{jump}"
            ),
            color=_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="collection", description="Твоя коллекция ночных находок")
    @app_commands.guild_only()
    async def collection_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        entries = await self.finds.get_collection.execute(interaction.guild_id, interaction.user.id)
        if not entries:
            await interaction.followup.send(
                "Пока пусто. Следи за моими ночными прогулками — и успевай первым.",
                ephemeral=True,
            )
            return
        order = [Rarity.LEGENDARY, Rarity.RARE, Rarity.UNCOMMON, Rarity.COMMON]
        lines: list[str] = []
        for rarity in order:
            group = [e for e in entries if e.item.rarity is rarity]
            if not group:
                continue
            lines.append(f"**{RARITY_EMOJI[rarity]} {RARITY_LABELS[rarity].capitalize()}:**")
            for entry in group:
                mark = " · 🎁 подарено мне" if entry.gifted_at is not None else ""
                lines.append(f"{entry.item.emoji} {entry.item.name}{mark}")
            lines.append("")
        embed = discord.Embed(
            title=f"🗃️ Коллекция ({len(entries)})",
            description="\n".join(lines).strip()[:4000],
            color=_EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="gift", description="Подарить Попосе предмет из коллекции")
    @app_commands.describe(item="Предмет из твоей коллекции")
    @app_commands.guild_only()
    async def gift_command(self, interaction: discord.Interaction, item: str) -> None:
        await interaction.response.defer()
        result = await self.finds.gift_item.execute(
            interaction.guild_id, interaction.user.id, item, datetime.now(UTC)
        )
        if result.status != "ok":
            await interaction.followup.send(
                "У тебя нет такого предмета. Дарить чужое — не твой уровень.",
                ephemeral=True,
            )
            return
        gifted = result.item
        embed = discord.Embed(
            title=f"🎁 {gifted.emoji} {gifted.name}",
            description=(
                f"{_GIFT_LINES[gifted.rarity]}\n\n"
                f"{interaction.user.mention} получает **+{result.bonus}** очков."
            ),
            color=_EMBED_COLOR,
        )
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions(users=True)
        )

    @gift_command.autocomplete("item")
    async def gift_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        entries = await self.finds.get_collection.execute(interaction.guild_id, interaction.user.id)
        seen: dict[str, str] = {}  # item_id -> название с эмодзи
        for entry in entries:
            if entry.gifted_at is None and entry.item.id not in seen:
                seen[entry.item.id] = f"{entry.item.emoji} {entry.item.name}"
        needle = current.lower()
        return [
            app_commands.Choice(name=name[:100], value=item_id)
            for item_id, name in seen.items()
            if needle in name.lower()
        ][:25]

    @app_commands.command(
        name="walk",
        description="Попросить Попосю сходить на специальную прогулку (раз в неделю, за очки)",
    )
    @app_commands.guild_only()
    async def walk_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        result = await self.finds.special_walk.execute(
            interaction.guild_id,
            interaction.user.id,
            now,
            season=season_for_month(now.month),
            holiday=self._holiday_key(now),
        )
        if result.status == "cooldown":
            await interaction.followup.send(
                f"Я тебе не такси. Следующая прогулка — {_ts(result.retry_at)}.",
                ephemeral=True,
            )
            return
        if result.status == "poor":
            await interaction.followup.send(
                f"Прогулка стоит **{result.cost}** очков, у тебя {result.points_total}. "
                "Сначала заслужи.",
                ephemeral=True,
            )
            return
        if result.status == "fail":
            await interaction.followup.send(
                f"{self._rng.choice(_WALK_FAIL)}\n"
                f"-# −{result.cost} очков за прогулку. Сейчас у тебя {result.points_total}.",
                ephemeral=True,
            )
            return
        item = result.item
        sign = "+" if result.points_delta >= 0 else "−"
        await interaction.followup.send(
            f"{self._rng.choice(_WALK_SUCCESS)}\n"
            f"{item.emoji} **{item.name}** — в твоей коллекции.\n"
            f"-# Итог: {sign}{abs(result.points_delta)} очков "
            f"(прогулка −{result.cost}, находка сверху). Сейчас у тебя {result.points_total}.",
            ephemeral=True,
        )
