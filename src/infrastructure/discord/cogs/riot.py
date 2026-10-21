"""«Riot-игры»: /riot add подписывает игру Riot (сейчас Valorant), бот заводит
по ней тред в форуме и постит туда каждую новость с сайта игры — с картинкой и
отформатированным текстом.

Ког тонкий: команды и Discord-работа здесь, доменная логика (что новее отметки)
— в use cases, HTML → markdown — в infrastructure/riot. Цикл опроса — общий
хелпер application/news/poller."""

import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.news.poller import NewsPoller
from src.application.riot.di import RiotContainer
from src.application.riot.dto import RiotNewsItemDTO
from src.application.riot.use_cases import GameAlreadyTrackedError, RiotGameUpdate
from src.config import Settings
from src.domain.riot.entities import TrackedRiotGame
from src.domain.riot.refs import GAMES, RiotGame, game_label, parse_game
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.access import can_manage_feature
from src.infrastructure.discord.feature_flags import block_if_module_off, require_tier
from src.infrastructure.discord.interaction_ctx import guild_of, member_of
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

# Тело новости в описание эмбеда: предел Discord — 4096, оставляем запас.
_NEWS_BODY_LIMIT = 4000


def _trim(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class RiotCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: RiotContainer,
        settings: Settings,
        guild_settings=None,
        entitlements=None,
        persona=None,
    ):
        self.bot = bot
        self.riot = container
        self.settings = settings
        self.gs = guild_settings
        self.entitlements = entitlements
        self.persona = persona if persona is not None else RegistryPersona()
        self._poller = NewsPoller(
            name="Riot-трекер",
            interval_seconds=self._interval_seconds,
            tick=self._run_poll,
        )

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await block_if_module_off(interaction, self.settings, self.gs, "riot_enabled"):
            return False
        return await require_tier(interaction, self.entitlements, "riot_enabled")

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        self._poller.stop()

    @property
    def _interval_seconds(self) -> int:
        return max(60, int(self.settings.riot_poll_interval_minutes) * 60)

    # --- фоновый цикл опроса новостей ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._poller.started:
            return
        self._poller.start()

    async def _run_poll(self) -> None:
        updates = await self.riot.poll_news.execute()
        for update in updates:
            await self._announce(update)

    async def _get_thread(self, thread_id: int) -> discord.abc.Messageable | None:
        if not thread_id:
            return None
        channel = self.bot.get_channel(thread_id)
        if channel is not None:
            return cast(discord.abc.Messageable, channel)
        try:
            return cast(discord.abc.Messageable, await self.bot.fetch_channel(thread_id))
        except discord.HTTPException:
            return None

    async def _announce(self, update: RiotGameUpdate) -> None:
        thread = await self._get_thread(update.game.thread_id)
        if thread is None:
            logger.warning(
                "Тред игры Riot не найден — пропускаю анонс",
                extra={"game": update.game.game, "thread_id": update.game.thread_id},
            )
            return
        for news in update.news:
            embed = self._news_embed(update.game.guild_id, update.game, news)
            try:
                await thread.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                logger.warning(
                    "Не удалось отправить новость Riot в тред",
                    exc_info=True,
                    extra={"game": update.game.game, "gid": news.gid},
                )
                return  # отметку не двигаем — повторим на следующем такте
            if update.game.id is not None:
                await self.riot.mark_announced.execute(update.game.id, news.gid, news.date)

    # --- эмбеды ---

    def _news_embed(
        self, guild_id: int, game: TrackedRiotGame, news: RiotNewsItemDTO
    ) -> discord.Embed:
        embed = discord.Embed(
            title=_trim(news.title or "Новость", 240),
            url=news.url or None,
            description=_trim(news.contents, _NEWS_BODY_LIMIT) or None,
            color=accent(guild_id),
            timestamp=news.date,
        )
        embed.set_author(name=_trim(game.name, 240))
        if news.is_video:
            embed.add_field(name="Видео", value=news.url, inline=False)
            if news.image_url:
                embed.set_thumbnail(url=news.image_url)
        elif news.image_url:
            embed.set_image(url=news.image_url)
        embed.set_footer(text=_trim(news.category or "Riot Games", 100))
        return embed

    def _game_card(self, guild_id: int, snapshot) -> discord.Embed:
        return discord.Embed(
            title=_trim(snapshot.name, 240),
            description="Новости игры с официального сайта Riot.",
            color=accent(guild_id),
        )

    def _forum_channel(self, guild: discord.Guild) -> discord.ForumChannel | None:
        forum_id = self._cfg(guild.id, "riot_forum_channel")
        if not forum_id:
            return None
        channel = guild.get_channel(forum_id)
        return channel if isinstance(channel, discord.ForumChannel) else None

    # --- команды /riot ---

    riot_group = app_commands.Group(
        name="riot",
        description="Riot-игры: новости в форуме (менеджеры)",
        guild_only=True,
    )

    async def _deny_if_not_manager(
        self, interaction: discord.Interaction, guild: discord.Guild
    ) -> bool:
        """True (и уже ответил), если у вызвавшего нет прав управлять /riot."""
        if can_manage_feature(member_of(interaction), ""):
            return False
        await interaction.followup.send(
            "Нет доступа. Нужно право «Управление сервером».", ephemeral=True
        )
        return True

    @riot_group.command(name="add", description="Отслеживать новости игры Riot")
    @app_commands.describe(game="Игра Riot (по умолчанию Valorant)")
    @app_commands.choices(
        game=[app_commands.Choice(name=game_label(RiotGame(key)), value=key) for key in GAMES]
    )
    async def riot_add(
        self, interaction: discord.Interaction, game: str = RiotGame.VALORANT.value
    ) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException:
            logger.warning("Не успел подтвердить /riot add вовремя (interaction истёк)")
            return
        guild = guild_of(interaction)
        if await self._deny_if_not_manager(interaction, guild):
            return

        parsed = parse_game(game)
        if parsed is None:
            await interaction.followup.send("Неизвестная игра Riot.", ephemeral=True)
            return

        forum = self._forum_channel(guild)
        if forum is None:
            await interaction.followup.send(
                "Сначала задайте форум-канал для Riot: настройка `riot_forum_channel` "
                "(панель «Настройки» или `/config`).",
                ephemeral=True,
            )
            return

        locale = str(self._cfg(guild.id, "riot_locale"))
        snapshot = await self.riot.fetch_news.execute(parsed, locale)
        if snapshot is None:
            await interaction.followup.send(
                "Не удалось получить новости Riot — сайт медленно ответил или изменил "
                "формат. Попробуйте ещё раз через минуту.",
                ephemeral=True,
            )
            return

        existing = await self.riot.get_game.execute(guild.id, parsed.value)
        if existing is not None:
            await interaction.followup.send(
                f"**{snapshot.name}** уже отслеживается — <#{existing.thread_id}>.",
                ephemeral=True,
            )
            return

        card = self._game_card(guild.id, snapshot)
        try:
            created = await forum.create_thread(
                name=_trim(f"{snapshot.name} — новости", 100),
                embed=card,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Нет прав создавать посты в форум-канале Riot.", ephemeral=True
            )
            return
        except discord.HTTPException:
            logger.warning("Не удалось создать тред игры Riot", exc_info=True)
            await interaction.followup.send(
                "Не удалось создать пост в форуме. Попробуйте ещё раз.", ephemeral=True
            )
            return

        try:
            tracked = await self.riot.add_game.execute(
                guild_id=guild.id,
                game=parsed,
                name=snapshot.name,
                added_by=interaction.user.id,
                thread_id=created.thread.id,
                baseline=snapshot.latest_news,
                now=datetime.now(UTC),
            )
        except GameAlreadyTrackedError:
            # гонка двух одновременных add: уникальность поймана, тред-дубль убираем
            try:
                await created.thread.delete()
            except discord.HTTPException:
                logger.warning("Не удалось удалить дубль-тред Riot", exc_info=True)
            await interaction.followup.send(
                f"**{snapshot.name}** уже отслеживается (кто-то успел добавить раньше).",
                ephemeral=True,
            )
            return

        # сразу показать последнюю новость (база уже на ней — опрос не повторит).
        # Сбой не критичен: игра отслеживается, карточка на месте
        if snapshot.latest_news is not None:
            try:
                await created.thread.send(
                    embed=self._news_embed(guild.id, tracked, snapshot.latest_news),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning(
                    "Не удалось отправить последнюю новость Riot при добавлении",
                    exc_info=True,
                )

        no_news = "" if snapshot.latest_news else "\nНовостей пока нет — сообщу, когда появятся."
        await interaction.followup.send(
            f"Отслеживаю **{snapshot.name}** → {created.thread.mention}.{no_news}",
            ephemeral=True,
        )

    @riot_group.command(name="list", description="Отслеживаемые игры Riot сервера")
    async def riot_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        games = await self.riot.list_games.execute(guild.id)
        if not games:
            await interaction.followup.send(
                "Пока ничего не отслеживается. Добавить — `/riot add`.",
                ephemeral=True,
            )
            return
        lines = [f"• **{g.name}** (`{g.game}`) → <#{g.thread_id}>" for g in games]
        embed = discord.Embed(
            title=f"Riot-игры ({len(games)})",
            description=_trim("\n".join(lines), 4000),
            color=accent(guild.id),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @riot_group.command(name="remove", description="Перестать отслеживать игру Riot")
    @app_commands.describe(game="Игра из /riot list")
    async def riot_remove(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        if await self._deny_if_not_manager(interaction, guild):
            return
        normalized = game.strip().lower()
        removed = await self.riot.remove_game.execute(guild.id, normalized)
        if removed:
            await interaction.followup.send(
                f"Больше не отслеживаю игру `{normalized}`. Тред в форуме остаётся — "
                "удалите его вручную, если нужно.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Игры `{normalized}` не было в списке. Проверьте `/riot list`.",
                ephemeral=True,
            )

    @riot_remove.autocomplete("game")
    async def _remove_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        games = await self.riot.list_games.execute(interaction.guild_id)
        needle = current.lower()
        return [
            app_commands.Choice(name=f"{g.name} ({g.game})"[:100], value=g.game)
            for g in games
            if needle in g.name.lower() or needle in g.game.lower()
        ][:25]
