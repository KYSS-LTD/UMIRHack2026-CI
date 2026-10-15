# Запуск «Онлайн-деканата»: сайт и (если есть токен) Telegram-бот одним процессом.
#
#   .\start.ps1             — запустить; сайт: http://127.0.0.1:8000, админка: /admin
#   .\start.ps1 -Port 8001  — на другом порту
#   .\start.ps1 -DryRun     — только показать, что будет запущено
#   .\start.ps1 -Restart    — если порт занят прежним запуском «Онлайн-деканата», остановить его и запустить заново
#
# Токен бота (его выдаёт @BotFather) кладётся в файл `.telegram-token` рядом с этим скриптом — одна строка с токеном.
# Файл в .gitignore и в репозиторий не попадает. Нет файла — запускается только сайт, бот выключен.
param(
    [int]$Port = 8000,
    [switch]$DryRun,
    [switch]$Restart
)

$root = $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$tokenFile = Join-Path $root ".telegram-token"
if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
    if ($token -match '^\d+:[\w-]{20,}$') {
        $env:DEKANAT_TELEGRAM_BOT_TOKEN = $token
        $env:DEKANAT_TELEGRAM_ENABLED = "true"
        Write-Host "Telegram-бот: включён (токен из .telegram-token)"
    } else {
        Write-Host "Telegram-бот выключен: в .telegram-token не токен бота (ожидается строка вида 123456:ABC...)" -ForegroundColor Yellow
    }
} else {
    Write-Host "Telegram-бот выключен: нет файла .telegram-token (токен выдаёт @BotFather)"
}

# порт занят? Чаще всего это прежний запуск (и сайт, и бот), который ещё не остановлен: второй бот на том же токене работать не будет
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($busy.OwningProcess)" -ErrorAction SilentlyContinue
    $ours = $proc -and ($proc.CommandLine -match "dekanat")
    if ($Restart -and $ours -and -not $DryRun) {
        Write-Host "Порт $Port занят прежним запуском (pid $($busy.OwningProcess)) — останавливаю."
        Stop-Process -Id $busy.OwningProcess -Force
        Start-Sleep -Seconds 1
    } else {
        $who = if ($ours) { "прежний запуск «Онлайн-деканата» (pid $($busy.OwningProcess))" } else { "другая программа (pid $($busy.OwningProcess))" }
        Write-Host "Порт $Port уже занят: $who." -ForegroundColor Yellow
        if ($ours) { Write-Host "Остановите его (Ctrl+C в его окне) или запустите заново с остановкой:  .\start.ps1 -Restart" -ForegroundColor Yellow }
        else { Write-Host "Выберите другой порт:  .\start.ps1 -Port 8001" -ForegroundColor Yellow }
        if (-not $DryRun) { exit 1 }
    }
}

Write-Host "Сайт: http://127.0.0.1:$Port   Админка: http://127.0.0.1:$Port/admin   Остановить: Ctrl+C"
if ($DryRun) { Write-Host "(пробный запуск: сервер не стартует)"; exit 0 }

& $python -m dekanat serve --port $Port
