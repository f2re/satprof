# API SatProf

Базовый префикс: `/api/v1`.

## Проверка процессов

```http
GET /health/live
GET /health/ready
GET /health/ready?deep=true
```

`ready` возвращает HTTP 503 при критической ошибке Workspace, SQLite, очереди или обязательного SatDump/RTTOV.

## Мониторинг

```http
GET /api/v1/monitoring
GET /api/v1/monitoring?deep=true
GET /metrics
```

`/metrics` использует Prometheus text exposition format.

## Получение профиля

```http
POST /api/v1/profile/retrieve
Content-Type: application/json

{"latitude":59.9,"longitude":30.3,"instrument":"mtvza_gy","max_distance_km":120}
```

Ответ содержит поле зрения, модель, уровни давления, температуру, удельную и относительную влажность и предупреждения.

## Задания

```http
POST /api/v1/jobs
{"job_type":"source.sync","payload":{},"dedupe_key":"manual:source.sync"}
```

Допустимые типы ограничены белым списком. SatDump-манифест должен находиться внутри настроенного inbox.

## Остальные endpoints

```text
GET  /api/v1/health
GET  /api/v1/status
GET  /api/v1/sources
GET  /api/v1/satellites
GET  /api/v1/granules
GET  /api/v1/granules/{id}
GET  /api/v1/jobs
GET  /api/v1/jobs/{id}
POST /api/v1/satdump/process
GET  /api/v1/models
```

`/satellites` и `/granules` возвращают GeoJSON WGS84. Полная схема доступна в `/docs` и `/openapi.json`.
