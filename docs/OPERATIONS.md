# Эксплуатация

Запускайте один `satprof-web` и один `satprof-worker` на Workspace. Наблюдение: `journalctl -u satprof-worker -f`, `/api/v1/health`, очередь `satprof jobs` и события SQLite.

Для резервной копии сохраняйте `catalog.sqlite`, `soundings/`, `satellite/`, `matchups/`, `models/`, `reports/` и конфигурацию/коэффициенты RTTOV. Обновление: `bash scripts/update.sh`; Workspace не удаляется.

Перед production накопите несколько недель Level‑1C и зондов, проверьте O−B по всем режимам и подтвердите поправки на независимом периоде.
