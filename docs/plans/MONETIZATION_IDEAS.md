# Монетизация Попоси — идеи и прайс

> Статус: **идея / черновик** (не спецификация к реализации).  
> Контекст: экосистема `Poposya P` + панель + WARDEN + legal.  
> Ориентиры рынка (2025–2026): Discord Premium обычно **$5–12 / сервер / мес**; AI часто отдельной надбавкой (MEE6 ~$12, Dyno/ProBot ~$5–10, Carl ~$8).

---

## 1. Принцип: что я оставляю бесплатным, а что делаю платным

| Тип фичи | Моя стратегия | Почему |
|---|---|---|
| **Доверие и база** (варны, мут, бан, антиспам, welcome, basic config) | **Free forever** | Иначе бота просто не ставят. Конкуренты это дают бесплатно |
| **Идентичность сервера** (базовая персона, очки, 2–3 роли) | **Free, с лимитами** | «Попробовать Попосю» — моя главная воронка |
| **Глубина / комфорт / лимиты** | **Premium** | Классика Discord-монетизации |
| **Дорогой COGS** (AI-токены, тяжёлая музыка, голос) | **Premium или AI-add-on** | Иначе я уйду в минус на активных серверах |
| **Владелец / multi-server / white-label** | **Pro / Business** | Другой покупатель (сеть серверов, бренды) |

**Что я принципиально не монетизирую:**

- апелляции как «только в Pro»
- privacy / удаление данных
- базовую модерацию
- возможность выключить бота

Это бьёт по репутации и ожиданиям ToS. Я этого делать не буду.

---

## 2. Три тарифа, которые я вижу

### Free — «зашла в гости»

Хватает маленькому серверу, чтобы **влипнуть** в персонажа.

| Включено | Лимиты |
|---|---|
| Ответы на @mention / reply | **N ответов/сутки на сервер** (напр. 30–50) или короткий контекст |
| Очки отношений + **2–3** статус-роли (не полный ladder) | Без Secret Room; «Единственный» урезан или нет |
| Музыка | очередь **5–10**; без radio / server playlists / karaoke live |
| Модерация базовая | warn / mute / kick / ban, антиспам |
| Welcome, dice / coinflip, birthday, 1 remind | — |
| Tempvoice | **да**, но **3–5** каморок max |
| Панель | **read-only** или 1–2 вкладки (settings light) |
| Persona | **1 пресет**, нельзя свою библиотеку |
| WARDEN / health | не нужно публично |

### Premium — «свой дом» (основной кэш)

То, за что реально платят админы mid-size серверов. Полная персона, музыка без боли, панель edit, community-loops.

### Pro / Network

Для сетей, больших гильдий, «свой бренд», multi-guild dashboard.

---

## 3. Что я собираюсь монетизировать (по фичам Попоси)

### A. AI и персона — мой самый сильный paywall

| Фича | Free | Premium | Pro |
|---|---|---|---|
| Ответы в характере | лимит/сутки | **высокий cap** (не юридический «безлимит») | то же + приоритет |
| Память (заметки, summary диалогов) | выкл или 1 строка | **полная** | + export / longer history |
| Своя persona library / фразы / стили | 1 дефолт | **свои шаблоны** | multi-persona / A-B |
| Mood, random thoughts, «скучаю», holiday tone | урезано | **вкл** | + fine-tune частоты |
| Secret room, full rank ladder, «Единственный» | нет / урезано | **да** | да |
| Introduce survey + бонусы | базово | **да** | да |

«Безлимит» я лучше сделаю как **высокий soft-cap** (например 500–2000 AI-реплик/мес) + fair use — иначе один сервер сожжёт мне всю маржу.

**Отдельный add-on «AI+»** (как у крупных ботов): имеет смысл, если нужно держать базовый Premium дешевле.

| Пакет | Цена (ориентир) | Что |
|---|---|---|
| AI inside Premium | проще UX | одна кнопка купить |
| Premium без AI + AI add-on | **+$4–8 / мес** | прозрачный COGS |

---

### B. Музыка — бесплатный бонус, а не paywall

Вся музыка стоит на yt-dlp + YouTube-cookies → **не продаю её отдельной строкой**
(ToS / DMCA, риск снятия верификации приложения Discord). Механику
воспроизведения оставляю **free целиком**. Premium трогает только то, что
**(а) легально чисто** и **(б) не ломает базовый UX**.

| Фича | Free | Premium |
|---|---|---|
| `/play`, `/skip`, `/queue`, `/pause`, `/resume`, `/volume`, `/shuffle`, `/remove`, `/nowplaying`, `/history`, `/leave` | **да** | да |
| **`/seek`** (перемотка) | **да** — базовый контрол, не запираю | да |
| `/lyrics` (статичный текст, lrclib.net) | **да** | да |
| **Караоке `/lyrics live:true`** (синхронная прокрутка) | нет | **да** — источник легальный, COGS≈0, «вау» |
| Лайки + `/liked` | коллекция **до 20** | **unlimited** |
| Server playlists (save / list / play) | 0–1 | **полностью** |
| 📻 Радио из лайков | нет | **да** |
| Длина очереди | 10–20 | 50–200 |
| Spotify playlist expand | нет | **да** |
| 24/7 / stay in channel | нет | **Pro** (держим соединение — реальный COGS) |

**Почему так:**

- `/seek` и все транспортные кнопки — **free**: запирать управление плеером =
  раздражать без нужды (см. §6, «лимиты, не feature missing»).
- **Караоке live** — единственная музыкальная фича не через YouTube (текст с
  lrclib.net), поэтому продавать её безопасно; статичный `/lyrics` бесплатный.
- Остальной Premium-слой — **удобство с нулевым/своим COGS** (playlists,
  unlimited likes, radio). Единственный дорогой пункт — **24/7** (постоянное
  голосовое соединение) — уводится в Pro.
- Музыка **нигде не подаётся как «причина купить Premium»** — она бонус.
  Headline-paywall остаётся на AI / отношениях / secret room / панели. Если
  YouTube завтра прикроет поток — платящие теряют бонус, а не оплаченное обещание.

---

### C. Community loops (отношения, finds, cinema) — soft premium

| Фича | Моя рекомендация |
|---|---|
| Очки + leaderboard + /rank /profile | Free (воронка) |
| Полный ladder ролей + decay + newcomer role | **Premium** |
| Secret room | **Premium** (высокая perceived value, почти 0 COGS) |
| Ночные находки, коллекция, gift, /walk | Free с редким спавном → Premium: чаще / rarer / multi |
| Киноклуб | Free basic → Premium: forum archive, AI-рецензия, больше вотчлиста |
| Achievements / карточки PNG | **Premium** (render = CPU, «вау») |
| Steam / GitHub releases в форум | **Premium** (удобство, низкий COGS) |
| Weekly digest | **Premium** |

Не обязаны быть жёстко закрыты: free с лимитами ок.  
**Secret room + full relationship + finds rate** — сильные «почему Premium».

---

### D. Модерация и trust — лимиты, не замок

| Фича | Free | Premium |
|---|---|---|
| warn / mute / ban / tempban / clear / slowmode | да | да |
| Appeals (кнопки в ЛС) | **да** (доверие) | + больше history / filters |
| Banwatch /checkuser | **1–3 проверки/день** или last 7d | **full + panel tab** |
| Mod history / case log | last 20 | full + export |
| Automod tunables | basic | full presets |
| Multi-mod role matrix in panel | нет | **да** |

**Я не буду делать:** «баны только в Premium».  
**Я буду делать:** «история, banwatch, панель, audit — в Premium».

---

### E. UX / панель / ops — B2B-вкус

| Фича | Free | Premium | Pro |
|---|---|---|---|
| Веб-панель | login + view | **edit everything** | multi-guild dashboard |
| Audit log панели | нет | **да** | + retention |
| Persona editor UI | нет | **да** | + libraries |
| WARDEN tab / pause for deploy | — | owner-only free | **hosted SLA** (если SaaS) |
| Custom bot avatar / name (white-label) | нет | — | **Pro** |
| Priority support | нет | ticket ~48h | ~24h / private |

Панель — сильный **upgrade trigger**: «настраивать в UI, а не 40 слэшей».

---

### F. Tempvoice / утилиты

| Фича | Free | Premium |
|---|---|---|
| Каморки | 3–5 | 25–50 |
| Полная панель кнопок | да | да |
| Reminds | 3 active | 50 |
| /send anonymous | 1–2 / day | full + log |
| Album (reaction → channel) | нет | **да** |

---

## 4. Сколько я планирую просить

### Global (USD, **per server / month**)

| Тариф | Monthly | Yearly (−20–30%) | Lifetime |
|---|---|---|---|
| **Free** | $0 | — | — |
| **Premium** | **$7.99–9.99** | **$69–89 / год** | лучше не надо; или $149–199 |
| **Premium + AI heavy** | **$12.99–14.99** | **$119–139 / год** | — |
| **Pro** (pack 5 гильдий / white-label) | **$24.99–39.99** | **$249–349 / год** | — |

### Стартовая точка (моя рекомендация)

| Тариф | Цена | Одной фразой |
|---|---|---|
| **Premium** | **$8.99 / мес** или **$79 / год** | Полная персона+отношения, музыка без боли, finds/cinema full, панель edit, banwatch |
| **AI Boost** (если COGS жжёт) | **+$5 / мес** | Выше cap AI, длинная память, thoughts / lonely |
| **Pro** | **$29 / мес** | 5 серверов, priority, custom branding, extended audit |

### RU / СНГ (PPP — сильно повышает конверсию)

| Тариф | ₽ / мес | ₽ / год |
|---|---|---|
| Premium | **399–599 ₽** | **3 990–5 490 ₽** |
| Premium + AI | **699–899 ₽** | **6 990–8 490 ₽** |
| Pro (сеть) | **1 990–2 990 ₽** | **19 990–24 990 ₽** |

$9 в РФ воспринимается тяжелее, чем 499 ₽. Это я учитываю.

### Сравнение с рынком

| Ориентир | Цена | Попося vs они |
|---|---|---|
| Dyno Premium | ~$5–6 | Дороже, но AI-персона |
| Carl | ~$8 | Вровень — ок для Premium |
| MEE6 Premium | ~$12 | Дешевле или вровень, если AI внутри |
| Муз. боты | $3–10 | Не конкурировать ценой музыки — **в комплекте** |

**Мои правила цены:**

- AI **внутри** Premium → цель **$9–13**
- AI **add-on** → Premium **$7–9** + AI **$5**
- ниже **$5** при COGS (LLM + voice + yt) — часто благотворительность

---

## 5. Шпаргалка: фича → тариф

| Фича | Free | Premium ~$9 | Pro ~$29 |
|---|:---:|:---:|:---:|
| Модерация core | ✅ | ✅ | ✅ |
| Appeals | ✅ | ✅ | ✅ |
| AI chat (лимит) | ✅ low | ✅ high | ✅ high |
| AI memory / summary | ❌ | ✅ | ✅ |
| Custom persona library | ❌ | ✅ | ✅ multi |
| Full relationship ladder | partial | ✅ | ✅ |
| Secret room | ❌ | ✅ | ✅ |
| Music queue long | ❌ | ✅ | ✅ |
| Radio / playlists / karaoke | ❌ | ✅ | ✅ |
| Finds full rate | low | ✅ | ✅ |
| Cinema + AI review | basic | ✅ | ✅ |
| Tempvoice many rooms | 5 | 50 | 50 |
| Banwatch full | ❌ / low | ✅ | ✅ |
| Web panel edit | ❌ | ✅ | ✅ multi-guild |
| Steam / GitHub trackers | ❌ | ✅ | ✅ |
| Digest / album / achievements | ❌ | ✅ | ✅ |
| Custom bot skin | ❌ | ❌ | ✅ |
| Priority support | ❌ | soft | ✅ |

> **Музыка** (§B): всё воспроизведение, `/seek` и статичный `/lyrics` — **free**.
> В Premium только караоке-live, unlimited likes, playlists, radio; 24/7 — Pro.
> Полная матрица всех фич — **§12**.

---

## 6. Как я собираюсь продавать, чтобы не бесили

1. **Trial 7–14 дней Premium** на сервер — лучше, чем paywall в лицо.
2. **Лимиты, не «feature missing»** где можно:  
   «ещё 3 AI-ответа сегодня · Upgrade» мягче, чем серое меню.
3. **Показывать value в продукте:**
   - `/premium` — что открыто
   - в панели — badge «Premium» на вкладках
   - CTA при упирании в cap
4. **Платит** владелец сервера / админ с Manage Guild, не каждый юзер.
5. **Не lifetime** в начале: инфляция COGS (LLM, host) убьёт маржу.
6. **Fair use** в Terms: abuse AI/music → throttle; не обещать «unlimited» юридически.

---

## 7. Экономика (COGS)

Грубо на **1 активный Premium-сервер**:

| Статья | Порядок |
|---|---|
| Hosting share | $0.05–0.30 |
| LLM (если активно общаются) | $0.50–5+ |
| Bandwidth / music cache disk | $0.10–1 — музыка «бонус», не продаётся; дорого только **24/7** → в Pro |
| Support time | главный скрытый cost |

**Мои цели:**

- Premium **$9** → COGS **<$2–3** на среднем сервере → маржа ок
- «AI безлимит на 5k мемберов» → только **Pro** или usage overage

**Overage (позже):**  
`+$1` за каждые **+500 AI-реплик** сверх пакета — честно и масштабируется.

---

## 8. Простое решение, с которого я начну

**Один платный план, без зоопарка.**

### Попося Premium — $8.99/мес · $79/год · ~499 ₽/мес

Всё, что делает сервер «её домом»:

- полная персона + память + высокий AI cap  
- полный relationship + secret room  
- музыка: radio, playlists, karaoke, liked unlimited  
- finds / cinema full  
- panel edit + banwatch + digest + steam/github  
- tempvoice max  

**Free я оставляю живым:**  
модерация, basic music, basic chat (лимит), кусок отношений, 1 persona —  
чтобы **не стыдно** жить бесплатно, но **хотелось** дом.

**Через 2–3 месяца**, если AI жрёт маржу → **AI Boost +$5**.  
**Когда 50+ платящих** → Pro multi-server.

---

## 9. Ожидания по выручке (мой реализм)

| Платящих серверов | @ $9 MRR | @ ~499 ₽ |
|---|---|---|
| 20 | ~$180 | ~10k ₽ |
| 50 | ~$450 | ~25k ₽ |
| 100 | ~$900 | ~50k ₽ |
| 300 | ~$2.7k | ~150k ₽ |

Для character-бота **50 платящих** — уже хороший год-1; **100+** — сильный indie. Я на это ориентируюсь.

---

## 10. Короткий вывод

- **Монетизирую:** AI-глубину, persona custom, full relationships / secret, панель, banwatch, finds / cinema / digests, лимиты комнат. **Музыку — не напрямую**: бонус (в Premium только караоке-live + удобства, 24/7 в Pro).  
- **Не монетизирую:** базовую модерацию, appeals, privacy.  
- **Прошу:** ~**$9/мес** ($79/год) или ~**499 ₽/мес** за Premium; **Pro ~$29** для сетей; AI overage/add-on если провайдер жжёт.  
- **Не прошу** lifetime и «unlimited AI» без soft-cap.

---

## 11. Дальше (если дойдёт до реализации) — **РЕАЛИЗОВАНО**

- [x] Текст `/premium` от лица Попоси — команда `/premium` (ког `premium.py`)  
- [x] Сообщения при упирании в лимиты (tone of voice) — гейт `require_tier` в голосе Попоси  
- [x] Модель entitlement в БД (guild_id → tier, expires_at) — таблица `guild_entitlements` (миграция 0040), `EntitlementService`  
- [ ] Платёжка (Boosty / FunPay / Stripe / Discord…) — **внешнее решение**: API выдачи готов, любой провайдер подключается вебхуком → `grant()`  
- [x] Синхронизация caps с AI circuit — `ai_rate_limits_by_level` клампится по уровням на free (music queue — намеренно нет, музыка = бонус)  
- [x] Обновить `poposya-legal` (Terms: подписка, fair use, refunds) — разделы 11–13  
- [x] Trial 14 дней + badge в панели — кнопка «Триал 14 дней» и бейдж тарифа во вкладке «Подписка»  

Детали реализации и переключатель раскатки — `Poposya P/docs/plans/monetization-prep.md`.
Ветки: `Poposya P` → `feat/monetization`; `poposya-legal` → `feat/subscription-terms`.

---

## 12. Полная матрица: все фичи бота → тариф

Легенда: ✅ полностью · ⚠️ с лимитом/урезано · ❌ нет. Premium ~$9 · Pro ~$29.
Всё, что помечено «навсегда free», не переносится в платное **никогда** (см. §13).

### Модерация и доверие (навсегда free-ядро)

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| warn / mute / kick / ban / tempban / unban | ✅ | ✅ | ✅ |
| clear / slowmode | ✅ | ✅ | ✅ |
| Антиспам (automod базовый) | ✅ | ✅ | ✅ |
| Appeals (обжалование в ЛС) | ✅ | ✅ | ✅ |
| `/rage`, `/say` | ✅ | ✅ | ✅ |
| Automod tunables (пресеты) | ⚠️ базовый | ✅ full | ✅ full |
| `/modhistory` / case log | ⚠️ last 20 | ✅ full + export | ✅ full + export |
| `/checkuser` · banwatch | ⚠️ 1–3/день или 7d | ✅ + вкладка панели | ✅ |
| Multi-mod role matrix (панель) | ❌ | ✅ | ✅ |
| `LOG_CHANNEL` / Discord-логи | ✅ | ✅ + retention | ✅ |

### AI и персона (главный paywall)

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| AI-чат (`@mention` / reply) | ⚠️ лимит/сутки | ✅ высокий cap | ✅ cap + приоритет |
| AI-память (заметки + summary диалогов) | ❌ / 1 строка | ✅ полная | ✅ + longer / export |
| Persona | ⚠️ 1 пресет | ✅ свои шаблоны | ✅ multi-persona / A-B |
| Mood / random thoughts / «скучаю» / holiday tone | ⚠️ урезано | ✅ | ✅ + тюнинг частоты |
| Комментирование треков | ⚠️ редко | ✅ | ✅ |

### Отношения / community

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| Очки · `/rank` · `/profile` · `/leaderboard` | ✅ | ✅ | ✅ |
| Роли-статусы (ladder) | ⚠️ 2–3 | ✅ полный + decay + newcomer | ✅ |
| Титул «Единственный» | ⚠️ урезано/нет | ✅ | ✅ |
| Secret room | ❌ | ✅ | ✅ |
| `/introduce` survey + бонус | ⚠️ базово | ✅ | ✅ |
| `/relationship` (admin) | ✅ | ✅ | ✅ |

### Находки · киноклуб · развлечения

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| `/finds` · `/collection` · `/gift` | ⚠️ редкий спавн | ✅ чаще / rarer / multi | ✅ |
| `/walk` | ✅ (кулдаун) | ✅ короче кулдаун | ✅ |
| Киноклуб `/movie` · `/movienight` | ⚠️ basic | ✅ forum-архив + AI-рецензия | ✅ |
| `/dice` · `/coinflip` · `/topic` · `/birthday` | ✅ | ✅ | ✅ |
| `/remind` | ⚠️ 3 active | ✅ 50 | ✅ 50 |
| `/send` (anon) | ⚠️ 1–2/день | ✅ full + log | ✅ full |
| Альбом (reaction → канал) | ❌ | ✅ | ✅ |

### Музыка (см. §B — бонус, не headline)

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| Воспроизведение + `/seek` + статичный `/lyrics` | ✅ | ✅ | ✅ |
| Караоке live | ❌ | ✅ | ✅ |
| Unlimited likes / playlists / radio / длинная очередь | ⚠️ лимит | ✅ | ✅ |
| 24/7 stay in channel | ❌ | ❌ | ✅ |

### Tempvoice · трекеры · дайджест

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| Каморки (кол-во) | ⚠️ 3–5 | ✅ 25–50 | ✅ 50 |
| Панель кнопок каморки | ✅ | ✅ | ✅ |
| GitHub `/git` · Steam `/steam` | ❌ | ✅ | ✅ |
| Weekly digest | ❌ | ✅ | ✅ |
| Achievements / PNG-карточки | ❌ | ✅ | ✅ |

### Роли (управление) · панель · ops

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| Автовыдача роли новичку | ⚠️ 1 роль | ✅ | ✅ |
| Панель ролей (create / color / reorder / perms / bulk) | ❌ view | ✅ | ✅ |
| Шаблоны ролей + export / import | ❌ | ✅ | ✅ |
| Веб-панель | ⚠️ login + view | ✅ edit everything | ✅ multi-guild |
| Audit log (Журнал) | ❌ | ✅ | ✅ + retention |
| Persona editor UI | ❌ | ✅ | ✅ + библиотеки |
| Server analytics / insights (domain `metrics`) | ❌ | ⚠️ базовый | ✅ расширенный |
| Custom bot avatar / name (white-label) | ❌ | ❌ | ✅ |
| WARDEN tab / pause for deploy | ✅ owner-only | ✅ owner-only | ✅ hosted SLA |
| Priority support | ❌ | ⚠️ soft ~48h | ✅ ~24h / private |

### Приватность (навсегда free — никогда не платно)

| Фича | Free | Premium | Pro |
|---|:---:|:---:|:---:|
| `/forgetme` (стереть свои данные) | ✅ | ✅ | ✅ |
| Автоочистка при выходе бота | ✅ | ✅ | ✅ |
| `/rules` · `/serverstats` | ✅ | ✅ | ✅ |
| Возможность выключить любой модуль | ✅ | ✅ | ✅ |

---

## 13. Ещё идеи, которые стоит обдумать

1. **À-la-carte / разовые покупки — критично для РФ.** Recurring через
   Boosty/FunPay ненадёжен (нет нормальных вебхуков/рефандов). Разовые анлоки
   ложатся на серые рельсы идеально: «Persona-пак», «boost-выходные ×2
   очки/находки», «разовый месяц Premium». Возможно, в РФ это станет **основным**
   доходом, а подписка останется для глобала / Discord-native.
2. **Подарить Premium (gifting).** Продукт эмоциональный — дай юзеру купить
   Premium **на чужой сервер** или в подарок. Закрывает разрыв «платит админ, а
   любит персонажа юзер».
3. **Personal AI Boost (per-user).** Активный юзер докупает лично себе выше cap
   AI + память, не завися от админа. Мостик между per-server и per-user моделью.
4. **Server analytics как Premium.** Есть domain `metrics` → вкладка «здоровье
   сервера» владельцу (рост, активность, отток, топ-каналы). Классический B2B-повод
   платить, свой COGS≈0.
5. **Founder-план для первых 20–30 серверов.** Не доход, а маркетинг: дешёвый/
   бесплатный Premium в обмен на отзыв/кейс — засеять доверие и воронку.
6. **Явно зафиксировать «неприкосновенное» в Terms.** Навсегда free и почему:
   core-модерация, appeals, `/forgetme`, `/checkuser` для наказанного, `/rules`,
   выключение бота, health. Это защита репутации, а не фича.
7. **Грейс при downgrade.** При истечении Premium — не резкий отбор (открытая
   secret room, активная 24/7-сессия), а мягкое завершение + грейс-период.
   Стыкуется с моделью entitlement из §11.

---

*Черновик идей. Не привязан к коду; правки — по мере решения «SaaS vs self-host vs гибрид».*