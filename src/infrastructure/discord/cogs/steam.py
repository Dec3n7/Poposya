"""«Steam-игры»: /steam add подписывает игру, бот заводит по ней тред в форуме
и постит туда каждую новую официальную новость (обновления/патчи/анонсы) —
с картинкой и отформатированным текстом.

Ког тонкий: команды и Discord-работа здесь, доменная логика (что официальное,
что новее отметки) — в use cases, рендер BBCode → в bbcode."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.steam.di import SteamContainer
from src.application.steam.use_cases import GameUpdate
from src.config import Settings
from src.domain.steam.entities import TrackedGame
from src.domain.steam.refs import header_url, parse_app_ref, store_url
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.access import can_manage_feature
from src.infrastructure.discord.feature_flags import block_if_module_off, require_tier
from src.infrastructure.discord.interaction_ctx import guild_of, member_of
from src.infrastructure.persona_service import RegistryPersona
from src.infrastructure.steam.bbcode import render_news

logger = logging.getLogger(__name__)

# Тело новости в описание эмбеда: предел Discord — 4096, оставляем запас.
_NEWS_BODY_LIMIT = 4000


def _trim(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class SteamCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: SteamContainer,
        settings: Settings,
        guild_settings=None,
        entitlements=None,
        persona=None,
    ):
        self.bot = bot
        self.steam = container
        self.settings = settings
        self.gs = guild_settings
        self.entitlements = entitlements
        self.persona = persona if persona is not None else RegistryPersona()
        self._loop_started = False
        self._tasks: list[asyncio.Task] = []

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await block_if_module_off(interaction, self.settings, self.gs, "steam_enabled"):
            return False
        return await require_tier(interaction, self.entitlements, "steam_enabled")

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        for task in self._tasks:
            task.cancel()

    @property
    def _interval_seconds(self) -> int:
        return max(60, int(self.settings.steam_poll_interval_minutes) * 60)

    # --- фоновый цикл опроса новостей ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._loop_started:
            return
        self._loop_started = True
        self._tasks.append(asyncio.create_task(self._poll_loop()))
        logger.info(
            "Steam-трекер: цикл опроса запущен (интервал %d мин)",
            self.settings.steam_poll_interval_minutes,
        )

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self._run_poll()
            except Exception:
                logger.exception("Опрос новостей Steam упал на такте — продолжаю")

    async def _run_poll(self) -> None:
        updates = await self.steam.poll_news.execute()
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

    async def _announce(self, update: GameUpdate) -> None:
        thread = await self._get_thread(update.game.thread_id)
        if thread is None:
            logger.warning(
                "Тред игры не найден — пропускаю анонс",
                extra={"appid": update.game.appid, "thread_id": update.game.thread_id},
            )
            return
        for news in update.news:
            embed = self._news_embed(update.game.guild_id, update.game, news)
            try:
                await thread.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                logger.warning(
                    "Не удалось отправить новость в тред",
                    exc_info=True,
                    extra={"appid": update.game.appid, "gid": news.gid},
                )
                return  # отметку не двигаем — повторим на следующем такте
            if update.game.id is not None:
                await self.steam.mark_announced.execute(update.game.id, news.gid, news.date)

    # --- эмбеды ---

    def _news_embed(self, guild_id: int, game: TrackedGame, news) -> discord.Embed:
        body, image = render_news(news.contents)
        embed = discord.Embed(
            title=_trim(news.title or "Новость", 240),
            url=news.url or None,
            description=_trim(body, _NEWS_BODY_LIMIT) or None,
            color=accent(guild_id),
            timestamp=news.date,
        )
        embed.set_author(name=_trim(game.name, 240), url=store_url(game.appid))
        embed.set_image(url=image or header_url(game.appid))
        embed.set_footer(text=_trim(news.feedlabel or "Steam", 100))
        return embed

    def _game_card(self, guild_id: int, info) -> discord.Embed:
        embed = discord.Embed(
            title=_trim(info.name, 240),
            url=info.store_url or None,
            description=_trim(info.short_description, 1000) or None,
            color=accent(guild_id),
        )
        embed.add_field(name="AppID", value=str(info.appid), inline=True)
        if info.header_image:
            embed.set_image(url=info.header_image)
        embed.set_footer(text="Отслеживаю новости и обновления")
        return embed

    def _forum_channel(self, guild: discord.Guild) -> discord.ForumChannel | None:
        forum_id = self._cfg(guild.id, "steam_forum_channel")
        if not forum_id:
            return None
        channel = guild.get_channel(forum_id)
        return channel if isinstance(channel, discord.ForumChannel) else None

    # --- команды /steam ---

    # administrator-дефолт снят: доступ к add/remove решает бот — менеджер сервера
    # ИЛИ роль steam_manager_role (иначе роль-менеджер не увидела бы команду).
    steam_group = app_commands.Group(
        name="steam",
        description="Steam-игры: новости и обновления в форуме (менеджеры)",
        guild_only=True,
    )

    async def _deny_if_not_manager(
        self, interaction: discord.Interaction, guild: discord.Guild
    ) -> bool:
        """True (и уже ответил), если у вызвавшего нет прав управлять /steam."""
        if can_manage_feature(
            member_of(interaction), str(self._cfg(guild.id, "steam_manager_role"))
        ):
            return False
        await interaction.followup.send(
            "Нет доступа. Нужна роль-менеджер `/steam` (настройка `steam_manager_role`) "
            "или право «Управление сервером».",
            ephemeral=True,
        )
        return True

    @steam_group.command(name="add", description="Отслеживать новости игры Steam")
    @app_commands.describe(game="AppID (напр. 730) или ссылка на страницу игры в Steam")
    async def steam_add(self, interaction: discord.Interaction, game: str) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException:
            # interaction истёк (не уложились в 3с Discord — обычно из-за пачки
            # команд подряд): ответить уже нельзя, тихо выходим без шумной ошибки
            logger.warning("Не успел подтвердить /steam add вовремя (interaction истёк)")
            return
        guild = guild_of(interaction)
        if await self._deny_if_not_manager(interaction, guild):
            return
        appid = parse_app_ref(game)
        if appid is None:
            await interaction.followup.send(
                "Не разобрал игру. Укажите AppID (напр. `730`) или ссылку "
                "`https://store.steampowered.com/app/730/...`.",
                ephemeral=True,
            )
            return

        forum = self._forum_channel(guild)
        if forum is None:
            await interaction.followup.send(
                "Сначала задайте форум-канал для игр: настройка `steam_forum_channel` "
                "(панель «Настройки» или `/config`).",
                ephemeral=True,
            )
            return

        snapshot = await self.steam.fetch_game.execute(appid)
        if snapshot is None:
            await interaction.followup.send(
                f"Не удалось получить игру `{appid}` из Steam — магазин медленно ответил "
                "или такой игры нет. Попробуйте ещё раз через минуту.",
                ephemeral=True,
            )
            return

        existing = await self.steam.get_game.execute(guild.id, appid)
        if existing is not None:
            await interaction.followup.send(
                f"**{snapshot.info.name}** уже отслеживается — <#{existing.thread_id}>.",
                ephemeral=True,
            )
            return

        card = self._game_card(guild.id, snapshot.info)
        try:
            created = await forum.create_thread(
                name=_trim(snapshot.info.name, 100),
                embed=card,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Нет прав создавать посты в форум-канале игр.", ephemeral=True
            )
            return
        except discord.HTTPException:
            logger.warning("Не удалось создать тред игры", exc_info=True)
            await interaction.followup.send(
                "Не удалось создать пост в форуме. Попробуйте ещё раз.", ephemeral=True
            )
            return

        tracked = await self.steam.add_game.execute(
            guild_id=guild.id,
            appid=appid,
            name=snapshot.info.name,
            added_by=interaction.user.id,
            thread_id=created.thread.id,
            baseline=snapshot.latest_news,
            now=datetime.now(UTC),
        )

        # сразу показать последнюю официальную новость (база уже на ней — опрос
        # не повторит). Сбой не критичен: игра отслеживается, карточка на месте
        if snapshot.latest_news is not None:
            try:
                await created.thread.send(
                    embed=self._news_embed(guild.id, tracked, snapshot.latest_news),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning(
                    "Не удалось отправить последнюю новость при добавлении", exc_info=True
                )

        no_news = (
            ""
            if snapshot.latest_news
            else "\nОфициальных новостей пока нет — сообщу, когда появятся."
        )
        await interaction.followup.send(
            f"Отслеживаю **{snapshot.info.name}** → {created.thread.mention}.{no_news}",
            ephemeral=True,
        )

    @steam_group.command(name="list", description="Отслеживаемые игры сервера")
    async def steam_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        games = await self.steam.list_games.execute(guild.id)
        if not games:
            await interaction.followup.send(
                "Пока ничего не отслеживается. Добавить — `/steam add <AppID>`.",
                ephemeral=True,
            )
            return
        lines = [f"• **{g.name}** (`{g.appid}`) → <#{g.thread_id}>" for g in games]
        embed = discord.Embed(
            title=f"Steam-игры ({len(games)})",
            description=_trim("\n".join(lines), 4000),
            color=accent(guild.id),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @steam_group.command(name="remove", description="Перестать отслеживать игру")
    @app_commands.describe(game="AppID или название из /steam list")
    async def steam_remove(self, interaction: discord.Interaction, game: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        if await self._deny_if_not_manager(interaction, guild):
            return
        appid = parse_app_ref(game)
        if appid is None:
            await interaction.followup.send(
                "Укажите AppID (число) или выберите из списка.", ephemeral=True
            )
            return
        removed = await self.steam.remove_game.execute(guild.id, appid)
        if removed:
            await interaction.followup.send(
                f"Больше не отслеживаю игру `{appid}`. Тред в форуме остаётся — "
                "удалите его вручную, если нужно.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Игры `{appid}` не было в списке. Проверьте `/steam list`.", ephemeral=True
            )

    @steam_remove.autocomplete("game")
    async def _remove_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        games = await self.steam.list_games.execute(interaction.guild_id)
        needle = current.lower()
        return [
            app_commands.Choice(name=f"{g.name} ({g.appid})"[:100], value=str(g.appid))
            for g in games
            if needle in g.name.lower() or needle in str(g.appid)
        ][:25]
