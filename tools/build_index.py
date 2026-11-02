#!/usr/bin/env python3
"""Собирает site/index.json и site/icons/* из GitHub Releases репозитория.

Для каждого id плагина берётся самая новая версия среди опубликованных релизов
(черновики и пре-релизы пропускаются). Всё, что попадает в каталог, берётся ИЗ САМОГО ПАКЕТА
(manifest.json, иконка), а sha256 считается по скачанному файлу.

Владелец плагина: воркер сайта (tools/worker.js) ставит в текст черновика метку
<!-- yg-submit:{"uid":..,"login":..} -->. Первый опубликованный релиз с данным id определяет владельца
(релиз без метки = мейнтейнер, владельца нет). Дальше релизы с меткой чужого владельца пропускаются,
релизы без метки (руками мейнтейнера) принимаются всегда. Результат пишется в site/owners.json.

env: REPO=owner/name  GH_TOKEN (необязательно)  API_BASE, ALLOW_HTTP=1 (только для тестов)
"""
import hashlib, io, json, os, re, sys, urllib.request, zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vector_icon  # svg / Android vector xml -> безопасный svg

REPO = os.environ["REPO"]
TOKEN = os.environ.get("GH_TOKEN", "")
API = os.environ.get("API_BASE", "https://api.github.com")
ALLOW_HTTP = os.environ.get("ALLOW_HTTP") == "1"
SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")

MIN_API = (2, 1)  # как в приложении (PluginApiVersion.MIN_MINOR). Верхней границы нет: 2.3 и всё новее принимается.
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
VER_RE = re.compile(r"[A-Za-z0-9._-]{1,40}")
MAX_PKG = 64 * 1024 * 1024      # лимит клиента
MAX_ICON = 512 * 1024
MAX_FILES = 200                 # лимит сканера в приложении
SUBMIT_RE = re.compile(r"<!--\s*yg-submit:(\{.*?\})\s*-->", re.S)
MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".webp": b"RIFF"}


def load_cfg(name):
    """Список логинов из [vars] wrangler.toml (TRUSTED, ADMINS): единый источник доверия для воркера и каталога."""
    try:
        with open(os.path.join(os.path.dirname(SITE), "wrangler.toml"), encoding="utf-8") as f:
            m = re.search(rf'^{name}\s*=\s*"([^"]*)"', f.read(), re.M)
    except OSError:
        return set()
    return {x.strip().lower() for x in (m.group(1) if m else "").split(",") if x.strip()}


def warn(msg):
    print(f"::warning::{msg}")


def fail(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def get_json(url):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "yougram-index-builder"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.load(r)


def download(url, limit):
    # Без Authorization: ссылка релиза перенаправляет на подписанный адрес, заголовок ему не нужен.
    req = urllib.request.Request(url, headers={"User-Agent": "yougram-index-builder"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("файл больше лимита")
    return data


def api_ok(v):
    try:
        a, b = (int(x) for x in str(v).split("."))
    except ValueError:
        return False
    return (a, b) >= MIN_API


def vkey(v):
    k = tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", v))
    return k + (0,) * (8 - len(k))


def unix(ts):
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def read_package(data):
    """Возвращает (manifest, icon_bytes|None, icon_ext|None) или бросает ValueError."""
    z = zipfile.ZipFile(io.BytesIO(data))
    if z.testzip():
        raise ValueError("архив повреждён")
    names = {n.replace("\\", "/"): n for n in z.namelist() if not n.endswith("/")}  # приложение тоже нормализует "\"
    if len(names) > MAX_FILES:
        raise ValueError(f"в пакете больше {MAX_FILES} файлов")
    for n in names:
        if n.startswith("/") or ".." in n.split("/"):
            raise ValueError("небезопасный путь в архиве")
    if "manifest.json" not in names:
        raise ValueError("нет manifest.json в корне")
    m = json.loads(z.read(names["manifest.json"]))

    if not ID_RE.fullmatch(str(m.get("id", ""))):
        raise ValueError("недопустимый id")
    if not VER_RE.fullmatch(str(m.get("version", ""))):
        raise ValueError("недопустимая version")
    if not str(m.get("name", "")).strip() or len(m["name"]) > 120:
        raise ValueError("недопустимое имя")
    if not api_ok(m.get("api")):
        raise ValueError(f'API {m.get("api")!r} не поддерживается (нужен формат MAJOR.MINOR, 2.1 или новее)')
    entry = str(m.get("entry", "libplugin.so"))
    if not any(re.fullmatch(rf"lib/[^/]+/{re.escape(entry)}", n) for n in names):
        raise ValueError(f"в пакете нет lib/<abi>/{entry}")
    if not all(isinstance(p, str) for p in m.get("permissions", [])):
        raise ValueError("permissions должен быть списком строк")

    icon = ext = None
    name = m.get("icon")
    if name:
        norm = str(name).replace("\\", "/")
        ext = os.path.splitext(norm)[1].lower()
        if norm.startswith("/") or ".." in norm.split("/") or norm not in names:
            raise ValueError(f"иконка {name} не найдена в пакете")
        icon = z.read(names[norm])
        if ext in (".svg", ".xml"):
            icon, ext = vector_icon.sanitize(icon), ".svg"  # в каталог попадает только пересобранный чистый svg
        elif ext not in MAGIC or len(icon) > MAX_ICON or not icon.startswith(MAGIC[ext]) \
                or (ext == ".webp" and icon[8:12] != b"WEBP"):
            raise ValueError("иконка повреждена, не png/webp/jpg или больше 512 КБ")
    return m, icon, ext


def main():
    releases = get_json(f"{API}/repos/{REPO}/releases?per_page=100")
    cands = {}      # id -> [dict]
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        published = unix(rel["published_at"])
        for a in rel.get("assets", []):
            name = a["name"]
            if not name.lower().endswith(".ygplugin") or a.get("state", "uploaded") != "uploaded":
                continue
            where = f'{rel.get("tag_name")}/{name}'
            url = a["browser_download_url"]
            if not (url.startswith("https://") or (ALLOW_HTTP and url.startswith("http://"))):
                warn(f"{where}: ссылка не HTTPS, пропущено")
                continue
            if a.get("size", 0) > MAX_PKG:
                warn(f"{where}: больше 64 МБ, пропущено")
                continue
            try:
                data = download(url, MAX_PKG)
            except Exception as e:  # сеть/скачивание: не затираем каталог при сбое
                fail(f"{where}: не удалось скачать: {e}")
            sha = hashlib.sha256(data).hexdigest()
            digest = a.get("digest") or ""
            if digest.startswith("sha256:") and digest[7:] != sha:
                warn(f"{where}: sha256 не совпал с данными GitHub, пропущено")
                continue
            try:
                m, icon, ext = read_package(data)
            except (ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
                warn(f"{where}: {e}, пропущено")
                continue
            raw_body = rel.get("body") or ""
            owner, marker = None, {}
            mt = SUBMIT_RE.search(raw_body)
            if mt:
                try:
                    o = json.loads(mt.group(1))
                    if isinstance(o.get("uid"), int) and isinstance(o.get("login"), str):
                        owner = {"uid": o["uid"], "login": o["login"]}
                        marker = o
                except ValueError:
                    pass
            body = SUBMIT_RE.sub("", raw_body).strip()
            c = dict(m=m, icon=icon, ext=ext, url=url, sha=sha, size=len(data), published=published,
                     changelog=body[:2000], owner=owner, marker=marker, where=where)
            cands.setdefault(m["id"], []).append(c)

    owners, first_seen, profiles = {}, {}, {}
    for pid in list(cands):
        lst = sorted(cands[pid], key=lambda x: x["published"])
        accepted, owner, owner_set = [], None, False
        for c in lst:
            o = c["owner"]
            if not owner_set:
                owner, owner_set = o, True
            elif o is not None and (owner is None or o["uid"] != owner["uid"]):
                who = f'@{owner["login"]}' if owner else "мейнтейнеру"
                warn(f'{c["where"]}: релиз от @{o["login"]}, а плагин {pid} принадлежит {who}, пропущено')
                continue
            accepted.append(c)
        cands[pid] = accepted
        first_seen[pid] = accepted[0]["published"]
        # Данные карточки (название, категория, описание), которые автор ввёл в форме на сайте.
        # Они приходят с первым выпуском; обновления их не меняют.
        prof = {}
        for c in accepted:
            for k in ("name", "category", "desc", "long", "site"):
                v = c["marker"].get(k)
                if isinstance(v, str) and v.strip():
                    prof[k] = v.strip()
        profiles[pid] = prof
        if owner:
            owners[pid] = {"id": owner["uid"], "login": owner["login"]}

    trusted = load_cfg("TRUSTED") | load_cfg("ADMINS")
    entries, keep_icons = [], set()
    os.makedirs(f"{SITE}/icons", exist_ok=True)
    for pid, lst in cands.items():
        c = max(lst, key=lambda x: (vkey(x["m"]["version"]), x["published"]))
        m = c["m"]
        prof = profiles.get(pid, {})
        desc = (prof.get("desc") or str(m.get("description", "")))[:500]
        long_desc = prof.get("long") or str(m.get("long_description", desc))
        if prof.get("site"):
            long_desc = (long_desc + "\n\nСайт: " + prof["site"]).strip()
        e = {
            "id": pid,
            "name": (prof.get("name") or m["name"])[:120],
            "author": str(m.get("author", "Unknown"))[:120],
            "version": m["version"],
            "api": m["api"],
            "description": desc,
            "long_description": long_desc[:4000],
            "category": (prof.get("category") or str(m.get("category", "Другое")))[:40],
            "url": c["url"],
            "sha256": c["sha"],
            "size": c["size"],
            "changelog": c["changelog"],
            "permissions": m.get("permissions", []),
            "added": first_seen[pid],
            # Кто выложил (GitHub-логин владельца; релиз без метки — мейнтейнер репозитория) и доверенный ли он.
            "developer": owners[pid]["login"] if pid in owners else REPO.split("/")[0],
            "verified": pid not in owners or owners[pid]["login"].lower() in trusted,
        }
        if c["icon"]:
            rel_path = f"icons/{pid}{c['ext']}"
            with open(f"{SITE}/{rel_path}", "wb") as f:
                f.write(c["icon"])
            keep_icons.add(os.path.basename(rel_path))
            e["icon"] = rel_path
        entries.append(e)

    for f in os.listdir(f"{SITE}/icons"):  # иконки удалённых плагинов
        if f != ".gitkeep" and f not in keep_icons:
            os.remove(f"{SITE}/icons/{f}")

    entries.sort(key=lambda p: p["name"].lower())
    out = json.dumps({"plugins": entries}, ensure_ascii=False, indent=2) + "\n"
    if len(out.encode()) > 2 * 1024 * 1024:
        fail("index.json больше 2 МБ — клиент его не примет")
    tmp = f"{SITE}/index.json.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    os.replace(tmp, f"{SITE}/index.json")

    # Кто может обновлять плагин через сайт. Публичные данные (GitHub id и логин), как в самом релизе.
    own = json.dumps(dict(sorted(owners.items())), ensure_ascii=False, indent=2) + "\n"
    with open(f"{SITE}/owners.json.tmp", "w", encoding="utf-8", newline="\n") as f:
        f.write(own)
    os.replace(f"{SITE}/owners.json.tmp", f"{SITE}/owners.json")
    print(f"OK: {len(entries)} плагин(ов): " + ", ".join(f'{e["id"]} {e["version"]}' for e in entries))


main()
