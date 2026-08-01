# 🛰️ SatProf 0.4

**SatProf** — автономная система внешней калибровки спутниковых радиаций по радиозондам, восстановления вертикальных профилей атмосферы и подготовки наблюдений для WRFDA.

```text
SatDump release/1.2.2 → Level‑1C TB → RTTOV → O−B
                              ↑          ↓
                     WIS2 / DWD / IGRA  поправки
                                         ↓
                         профиль T / q / RH + WRFDA
```

В состав входят worker, MQTT(S)-подписчик WIS 2.0, SQLite-каталог и веб-интерфейс FastAPI/OpenLayers.

## ✨ Возможности

- 📡 интеграция с [`f2re/SatDump`](https://github.com/f2re/SatDump/tree/release/1.2.2), строго ветка `release/1.2.2`;
- 🧪 импорт физических Level‑1C NetCDF и калиброванных стеков GeoTIFF;
- 🎈 оперативные TEMP BUFR через WIS 2.0 и DWD, история NOAA IGRA;
- 🧭 траектория шара и четырёхмерная коллокация;
- 🌡️ RTTOV/PyRTTOV, `TBрасч`, O−B и полная ошибка пары;
- 📐 робастные поправки по каналу, скану, углу, поверхности, орбите и сезону;
- 📈 статистика, контроль дрейфа и предупреждения;
- 🧠 восстановление `T(p)`, `q(p)`, `RH(p)` и каркас 1D‑Var;
- 🗺️ карта спутников, орбит, гранул и выбора точки;
- ⚙️ постоянная очередь с дедупликацией и повторными попытками;
- 📦 NetCDF-экспорт для собственного считывателя WRFDA;
- 🛡️ systemd-службы, атомарная загрузка и проверка SHA.

## ⚠️ Спутниковые данные

PNG/JPEG и оформленные композиты не используются как радиометрия. Нужны физические яркостные температуры Level‑1C с геопривязкой, временем и геометрией поля зрения либо многослойный GeoTIFF с явным описанием каналов. Обычного LRPT «Метеор‑М» недостаточно для полного МТВЗА‑ГЯ/ИКФС‑2: требуется соответствующий HRPT/X-band или официальный Level‑1C.

## 🚀 Установка

```bash
git clone --branch release/1.2.2 https://github.com/f2re/SatDump.git /opt/SatDump
cd /opt/SatDump
bash scripts/astra/install-deps.sh --profile headless --bootstrap-missing
bash scripts/astra/build.sh --profile headless --clean --install
```

```bash
git clone https://github.com/f2re/satprof.git /opt/satprof
cd /opt/satprof
bash scripts/install.sh
cp config/config.example.yaml config/config.yaml
```

Для Astra Linux:

```bash
bash scripts/install_astra.sh
```

Минимум в конфигурации:

```yaml
workspace: /opt/satprof/workspace
satdump:
  root: /opt/SatDump
  required_branch: release/1.2.2
instruments:
  mtvza_gy:
    coefficient_file: /opt/rttov/coefficients/rtcoef_meteor_2_mtvza_gy.dat
```

Проверка и запуск:

```bash
.venv/bin/satprof init --config config/config.yaml
.venv/bin/satprof satdump-validate --config config/config.yaml
.venv/bin/satprof sync-tle --config config/config.yaml
.venv/bin/satprof-worker --config config/config.yaml
.venv/bin/satprof-web --config config/config.yaml
```

Интерфейс: `http://127.0.0.1:8088`.

## 🌐 WIS 2.0

`satprof-wis2` подписывается на Global Broker по MQTT v5, разбирает WIS2 Notification Message, получает inline/HTTP-объект, проверяет SHA-2/SHA-3 и сигнатуру `BUFR`, пишет BUFR и JSON-паспорт атомарно, дедуплицирует Global Cache по `properties.data_id`, обрабатывает update/deletion и ставит `source.sync` в очередь.

```yaml
sources:
  wis2:
    enabled: true
    broker: mqtts://everyone:everyone@wis2broker.globaldata.nws.noaa.gov:8883
    topics:
      - cache/a/wis2/+/data/core/weather/surface-based-observations/temp/#
    download_dir: inbox/wis2
    verify_tls: true
    require_bufr_magic: true
```

Учётные данные можно передать через `SATPROF_WIS2_USERNAME` и `SATPROF_WIS2_PASSWORD`.

```bash
sudo systemctl enable --now satprof-wis2.service
journalctl -u satprof-wis2 -f
```

Подробно: [docs/WIS2.md](docs/WIS2.md).

## 📡 SatDump

Манифест и запись помещаются в `workspace/inbox/satdump/`. Worker выполняет:

```text
bash /opt/SatDump/scripts/astra/run.sh -- PIPELINE INPUT_LEVEL INPUT OUTPUT [OPTIONS]
```

После запуска SatProf импортирует физический продукт, сохраняет provenance и переносит манифест в `processed/`. См. [интеграцию SatDump](docs/SATDUMP_INTEGRATION.md).

## 🎈 Зондирование

| Источник | Роль |
|---|---|
| [WIS 2.0](https://community.wmo.int/en/activity-areas/wis) | основной оперативный поток |
| [DWD TEMP BUFR](https://opendata.dwd.de/weather/weather_reports/radiosonde/bufr/) | открытый резерв |
| [NOAA IGRA](https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive) | история и дозаполнение |
| [GRUAN](https://www.gruan.org/) | метрологическая валидация |
| [ЕИП Росгидромета](https://eip.meteo.ru/opendata) | региональное дополнение |

```text
профиль зонда + поверхность + геометрия → RTTOV → TBрасч
O−B = TBнабл − TBрасч
```

Ошибка одного BUFR или внешнего источника не останавливает остальные данные.

## 🗺️ API и интерфейс

OpenLayers показывает спутники, TLE-трассы, Level‑1C и выбранную точку. Панель состояния отображает очередь, модели и WIS2/DWD/IGRA.

```text
GET  /api/v1/health
GET  /api/v1/status
GET  /api/v1/sources
GET  /api/v1/satellites
GET  /api/v1/granules
POST /api/v1/profile/retrieve
GET  /api/v1/jobs
POST /api/v1/jobs
GET  /api/v1/models
```

OpenAPI: `http://host:8088/docs`.

## 🧪 Проверка

```bash
PYTHONPATH=src pytest -q
python -m compileall -q src
node --check src/satprof_calibrator/web/static/app.js
node --check src/satprof_calibrator/web/static/source-status.js
```

## 📚 Документация

- [Архитектура](docs/ARCHITECTURE.md)
- [WIS 2.0](docs/WIS2.md)
- [Источники](docs/DATA_SOURCES.md)
- [SatDump](docs/SATDUMP_INTEGRATION.md)
- [Калибровка](docs/CALIBRATION_METHOD.md)
- [Веб-интерфейс](docs/WEB_UI.md)
- [Эксплуатация](docs/OPERATIONS.md)
- [API](docs/API.md)
- [Валидация](docs/VALIDATION.md)

## 📄 Лицензия

MIT. На SatDump, RTTOV и внешние данные распространяются их собственные лицензии.
