# YouGram plugin store

Статический каталог плагинов. Деплой: Cloudflare Pages, папка `site/`.

## Выпуск плагина
1. Подними `version` в `manifest.json` плагина
2. `python pack.py <папка_плагина> <id>.ygplugin`
3. `git pull --rebase`
4. `python publish.py <id>.ygplugin --category Утилиты --changelog "что нового"`
5. Ветка + Pull Request → ревью → merge в `main` → сайт обновится сам

Плагин содержит нативный код и выполняется у всех пользователей.
Merge в `main` только после ревью.
