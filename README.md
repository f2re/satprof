# 🛰️ SatProf

**SatProf 0.5** — автономная система внешней калибровки спутниковых радиаций по радиозондам, восстановления вертикальных профилей атмосферы и подготовки наблюдений для WRFDA.

```text
SatDump release/1.2.2 → физический Level‑1C → RTTOV → O−B
                              ↑                ↓
                     DWD / WIS2 / IGRA     поправки и дрейф
                                               ↓
                               профиль T / q / RH + WRFDA
```

Система рассчитана на непрерывную работу без Docker на Debian и Astra Linux: отдельный worker обрабатывает данные и обучает модели, WIS2-подписчик получает оперативные TEMP, а FastAPI/OpenLayers предоставляет карту спутников, гранул и восстановленных профилей.

## ✨ Что реализовано

- 📡 интеграция с [`f2re/SatDump`](https://github.com/f2re/SatDump/tree/release/1.2.2), строго ветка `release/1.2.2`;
- 🧪 импорт физических Level‑1C NetCDF и калиброванных стеков GeoTIFF;
- 🎈 загрузка TEMP BUFR из DWD и непрерывная MQTT(S)-подписка WIS 2.0;
- 🗄️ исторические профили NOAA IGRA и адаптеры GRUAN/Росгидромета;
- 🧭 траектория шара и четырёхмерная коллокация спутник–зонд;
- 🌡️ RTTOV/PyRTTOV, расчёт `TBрасч`, O−B и полной ошибки пары;
- 📐 робастные поканальные поправки по скану, углу, поверхности, орбите и сезону;
- 📈 контроль временного дрейфа, суточная статистика и предупреждения;
- 🧠 восстановление `T(p)`, `q(p)`, `RH(p)` методом PCA + Ridge и каркас 1D‑Var;
- 🗺️ OpenLayers-карта спутников, орбит, гранул и выбора точки профиля;
- ⚙️ постоянная SQLite-очередь с дедупликацией, повторными попытками и восстановлением;
- 📦 NetCDF-экспорт скорректированных радиаций для собственного считывателя WRFDA;
- 🛡️ systemd-службы, атомарная загрузка и проверка контрольных сумм;
- 🩺 readiness/liveness, Prometheus-метрики и глубокий systemd-мониторинг;
- 🚀 версионированное развёртывание с автоматическим откатом;
- 📦 нативные офлайн-бандлы для Astra Linux 1.6/1.7.

## ⚠️ Физическое ограничение

SatProf не извлекает радиометрию из PNG/JPEG и оформленных композитов SatDump. Для калибровки необходимы:

- яркостные температуры Level‑1C с геопривязкой, временем и геометрией каждого поля зрения; либо
- многослойный GeoTIFF, где каждый слой является физическим каналом в K и описан манифестом.

Обычный LRPT «Метеор‑М» не содержит полноценный профильный поток МТВЗА‑ГЯ/ИКФС‑2. Требуется соответствующий HRPT/X-band либо официальный Level‑1C.

## 🚀 Быстрый запуск

### 1. SatDump 1.2.2

```bash
git clone --branch release/1.2.2 \
  https://github.com/f2re/SatDump.git /opt/SatDump
cd /opt/SatDump

bash scripts/astra/install-deps.sh \
  --profile headless \
  --bootstrap-missing

bash scripts/astra/build.sh \
  --profile headless \
  --clean \
  --install

bash scripts/astra/run.sh -- version
```

### 2. SatProf

```bash
git clone https://github.com/f2re/satprof.git /opt/satprof
cd /opt/satprof
bash scripts/install.sh
```

Для Astra Linux 1.6/1.7 весь цикл выполняется одной командой:

```bash
bash scripts/install_astra.sh --satdump-install-deps
```

Установщик проверяет платформу, при необходимости собирает локальный CPython 3.11, получает строго `f2re/SatDump:release/1.2.2`, собирает SatDump в versioned prefix, создаёт неизменяемый release SatProf, запускает тесты, переключает symlink и проверяет `/health/ready`. При неуспехе выполняется автоматический откат.

### 3. Настройка

```bash
cp config/config.example.yaml config/config.yaml
nano config/config.yaml
```

Минимум:

```yaml
workspace: /opt/satprof/workspace

satdump:
  root: /opt/SatDump
  required_branch: release/1.2.2
  install_prefix: /opt/satdump/current
  # expected_commit: полный SHA для жёсткого закрепления

instruments:
  mtvza_gy:
    coefficient_file: /opt/rttov/coefficients/rtcoef_meteor_2_mtvza_gy.dat
```

Проверка:

```bash
.venv/bin/satprof init --config config/config.yaml
.venv/bin/satprof satdump-validate --config config/config.yaml
.venv/bin/satprof sync-tle --config config/config.yaml
```

### 4. Запуск

```bash
.venv/bin/satprof-worker --config config/config.yaml
```

```bash
.venv/bin/satprof-web --config config/config.yaml
```

Откройте `http://127.0.0.1:8088`.

Для разработки:

```bash
bash scripts/run-dev.sh
```

## 🌐 Оперативные TEMP через WIS 2.0

SatProf содержит нативный MQTT v5-клиент WIS2. Он:

1. подписывается на один или несколько WIS2 topic;
2. разбирает WIS2 Notification Message;
3. принимает inline-content либо загружает canonical/update URL;
4. проверяет объявленную SHA-2/SHA-3 сумму;
5. проверяет сигнатуру `BUFR`;
6. сохраняет файл и JSON-паспорт атомарно;
7. дедуплицирует сообщения разных Global Cache по `properties.data_id`;
8. ставит `source.sync` в очередь;
9. обрабатывает update/deletion без накопления устаревших файлов.

Настройка:

```yaml
sources:
  wis2:
    enabled: true
    broker: mqtts://everyone:everyone@wis2broker.globaldata.nws.noaa.gov:8883
    topics:
      - cache/a/wis2/+/data/core/weather/surface-based-observations/temp/#
    download_dir: inbox/wis2
    qos: 1
    verify_tls: true
    require_bufr_magic: true
```

Пароль можно не хранить в YAML:

```bash
export SATPROF_WIS2_USERNAME=everyone
export SATPROF_WIS2_PASSWORD=everyone
```

После настройки:

```bash
sudo systemctl enable --now satprof-wis2.service
journalctl -u satprof-wis2 -f
```

Подробно: [docs/WIS2.md](docs/WIS2.md).

## 📡 Связка с SatDump

Манифест и входная запись помещаются в:

```text
workspace/inbox/satdump/
```

Пример: [`examples/satdump/meteor-m2-4-lrpt.satprof.json`](examples/satdump/meteor-m2-4-lrpt.satprof.json).

Worker выполняет SatDump через явно заданный prefix:

```text
bash /opt/SatDump/scripts/astra/run.sh \
  --prefix /opt/satdump/current -- \
  PIPELINE INPUT_LEVEL INPUT OUTPUT [OPTIONS]
```

Перед каждым запуском проверяются ветка, commit, marker установки, бинарник, ресурсы, pipelines и команда `version`. Обработка защищена файловой блокировкой, выполняется в `.partial`, успешный каталог переключается атомарно, предыдущий результат архивируется, а состояние запуска сохраняется в `workspace/monitoring/satdump/`.

Подробно: [docs/SATDUMP_INTEGRATION.md](docs/SATDUMP_INTEGRATION.md).

## 🎈 Источники зондирования

| Источник | Назначение | Состояние |
|---|---|---|
| [WIS 2.0](https://community.wmo.int/en/activity-areas/wis) | оперативный международный поток | MQTT(S), WNM, HTTP/inline, SHA, update/delete |
| [DWD Open Data](https://opendata.dwd.de/weather/weather_reports/radiosonde/bufr/) | открытый резервный TEMP BUFR | автоматическая загрузка и ecCodes |
| [NOAA IGRA](https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive) | история и дозаполнение | постанционные архивы |
| [GRUAN](https://www.gruan.org/) | метрологическая валидация | NetCDF с неопределённостями |
| [ЕИП Росгидромета](https://eip.meteo.ru/opendata) | региональное дополнение | настраиваемый CSV-адаптер |

Радиозонд сравнивается со спутником только через радиационный оператор:

```text
профиль зонда + поверхность + геометрия → RTTOV → TBрасч
O−B = TBнабл − TBрасч
```

## 🗺️ Веб-интерфейс

OpenLayers показывает:

- спутники «Метеор‑М» и «Электро‑Л»;
- траектории по TLE/SGP4;
- контуры доступных Level‑1C;
- выбранную точку и ближайшее поле зрения;
- `T`, `q`, `RH` на изобарических уровнях;
- статус worker, очереди, моделей, DWD/IGRA/WIS2;
- время последнего WIS2-сообщения и число принятых файлов.

Профиль не создаётся, если нет реальной гранулы или зарегистрированной модели. Вместо вымышленного результата API возвращает диагностический код.

## ⚙️ Фоновые процессы

| Процесс/задание | Назначение |
|---|---|
| `satprof-wis2` | постоянная MQTT(S)-подписка |
| `source.sync` | разобрать WIS2/DWD/IGRA и выполнить QC |
| `tle.sync` | обновить орбитальные элементы |
| `satdump.scan` | найти новые манифесты |
| `satdump.process` | запустить SatDump и импортировать Level‑1C |
| `instrument.refresh` | коллокация, RTTOV и O−B |
| `statistics.update` | статистика и предупреждения |
| `calibration.train` | поправки и модель профиля |

Ручной запуск:

```bash
.venv/bin/satprof enqueue source.sync --config config/config.yaml
.venv/bin/satprof enqueue satdump.scan --config config/config.yaml
.venv/bin/satprof jobs --config config/config.yaml
```

Ошибка отдельного BUFR, DWD или одной станции IGRA записывается в события и не прерывает обработку остальных источников.


## 🩺 Мониторинг

SatProf контролирует не только факт работы HTTP-процесса, но и готовность всей цепочки:

- доступность и целостность SQLite;
- свободное место и права записи Workspace;
- очередь, зависшие и ошибочные задания;
- свежесть зондов, гранул, коллокаций и моделей;
- исходники, branch/commit и установленный runtime SatDump;
- наличие коэффициентов RTTOV.

```bash
/opt/satprof/.venv/bin/satprof-monitor \
  --config /etc/satprof/config.yaml \
  --deep --write-snapshot
```

HTTP-контроль:

```bash
curl -fsS http://127.0.0.1:8088/health/live
curl -fsS http://127.0.0.1:8088/health/ready
curl -fsS http://127.0.0.1:8088/metrics
```

`satprof-monitor.timer` выполняет глубокую проверку каждые пять минут. Формат `/metrics` совместим с Prometheus text exposition.

## 🚀 Развёртывание и откат

```bash
bash scripts/astra/deploy.sh --source "$PWD"
```

Каждый релиз размещается в `/opt/satprof/.releases/<UTC>-<SHA>`. Новая venv создаётся отдельно, затем выполняются `pytest`, `compileall`, JavaScript-проверки, установка unit-файлов и атомарное переключение `/opt/satprof/current` и `/opt/satprof/.venv`. Существующая БД, Workspace и `/etc/satprof/config.yaml` не заменяются.

Ручной откат:

```bash
bash scripts/astra/rollback.sh
# либо
bash scripts/astra/rollback.sh --to 20260801T180000Z-abcdef123456
```

Обновление исходников и безопасный deploy:

```bash
bash scripts/update.sh
bash scripts/update.sh --with-satdump
```

## 📦 Офлайн-бандл Astra

Бандл следует собирать на той же линии Astra и архитектуре, где он будет установлен:

```bash
bash scripts/astra/build-offline-bundle.sh --output dist
```

В архив входят нативный Python wheelhouse, исходники SatProf, manifest, SHA256SUMS и, по умолчанию, исходники и установленный runtime SatDump. На закрытой машине:

```bash
tar -xzf satprof-offline-*.tar.gz
cd satprof-offline-*
bash install.sh
```

Подробно: [развёртывание под Astra](docs/ASTRA_DEPLOYMENT.md) и [мониторинг](docs/MONITORING.md).

## 🔌 API

```text
GET  /health/live
GET  /health/ready
GET  /metrics
GET  /api/v1/health
GET  /api/v1/status
GET  /api/v1/monitoring
GET  /api/v1/sources
GET  /api/v1/satellites
GET  /api/v1/granules
POST /api/v1/profile/retrieve
GET  /api/v1/jobs
POST /api/v1/jobs
POST /api/v1/satdump/process
GET  /api/v1/models
```

OpenAPI: `http://host:8088/docs`.

## 🧪 Проверка

```bash
PYTHONPATH=src pytest -q
python -m compileall -q src
node --check src/satprof_calibrator/web/static/app.js
node --check src/satprof_calibrator/web/static/source-status.js
node --check src/satprof_calibrator/web/static/monitoring-status.js
find scripts -name '*.sh' -print0 | xargs -0 -n1 bash -n
```

Демонстрационный цикл:

```bash
.venv/bin/satprof demo --workspace /tmp/satprof-demo --soundings 120
.venv/bin/satprof-web --workspace /tmp/satprof-demo
```

Синтетическая демонстрация проверяет программную цепочку, но не характеризует реальную точность прибора.

## 🖥️ systemd

```bash
sudo cp systemd/satprof-*.service systemd/satprof-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now satprof-worker.service satprof-web.service satprof-monitor.timer
```

После включения `sources.wis2.enabled`:

```bash
sudo systemctl enable --now satprof-wis2.service
```

Диагностика:

```bash
journalctl -u satprof-worker -f
journalctl -u satprof-wis2 -f
curl -s http://127.0.0.1:8088/api/v1/health | python -m json.tool
curl -s http://127.0.0.1:8088/api/v1/sources | python -m json.tool
curl -s http://127.0.0.1:8088/api/v1/monitoring | python -m json.tool
systemctl list-timers satprof-monitor.timer
```

## 📚 Документация

- [Архитектура](docs/ARCHITECTURE.md)
- [WIS 2.0](docs/WIS2.md)
- [Источники данных](docs/DATA_SOURCES.md)
- [Интеграция SatDump](docs/SATDUMP_INTEGRATION.md)
- [Методика калибровки](docs/CALIBRATION_METHOD.md)
- [Веб-интерфейс](docs/WEB_UI.md)
- [Эксплуатация](docs/OPERATIONS.md)
- [Astra Linux: сборка и развёртывание](docs/ASTRA_DEPLOYMENT.md)
- [Мониторинг](docs/MONITORING.md)
- [API](docs/API.md)
- [Валидация](docs/VALIDATION.md)

## 🧰 Полезные ссылки

- [SatDump release/1.2.2](https://github.com/f2re/SatDump/tree/release/1.2.2)
- [WIS 2.0](https://community.wmo.int/en/activity-areas/wis)
- [WIS2 Notification Message](https://wmo-im.github.io/wis2-notification-message/)
- [OpenLayers](https://openlayers.org/)
- [RTTOV / NWP SAF](https://nwp-saf.eumetsat.int/site/software/rttov/)
- [NWP SAF 1D‑Var](https://nwp-saf.eumetsat.int/site/software/1d-var/)
- [WRF/WRFDA](https://www2.mmm.ucar.edu/wrf/users/)
- [NOAA IGRA](https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive)
- [GRUAN](https://www.gruan.org/)

## 📄 Лицензия

MIT. На SatDump, RTTOV и внешние данные распространяются их собственные лицензии и условия использования.
