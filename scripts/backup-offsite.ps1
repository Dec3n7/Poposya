<#
  Poposya — офсайт-копия БД на другой диск.

  Делает свежий pg_dump (формат -Fc, тот же, что у встроенного бэкапа бота) из
  контейнера Postgres и кладёт копию на ОТДЕЛЬНЫЙ диск. Это независимая копия
  ВНЕ системного диска: если умрёт диск C: (где живут и БД, и внутренние бэкапы
  в docker-volume) — эта копия уцелеет.

  Запуск вручную:
    powershell -ExecutionPolicy Bypass -File scripts\backup-offsite.ps1

  Планировщик: задача «Poposya DB offsite backup» уже зарегистрирована —
  ежедневно 04:30 МСК, -StartWhenAvailable (догоняет, если ПК был выключен).
  Пересоздать при переносе / убрать:
    Register-ScheduledTask -TaskName 'Poposya DB offsite backup' -Force `
      -Action  (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NonInteractive -ExecutionPolicy Bypass -File "<путь к этому файлу>"') `
      -Trigger (New-ScheduledTaskTrigger -Daily -At 4:30am) `
      -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
    Unregister-ScheduledTask -TaskName 'Poposya DB offsite backup' -Confirm:$false   # убрать

  Восстановление (ОСТОРОЖНО — перезапишет данные в БД):
    docker cp "S:\PoposyaBackups\poposya_ГГГГ-ММ-ДД_ЧЧММ.dump" poposyap-db-1:/tmp/restore.dump
    docker exec poposyap-db-1 pg_restore -U poposya -d poposya --clean --if-exists /tmp/restore.dump
    docker exec poposyap-db-1 rm -f /tmp/restore.dump
#>

$ErrorActionPreference = 'Stop'

# --- настройки -------------------------------------------------------------
$Dest      = 'S:\PoposyaBackups'   # куда складывать (другой диск)
$Keep      = 30                    # сколько последних дампов хранить
$Container = 'poposyap-db-1'       # имя контейнера Postgres
$DbUser    = 'poposya'
$DbName    = 'poposya'
# ---------------------------------------------------------------------------

$stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
$inC   = "/tmp/poposya_$stamp.dump"                 # временный путь ВНУТРИ контейнера
$final = Join-Path $Dest "poposya_$stamp.dump"
$logf  = Join-Path $Dest 'backup.log'

function Write-Log([string]$msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 's'), $msg
    Write-Host $line
    if (Test-Path -LiteralPath $Dest) {
        Add-Content -LiteralPath $logf -Value $line -Encoding utf8
    }
}

try {
    # диск на месте? (внешний/съёмный мог быть отключён)
    $root = (Split-Path -Qualifier $Dest) + '\'
    if (-not (Test-Path -LiteralPath $root)) { throw "Диск $root недоступен — бэкап пропущен" }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null

    # 1) дамп внутри контейнера (pg_dump той же версии, что сервер; -Fc + --no-owner)
    docker exec $Container pg_dump -U $DbUser -Fc --no-owner -f $inC $DbName
    if ($LASTEXITCODE -ne 0) { throw "pg_dump вернул код $LASTEXITCODE" }

    # 2) вытащить файл на диск. docker cp копирует байт-в-байт — в отличие от
    #    PowerShell-редиректа '>' , который испортил бы бинарный -Fc дамп.
    docker cp "${Container}:$inC" $final
    if ($LASTEXITCODE -ne 0) { throw "docker cp вернул код $LASTEXITCODE" }

    # 3) убрать временный файл из контейнера (не копим в /tmp)
    docker exec $Container rm -f $inC | Out-Null

    # 4) проверка: файл есть и не подозрительно мал (пустой дамп = молчаливая беда)
    $size = (Get-Item -LiteralPath $final).Length
    if ($size -lt 1024) { throw "дамп подозрительно мал: $size байт" }

    # 5) ротация: оставить последние $Keep дампов
    Get-ChildItem -LiteralPath $Dest -Filter 'poposya_*.dump' |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

    Write-Log ('OK    {0}  ({1:N2} МБ)' -f (Split-Path $final -Leaf), ($size / 1MB))
}
catch {
    # подчистить временный файл в контейнере, если дамп успел создаться
    try { docker exec $Container rm -f $inC | Out-Null } catch { }
    Write-Log "FAIL  $($_.Exception.Message)"
    exit 1
}
