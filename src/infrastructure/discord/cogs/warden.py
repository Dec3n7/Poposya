"""Слэш `/warden` — управление внешним сторожем прямо из Discord.

WARDEN — отдельный сервис: наблюдает за контейнерами и в вооружённом режиме
перезапускает зависшие. Перед плановым деплоем/обслуживанием оператор ставит
паузу, чтобы сторож не принял рукотворную остановку за отказ и не устроил
рестарт-войну. Команда зовёт HTTP-API сторожа: status — под read-токеном,
pause/resume — под control-токеном (если задан отдельный; иначе оба по read).

Доступ — только операторам бота (`web_operator_ids`): это глобальная
инфраструктура, а не настройка конкретного сервера. Ког подключается лишь когда
WARDEN подключён (есть URL и токен) — иначе мёртвой команды в дереве нет.
"""

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from src.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=5)


class WardenCog(commands.Cog):
    # группа под замком администратора (меньше визуального шума), но настоящий
    # гейт — проверка оператора внутри: права сервера тут ни при чём
    warden = app_commands.Group(
        name="warden",
        description="Управление сторожем WARDEN (только оператор бота)",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot, settings: Settings):
        self.bot = bot
        self.settings = settings

    async def _call(
        self, method: str, path: str, payload: dict | None = None, *, control: bool = False
    ) -> dict:
        url = self.settings.warden_api_url.rstrip("/") + path
        # управляющие вызовы (pause/resume) — под control-токеном (least
        # privilege), status — под read-токеном
        token = (
            self.settings.warden_control_token_effective
            if control
            else self.settings.warden_api_token
        )
        headers = {"X-Warden-Token": token}
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.request(method, url, headers=headers, json=payload) as resp:
                if resp.status == 401:
                    return {"error": "неверный токен"}
                resp.raise_for_status()
                data = await resp.json()
        return data if isinstance(data, dict) else {}

    async def _guard(self, interaction: discord.Interaction) -> bool:
        """Общий вход обеих команд: только оператор бота (URL/токен проверены при
        подключении кога, поэтому здесь достаточно проверки прав)."""
        if interaction.user.id not in self.settings.web_operator_ids:
            await interaction.response.send_message(
                "Команда только для операторов бота.", ephemeral=True
            )
            return False
        return True

    @warden.command(name="pause", description="Приостановить рестарты сторожа на N минут")
    @app_commands.describe(minutes="Сколько минут держать паузу (1–720, по умолчанию 15)")
    async def pause(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 720] = 15,
    ) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            state = await self._call("POST", "/pause", {"minutes": int(minutes)}, control=True)
        except Exception as exc:
            logger.warning("WARDEN /pause: сторож не ответил: %s", exc)
            await interaction.followup.send(
                f"Сторож не ответил ({type(exc).__name__}).", ephemeral=True
            )
            return
        if state.get("error"):
            await interaction.followup.send(f"Сторож отказал: {state['error']}.", ephemeral=True)
            return
        remaining = int(state.get("remaining_seconds") or int(minutes) * 60)
        await interaction.followup.send(
            f"⏸ Пауза на ~{remaining // 60} мин — рестарты подавлены. Наблюдение продолжается.",
            ephemeral=True,
        )

    @warden.command(name="resume", description="Снять паузу — вернуть сторожу право на рестарты")
    async def resume(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self._call("POST", "/resume", control=True)
        except Exception as exc:
            logger.warning("WARDEN /resume: сторож не ответил: %s", exc)
            await interaction.followup.send(
                f"Сторож не ответил ({type(exc).__name__}).", ephemeral=True
            )
            return
        await interaction.followup.send("▶ Пауза снята — действия снова разрешены.", ephemeral=True)

    @warden.command(name="status", description="Кратко: режим и открытые инциденты сторожа")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            snap = await self._call("GET", "/status")
        except Exception as exc:
            await interaction.followup.send(
                f"Сторож не ответил ({type(exc).__name__}).", ephemeral=True
            )
            return
        w = snap.get("warden") or {}
        if w.get("dry_run"):
            mode = "наблюдение (dry-run)"
        elif w.get("paused"):
            mins = (int(w.get("pause_remaining_seconds") or 0) + 59) // 60
            mode = f"на паузе (ещё ~{mins} мин)"
        else:
            mode = "действия разрешены"
        incidents = int(snap.get("incidents_open") or 0)
        await interaction.followup.send(
            f"🛡 WARDEN: {mode}. Открытых инцидентов: {incidents}.", ephemeral=True
        )
