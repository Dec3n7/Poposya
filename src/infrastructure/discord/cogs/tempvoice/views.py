"""Панель каморки: embed с состоянием + кнопки.

Панель лежит в текстовом чате самой каморки, поэтому владельца всегда можно
найти по interaction.channel_id — custom_id держать в себе ничего не нужно
и хватает статических id (в отличие от staykick, где кнопки живут в ЛС).

bot.add_view(TempVoicePanel()) регистрирует вид как МАРШРУТИЗАТОР колбэков:
подписи кнопок Discord хранит в самом сообщении, поэтому после рестарта панель
показывает актуальное состояние, а зарегистрированный экземпляр нужен только
чтобы нажатие нашло свой обработчик.

Сами кнопки — тонкие: вся логика в TempVoiceCog."""

import discord

from . import phrases

PANEL_COLOR = 0x9B59B6


def panel_state(channel: discord.VoiceChannel) -> tuple[bool, bool]:
    """(закрыта, скрыта). Состояние — сами overwrites Discord: отдельно в БД
    его не держим, иначе появился бы второй источник правды."""
    overwrite = channel.overwrites_for(channel.guild.default_role)
    return overwrite.connect is False, overwrite.view_channel is False


def hub_embed() -> discord.Embed:
    """Витрина в чате хаба: одна на сервер, поэтому состояния конкретной
    каморки показать не может — только объясняет, что тут происходит."""
    embed = discord.Embed(
        title=phrases.HUB_TITLE,
        description=phrases.HUB_INTRO,
        color=PANEL_COLOR,
    )
    embed.add_field(name="Кнопки", value=phrases.HUB_HOW, inline=False)
    return embed


def is_panel(message: discord.Message, bot_user_id: int) -> bool:
    """Наша ли это панель — по custom_id кнопки, а не по тексту: тексты
    правятся, id кнопок держат совместимость со старыми сообщениями."""
    if message.author.id != bot_user_id:
        return False
    for row in message.components:
        for item in getattr(row, "children", ()):
            if getattr(item, "custom_id", None) == "tv:lock":
                return True
    return False


def panel_embed(channel: discord.VoiceChannel, owner_id: int) -> discord.Embed:
    locked, hidden = panel_state(channel)
    embed = discord.Embed(
        title=channel.name,
        description=phrases.PANEL_INTRO,
        color=PANEL_COLOR,
    )
    embed.add_field(name="Дверь", value="🔒 закрыта" if locked else "🔓 открыта")
    embed.add_field(name="Видимость", value="🙈 скрыта" if hidden else "👁 видна всем")
    embed.add_field(
        name="Мест", value="без лимита" if not channel.user_limit else str(channel.user_limit)
    )
    embed.add_field(name="Хозяин", value=f"<@{owner_id}>")
    return embed


class _CogButton(discord.ui.Button):
    """Кнопка, зовущая метод кога по имени: ког — единственное место с логикой."""

    def __init__(self, handler: str, **kwargs):
        super().__init__(**kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("TempVoiceCog")
        if cog is not None:
            await getattr(cog, self._handler)(interaction)


class TempVoicePanel(discord.ui.View):
    """Дефолтные аргументы обязательны: bot.add_view() создаёт экземпляр без
    состояния конкретного канала."""

    def __init__(self, locked: bool = False, hidden: bool = False):
        super().__init__(timeout=None)
        # тоглы меняют подпись и цвет — как ⏯️ у плеера: по кнопке видно,
        # что она сделает, а по embed — что сейчас
        self.add_item(
            _CogButton(
                "on_lock",
                custom_id="tv:lock",
                label="Открыть" if locked else "Закрыть",
                emoji="🔓" if locked else "🔒",
                style=discord.ButtonStyle.danger if locked else discord.ButtonStyle.secondary,
                row=0,
            )
        )
        self.add_item(
            _CogButton(
                "on_hide",
                custom_id="tv:hide",
                label="Показать" if hidden else "Скрыть",
                emoji="👁" if hidden else "🙈",
                style=discord.ButtonStyle.danger if hidden else discord.ButtonStyle.secondary,
                row=0,
            )
        )
        self.add_item(_CogButton("on_rename", custom_id="tv:name", label="Имя", emoji="✏️", row=0))
        self.add_item(_CogButton("on_limit", custom_id="tv:limit", label="Мест", emoji="🔢", row=0))
        self.add_item(_CogButton("on_kick", custom_id="tv:kick", label="Выгнать", emoji="✂️", row=1))
        self.add_item(
            _CogButton("on_permit", custom_id="tv:permit", label="Впустить", emoji="✅", row=1)
        )
        self.add_item(
            _CogButton(
                "on_block",
                custom_id="tv:block",
                label="Забанить",
                emoji="⛔",
                style=discord.ButtonStyle.danger,
                row=1,
            )
        )
        self.add_item(
            _CogButton(
                "on_claim",
                custom_id="tv:claim",
                label="Забрать",
                emoji="👑",
                style=discord.ButtonStyle.success,
                row=1,
            )
        )


class NameModal(discord.ui.Modal, title="Имя каморки"):
    def __init__(self, current: str):
        super().__init__()
        self.name = discord.ui.TextInput(
            label="Как назвать", default=current, min_length=1, max_length=100
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("TempVoiceCog")
        if cog is not None:
            await cog.apply_name(interaction, str(self.name))


class LimitModal(discord.ui.Modal, title="Сколько мест"):
    def __init__(self, current: int):
        super().__init__()
        self.limit = discord.ui.TextInput(
            label="0–99 (0 — без лимита)", default=str(current), max_length=2
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("TempVoiceCog")
        if cog is not None:
            await cog.apply_limit(interaction, str(self.limit))


class MemberPickView(discord.ui.View):
    """Эфемерный пикер участника. Живёт минуты, поэтому персистентность не
    нужна — нативный UserSelect вместо ввода ников руками."""

    def __init__(self, action: str, placeholder: str):
        super().__init__(timeout=120)
        self._action = action
        select = discord.ui.UserSelect(placeholder=placeholder, max_values=1)
        select.callback = self._picked
        self._select = select
        self.add_item(select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("TempVoiceCog")
        if cog is not None:
            await cog.apply_member_action(interaction, self._action, self._select.values[0])
