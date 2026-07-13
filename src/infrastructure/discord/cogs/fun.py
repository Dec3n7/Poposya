import asyncio
import logging
import random
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from src.application.activity.di import ActivityContainer
from src.application.ai_chat.service import ChatService
from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.infrastructure.ai.rate_limiter import InMemoryRateLimiter

logger = logging.getLogger(__name__)

_EMBED_COLOR = 0x9B59B6
_REMINDER_CHECK_INTERVAL = 30

_TOPICS = [
    "Какая игра тебя по-настоящему удивила за последний год?",
    "Кофе или чай — и почему твой выбор правильный?",
    "Лучший саундтрек из игры, который ты слушаешь отдельно?",
    "Есть ли фильм, который все хвалят, а тебе не зашёл?",
    "Какую способность из игр ты бы забрал в реальную жизнь?",
    "Самая переоценённая вещь в интернете сейчас?",
    "Что ты умеешь делать лучше большинства здесь?",
    "Идеальный вечер: расписание по пунктам.",
    "Какой босс в играх заставил тебя страдать сильнее всего?",
    "Если бы завтра переезд в любую страну — куда и почему?",
    "Какая книга или манга изменила твой взгляд на что-то?",
    "Дождь за окном: уют или тоска?",
    "Какую еду ты можешь есть бесконечно?",
    "Самый бесполезный факт, который ты знаешь?",
    "Что бы ты сказал себе пять лет назад?",
]

_RULES_TEXT = (
    "**Правила сервера** *(тестовый текст — заменить на настоящие правила)*\n\n"
    "`1.` Уважай собеседников. Сарказм — можно, травля — нет.\n"
    "`2.` Без NSFW, шок-контента и политики.\n"
    "`3.` Спам и флуд караются мутом — я слежу. ✂️👁🖤\n"
    "`4.` Реклама — только с разрешения администрации.\n"
    "`5.` Ники и аватары — читаемые и приличные.\n"
    "`6.` Споры решает администрация. Финально.\n"
)


class FunCog(commands.Cog):
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
    ):
        self.bot = bot
        self.activity = activity
        self.relationship = relationship
        self.chat = chat_service
        self.settings = settings
        self.finds = finds
        self.music = music
        self.cinema = cinema
        self._reminder_task: asyncio.Task | None = None
        self._send_limiter = InMemoryRateLimiter()

    async def cog_load(self) -> None:
        # напоминания хранятся в БД — рестарт не сбрасывает таймеры
        self._reminder_task = asyncio.create_task(self._reminder_loop())
        logger.info(
            "Развлечения: цикл напоминаний запущен (проверка каждые %d с)", _REMINDER_CHECK_INTERVAL
        )

    def cog_unload(self) -> None:
        if self._reminder_task is not None:
            self._reminder_task.cancel()

    # --- простые развлечения ---

    _DICE_MAX_ROUNDS = 10
    _DICE_MAX_PLAYERS = 20  # больше не влезает в сообщение по-человечески

    @app_commands.command(name="dice", description="Бросить кубик — или вызвать других на дуэль")
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
        for member in (interaction.user, *mentioned):
            if member not in players:
                players.append(member)
        if len(players) > self._DICE_MAX_PLAYERS:
            await interaction.response.send_message(
                f"Больше {self._DICE_MAX_PLAYERS} игроков — это уже лотерея, "
                "а не кости. Сократи список.",
                ephemeral=True,
            )
            return

        if len(players) < 2:
            if had_bots:
                await interaction.response.send_message(
                    "Боты в кости не играют — у них всё по алгоритму. Позови живых.",
                    ephemeral=True,
                )
                return
            result = random.randint(1, sides)
            await interaction.response.send_message(f"🎲 d{sides} → **{result}**")
            return

        # дуэль: бросают все; ничья наверху — тайбрейк только между лидерами
        lines: list[str] = [f"🎲 **Дуэль на кубиках** (d{sides})"]
        contenders = players
        winner: discord.Member | None = None
        for round_no in range(1, self._DICE_MAX_ROUNDS + 1):
            rolls = [(member, random.randint(1, sides)) for member in contenders]
            lines.append(
                f"**Раунд {round_no}:** " + ", ".join(f"{m.display_name} — `{r}`" for m, r in rolls)
            )
            best = max(result for _, result in rolls)
            top = [member for member, result in rolls if result == best]
            if len(top) == 1:
                winner = top[0]
                lines.append(f"🏆 Победа: {winner.mention} — выбросил **{best}**!")
                break
            lines.append(
                "Ничья между " + " и ".join(m.display_name for m in top) + " — перебрасываю. 🎲"
            )
            contenders = top
        if winner is None:
            lines.append(
                f"{self._DICE_MAX_ROUNDS} раундов подряд ничья. Судьба говорит вам "
                "дружить. Мне надоело."
            )
        await interaction.response.send_message(
            "\n".join(lines)[:2000],
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @app_commands.command(name="coinflip", description="Подбросить монетку")
    async def coinflip(self, interaction: discord.Interaction) -> None:
        side = random.choice(("Орёл 🦅", "Решка 🪙"))
        await interaction.response.send_message(f"**{side}**")

    @app_commands.command(name="topic", description="Случайная тема для разговора")
    async def topic(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"💬 {random.choice(_TOPICS)}")

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
    _ATTITUDE = {
        1: "настороженное",
        2: "сдержанное",
        3: "нейтральное",
        4: "тёплое",
        5: "доверительное",
        6: "близкое",
        7: "ты — единственный",
    }
    _OPENERS = {
        1: "Это ты… Напомни, как тебя зовут?",
        2: "Это ты, {name}… Я помню каждый твой шаг.",
        3: "Это ты, {name}… Я помню каждый твой шаг.",
        4: "О, {name}. Я как раз о тебе думала. Не льсти себе.",
        5: "{name}. Кофе уже остыл, пока ты собирался зайти.",
        6: "{name}. Моё вечернее расписание знает твоё имя.",
        7: "{name}. Кресло у окна — твоё.",
    }
    _FOOTERS = {
        1: "Пока ты просто имя в списке.",
        2: "Ты уже не просто имя в списке.",
        3: "Ты уже не просто имя в списке.",
        4: "С тобой этот дом немного теплее.",
        5: "С тобой этот дом немного теплее.",
        6: "Виски на двоих — это про тебя.",
        7: "✂️👁🖤",
    }

    def _relationship_bar(self, points: int, next_threshold: int | None, width: int = 10) -> str:
        thresholds = self.relationship.policy.thresholds
        if next_threshold is None:
            return "▰" * width
        lower = max((t for t in thresholds if t <= points), default=0)
        span = max(1, next_threshold - lower)
        filled = min(width, int(width * (points - lower) / span))
        return "▰" * filled + "▱" * (width - filled)

    @staticmethod
    def _last_seen(last: datetime | None) -> str:
        if last is None:
            return "ещё ни разу не заговаривал со мной"
        days = (datetime.now(UTC) - last).days
        if days <= 0:
            return "говорил со мной сегодня"
        if days == 1:
            return "говорил со мной вчера"
        return f"говорил со мной {days} дн. назад"

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
                    line = f"🗃 Находки: {parts}"
                    if gifted:
                        line += f" · 🎁 подарено мне: {gifted}"
                    lines.append(line)
            if self.music is not None:
                liked = await self.music.list_liked.execute(user_id)
                if liked:
                    lines.append(f"🎧 Лайкнутых треков: **{len(liked)}**")
            if self.cinema is not None:
                stats = await self.cinema.cinema_profile.execute(guild_id, user_id)
                if stats.proposed or stats.ratings_count:
                    line = (
                        f"🎬 Кино: предложено **{stats.proposed}**, "
                        f"оценок **{stats.ratings_count}**"
                    )
                    if stats.avg_given is not None:
                        line += f" (средняя {stats.avg_given})"
                    lines.append(line)
            hours = await self.activity.get_voice_hours.execute(guild_id, user_id)
            if hours >= 0.5:
                lines.append(f"🎙 В войсе со мной: **{hours:.1f} ч**")
        except Exception:
            logger.exception("Витрина профиля не собралась")
        return lines

    @app_commands.command(name="profile", description="Профиль пользователя глазами Попоси")
    @app_commands.describe(user="Чей профиль (по умолчанию — твой)")
    @app_commands.guild_only()
    async def profile(
        self, interaction: discord.Interaction, user: discord.Member | None = None
    ) -> None:
        target = user or interaction.user
        if target.bot:
            await interaction.response.send_message("У ботов нет души. И профиля.", ephemeral=True)
            return
        await interaction.response.defer()
        info = await self.relationship.get_rank.execute(target.id, interaction.guild_id)
        role_name = (
            self.relationship.role_names[info.role_index]
            if info.role_index is not None
            else "☕ Случайный прохожий (пока без статуса)"
        )

        embed = discord.Embed(
            description=f"**{self._OPENERS[info.level].format(name=target.display_name)}**",
            color=_EMBED_COLOR,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        attachment = f"{role_name}\n`{self._relationship_bar(info.points, info.next_threshold)}` **{info.points}** очков"
        if info.next_threshold is not None:
            attachment += f"\n*До следующего статуса: {info.next_threshold - info.points}*"
        embed.add_field(name="Привязанность", value=attachment, inline=False)

        activity_lines = [f"👁 {self._last_seen(info.last_dialog_at)}"]
        if info.deep_dialogs:
            activity_lines.insert(0, f"🍷 Долгих разговоров: **{info.deep_dialogs}**")
        embed.add_field(name="Активность", value="\n".join(activity_lines), inline=False)

        showcase = await self._build_showcase(interaction.guild_id, target.id)
        if showcase:
            embed.add_field(name="Витрина", value="\n".join(showcase)[:1000], inline=False)

        known: list[str] = []
        if info.user_notes:
            known.extend(f"• {line}" for line in info.user_notes.splitlines() if line.strip())
        elif info.survey.interests:
            known.append(f"• Интересы: {info.survey.interests}")
        if info.survey.season:
            known.append(f"• Любимое время года: {info.survey.season}")
        if not known:
            known.append("• Пока почти ничего. Исправь это.")
        embed.add_field(name="Что я знаю о тебе…", value="\n".join(known)[:1000], inline=False)

        embed.add_field(name="Моё отношение", value=self._ATTITUDE[info.level])
        birthday = (
            f"{info.birthday_day} {self._MONTHS_RU[info.birthday_month]}"
            if info.birthday_day and info.birthday_month
            else "—"
        )
        embed.add_field(name="День рождения", value=birthday)
        badges: list[str] = []
        if info.survey.completed:
            badges.append("📋 Анкета")
        if info.birthday_day:
            badges.append("🎂 Дата в календаре")
        if info.deep_dialogs >= 5:
            badges.append("🍷 Вечерние разговоры")
        if info.is_exclusive:
            badges.append("✂️👁🖤 Кресло у окна")
        embed.add_field(name="✨ Отметки", value="\n".join(badges) or "—")

        embed.set_footer(text=self._FOOTERS[info.level])
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
        ok = await self.relationship.set_birthday.execute(
            interaction.user.id, interaction.guild_id, day, month
        )
        if not ok:
            await interaction.followup.send(
                "Такой даты не существует. Попробуй ещё раз, календарь в помощь.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Записала: {day:02d}.{month:02d}. Я помню дни рождения своих гостей — "
            "теперь и твой. ✂️👁🖤",
            ephemeral=True,
        )

    # --- утилиты ---

    @app_commands.command(name="rules", description="Опубликовать правила сервера (разово)")
    @app_commands.default_permissions(administrator=True)
    async def rules(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="📜 Правила", description=_RULES_TEXT, color=_EMBED_COLOR)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverstats", description="Статистика сервера")
    @app_commands.guild_only()
    async def serverstats(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans
        embed = discord.Embed(title=f"📊 {guild.name}", color=_EMBED_COLOR)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Участников", value=f"{humans} людей + {bots} ботов")
        embed.add_field(
            name="Каналов",
            value=f"{len(guild.text_channels)} текстовых, {len(guild.voice_channels)} голосовых",
        )
        embed.add_field(
            name="Бустов", value=f"{guild.premium_subscription_count} (ур. {guild.premium_tier})"
        )
        embed.add_field(name="Создан", value=f"<t:{int(guild.created_at.timestamp())}:D>")
        if guild.owner:
            embed.add_field(name="Владелец", value=str(guild.owner))
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="send", description="Передать сообщение через Попосю в ЛС")
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
        if user.bot:
            await interaction.response.send_message(
                "Ботам письма не ношу. У них нет души — читать нечем.", ephemeral=True
            )
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "Письмо самому себе? Займись чем-нибудь. ✂️👁🖤", ephemeral=True
            )
            return
        key = f"send:{interaction.guild_id}:{interaction.user.id}"
        if not self._send_limiter.try_acquire(key, self.settings.send_per_hour):
            await interaction.response.send_message(
                "Я курьер, а не почтовое отделение. На сегодня с тебя хватит — позже.",
                ephemeral=True,
            )
            return
        # дальше — сетевые вызовы (ЛС получателю): отвечаем через defer
        await interaction.response.defer(ephemeral=True)

        text = text[:1500]
        if mode == "анонимно":
            dm_text = (
                f"📨 **Анонимное письмо** (сервер «{interaction.guild.name}»):\n\n"
                f"{text}\n\n"
                "-# Отправителя не выдам. Но я-то знаю, кто это. ✂️👁🖤"
            )
        else:
            dm_text = (
                f"📨 **Письмо от {interaction.user.display_name}** "
                f"(сервер «{interaction.guild.name}»):\n\n"
                f"{text}\n\n"
                "-# Передала лично. Попося ✂️👁🖤"
            )
        try:
            await user.send(dm_text)
        except discord.Forbidden:
            await interaction.followup.send(
                f"У {user.display_name} закрыты личные сообщения — письмо не доставить.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Доставлено ({mode}). Содержимое я, разумеется, прочитала.", ephemeral=True
        )
        # анонимность — для получателя; модерация всегда видит отправителя
        if self.settings.log_channel:
            log_channel = interaction.guild.get_channel(self.settings.log_channel)
            if log_channel is not None:
                try:
                    await log_channel.send(
                        f"📨 /send ({mode}): {interaction.user} → {user}: {text[:200]}",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.warning("Не удалось записать /send в лог-канал", exc_info=True)

    @app_commands.command(name="remind", description="Напомню в ЛС через N минут")
    @app_commands.describe(minutes="Через сколько минут (1–10080)", text="О чём напомнить")
    @app_commands.guild_only()
    async def remind(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 10080],
        text: str,
    ) -> None:
        due_at = datetime.now(UTC) + timedelta(minutes=minutes)
        await self.activity.add_reminder.execute(
            interaction.user.id, interaction.guild_id, text[:500], due_at
        )
        await interaction.response.send_message(
            f"⏰ Напомню в ЛС <t:{int(due_at.timestamp())}:R>: {text[:200]}\n"
            "Проверь, что личные сообщения от участников сервера открыты.",
            ephemeral=True,
        )

    async def _reminder_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                due = await self.activity.pop_due_reminders.execute(datetime.now(UTC))
                for reminder in due:
                    user = self.bot.get_user(reminder.user_id)
                    if user is None:
                        try:
                            user = await self.bot.fetch_user(reminder.user_id)
                        except discord.HTTPException:
                            continue
                    try:
                        await user.send(f"⏰ Ты просил напомнить: {reminder.text}")
                    except discord.Forbidden:
                        logger.info(
                            "ЛС закрыты — напоминание не доставлено",
                            extra={"user_id": reminder.user_id},
                        )
            except Exception:
                logger.exception("Ошибка цикла напоминаний")
            await asyncio.sleep(_REMINDER_CHECK_INTERVAL)
