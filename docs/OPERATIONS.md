# Эксплуатация

## Службы

```bash
systemctl status satprof-web satprof-worker satprof-wis2
systemctl status satprof-monitor.timer
journalctl -u satprof-worker -f
journalctl -u satprof-monitor.service
```

Worker и web включаются установщиком. WIS2 включается только при `sources.wis2.enabled: true`.

## Ежедневная проверка

```bash
curl -fsS http://127.0.0.1:8088/health/ready | python -m json.tool
/opt/satprof/.venv/bin/satprof-monitor --config /etc/satprof/config.yaml --deep
```

Проверьте возраст спутниковых данных, ошибки очереди, свободное место, branch/commit SatDump и наличие коэффициентов RTTOV.

## Резервное копирование

Сохраняйте:

```text
/etc/satprof/
/opt/satprof/workspace/catalog.sqlite
/opt/satprof/workspace/soundings/
/opt/satprof/workspace/satellite/
/opt/satprof/workspace/matchups/
/opt/satprof/workspace/models/
/opt/satprof/workspace/reports/
/opt/rttov/coefficients/
```

Перед копированием SQLite используйте `sqlite3 catalog.sqlite '.backup ...'` либо кратко остановите worker.

## Обновление и откат

```bash
bash scripts/update.sh
bash scripts/astra/rollback.sh
```

Конфигурация и Workspace не удаляются. Новая venv переключается только после тестов; при неуспешной readiness-проверке deploy возвращает предыдущий release.

## SatDump

```bash
bash scripts/astra/build-satdump.sh
bash scripts/astra/healthcheck.sh --deep
```

Не изменяйте `/opt/satdump/current` вручную. Сборки хранятся в `/opt/satdump/releases`, а symlink переключается после `version` smoke-test.

## Очистка

Автоматически сохраняются несколько последних release. Каталоги `satdump-output/archive` и `failed` требуют отдельной политики хранения в соответствии с объёмом исходных данных. Не удаляйте каталоги, на которые ссылается `current`.
