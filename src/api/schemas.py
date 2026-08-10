"""DTO ответов API (Pydantic). ID Discord — строками: snowflake больше 2^53 и
в JS-числе теряет точность."""

from pydantic import BaseModel, Field


class EntitlementDTO(BaseModel):
    """Тариф (подписка) сервера — для панели. Показывается только оператору бота."""

    guild_id: str
    tier: str  # эффективный тариф: free | premium | pro
    active: bool  # есть ли явная (неистёкшая) подписка, а не просто дефолт
    expires_at: str | None = None  # ISO8601 UTC; null = бессрочно / нет подписки
    default_tier: str  # тариф по умолчанию (ENTITLEMENTS_DEFAULT_TIER) — для контекста
    enforced: bool  # включён ли enforcement (default_tier != pro)
    trial_used: bool = False  # пробный период уже использован (кнопка триала гаснет)


class EntitlementGrant(BaseModel):
    """Ручная выдача подписки: тариф + срок в днях (N времени)."""

    tier: str  # free | premium | pro (обычно premium/pro)
    duration_days: int | None = Field(default=None, ge=0, le=3650)  # null/0 = бессрочно


class GuildPermsDTO(BaseModel):
    """Права пользователя Discord на сервере — вычислены бэкендом из маски сессии.
    Фронт по ним гасит недоступные действия (не 403 по клику), но границу всё
    равно стережёт гвард на бэке — это лишь UX-подсказка. Легаси-токен без
    записанных битов трактуется как «всё можно» (см. Session.has_permission)."""

    can_ban: bool
    can_kick: bool
    can_moderate: bool  # тайм-аут; сюда же relies clearwarns
    can_manage_roles: bool


class GuildDTO(BaseModel):
    id: str
    name: str
    icon: str | None = None
    perms: GuildPermsDTO


class MeDTO(BaseModel):
    user_id: str
    username: str
    avatar: str | None = None
    guilds: list[GuildDTO]  # серверы, где пользователь может управлять
    # оператор бота (web_operator_ids) — фронт по этому флагу показывает вкладку
    # «Персона»; сам доступ к роутам всё равно стережёт require_operator на бэке
    is_operator: bool = False


class SettingFieldDTO(BaseModel):
    """Одна настройка сервера: описание поля + текущее значение. Фронт строит по
    этому форму, не хардкодя поля. channel-значения — строками (snowflake > 2^53)."""

    key: str
    label: str
    kind: str  # bool | channel | float | int
    unit: str = ""
    min: float | None = None
    max: float | None = None
    default: bool | int | float | str  # глобальный дефолт из .env
    value: bool | int | float | str  # действующее значение (override или дефолт)
    is_override: bool  # переопределено на этом сервере


class SettingUpdate(BaseModel):
    # значение приходит как есть (строка/число/булево); бэкенд валидирует и парсит
    value: bool | int | float | str


class BatchUpdate(BaseModel):
    # для списочных/словарных настроек (роли, лимиты): несколько ключей разом,
    # чтобы связанные (пороги ↔ имена) прошли кросс-валидацию вместе (set_many)
    values: dict[str, object]


# --- персоны (только оператор бота) ---


class PersonaSummaryDTO(BaseModel):
    """Строка списка библиотек персон."""

    id: int
    name: str
    is_default: bool
    assigned_count: int  # на скольких серверах активна


class PersonaDetailDTO(BaseModel):
    """Персона для редактора: сохранённые промпты (пусто = встроенный дефолт) +
    сами встроенные дефолты для показа как базовой строки."""

    id: int
    name: str
    is_default: bool
    prompt: str
    chime_prompt: str
    default_prompt: str  # встроенный промпт из файла (что применяется при пустом)
    default_chime_prompt: str
    assigned_count: int


class PersonaImportIssueDTO(BaseModel):
    """Одна отброшенная при импорте строка: что и почему (заготовку правят
    руками — оператор должен видеть, что не приняли)."""

    key: str | None  # ключ фразы/атрибута; None если строка вообще не объект
    reason: str


class PersonaImportReportDTO(BaseModel):
    phrases_accepted: int
    phrases_ignored: list[PersonaImportIssueDTO]
    attributes_ignored: list[PersonaImportIssueDTO]


class PersonaImportResultDTO(BaseModel):
    """Ответ импорта: созданная персона + отчёт о принятом/отброшенном."""

    persona: PersonaDetailDTO
    report: PersonaImportReportDTO


class PersonaCreate(BaseModel):
    name: str
    duplicate_of: int | None = None  # если задано — копия этой персоны


class PersonaRename(BaseModel):
    name: str


class PromptUpdate(BaseModel):
    prompt: str  # пустая строка = сброс к встроенному дефолту


class PersonaPhraseDTO(BaseModel):
    """Одна строка каталога фраз в редакторе: дефолт из кода + override."""

    key: str
    label: str
    category: str
    kind: str  # str | template | list | dict
    default: object
    value: object | None = None  # None = override нет (действует дефолт)
    mode: str
    is_override: bool
    placeholders: list[str]
    allowed_modes: list[str]


class PhraseUpdate(BaseModel):
    value: object
    mode: str | None = None  # None = оставить/взять дефолтный режим ключа


class PhraseReplace(BaseModel):
    """Глобальный find-and-replace по фразам персоны."""

    find: str
    replace: str
    dry_run: bool = False


class PhraseChangeDTO(BaseModel):
    key: str
    before: object
    after: object


class PersonaIdentityDTO(BaseModel):
    """Мягкая личность персоны (эффективные значения) + дефолты из кода для
    подсказки «сброс вернёт вот это»."""

    display_name: str
    signature: str
    accent_color: int  # 0..0xFFFFFF
    presence: list[str]  # строки Discord-статуса; пусто = встроенный канон
    default_display_name: str
    default_signature: str
    default_accent_color: int


class PersonaIdentityUpdate(BaseModel):
    display_name: str
    signature: str
    accent_color: int
    presence: list[str]


class GuildPersonaDTO(BaseModel):
    guild_id: str
    persona_id: int  # что реально применяется (назначенная или дефолт)


class GuildPersonaAssign(BaseModel):
    persona_id: int
