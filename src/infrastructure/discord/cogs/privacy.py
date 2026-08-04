"""Приватность: удаление данных.

- `on_guild_remove` — бот покинул сервер: ставим отметку выхода. Фоновый цикл
  стирает данные, когда отметка старше окна отсрочки (защита от случайного
  кика/переинвайта). `on_guild_join` — вернулись: снимаем отметку.
- `/forgetme` — участник стирает свои «личные» данные на текущем сервере
  (с подтверждением). Модерация сохраняется — см. PrivacyService.

Логика удаления — в PrivacyService; ког тонкий: расписание + Discord-UX."""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.config import Settings
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.privacy_service import PrivacyService, utcnow

logger = logging.getLogger(__name__)

_FORGET_WARNING = (
    "⚠️ **Удалить твои данные на этом сервере?**\n"
    "Сотру: очки и уровень, историю активности, память наших разговоров, "
    "коллекцию находок, голоса и оценки в киноклубе, зеркало ролей, напоминания.\n\n"
    "Останутся: модерационные записи (предупреждения/баны) — их не удаляю из "
    "соображений безопасности. Глобальные лайки музыки не привязаны к серверу и "
    "не трогаются.\n\n"
    "Это **необратимо**."
)


class _ConfirmForget(discord.ui.View):
    """Эфемерное подтверждение, залоченное на вызвавшего."""

    def __init__(self, invoker_id: int):
        super().__init__(timeout=60)
        self._invoker_id = invoker_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._invoker_id:
            await interaction.response.send_message("Это не твоя кнопка.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Да, удалить мои данные", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


class PrivacyCog(commands.Cog):
    def __init__(self, bot: commands.Bot, privacy: PrivacyService, settings: Settings):
        self.bot = bot
        self.privacy = privacy
        self.settings = settings
        # окно на busy-loop не проверяем через 0: минимум час
        self._interval = max(1, settings.privacy_purge_interval_hours) * 3600
        self._task: asyncio.Task | None = None

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        if self._task is not None:
            self._task.cancel()

    # --- жизненный цикл сервера ---

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.privacy.mark_departure(guild.id, utcnow())
        logger.info(
            "Бот покинул сервер — данные помечены на удаление через %d дн.",
            self.settings.privacy_purge_grace_days,
            extra={"guild_id": guild.id},
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if await self.privacy.cancel_departure(guild.id):
            logger.info(
                "Бот вернулся на сервер — отложенное удаление данных отменено",
                extra={"guild_id": guild.id},
            )

    # --- фоновый цикл стирания просроченных ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._sweep_loop())
            logger.info(
                "Приватность: цикл удаления запущен (проверка раз в %d ч, окно %d дн.)",
                self.settings.privacy_purge_interval_hours,
                self.settings.privacy_purge_grace_days,
            )

    async def _sweep_loop(self) -> None:
        while True:
            try:
                purged = await self.privacy.sweep_expired(utcnow())
                for guild_id, counts in purged:
                    logger.info(
                        "Приватность: данные сервера стёрты по истечении окна",
                        extra={"guild_id": guild_id, "rows_deleted": sum(counts.values())},
                    )
            except Exception:
                logger.exception("Приватность: проход удаления упал — продолжаю")
            await asyncio.sleep(self._interval)

    # --- /forgetme ---

    @app_commands.command(name="forgetme", description="Удалить мои данные на этом сервере")
    @app_commands.guild_only()
    async def forgetme(self, interaction: discord.Interaction) -> None:
        guild = guild_of(interaction)
        view = _ConfirmForget(interaction.user.id)
        await interaction.response.send_message(_FORGET_WARNING, view=view, ephemeral=True)
        await view.wait()
        if view.confirmed is not True:
            await interaction.edit_original_response(
                content="Отменено — ничего не удалено.", view=None
            )
            return
        counts = await self.privacy.forget_user(guild.id, interaction.user.id)
        total = sum(counts.values())
        if total == 0:
            message = "У меня и не было твоих данных на этом сервере."
        else:
            message = (
                f"Готово — удалила {total} запис{_plural(total)}. "
                "Здесь я тебя больше не помню (модерационные записи, если были, остались)."
            )
        await interaction.edit_original_response(content=message, view=None)


def _plural(n: int) -> str:
    """Русское окончание для «запись/записи/записей»."""
    if 11 <= n % 100 <= 14:
        return "ей"
    last = n % 10
    if last == 1:
        return "ь"
    if 2 <= last <= 4:
        return "и"
    return "ей"
