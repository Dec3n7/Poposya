"""Апелляции наказаний. Наказанному (бан/темпбан/мут) в ЛС приходит кнопка
«Обжаловать»; по ней — форма, апелляция уходит в канал модерации с кнопками
Принять/Отклонить. Принять → снимаю наказание (разбан/анмут) + ЛС участнику.

Кнопки — persistent через DynamicItem (custom_id несёт данные, работает после
рестарта). Зависимости берём из `interaction.client.container` — так DynamicItem
не нужно ничего инжектить."""

import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord.ext import commands

from src.application.appeals.di import AppealsContainer
from src.config import Settings
from src.domain.appeals.entities import (
    ACTION_BAN,
    ACTION_KICK,
    ACTION_MUTE,
    ACTION_TEMPBAN,
    Appeal,
)
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import flag_on
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

_ACTION_LABELS = {
    ACTION_BAN: "бан",
    ACTION_TEMPBAN: "временный бан",
    ACTION_MUTE: "мут",
    ACTION_KICK: "кик",
}


def _phrase(client: discord.Client, guild_id: int, key: str, **vars: object) -> str:
    return str(client.container.persona.phrase(guild_id, key, **vars))  # type: ignore[attr-defined]


def _appeals_channel_id(client: discord.Client, guild_id: int) -> int:
    settings = client.container.settings  # type: ignore[attr-defined]
    gs = client.container.guild_settings  # type: ignore[attr-defined]
    default = settings.appeals_channel
    value = gs.get(guild_id, "appeals_channel", default) if gs is not None else default
    return int(value or 0)


def _appeals_on(client: discord.Client, guild_id: int) -> bool:
    settings = client.container.settings  # type: ignore[attr-defined]
    gs = client.container.guild_settings  # type: ignore[attr-defined]
    return (
        flag_on(settings, gs, guild_id, "appeals_enabled")
        and _appeals_channel_id(client, guild_id) > 0
    )


def _can_resolve(member: discord.Member, action: str) -> bool:
    perms = member.guild_permissions
    if action in (ACTION_BAN, ACTION_TEMPBAN):
        return perms.ban_members or perms.administrator
    if action == ACTION_KICK:
        return perms.kick_members or perms.administrator
    return perms.moderate_members or perms.administrator


# --- модалка апелляции ---


class AppealModal(discord.ui.Modal):
    def __init__(self, guild_id: int, action: str, title: str, field_label: str):
        super().__init__(title=title[:45])
        self._guild_id = guild_id
        self._action = action
        self._text: discord.ui.TextInput = discord.ui.TextInput(
            label=field_label[:45],
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.add_item(self._text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        client = interaction.client
        gid = self._guild_id
        result = await client.container.appeals.create.execute(  # type: ignore[attr-defined]
            gid, interaction.user.id, self._action, self._text.value, datetime.now(UTC)
        )
        if not result.ok:
            key = "appeals.duplicate" if result.error == "duplicate" else "appeals.empty"
            await interaction.response.send_message(_phrase(client, gid, key), ephemeral=True)
            return
        await _post_review(client, result.appeal)
        await interaction.response.send_message(
            _phrase(client, gid, "appeals.submitted"), ephemeral=True
        )


# --- persistent-кнопки ---


class AppealButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"appeal:new:(?P<gid>\d+):(?P<action>[a-z]+)",
):
    def __init__(self, guild_id: int, action: str, label: str = "Обжаловать"):
        self.guild_id = guild_id
        self.action = action
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.secondary,
                emoji="⚖️",
                custom_id=f"appeal:new:{guild_id}:{action}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["gid"]), match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        client = interaction.client
        gid = self.guild_id
        if not _appeals_on(client, gid):
            await interaction.response.send_message(
                _phrase(client, gid, "appeals.closed"), ephemeral=True
            )
            return
        await interaction.response.send_modal(
            AppealModal(
                gid,
                self.action,
                _phrase(client, gid, "appeals.modal_title"),
                _phrase(client, gid, "appeals.modal_field"),
            )
        )


class AppealDecision(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"appeal:(?P<decision>approve|reject):(?P<id>\d+):(?P<action>[a-z]+)",
):
    def __init__(self, appeal_id: int, approve: bool, action: str):
        self.appeal_id = appeal_id
        self.approve = approve
        self.action = action
        decision = "approve" if approve else "reject"
        super().__init__(
            discord.ui.Button(
                label="Принять" if approve else "Отклонить",
                style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
                custom_id=f"appeal:{decision}:{appeal_id}:{action}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), match["decision"] == "approve", match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_decision(interaction, self.appeal_id, self.approve, self.action)


# --- пост апелляции в канал модерации и разбор ---


async def _post_review(client: discord.Client, appeal: Appeal) -> None:
    gid = appeal.guild_id
    channel = client.get_channel(_appeals_channel_id(client, gid))
    if not isinstance(channel, discord.abc.Messageable):
        return
    guild = client.get_guild(gid)
    member = guild.get_member(appeal.user_id) if guild is not None else None
    who = member.mention if member is not None else f"<@{appeal.user_id}>"
    embed = discord.Embed(
        title=_phrase(client, gid, "appeals.review_title"),
        description=appeal.text[:2000],
        color=accent(gid),
    )
    embed.add_field(name="Кто", value=who, inline=True)
    embed.add_field(
        name="Наказание", value=_ACTION_LABELS.get(appeal.action, appeal.action), inline=True
    )
    if appeal.original_reason:
        embed.add_field(name="Причина наказания", value=appeal.original_reason[:1024], inline=False)
    embed.set_footer(text=f"Апелляция #{appeal.id}")
    view = discord.ui.View(timeout=None)
    view.add_item(AppealDecision(cast(int, appeal.id), True, appeal.action))
    view.add_item(AppealDecision(cast(int, appeal.id), False, appeal.action))
    await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


async def _lift_punishment(guild: discord.Guild, appeal: Appeal) -> None:
    """Снять наказание при одобрении (лучший эффорт: ушёл/уже снято — тихо).
    Кик снять нечем — человек уже вне сервера; одобрение только уведомит его
    (ниже, _notify_appellant), а вернуть должен модератор приглашением."""
    try:
        if appeal.action in (ACTION_BAN, ACTION_TEMPBAN):
            await guild.unban(discord.Object(id=appeal.user_id), reason="Апелляция принята")
        elif appeal.action == ACTION_MUTE:
            member = guild.get_member(appeal.user_id)
            if member is not None:
                await member.timeout(None, reason="Апелляция принята")
    except discord.HTTPException:
        logger.warning("Апелляция: не снял наказание для %s", appeal.user_id, exc_info=True)


async def _notify_appellant(client: discord.Client, appeal: Appeal, approve: bool) -> None:
    key = "appeals.approved_dm" if approve else "appeals.rejected_dm"
    guild = client.get_guild(appeal.guild_id)
    guild_name = guild.name if guild is not None else ""
    try:
        user = await client.fetch_user(appeal.user_id)
        await user.send(_phrase(client, appeal.guild_id, key, guild=guild_name))
    except (discord.HTTPException, discord.Forbidden):
        pass  # закрытые ЛС — норма


async def _handle_decision(
    interaction: discord.Interaction, appeal_id: int, approve: bool, action: str
) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not _can_resolve(member, action):
        await interaction.response.send_message(
            "Недостаточно прав для разбора этой апелляции.", ephemeral=True
        )
        return
    client = interaction.client
    result = await client.container.appeals.resolve.execute(  # type: ignore[attr-defined]
        appeal_id, approve, member.id, datetime.now(UTC)
    )
    if not result.ok:
        text = (
            "Эту апелляцию уже разобрали." if result.error == "already" else "Апелляция не найдена."
        )
        await interaction.response.send_message(text, ephemeral=True)
        return

    # обновляем карточку и гасим кнопки (это же — ответ на взаимодействие)
    embeds = interaction.message.embeds if interaction.message is not None else []
    embed = embeds[0] if embeds else discord.Embed()
    embed.color = discord.Color.green() if approve else discord.Color.red()
    verdict = "✅ Принято" if approve else "❌ Отклонено"
    embed.add_field(name="Итог", value=f"{verdict} — {member.mention}", inline=False)
    await interaction.response.edit_message(embed=embed, view=None)

    # побочные эффекты после ответа
    if approve and interaction.guild is not None:
        await _lift_punishment(interaction.guild, result.appeal)
    await _notify_appellant(client, result.appeal, approve)


class AppealsCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        appeals: AppealsContainer,
        settings: Settings,
        guild_settings=None,
        persona=None,
    ):
        self.bot = bot
        self.appeals = appeals
        self.settings = settings
        self.gs = guild_settings
        self.persona = persona if persona is not None else RegistryPersona()

    async def cog_load(self) -> None:
        # persistent-кнопки: маршрутизация по шаблону custom_id переживает рестарт
        self.bot.add_dynamic_items(AppealButton, AppealDecision)

    async def resolve_from_panel(
        self, guild: discord.Guild | None, appeal_id: int, approve: bool, resolver_id: int
    ) -> str:
        """Разбор апелляции из панели (командный мост). Меняет статус, снимает
        наказание при одобрении и шлёт ЛС участнику. Карточку в канале не трогаем
        (кнопки там остаются, но идемпотентны — повторный клик увидит «уже
        разобрано»)."""
        result = await self.appeals.resolve.execute(
            appeal_id, approve, resolver_id, datetime.now(UTC)
        )
        if not result.ok:
            return (
                "Эту апелляцию уже разобрали."
                if result.error == "already"
                else ("Апелляция не найдена.")
            )
        appeal = result.appeal
        if appeal is None:  # ok=True гарантирует наличие; страховка для типов
            return "Апелляция не найдена."
        if approve and guild is not None:
            await _lift_punishment(guild, appeal)
        await _notify_appellant(self.bot, appeal, approve)
        return "Апелляция принята — наказание снято." if approve else "Апелляция отклонена."

    def build_button_view(self, guild_id: int, action: str) -> discord.ui.View | None:
        """View с кнопкой «Обжаловать» для ЛС-уведомления (или None, если модуль
        выключен/канал не задан). Зовёт модерация при бане/темпбане/муте."""
        if not flag_on(self.settings, self.gs, guild_id, "appeals_enabled"):
            return None
        default = self.settings.appeals_channel
        channel_id = self.gs.get(guild_id, "appeals_channel", default) if self.gs else default
        if not int(channel_id or 0):
            return None
        label = str(self.persona.phrase(guild_id, "appeals.button_label"))
        view = discord.ui.View(timeout=None)
        view.add_item(AppealButton(guild_id, action, label))
        return view
