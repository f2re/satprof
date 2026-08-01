# Автоматический Level‑1C: SatDump → SatProf

## Назначение

SatProf 0.6 использует количественный экспорт из `f2re/SatDump:release/1.2.2`. Связка больше не зависит от оформленных PNG, ручной конвертации и индивидуального NetCDF-маппинга каждого пролёта.

```text
новый файл приёмной станции
        ↓ watch_profiles
автоматический *.satprof.json
        ↓ worker
SatDump pipeline → product.cbor
        ↓ satdump level1c
проверяемый бинарный Level‑1C
        ↓ SatProf reader + CRC
TB либо raw counts + геометрия + время
        ↓
RTTOV / count→TB / O−B / bias / retrieval
```

## Команда SatDump

После декодирования SatProf запускает:

```bash
bash /opt/SatDump/scripts/astra/run.sh \
  --prefix /opt/satdump/current -- \
  level1c \
  /path/to/product-directory \
  /path/to/product-directory/satprof-level1c \
  --instrument mtvza_gy \
  --satellite METEOR-M2-4 \
  --stride 1 \
  --overwrite
```

Экспортёр группирует каналы с одинаковой геометрией и создаёт:

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

Для каждого массива зафиксированы `dtype`, порядок байт, размерность, единицы, число байт и CRC32. Reader запрещает выход пути за пределы продукта и отклоняет повреждённый файл.

## Состояния радиометрии

### `calibrated`

Все выбранные каналы имеют штатный калибратор SatDump и физическую яркостную температуру.

### `partial`

Часть каналов имеет TB, остальные сохраняются только как исходные счёты.

### `raw_counts`

Физической абсолютной калибровки в SatDump пока нет. Это текущее штатное состояние декодированных каналов МТВЗА‑ГЯ. Значения `raw_counts` сохраняются без потерь, а массив TB заполняется `NaN`.

SatProf не интерпретирует raw counts как K.

### `vicarious_calibrated`

SatProf накопил достаточное число коллокаций и построил прошедшую независимую проверку модель:

```text
TB = f(raw_count, raw_count², scan, scan², sec θ − 1)
```

Целевым значением служит TB, рассчитанная RTTOV по радиозонду, поверхности и геометрии. Выборка разделяется по независимым запускам зонда и по времени. Для каждого канала сохраняются RMSE, bias, диапазон счётов и количество запусков.

После получения статуса `production` модель применяется к сохранённым гранулам, затем обычный контур повторно формирует O−B и оценивает остаточную bias-модель.

## Автоматическое наблюдение каталогов

Пример находится в [`config/watch_profiles.example.yaml`](../config/watch_profiles.example.yaml).

Профиль задаёт:

- каталог и шаблон файлов;
- минимальный возраст и размер завершённой записи;
- pipeline и уровень входа SatDump;
- прибор и спутник;
- параметры Level‑1C-exporter;
- перечень каналов и прореживание.

Worker каждые `worker.schedules.scan_satdump` секунд:

1. сканирует каталоги;
2. рассчитывает SHA‑256 файла;
3. создаёт детерминированный манифест;
4. ставит обработку в очередь с дедупликацией;
5. после успеха переносит манифест в `processed/`;
6. сохраняет provenance, журнал и состояние обработки.

Одинаковое содержимое не обрабатывается повторно. Изменившийся файл получает новый SHA и новое задание.

## Ввод в эксплуатацию МТВЗА‑ГЯ

1. Включить X-band/HRPT pipeline, реально создающий `MTVZA/product.cbor`.
2. Включить профиль `meteor-m2-4-mtvza-xband`.
3. Проверить поступление raw-count гранул через `/api/v1/granules`.
4. Подключить RTTOV и оперативные TEMP WIS2/DWD.
5. Накопить не менее 40 независимых запусков и 80 пар на канал; для production рекомендуется 200–500 запусков на режим.
6. Запустить `calibration.train`.
7. Проверить `radiometric` model registry, диапазоны счётов и независимую RMSE.
8. После статуса `production` проверить O−B, зависимость от скана, поверхности и орбиты.
9. Только после этого использовать TB в retrieval или WRFDA.

## Диагностика

```bash
/opt/satprof/.venv/bin/satprof satdump-validate \
  --config /etc/satprof/config.yaml
```

Глубокий readiness запускает обе проверки:

```text
satdump version
satdump level1c --help
```

```bash
curl -s 'http://127.0.0.1:8088/health/ready?deep=true' \
  | python -m json.tool
```

Журналы и состояния:

```text
workspace/logs/jobs/satdump-*.log
workspace/monitoring/satdump/*.json
workspace/satdump-output/.partial/
workspace/satdump-output/failed/
workspace/satdump-output/archive/
```

## Ограничения

- Наличие raw counts ещё не означает наличие физической TB.
- Модель count→TB привязана к аппарату, прибору, каналу, версии декодера и калибровочной эпохе.
- После изменения аппаратуры, коэффициентов, SRF или кода L1B необходимо начать новую эпоху и повторить независимую проверку.
- Для ИКФС‑2 и МСУ‑ГС конкретные pipeline и имена product-каталогов должны быть подтверждены реальным выходом приёмной станции.
