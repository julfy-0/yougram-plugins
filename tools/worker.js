// YouGram Plugins: сайт + приём плагинов с входом через GitHub.
//
// Маршруты:
//   GET  /api/auth/login     -> редирект на GitHub OAuth
//   GET  /api/auth/callback  -> обмен кода на профиль, выдача подписанной cookie-сессии
//   POST /api/auth/logout    -> выход
//   GET  /api/me             -> {user, admin, owned: [id плагинов, которые можно обновлять]}
//   POST /api/publish        -> новый плагин или обновление; файл уходит ЧЕРНОВИКОМ релиза в GitHub
// Всё остальное отдаёт статика из site/ (binding ASSETS).
//
// Секреты/переменные (Cloudflare -> Settings -> Variables and Secrets, в репозиторий не класть):
//   GITHUB_TOKEN     секрет: токен с правом создавать релизы в REPO (Contents: write)
//   GH_CLIENT_SECRET секрет: Client secret из GitHub OAuth App
//   SESSION_SECRET   секрет: любая длинная случайная строка (подпись cookie)
//   GH_CLIENT_ID     переменная: Client ID из GitHub OAuth App
//   REPO             переменная: owner/name репозитория каталога
//   ADMINS           переменная: GitHub-логины через запятую (могут обновлять любые плагины)
//
// Черновики не попадают в каталог (tools/build_index.py их пропускает), пока владелец не нажмёт Publish.
// Владельца плагина определяет build_index.py по метке в тексте релиза, её ставит только этот воркер.

const MAX_SIZE = 32 * 1024 * 1024; // воркер держит файл в памяти (лимит 128 МБ), поэтому ниже, чем 64 МБ приложения
const MAX_FILES = 200;
const MAX_MANIFEST = 256 * 1024;
const SESSION_DAYS = 30;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/;
const VER_RE = /^[A-Za-z0-9._-]{1,40}$/;
const UA = "yougram-plugin-store";

const enc = new TextEncoder();

const json = (obj, status = 200, extra) => {
  const h = new Headers({ "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  if (extra) for (const [k, v] of extra) h.append(k, v);
  return new Response(JSON.stringify(obj), { status, headers: h });
};

const redirect = (to, cookies = []) => {
  const h = new Headers({ Location: to, "cache-control": "no-store" });
  for (const c of cookies) h.append("Set-Cookie", c);
  return new Response(null, { status: 302, headers: h });
};

// ---------- cookie-сессия: base64url(json).base64url(HMAC-SHA256) ----------

const b64u = {
  enc(bytes) {
    let s = "";
    for (const b of bytes) s += String.fromCharCode(b);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  },
  dec(str) {
    const s = str.replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(s + "=".repeat((4 - (s.length % 4)) % 4)), (c) => c.charCodeAt(0));
  },
};

const hmacKey = (secret) =>
  crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);

async function signSession(env, payload) {
  const p = b64u.enc(enc.encode(JSON.stringify(payload)));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(env.SESSION_SECRET), enc.encode(p));
  return `${p}.${b64u.enc(new Uint8Array(sig))}`;
}

function getCookie(request, name) {
  for (const part of (request.headers.get("cookie") || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return "";
}

const setCookie = (name, value, maxAge) =>
  `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;

async function readSession(request, env) {
  if (!env.SESSION_SECRET) return null;
  const raw = getCookie(request, "yg_session");
  const [p, sig] = raw.split(".");
  if (!p || !sig) return null;
  try {
    const ok = await crypto.subtle.verify("HMAC", await hmacKey(env.SESSION_SECRET), b64u.dec(sig), enc.encode(p));
    if (!ok) return null;
    const s = JSON.parse(new TextDecoder().decode(b64u.dec(p)));
    if (!s || !Number.isInteger(s.uid) || typeof s.login !== "string" || !(s.exp > Date.now() / 1000)) return null;
    return s;
  } catch {
    return null;
  }
}

const isAdmin = (env, user) =>
  (env.ADMINS || "")
    .split(",")
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean)
    .includes(user.login.toLowerCase());

// ---------- вход через GitHub ----------

function authConfigured(env) {
  return Boolean(env.GH_CLIENT_ID && env.GH_CLIENT_SECRET && env.SESSION_SECRET);
}

function login(url, env) {
  if (!authConfigured(env)) return json({ error: "Вход через GitHub не настроен" }, 500);
  const state = b64u.enc(crypto.getRandomValues(new Uint8Array(24)));
  const q = new URLSearchParams({
    client_id: env.GH_CLIENT_ID,
    redirect_uri: `${url.origin}/api/auth/callback`,
    state,
  });
  return redirect(`https://github.com/login/oauth/authorize?${q}`, [setCookie("yg_state", state, 600)]);
}

async function callback(request, url, env, ctx) {
  const clearState = setCookie("yg_state", "", 0);
  const fail = () => redirect("/?auth=error", [clearState]);
  if (!authConfigured(env)) return fail();

  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const saved = getCookie(request, "yg_state");
  if (!code || !state || !saved || state !== saved) return fail();

  const tokRes = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "User-Agent": UA },
    body: JSON.stringify({
      client_id: env.GH_CLIENT_ID,
      client_secret: env.GH_CLIENT_SECRET,
      code,
      redirect_uri: `${url.origin}/api/auth/callback`,
    }),
  }).catch(() => null);
  const tok = tokRes && (await tokRes.json().catch(() => ({})));
  if (!tok || !tok.access_token) return fail();

  const meRes = await fetch("https://api.github.com/user", {
    headers: { Authorization: `Bearer ${tok.access_token}`, Accept: "application/vnd.github+json", "User-Agent": UA },
  }).catch(() => null);
  const me = meRes && meRes.ok ? await meRes.json().catch(() => null) : null;

  // Токен нужен был только чтобы узнать, кто пришёл. Отзываем его, чтобы он нигде не лежал.
  const revoke = fetch(`https://api.github.com/applications/${env.GH_CLIENT_ID}/token`, {
    method: "DELETE",
    headers: {
      Authorization: `Basic ${btoa(`${env.GH_CLIENT_ID}:${env.GH_CLIENT_SECRET}`)}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": UA,
    },
    body: JSON.stringify({ access_token: tok.access_token }),
  }).catch(() => {});
  if (ctx && ctx.waitUntil) ctx.waitUntil(revoke);

  if (!me || !Number.isInteger(me.id) || typeof me.login !== "string") return fail();
  const exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400;
  const session = await signSession(env, { uid: me.id, login: me.login, avatar: String(me.avatar_url || ""), exp });
  return redirect("/", [setCookie("yg_session", session, SESSION_DAYS * 86400), clearState]);
}

// ---------- каталог (из статики сайта) ----------

async function catalog(env, request) {
  const get = async (path) => {
    const r = await env.ASSETS.fetch(new Request(new URL(path, request.url)));
    return r.ok ? r.json().catch(() => null) : null;
  };
  const index = await get("/index.json");
  const owners = (await get("/owners.json")) || {};
  return { plugins: index && Array.isArray(index.plugins) ? index.plugins : null, owners };
}

const canUpdate = (env, user, owners, id) => isAdmin(env, user) || (owners[id] && owners[id].id === user.uid);

async function me(request, env) {
  const user = await readSession(request, env);
  if (!user) return json({ user: null });
  const { plugins, owners } = await catalog(env, request);
  const owned = (plugins || []).filter((p) => canUpdate(env, user, owners, p.id)).map((p) => p.id);
  return json({ user: { login: user.login, avatar: user.avatar }, admin: isAdmin(env, user), owned });
}

// ---------- чтение manifest.json из zip без распаковки всего файла ----------

async function readZip(buf) {
  const u8 = new Uint8Array(buf);
  const v = new DataView(buf);
  const bad = () => new Error("Файл не похож на пакет .ygplugin (повреждённый zip)");
  let eocd = -1;
  for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 22 - 65535); i--) {
    if (v.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw bad();
  const total = v.getUint16(eocd + 10, true);
  const cdSize = v.getUint32(eocd + 12, true);
  const cdOff = v.getUint32(eocd + 16, true);
  if (total === 0xffff || cdOff === 0xffffffff) throw new Error("Zip64 не поддерживается");
  if (cdOff + cdSize > eocd) throw bad();

  const dec = new TextDecoder();
  const files = new Map();
  let p = cdOff;
  for (let i = 0; i < total; i++) {
    if (p + 46 > buf.byteLength || v.getUint32(p, true) !== 0x02014b50) throw bad();
    const nl = v.getUint16(p + 28, true);
    const el = v.getUint16(p + 30, true);
    const cl = v.getUint16(p + 32, true);
    const name = dec.decode(u8.subarray(p + 46, p + 46 + nl)).replace(/\\/g, "/");
    if (!name.endsWith("/")) {
      files.set(name, {
        flags: v.getUint16(p + 8, true),
        method: v.getUint16(p + 10, true),
        csize: v.getUint32(p + 20, true),
        usize: v.getUint32(p + 24, true),
        lho: v.getUint32(p + 42, true),
      });
    }
    p += 46 + nl + el + cl;
  }

  async function read(name, max) {
    const f = files.get(name);
    if (!f) throw new Error(`В пакете нет ${name}`);
    if (f.flags & 1) throw new Error(`${name} зашифрован`);
    if (f.usize > max || f.csize > max) throw new Error(`${name} слишком большой`);
    const o = f.lho;
    if (o + 30 > buf.byteLength || v.getUint32(o, true) !== 0x04034b50) throw bad();
    const start = o + 30 + v.getUint16(o + 26, true) + v.getUint16(o + 28, true);
    if (start + f.csize > buf.byteLength) throw bad();
    const raw = u8.subarray(start, start + f.csize);
    if (f.method === 0) return raw;
    if (f.method !== 8) throw new Error("Неподдерживаемое сжатие в архиве");
    const reader = new Blob([raw]).stream().pipeThrough(new DecompressionStream("deflate-raw")).getReader();
    const chunks = [];
    let n = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      n += value.length;
      if (n > max) {
        await reader.cancel();
        throw new Error(`${name} слишком большой`);
      }
      chunks.push(value);
    }
    const out = new Uint8Array(n);
    let off = 0;
    for (const c of chunks) {
      out.set(c, off);
      off += c.length;
    }
    return out;
  }
  return { files, read };
}

// Те же проверки, что в tools/build_index.py, чтобы автор узнал об ошибке сразу, а не при одобрении.
async function readManifest(buf) {
  const zip = await readZip(buf);
  const names = [...zip.files.keys()];
  if (names.length > MAX_FILES) throw new Error(`В пакете больше ${MAX_FILES} файлов`);
  for (const n of names) {
    if (n.startsWith("/") || n.split("/").includes("..")) throw new Error("Небезопасный путь в архиве");
  }
  if (!zip.files.has("manifest.json")) throw new Error("Нет manifest.json в корне пакета");
  let m;
  try {
    m = JSON.parse(new TextDecoder().decode(await zip.read("manifest.json", MAX_MANIFEST)));
  } catch (e) {
    throw new Error(e instanceof SyntaxError ? "manifest.json не читается (неверный JSON)" : e.message);
  }
  if (!m || typeof m !== "object") throw new Error("manifest.json должен быть объектом");
  if (!ID_RE.test(String(m.id || ""))) throw new Error("В manifest.json недопустимый id");
  if (!VER_RE.test(String(m.version || ""))) throw new Error("В manifest.json недопустимая version");
  if (typeof m.name !== "string" || !m.name.trim() || m.name.length > 120) throw new Error("В manifest.json недопустимое name");
  if (!/^2\.[12]$/.test(String(m.api))) throw new Error(`API ${JSON.stringify(m.api)} не поддерживается (нужно 2.1 или 2.2)`);
  const entry = String(m.entry || "libplugin.so");
  const esc = entry.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!names.some((n) => new RegExp(`^lib/[^/]+/${esc}$`).test(n))) throw new Error(`В пакете нет lib/<abi>/${entry}`);
  return m;
}

// Сравнение версий как в build_index.py (vkey).
function vkey(v) {
  const k = String(v).split(/[.-]/).map((x) => (/^\d+$/.test(x) ? parseInt(x, 10) : 0));
  while (k.length < 8) k.push(0);
  return k;
}
function cmpVersion(a, b) {
  const x = vkey(a), y = vkey(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0) ? -1 : 1;
  }
  return 0;
}

// ---------- приём плагина ----------

const dec = (v) => {
  try {
    return decodeURIComponent(v || "");
  } catch {
    return "";
  }
};

// Без управляющих символов (кроме переводов строки); «@» ломаем, чтобы заявка не пинговала людей на GitHub.
const cleanText = (v, max) =>
  String(v || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f]/g, " ")
    .replace(/@/g, "@\u200b")
    .trim()
    .slice(0, max);

async function publish(request, url, env) {
  const user = await readSession(request, env);
  if (!user) return json({ error: "Войдите через GitHub, чтобы отправить плагин", login: true }, 401);

  // Защита от CSRF: запрос должен прийти с нашего же сайта.
  const origin = request.headers.get("origin");
  if (origin && origin !== url.origin) return json({ error: "Запрос с чужого сайта" }, 403);
  if (!env.GITHUB_TOKEN || !env.REPO) return json({ error: "Сервер не настроен" }, 500);

  const fileName = dec(request.headers.get("x-file-name"));
  const description = cleanText(dec(request.headers.get("x-description")), 1000);
  const expectedId = dec(request.headers.get("x-plugin-id"));
  const size = Number(request.headers.get("content-length"));

  if (!/\.ygplugin$/i.test(fileName)) return json({ error: "Нужен файл с расширением .ygplugin" }, 400);
  if (description.length < 10) return json({ error: "Опишите плагин или что изменилось (минимум 10 символов)" }, 400);
  if (!Number.isFinite(size) || size < 100) return json({ error: "Файл пустой или повреждён" }, 400);
  if (size > MAX_SIZE) return json({ error: "Через сайт можно отправить файл до 32 МБ" }, 413);
  if (!request.body) return json({ error: "Нет файла" }, 400);

  const buf = await request.arrayBuffer();
  if (buf.byteLength !== size) return json({ error: "Файл получен не полностью, попробуйте ещё раз" }, 400);

  let m;
  try {
    m = await readManifest(buf);
  } catch (e) {
    return json({ error: e.message }, 400);
  }
  if (expectedId && expectedId !== m.id) {
    return json({ error: `В файле плагин «${m.id}», а вы обновляете «${expectedId}»` }, 400);
  }

  const { plugins, owners } = await catalog(env, request);
  if (!plugins) return json({ error: "Каталог сейчас недоступен, попробуйте позже" }, 503);
  const existing = plugins.find((p) => p.id === m.id);
  const kind = existing ? "update" : "new";
  if (existing) {
    if (!canUpdate(env, user, owners, m.id)) {
      return json({ error: `Плагин «${m.id}» уже есть в каталоге и принадлежит другому автору` }, 403);
    }
    if (cmpVersion(m.version, existing.version) <= 0) {
      return json({ error: `Версия ${m.version} не новее текущей (${existing.version}). Поднимите version в manifest.json` }, 400);
    }
  }

  const ghHeaders = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": UA,
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const meta = JSON.stringify({ uid: user.uid, login: user.login, kind, id: m.id });
  const tag = `submission-${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 8)}`;
  const title = `${kind === "update" ? "Обновление" : "Новый плагин"}: ${m.name.trim().slice(0, 60)} ${m.version} (@${user.login})`;

  const created = await fetch(`https://api.github.com/repos/${env.REPO}/releases`, {
    method: "POST",
    headers: { ...ghHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({
      tag_name: tag,
      name: title,
      // Метку читает build_index.py: по ней определяется владелец. Остальной текст попадает в «Что нового».
      body: `<!-- yg-submit:${meta} -->\n${description}`,
      draft: true,
      prerelease: false,
    }),
  });
  if (!created.ok) return json({ error: "Не удалось создать заявку" }, 502);
  const release = await created.json();

  const assetName = `${m.id}-${m.version}.ygplugin`;
  const upload = await fetch(
    `https://uploads.github.com/repos/${env.REPO}/releases/${release.id}/assets?name=${encodeURIComponent(assetName)}`,
    { method: "POST", headers: { ...ghHeaders, "Content-Type": "application/octet-stream" }, body: buf },
  );
  if (!upload.ok) {
    await fetch(`https://api.github.com/repos/${env.REPO}/releases/${release.id}`, { method: "DELETE", headers: ghHeaders });
    return json({ error: "Не удалось загрузить файл" }, 502);
  }
  return json({ ok: true, kind, id: m.id, name: m.name, version: m.version });
}

// ---------- роутер ----------

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    if (!path.startsWith("/api/")) {
      return env.ASSETS ? env.ASSETS.fetch(request) : new Response("Not found", { status: 404 });
    }

    const method = request.method;
    if (path === "/api/auth/login" && method === "GET") return login(url, env);
    if (path === "/api/auth/callback" && method === "GET") return callback(request, url, env, ctx);
    if (path === "/api/auth/logout" && method === "POST") {
      return json({ ok: true }, 200, [["Set-Cookie", setCookie("yg_session", "", 0)]]);
    }
    if (path === "/api/me" && method === "GET") return me(request, env);
    if (path === "/api/publish") {
      if (method !== "POST") return json({ error: "Метод не поддерживается" }, 405);
      return publish(request, url, env);
    }
    return json({ error: "Не найдено" }, 404);
  },
};
