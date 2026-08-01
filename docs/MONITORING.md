# 🩺 Мониторинг SatProf

## Уровни проверки

### Liveness

```http
GET /health/live
```

Показывает, что FastAPI-процесс отвечает. Этот endpoint не подтверждает готовность SatDump, БД и данных.

### Readiness

```http
GET /health/ready
GET /health/ready?deep=true
```

Возвращает HTTP 200 только при отсутствии критических ошибок. `deep=true` дополнительно выполняет SQLite `quick_check` и запускает установленный SatDump с командой `version`.

### Полный отчёт

```http
GET /api/v1/monitoring
GET /api/v1/monitoring?deep=true
```

Проверяются:

- права записи и место на файловой системе;
- доступность/целостность SQLite;
- длина очереди, возраст ожидающих и выполняющихся заданий;
- свежесть зондов, гранул, коллокаций и моделей;
- source branch/commit, marker и runtime SatDump;
- наличие коэффициентов RTTOV.

Отсутствие данных на этапе ввода в эксплуатацию по умолчанию даёт `warning`, но не ломает readiness. Для строгого режима:

```yaml
monitoring:
  require_fresh_data_for_readiness: true
  require_rttov_for_readiness: true
```

## Prometheus

```http
GET /metrics
GET /api/v1/metrics
```

Основные метрики:

```text
satprof_up
satprof_ready
satprof_monitoring_check_status{check,critical}
satprof_workspace_free_bytes
satprof_catalog_objects{kind}
satprof_data_age_seconds{kind}
```

Пример scrape-конфигурации:

```yaml
scrape_configs:
  - job_name: satprof
    static_configs:
      - targets: ["satprof-host:8088"]
    metrics_path: /metrics
```

## Systemd timer

```bash
sudo systemctl enable --now satprof-monitor.timer
systemctl list-timers satprof-monitor.timer
journalctl -u satprof-monitor.service
```

Таймер выполняет глубокую проверку раз в пять минут и записывает последний отчёт атомарно:

```text
workspace/monitoring/latest.json
```

Критическая ошибка даёт ненулевой код `satprof-monitor.service`, поэтому её можно связать с `OnFailure=`, локальной системой оповещения или мониторингом состояния unit.

## CLI

```bash
/opt/satprof/.venv/bin/satprof-monitor \
  --config /etc/satprof/config.yaml \
  --deep --write-snapshot
```

Prometheus-вывод:

```bash
satprof-monitor --config /etc/satprof/config.yaml --prometheus
```

Для cron/systemd, где предупреждения допустимы:

```bash
satprof-monitor --deep --write-snapshot --allow-degraded
```

## Контроль SatDump

В deep-режиме проверяются:

1. каталог исходников;
2. ветка `release/1.2.2`;
3. optional `expected_commit`;
4. грязное Git-дерево;
5. `/opt/satdump/current/bin/satdump`;
6. resources, pipelines и `satdump_cfg.json`;
7. `.satdump-install-root`;
8. фактический запуск `version` через `scripts/astra/run.sh --prefix`.

Состояния отдельных обработок находятся в:

```text
workspace/monitoring/satdump/*.json
workspace/logs/jobs/satdump-*.log
workspace/satdump-output/.partial/
workspace/satdump-output/failed/
workspace/satdump-output/archive/
```
