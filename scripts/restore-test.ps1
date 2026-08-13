<#
  Poposya — тест восстановления бэкапа (disaster-recovery drill).

  «Бэкап не считается надёжным, пока не проверен restore» (v2-аудит §42). Этот
  скрипт берёт дамп, поднимает ОДНОРАЗОВЫЙ Postgres в отдельном контейнере,
  восстанавливает туда и проверяет, что схема/миграции на месте. Продакшн-БД
  НЕ трогается — всё происходит в изолированном временном контейнере, который
  сносится в конце (даже при ошибке).

  Что проверяется:
    · pg_restore проходит без фатальных ошибок;
    · есть таблицы в схеме public (БД не пустая);
    · есть alembic_version с ревизией (миграции докатились) — иначе дамп бесполезен.

  Запуск:
    # взять последний офсайт-дамп из S:\PoposyaBackups
    powershell -ExecutionPolicy Bypass -File scripts\restore-test.ps1
    # или конкретный файл
    powershell -ExecutionPolicy Bypass -File scripts\restore-test.ps1 -DumpPath "S:\PoposyaBackups\poposya_2026-08-14_0430.dump"
    # или свежий дамп прямо из живой БД (без обращения к офсайту)
    powershell -ExecutionPolicy Bypass -File scripts\restore-test.ps1 -FromLive

  Рекомендация аудита: гонять хотя бы раз в месяц (ручной DR-drill).
#>

param(
    [string]$DumpPath = '',                          # конкретный дамп; пусто = последний из $BackupDir
    [string]$BackupDir = 'S:\PoposyaBackups',        # где лежат офсайт-дампы
    [switch]$FromLive,                               # снять свежий дамп из живого контейнера
    [string]$LiveContainer = 'poposyap-db-1',        # имя контейнера продакшн-Postgres
    [string]$PgImage = 'postgres:16-alpine',         # тот же мажор, что в compose
    [string]$DbUser = 'poposya',
    [string]$DbName = 'poposya'
)

$ErrorActionPreference = 'Stop'

# одноразовый контейнер и пароль: имя с меткой времени, чтобы не столкнуться
$stamp     = Get-Date -Format 'yyyyMMdd_HHmmss'
$tmpName   = "poposya-restoretest-$stamp"
$tmpPass   = [guid]::NewGuid().ToString('N')        # эфемерный, живёт только в этом контейнере
$inCont    = '/tmp/restore.dump'                    # путь дампа ВНУТРИ временного контейнера

function Info([string]$m) { Write-Host ('  {0}' -f $m) }
function Ok([string]$m)   { Write-Host ('OK   {0}' -f $m) -ForegroundColor Green }
function Die([string]$m)  { Write-Host ('FAIL {0}' -f $m) -ForegroundColor Red; exit 1 }

# docker вообще есть?
try { docker version --format '{{.Server.Version}}' *> $null } catch { Die 'Docker недоступен (Docker Desktop запущен?)' }
if ($LASTEXITCODE -ne 0) { Die 'Docker-демон не отвечает' }

# --- 0) откуда берём дамп -----------------------------------------------------
$freshDump = $null
if ($FromLive) {
    Info "Снимаю свежий дамп из живого контейнера $LiveContainer ..."
    $freshDump = Join-Path $env:TEMP "poposya_livedump_$stamp.dump"
    docker exec $LiveContainer pg_dump -U $DbUser -Fc --no-owner -f /tmp/live.dump $DbName
    if ($LASTEXITCODE -ne 0) { Die "pg_dump из живой БД вернул код $LASTEXITCODE" }
    docker cp "${LiveContainer}:/tmp/live.dump" $freshDump
    docker exec $LiveContainer rm -f /tmp/live.dump | Out-Null
    $DumpPath = $freshDump
}
elseif (-not $DumpPath) {
    if (-not (Test-Path -LiteralPath $BackupDir)) { Die "Каталог бэкапов $BackupDir недоступен" }
    $latest = Get-ChildItem -LiteralPath $BackupDir -Filter 'poposya_*.dump' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { Die "В $BackupDir нет дампов poposya_*.dump" }
    $DumpPath = $latest.FullName
}
if (-not (Test-Path -LiteralPath $DumpPath)) { Die "Дамп не найден: $DumpPath" }
$size = (Get-Item -LiteralPath $DumpPath).Length
if ($size -lt 1024) { Die "Дамп подозрительно мал ($size байт) — вероятно битый" }
Info ('Дамп: {0} ({1:N2} МБ)' -f (Split-Path $DumpPath -Leaf), ($size / 1MB))

$failed = $false
try {
    # --- 1) одноразовый Postgres ---------------------------------------------
    Info "Поднимаю временный Postgres ($PgImage) ..."
    docker run -d --name $tmpName -e "POSTGRES_PASSWORD=$tmpPass" -e "POSTGRES_USER=$DbUser" -e "POSTGRES_DB=$DbName" $PgImage *> $null
    if ($LASTEXITCODE -ne 0) { throw "не удалось запустить временный контейнер" }

    # ждём готовности (pg_isready), максимум ~30 с
    $ready = $false
    foreach ($i in 1..30) {
        docker exec $tmpName pg_isready -U $DbUser -d $DbName *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "временный Postgres не поднялся за 30 с" }

    # --- 2) restore -----------------------------------------------------------
    Info "Восстанавливаю дамп ..."
    docker cp $DumpPath "${tmpName}:$inCont"
    if ($LASTEXITCODE -ne 0) { throw "docker cp дампа в контейнер не удался" }
    # --clean --if-exists: БД создана пустой из образа, но так restore идемпотентен.
    # stderr pg_restore не глушим как ошибку: NOTICE-и это норма, фатальный код ловим.
    docker exec $tmpName pg_restore -U $DbUser -d $DbName --no-owner --clean --if-exists $inCont
    $restoreCode = $LASTEXITCODE
    if ($restoreCode -ne 0) {
        # pg_restore отдаёт код 1 и на несмертельные warnings; проверку схемы ниже
        # считаем финальным арбитром. Но код фиксируем в отчёте.
        Info "pg_restore завершился с кодом $restoreCode (проверю схему — она арбитр)"
    }

    # --- 3) верификация схемы/миграций ---------------------------------------
    Info "Проверяю схему ..."
    $tblRaw = docker exec $tmpName psql -U $DbUser -d $DbName -tA -c `
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
    $tables = 0; [int]::TryParse(($tblRaw | Select-Object -Last 1).Trim(), [ref]$tables) | Out-Null
    if ($tables -lt 1) { throw "в public нет таблиц — восстановление пустое" }

    $rev = (docker exec $tmpName psql -U $DbUser -d $DbName -tA -c `
        "SELECT version_num FROM alembic_version LIMIT 1;" 2>$null | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or -not $rev) { throw "нет alembic_version/ревизии — миграции не восстановились" }

    Ok ("восстановление прошло: таблиц public = {0}, миграция = {1}" -f $tables, $rev.Trim())
}
catch {
    $failed = $true
    Write-Host ("FAIL {0}" -f $_.Exception.Message) -ForegroundColor Red
}
finally {
    # --- 4) снос временного контейнера (всегда) ------------------------------
    Info "Убираю временный контейнер ..."
    docker rm -f $tmpName *> $null
    if ($FromLive -and $freshDump -and (Test-Path -LiteralPath $freshDump)) {
        Remove-Item -LiteralPath $freshDump -Force -ErrorAction SilentlyContinue
    }
}

if ($failed) { exit 1 }
Write-Host 'Тест восстановления пройден.' -ForegroundColor Green
