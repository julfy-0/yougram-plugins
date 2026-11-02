#!/usr/bin/env python3
"""python pack.py <папка_плагина> [out.ygplugin]
Папка: manifest.json + icon.png + lib/arm64-v8a/libX.so (+ lib/armeabi-v7a/...)
В manifest.json: "icon": "icon.png"  (png/webp/jpg, лучше квадрат 256x256, до 512 КБ)"""
import json, os, struct, sys, zipfile

MIN_API, MAX_API = (2, 1), (2, 2)  # как в приложении: PluginApiVersion (MIN_MINOR..MINOR). Поднимай MAX_API вместе с приложением.


def api_ok(v):
    try:
        ma, mi = (int(x) for x in str(v).split("."))
    except ValueError:
        return False
    return MIN_API <= (ma, mi) <= MAX_API and ma == 2

src = os.path.abspath(sys.argv[1])
out = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(src) + ".ygplugin"

mf = os.path.join(src, "manifest.json")
assert os.path.isfile(mf), "нет manifest.json в корне"
m = json.load(open(mf, encoding="utf-8"))
assert api_ok(m.get("api")), f'manifest.json: "api" должен быть 2.1 или 2.2 (сейчас: {m.get("api")!r}). Плагины старее 2.1 приложение не установит'

icon = m.get("icon")
if not icon:
    print("ВНИМАНИЕ: в manifest.json нет поля \"icon\" — в магазине будет буква вместо иконки")
else:
    p = os.path.join(src, icon)
    assert os.path.isfile(p), f"иконка {icon} не найдена в папке плагина"
    assert icon.lower().endswith((".png", ".webp", ".jpg", ".jpeg")), "иконка: png, webp или jpg (svg приложение не покажет)"
    assert os.path.getsize(p) <= 512 * 1024, "иконка больше 512 КБ"
    if icon.lower().endswith(".png"):
        with open(p, "rb") as f:
            h = f.read(24)
        if h[:8] == b"\x89PNG\r\n\x1a\n":
            w, hh = struct.unpack(">II", h[16:24])
            if w != hh:
                print(f"ВНИМАНИЕ: иконка {w}x{hh} не квадратная, приложение обрежет края")
            if w < 128:
                print(f"ВНИМАНИЕ: иконка {w}x{hh} маловата, лучше 256x256")

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for f in sorted(files):
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, src).replace("\\", "/"))
print("OK", out, os.path.getsize(out), "bytes")
