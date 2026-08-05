import asyncio
import logging
import random
import time
from datetime import UTC, datetime
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.ai_chat.mood import MoodTracker
from src.application.finds.di import FindsContainer
from src.application.finds.use_cases import ClaimResult
from src.config import Settings
from src.domain.finds.catalog import RARITY_EMOJI, RARITY_LABELS, season_for_month
from src.domain.finds.entities import NightFind, Rarity
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.channels import is_designated_main, resolve_channel
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)
# «только если в основном канале была активность» — окно, в котором сервер
# считается живым для спавна находки
_ACTIVITY_WINDOW_SECONDS = 12 * 3600


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
        persona=None,
    ):
        self.bot = bot
        self.finds = container
        self.settings = settings
        self.mood = mood
        self.gs = guild_settings
        # голос кога — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()
        self._main_last_activity: dict[int, float] = {}  # guild_id -> monotonic
        self._expiry_tasks: dict[int, asyncio.Task] = {}  # find_id -> task
        self._next_spawn: dict[int, float] = {}  # guild_id -> monotonic след. спавна
        self._loops_started = False
        self._tasks: list[asyncio.Task] = []
        self._rng = random.Random()

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    def _p(self, guild_id: int, key: str, **vars: object) -> str:
        """Строковая фраза каталога персоны сервера."""
        return str(self.persona.phrase(guild_id, key, **vars))

    async def _pick(self, guild_id: int, key: str, **vars: object) -> str:
        """Случайный элемент фразы-списка (через render_block: режим/random)."""
        return await self.persona.render_block(guild_id, key, None, **vars) or ""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await block_if_module_off(interaction, self.settings, self.gs, "finds_enabled")

    def _roll_interval(self, guild_id: int) -> float:
        """Случайный интервал до следующей находки (секунды) по настройкам сервера."""
        lo = self._cfg(guild_id, "finds_min_interval_hours")
        hi = max(self._cfg(guild_id, "finds_max_interval_hours"), lo)
        return self._rng.uniform(lo, hi) * 3600

    async def cog_load(self) -> None:
        # одна глобальная регистрация view — кнопки всех анонсов, включая
        # отправленные до рестарта, попадают в handle_claim
        self.bot.add_view(FindClaimView(self))

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        for task in [*self._tasks, *self._expiry_tasks.values()]:
            task.cancel()

    # --- каналы и активность ---

    def _announce_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        # /config finds_channel_id → легаси-имя (finds/main). Находки — opt-in:
        # без явного канала в авто-подобранный не сыплем (fallback=False).
        return resolve_channel(
            guild,
            self._cfg(guild.id, "finds_channel_id"),
            self.settings.finds_channel or self.settings.main_channel,
            fallback=False,
        )

    def _holiday_key(self, now: datetime) -> str | None:
        """ "ДД-ММ", если сегодня праздник из настроек, иначе None."""
        key = f"{now.day:02d}-{now.month:02d}"
        return key if key in self.settings.holidays else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if is_designated_main(
            message.channel,
            self._cfg(message.guild.id, "main_channel_id"),
            self.settings.main_channel,
        ):
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
            "Находки: цикл спавна запущен (интервал %d–%d ч)",
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
                if not self._cfg(guild.id, "finds_enabled"):
                    continue  # модуль «Находки» выключен на сервере
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
        # «сервер живой»: находку спавним только если в основном канале недавно
        # была активность — иначе Попося гуляет молча. Форс из /spawnfind
        # (админ-тест) этот гейт игнорирует, как и проверку настроения ниже.
        if not force:
            last = self._main_last_activity.get(guild.id)
            if last is None or time.monotonic() - last > _ACTIVITY_WINDOW_SECONDS:
                return None  # сервер спит — Попося гуляет молча
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
        opener = await self._pick(guild.id, "finds.opener", place=location.name)
        body = self._p(guild.id, "finds.announce_body")
        place = self._p(
            guild.id, "finds.announce_place", place=location.name, place_flavor=location.flavor
        )
        embed = discord.Embed(
            title=self._p(guild.id, "finds.announce_title"),
            description="\n\n".join(part for part in (opener, body, place) if part),
            color=accent(guild.id),
        )
        footer = self._p(guild.id, "finds.announce_footer")
        if footer:
            embed.set_footer(text=footer)
        try:
            message = await channel.send(
                embed=embed,
                view=FindClaimView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Не удалось отправить анонс находки", exc_info=True)
            return None
        await self.finds.register_find_message.execute(cast(int, find.id), channel.id, message.id)
        find.channel_id, find.message_id = channel.id, message.id
        self._schedule_expiry(find)
        return find

    # --- протухание находки ---

    def _schedule_expiry(self, find: NightFind) -> None:
        if find.id is None or find.id in self._expiry_tasks:
            return
        fid = find.id  # локал: в замыкании find.id снова Optional
        task = asyncio.create_task(self._expire_later(find))
        self._expiry_tasks[fid] = task
        task.add_done_callback(lambda _: self._expiry_tasks.pop(fid, None))

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
        await self._close_announcement(find, self._p(find.guild_id, "finds.expired"))

    async def _close_announcement(self, find: NightFind, note: str) -> None:
        """Убрать кнопку с анонса и дописать итог."""
        if not find.channel_id or not find.message_id:
            return
        channel = self.bot.get_channel(find.channel_id)
        if channel is None:
            return
        try:
            message = await cast(discord.abc.Messageable, channel).fetch_message(find.message_id)
            embed = message.embeds[0] if message.embeds else None
            if embed is not None:
                embed.description = f"{embed.description}\n\n{note}"
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    # --- кнопка «Сходить туда» ---

    async def handle_claim(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        gid = guild.id
        now = datetime.now(UTC)
        result = await self.finds.claim_find.execute(
            gid, interaction.user.id, cast(discord.Message, interaction.message).id, now
        )
        if result.status == "gone":
            await interaction.followup.send(self._p(gid, "finds.claim_gone"), ephemeral=True)
            return
        if result.status == "already":
            await interaction.followup.send(self._p(gid, "finds.claim_already"), ephemeral=True)
            return
        if result.status == "cooldown":
            await interaction.followup.send(
                self._p(gid, "finds.claim_cooldown", retry=_ts(cast(datetime, result.retry_at))),
                ephemeral=True,
            )
            return
        if result.status == "fail":
            line = await self._pick(gid, "finds.claim_fail")
            tail = self._p(
                gid, "finds.points_tail", delta=result.points_delta, total=result.points_total
            )
            await interaction.followup.send(f"{line}\n{tail}".strip(), ephemeral=True)
            return

        # success
        item = result.item
        if item is None:  # success гарантирует предмет; страховка для типов
            return
        await self._announce_claim(interaction, result)
        await interaction.followup.send(
            self._p(
                gid,
                "finds.claim_success_note",
                item_emoji=item.emoji,
                item=item.name,
                delta=result.points_delta,
            ),
            ephemeral=True,
        )

    async def _announce_claim(self, interaction: discord.Interaction, result: ClaimResult) -> None:
        item = result.item
        if item is None:  # зовётся только на success; страховка для типов
            return
        user = interaction.user
        gid = guild_of(interaction).id
        # закрыть анонс
        try:
            message = cast(discord.Message, interaction.message)  # клик по кнопке анонса
            embed = message.embeds[0] if message.embeds else None
            if embed is not None:
                taken = self._p(
                    gid,
                    "finds.claim_taken_note",
                    user_mention=user.mention,
                    item_emoji=item.emoji,
                    item=item.name,
                )
                embed.description = f"{embed.description}\n\n{taken}"
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
        # публичная реакция Попоси: тепло растёт с уровнем отношений
        if item.rarity is Rarity.LEGENDARY:
            line_key = "finds.success_legendary"
        elif result.level >= 6:
            line_key = "finds.success_high"
        elif result.level >= 3:
            line_key = "finds.success_mid"
        else:
            line_key = "finds.success_low"
        line = self._p(gid, line_key)
        award = self._p(
            gid,
            "finds.claim_award",
            user_mention=user.mention,
            delta=result.points_delta,
            rarity_emoji=RARITY_EMOJI[item.rarity],
            rarity=RARITY_LABELS[item.rarity],
        )
        embed = discord.Embed(
            title=f"{item.emoji} {item.name}",
            description="\n\n".join(part for part in (f"-# {item.flavor}", line, award) if part),
            color=accent(gid),
        )
        try:
            await cast(discord.abc.Messageable, interaction.channel).send(
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
        guild = guild_of(interaction)
        channel = self._announce_channel(guild)
        if channel is None:
            await interaction.response.send_message(
                self._p(guild.id, "finds.admin_no_channel"), ephemeral=True
            )
            return
        existing = await self.finds.get_active_find.execute(guild.id, datetime.now(UTC))
        if existing is not None:
            await interaction.response.send_message(
                self._p(guild.id, "finds.admin_active_exists"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        find = await self._try_spawn(guild, force=True)
        if find is not None:
            await interaction.followup.send(
                self._p(guild.id, "finds.admin_spawned", channel_mention=channel.mention),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                self._p(guild.id, "finds.admin_spawn_failed"), ephemeral=True
            )

    @app_commands.command(name="finds", description="Активная ночная находка на сервере")
    @app_commands.guild_only()
    async def finds_command(self, interaction: discord.Interaction) -> None:
        gid = guild_of(interaction).id
        view = await self.finds.get_active_find.execute(gid, datetime.now(UTC))
        if view is None:
            await interaction.response.send_message(
                self._p(gid, "finds.none_active"), ephemeral=True
            )
            return
        body = self._p(
            gid,
            "finds.active_body",
            place=view.location.name,
            place_flavor=view.location.flavor,
            expires=_ts(view.find.expires_at),
        )
        if view.find.channel_id and view.find.message_id:
            url = (
                f"https://discord.com/channels/"
                f"{gid}/{view.find.channel_id}/{view.find.message_id}"
            )
            body += "\n" + self._p(gid, "finds.active_jump", url=url)
        embed = discord.Embed(
            title=self._p(gid, "finds.active_title"),
            description=body,
            color=accent(gid),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="collection", description="Твоя коллекция ночных находок")
    @app_commands.guild_only()
    async def collection_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        entries = await self.finds.get_collection.execute(gid, interaction.user.id)
        if not entries:
            await interaction.followup.send(self._p(gid, "finds.collection_empty"), ephemeral=True)
            return
        order = [Rarity.LEGENDARY, Rarity.RARE, Rarity.UNCOMMON, Rarity.COMMON]
        gifted_mark = self._p(gid, "finds.collection_gifted_mark")
        lines: list[str] = []
        for rarity in order:
            group = [e for e in entries if e.item.rarity is rarity]
            if not group:
                continue
            lines.append(f"**{RARITY_EMOJI[rarity]} {RARITY_LABELS[rarity].capitalize()}:**")
            for entry in group:
                mark = gifted_mark if entry.gifted_at is not None else ""
                lines.append(f"{entry.item.emoji} {entry.item.name}{mark}")
            lines.append("")
        embed = discord.Embed(
            title=self._p(gid, "finds.collection_title", count=len(entries)),
            description="\n".join(lines).strip()[:4000],
            color=accent(gid),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="gift", description="Подарить Попосе предмет из коллекции")
    @app_commands.describe(item="Предмет из твоей коллекции")
    @app_commands.guild_only()
    async def gift_command(self, interaction: discord.Interaction, item: str) -> None:
        await interaction.response.defer()
        result = await self.finds.gift_item.execute(
            guild_of(interaction).id, interaction.user.id, item, datetime.now(UTC)
        )
        gid = guild_of(interaction).id
        if result.status != "ok":
            await interaction.followup.send(self._p(gid, "finds.gift_no_item"), ephemeral=True)
            return
        gifted = result.item
        if gifted is None:  # status=="ok" гарантирует предмет; страховка для типов
            return
        gift_lines = self.persona.phrase(gid, "finds.gift")
        line = gift_lines.get(gifted.rarity.value, "") if isinstance(gift_lines, dict) else ""
        award = self._p(
            gid, "finds.gift_award", user_mention=interaction.user.mention, bonus=result.bonus
        )
        embed = discord.Embed(
            title=f"🎁 {gifted.emoji} {gifted.name}",
            description="\n\n".join(part for part in (line, award) if part),
            color=accent(gid),
        )
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions(users=True)
        )

    @gift_command.autocomplete("item")
    async def gift_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        entries = await self.finds.get_collection.execute(guild_of(interaction).id, interaction.user.id)
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
            guild_of(interaction).id,
            interaction.user.id,
            now,
            season=season_for_month(now.month),
            holiday=self._holiday_key(now),
        )
        gid = guild_of(interaction).id
        if result.status == "cooldown":
            await interaction.followup.send(
                self._p(gid, "finds.walk_cooldown", retry=_ts(cast(datetime, result.retry_at))),
                ephemeral=True,
            )
            return
        if result.status == "poor":
            await interaction.followup.send(
                self._p(gid, "finds.walk_poor", cost=result.cost, total=result.points_total),
                ephemeral=True,
            )
            return
        if result.status == "fail":
            line = await self._pick(gid, "finds.walk_fail")
            tail = self._p(
                gid, "finds.walk_fail_tail", cost=result.cost, total=result.points_total
            )
            await interaction.followup.send(f"{line}\n{tail}".strip(), ephemeral=True)
            return
        item = result.item
        if item is None:  # success гарантирует предмет; страховка для типов
            return
        sign = "+" if result.points_delta >= 0 else "−"
        line = await self._pick(gid, "finds.walk_success")
        tail = self._p(
            gid,
            "finds.walk_success_tail",
            item_emoji=item.emoji,
            item=item.name,
            sign=sign,
            delta=abs(result.points_delta),
            cost=result.cost,
            total=result.points_total,
        )
        await interaction.followup.send(f"{line}\n{tail}".strip(), ephemeral=True)
