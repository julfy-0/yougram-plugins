#!/usr/bin/env python3
"""python pack.py <папка_плагина> [out.ygplugin]
Папка: manifest.json + lib/arm64-v8a/libX.so (+ lib/armeabi-v7a/...)"""
import os, sys, zipfile

src = os.path.abspath(sys.argv[1])
out = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(src) + ".ygplugin"
assert os.path.isfile(os.path.join(src, "manifest.json")), "нет manifest.json в корне"

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for f in sorted(files):
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, src).replace("\\", "/"))
print("OK", out, os.path.getsize(out), "bytes")
