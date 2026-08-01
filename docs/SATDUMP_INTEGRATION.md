# Интеграция SatDump 1.2.2

Поддерживается `f2re/SatDump`, ветка `release/1.2.2`. При наличии `.git` SatProf проверяет ветку.

```text
bash scripts/astra/run.sh -- PIPELINE INPUT_LEVEL INPUT OUTPUT [OPTIONS]
```

JSON-манифест задаёт запись, pipeline, прибор, спутник и reader. Поддержаны Level‑1C NetCDF по YAML-карте и стек GeoTIFF в K по `*.satprof-level1c.json`.

PNG/JPEG, LUT-композиты и изображения без физических единиц не допускаются. Экспорт количественного Level‑1C конкретного российского прибора может потребовать отдельного модуля SatDump; SatProf предоставляет стабильный интерфейс приёма.
