> Статус: ✅ В ОСНОВНОМ РЕАЛИЗОВАНО — прогресс-бар, thumbnail/uploader/requester,
> визуализация очереди, `/lyrics` + караоке, радио, префетч аудио уже работают
> (ког `cogs/music/`). Файл — исходный UX-бэклог плеера.

---

UX/UI плеера
Теперь-playing embed сделать красивее:
Progress bar в Unicode или custom emoji.
Thumbnail + uploader + requester.
Добавлять "Added by @user" в очередь.
Queue visualization:
/queue с страницами (кнопки ← →) или select menu.
Показывать позицию и длительность каждого трека.
Remove from queue по номеру или через select.
Save current queue as playlist кнопкой прямо из плеера.
History (последние 20–50 треков) с возможностью сыграть снова.
3. Поиск и добавление
Улучшить поиск: показывать больше метаданных (views, upload date).
Автодополнение в слеш-команде (discord.py supports choices, но для динамики — можно через modal).
Поддержка SoundCloud, Bandcamp, Apple Music (через yt-dlp).
Radio / endless mode — когда очередь кончилась, автоматически добавлять похожие треки (по жанру/артисту).
Karaoke / Lyrics улучшения

Более красивое отображение (цвета, bold для текущей строки).
Авто-скролл + highlight.
Синхронизация по времени (timestamp-based).
Возможность загружать свои .lrc файлы.
0Технические полировки
Separate voice session management — лучше обрабатывать ситуации, когда бота кикнули/переместили.
Multi-guild performance — убедиться, что prefetch не жрёт слишком много CPU/диска одновременно.
Error resilience — если yt-dlp упал посреди трека — gracefully skip + сообщение.
Seek (/seek 1:23) — очень полезно.
Pause timeout — если все вышли, но трек на паузе — не выходить сразу.
Поиск и добавление
Улучшить поиск: показывать больше метаданных (views, upload date).
Автодополнение в слеш-команде (discord.py supports choices, но для динамики — можно через modal).
Поддержка SoundCloud, Bandcamp, Apple Music (через yt-dlp).
Radio / endless mode — когда очередь кончилась, автоматически добавлять похожие треки (по жанру/артисту).
Качество звука и стабильность (самое важное)
Opus bitrate / resampling control — добавить команду /quality или настройку в guild settings (например, 128/256/320 kbps).
Crossfade между треками (1–3 секунды) — очень сильно поднимает "премиум"-ощущение.
Gapless playback — минимизировать паузы между треками (уже частично есть благодаря кэшу, но можно отшлифовать).
Fallback sources: если основной формат упал — пробовать следующий из yt-dlp formats.