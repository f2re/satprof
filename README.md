# 🛰️ SatProf 0.6

**SatProf** — автономная система калибровки спутниковых измерений по радиозондам, восстановления вертикальных профилей атмосферы и подготовки наблюдений для WRFDA.

```text
приёмный поток → SatDump release/1.2.2 → физический Level‑1C
                                             │
WIS2 / DWD / IGRA → радиозонд → RTTOV ───────┤
                                             ↓
                         count→TB → O−B → bias → T/q/RH → WRFDA
```

Система рассчитана на непрерывную работу без Docker на Debian и Astra Linux 1.6/1.7.

## ✨ Реализовано

- 📡 строгая связка с [`f2re/SatDump:release/1.2.2`](https://github.com/f2re/SatDump/tree/release/1.2.2);
- 🧪 встроенная команда SatDump `level1c` для количественного экспорта `product.cbor`;
- 🔢 сохранение исходных цифровых счётов без потерь;
- 🌡️ экспорт яркостных температур только при наличии реального калибратора;
- 🧭 широта, долгота, время, положение в скане и спутниковый зенитный угол;
- 🔐 проверка размерностей, единиц, путей, числа байт и CRC32 каждого массива;
- 👁️ автоматическое наблюдение каталогов приёмной станции;
- 🎈 оперативные TEMP BUFR через WIS 2.0 и DWD, история NOAA IGRA;
- 📐 четырёхмерная коллокация спутник–радиозонд с учётом дрейфа шара;
- 🌍 RTTOV/PyRTTOV, расчёт `TBрасч`, O−B и полной ошибки пары;
- 📈 викарная модель `raw count → TB`, остаточные поправки и контроль дрейфа;
- 🧠 восстановление `T(p)`, `q(p)`, `RH(p)` и каркас 1D‑Var;
- 🗺️ FastAPI/OpenLayers: спутники, орбиты, гранулы и выбор точки профиля;
- ⚙️ постоянная SQLite-очередь, дедупликация, повторные попытки и восстановление;
- 🩺 readiness/liveness, Prometheus-метрики и systemd-мониторинг;
- 🚀 версионированное развёртывание с автоматическим откатом;
- 📦 нативный офлайн-бандл для Astra Linux 1.6/1.7.

## ⚠️ Научная честность данных

SatProf не получает радиометрию из PNG/JPEG и оформленных композитов.

Поддерживаются три состояния:

| Состояние | Содержание |
|---|---|
| `calibrated` | SatDump выдал физические яркостные температуры |
| `partial` | TB доступна только для части каналов |
| `raw_counts` | сохранены только исходные цифровые отсчёты |
| `vicarious_calibrated` | SatProf построил проверенную модель `count→TB` |

Текущий декодер МТВЗА‑ГЯ в SatDump сохраняет исходные 16-битные отсчёты, но не имеет штатной абсолютной калибровки всех каналов. Поэтому такие данные маркируются как `raw_counts`, а не как кельвины. После накопления коллокаций SatProf обучает отдельную модель по целевым TB, рассчитанным RTTOV из радиозонда.

## 🚀 Быстрый запуск

### 1. Установить SatDump

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
```

Проверка нового экспортёра:

```bash
bash scripts/astra/run.sh -- level1c --help
```

### 2. Установить SatProf

```bash
git clone https://github.com/f2re/satprof.git /opt/satprof
cd /opt/satprof
bash scripts/install.sh
```

На Astra Linux полный цикл:

```bash
bash scripts/install_astra.sh --satdump-install-deps
```

Установщик:

1. проверяет Astra Linux и архитектуру;
2. при необходимости собирает изолированный CPython 3.11;
3. получает строго ветку SatDump `release/1.2.2`;
4. собирает SatDump в версионный prefix;
5. создаёт неизменяемый release SatProf;
6. выполняет тесты;
7. переключает символьные ссылки;
8. проверяет `/health/ready`;
9. при ошибке возвращает предыдущую версию.

## 🔧 Минимальная конфигурация

```bash
cp config/config.example.yaml config/config.yaml
nano config/config.yaml
```

```yaml
workspace: /opt/satprof/workspace

satdump:
  root: /opt/SatDump
  repository: https://github.com/f2re/SatDump
  required_branch: release/1.2.2
  install_prefix: /opt/satdump/current
  # expected_commit: ПОЛНЫЙ_SHA_ПРОВЕРЕННОЙ_СБОРКИ

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

Запуск:

```bash
.venv/bin/satprof-worker --config config/config.yaml
```

```bash
.venv/bin/satprof-web --config config/config.yaml
```

Интерфейс: `http://127.0.0.1:8088`.

## 📡 Автоматический приём спутниковых файлов

Готовые профили находятся в [`config/watch_profiles.example.yaml`](config/watch_profiles.example.yaml).

Пример для X-band Метеор‑М:

```yaml
satdump:
  watch_profiles:
    - name: meteor-m2-4-mtvza-xband
      enabled: true
      root: /data/receiver/meteor-m2-4/xband
      glob: "*.cadu"
      min_age_seconds: 45
      min_size_bytes: 1048576
      pipeline: meteor_m2x_xband
      input_level: cadu
      instrument: mtvza_gy
      satellite: METEOR-M2-4
      reader:
        type: auto
        product_glob:
          - "**/MTVZA/product.cbor"
        stride: 1
```

Worker автоматически:

1. ждёт стабилизации размера/возраста файла;
2. рассчитывает SHA‑256;
3. создаёт детерминированный манифест;
4. запускает pipeline SatDump;
5. находит `product.cbor` требуемого прибора;
6. запускает `satdump level1c`;
7. проверяет и импортирует массивы;
8. ставит расчёт коллокаций в очередь;
9. переносит обработанный манифест в `processed/`.

Подробно: [`docs/LEVEL1C_PIPELINE.md`](docs/LEVEL1C_PIPELINE.md).

## 🧪 Формат бинарного Level‑1C

```text
satprof-level1c/
├── satprof-level1c-index.json
└── group-1-200xNNN/
    ├── satprof-level1c.json
    ├── raw_counts.u16
    ├── brightness_temperature.f32
    ├── latitude.f32
    ├── longitude.f32
    ├── observation_time.f64
    ├── scan_position.f32
    ├── satellite_zenith.f32
    ├── quality_flag.u16
    ├── source_x.u32
    └── source_y.u32
```

Формат не требует NetCDF и пригоден для закрытого контура Astra Linux. Для каждого массива указаны `dtype`, little-endian, shape, units, bytes и CRC32.

## 📐 Викарная калибровка `count → TB`

```text
raw count + scan + sec(θ)
            ↓
робастная поканальная модель
            ↓
TB, рассчитанная RTTOV по радиозонду
```

Обучение разделяется по независимым запускам радиозонда и по времени. Для каждого канала контролируются:

- число пар и независимых запусков;
- диапазон raw counts;
- RMSE и bias на отложенном периоде;
- переносимость по позиции скана;
- физический диапазон полученной TB.

После статуса `production` модель применяется к ранее накопленным гранулам. Затем система повторно формирует O−B и рассчитывает остаточную bias-модель.

## 🌐 Радиозонды

| Источник | Назначение |
|---|---|
| WIS 2.0 | основной оперативный международный поток |
| DWD Open Data | открытый резервный TEMP BUFR |
| NOAA IGRA | история и дозаполнение |
| GRUAN | метрологическая валидация |
| ЕИП Росгидромета | региональное дополнение |

Радиозонд сравнивается со спутником только через радиационный оператор:

```text
профиль + поверхность + геометрия → RTTOV → TBрасч
```

Подробно: [`docs/WIS2.md`](docs/WIS2.md) и [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

## 🗺️ Веб-интерфейс

OpenLayers показывает:

- текущие положения «Метеор‑М» и «Электро‑Л»;
- орбиты по TLE/SGP4;
- контуры Level‑1C-гранул;
- выбранную точку и ближайшее поле зрения;
- восстановленные `T`, `q`, `RH`;
- состояние SatDump, WIS2, DWD, IGRA, очереди и моделей.

Профиль не создаётся, если отсутствует реальная гранула или подходящая модель.

## 🩺 Мониторинг

```http
GET /health/live
GET /health/ready
GET /health/ready?deep=true
GET /metrics
```

Глубокий контроль проверяет:

- SQLite `quick_check`;
- права и свободное место Workspace;
- возраст и состояние очереди;
- свежесть зондов, гранул, коллокаций и моделей;
- ветку, commit и runtime SatDump;
- `satdump version` и `satdump level1c --help`;
- наличие коэффициентов RTTOV.

```bash
/opt/satprof/.venv/bin/satprof-monitor \
  --config /etc/satprof/config.yaml \
  --deep --write-snapshot
```

## 🚀 Обновление и откат

```bash
bash scripts/update.sh
bash scripts/update.sh --with-satdump
```

Релизы размещаются в `/opt/satprof/.releases/`. Конфигурация, Workspace и база данных не заменяются.

Откат:

```bash
bash /opt/satprof/current/scripts/astra/rollback.sh
```

## 📦 Офлайн-бандл

На машине с той же версией Astra:

```bash
bash scripts/astra/build-offline-bundle.sh --output dist
```

На закрытой машине:

```bash
tar -xzf satprof-offline-*.tar.gz
cd satprof-offline-*
bash install.sh
```

Бандл содержит wheelhouse Python, Git bundle SatDump, собранный runtime, SHA256SUMS и сценарий установки.

## ✅ Проверка разработки

```bash
PYTHONPATH=src pytest -q
python -m compileall -q src
find src/satprof_calibrator/web/static -name '*.js' -print0 \
  | xargs -0 -n1 node --check
find scripts -name '*.sh' -print0 \
  | xargs -0 -n1 bash -n
```

Нативная сборка SatDump и офлайн-бандла проверяются self-hosted runner-ами с метками `astra-1.6` и `astra-1.7`.

## 📚 Документация

- [Архитектура](docs/ARCHITECTURE.md)
- [Автоматический Level‑1C](docs/LEVEL1C_PIPELINE.md)
- [Интеграция SatDump](docs/SATDUMP_INTEGRATION.md)
- [Методика калибровки](docs/CALIBRATION_METHOD.md)
- [Источники данных](docs/DATA_SOURCES.md)
- [WIS 2.0](docs/WIS2.md)
- [Веб-интерфейс](docs/WEB_UI.md)
- [Мониторинг](docs/MONITORING.md)
- [Развёртывание Astra](docs/ASTRA_DEPLOYMENT.md)
- [Валидация](docs/VALIDATION.md)

## 📄 Лицензии

Код SatProf распространяется по MIT. На SatDump, RTTOV и внешние данные действуют их собственные лицензии и условия использования.
