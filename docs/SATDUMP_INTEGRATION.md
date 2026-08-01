# 📡 Интеграция SatDump 1.2.2

## Зафиксированный источник

Поддерживается только `f2re/SatDump`, ветка `release/1.2.2`. Исходники и установленный runtime разделены:

```yaml
satdump:
  root: /opt/SatDump
  required_branch: release/1.2.2
  expected_commit: ""
  install_prefix: /opt/satdump/current
  runner: scripts/astra/run.sh
```

`expected_commit` рекомендуется заполнить полным SHA после приемочных испытаний.

## Сборка

```bash
bash scripts/astra/build-satdump.sh --install-deps
```

Сценарий вызывает штатные Astra-скрипты SatDump, устанавливает результат в versioned prefix и атомарно переключает `/opt/satdump/current` только после smoke-test.

## Запуск из SatProf

Команда всегда содержит явный prefix:

```text
bash /opt/SatDump/scripts/astra/run.sh \
  --prefix /opt/satdump/current -- \
  PIPELINE INPUT_LEVEL INPUT OUTPUT [OPTIONS]
```

Это исключает зависимость от `$HOME` пользователя systemd.

## Манифест

```json
{
  "schema": "satprof.satdump-job/1",
  "input_file": "./pass.cs16",
  "pipeline": "meteor_m2x_lrpt",
  "input_level": "baseband",
  "samplerate": 240000,
  "baseband_format": "cs16",
  "instrument": "mtvza_gy",
  "satellite": "Метеор-М №2-4",
  "reader": {
    "type": "netcdf",
    "glob": "**/*level1c*.nc",
    "mapping": "config/satellite_mapping.example.yaml"
  }
}
```

Манифест и входная запись помещаются в `workspace/inbox/satdump/`.

## Безопасность и идемпотентность

- pipeline/input level проверяются как безопасные токены;
- число extra args ограничивается;
- один output защищён `flock`;
- обработка идёт в `.partial`;
- при успехе каталог атомарно переименовывается;
- старый результат перемещается в `archive`;
- ошибки сохраняются в `failed` и отдельном state JSON;
- успешный input SHA повторно не обрабатывается;
- NetCDF и GeoTIFF input дедуплицируются каталогом SQLite.

## Научный контракт

SatProf импортирует только физические Level‑1C:

- NetCDF по YAML-карте переменных;
- стек GeoTIFF в K с `*.satprof-level1c.json`.

PNG/JPEG, LUT-композиты и презентационные изображения не являются входом калибровки. Если SatDump для конкретного прибора не выпускает количественный Level‑1C, требуется отдельный exporter на стороне SatDump или официальный продукт.

## Provenance

`output/satprof-provenance.json` содержит:

- SHA-256 входа;
- pipeline и параметры;
- branch/commit/source marker SatDump;
- install prefix и фактическую команду;
- времена и длительность;
- журнал;
- импортированные гранулы и QC.
