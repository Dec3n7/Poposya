"""Discord-компоненты киноклуба: выбор фильма, карточка с 👍/👎, кнопка
«посмотрели», окно рецензии, кнопки оценок 1–10 и опрос киновечера.

Все persistent-view (timeout=None) переживают рестарт: custom_id стабилен,
а привязку к сообщению ког восстанавливает из БД в on_ready."""

from typing import TYPE_CHECKING

import discord

from src.domain.cinema.entities import MovieEntry
from src.infrastructure.cinema.provider import MovieInfo

from .formatting import _title_of, _trim

if TYPE_CHECKING:
    from .cog import CinemaCog


class MoviePickView(discord.ui.View):
    """Выбор фильма из результатов TMDB (эфемерный, как поиск музыки)."""

    def __init__(self, cog: "CinemaCog", results: list[MovieInfo]):
        super().__init__(timeout=60)
        options = [
            discord.SelectOption(
                label=_trim(f"{info.title} ({info.year})" if info.year else info.title, 95),
                description=_trim(info.overview or "Без описания", 95),
                value=str(i),
                emoji="🎬",
            )
            for i, info in enumerate(results)
        ]
        select = discord.ui.Select(placeholder="Какой из них?", options=options)

        async def on_pick(interaction: discord.Interaction) -> None:
            info = results[int(select.values[0])]
            content = str(
                cog.persona.phrase(
                    interaction.guild_id, "cinema.pick_taken", title=_trim(info.title, 100)
                )
            )
            await interaction.response.edit_message(content=content, view=None)
            await cog.add_entry(interaction, info)

        select.callback = on_pick
        self.add_item(select)


class CinemaCardView(discord.ui.View):
    """👍/👎 на карточке фильма; persistent — переживает рестарт."""

    def __init__(self, cog: "CinemaCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(emoji="👍", style=discord.ButtonStyle.secondary, custom_id="cinema:vote:up")
    async def up_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_vote(interaction, +1)

    @discord.ui.button(
        emoji="👎", style=discord.ButtonStyle.secondary, custom_id="cinema:vote:down"
    )
    async def down_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_vote(interaction, -1)


class CinemaWatchedView(discord.ui.View):
    """Кнопка «Мы посмотрели» под анонсом победителя; persistent."""

    def __init__(self, cog: "CinemaCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Мы посмотрели — оценить",
        emoji="🍿",
        style=discord.ButtonStyle.primary,
        custom_id="cinema:beginrating",
    )
    async def begin_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.handle_begin_rating(interaction)


class ReviewModal(discord.ui.Modal, title="Твой отзыв о фильме"):
    """Окно-форма для текстовой рецензии зрителя."""

    review = discord.ui.TextInput(
        label="Рецензия",
        style=discord.TextStyle.paragraph,
        placeholder="Пара фраз: понравилось или нет и почему…",
        max_length=500,
        required=True,
    )

    def __init__(self, cog: "CinemaCog", rating_message_id: int):
        super().__init__()
        self.cog = cog
        self.rating_message_id = rating_message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_review(interaction, self.rating_message_id, str(self.review.value))


class CinemaRatingView(discord.ui.View):
    """Кнопки оценок 1–10 + «Отзыв»; persistent, фильм ищется по message_id."""

    def __init__(self, cog: "CinemaCog"):
        super().__init__(timeout=None)
        self.cog = cog
        for score in range(1, 11):
            button = discord.ui.Button(
                label=str(score),
                style=discord.ButtonStyle.secondary,
                row=0 if score <= 5 else 1,
                custom_id=f"cinema:rate:{score}",
            )
            button.callback = self._make_callback(score)
            self.add_item(button)
        review_btn = discord.ui.Button(
            label="Отзыв",
            emoji="✍️",
            style=discord.ButtonStyle.primary,
            row=2,
            custom_id="cinema:review",
        )
        review_btn.callback = self._open_review
        self.add_item(review_btn)

    def _make_callback(self, score: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.handle_rate(interaction, score)

        return callback

    async def _open_review(self, interaction: discord.Interaction) -> None:
        # message-id карточки оценок берём из интеракции нажатия — в сабмите
        # модалки его уже не будет
        await interaction.response.send_modal(ReviewModal(self.cog, interaction.message.id))


class NightPollView(discord.ui.View):
    """Опрос киновечера; custom_id содержит id ночи — после рестарта view
    пересоздаётся из БД и привязывается к сообщению заново."""

    def __init__(self, cog: "CinemaCog", night_id: int, candidates: list[MovieEntry]):
        super().__init__(timeout=None)
        options = [
            discord.SelectOption(label=_trim(_title_of(entry), 95), value=str(entry.id), emoji="🎬")
            for entry in candidates
        ]
        select = discord.ui.Select(
            placeholder="Твой голос…",
            options=options,
            custom_id=f"cinema:poll:{night_id}",
        )

        async def on_vote(interaction: discord.Interaction) -> None:
            await cog.handle_night_vote(interaction, int(select.values[0]))

        select.callback = on_vote
        self.add_item(select)
