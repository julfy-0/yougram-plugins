#!/usr/bin/env python3
"""python publish.py my.ygplugin [--category Утилиты] [--icon icon.png]
                    [--long "..."] [--changelog "..."]"""
import argparse, hashlib, json, os, re, shutil, time, zipfile

SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
INDEX = os.path.join(SITE, "index.json")
SAFE = re.compile(r"[A-Za-z0-9._-]+")

ap = argparse.ArgumentParser()
ap.add_argument("package")
ap.add_argument("--category", default="Другое")
ap.add_argument("--icon")
ap.add_argument("--long", default="")
ap.add_argument("--changelog", default="")
a = ap.parse_args()

with zipfile.ZipFile(a.package) as z:
    if z.testzip():
        raise SystemExit("архив повреждён")
    m = json.loads(z.read("manifest.json"))

pid, ver = m["id"], m["version"]
if not SAFE.fullmatch(pid) or not SAFE.fullmatch(ver):
    raise SystemExit("недопустимый id/version")

os.makedirs(f"{SITE}/plugins", exist_ok=True)
os.makedirs(f"{SITE}/icons", exist_ok=True)
fname = f"{pid}-{ver}.ygplugin"
dst = f"{SITE}/plugins/{fname}"
shutil.copyfile(a.package, dst)

h = hashlib.sha256()
with open(dst, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)

icon = None
if a.icon:
    icon = f"icons/{pid}{os.path.splitext(a.icon)[1].lower()}"
    shutil.copyfile(a.icon, f"{SITE}/{icon}")

data = {"plugins": []}
if os.path.exists(INDEX):
    with open(INDEX, encoding="utf-8") as f:
        data = json.load(f)

old = next((p for p in data["plugins"] if p["id"] == pid), {})
entry = {
    "id": pid,
    "name": m["name"],
    "author": m.get("author", "Unknown"),
    "version": ver,
    "description": m.get("description", ""),
    "long_description": a.long or old.get("long_description", m.get("description", "")),
    "category": a.category if a.category != "Другое" else old.get("category", "Другое"),
    "url": f"plugins/{fname}",
    "sha256": h.hexdigest(),
    "size": os.path.getsize(dst),
    "changelog": a.changelog or old.get("changelog", ""),
    "permissions": m.get("permissions", []),
    "added": old.get("added", int(time.time())),
}
if icon or old.get("icon"):
    entry["icon"] = icon or old["icon"]

data["plugins"] = [p for p in data["plugins"] if p["id"] != pid] + [entry]
data["plugins"].sort(key=lambda p: p["name"].lower())

tmp = INDEX + ".tmp"
with open(tmp, "w", encoding="utf-8", newline="\n") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, INDEX)
print(f"OK {pid} {ver} sha256={entry['sha256'][:12]}... size={entry['size']}")
