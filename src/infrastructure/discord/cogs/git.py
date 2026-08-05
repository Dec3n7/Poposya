"""«GitHub-репозитории»: /git add подписывает репозиторий, бот заводит по нему
тред в форуме и постит туда каждый новый релиз.

Ког — тонкий: команды и Discord-работа (форум, треды, эмбеды) здесь, вся
доменная логика (что считать новым релизом, отметки) — в use cases. Фоновый
цикл опроса запускается на on_ready и отменяется в cog_unload (как в находках)."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.repos.di import ReposContainer
from src.application.repos.use_cases import RepoUpdate
from src.config import Settings
from src.domain.repos.entities import TrackedRepo
from src.domain.repos.refs import parse_repo_ref
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.access import can_manage_feature
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.interaction_ctx import guild_of, member_of
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

# Целевой потолок запросов к GitHub в час: анонимный лимит — 60/ч на IP, держим
# запас. Безопасное число репозиториев зависит от интервала опроса.
_RATE_BUDGET_PER_HOUR = 50

# Сколько символов текста релиза («What's Changed») влезает в описание эмбеда.
# Предел Discord — 4096 на описание и 6000 на весь эмбед; оставляем запас на
# заголовок/поля/футер.
_RELEASE_BODY_LIMIT = 4000


def _trim(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class GitCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: ReposContainer,
        settings: Settings,
        guild_settings=None,
        persona=None,
    ):
        self.bot = bot
        self.repos = container
        self.settings = settings
        self.gs = guild_settings
        self.persona = persona if persona is not None else RegistryPersona()
        self._loop_started = False
        self._tasks: list[asyncio.Task] = []

    # --- инфраструктура кога ---

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await block_if_module_off(interaction, self.settings, self.gs, "git_enabled")

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        for task in self._tasks:
            task.cancel()

    @property
    def _interval_seconds(self) -> int:
        return max(60, int(self.settings.github_poll_interval_minutes) * 60)

    def _safe_repo_cap(self) -> int:
        """Сколько репозиториев суммарно можно опрашивать, не пробивая бюджет
        запросов при текущем интервале (1 запрос на репо за такт)."""
        cycles_per_hour = 3600 / self._interval_seconds
        return max(1, int(_RATE_BUDGET_PER_HOUR / cycles_per_hour))

    # --- фоновый цикл опроса релизов ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._loop_started:
            return
        self._loop_started = True
        self._tasks.append(asyncio.create_task(self._poll_loop()))
        logger.info(
            "GitHub-трекер: цикл опроса запущен (интервал %d мин)",
            self.settings.github_poll_interval_minutes,
        )

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self._run_poll()
            except Exception:
                logger.exception("Опрос релизов упал на такте — продолжаю")

    async def _run_poll(self) -> None:
        updates = await self.repos.poll_releases.execute()
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

    async def _announce(self, update: RepoUpdate) -> None:
        thread = await self._get_thread(update.repo.thread_id)
        if thread is None:
            logger.warning(
                "Тред репозитория не найден — пропускаю анонс",
                extra={"repo": update.repo.full_name, "thread_id": update.repo.thread_id},
            )
            return
        releases = update.new_releases
        for index, release in enumerate(releases):
            embed = self._release_embed(update.repo.guild_id, update.repo, release)
            try:
                await thread.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                logger.warning(
                    "Не удалось отправить релиз в тред",
                    exc_info=True,
                    extra={"repo": update.repo.full_name, "tag": release.tag_name},
                )
                return  # отметку не двигаем — повторим на следующем такте
            # etag сохраняем только после последнего релиза: если отправка
            # прервётся раньше, следующий опрос заново получит список (не 304)
            etag = update.etag if index == len(releases) - 1 else None
            if update.repo.id is not None:
                await self.repos.mark_announced.execute(
                    update.repo.id, release.id, release.published_at, etag
                )

    # --- эмбеды ---

    def _release_embed(self, guild_id: int, repo: TrackedRepo, release) -> discord.Embed:
        title = f"{repo.full_name} — {release.name or release.tag_name or 'релиз'}"
        embed = discord.Embed(
            title=_trim(title, 240),
            url=release.html_url or None,
            description=_trim(release.body, _RELEASE_BODY_LIMIT) or None,
            color=accent(guild_id),
            timestamp=release.published_at,
        )
        tag = release.tag_name or "—"
        embed.add_field(name="Тег", value=_trim(tag, 100), inline=True)
        if release.prerelease:
            embed.add_field(name="Тип", value="pre-release", inline=True)
        footer = f"🚀 Новый релиз · {release.author}".strip(" ·") if release.author else "🚀 Новый релиз"
        embed.set_footer(text=_trim(footer, 100))
        return embed

    def _repo_card(self, guild_id: int, info, latest) -> discord.Embed:
        embed = discord.Embed(
            title=_trim(info.full_name, 240),
            url=info.html_url or None,
            description=_trim(info.description, 1000) or None,
            color=accent(guild_id),
        )
        embed.add_field(name="⭐ Звёзды", value=str(info.stars), inline=True)
        if info.language:
            embed.add_field(name="Язык", value=_trim(info.language, 50), inline=True)
        embed.add_field(
            name="Последний релиз",
            value=(latest.tag_name or latest.name or "есть") if latest else "нет",
            inline=True,
        )
        embed.set_footer(text="Отслеживаю релизы")
        return embed

    def _forum_channel(self, guild: discord.Guild) -> discord.ForumChannel | None:
        forum_id = self._cfg(guild.id, "git_forum_channel")
        if not forum_id:
            return None
        channel = guild.get_channel(forum_id)
        return channel if isinstance(channel, discord.ForumChannel) else None

    # --- команды /git ---

    # administrator-дефолт снят намеренно: доступ к add/remove решает бот —
    # менеджер сервера ИЛИ роль git_manager_role (см. _deny_if_not_manager).
    # Иначе роль-менеджер вообще не увидела бы команду (Discord скрыл бы её).
    git_group = app_commands.Group(
        name="git",
        description="GitHub-репозитории: релизы в форуме (менеджеры)",
        guild_only=True,
    )

    async def _deny_if_not_manager(
        self, interaction: discord.Interaction, guild: discord.Guild
    ) -> bool:
        """True (и уже ответил), если у вызвавшего нет прав управлять /git."""
        if can_manage_feature(member_of(interaction), str(self._cfg(guild.id, "git_manager_role"))):
            return False
        await interaction.followup.send(
            "Нет доступа. Нужна роль-менеджер `/git` (настройка `git_manager_role`) "
            "или право «Управление сервером».",
            ephemeral=True,
        )
        return True

    @git_group.command(name="add", description="Отслеживать релизы репозитория GitHub")
    @app_commands.describe(repo="owner/name или ссылка на репозиторий GitHub")
    async def git_add(self, interaction: discord.Interaction, repo: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        if await self._deny_if_not_manager(interaction, guild):
            return
        parsed = parse_repo_ref(repo)
        if parsed is None:
            await interaction.followup.send(
                "Не разобрал ссылку. Формат: `owner/name` или "
                "`https://github.com/owner/name`.",
                ephemeral=True,
            )
            return
        owner, name = parsed

        forum = self._forum_channel(guild)
        if forum is None:
            await interaction.followup.send(
                "Сначала задайте форум-канал для релизов: настройка "
                "`git_forum_channel` (панель «Настройки» или `/config`).",
                ephemeral=True,
            )
            return

        snapshot = await self.repos.fetch_repo.execute(owner, name)
        if snapshot is None:
            await interaction.followup.send(
                f"Репозиторий `{owner}/{name}` не найден на GitHub (или он приватный).",
                ephemeral=True,
            )
            return
        # каноничные owner/name с GitHub — дедуп не зависит от регистра ввода
        owner, name = snapshot.info.owner, snapshot.info.name

        existing = await self.repos.get_repo.execute(guild.id, owner, name)
        if existing is not None:
            await interaction.followup.send(
                f"`{owner}/{name}` уже отслеживается — <#{existing.thread_id}>.",
                ephemeral=True,
            )
            return

        # предупреждение о лимите: без токена анонимный бюджет тесный
        cap = self._safe_repo_cap()
        total = await self.repos.count_repos.execute()
        budget_warning = ""
        if not self.settings.github_token and total + 1 > cap:
            budget_warning = (
                f"\n⚠️ Отслеживается уже {total + 1} репо, а без токена GitHub безопасно "
                f"~{cap} при опросе раз в {self.settings.github_poll_interval_minutes} мин. "
                "Часть релизов может прийти с задержкой."
            )

        card = self._repo_card(guild.id, snapshot.info, snapshot.latest)
        try:
            created = await forum.create_thread(
                name=_trim(f"{owner}/{name}", 100),
                embed=card,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Нет прав создавать посты в форум-канале релизов.", ephemeral=True
            )
            return
        except discord.HTTPException:
            logger.warning("Не удалось создать тред репозитория", exc_info=True)
            await interaction.followup.send(
                "Не удалось создать пост в форуме. Попробуйте ещё раз.", ephemeral=True
            )
            return

        tracked = await self.repos.add_repo.execute(
            guild_id=guild.id,
            owner=owner,
            name=name,
            added_by=interaction.user.id,
            thread_id=created.thread.id,
            baseline=snapshot.latest,
            now=datetime.now(UTC),
        )

        # сразу показать текущий последний релиз с его текстом (база уже на нём —
        # фоновый опрос не объявит его повторно). Сбой не критичен: репо уже
        # отслеживается, карточка на месте
        if snapshot.latest is not None:
            try:
                await created.thread.send(
                    embed=self._release_embed(guild.id, tracked, snapshot.latest),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning("Не удалось отправить последний релиз при добавлении", exc_info=True)

        no_releases = "" if snapshot.latest else "\nУ репозитория пока нет релизов — сообщу, когда появится."
        await interaction.followup.send(
            f"Отслеживаю `{owner}/{name}` → {created.thread.mention}.{no_releases}{budget_warning}",
            ephemeral=True,
        )

    @git_group.command(name="list", description="Отслеживаемые репозитории сервера")
    async def git_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        repos = await self.repos.list_repos.execute(guild.id)
        if not repos:
            await interaction.followup.send(
                "Пока ничего не отслеживается. Добавить — `/git add owner/name`.",
                ephemeral=True,
            )
            return
        lines = [f"• `{r.full_name}` → <#{r.thread_id}>" for r in repos]
        embed = discord.Embed(
            title=f"GitHub-репозитории ({len(repos)})",
            description=_trim("\n".join(lines), 4000),
            color=accent(guild.id),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @git_group.command(name="remove", description="Перестать отслеживать репозиторий")
    @app_commands.describe(repo="owner/name из /git list")
    async def git_remove(self, interaction: discord.Interaction, repo: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = guild_of(interaction)
        if await self._deny_if_not_manager(interaction, guild):
            return
        parsed = parse_repo_ref(repo)
        if parsed is None:
            await interaction.followup.send("Формат: `owner/name`.", ephemeral=True)
            return
        owner, name = parsed
        removed = await self.repos.remove_repo.execute(guild.id, owner, name)
        if removed:
            await interaction.followup.send(
                f"Больше не отслеживаю `{owner}/{name}`. Тред в форуме остаётся — "
                "удалите его вручную, если нужно.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"`{owner}/{name}` не было в списке. Проверьте `/git list`.", ephemeral=True
            )

    @git_remove.autocomplete("repo")
    async def _remove_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        repos = await self.repos.list_repos.execute(interaction.guild_id)
        needle = current.lower()
        return [
            app_commands.Choice(name=r.full_name[:100], value=r.full_name)
            for r in repos
            if needle in r.full_name.lower()
        ][:25]
