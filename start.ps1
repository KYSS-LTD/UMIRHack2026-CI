# Запуск «Онлайн-деканата»: сайт и (если есть токен) Telegram-бот одним процессом.
#
#   .\start.ps1             — запустить; сайт: http://127.0.0.1:8000, админка: /admin
#   .\start.ps1 -Port 8001  — на другом порту
#   .\start.ps1 -DryRun     — только показать, что будет запущено
#
# Токен бота (его выдаёт @BotFather) кладётся в файл `.telegram-token` рядом с этим скриптом — одна строка с токеном.
# Файл в .gitignore и в репозиторий не попадает. Нет файла — запускается только сайт, бот выключен.
param(
    [int]$Port = 8000,
    [switch]$DryRun
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

Write-Host "Сайт: http://127.0.0.1:$Port   Админка: http://127.0.0.1:$Port/admin   Остановить: Ctrl+C"
if ($DryRun) { Write-Host "(пробный запуск: сервер не стартует)"; exit 0 }

& $python -m dekanat serve --port $Port
