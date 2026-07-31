from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Минимальная длина ключа подписи сессий. token_urlsafe(32) даёт 43 символа —
# с запасом; всё короче считаем ошибкой конфигурации, а не рабочим секретом.
_MIN_SESSION_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    discord_token: str
    # id сервера для мгновенной синхронизации слеш-команд в разработке;
    # без него команды синхронизируются глобально (до часа задержки)
    dev_guild_id: int | None = None

    log_level: str = "INFO"
    log_format: str = "json"  # json | plain
    log_file: str = "logs/bot.log"  # DEBUG-лог с ротацией; пусто = только консоль
    # лог в Discord-канал: ID канала (0 = выключено) и минимальный уровень
    discord_log_channel: int = 0
    discord_log_level: str = "WARNING"  # DEBUG | INFO | WARNING | ERROR

    ffmpeg_path: str = "ffmpeg"

    # --- health-эндпоинт (aiohttp, /health и /ready) ---
    health_port: int = 8080

    # --- веб-панель (отдельный FastAPI-процесс; секреты ТОЛЬКО в .env) ---
    # discord_client_id/secret — из Discord Developer Portal (OAuth2).
    discord_client_id: str = ""
    discord_client_secret: str = ""
    web_oauth_redirect: str = "http://localhost:8081/api/auth/callback"
    # ключ подписи JWT-сессий (сгенерировать своим secrets.token_urlsafe)
    web_session_secret: str = ""
    web_allowed_origin: str = "http://localhost:5173"  # адрес фронта для CORS
    web_api_port: int = 8081
    web_session_ttl_hours: int = 24
    # серверный «рубильник» сессий: версия вшивается в JWT (claim sv), при
    # расшифровке сверяется. Увеличить на 1 в .env -> все выданные токены
    # мгновенно недействительны (аварийный logout всех, без ротации секрета
    # подписи и без стора на сервере). Украденный токен так гасится до TTL.
    web_session_version: int = 1
    # интерактивная схема FastAPI (/docs, /redoc, /openapi.json). По умолчанию
    # ВЫКЛ — на публике не светим карту эндпоинтов; для локальной разработки
    # можно включить в .env: WEB_DOCS_ENABLED=true
    web_docs_enabled: bool = False
    # сколько ждать результат команды моста (бан/мут/музыка) перед ответом
    # «отправлено, применяется»; бот обычно исполняет за доли секунды
    web_command_wait_seconds: float = 5.0
    # операторы бота (Discord-ID): только они управляют персонами в панели.
    # Серверные админы персону не трогают (границу проверяет require_operator).
    web_operator_ids: list[int] = []

    # --- WARDEN (внешний сторож-монитор) ---
    # Панель показывает его состояние на отдельной вкладке. Ходит только
    # бэкенд: токен во фронт не попадает. Пусто = вкладка не показывается.
    warden_api_url: str = ""
    warden_api_token: str = ""

    # --- база данных ---
    database_url: str = "sqlite+aiosqlite:///./poposya.db"
    auto_migrate: bool = True  # применять миграции Alembic при старте (dev-режим)
    # бэкап при старте и раз в N часов, хранится backup_keep последних
    # (0 в любом поле = выключено). SQLite — online-копия рядом с БД; Postgres —
    # pg_dump -Fc в backup_dir (в Docker это volume bot_data, отдельный от БД)
    backup_interval_hours: int = 24
    backup_keep: int = 7
    backup_dir: str = "data/backups"  # используется для Postgres; SQLite кладёт рядом с БД
    # outbox критичных событий: как часто добивать неопубликованные, сек
    outbox_dispatch_interval: int = 60
    outbox_max_attempts: int = 10  # после стольких неудач событие оставляется в покое

    # --- AI (Groq: OpenAI-совместимый API) ---
    groq_api_key: str = ""
    ai_model: str = "llama-3.3-70b-versatile"
    # надёжность: ретраи, резервная модель, circuit breaker (ТЗ 8.2–8.3)
    ai_fallback_model: str = "llama-3.1-8b-instant"  # пусто = без фолбэка
    ai_retry_attempts: int = 3
    ai_retry_base_delay: float = 1.0
    ai_cb_failure_threshold: int = 5
    ai_cb_timeout_seconds: int = 60
    ai_temperature: float = 0.8
    ai_max_tokens: int = 400
    ai_max_concurrent: int = 2  # семафор AIQueue (бесплатный тариф Groq)
    ai_request_timeout: int = 60
    ai_context_messages: int = 25  # сколько последних сообщений канала идёт в промпт
    ai_notes_update_every: int = 10  # обновлять заметку о пользователе каждые N очков
    # память о разговорах: сессия диалога и резюме
    ai_dialog_gap_minutes: int = 30  # пауза, после которой диалог считается законченным
    ai_dialog_min_exchanges: int = 3  # минимум обменов для резюме диалога
    ai_deep_dialog_exchanges: int = 5  # с какого числа обменов диалог считается «долгим»
    ai_dialog_summary_keep: int = 5  # сколько резюме хранить на человека
    ai_event_comment_chance: float = 0.12
    ai_event_comment_cooldown: int = 900
    # пассивное вклинивание в чужие разговоры (Попося сама решает встрять):
    # включено; консервативно по умолчанию (порог уверенности, кулдаун, только
    # главный канал), отключается пер-сервер через /config при желании
    ai_passive_enabled: bool = True
    ai_passive_only_main_channel: bool = True  # только главный канал
    ai_passive_min_users: int = 2  # минимум разных людей в окне
    ai_passive_max_messages: int = 20  # сколько сообщений брать в контекст
    ai_passive_debounce_seconds: int = 45  # пауза в разговоре до решения
    ai_passive_cooldown_minutes: int = 12  # не чаще раза в N минут на канал
    ai_passive_confidence_min: float = 0.7  # порог уверенности «встрять»
    ai_prompt_path: str = "src/infrastructure/ai/prompts/poposya_v1.md"
    ai_chime_prompt_path: str = "src/infrastructure/ai/prompts/poposya_chime_decision_v1.md"
    # реплик в час на пользователя по уровню отношений
    ai_rate_limits_by_level: dict[int, int] = {
        1: 5,
        2: 10,
        3: 20,
        4: 40,
        5: 60,
        6: 120,
        7: 240,
    }

    # --- relationship (очки и роли) ---
    relationship_daily_point_cap: int = 20
    # мягкое угасание очков при неактивности
    relationship_decay_after_days: int = 30  # дней тишины до начала угасания
    relationship_decay_every_days: int = 3  # раз в сколько дней списывать
    relationship_decay_points: int = 1  # сколько очков списывать
    relationship_role_thresholds: list[int] = [100, 250, 450, 700, 950, 1200]
    relationship_exclusive_threshold: int = 1250
    relationship_absence_days: int = 30
    relationship_notes_max_chars: int = 700
    relationship_role_names: list[str] = [
        "☕ Случайный прохожий",
        "🌧 Знакомый силуэт",
        "🎨 Занятный собеседник",
        "🎧 На одной волне",
        "🍷 Вечерняя компания",
        "🖤 Особенный",
        "✂️👁🖤 Единственный",
    ]
    # роли, автоматически выдаваемые новичку при входе (id ролей; пусто = выкл).
    # Настраивается пер-сервер через панель; глобального смысла у дефолта нет.
    autorole_ids: list[int] = []
    # роль-«ступень 0»: держат все ниже первого порога (0–99 очков), снимается
    # при взятии первой статус-роли и возвращается при угасании. Имя роли; пусто
    # = функция выключена. Пер-сервер, глобального смысла у дефолта нет.
    relationship_newcomer_role: str = ""

    # --- антиспам ---
    spam_limit: int = 5  # сколько сообщений за окно = спам
    spam_window: int = 10  # окно отслеживания, секунды
    spam_mute_minutes: int = 2  # длительность мута за спам

    # --- каналы ---
    welcome_channel: str = "bots"  # канал приветствий/прощаний (по названию)
    main_channel: str = "основной"  # главный канал: активность, случайные реплики
    log_channel: int = 0  # ID канала логов модерации (0 = отключено)

    # --- анкета знакомства (/introduce) ---
    survey_bonus_points: int = 5  # разовый бонус очков за заполнение
    survey_interest_options: list[str] = [
        "Игры",
        "Аниме",
        "Музыка",
        "Арт",
        "Код",
        "Спорт",
        "Кино",
    ]

    # --- дни рождения и праздники ---
    birthday_remind_days: int = 3  # за сколько дней напоминать о ДР
    holiday_points_multiplier: int = 2  # множитель очков в праздники
    holidays: dict[str, str] = {
        "01-01": "Новый год",
        "14-02": "День святого Валентина",
        "02-06": "День рождения Попоси",  # 2 июня — её собственный праздник
        "31-10": "Хэллоуин",
        "25-12": "Рождество",
    }

    # --- доставка сообщений (/send) ---
    send_per_hour: int = 5  # лимит отправок на пользователя в час

    # --- секретная комната ---
    secret_room_min_level: int = 5  # уровень отношений для ключа и входа
    secret_room_hours: int = 12  # время жизни комнаты
    secret_room_text_name: str = "🖤-тайная-комната"
    secret_room_voice_name: str = "🖤 Тайная комната"

    # --- альбом Попоси (starboard) ---
    album_channel: str = "альбом-попоси"  # канал-альбом (по названию)
    album_reaction_threshold: int = 5  # реакций для попадания в альбом
    album_reaction_emoji: str = ""  # конкретное эмодзи; пусто = любое

    # --- модерация ---
    warn_threshold: int = 3  # варнов до мута
    warn_mute_minutes: int = 120  # длительность мута при накоплении варнов

    auto_role: str = ""  # роль новичку при входе (название; пусто = выключено)

    # --- «остаться или уйти»: ЛС новичку с кнопками (по умолчанию выкл) ---
    staykick_enabled: bool = False
    staykick_hours: int = 12  # через сколько часов авто-кик, если выбрал «уйти»
    staykick_remind_before_minutes: int = 60  # за сколько до кика напомнить

    # --- временные голосовые каналы («каморки») ---
    # хаб — он же выключатель: вошёл в него -> получил свою каморку, 0 = фича выкл
    tempvoice_hub_channel: int = 0
    tempvoice_category: int = 0  # куда создавать; 0 = категория самого хаба
    # потолок живых каморок: у Discord жёсткий лимит 50 каналов в категории
    tempvoice_max_per_guild: int = 25
    tempvoice_default_limit: int = 0  # мест в новой каморке (0 = без лимита)
    # тумблеры модуля «Каморки» (per-server через панель «Модули»)
    tempvoice_enabled: bool = True
    tempvoice_panel: bool = True
    # тумблеры прочих модулей (мастера); staykick_enabled — рядом со staykick
    fun_enabled: bool = True
    introduce_enabled: bool = True
    secret_room_enabled: bool = True
    music_enabled: bool = True
    cinema_enabled: bool = True
    finds_enabled: bool = True
    git_enabled: bool = True
    steam_enabled: bool = True
    banwatch_enabled: bool = True
    # кросс-серверные баны: с какого числа серверов участник считается «отмеченным»
    banwatch_threshold: int = 3
    # тумблеры модуля «Модерация» (мастер + автоантиспам)
    moderation_enabled: bool = True
    moderation_antispam: bool = True
    # тумблеры модуля «AI-чат» (ai_passive_enabled выше — подфлаг «пассив»)
    ai_chat_enabled: bool = True
    ai_reactive: bool = True
    ai_event_comments: bool = True
    # тумблеры модуля «Отношения и роли» (мастер + выдача Discord-ролей)
    relationship_enabled: bool = True
    relationship_role_sync: bool = True

    # слова, которые бот считает оскорблением в свой адрес (настроение −5)
    bot_insult_words: list[str] = [
        "дурак",
        "тупая",
        "тупой",
        "идиот",
        "заткнись",
        "глупая",
        "бесишь",
        "отстой",
    ]

    # --- активность бота ---
    lonely_hours: int = 12  # часов тишины в главном канале до «скучаю»
    absent_days_threshold: int = 7  # дней отсутствия участника до «с возвращением»
    random_thought_min_hours: int = 3  # случайные реплики: минимальный интервал
    random_thought_max_hours: int = 6  # случайные реплики: максимальный интервал
    # «живой» Discord-статус: как часто менять занятие Попоси, когда нет музыки
    presence_rotate_minutes: int = 30
    # очки отношений за присутствие в голосовых каналах (0 = выключено);
    # дневной потолок общий с очками за сообщения, AFK-канал и заглушённые
    # «в наушниках» не считаются
    voice_points_per_hour: int = 3

    # --- тумблеры модуля «Активность» (per-server через панель «Модули»);
    # глобальный дефолт = вкл, сервер может выключить ---
    activity_enabled: bool = True
    activity_greetings: bool = True
    activity_return_remarks: bool = True
    activity_album: bool = True
    activity_voice_points: bool = True
    activity_holidays: bool = True
    activity_birthdays: bool = True
    activity_decay: bool = True
    activity_lonely: bool = True
    activity_random_thoughts: bool = True

    # --- ночные находки («Токийские трофеи») ---
    finds_channel: str = ""  # канал анонсов по ИМЕНИ; пусто = main_channel
    finds_channel_id: int = 0  # канал анонсов по ID (через /config); 0 = по имени выше
    finds_min_interval_hours: int = 12  # интервал между находками (случайный)
    finds_max_interval_hours: int = 48
    finds_lifetime_hours: int = 12  # сколько живёт неразобранная находка
    finds_claim_cooldown_hours: int = 8  # кулдаун походов на пользователя
    finds_fail_penalty: int = 5  # штраф очков за провал (не ниже 0)
    finds_walk_cost: int = 60  # цена «специальной прогулки»
    finds_walk_cooldown_days: int = 7

    # --- киноклуб ---
    # основной источник данных о фильмах; второй настроенный — автофолбэк
    movie_provider: str = "tmdb"  # tmdb | kinopoisk
    tmdb_api_key: str = ""  # ключ themoviedb.org (блокируется по IP в РФ)
    kinopoisk_api_key: str = ""  # токен kinopoisk.dev (работает из РФ)
    cinema_watchlist_max: int = 50  # предел вотчлиста на сервер
    cinema_poll_options: int = 5  # кандидатов в опросе киновечера
    cinema_rating_hours: int = 24  # сколько собираются оценки после просмотра
    cinema_rating_minutes: int = 0  # >0 переопределяет часы (короткое окно для тестов)
    # форум-канал «золотой фонд» (ID): после закрытия оценок бот публикует туда
    # отдельный пост по фильму со сводкой и всеми рецензиями (0 = выключено)
    cinema_forum_channel: int = 0
    cinema_rating_points: int = 2  # очков отношений за первую оценку
    cinema_utc_offset: int = 3  # часовой пояс дат киновечера (МСК = +3)

    # --- GitHub-репозитории (/git) ---
    # токен опционален: пусто = анонимно (60 запросов/ч на IP), с токеном — 5000/ч.
    # Только публичные репозитории; классический PAT без прав достаточно.
    github_token: str = ""
    github_poll_interval_minutes: int = 120  # как часто опрашивать релизы
    # форум-канал для тредов релизов (дефолт-зеркало guild-настройки; 0 = выкл)
    git_forum_channel: int = 0

    # --- Steam-игры (/steam) ---
    steam_poll_interval_minutes: int = 120  # как часто опрашивать новости игр
    # форум-канал для тредов игр (дефолт-зеркало guild-настройки; 0 = выкл)
    steam_forum_channel: int = 0

    # --- плейлисты сервера ---
    music_playlist_max_per_guild: int = 25
    music_playlist_max_tracks: int = 100

    # --- Spotify (задел под будущий API: пока работают только одиночные
    # ссылки на треки через oEmbed -> поиск на YouTube) ---
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    music_default_volume: float = 0.5
    music_playlist_limit: int = 50
    music_idle_timeout: int = 300
    music_idle_warn_seconds: int = 120  # за сколько до выхода спросить «включить ещё?»
    music_progress_interval: int = 5
    # караоке: на сколько секунд показывать строки раньше счётчика
    # (компенсация буферизации звука; больше = текст раньше)
    music_lyrics_offset: float = 1.0
    music_karaoke_ansi: bool = (
        False  # цветная подсветка (ANSI); дёргает чат и вид спорный — по умолч. выкл
    )
    music_search_limit: int = 5
    music_liked_max_per_user: int = 300  # потолок личного списка лайков

    # --- кэш аудио: следующие треки очереди скачиваются на диск заранее
    # и играют из файла — без сетевых заиканий (стрим остаётся фолбэком
    # и способом мгновенного старта первого трека); 0 = выключить ---
    music_prefetch_tracks: int = 3
    music_cache_dir: str = "data/audio_cache"  # в Docker попадает на volume
    music_cache_max_mb: int = 300  # LRU-вытеснение старых файлов

    # Обход проверки YouTube «подтвердите, что вы не бот»:
    # браузер, из которого yt-dlp возьмёт cookies (chrome/firefox/edge/…),
    # либо путь к файлу cookies в формате Netscape
    ytdlp_cookies_from_browser: str | None = None
    ytdlp_cookies_file: str | None = None

    @model_validator(mode="after")
    def _validate_web_panel_secrets(self) -> "Settings":
        # Веб-панель разворачивают тогда, когда задан DISCORD_CLIENT_ID (без него
        # /login не стартует). Раз панель поднимается — подпись сессий обязана
        # быть стойкой: пустой/короткий секрет делает JWT подделываемыми (любой
        # соберёт токен с нужным claim guilds и обойдёт require_guild_manager).
        # Поэтому падаем на старте, а не работаем дырявыми. Чисто ботовый профиль
        # (без CLIENT_ID) валидатора не касается — секрет ему не нужен.
        if self.discord_client_id:
            if len(self.web_session_secret) < _MIN_SESSION_SECRET_LEN:
                raise ValueError(
                    "WEB_SESSION_SECRET обязателен и должен быть не короче "
                    f"{_MIN_SESSION_SECRET_LEN} символов, когда включена веб-панель "
                    "(задан DISCORD_CLIENT_ID) — иначе сессии подделываемы. "
                    'Сгенерируйте: python -c "import secrets; '
                    "print(secrets.token_urlsafe(32))\""
                )
            if not self.discord_client_secret:
                raise ValueError(
                    "DISCORD_CLIENT_SECRET обязателен, когда включена веб-панель "
                    "(задан DISCORD_CLIENT_ID)."
                )
        return self
