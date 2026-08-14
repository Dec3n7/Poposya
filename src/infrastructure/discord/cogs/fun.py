import asyncio
import logging
import random
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.activity.di import ActivityContainer
from src.application.ai_chat.service import ChatService
from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.infrastructure.ai.rate_limiter import InMemoryRateLimiter
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import block_if_module_off, flag_on, limit_suffix
from src.infrastructure.discord.interaction_ctx import guild_of, member_of
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

_REMINDER_CHECK_INTERVAL = 30


class FunCog(PersonaPhraseMixin, commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        activity: ActivityContainer,
        relationship: RelationshipContainer,
        chat_service: ChatService | None,
        settings: Settings,
        finds=None,  # FindsContainer — витрина коллекции в /profile
        music=None,  # MusicContainer — лайки в /profile
        cinema=None,  # CinemaContainer — кино-статистика в /profile
        guild_settings=None,  # GuildSettingsService — имена ролей per-guild
        persona=None,  # PersonaService — голос кога (каталог фраз)
    ):
        self.bot = bot
        self.activity = activity
        self.relationship = relationship
        self.chat = chat_service
        self.settings = settings
        self.finds = finds
        self.music = music
        self.cinema = cinema
        self.gs = guild_settings
        self.persona = persona if persona is not None else RegistryPersona()
        self._reminder_task: asyncio.Task | None = None
        self._send_limiter = InMemoryRateLimiter()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # модуль «Развлечения» выключен на сервере -> команды не работают
        return await block_if_module_off(interaction, self.settings, self.gs, "fun_enabled")

    # --- каталог фраз персоны сервера ---

    async def _pick(self, guild_id: int, key: str, **vars: object) -> str:
        """Случайный элемент фразы-списка (через render_block: режим/random)."""
        return await self.persona.render_block(guild_id, key, None, **vars) or ""

    def _pd(self, guild_id: int, key: str, sub: str, **vars: object) -> str:
        """Элемент фразы-словаря с мягким форматированием (кривой override не
        роняет ког — текст возвращается как есть)."""
        value = self.persona.phrase(guild_id, key)
        text = value.get(sub, "") if isinstance(value, dict) else ""
        if vars:
            try:
                return text.format(**vars)
            except (KeyError, IndexError, ValueError):
                return text
        return text

    def _names(self, guild_id: int) -> list[str]:
        """Имена ролей-статусов сервера (per-guild override или глобальный дефолт)."""
        if self.gs is not None:
            return self.gs.resolved(guild_id).relationship_role_names
        return self.relationship.role_names

    async def cog_load(self) -> None:
        # напоминания хранятся в БД — рестарт не сбрасывает таймеры
        self._reminder_task = asyncio.create_task(self._reminder_loop())
        logger.info(
            "Развлечения: цикл напоминаний запущен (проверка каждые %d с)", _REMINDER_CHECK_INTERVAL
        )

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        if self._reminder_task is not None:
            self._reminder_task.cancel()

    # --- простые развлечения ---

    _DICE_MAX_ROUNDS = 10
    _DICE_MAX_PLAYERS = 20  # больше не влезает в сообщение по-человечески

    @app_commands.command(name="dice", description="Бросить кубик — или вызвать других на дуэль")
    @app_commands.checks.cooldown(1, 5)
    @app_commands.describe(
        sides="Число граней (по умолчанию 6)",
        users="Соперники: просто отметь @участников через пробел",
    )
    async def dice(
        self,
        interaction: discord.Interaction,
        sides: app_commands.Range[int, 2, 1000] = 6,
        users: str | None = None,
    ) -> None:
        gid = cast(int, interaction.guild_id)  # без guild_only: в ЛС None (пер-серверных фраз нет)
        mentioned: list[discord.Member] = []
        had_bots = False
        if users and interaction.guild is not None:
            for raw_id in re.findall(r"<@!?(\d+)>", users):
                member = interaction.guild.get_member(int(raw_id))
                if member is None:
                    continue
                if member.bot:
                    had_bots = True
                    continue
                mentioned.append(member)

        players: list[discord.Member] = []
        for member in (member_of(interaction), *mentioned):
            if member not in players:
                players.append(member)
        if len(players) > self._DICE_MAX_PLAYERS:
            await interaction.response.send_message(
                self._p(gid, "fun.dice_too_many", max=self._DICE_MAX_PLAYERS),
                ephemeral=True,
            )
            return

        if len(players) < 2:
            if had_bots:
                await interaction.response.send_message(
                    self._p(gid, "fun.dice_bots_only"), ephemeral=True
                )
                return
            result = random.randint(1, sides)
            await interaction.response.send_message(f"🎲 d{sides} → **{result}**")
            return

        # дуэль: бросают все; ничья наверху — тайбрейк только между лидерами
        lines: list[str] = [self._p(gid, "fun.dice_duel_title", sides=sides)]
        contenders = players
        winner: discord.Member | None = None
        for round_no in range(1, self._DICE_MAX_ROUNDS + 1):
            rolls = [(member, random.randint(1, sides)) for member in contenders]
            lines.append(
                self._p(gid, "fun.dice_round", round=round_no)
                + " "
                + ", ".join(f"{m.display_name} — `{r}`" for m, r in rolls)
            )
            best = max(result for _, result in rolls)
            top = [member for member, result in rolls if result == best]
            if len(top) == 1:
                winner = top[0]
                lines.append(self._p(gid, "fun.dice_winner", winner=winner.mention, best=best))
                break
            names = " и ".join(m.display_name for m in top)
            lines.append(self._p(gid, "fun.dice_tie", names=names))
            contenders = top
        if winner is None:
            lines.append(self._p(gid, "fun.dice_stalemate", rounds=self._DICE_MAX_ROUNDS))
        await interaction.response.send_message(
            "\n".join(lines)[:2000],
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @app_commands.command(name="coinflip", description="Подбросить монетку")
    @app_commands.checks.cooldown(1, 5)
    async def coinflip(self, interaction: discord.Interaction) -> None:
        side = await self._pick(cast(int, interaction.guild_id), "fun.coin_sides")
        await interaction.response.send_message(f"**{side}**")

    @app_commands.command(name="topic", description="Случайная тема для разговора")
    @app_commands.checks.cooldown(1, 5)
    async def topic(self, interaction: discord.Interaction) -> None:
        topic = await self._pick(cast(int, interaction.guild_id), "fun.topics")
        await interaction.response.send_message(f"💬 {topic}")

    # --- профиль глазами Попоси ---

    _MONTHS_RU = [
        "",
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]

    def _relationship_bar(self, points: int, next_threshold: int | None, width: int = 10) -> str:
        thresholds = self.relationship.policy.thresholds
        if next_threshold is None:
            return "▰" * width
        lower = max((t for t in thresholds if t <= points), default=0)
        span = max(1, next_threshold - lower)
        filled = min(width, int(width * (points - lower) / span))
        return "▰" * filled + "▱" * (width - filled)

    def _last_seen(self, guild_id: int, last: datetime | None) -> str:
        if last is None:
            return self._p(guild_id, "fun.last_seen_never")
        days = (datetime.now(UTC) - last).days
        if days <= 0:
            return self._p(guild_id, "fun.last_seen_today")
        if days == 1:
            return self._p(guild_id, "fun.last_seen_yesterday")
        return self._p(guild_id, "fun.last_seen_days", days=days)

    async def _build_showcase(self, guild_id: int, user_id: int) -> list[str]:
        """Сводка по всем модулям бота: находки, музыка, кино, войс.
        Пустые разделы не показываются; ошибки не валят профиль."""
        from src.domain.finds.catalog import RARITY_EMOJI
        from src.domain.finds.entities import Rarity

        lines: list[str] = []
        try:
            if self.finds is not None:
                collection = await self.finds.get_collection.execute(guild_id, user_id)
                if collection:
                    by_rarity = Counter(e.item.rarity for e in collection)
                    order = (Rarity.LEGENDARY, Rarity.RARE, Rarity.UNCOMMON, Rarity.COMMON)
                    parts = " ".join(
                        f"{RARITY_EMOJI[r]}{by_rarity[r]}" for r in order if by_rarity[r]
                    )
                    gifted = sum(1 for e in collection if e.gifted_at is not None)
                    line = self._p(guild_id, "fun.showcase_finds", parts=parts)
                    if gifted:
                        line += self._p(guild_id, "fun.showcase_finds_gifted", gifted=gifted)
                    lines.append(line)
            if self.music is not None:
                liked = await self.music.list_liked.execute(user_id)
                if liked:
                    lines.append(self._p(guild_id, "fun.showcase_music", count=len(liked)))
            if self.cinema is not None:
                stats = await self.cinema.cinema_profile.execute(guild_id, user_id)
                if stats.proposed or stats.ratings_count:
                    line = self._p(
                        guild_id,
                        "fun.showcase_cinema",
                        proposed=stats.proposed,
                        ratings=stats.ratings_count,
                    )
                    if stats.avg_given is not None:
                        line += self._p(guild_id, "fun.showcase_cinema_avg", avg=stats.avg_given)
                    lines.append(line)
            hours = await self.activity.get_voice_hours.execute(guild_id, user_id)
            if hours >= 0.5:
                lines.append(self._p(guild_id, "fun.showcase_voice", hours=f"{hours:.1f}"))
        except Exception:
            logger.exception("Витрина профиля не собралась")
        return lines

    @app_commands.command(name="profile", description="Профиль пользователя глазами Попоси")
    @app_commands.checks.cooldown(1, 15)
    @app_commands.describe(user="Чей профиль (по умолчанию — твой)")
    @app_commands.guild_only()
    async def profile(
        self, interaction: discord.Interaction, user: discord.Member | None = None
    ) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        target = user or interaction.user
        if target.bot:
            await interaction.response.send_message(self._p(gid, "fun.profile_bot"), ephemeral=True)
            return
        await interaction.response.defer()
        info = await self.relationship.get_rank.execute(target.id, gid)
        role_name = (
            self._names(gid)[info.role_index]
            if info.role_index is not None
            else self._p(gid, "fun.profile_no_status")
        )

        opener = self._p(gid, f"fun.profile_opener_{info.level}", name=target.display_name)
        embed = discord.Embed(
            description=f"**{opener}**",
            color=accent(gid),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        attachment = self._p(
            gid,
            "fun.profile_attachment",
            role_name=role_name,
            bar=self._relationship_bar(info.points, info.next_threshold),
            points=info.points,
        )
        if info.next_threshold is not None:
            attachment += "\n" + self._p(
                gid, "fun.profile_to_next", remaining=info.next_threshold - info.points
            )
        embed.add_field(
            name=self._p(gid, "fun.profile_field_attachment"), value=attachment, inline=False
        )

        activity_lines = [f"👁 {self._last_seen(gid, info.last_dialog_at)}"]
        if info.deep_dialogs:
            activity_lines.insert(
                0, self._p(gid, "fun.profile_deep_dialogs", count=info.deep_dialogs)
            )
        embed.add_field(
            name=self._p(gid, "fun.profile_field_activity"),
            value="\n".join(activity_lines),
            inline=False,
        )

        showcase = await self._build_showcase(gid, target.id)
        if showcase:
            embed.add_field(
                name=self._p(gid, "fun.profile_field_showcase"),
                value="\n".join(showcase)[:1000],
                inline=False,
            )

        known: list[str] = []
        if info.user_notes:
            known.extend(f"• {line}" for line in info.user_notes.splitlines() if line.strip())
        elif info.survey.interests:
            known.append(
                self._p(gid, "fun.profile_known_interests", interests=info.survey.interests)
            )
        if info.survey.season:
            known.append(self._p(gid, "fun.profile_known_season", season=info.survey.season))
        if not known:
            known.append(self._p(gid, "fun.profile_known_empty"))
        embed.add_field(
            name=self._p(gid, "fun.profile_field_known"),
            value="\n".join(known)[:1000],
            inline=False,
        )

        embed.add_field(
            name=self._p(gid, "fun.profile_field_attitude"),
            value=self._pd(gid, "fun.profile_attitude", str(info.level)),
        )
        birthday = (
            f"{info.birthday_day} {self._MONTHS_RU[info.birthday_month]}"
            if info.birthday_day and info.birthday_month
            else "—"
        )
        embed.add_field(name=self._p(gid, "fun.profile_field_birthday"), value=birthday)
        badges: list[str] = []
        if info.survey.completed:
            badges.append(self._pd(gid, "fun.profile_badges", "survey"))
        if info.birthday_day:
            badges.append(self._pd(gid, "fun.profile_badges", "birthday"))
        if info.deep_dialogs >= 5:
            badges.append(self._pd(gid, "fun.profile_badges", "deep"))
        if info.is_exclusive:
            badges.append(self._pd(gid, "fun.profile_badges", "exclusive"))
        embed.add_field(
            name=self._p(gid, "fun.profile_field_badges"), value="\n".join(badges) or "—"
        )

        embed.set_footer(text=self._pd(gid, "fun.profile_footers", str(info.level)))
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="birthday", description="Указать свой день рождения (без года)")
    @app_commands.describe(day="День (1–31)", month="Месяц (1–12)")
    @app_commands.guild_only()
    async def birthday(
        self,
        interaction: discord.Interaction,
        day: app_commands.Range[int, 1, 31],
        month: app_commands.Range[int, 1, 12],
    ) -> None:
        # defer сразу: у Discord лимит 3 секунды на первый ответ,
        # а запись в БД на холодном старте может не успеть (ошибка 10062)
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        ok = await self.relationship.set_birthday.execute(interaction.user.id, gid, day, month)
        if not ok:
            await interaction.followup.send(self._p(gid, "fun.birthday_invalid"), ephemeral=True)
            return
        await interaction.followup.send(
            self._p(gid, "fun.birthday_saved", day=f"{day:02d}", month=f"{month:02d}"),
            ephemeral=True,
        )

    # --- утилиты ---

    def _build_rules_embed(self, gid: int | None) -> discord.Embed:
        """Embed правил сервера. Заголовок вынесен в описание (## ...), т.к.
        кастом-эмодзи Discord в embed.title не рендерит."""
        return discord.Embed(
            title=self._p(gid, "fun.rules_title") or None,
            description=self._p(gid, "fun.rules_text"),
            color=accent(gid),
        )

    async def publish_rules(self, channel: discord.abc.Messageable, guild_id: int) -> None:
        """Публикация правил в канал из веб-панели (мост). Тот же embed, что у /rules."""
        await channel.send(embed=self._build_rules_embed(guild_id))

    @app_commands.command(name="rules", description="Опубликовать правила сервера (разово)")
    @app_commands.describe(channel="Канал для публикации (по умолчанию — текущий)")
    @app_commands.default_permissions(administrator=True)
    async def rules(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        gid = cast(int, interaction.guild_id)  # без guild_only: в ЛС None
        embed = self._build_rules_embed(gid)
        # постим правила обычным сообщением от лица бота — без «использовал /rules»;
        # автору отвечаем эфемерно, чтобы атрибуция не светилась в канале
        target = channel or cast(discord.TextChannel, interaction.channel)
        try:
            await target.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                self._p(gid, "fun.rules_no_perm", channel=target.mention), ephemeral=True
            )
            return
        await interaction.response.send_message(
            self._p(gid, "fun.rules_published", channel=target.mention), ephemeral=True
        )

    @app_commands.command(name="serverstats", description="Статистика сервера")
    @app_commands.checks.cooldown(1, 30)
    @app_commands.guild_only()
    async def serverstats(self, interaction: discord.Interaction) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        humans = sum(1 for m in guild.members if not m.bot)
        bots = (guild.member_count or 0) - humans
        embed = discord.Embed(title=f"📊 {guild.name}", color=accent(gid))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(
            name=self._p(gid, "fun.stats_field_members"),
            value=self._p(gid, "fun.stats_members", humans=humans, bots=bots),
        )
        embed.add_field(
            name=self._p(gid, "fun.stats_field_channels"),
            value=self._p(
                gid,
                "fun.stats_channels",
                text=len(guild.text_channels),
                voice=len(guild.voice_channels),
            ),
        )
        embed.add_field(
            name=self._p(gid, "fun.stats_field_boosts"),
            value=self._p(
                gid,
                "fun.stats_boosts",
                count=guild.premium_subscription_count,
                tier=guild.premium_tier,
            ),
        )
        embed.add_field(
            name=self._p(gid, "fun.stats_field_created"),
            value=f"<t:{int(guild.created_at.timestamp())}:D>",
        )
        if guild.owner:
            embed.add_field(name=self._p(gid, "fun.stats_field_owner"), value=str(guild.owner))
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="send", description="Передать сообщение через Попосю в ЛС")
    @app_commands.checks.cooldown(1, 60)  # релей в чужие ЛС — тормозим спам/харассмент
    @app_commands.describe(
        user="Кому доставить",
        text="Текст сообщения",
        mode="Открыто (с твоим именем) или анонимно",
    )
    @app_commands.guild_only()
    async def send(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        text: str,
        mode: Literal["открыто", "анонимно"] = "открыто",
    ) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        if user.bot:
            await interaction.response.send_message(self._p(gid, "fun.send_bot"), ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(self._p(gid, "fun.send_self"), ephemeral=True)
            return
        key = f"send:{gid}:{interaction.user.id}"
        send_per_hour = (
            self.gs.get(gid, "send_per_hour", self.settings.send_per_hour)
            if self.gs is not None
            else self.settings.send_per_hour
        )
        if not self._send_limiter.try_acquire(key, send_per_hour):
            await interaction.response.send_message(
                self._p(gid, "fun.send_rate_limited") + limit_suffix(self.gs, gid),
                ephemeral=True,
            )
            return
        # дальше — сетевые вызовы (ЛС получателю): отвечаем через defer
        await interaction.response.defer(ephemeral=True)

        text = text[:1500]
        if mode == "анонимно":
            dm_text = self._p(gid, "fun.send_dm_anon", guild=guild.name, text=text)
        else:
            dm_text = self._p(
                gid,
                "fun.send_dm_open",
                sender=interaction.user.display_name,
                guild=guild.name,
                text=text,
            )
        try:
            await user.send(dm_text)
        except discord.Forbidden:
            await interaction.followup.send(
                self._p(gid, "fun.send_forbidden", name=user.display_name),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            self._p(gid, "fun.send_delivered", mode=mode), ephemeral=True
        )
        # анонимность — для получателя; модерация всегда видит отправителя
        if self.settings.log_channel:
            log_channel = guild.get_channel(self.settings.log_channel)
            if log_channel is not None:
                try:
                    await cast(discord.abc.Messageable, log_channel).send(
                        f"📨 /send ({mode}): {interaction.user} → {user}: {text[:200]}",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.warning("Не удалось записать /send в лог-канал", exc_info=True)

    @app_commands.command(name="remind", description="Напомню в ЛС через N минут")
    @app_commands.checks.cooldown(1, 15)
    @app_commands.describe(minutes="Через сколько минут (1–10080)", text="О чём напомнить")
    @app_commands.guild_only()
    async def remind(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 10080],
        text: str,
    ) -> None:
        gid = guild_of(interaction).id
        due_at = datetime.now(UTC) + timedelta(minutes=minutes)
        await self.activity.add_reminder.execute(interaction.user.id, gid, text[:500], due_at)
        await interaction.response.send_message(
            self._p(
                gid,
                "fun.remind_scheduled",
                when=f"<t:{int(due_at.timestamp())}:R>",
                text=text[:200],
            ),
            ephemeral=True,
        )

    async def _reminder_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                due = await self.activity.pop_due_reminders.execute(datetime.now(UTC))
                for reminder in due:
                    # модуль выключен на сервере -> напоминание не шлём
                    if not flag_on(self.settings, self.gs, reminder.guild_id, "fun_enabled"):
                        continue
                    user = self.bot.get_user(reminder.user_id)
                    if user is None:
                        try:
                            user = await self.bot.fetch_user(reminder.user_id)
                        except discord.HTTPException:
                            continue
                    try:
                        await user.send(
                            self._p(reminder.guild_id, "fun.remind_dm", text=reminder.text)
                        )
                    except discord.Forbidden:
                        logger.info(
                            "ЛС закрыты — напоминание не доставлено",
                            extra={"user_id": reminder.user_id},
                        )
            except Exception:
                logger.exception("Ошибка цикла напоминаний")
            await asyncio.sleep(_REMINDER_CHECK_INTERVAL)
