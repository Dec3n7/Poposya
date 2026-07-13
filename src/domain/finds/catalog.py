"""Каталог «Ночных находок»: локации, предметы, вероятности.

Данные живут в коде (как holidays.py): пополнять — просто добавить строку,
БД хранит только id. Сезонность: предмет с season получает двойной вес,
когда на дворе его время года (лето — цветы и фотографии, осень — листья
и старые книги, ТЗ hunt_sys.md)."""

import random
from dataclasses import dataclass

from src.domain.finds.entities import Rarity


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    flavor: str  # строка атмосферы для анонса


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    rarity: Rarity
    flavor: str
    season: str | None = None   # "весна" | "лето" | "осень" | "зима"
    holiday: str | None = None  # "ДД-ММ": предмет существует только в этот день
    emoji: str = "🎁"           # тематическая иконка предмета


LOCATIONS: tuple[Location, ...] = (
    Location("nezu_square", "Заброшенный сквер у станции Nezu",
             "трава по колено и ни одного фонаря, который бы не мигал"),
    Location("shibuya_roof", "Крыша старого здания в Shibuya",
             "доступ через пожарную лестницу; город внизу гудит, как чужой сон"),
    Location("yanaka_shrine", "Маленькое синтоистское святилище в Yanaka",
             "колокольчик звенит сам по себе, если стоять тихо"),
    Location("kanda_bench", "Скамейка у реки Kanda под мостом",
             "капли с моста падают ровно в такт, я проверяла"),
    Location("koenji_vinyl", "Задний двор винилового магазинчика в Koenji",
             "ящики с пластинками, которые никто не разбирал лет десять"),
    Location("aoyama_park", "Ночной парк в Aoyama",
             "фонари здесь тёплые, а тени — нет"),
    Location("golden_gai", "Переулок с красными фонарями в Golden Gai",
             "семь баров на десять метров и ни одной свободной табуретки"),
    Location("rainbow_bridge", "Набережная у Rainbow Bridge",
             "поздно ночью мост выглядит так, будто ведёт не туда"),
    Location("shinjuku_roof", "Заброшенный rooftop в Shinjuku",
             "кто-то оставил здесь два стула. Друг напротив друга"),
    Location("yoyogi_temple", "Храм в Yoyogi под дождём",
             "дождь по крыше храма — лучший звук в этом городе"),
    Location("closed_cafe", "Старое кафе, закрывшееся месяц назад",
             "стулья всё ещё на столах, а запах кофе — всё ещё в стенах"),
    Location("harajuku_street", "Улица уличных художников в Harajuku",
             "краска на асфальте свежая, а художников уже нет"),
    Location("setagaya_block", "Тихий жилой квартал в Setagaya",
             "здесь так тихо, что слышно, как остывает асфальт"),
    Location("asakusa_bridge", "Мостик над каналом в Asakusa",
             "вода тёмная и несёт чей-то бумажный фонарик"),
    Location("yanaka_cemetery", "Кладбище в Yanaka",
             "атмосферно, но не страшно. Мне — точно нет"),
)

ITEMS: tuple[Item, ...] = (
    # --- обычные (8) ---
    Item("postcard_90s", "Промокшая открытка 90-х", Rarity.COMMON,
         "чернила поплыли, но «скучаю» ещё читается", emoji="💌"),
    Item("hibiki_mini", "Пустая миниатюра Hibiki", Rarity.COMMON,
         "кто-то пил хороший виски в плохом месте", emoji="🥃"),
    Item("hair_ribbon", "Выцветшая лента для волос", Rarity.COMMON,
         "когда-то была алой. Токио выпил цвет", emoji="🎀"),
    Item("train_ticket", "Старый билет на поезд", Rarity.COMMON,
         "в один конец, до станции, которой больше нет", emoji="🎫"),
    Item("cat_badge", "Ржавый значок с кошкой", Rarity.COMMON,
         "кошка смотрит осуждающе. Уважаю", emoji="🐱"),
    Item("shrine_candle", "Огарок свечи из храма", Rarity.COMMON,
         "догорела ровно наполовину и погасла сама", emoji="🕯️"),
    Item("ramune_marble", "Стеклянный шарик из бутылки рамунэ", Rarity.COMMON,
         "лето, запертое в стекле", season="лето", emoji="🔮"),
    Item("maple_leaf", "Кленовый лист, прижатый камнем", Rarity.COMMON,
         "кто-то оставил его сушиться и не вернулся", season="осень", emoji="🍁"),
    # --- необычные (10) ---
    Item("film_photo", "Плёночная фотография: силуэт под дождём", Rarity.UNCOMMON,
         "девушка с зонтом, лица не разглядеть. Может, и к лучшему", emoji="🖼️"),
    Item("butterfly_pin", "Сломанная серебряная заколка-бабочка", Rarity.UNCOMMON,
         "одно крыло погнуто. Летала, пока могла", emoji="🦋"),
    Item("notebook_jp", "Записная книжка с японскими записями", Rarity.UNCOMMON,
         "последняя запись: «завтра обязательно скажу». Дальше пусто", emoji="📓"),
    Item("gashapon_rare", "Фигурка из гашапона — редкий персонаж", Rarity.UNCOMMON,
         "тот самый, ради которого крутят по двадцать раз", emoji="🧸"),
    Item("night_cassette", "Кассета «для ночных поездок»", Rarity.UNCOMMON,
         "подписана от руки. Сторона B стёрта почти в ноль", emoji="📼"),
    Item("furin_bell", "Латунный колокольчик фурин", Rarity.UNCOMMON,
         "звенит, даже когда ветра нет", season="лето", emoji="🎐"),
    Item("cinema_ticket", "Билет в кино, 2003 год", Rarity.UNCOMMON,
         "сеанс 23:40, место 13. Фильм давно забыли, билет — нет", emoji="🎟️"),
    Item("cracked_mirror", "Карманное зеркальце с трещиной", Rarity.UNCOMMON,
         "трещина ровно посередине. Отражение всё равно честное", emoji="🪞"),
    Item("arcade_stickers", "Наклейки из старого аркадного зала", Rarity.UNCOMMON,
         "зал закрылся, а очки на автомате так и висят", emoji="🕹️"),
    Item("haiku_page", "Вырванная страница с хайку про осень", Rarity.UNCOMMON,
         "почерк торопливый, будто дописывали на ходу", season="осень", emoji="📜"),
    # --- редкие (6) ---
    Item("film_roll", "Почти целая плёнка, 12 кадров", Rarity.RARE,
         "не проявлена. Двенадцать чужих моментов в кассете", emoji="🎞️"),
    Item("zippo_engraved", "Зажигалка Zippo с гравировкой", Rarity.RARE,
         "«не гасни» — выбито изнутри крышки", emoji="🔥"),
    Item("murakami_notes", "Книга Мураками с пометками на полях", Rarity.RARE,
         "читатель спорил с автором. Иногда — выигрывал", season="осень", emoji="📖"),
    Item("embroidered_cloth", "Платок с вышивкой", Rarity.RARE,
         "инициалы «М. К.» и ветка сакуры. Ручная работа", emoji="🧵"),
    Item("shibuya_polaroid", "Полароид ночного Shibuya", Rarity.RARE,
         "перекрёсток без единого человека. Такого не бывает", season="лето", emoji="📸"),
    Item("netsuke_fox", "Нэцкэ-лисица из тёмного дерева", Rarity.RARE,
         "отполирована чьими-то пальцами до блеска", emoji="🦊"),
    # --- легендарные (3) ---
    Item("unsent_letter", "Письмо без адресата", Rarity.LEGENDARY,
         "запечатано. Я не вскрывала. Почти уверена, что не вскрывала", emoji="✉️"),
    Item("unknown_key", "Старый ключ от неизвестной двери", Rarity.LEGENDARY,
         "тяжёлый, латунный. Где-то в Токио есть его дверь", emoji="🗝️"),
    Item("tokyo_1987", "Фотография Токио 1987 года", Rarity.LEGENDARY,
         "город, которого больше нет, в идеальной сохранности", emoji="🏙️"),
    # --- праздничные: существуют только в свой день (сезонные события) ---
    Item("first_bell", "Колокольчик первого рассвета", Rarity.RARE,
         "звонил в новогоднюю ночь у храма. Один раз — и хватит",
         holiday="01-01", emoji="🔔"),
    Item("lost_valentine", "Недоставленная валентинка", Rarity.UNCOMMON,
         "имя размыто дождём. Чувство — нет",
         holiday="14-02", emoji="💘"),
    Item("poposya_polaroid", "Полароид с вечеринки Попоси", Rarity.LEGENDARY,
         "я на нём почти улыбаюсь. Почти. Никому не показывай",
         holiday="02-06", emoji="🖤"),
    Item("kitsune_mask", "Треснувшая маска кицунэ", Rarity.RARE,
         "трещина ровно между глаз. Надевать не советую",
         holiday="31-10", emoji="🎭"),
    Item("snow_globe", "Стеклянный шар с ночным Токио", Rarity.UNCOMMON,
         "встряхнёшь — и над Синдзюку идёт снег",
         holiday="25-12", emoji="❄️"),
)

_ITEMS_BY_ID = {item.id: item for item in ITEMS}
_LOCATIONS_BY_ID = {loc.id: loc for loc in LOCATIONS}

# вероятности редкости, % (hunt_sys.md)
RARITY_WEIGHTS: dict[Rarity, int] = {
    Rarity.COMMON: 55,
    Rarity.UNCOMMON: 30,
    Rarity.RARE: 12,
    Rarity.LEGENDARY: 3,
}
# повышенные шансы: специальная прогулка и уровень отношений 6+
BOOSTED_RARITY_WEIGHTS: dict[Rarity, int] = {
    Rarity.COMMON: 35,
    Rarity.UNCOMMON: 35,
    Rarity.RARE: 22,
    Rarity.LEGENDARY: 8,
}

# очки отношений за находку и за подарок Попосе
REWARD_RANGES: dict[Rarity, tuple[int, int]] = {
    Rarity.COMMON: (15, 25),
    Rarity.UNCOMMON: (35, 55),
    Rarity.RARE: (70, 110),
    Rarity.LEGENDARY: (140, 200),
}
GIFT_BONUSES: dict[Rarity, int] = {
    Rarity.COMMON: 20,
    Rarity.UNCOMMON: 40,
    Rarity.RARE: 80,
    Rarity.LEGENDARY: 120,
}

RARITY_LABELS: dict[Rarity, str] = {
    Rarity.COMMON: "обычная",
    Rarity.UNCOMMON: "необычная",
    Rarity.RARE: "редкая",
    Rarity.LEGENDARY: "легендарная",
}
RARITY_EMOJI: dict[Rarity, str] = {
    Rarity.COMMON: "⚪",
    Rarity.UNCOMMON: "🔵",
    Rarity.RARE: "🟣",
    Rarity.LEGENDARY: "🌟",
}


def season_for_month(month: int) -> str:
    if month in (12, 1, 2):
        return "зима"
    if month in (3, 4, 5):
        return "весна"
    if month in (6, 7, 8):
        return "лето"
    return "осень"


def get_item(item_id: str) -> Item | None:
    return _ITEMS_BY_ID.get(item_id)


def get_location(location_id: str) -> Location | None:
    return _LOCATIONS_BY_ID.get(location_id)


def success_chance(level: int) -> float:
    """Шанс успеха похода по уровню отношений (hunt_sys.md, п. 5)."""
    if level >= 7:
        return 0.70
    if level >= 5:
        return 0.60
    if level >= 3:
        return 0.52
    return 0.45


def roll_item(
    rng: random.Random,
    season: str | None = None,
    boosted: bool = False,
    holiday: str | None = None,
) -> Item:
    """Редкость по весам, затем предмет внутри редкости. Предметы «в сезон»
    выпадают вдвое чаще; праздничные существуют только в свой день ("ДД-ММ")
    и в этот день выпадают вчетверо чаще обычных."""
    weights = BOOSTED_RARITY_WEIGHTS if boosted else RARITY_WEIGHTS
    rarity = rng.choices(list(weights), weights=list(weights.values()))[0]
    pool = [
        item for item in ITEMS
        if item.rarity is rarity and (item.holiday is None or item.holiday == holiday)
    ]
    item_weights = []
    for item in pool:
        if holiday and item.holiday == holiday:
            item_weights.append(4)
        elif season and item.season == season:
            item_weights.append(2)
        else:
            item_weights.append(1)
    return rng.choices(pool, weights=item_weights)[0]


def roll_location(rng: random.Random) -> Location:
    return rng.choice(LOCATIONS)


def roll_reward(rng: random.Random, rarity: Rarity, exclusive_bonus: bool = False) -> int:
    """Очки за находку; «Единственному» — +10–20 сверху (hunt_sys.md, п. 5)."""
    low, high = REWARD_RANGES[rarity]
    reward = rng.randint(low, high)
    if exclusive_bonus:
        reward += rng.randint(10, 20)
    return reward
