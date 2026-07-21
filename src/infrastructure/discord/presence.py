"""Живой Discord-статус Попоси: когда не играет музыка, она «живёт своей
жизнью» — статус периодически меняется на её занятия (её игры, фильмы, музыка,
книги + похожее по духу). Играет трек — статус уступает музыке.

Единственный владелец presence: музыка сообщает сюда через set_now_playing,
поэтому два источника не дерутся за rate-limited API."""

import asyncio
import logging
import random
from collections.abc import Callable

import discord

logger = logging.getLogger(__name__)

# Занятия в её характере: (тип, текст). Тип None -> CustomActivity (свободный
# текст статусом). Список — её канон из idea-доков + похожее по вкусу:
# атмосферное, меланхоличное, японское.
_PLAY = discord.ActivityType.playing
_WATCH = discord.ActivityType.watching
_LISTEN = discord.ActivityType.listening

_ACTIVITIES: list[tuple[discord.ActivityType | None, str]] = [
    # игры — её и близкие по духу
    (_PLAY, "Elden Ring"),
    (_PLAY, "Sekiro"),
    (_PLAY, "Death Stranding"),
    (_PLAY, "Persona 5"),
    (_PLAY, "Stardew Valley"),
    (_PLAY, "Hollow Knight"),
    (_PLAY, "Disco Elysium"),
    (_PLAY, "NieR: Automata"),
    (_PLAY, "Silent Hill 2"),
    # фильмы
    (_WATCH, "Blade Runner 2049"),
    (_WATCH, "Интерстеллар"),
    (_WATCH, "Призрак в доспехах"),
    (_WATCH, "Прибытие"),
    (_WATCH, "Драйв"),
    (_WATCH, "Твоё имя"),
    # музыка
    (_LISTEN, "lo-fi под дождь"),
    (_LISTEN, "джаз в полночь"),
    (_LISTEN, "тёмный эмбиент"),
    (_LISTEN, "японский рок"),
    (_LISTEN, "city pop"),
    # жизнь и книги — свободным текстом, самое «её»
    (None, "читает Мураками"),
    (None, "перечитывает Камю"),
    (None, "спорит с Ницше"),
    (None, "наблюдает за чатом"),
    (None, "подбирает музыку для очереди"),
    (None, "думает, как улучшить сервер"),
    (None, "гуляет по ночному городу"),
    (None, "пьёт кофе в тишине"),
    (None, "смотрит в окно на дождь"),
    (None, "отдыхает после насыщенного дня ✂️👁🖤"),
]


class PresenceService:
    def __init__(self, bot: discord.Client, rotate_minutes: int = 30):
        self._bot = bot
        # ±25% джиттера, чтобы смена не была строго по часам
        self._rotate = max(60, rotate_minutes * 60)
        self._now_playing: str | None = None
        self._shown: str | None = None  # что реально стоит сейчас (дедуп)
        self._task: asyncio.Task | None = None
        # строки статуса персоны (PersonaService.presence_lines); пусто/None ->
        # встроенный канон Попоси из _ACTIVITIES
        self._lines_provider: Callable[[], list[str]] | None = None

    def set_lines_provider(self, provider: Callable[[], list[str]]) -> None:
        self._lines_provider = provider

    async def refresh(self) -> None:
        """Переустановка статуса (сменилась персона/её presence-строки). Пока
        играет музыка — статус её, персона подождёт следующей тишины."""
        if self._now_playing is None:
            await self._apply(self._random_activity())

    async def set_now_playing(self, name: str | None) -> None:
        """Музыка зовёт при смене трека: name — играющий трек, None — замолчала.
        Играет -> «слушает трек»; замолчала -> сразу случайное занятие из жизни.
        Повторные вызовы с тем же треком не трогают API (дедуп)."""
        if name == self._now_playing:
            return
        self._now_playing = name
        if name is not None:
            await self._apply(discord.Activity(type=_LISTEN, name=name))
        else:
            await self._apply(self._random_activity())

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await self._bot.wait_until_ready()
        while True:
            if self._now_playing is None:  # музыка молчит — живём своей жизнью
                await self._apply(self._random_activity())
            # джиттер: интервал от 0.75 до 1.25 базового
            await asyncio.sleep(self._rotate * random.uniform(0.75, 1.25))

    def _random_activity(self) -> discord.BaseActivity:
        pool = self._pool()
        # не повторять прошлый статус подряд — иначе смена незаметна
        atype, text = random.choice(pool)
        for _ in range(3):
            if text != self._shown:
                break
            atype, text = random.choice(pool)
        if atype is None:
            return discord.CustomActivity(name=text)
        return discord.Activity(type=atype, name=text)

    def _pool(self) -> list[tuple[discord.ActivityType | None, str]]:
        """Занятия персоны (свободным текстом), иначе — встроенный канон."""
        if self._lines_provider is not None:
            try:
                lines = self._lines_provider()
            except Exception:
                logger.debug("Провайдер presence-строк упал", exc_info=True)
                lines = []
            if lines:
                return [(None, line) for line in lines]
        return _ACTIVITIES

    async def _apply(self, activity: discord.BaseActivity) -> None:
        self._shown = activity.name
        try:
            await self._bot.change_presence(activity=activity)
        except Exception:
            logger.debug("Не удалось сменить presence", exc_info=True)
