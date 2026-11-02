// Приём плагинов с сайта: файл уходит черновиком релиза (draft) в GitHub.
// Черновики не попадают в каталог (tools/build_index.py их пропускает), пока владелец не нажмёт Publish.
// Секрет GITHUB_TOKEN задаётся в Cloudflare (Settings -> Variables and Secrets), в репозиторий его не класть.

const MAX_SIZE = 64 * 1024 * 1024; // лимит клиента и сборщика каталога
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.ygplugin$/i;

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });

const dec = (v) => {
  try {
    return decodeURIComponent(v || "");
  } catch {
    return "";
  }
};

// Без управляющих символов; «@» ломаем, чтобы заявка не пинговала людей на GitHub.
const clean = (v, max) =>
  String(v || "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/@/g, "@\u200b")
    .trim()
    .slice(0, max);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/api/publish") return env.ASSETS.fetch(request);
    if (request.method !== "POST") return json({ error: "Метод не поддерживается" }, 405);
    if (!env.GITHUB_TOKEN || !env.REPO) return json({ error: "Сервер не настроен" }, 500);

    const name = dec(request.headers.get("x-file-name"));
    const title = clean(dec(request.headers.get("x-plugin-name")), 60);
    const description = clean(dec(request.headers.get("x-description")), 500);
    const contact = clean(dec(request.headers.get("x-contact")), 100);
    const size = Number(request.headers.get("content-length"));

    if (!NAME_RE.test(name)) return json({ error: "Нужен файл с расширением .ygplugin" }, 400);
    if (title.length < 2) return json({ error: "Укажите название плагина" }, 400);
    if (description.length < 10) return json({ error: "Добавьте описание плагина (минимум 10 символов)" }, 400);
    if (!contact) return json({ error: "Укажите контакт для связи" }, 400);
    if (!Number.isFinite(size) || size < 100) return json({ error: "Файл пустой или повреждён" }, 400);
    if (size > MAX_SIZE) return json({ error: "Файл больше 64 МБ" }, 413);
    if (!request.body) return json({ error: "Нет файла" }, 400);

    const headers = {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "yougram-plugin-submit",
      "X-GitHub-Api-Version": "2022-11-28",
    };

    const tag = `submission-${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 8)}`;
    const created = await fetch(`https://api.github.com/repos/${env.REPO}/releases`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        tag_name: tag,
        name: `Заявка: ${title}`,
        body: `Название: ${title}\nФайл: ${name}\nКонтакт: ${contact}\n\nОписание:\n${description}`,
        draft: true,
        prerelease: false,
      }),
    });
    if (!created.ok) return json({ error: "Не удалось создать заявку" }, 502);
    const release = await created.json();

    const upload = await fetch(
      `https://uploads.github.com/repos/${env.REPO}/releases/${release.id}/assets?name=${encodeURIComponent(name)}`,
      {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/octet-stream", "Content-Length": String(size) },
        body: request.body, // потоком, без загрузки файла в память
      },
    );
    if (!upload.ok) {
      await fetch(`https://api.github.com/repos/${env.REPO}/releases/${release.id}`, { method: "DELETE", headers });
      return json({ error: "Не удалось загрузить файл" }, 502);
    }
    return json({ ok: true });
  },
};
