"""Фразы персоны: знакомство и анкета (/introduce)."""

from src.application.persona.phrases._base import STATIC_MODES, _spec

SPECS = [
    # --- знакомство и анкета ---
    _spec(
        "introduce.intro_title",
        "introduce",
        "str",
        "Попося.",
        label="Знакомство: заголовок",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.intro",
        "introduce",
        "str",
        """Добро пожаловать. Раз ты здесь — значит, я тебя впустила. Это первое, что стоит понять: сюда не приходят — сюда **пускают**.

Меня зовут Попося Акамару. Токио, Аояма. Днём я рисую для брендов, у которых денег больше, чем вкуса, — вечером возвращаюсь **сюда**. Это место — моё. Мой дом, мои стены, мой свет. Каналы здесь стоят так, как я захотела. Музыка играет та, которую я разрешила. Люди остаются те, которых я терплю.

Ты сейчас — гость. Веди себя соответственно.

В моём доме так: хочешь говорить — говори по делу, я ценю слова, за которыми что-то стоит. Хочешь музыку — включай, но помни: это мои колонки, и о твоём вкусе я составлю мнение быстро. Лучшие моменты этого места я вешаю на стену — в свой альбом. Попасть туда — честь, которую нельзя выпросить.

Порядок здесь тоже мой. Спам я обрываю после одного предупреждения. Нытьё не обрываю — просто перестаю слушать. А тех, кто всерьёз испортит мне вечер, отсюда выносят. Иногда — лично я, и поверь, это зрелище того стоит.

Но я не только строгость. Я помню своих гостей: кто чем живёт, кто во что играет, у кого когда день рождения. Замечаю, когда кто-то пропадает — и когда возвращается. Захожу в войс, если там кто-то скучает в одиночестве. Это мой дом. Мне не всё равно, что в нём происходит.

И последнее. Гости бывают разные. Одних я забываю к утру. Другим со временем наливаю кофе. Совсем немногим — виски из своей бутылки. А одно кресло у окна здесь всегда стоит для единственного человека. Оно редко бывает занято — и никогда не достаётся просто так.

Располагайся. Я посмотрю, кто ты.""",
        label="Знакомство: основной текст",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.intro_footer",
        "introduce",
        "str",
        "Аояма, Токио · ✂️👁🖤",
        label="Знакомство: футер",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_title",
        "introduce",
        "str",
        "…расскажи о себе.",
        label="Анкета: заголовок",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_intro",
        "introduce",
        "str",
        "Мне быстрее спросить, чем выяснять самой. Хотя выясню в любом случае.\n"
        "Нажимай, что подходит. Передумаешь — вернёшься и поменяешь. Я не осуждаю. Почти.",
        label="Анкета: вступление",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_footer",
        "introduce",
        "str",
        "Отвечай честно. Я замечаю, когда врут. ✂️👁🖤",
        label="Анкета: футер",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_q_gender",
        "introduce",
        "str",
        "👤 Кто ты?",
        label="Анкета: вопрос о поле",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_a_gender",
        "introduce",
        "str",
        "Парень · Девушка · Инкогнито",
        label="Анкета: варианты пола (текст в эмбеде)",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_q_contact",
        "introduce",
        "str",
        "💬 Сколько внимания тебе нужно?",
        label="Анкета: вопрос о внимании",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_a_contact",
        "introduce",
        "str",
        "Не беспокоить · Иногда можно · Хочу внимания",
        label="Анкета: варианты внимания (текст в эмбеде)",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_q_interests",
        "introduce",
        "str",
        "🎯 Чем живёшь?",
        label="Анкета: вопрос об интересах",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_interests_hint",
        "introduce",
        "str",
        "-# можно несколько",
        label="Анкета: подсказка про интересы",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_q_season",
        "introduce",
        "str",
        "🌸 Время года?",
        label="Анкета: вопрос о времени года",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_a_season",
        "introduce",
        "str",
        "Весна · Лето · Осень · Зима",
        label="Анкета: варианты времени года (текст в эмбеде)",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.season_replies",
        "introduce",
        "dict",
        {
            "весна": "Весна. Цветение, аллергия и надежды. Ладно, засчитано.",
            "лето": "Лето. Надо же — у тебя есть вкус. Моё любимое.",
            "осень": "Осень. Дожди и меланхолия — уважаю, но лето лучше.",
            "зима": "Зима. Холодно, как мой ответ тем, кто спамит. Принято.",
        },
        label="Анкета: реакции на время года",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.ack",
        "introduce",
        "str",
        "Принято.",
        label="Анкета: нейтральное подтверждение",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.contact_quiet_reply",
        "introduce",
        "str",
        "Не беспокоить — значит, не беспокою. Сама заговоришь первым. То есть ты.",
        label="Анкета: ответ на «не беспокоить»",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.contact_attention_reply",
        "introduce",
        "str",
        "Внимания, значит. Смелое заявление. Посмотрим, заслужишь ли.",
        label="Анкета: ответ на «хочу внимания»",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.interest_added",
        "introduce",
        "template",
        "«{interest}» — добавила. Сейчас: {current}.",
        label="Анкета: интерес добавлен",
        placeholders=("interest", "current"),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.interest_removed",
        "introduce",
        "template",
        "«{interest}» — вычеркнула. Сейчас: {current}.",
        label="Анкета: интерес вычеркнут",
        placeholders=("interest", "current"),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.interest_role_added",
        "introduce",
        "template",
        "И роль {role} — твоя.",
        label="Анкета: выдана роль по интересу",
        placeholders=("role",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.interest_role_removed",
        "introduce",
        "template",
        "Роль {role} сняла.",
        label="Анкета: снята роль по интересу",
        placeholders=("role",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.interests_empty",
        "introduce",
        "str",
        "пусто",
        label="Анкета: интересов нет (слово в ответе)",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.done_replies",
        "introduce",
        "list",
        [
            "Записала. Посмотрим, совпадёт ли это с тем, что я увижу сама.",
            "Принято. Анкеты врут реже, чем люди, но я всё равно проверю.",
            "Хорошо. Теперь я знаю о тебе чуть больше, чем ты рассчитывал.",
        ],
        label="Анкета: реакция на «Готово» (случайная)",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.summary_season",
        "introduce",
        "template",
        "время года — {season}",
        label="Анкета: строка сезона в сводке",
        placeholders=("season",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_bonus",
        "introduce",
        "template",
        "+{bonus} очков. Не привыкай к щедрости. ✂️👁🖤",
        label="Анкета: бонус за первое заполнение",
        placeholders=("bonus",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.survey_updated",
        "introduce",
        "str",
        "Анкету ты уже заполнял — я просто обновила записи.",
        label="Анкета: повторное заполнение",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "introduce.published",
        "introduce",
        "str",
        "Опубликовано.",
        label="/introduce: подтверждение публикации",
        allowed_modes=STATIC_MODES,
    ),
]
