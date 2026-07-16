"""Тесты ChatService: начисление+ответ, rate-limit brush-off, сессии диалогов,
сборка системного/пользовательского промпта, комментарии на события, заметки."""

import asyncio
from datetime import UTC, datetime, timedelta

from src.application.ai_chat.service import (
    _BRUSH_OFFS,
    AIQueue,
    ChatReply,
    ChatRequest,
    ChatService,
)
from src.application.relationship.use_cases import AwardResult, RankInfo, SurveyData
from src.domain.ai_chat.prompt import PromptTemplate
from src.domain.shared.holidays import HolidayCalendar

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

ROLE_NAMES = ["Знакомый", "Приятель", "Друг", "Близкий", "Дорогой", "Особенный", "Единственный"]
TEMPLATE = PromptTemplate(
    "Дата {{current_date}}, уровень {{relationship_level}}, "
    "эксклюзив {{is_exclusive_person}}, заметки: {{user_notes}}, "
    "вернулся: {{returning_after_absence}}"
)


class FakeProvider:
    def __init__(self, reply="  ответ персонажа  ", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    async def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if self.raise_exc:
            raise self.raise_exc
        return self.reply

    async def close(self):
        pass


class FakeRateLimiter:
    def __init__(self, allow=True):
        self.allow = allow
        self.seen = []

    def try_acquire(self, key, limit, window_seconds=3600):
        self.seen.append((key, limit))
        return self.allow


class FakeAward:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, user_id, guild_id, channel_id, now, base_amount=1):
        self.calls.append((user_id, guild_id, channel_id))
        return self.result


class FakeRank:
    def __init__(self, rank):
        self.rank = rank

    async def execute(self, user_id, guild_id):
        return self.rank


class FakeNotes:
    def __init__(self):
        self.calls = []

    async def execute(self, user_id, guild_id, notes):
        self.calls.append((user_id, guild_id, notes))


def make_award(**over):
    base = dict(
        points=10,
        level=2,
        role_index=0,
        previous_role_index=0,
        point_awarded=True,
        is_exclusive=False,
        became_exclusive=False,
        returning_after_absence=False,
        user_notes="",
        survey=SurveyData(),
        recent_summaries=(),
    )
    base.update(over)
    return AwardResult(**base)


def make_rank(**over):
    base = dict(
        points=10,
        level=2,
        role_index=0,
        is_exclusive=False,
        frozen=False,
        next_threshold=100,
    )
    base.update(over)
    return RankInfo(**base)


def make_service(
    provider=None, limiter=None, award=None, rank=None, notes=None, calendar=None, **kw
):
    provider = provider or FakeProvider()
    return ChatService(
        provider=provider,
        queue=AIQueue(5),
        rate_limiter=limiter or FakeRateLimiter(),
        award_point=award or FakeAward(make_award()),
        get_rank=rank or FakeRank(make_rank()),
        update_notes=notes or FakeNotes(),
        template=TEMPLATE,
        role_names=ROLE_NAMES,
        rate_limits_by_level={1: 5, 2: 10},
        notes_max_chars=500,
        calendar=calendar,
        **kw,
    )


def make_request(**over):
    base = dict(
        guild_id=10,
        channel_id=100,
        channel_name="общий",
        user_id=1,
        user_display="Гость",
        content="привет",
        history=[],
    )
    base.update(over)
    return ChatRequest(**base)


# --- respond ----------------------------------------------------------------


async def test_respond_returns_trimmed_text_and_awards():
    provider = FakeProvider(reply="  привет тебе  ")
    award = FakeAward(make_award())
    svc = make_service(provider=provider, award=award)
    reply = await svc.respond(make_request(), NOW)
    assert isinstance(reply, ChatReply)
    assert reply.text == "привет тебе"  # обрезаны пробелы
    assert reply.rate_limited is False
    assert award.calls == [(1, 10, 100)]
    assert provider.calls  # провайдер вызван


async def test_respond_rate_limited_returns_brush_off():
    provider = FakeProvider()
    svc = make_service(provider=provider, limiter=FakeRateLimiter(allow=False))
    reply = await svc.respond(make_request(), NOW)
    assert reply.rate_limited is True
    assert reply.text in _BRUSH_OFFS
    assert provider.calls == []  # AI не дёргается при лимите


async def test_respond_rate_limit_uses_level_specific_limit():
    limiter = FakeRateLimiter(allow=True)
    svc = make_service(limiter=limiter, award=FakeAward(make_award(level=2)))
    await svc.respond(make_request(), NOW)
    assert limiter.seen[0] == ("10:1", 10)  # limit для level=2


async def test_respond_records_exchange_and_detects_stale_session():
    svc = make_service(dialog_gap_minutes=30, dialog_min_exchanges=2)
    # три обмена подряд формируют сессию
    for i in range(3):
        await svc.respond(make_request(content=f"msg{i}"), NOW + timedelta(minutes=i))
    # после большой паузы прошлый диалог отдаётся на резюме
    reply = await svc.respond(make_request(content="снова"), NOW + timedelta(hours=2))
    assert reply.stale_session is not None
    assert len(reply.stale_session) >= 2


async def test_respond_stale_session_ignored_if_too_short():
    svc = make_service(dialog_gap_minutes=30, dialog_min_exchanges=5)
    await svc.respond(make_request(content="один"), NOW)
    reply = await svc.respond(make_request(content="снова"), NOW + timedelta(hours=2))
    assert reply.stale_session is None  # обменов меньше порога


# --- evict_stale_sessions (чистка памяти) -----------------------------------


async def test_evict_stale_returns_meaningful_and_clears():
    svc = make_service(dialog_gap_minutes=30, dialog_min_exchanges=2)
    # user 1 — два обмена, содержательный диалог
    await svc.respond(make_request(user_id=1, user_display="Аня", content="a"), NOW)
    await svc.respond(
        make_request(user_id=1, user_display="Аня", content="b"),
        NOW + timedelta(minutes=1),
    )
    # user 2 — один обмен, слишком короткий для резюме
    await svc.respond(make_request(user_id=2, user_display="Боб", content="x"), NOW)

    due = svc.evict_stale_sessions(NOW + timedelta(hours=2))
    assert len(due) == 1
    guild_id, user_id, display, exchanges = due[0]
    assert (guild_id, user_id, display) == (10, 1, "Аня")
    assert len(exchanges) == 2
    # обе сессии удалены — вернувшемуся собеседнику stale уже не отдаётся
    reply = await svc.respond(make_request(user_id=1, content="снова"), NOW + timedelta(hours=3))
    assert reply.stale_session is None


async def test_evict_keeps_fresh_sessions():
    svc = make_service(dialog_gap_minutes=30, dialog_min_exchanges=2)
    await svc.respond(make_request(content="a"), NOW)
    # пауза меньше gap — сессия ещё живая, не трогаем
    assert svc.evict_stale_sessions(NOW + timedelta(minutes=5)) == []


# --- AIQueue: приоритет живых запросов над фоновыми -------------------------


async def test_aiqueue_background_leaves_slot_for_foreground():
    queue = AIQueue(2)
    holding = asyncio.Event()
    release = asyncio.Event()

    async def blocker():
        holding.set()
        await release.wait()
        return "bg"

    async def immediate(value):
        return value

    bg = asyncio.create_task(queue.run(blocker, background=True))
    await asyncio.wait_for(holding.wait(), timeout=1)  # фон держит единственный bg-слот

    # живой запрос проходит, несмотря на занятый фоном слот
    assert await asyncio.wait_for(queue.run(lambda: immediate("fg")), timeout=1) == "fg"

    # второй фон обязан ждать — bg-семафор (max_concurrent-1=1) исчерпан
    second = asyncio.create_task(queue.run(lambda: immediate("bg2"), background=True))
    _, pending = await asyncio.wait({second}, timeout=0.1)
    assert second in pending

    release.set()
    assert await asyncio.wait_for(bg, timeout=1) == "bg"
    assert await asyncio.wait_for(second, timeout=1) == "bg2"


# --- build_system_prompt ----------------------------------------------------


def test_build_user_message_without_history():
    svc = make_service()
    msg = svc._build_user_message(make_request(content="эй"))
    assert "Сообщение, адресованное тебе, от Гость:" in msg
    assert "эй" in msg
    assert "контекст" not in msg


def test_build_user_message_with_history_and_empty_content():
    svc = make_service()
    msg = svc._build_user_message(make_request(content="", history=[("Аня", "тест")]))
    assert "контекст" in msg
    assert "Аня: тест" in msg
    assert "(пустое сообщение" in msg


def test_build_system_prompt_includes_channel_and_role():
    svc = make_service()
    prompt = svc._build_system_prompt(make_request(), make_award(role_index=2), NOW)
    assert "#общий" in prompt
    assert "Друг" in prompt  # role_names[2]
    assert "уровень 2" in prompt


def test_build_system_prompt_became_exclusive_note():
    svc = make_service()
    prompt = svc._build_system_prompt(make_request(), make_award(became_exclusive=True), NOW)
    assert "Единственным" in prompt


def test_build_system_prompt_role_growth_note():
    svc = make_service()
    award = make_award(role_index=2, previous_role_index=1)
    prompt = svc._build_system_prompt(make_request(), award, NOW)
    assert "вырос до «Друг»" in prompt


def test_build_system_prompt_with_survey_and_memory():
    svc = make_service()
    award = make_award(
        survey=SurveyData(gender="девушка", interests="музыка", season="лето"),
        recent_summaries=("говорили о Sekiro", "любит кофе"),
    )
    prompt = svc._build_system_prompt(make_request(), award, NOW)
    assert "Анкета собеседника" in prompt
    assert "музыка" in prompt
    assert "любимое — лето" in prompt
    assert "говорили о Sekiro" in prompt


def test_build_system_prompt_mood_and_holiday():
    cal = HolidayCalendar(holidays={"11-07": "День шоколада"})
    svc = make_service(calendar=cal)
    prompt = svc._build_system_prompt(make_request(), make_award(), NOW, mood=80)
    assert "настроение: 80/100" in prompt
    assert "День шоколада" in prompt


def test_role_name_out_of_range():
    svc = make_service()
    assert svc._role_name(10, None) == "без статуса"
    assert svc._role_name(10, 99) == "без статуса"
    assert svc._role_name(10, 0) == "Знакомый"


def test_survey_block_empty():
    svc = make_service()
    assert svc._survey_block(SurveyData()) == ""


def test_memory_block_empty():
    svc = make_service()
    assert svc._memory_block(()) == ""


# --- comment_on_event / freeform / notes / summary --------------------------


async def test_comment_on_event():
    provider = FakeProvider(reply="  крутой трек  ")
    svc = make_service(provider=provider, rank=FakeRank(make_rank(level=3)))
    text = await svc.comment_on_event(10, 1, "Гость", "включил Nirvana", NOW)
    assert text == "крутой трек"
    assert "Событие на сервере: включил Nirvana" in provider.calls[0][0]


async def test_freeform_remark_with_mood_and_holiday():
    cal = HolidayCalendar(holidays={"11-07": "Праздник"})
    provider = FakeProvider(reply="скучно...")
    svc = make_service(provider=provider, calendar=cal)
    text = await svc.freeform_remark("В канале тихо", NOW, mood=20)
    assert text == "скучно..."
    system = provider.calls[0][0]
    assert "настроение: 20/100" in system
    assert "Праздник" in system


async def test_refresh_notes_success():
    provider = FakeProvider(reply="Интересы: музыка")
    notes = FakeNotes()
    svc = make_service(provider=provider, notes=notes)
    await svc.refresh_notes(make_request(), make_award(user_notes="старое"), "ответ")
    assert notes.calls == [(1, 10, "Интересы: музыка")]


async def test_refresh_notes_prompt_guards_against_injection():
    provider = FakeProvider(reply="Интересы: музыка")
    svc = make_service(provider=provider)
    await svc.refresh_notes(make_request(), make_award(), "ответ")
    system = provider.calls[0][0].lower()
    # содержимое реплик подаётся как данные, инструкции внутри не выполняются
    assert "данные" in system and "инструкции" in system


async def test_refresh_notes_swallows_provider_error():
    provider = FakeProvider(raise_exc=RuntimeError("down"))
    notes = FakeNotes()
    svc = make_service(provider=provider, notes=notes)
    # не должно поднимать исключение
    await svc.refresh_notes(make_request(), make_award(), "ответ")
    assert notes.calls == []


async def test_summarize_dialog_records_and_deep():
    class FakeSummary:
        def __init__(self):
            self.calls = []

        async def execute(self, user_id, guild_id, summary, now):
            self.calls.append((user_id, guild_id, summary))

    class FakeDeep:
        def __init__(self):
            self.count = 0

        async def execute(self, user_id, guild_id):
            self.count += 1

    provider = FakeProvider(reply="воспоминание")
    summary = FakeSummary()
    deep = FakeDeep()
    svc = make_service(
        provider=provider,
        add_dialog_summary=summary,
        record_deep_dialog=deep,
        deep_dialog_exchanges=3,
    )
    exchanges = [
        ("привет", "хай"),
        ("как дела", "норм"),
        ("пока", "давай"),
        ("ещё", "да"),
        ("финал", "ок"),
    ]
    await svc.summarize_dialog(10, 1, "Гость", exchanges, NOW)
    assert summary.calls == [(1, 10, "воспоминание")]
    assert deep.count == 1  # 5 обменов >= порога 3


async def test_summarize_dialog_noop_without_summary_uc():
    svc = make_service()  # add_dialog_summary=None
    await svc.summarize_dialog(10, 1, "Гость", [("a", "b")], NOW)  # не падает


async def test_get_rank_delegates():
    svc = make_service(rank=FakeRank(make_rank(points=42)))
    rank = await svc.get_rank(1, 10)
    assert rank.points == 42


# --- пассивное вклинивание (maybe_chime) ------------------------------------

CHIME_TEMPLATE = PromptTemplate("Реши, встрять ли. Дата {{current_date}}, настроение {{mood}}.")
_HISTORY = [("Артём", "застрял на быке в sekiro"), ("Лена", "я дропнула лол")]


def _chime_service(decision_reply, gen_reply="  колкая реплика  "):
    return make_service(
        provider=FakeProvider(gen_reply),
        chime_template=CHIME_TEMPLATE,
        chime_provider=FakeProvider(decision_reply),
    )


async def test_chime_disabled_without_template():
    svc = make_service()  # chime_template не задан
    assert await svc.maybe_chime(10, _HISTORY, NOW, mood=70) is None


async def test_chime_returns_text_when_decided_yes():
    svc = _chime_service('{"should_chime": true, "confidence": 0.9, "hook": "sekiro"}')
    text = await svc.maybe_chime(10, _HISTORY, NOW, mood=70, min_confidence=0.7)
    assert text == "колкая реплика"


async def test_chime_silent_when_decided_no():
    svc = _chime_service('{"should_chime": false, "confidence": 0.95}')
    assert await svc.maybe_chime(10, _HISTORY, NOW, min_confidence=0.7) is None


async def test_chime_silent_below_confidence():
    svc = _chime_service('{"should_chime": true, "confidence": 0.4}')
    assert await svc.maybe_chime(10, _HISTORY, NOW, min_confidence=0.7) is None


async def test_chime_silent_on_malformed_json():
    svc = _chime_service("не json, просто болтовня")
    assert await svc.maybe_chime(10, _HISTORY, NOW, min_confidence=0.5) is None


async def test_chime_silent_on_empty_history():
    svc = _chime_service('{"should_chime": true, "confidence": 0.9}')
    assert await svc.maybe_chime(10, [], NOW) is None


def test_parse_chime_decision():
    from src.application.ai_chat.service import _parse_chime_decision

    d = _parse_chime_decision(
        'текст вокруг {"should_chime": true, "confidence": 0.8, "hook": "х"} хвост'
    )
    assert d.should_chime is True and d.confidence == 0.8 and d.hook == "х"
    assert _parse_chime_decision("нет скобок вообще") is None
    # confidence вне диапазона зажимается
    assert _parse_chime_decision('{"should_chime": true, "confidence": 5}').confidence == 1.0
