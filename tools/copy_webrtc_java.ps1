# Копирует Java-часть WebRTC (org.webrtc.*) из клона Telegram в проект Yougram (Windows).
# Берите ТОТ ЖЕ клон и коммит, из которого взяты voip/ и third_party/:
# нативная сборка и эти классы должны совпадать по версии.
#
# Запуск из корня проекта:
#   powershell -ExecutionPolicy Bypass -File tools\copy_webrtc_java.ps1 -Telegram C:\path\to\Telegram
param(
    [Parameter(Mandatory = $true)][string]$Telegram
)
$ErrorActionPreference = 'Stop'

$src = Join-Path $Telegram 'TMessagesProj\src\main\java\org\webrtc'
$dst = Join-Path (Split-Path $PSScriptRoot -Parent) 'app\src\main\java\org\webrtc'

if (-not (Test-Path $src)) {
    Write-Error "Не найдено: $src (нужен клон github.com/DrKLO/Telegram)"
    exit 1
}

New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
$count = (Get-ChildItem $dst -Recurse -Filter *.java).Count
Write-Host "Скопировано .java файлов: $count -> $dst"
