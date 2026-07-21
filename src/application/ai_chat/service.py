import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime

from src.application.interfaces.ai_provider import ChatMessage, IAIProvider
from src.application.interfaces.rate_limiter import IRateLimiter
from src.application.persona.registry import default_phrase
from src.application.relationship.use_cases import (
    AddDialogSummaryUseCase,
    AwardPointUseCase,
    AwardResult,
    GetRankUseCase,
    RecordDeepDialogUseCase,
    UpdateUserNotesUseCase,
)
from src.domain.ai_chat.prompt import PromptTemplate
from src.domain.shared.holidays import HolidayCalendar

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatRequest:
    guild_id: int
    channel_id: int
    channel_name: str
    user_id: int
    user_display: str
    content: str
    history: list[tuple[str, str]] = field(default_factory=list)  # (автор, текст)


@dataclass(frozen=True)
class ChatReply:
    text: str
    rate_limited: bool
    award: AwardResult
    # завершившийся (по паузе) прошлый диалог — ког отправит его на резюме
    stale_session: list[tuple[str, str]] | None = None


@dataclass(frozen=True)
class ChimeDecision:
    should_chime: bool
    confidence: float
    hook: str = ""


def _parse_chime_decision(raw: str) -> ChimeDecision | None:
    """Лояльный разбор JSON-решения: находим первый {...}, вытаскиваем поля.
    Любой сбой -> None (трактуется как «молчать»)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    try:
        confidence = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return ChimeDecision(
        should_chime=bool(data.get("should_chime")),
        confidence=max(0.0, min(1.0, confidence)),
        hook=str(data.get("hook") or "")[:120],
    )


class AIQueue:
    """Глобальное ограничение конкурентности запросов к провайдеру (ТЗ 8.4).
    Определён здесь как application-компонент; семафор — деталь реализации.

    Живой ответ пользователю (respond) имеет приоритет над фоновой работой
    (резюме диалога, обновление заметок, реплики-инициативы): фон занимает
    не больше max_concurrent-1 слотов, поэтому хотя бы один слот к провайдеру
    всегда свободен под живой запрос и тот не ждёт за пачкой фоновых."""

    def __init__(self, max_concurrent: int):
        import asyncio

        self._semaphore = asyncio.Semaphore(max_concurrent)
        # фон не выбирает последний слот — оставляет форграунду форточку
        self._background = asyncio.Semaphore(max(1, max_concurrent - 1))

    async def run(self, factory, *, background: bool = False):
        if background:
            async with self._background:
                async with self._semaphore:
                    return await factory()
        async with self._semaphore:
            return await factory()


class ChatService:
    def __init__(
        self,
        provider: IAIProvider,
        queue: AIQueue,
        rate_limiter: IRateLimiter,
        award_point: AwardPointUseCase,
        get_rank: GetRankUseCase,
        update_notes: UpdateUserNotesUseCase,
        template: PromptTemplate,
        role_names: list[str],
        rate_limits_by_level: dict[int, int],
        notes_max_chars: int,
        calendar: HolidayCalendar | None = None,
        add_dialog_summary: AddDialogSummaryUseCase | None = None,
        record_deep_dialog: RecordDeepDialogUseCase | None = None,
        dialog_gap_minutes: int = 30,
        dialog_min_exchanges: int = 3,
        deep_dialog_exchanges: int = 5,
        settings_provider=None,
        chime_template: PromptTemplate | None = None,
        chime_provider: IAIProvider | None = None,
        persona=None,
    ):
        self._calendar = calendar
        self._add_summary = add_dialog_summary
        self._record_deep = record_deep_dialog
        self._dialog_gap = dialog_gap_minutes
        self._dialog_min_exchanges = dialog_min_exchanges
        self._deep_exchanges = deep_dialog_exchanges
        self._settings = settings_provider
        # источник системного промпта per-guild: персона сервера (PersonaService,
        # утиный тип — как settings_provider, без импорта инфраструктуры). Пустой
        # промпт дефолт-персоны резолвится в файл-шаблон, поэтому поведение без
        # назначенной персоны = прежнему. None (тесты) → фолбэк на self._template.
        self._persona = persona
        # пассивное вклинивание: шаблон решения и (дешёвый) провайдер для него;
        # если шаблон не задан — фича выключена, maybe_chime всегда вернёт None
        self._chime_template = chime_template
        self._chime_provider = chime_provider
        # (guild_id, user_id) -> текущая сессия диалога (in-memory)
        self._sessions: dict[tuple[int, int], dict] = {}
        self._provider = provider
        self._queue = queue
        self._limiter = rate_limiter
        self._award_point = award_point
        self._get_rank = get_rank
        self._update_notes = update_notes
        self._template = template
        self._role_names = role_names
        self._rate_limits = rate_limits_by_level
        self._notes_max_chars = notes_max_chars

    # --- пер-серверные диалоговые параметры (override сервера или дефолт) ---

    def _cfg(self, guild_id: int, key: str, fallback):
        return (
            self._settings.get(guild_id, key, fallback) if self._settings is not None else fallback
        )

    # --- фразы каталога персоны (голос/инструкции промпта; дефолты реестра) ---

    def _phrase(self, guild_id: int, key: str, **variables: object) -> object:
        """Значение фразы: из персоны сервера, либо дефолт реестра, если персона
        не проброшена (тесты). Инструкции промпта тоже редактируются per-persona."""
        if self._persona is not None:
            return self._persona.phrase(guild_id, key, **variables)
        return default_phrase(key, **variables)

    def _phrase_str(self, guild_id: int, key: str, **variables: object) -> str:
        return str(self._phrase(guild_id, key, **variables))

    def _phrase_list(self, guild_id: int, key: str) -> list[str]:
        value = self._phrase(guild_id, key)
        return value if isinstance(value, list) else []

    # --- сессии диалогов (память) ---

    def _pop_stale_session(
        self, guild_id: int, user_id: int, now: datetime
    ) -> list[tuple[str, str]] | None:
        """Если прошлый диалог закончился (пауза больше gap) и был содержательным —
        возвращает его обмены для резюме и убирает сессию."""
        key = (guild_id, user_id)
        session = self._sessions.get(key)
        if session is None:
            return None
        gap = (now - session["last"]).total_seconds()
        if gap < self._cfg(guild_id, "ai_dialog_gap_minutes", self._dialog_gap) * 60:
            return None
        exchanges = self._sessions.pop(key)["exchanges"]
        if len(exchanges) >= self._cfg(
            guild_id, "ai_dialog_min_exchanges", self._dialog_min_exchanges
        ):
            return exchanges
        return None

    def _record_exchange(
        self,
        guild_id: int,
        user_id: int,
        user_display: str,
        user_text: str,
        reply: str,
        now: datetime,
    ) -> None:
        key = (guild_id, user_id)
        session = self._sessions.setdefault(key, {"exchanges": [], "last": now})
        session["exchanges"].append((user_text[:300], reply[:300]))
        session["exchanges"] = session["exchanges"][-12:]
        session["last"] = now
        session["display"] = user_display

    def evict_stale_sessions(
        self, now: datetime
    ) -> list[tuple[int, int, str, list[tuple[str, str]]]]:
        """Убирает из памяти все диалоги, где пауза превысила gap, чтобы
        словарь сессий не рос без предела на «одноразовых» собеседниках.
        Возвращает содержательные (>= min_exchanges) сессии — их фоновый цикл
        отправит на резюме, поэтому брошенный диалог тоже попадёт в память,
        а не только тот, к которому собеседник вернулся сам."""
        due: list[tuple[int, int, str, list[tuple[str, str]]]] = []
        for key in list(self._sessions):
            guild_id, user_id = key
            session = self._sessions[key]
            gap_seconds = self._cfg(guild_id, "ai_dialog_gap_minutes", self._dialog_gap) * 60
            if (now - session["last"]).total_seconds() < gap_seconds:
                continue
            session = self._sessions.pop(key)
            min_ex = self._cfg(guild_id, "ai_dialog_min_exchanges", self._dialog_min_exchanges)
            if len(session["exchanges"]) >= min_ex:
                display = session.get("display", "собеседник")
                due.append((guild_id, user_id, display, session["exchanges"]))
        return due

    async def summarize_dialog(
        self,
        guild_id: int,
        user_id: int,
        user_display: str,
        exchanges: list[tuple[str, str]],
        now: datetime,
    ) -> None:
        """Резюме завершившегося диалога + учёт «долгих разговоров»."""
        if self._add_summary is None:
            return
        try:
            transcript = "\n".join(f"{user_display}: {u}\nТы: {r}" for u, r in exchanges)
            system = self._phrase_str(guild_id, "ai_chat.summary_instruction")
            summary = await self._queue.run(
                lambda: self._provider.generate(system, [ChatMessage("user", transcript[:4000])]),
                background=True,
            )
            await self._add_summary.execute(user_id, guild_id, summary, now)
            deep = self._cfg(guild_id, "ai_deep_dialog_exchanges", self._deep_exchanges)
            if self._record_deep is not None and len(exchanges) >= deep:
                await self._record_deep.execute(user_id, guild_id)
        except Exception:
            logger.warning("Резюме диалога не сохранилось", exc_info=True)

    async def respond(
        self, request: ChatRequest, now: datetime, mood: int | None = None
    ) -> ChatReply:
        stale = self._pop_stale_session(request.guild_id, request.user_id, now)

        award = await self._award_point.execute(
            request.user_id, request.guild_id, request.channel_id, now
        )

        rate_limits = self._cfg(request.guild_id, "ai_rate_limits_by_level", self._rate_limits)
        limit = rate_limits.get(award.level, 5)
        key = f"{request.guild_id}:{request.user_id}"
        if not self._limiter.try_acquire(key, limit):
            return ChatReply(
                text=random.choice(self._phrase_list(request.guild_id, "ai_chat.brush_offs")),
                rate_limited=True,
                award=award,
                stale_session=stale,
            )

        system_prompt = self._build_system_prompt(request, award, now, mood)
        user_message = self._build_user_message(request)
        text = await self._queue.run(
            lambda: self._provider.generate(system_prompt, [ChatMessage("user", user_message)])
        )
        text = text.strip()
        self._record_exchange(
            request.guild_id,
            request.user_id,
            request.user_display,
            request.content,
            text,
            now,
        )
        return ChatReply(text=text, rate_limited=False, award=award, stale_session=stale)

    async def comment_on_event(
        self, guild_id: int, user_id: int, user_display: str, instruction: str, now: datetime
    ) -> str:
        """Короткая реплика на событие (включённый трек и т.п.)."""
        rank = await self._get_rank.execute(user_id, guild_id)
        variables = self._base_variables(now, rank.level, rank.is_exclusive, "", False)
        system_prompt = self._render_base(guild_id, variables) + "\n---\n" + self._phrase_str(
            guild_id, "ai_chat.event_instruction", instruction=instruction, user_display=user_display
        )
        text = await self._queue.run(
            lambda: self._provider.generate(system_prompt, [ChatMessage("user", instruction)]),
            background=True,
        )
        return text.strip()

    async def freeform_remark(
        self, guild_id: int, instruction: str, now: datetime, mood: int | None = None
    ) -> str:
        """Реплика без конкретного собеседника: скука в пустом канале,
        случайная мысль, приветствие новичка. Нейтральный тон (уровень 2).
        Промпт берётся от персоны сервера (guild_id)."""
        variables = self._base_variables(now, 2, False, "", False)
        system_prompt = self._render_base(guild_id, variables) + (
            "\n---\n"
            f"{self._mood_line(guild_id, mood)}"
            f"{self._holiday_line(guild_id, now)}"
            f"{instruction}\n"
            + self._phrase_str(guild_id, "ai_chat.freeform_tail")
        )
        text = await self._queue.run(
            lambda: self._provider.generate(system_prompt, [ChatMessage("user", instruction)]),
            background=True,
        )
        return text.strip()

    # --- пассивное вклинивание в чужие разговоры ---

    async def maybe_chime(
        self,
        guild_id: int,
        history: list[tuple[str, str]],
        now: datetime,
        mood: int | None = None,
        min_confidence: float = 0.7,
    ) -> str | None:
        """Дешёвая модель решает, встревать ли в разговор; если да и уверенность
        не ниже порога — основная модель генерит реплику в характере. None —
        молчим. Фича выключена, если шаблон решения не задан."""
        if self._chime_template is None or not history:
            return None
        decision = await self._decide_chime(guild_id, history, now, mood)
        if decision is None or not decision.should_chime:
            return None
        if decision.confidence < min_confidence:
            return None
        text = await self._generate_chime(guild_id, history, decision.hook, now, mood)
        return text or None

    async def _decide_chime(
        self, guild_id: int, history: list[tuple[str, str]], now: datetime, mood: int | None
    ) -> ChimeDecision | None:
        if self._chime_template is None:
            return None
        system = self._render_chime_decision(
            guild_id,
            {"current_date": now.strftime("%d.%m.%Y"), "mood": mood if mood is not None else 50},
        )
        conversation = "\n".join(f"{author}: {text}" for author, text in history)
        provider = self._chime_provider or self._provider
        try:
            raw = await self._queue.run(
                lambda: provider.generate(system, [ChatMessage("user", conversation[:4000])]),
                background=True,
            )
        except Exception:
            logger.warning("Решение о вклинивании не получено", exc_info=True)
            return None
        return _parse_chime_decision(raw)

    async def _generate_chime(
        self, guild_id: int, history: list[tuple[str, str]], hook: str, now: datetime, mood: int | None
    ) -> str:
        variables = self._base_variables(now, 2, False, "", False)
        system = self._render_base(guild_id, variables) + (
            "\n---\n"
            f"{self._mood_line(guild_id, mood)}"
            f"{self._holiday_line(guild_id, now)}"
            + self._phrase_str(guild_id, "ai_chat.chime_lead")
            + (self._phrase_str(guild_id, "ai_chat.chime_hook", hook=hook) if hook else "")
            + self._phrase_str(guild_id, "ai_chat.chime_body")
        )
        conversation = "Разговор в канале:\n" + "\n".join(
            f"{author}: {text}" for author, text in history
        )
        try:
            text = await self._queue.run(
                lambda: self._provider.generate(system, [ChatMessage("user", conversation[:4000])]),
                background=True,
            )
        except Exception:
            logger.warning("Реплика-вклинивание не сгенерировалась", exc_info=True)
            return ""
        return text.strip()

    async def refresh_notes(self, request: ChatRequest, award: AwardResult, reply: str) -> None:
        """Обновление заметки о пользователе отдельным дешёвым вызовом."""
        system = self._phrase_str(
            request.guild_id, "ai_chat.notes_instruction", max_chars=self._notes_max_chars
        )
        content = (
            f"Текущая заметка о «{request.user_display}»:\n{award.user_notes or '(пусто)'}\n\n"
            f"Свежий фрагмент диалога:\n{request.user_display}: {request.content}\n"
            f"Персонаж: {reply}"
        )
        try:
            notes = await self._queue.run(
                lambda: self._provider.generate(system, [ChatMessage("user", content)]),
                background=True,
            )
            await self._update_notes.execute(request.user_id, request.guild_id, notes)
        except Exception:
            logger.warning("Не удалось обновить заметку о пользователе", exc_info=True)

    # --- сборка промпта ---

    def _base_variables(
        self, now: datetime, level: int, is_exclusive: bool, notes: str, returning: bool
    ) -> dict[str, object]:
        return {
            "current_date": now.strftime("%d.%m.%Y"),
            "relationship_level": level,
            "is_exclusive_person": "true" if is_exclusive else "false",
            "user_notes": notes or "(заметок пока нет — вы почти не знакомы)",
            "returning_after_absence": "true" if returning else "false",
        }

    def _survey_block(self, guild_id: int, survey) -> str:
        if not (survey.gender or survey.interests or survey.season or survey.contact):
            return ""
        parts = [self._phrase_str(guild_id, "ai_chat.survey_header")]
        if survey.gender:
            parts.append(self._phrase_str(guild_id, "ai_chat.survey_gender", gender=survey.gender))
        if survey.interests:
            parts.append(
                self._phrase_str(guild_id, "ai_chat.survey_interests", interests=survey.interests)
            )
        if survey.season:
            note = (
                self._phrase_str(guild_id, "ai_chat.survey_season_summer")
                if survey.season == "лето"
                else ""
            )
            parts.append(
                self._phrase_str(guild_id, "ai_chat.survey_season", season=survey.season, note=note)
            )
        return "\n".join(parts)

    def _memory_block(self, guild_id: int, summaries: tuple[str, ...]) -> str:
        if not summaries:
            return ""
        lines = [self._phrase_str(guild_id, "ai_chat.memory_header")]
        lines.extend(f"- {s}" for s in summaries)
        lines.append(self._phrase_str(guild_id, "ai_chat.memory_footer"))
        return "\n".join(lines)

    async def get_rank(self, user_id: int, guild_id: int):
        """Доступ к рангу/анкете для когов (инициатива, фильтры внимания)."""
        return await self._get_rank.execute(user_id, guild_id)

    def _role_name(self, guild_id: int, index: int | None) -> str:
        names = self._cfg(guild_id, "relationship_role_names", self._role_names)
        if index is None or not (0 <= index < len(names)):
            return "без статуса"
        return names[index]

    def _mood_line(self, guild_id: int, mood: int | None) -> str:
        if mood is None:
            return ""
        from src.application.ai_chat.mood import MoodTracker

        return (
            self._phrase_str(
                guild_id, "ai_chat.mood_line", mood=mood, description=MoodTracker.describe(mood)
            )
            + "\n"
        )

    def _holiday_line(self, guild_id: int, now: datetime) -> str:
        if self._calendar is None:
            return ""
        name = self._calendar.holiday_name(now.date())
        if not name:
            return ""
        return self._phrase_str(guild_id, "ai_chat.holiday_line", holiday=name) + "\n"

    def _render_base(self, guild_id: int, variables: dict) -> str:
        """Базовый системный промпт персоны сервера (или файл-шаблон, если
        персона не проброшена — в тестах). Формат-хвосты добавляют вызывающие."""
        if self._persona is not None:
            return self._persona.render_prompt(guild_id, variables)
        return self._template.render(variables)

    def _render_chime_decision(self, guild_id: int, variables: dict) -> str:
        """Промпт решения о вклинивании (chime_prompt персоны или файл-шаблон)."""
        if self._persona is not None:
            return self._persona.render_chime_prompt(guild_id, variables)
        assert self._chime_template is not None
        return self._chime_template.render(variables)

    def _build_system_prompt(
        self,
        request: ChatRequest,
        award: AwardResult,
        now: datetime,
        mood: int | None = None,
    ) -> str:
        variables = self._base_variables(
            now,
            award.level,
            award.is_exclusive,
            award.user_notes,
            award.returning_after_absence,
        )
        gid = request.guild_id
        prompt = self._render_base(gid, variables)
        extra = [
            "---",
            self._mood_line(gid, mood).rstrip("\n"),
            self._holiday_line(gid, now).rstrip("\n"),
            self._phrase_str(
                gid,
                "ai_chat.context_line",
                channel=request.channel_name,
                user_display=request.user_display,
                status=self._role_name(gid, award.role_index),
            ),
            self._survey_block(gid, award.survey),
            self._memory_block(gid, award.recent_summaries),
        ]
        extra = [line for line in extra if line]
        if award.became_exclusive:
            extra.append(self._phrase_str(gid, "ai_chat.became_exclusive"))
        elif award.role_index != award.previous_role_index:
            extra.append(
                self._phrase_str(gid, "ai_chat.role_up", status=self._role_name(gid, award.role_index))
            )
        extra.append(self._phrase_str(gid, "ai_chat.answer_tail"))
        return prompt + "\n" + "\n".join(extra)

    def _build_user_message(self, request: ChatRequest) -> str:
        lines = []
        if request.history:
            lines.append("Последние сообщения канала (контекст):")
            lines.extend(f"{author}: {text}" for author, text in request.history)
            lines.append("")
        lines.append(f"Сообщение, адресованное тебе, от {request.user_display}:")
        lines.append(request.content or "(пустое сообщение, просто упоминание)")
        return "\n".join(lines)
