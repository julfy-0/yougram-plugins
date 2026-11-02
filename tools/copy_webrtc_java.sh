#!/usr/bin/env bash
# Копирует Java-часть WebRTC (org.webrtc.*) из клона Telegram в проект Yougram.
# Берите ТОТ ЖЕ клон и коммит, из которого взяты voip/ и third_party/: нативная сборка и эти классы
# должны совпадать по версии, иначе JNI не найдёт методы.
#
# Использование: tools/copy_webrtc_java.sh /путь/к/клону/DrKLO/Telegram
set -euo pipefail

TG="${1:?Использование: tools/copy_webrtc_java.sh /путь/к/клону/Telegram}"
SRC="$TG/TMessagesProj/src/main/java/org/webrtc"
DST="$(cd "$(dirname "$0")/.." && pwd)/app/src/main/java/org/webrtc"

if [ ! -d "$SRC" ]; then
    echo "Не найдено: $SRC" >&2
    echo "Нужен клон github.com/DrKLO/Telegram (папка TMessagesProj/src/main/java/org/webrtc)." >&2
    exit 1
fi

mkdir -p "$DST"
cp -R "$SRC/." "$DST/"
echo "Скопировано .java файлов: $(find "$DST" -name '*.java' | wc -l) -> $DST"
