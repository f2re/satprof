# API SatProf

Базовый префикс: `/api/v1`.

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

`/satellites` и `/granules` возвращают GeoJSON WGS84. Полная схема доступна в `/docs` и `/openapi.json`.
