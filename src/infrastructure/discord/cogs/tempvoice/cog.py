"""Каморки: временные голосовые каналы.

Вход в канал-хаб — Попося создаёт личную каморку, переносит туда и кладёт в её
текстовый чат панель управления. Вышел последний человек — каморка удаляется
вместе с панелью. Хаб задаётся в /config (tempvoice_hub_channel) и он же
выключатель: 0 = фича не работает.

Реестр каналов живёт в БД, поэтому осиротевшие каморки (пустые или удалённые
руками, пока бот лежал) подметаются один раз при старте.

Команд у модуля нет — всё делается кнопками панели. Права владельца нигде не
дублируются: состояние двери/видимости — это сами overwrites канала, владелец —
строка в БД."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import discord
from discord.ext import commands

from src.application.tempvoice.di import TempVoiceContainer
from src.config import Settings
from src.domain.tempvoice.entities import TempChannel
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.persona_service import RegistryPersona

from .views import (
    LimitModal,
    MemberPickView,
    NameModal,
    TempVoicePanel,
    hub_embed,
    is_panel,
    panel_embed,
    panel_state,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Target:
    """Каморка, на которую действует нажатие, и можно ли перерисовать панель.

    local=False — жали панель хаба: она одна на всех, править её под одного
    человека нельзя, ответ уходит эфемерно."""

    channel: discord.VoiceChannel
    temp: TempChannel
    local: bool


# защита от спам-джойна хаба: шторм create/delete упрётся в лимиты Discord
_CREATE_COOLDOWN_SECONDS = 30

# Discord разрешает 2 переименования канала за 10 минут; на третьем discord.py
# молча заснёт до конца окна и интеракция протухнет — лучше честно отказать
_RENAME_LIMIT = 2
_RENAME_WINDOW_SECONDS = 600

# сколько сообщений чата хаба просмотреть в поисках своей панели
_HUB_HISTORY_SCAN = 20


def _now() -> datetime:
    return datetime.now(UTC)


def _humans(channel: discord.VoiceChannel) -> int:
    """Люди в канале. Попося, сидящая там с музыкой, — не человек и каморку
    живой не делает, иначе канал не опустеет никогда."""
    return sum(1 for m in channel.members if not m.bot)


async def _dm_quiet(member: discord.Member, text: str) -> None:
    try:
        await member.send(text)
    except discord.HTTPException:
        pass  # ЛС закрыты — не наша забота


async def _delete_quiet(channel: discord.abc.GuildChannel, reason: str) -> None:
    try:
        await channel.delete(reason=reason)
    except discord.HTTPException:
        logger.warning(
            "Не удалось удалить каморку", extra={"channel_id": channel.id}, exc_info=True
        )


class TempVoiceCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: TempVoiceContainer,
        settings: Settings,
        guild_settings=None,
        persona=None,
    ):
        self.bot = bot
        self.container = container
        self.settings = settings
        self.gs = guild_settings
        # голос кога — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()
        # кулдаун создания: user_id -> момент последней каморки. In-memory:
        # потерять его при рестарте не страшно, хуже кулдауна нет.
        self._last_created: dict[int, float] = {}
        # переименования: channel_id -> моменты. Чистится вместе с каморкой.
        self._renames: dict[int, list[float]] = {}
        # хабы, где панель уже проверена. Ключ — id хаба, а не гильдии: сменили
        # хаб через /config -> id другой -> панель появится и без рестарта.
        self._panelled: set[int] = set()
        self._swept = False

    async def cog_load(self) -> None:
        # маршрутизатор нажатий: подписи кнопок Discord помнит в самом
        # сообщении, поэтому панели переживают рестарт вместе с состоянием
        self.bot.add_view(TempVoicePanel())

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    def _p(self, guild_id: int, key: str, **vars: object) -> str:
        """Строковая фраза каталога персоны сервера."""
        return str(self.persona.phrase(guild_id, key, **vars))

    def _feature(self, guild_id: int, sub: str | None = None) -> bool:
        """Модуль «Каморки» (мастер) и подфункция (вкладка «Модули»). Флаг,
        отсутствующий в настройках (тест-заглушки), считаем включённым."""

        def on(key: str) -> bool:
            default = getattr(self.settings, key, True)
            value = self.gs.get(guild_id, key, default) if self.gs is not None else default
            return bool(value)

        if not on("tempvoice_enabled"):
            return False
        return on(sub) if sub is not None else True

    # --- старт: подмести осиротевшие, вернуть панель в хаб ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # on_ready повторяется при реконнектах; подметаем только на первом,
        # иначе рискуем снести каморку в момент между созданием и переносом
        if self._swept:
            return
        self._swept = True
        for guild in self.bot.guilds:
            try:
                await self._sweep(guild)
                await self._ensure_hub_panel(guild)
            except Exception:
                logger.exception("Старт каморок упал", extra={"guild_id": guild.id})

    async def _ensure_hub_panel(self, guild: discord.Guild) -> None:
        """Панель в чате хаба должна быть всегда. Проверяем один раз на хаб:
        при старте и при первом входе (последнее — чтобы панель появилась
        сразу после /config, не дожидаясь рестарта)."""
        if not self._feature(guild.id):
            return  # модуль выключен — не выкладываем приглашение в хаб
        hub_id = self._cfg(guild.id, "tempvoice_hub_channel")
        if not hub_id or hub_id in self._panelled:
            return
        # помечаем ДО await: пока идёт запрос истории, параллельные входы
        # не должны положить вторую панель
        self._panelled.add(hub_id)
        hub = guild.get_channel(hub_id)
        if not isinstance(hub, discord.VoiceChannel):
            logger.warning(
                "Хаб каморок не найден или не голосовой канал",
                extra={"guild_id": guild.id, "channel_id": hub_id},
            )
            return
        try:
            async for message in hub.history(limit=_HUB_HISTORY_SCAN):
                if is_panel(message, cast(discord.ClientUser, self.bot.user).id):
                    return  # панель на месте
            await hub.send(embed=hub_embed(guild.id, self.persona), view=TempVoicePanel())
            logger.info("Панель хаба выложена", extra={"guild_id": guild.id, "channel_id": hub_id})
        except discord.HTTPException:
            # не смогли — фича всё равно работает, панель есть в самой каморке
            self._panelled.discard(hub_id)  # дать шанс следующему входу
            logger.warning(
                "Не удалось выложить панель хаба",
                extra={"guild_id": guild.id, "channel_id": hub_id},
                exc_info=True,
            )

    async def _sweep(self, guild: discord.Guild) -> None:
        swept = 0
        for temp in await self.container.list_channels.execute(guild.id):
            channel = guild.get_channel(temp.channel_id)
            if channel is None:  # удалили руками, пока бот лежал
                await self.container.release.execute(temp.channel_id)
                swept += 1
                continue
            if _humans(cast(discord.VoiceChannel, channel)):
                continue  # там сидят люди — каморка живая
            await _delete_quiet(channel, "Каморка: пустая после рестарта")
            await self.container.release.execute(temp.channel_id)
            swept += 1
        if swept:
            logger.info("Подметено осиротевших каморок: %d", swept, extra={"guild_id": guild.id})

    # --- жизненный цикл каморки ---

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel == after.channel:
            return  # мут/глушение/стрим — канал не менялся
        # порядок важен: сперва освободить покинутый канал, потом создавать
        # новый — человек мог уйти из своей каморки прямо в хаб
        if before.channel is not None:
            await self._maybe_delete(cast(discord.VoiceChannel, before.channel))
        if after.channel is not None:
            await self._maybe_create(member, cast(discord.VoiceChannel, after.channel))

    async def _maybe_delete(self, channel: discord.VoiceChannel) -> None:
        temp = await self.container.get.execute(channel.id)
        if temp is None:
            return  # не наша каморка
        if _humans(channel):
            return
        await _delete_quiet(channel, "Каморка: вышел последний")
        await self.container.release.execute(channel.id)
        self._renames.pop(channel.id, None)  # канал умер — счётчик ни к чему
        logger.info(
            "Каморка удалена",
            extra={"guild_id": temp.guild_id, "channel_id": channel.id},
        )

    async def _maybe_create(self, member: discord.Member, channel: discord.VoiceChannel) -> None:
        guild = member.guild
        if not self._feature(guild.id):
            return  # модуль «Каморки» выключен на сервере (вкладка «Модули»)
        hub_id = self._cfg(guild.id, "tempvoice_hub_channel")
        if not hub_id or channel.id != hub_id:
            return  # не хаб (или фича выключена)
        await self._ensure_hub_panel(guild)

        entered = time.monotonic()
        last = self._last_created.get(member.id)
        if last is not None and entered - last < _CREATE_COOLDOWN_SECONDS:
            # человек только что получил каморку и снова лезет в хаб — молчим:
            # своя каморка у него уже есть, объяснять нечего
            logger.debug("Каморка: кулдаун создания", extra={"user_id": member.id})
            return

        if await self.container.count.execute(guild.id) >= self._cfg(
            guild.id, "tempvoice_max_per_guild"
        ):
            await _dm_quiet(member, self._p(guild.id, "tempvoice.cap_reached"))
            return

        temp = await self._create_channel(member, channel)
        if temp is None:
            return
        # запись ДО переноса: иначе есть окно, где человек уже вышел, а строки
        # ещё нет — такую каморку не удалит ни событие, ни подметание
        await self.container.register.execute(guild.id, temp.id, member.id, _now())
        if not await self._move_in(member, temp):
            await self.container.release.execute(temp.id)
            return
        self._last_created[member.id] = entered
        await self._post_panel(temp, member.id)
        logger.info(
            "Каморка создана",
            extra={"guild_id": guild.id, "channel_id": temp.id, "owner_id": member.id},
        )

    async def _post_panel(self, channel: discord.VoiceChannel, owner_id: int) -> None:
        """Панель — в текстовый чат самой каморки: умрёт вместе с ней, никаких
        осиротевших сообщений."""
        if not self._feature(channel.guild.id, "tempvoice_panel"):
            return  # панель управления выключена на сервере (каморка работает и так)
        try:
            await channel.send(
                embed=panel_embed(channel, owner_id, self.persona), view=TempVoicePanel()
            )
        except discord.HTTPException:
            # без панели каморка всё равно работает — не повод её сносить
            logger.warning(
                "Не удалось положить панель в каморку",
                extra={"channel_id": channel.id},
                exc_info=True,
            )

    async def _create_channel(
        self, member: discord.Member, hub: discord.abc.Connectable
    ) -> discord.VoiceChannel | None:
        guild = member.guild
        category_id = self._cfg(guild.id, "tempvoice_category")
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            # пикер /config отдаёт любой канал: если указали не категорию
            # (или её удалили) — создаём рядом с хабом, а не падаем
            category = cast(discord.VoiceChannel, hub).category
        try:
            return await guild.create_voice_channel(
                name=self._p(guild.id, "tempvoice.channel_name", display_name=member.display_name)[
                    :100
                ],
                category=category,
                user_limit=self._cfg(guild.id, "tempvoice_default_limit"),
                reason=f"Каморка для {member}",
            )
        except discord.Forbidden:
            logger.warning("Нет права Manage Channels для каморок", extra={"guild_id": guild.id})
            await _dm_quiet(member, self._p(guild.id, "tempvoice.no_manage_perms"))
        except discord.HTTPException:
            logger.warning(
                "Не удалось создать каморку", extra={"guild_id": guild.id}, exc_info=True
            )
        return None

    # --- кнопки панели ---

    async def _target(self, interaction: discord.Interaction) -> _Target | None:
        """На какую каморку действует нажатие.

        Панель лежит в чате каморки — значит на неё саму. Панель хаба одна на
        всех и о конкретной каморке ничего не знает — значит на тот войс, где
        нажавший сейчас сидит. Отказы объясняет сам."""
        temp = await self.container.get.execute(cast(int, interaction.channel_id))
        if temp is not None:
            return _Target(cast(discord.VoiceChannel, interaction.channel), temp, local=True)
        voice_state = getattr(interaction.user, "voice", None)
        voice = voice_state.channel if voice_state is not None else None
        if voice is None:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.not_in_voice"), ephemeral=True
            )
            return None
        temp = await self.container.get.execute(voice.id)
        if temp is None:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.not_in_temp"), ephemeral=True
            )
            return None
        return _Target(voice, temp, local=False)

    async def _owned(self, interaction: discord.Interaction) -> _Target | None:
        """Цель, если жмёт её владелец. Иначе сам объясняет отказ и None."""
        target = await self._target(interaction)
        if target is None:
            return None
        if target.temp.owner_id != interaction.user.id:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.not_owner", owner_id=target.temp.owner_id),
                ephemeral=True,
            )
            return None
        return target

    async def _show(
        self, interaction: discord.Interaction, target: _Target, owner_id: int | None = None
    ) -> None:
        """Показать состояние каморки после действия.

        Панель каморки — перерисовать на месте (embed говорит, что сейчас,
        тоглы — что сделают). Панель хаба общая: перерисовать её под одного
        человека нельзя, поэтому то же состояние уходит ему эфемерно."""
        channel = target.channel
        embed = panel_embed(
            channel,
            owner_id if owner_id is not None else target.temp.owner_id,
            self.persona,
        )
        if target.local:
            locked, hidden = panel_state(channel)
            await interaction.response.edit_message(
                embed=embed, view=TempVoicePanel(locked, hidden)
            )
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_lock(self, interaction: discord.Interaction) -> None:
        target = await self._owned(interaction)
        if target is None:
            return
        locked, _ = panel_state(target.channel)
        # None (а не True) — вернуть наследование от категории, а не выдать
        # право поверх серверных настроек
        if not await self._set_everyone(
            interaction, target.channel, connect=None if locked else False
        ):
            return
        await self._show(interaction, target)

    async def on_hide(self, interaction: discord.Interaction) -> None:
        target = await self._owned(interaction)
        if target is None:
            return
        _, hidden = panel_state(target.channel)
        if not await self._set_everyone(
            interaction, target.channel, view_channel=None if hidden else False
        ):
            return
        await self._show(interaction, target)

    async def _set_everyone(
        self, interaction: discord.Interaction, channel: discord.VoiceChannel, **fields
    ) -> bool:
        overwrite = channel.overwrites_for(channel.guild.default_role)
        for key, value in fields.items():
            setattr(overwrite, key, value)
        try:
            await channel.set_permissions(
                channel.guild.default_role, overwrite=overwrite, reason="Каморка: панель"
            )
            return True
        except discord.HTTPException:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.action_failed"), ephemeral=True
            )
            logger.warning(
                "Не удалось сменить права каморки",
                extra={"channel_id": channel.id},
                exc_info=True,
            )
            return False

    async def on_rename(self, interaction: discord.Interaction) -> None:
        target = await self._owned(interaction)
        if target is None:
            return
        wait = self._rename_wait(target.channel.id)
        if wait:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.rename_too_soon", seconds=wait),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(NameModal(target.channel.name))

    def _rename_wait(self, channel_id: int) -> int:
        """0 — можно переименовать; иначе сколько секунд ждать."""
        now = time.monotonic()
        stamps = [t for t in self._renames.get(channel_id, []) if now - t < _RENAME_WINDOW_SECONDS]
        self._renames[channel_id] = stamps
        if len(stamps) < _RENAME_LIMIT:
            return 0
        return int(_RENAME_WINDOW_SECONDS - (now - stamps[0])) + 1

    async def apply_name(self, interaction: discord.Interaction, name: str) -> None:
        target = await self._owned(interaction)
        if target is None:
            return
        try:
            await target.channel.edit(name=name[:100], reason="Каморка: имя от владельца")
        except discord.HTTPException:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.action_failed"), ephemeral=True
            )
            return
        self._renames.setdefault(target.channel.id, []).append(time.monotonic())
        await self._show(interaction, target)

    async def on_limit(self, interaction: discord.Interaction) -> None:
        target = await self._owned(interaction)
        if target is None:
            return
        await interaction.response.send_modal(LimitModal(target.channel.user_limit))

    async def apply_limit(self, interaction: discord.Interaction, raw: str) -> None:
        target = await self._owned(interaction)
        if target is None:
            return
        try:
            limit = int(raw.strip())
        except ValueError:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.limit_bad"), ephemeral=True
            )
            return
        if not 0 <= limit <= 99:  # 99 — максимум Discord
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.limit_bad"), ephemeral=True
            )
            return
        try:
            await target.channel.edit(user_limit=limit, reason="Каморка: лимит от владельца")
        except discord.HTTPException:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.action_failed"), ephemeral=True
            )
            return
        await self._show(interaction, target)

    async def on_kick(self, interaction: discord.Interaction) -> None:
        await self._pick_member(interaction, "kick", "Кого выставить за дверь")

    async def on_permit(self, interaction: discord.Interaction) -> None:
        await self._pick_member(interaction, "permit", "Кого впускать даже в закрытую")

    async def on_block(self, interaction: discord.Interaction) -> None:
        await self._pick_member(interaction, "block", "Кому сюда больше нельзя")

    async def _pick_member(
        self, interaction: discord.Interaction, action: str, placeholder: str
    ) -> None:
        if await self._owned(interaction) is None:
            return
        await interaction.response.send_message(
            view=MemberPickView(action, placeholder), ephemeral=True
        )

    async def apply_member_action(
        self, interaction: discord.Interaction, action: str, member: discord.Member
    ) -> None:
        target = await self._owned(interaction)
        if target is None:
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.self_target"), ephemeral=True
            )
            return
        try:
            text = await self._do_member_action(
                guild_of(interaction).id, action, target.channel, member
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "tempvoice.action_failed"), ephemeral=True
            )
            logger.warning("Действие панели не прошло", extra={"action": action}, exc_info=True)
            return
        # панель не трогаем: выгнать/впустить/забанить не меняют ничего из того,
        # что она показывает (дверь, видимость, мест, хозяин)
        await interaction.response.send_message(text, ephemeral=True)

    async def _do_member_action(
        self, guild_id: int, action: str, channel: discord.VoiceChannel, target: discord.Member
    ) -> str:
        if action == "kick":
            if target not in channel.members:
                return self._p(guild_id, "tempvoice.not_here")
            await target.move_to(None, reason="Каморка: выгнал владелец")
            return self._p(guild_id, "tempvoice.kicked", user_id=target.id)
        if action == "permit":
            await channel.set_permissions(
                target, connect=True, view_channel=True, reason="Каморка: впустил владелец"
            )
            return self._p(guild_id, "tempvoice.permitted", user_id=target.id)
        # block: закрыть дверь и выставить, если уже внутри
        await channel.set_permissions(
            target, connect=False, view_channel=False, reason="Каморка: забанил владелец"
        )
        if target in channel.members:
            await target.move_to(None, reason="Каморка: забанил владелец")
        return self._p(guild_id, "tempvoice.blocked", user_id=target.id)

    async def on_claim(self, interaction: discord.Interaction) -> None:
        """Единственная кнопка не для владельца — иначе брошенную каморку
        никто не смог бы прибрать к рукам. Поэтому идёт через _target, а не
        через _owned."""
        target = await self._target(interaction)
        if target is None:
            return
        channel = target.channel
        present = {m.id for m in channel.members if not m.bot}
        result = await self.container.claim.execute(channel.id, interaction.user.id, present)
        if not result.ok:
            refusals = self.persona.phrase(guild_of(interaction).id, "tempvoice.claim_refusals")
            text = refusals.get(result.reason) if isinstance(refusals, dict) else None
            await interaction.response.send_message(
                text or self._p(guild_of(interaction).id, "tempvoice.action_failed"), ephemeral=True
            )
            return
        await self._show(interaction, target, owner_id=interaction.user.id)
        await interaction.followup.send(
            self._p(guild_of(interaction).id, "tempvoice.claimed", previous_owner_id=result.owner_id),
            ephemeral=True,
        )
        logger.info(
            "Каморка сменила хозяина",
            extra={
                "channel_id": channel.id,
                "from": result.owner_id,
                "to": interaction.user.id,
            },
        )

    # --- создание: перенос ---

    async def _move_in(self, member: discord.Member, temp: discord.VoiceChannel) -> bool:
        try:
            await member.move_to(temp, reason="Каморка: перенос владельца")
            return True
        except discord.HTTPException:
            # нет права Move Members либо человек уже вышел из хаба. Пустую
            # каморку оставлять нельзя: её некому будет опустошить, а значит
            # и удалить — событий выхода по ней уже не придёт
            await _delete_quiet(temp, "Каморка: перенос не удался")
            await _dm_quiet(member, self._p(member.guild.id, "tempvoice.no_move_perms"))
            logger.warning(
                "Не удалось перенести в каморку",
                extra={"guild_id": member.guild.id, "user_id": member.id},
                exc_info=True,
            )
            return False
