# API SatProf

Базовый префикс: `/api/v1`. Интерактивная схема: `/docs`, OpenAPI JSON: `/openapi.json`.

## Состояние

```http
GET /api/v1/health
GET /api/v1/status
GET /api/v1/sources
```

`/sources` возвращает статистику разобранных файлов и состояние WIS2: статус, счётчики, последний topic/data_id и ошибки.

## Получение профиля

```http
POST /api/v1/profile/retrieve
Content-Type: application/json

{"latitude":59.9,"longitude":30.3,"instrument":"mtvza_gy","max_distance_km":120}
```

Ответ содержит поле зрения, модель, уровни давления, температуру, удельную и относительную влажность и предупреждения. При отсутствии гранулы или модели возвращается диагностическая ошибка, а не синтетический профиль.

## Карта

```http
GET /api/v1/satellites
GET /api/v1/granules?instrument=mtvza_gy&limit=80
GET /api/v1/granules/123
```

`/satellites` и `/granules` используют GeoJSON WGS84.

## Задания

```http
POST /api/v1/jobs
Content-Type: application/json

{"job_type":"source.sync","payload":{},"dedupe_key":"manual:source.sync"}
```

Типы ограничены белым списком. Активные задания с одним `dedupe_key` не дублируются.

## SatDump и модели

```http
POST /api/v1/satdump/process
GET /api/v1/models?instrument=mtvza_gy&model_type=bias
GET /api/v1/models?instrument=mtvza_gy&model_type=retrieval
```

SatDump-манифест обязан находиться внутри настроенного inbox.
