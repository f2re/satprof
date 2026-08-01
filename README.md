# 🛰️ SatProf

**SatProf** — автономный конвейер внешней калибровки спутниковых радиаций по радиозондам, восстановления вертикальных профилей атмосферы и подготовки наблюдений для WRFDA.

Проект связывает:

```text
SatDump release/1.2.2 → Level‑1C TB → радиозонды → RTTOV → O−B
                                      ↓
                    поправки, статистика, контроль дрейфа
                                      ↓
                   профиль T/q/RH + экспорт для WRFDA
```

В состав входят фоновый рабочий процесс, постоянная очередь заданий, веб-интерфейс с картой OpenLayers, каталог SQLite и воспроизводимые модели калибровки.

## ✨ Возможности

- 📡 запуск локального SatDump из ветки `release/1.2.2` по JSON-манифестам;
- 🧪 импорт количественных Level‑1C NetCDF и откалиброванных стеков GeoTIFF;
- 🎈 автоматическая загрузка TEMP BUFR DWD и исторических данных IGRA;
- 🧭 учёт траектории радиозонда и четырёхмерная коллокация;
- 🌡️ моделирование каналов по профилю через RTTOV/PyRTTOV;
- 📐 робастные поканальные поправки, зависимости от скана, угла, поверхности, орбиты и сезона;
- 📈 суточная статистика O−B, контроль дрейфа и журнал предупреждений;
- 🧠 восстановление `T(p)`, `q(p)` и `RH(p)` методом PCA + Ridge;
- 🗺️ карта спутников, орбит, полос наблюдения и выбор точки профиля;
- ⚙️ фоновая очередь: загрузка, SatDump, коллокация, калибровка и обучение;
- 📦 NetCDF-экспорт радиаций для собственного считывателя WRFDA;
- 🛡️ systemd-службы и установка без Docker.

## ⚠️ Научное ограничение

SatProf **не использует оформленные PNG SatDump как источник радиометрии**. Для калибровки нужны физические яркостные температуры:

- Level‑1C NetCDF с геопривязкой, временем и геометрией каждого поля зрения; либо
- стек GeoTIFF, где каждый слой является откалиброванным каналом в кельвинах и описан манифестом.

Обычный LRPT-поток «Метеор‑М» не содержит полный набор профильных каналов МТВЗА‑ГЯ/ИКФС‑2. Для реальной профильной системы требуется соответствующий HRPT/X-band или официальный Level‑1C.

## 🚀 Быстрый запуск

### 1. SatDump 1.2.2

```bash
git clone --branch release/1.2.2 https://github.com/f2re/SatDump.git /opt/SatDump
cd /opt/SatDump
bash scripts/astra/install-deps.sh --profile headless --bootstrap-missing
bash scripts/astra/build.sh --profile headless --clean --install
bash scripts/astra/run.sh -- version
```

Используется именно форк: [f2re/SatDump, ветка release/1.2.2](https://github.com/f2re/SatDump/tree/release/1.2.2).

### 2. SatProf

```bash
git clone https://github.com/f2re/satprof.git /opt/satprof
cd /opt/satprof
bash scripts/install.sh
cp config/config.example.yaml config/config.yaml
```

Отредактируйте минимум:

```yaml
workspace: /opt/satprof/workspace
satdump:
  root: /opt/SatDump
  required_branch: release/1.2.2

instruments:
  mtvza_gy:
    coefficient_file: /opt/rttov/.../rtcoef_meteor_2_mtvza_gy.dat
```

### 3. Проверка

```bash
.venv/bin/satprof init --config config/config.yaml
.venv/bin/satprof satdump-validate --config config/config.yaml
.venv/bin/satprof sync-tle --config config/config.yaml
```

### 4. Запуск

В двух терминалах:

```bash
.venv/bin/satprof-worker --config config/config.yaml
```

```bash
.venv/bin/satprof-web --config config/config.yaml
```

Откройте `http://127.0.0.1:8088`.

Для разработки можно использовать:

```bash
bash scripts/run-dev.sh
```

## 🗺️ Веб-интерфейс

Карта OpenLayers отображает:

- текущие положения «Метеор‑М» и «Электро‑Л» по TLE;
- рассчитанные участки орбиты;
- контуры доступных Level‑1C гранул;
- выбранную пользователем точку;
- ближайшее спутниковое поле зрения и расстояние до него.

При нажатии на карту сервер:

1. выбирает ближайшую пригодную гранулу выбранного прибора;
2. ищет ближайшее поле зрения;
3. проверяет максимальное расстояние;
4. загружает последнюю `production`/`candidate` модель восстановления;
5. формирует признаки из каналов, скана и геометрии;
6. возвращает профиль температуры, удельной и относительной влажности.

Профиль не создаётся, если нет реальных гранул или обученной модели.

## 📡 Связка с SatDump

Положите запись и манифест в:

```text
workspace/inbox/satdump/
```

Пример: [`examples/satdump/meteor-m2-4-lrpt.satprof.json`](examples/satdump/meteor-m2-4-lrpt.satprof.json).

Рабочий процесс выполняет команду ветки 1.2.2:

```text
bash /opt/SatDump/scripts/astra/run.sh -- \
  <pipeline> <input_level> <input_file> <output_dir> [параметры]
```

После успешной обработки SatProf ищет научный продукт по разделу `reader` манифеста, импортирует его, сохраняет provenance и переносит манифест в `processed/`.

Подробно: [docs/SATDUMP_INTEGRATION.md](docs/SATDUMP_INTEGRATION.md).

## 🎈 Источники зондирования

| Источник | Роль | Реализация |
|---|---|---|
| [DWD Open Data TEMP BUFR](https://opendata.dwd.de/weather/weather_reports/radiosonde/bufr/) | оперативный глобальный поток | загрузка и ecCodes-декодирование |
| [NOAA IGRA 2](https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive) | история и дозаполнение | постанционные файлы |
| [GRUAN](https://www.gruan.org/) | метрологическая валидация | NetCDF с неопределённостями |
| [ЕИП Росгидромета](https://eip.meteo.ru/opendata) | региональные данные | настраиваемый CSV-адаптер |
| [WIS 2.0](https://community.wmo.int/en/activity-areas/wis) | производственный оперативный поток | предусмотрен интерфейс, MQTT-подписчик — следующая стадия |

Радиозонд сравнивается со спутником только через радиационный оператор:

```text
профиль зонда + поверхность + геометрия → RTTOV → TBрасч
O−B = TBнабл − TBрасч
```

## ⚙️ Фоновые задания

| Задание | Назначение |
|---|---|
| `source.sync` | загрузить и разобрать DWD/IGRA |
| `tle.sync` | обновить TLE |
| `satdump.scan` | найти новые манифесты |
| `satdump.process` | выполнить SatDump и импортировать Level‑1C |
| `instrument.refresh` | коллокация, RTTOV и O−B |
| `statistics.update` | пересчитать статистику и тревоги |
| `calibration.train` | поправки и модель восстановления |

Периоды задаются в `worker.schedules`. Задания хранятся в SQLite, имеют дедупликацию, повторные попытки и восстановление после аварийного завершения.

Ручной запуск:

```bash
.venv/bin/satprof enqueue source.sync --config config/config.yaml
.venv/bin/satprof enqueue satdump.scan --config config/config.yaml
.venv/bin/satprof jobs --config config/config.yaml
```

## 🧪 Демонстрационный цикл

Демонстрация создаёт синтетические профили и наблюдения, затем проходит полный цикл калибровки:

```bash
.venv/bin/satprof demo --workspace /tmp/satprof-demo --soundings 120
.venv/bin/satprof-web --workspace /tmp/satprof-demo
```

Это проверка программной логики, а не характеристика реального прибора.

## 🔌 API

Основные конечные точки:

```text
GET  /api/v1/health
GET  /api/v1/status
GET  /api/v1/satellites
GET  /api/v1/granules
POST /api/v1/profile/retrieve
GET  /api/v1/jobs
POST /api/v1/jobs
POST /api/v1/satdump/process
GET  /api/v1/models
```

OpenAPI: `http://host:8088/docs`.

Подробно: [docs/API.md](docs/API.md).

## 🧱 Структура

```text
src/satprof_calibrator/
├── sources/             радиозонды
├── integrations/        SatDump
├── web/                 FastAPI + OpenLayers
├── satellite.py         импорт Level‑1C
├── orbit.py             TLE/SGP4
├── collocation.py       4D-коллокация
├── rtm.py               RTTOV
├── calibration.py       поправки и дрейф
├── retrieval.py         восстановление профиля
├── jobs.py              очередь
├── worker.py            планировщик и обработчики
└── wrfda.py             экспорт
```

Архитектура: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 🖥️ systemd

```bash
sudo useradd --system --home /opt/satprof --shell /usr/sbin/nologin satprof || true
sudo mkdir -p /etc/satprof
sudo cp systemd/satprof.env.example /etc/satprof/satprof.env
sudo cp systemd/satprof-web.service systemd/satprof-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now satprof-worker.service satprof-web.service
```

Проверьте права пользователя `satprof` на `/opt/satprof/workspace`, входные записи и запуск `/opt/SatDump/scripts/astra/run.sh`.

## 🧰 Полезные ссылки

- [SatDump fork release/1.2.2](https://github.com/f2re/SatDump/tree/release/1.2.2)
- [OpenLayers](https://openlayers.org/)
- [RTTOV / NWP SAF](https://nwp-saf.eumetsat.int/site/software/rttov/)
- [NWP SAF 1D-Var](https://nwp-saf.eumetsat.int/site/software/1d-var/)
- [WRF/WRFDA](https://www2.mmm.ucar.edu/wrf/users/)
- [DWD Open Data](https://opendata.dwd.de/)
- [NOAA IGRA](https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive)
- [GRUAN](https://www.gruan.org/)

## 📚 Документация

- [Архитектура](docs/ARCHITECTURE.md)
- [Интеграция SatDump](docs/SATDUMP_INTEGRATION.md)
- [Веб-интерфейс](docs/WEB_UI.md)
- [Эксплуатация](docs/OPERATIONS.md)
- [API](docs/API.md)
- [Методика калибровки](docs/CALIBRATION_METHOD.md)
- [Источники данных](docs/DATA_SOURCES.md)
- [Валидация](docs/VALIDATION.md)

## ✅ Проверка разработки

```bash
PYTHONPATH=src pytest -q
python -m compileall -q src
node --check src/satprof_calibrator/web/static/app.js
```

## 📄 Лицензия

MIT. На внешние данные, RTTOV и SatDump распространяются их собственные лицензии и условия использования.
