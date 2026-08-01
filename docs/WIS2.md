# 🌐 Оперативный приём TEMP через WIS 2.0

## Назначение

`SatProf WIS2` — отдельный долгоживущий процесс, который получает уведомления WIS2 Global Broker и сохраняет связанные TEMP BUFR в Workspace. Тяжёлый ecCodes-разбор не выполняется внутри MQTT callback: после безопасной загрузки ставится обычное задание `source.sync`.

```text
Global Broker MQTT(S)
        ↓
WIS2 Notification Message, GeoJSON Feature
        ↓
canonical / update / deletion
        ↓
inline base64/gzip или HTTP(S)
        ↓
integrity + BUFR magic
        ↓
атомарный файл + .wis2.json
        ↓
JobQueue: source.sync → ecCodes → QC
```

## Дедупликация

Одно уведомление может поступить через несколько Global Cache. Идентификатор сообщения различается, а `properties.data_id` относится к ресурсу, поэтому имя файла и дедупликация строятся по `data_id`.

## Поддержка

- `rel=canonical`, `rel=update`, `rel=deletion`;
- `content.encoding=utf-8|base64|gzip`;
- HTTP/HTTPS;
- SHA-256/384/512 и SHA3-256/384/512;
- автоматическое удаление старого объекта при deletion;
- принудительная перезагрузка при update.

## Настройка

```yaml
sources:
  wis2:
    enabled: true
    broker: mqtts://everyone:everyone@wis2broker.globaldata.nws.noaa.gov:8883
    topics:
      - cache/a/wis2/+/data/core/weather/surface-based-observations/temp/#
    download_dir: inbox/wis2
    file_globs: ["*.bufr", "*.bufr4", "*.bin"]
    media_types: [application/bufr, application/x-bufr, application/octet-stream]
    qos: 1
    keepalive: 60
    verify_tls: true
    require_bufr_magic: true
    download_timeout_seconds: 90
    max_download_bytes: 134217728
    enqueue_sync: true
```

Несколько topic допустимы. Для снижения нагрузки оставляйте только ветвь `core/weather/surface-based-observations/temp`.

## Учётные данные

Broker URL может содержать пользователя/пароль. Также поддерживаются ключи YAML и переменные:

```bash
SATPROF_WIS2_USERNAME=everyone
SATPROF_WIS2_PASSWORD=everyone
```

Для непубличных данных задайте права `0640` на `/etc/satprof/satprof.env` и не помещайте секреты в Git.

## Запуск

```bash
.venv/bin/satprof-wis2 --config config/config.yaml
```

```bash
sudo cp systemd/satprof-wis2.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now satprof-wis2.service
journalctl -u satprof-wis2 -f
```

## Состояние

Подписчик сохраняет `source.wis2` в `scheduler_state`: статус `connecting/connected/disconnected/degraded/stopped`, счётчики, последний topic/data_id/результат и время ошибки.

```bash
curl -s http://127.0.0.1:8088/api/v1/sources | python -m json.tool
```

## Защита

- TLS по умолчанию;
- максимальный размер объекта;
- только HTTP(S) и inline-content;
- криптографическая сумма;
- обязательная сигнатура `BUFR` по умолчанию;
- запись через `.part` и атомарный rename;
- canonical пропускается только при совпадении `data_id`, SHA и integrity;
- update не использует старый кэш;
- deletion удаляет файл и sidecar.

Ошибка одного notification переводит источник в `degraded`, записывается в events, но не завершает процесс. Ошибка одного BUFR при `source.sync` не блокирует остальные файлы и резервный DWD/IGRA.

## Ссылки

- [WIS 2.0](https://community.wmo.int/en/activity-areas/wis)
- [WIS2 Notification Message](https://wmo-im.github.io/wis2-notification-message/)
- [WIS2 Topic Hierarchy](https://wmo-im.github.io/wis2-topic-hierarchy/)
- [pywis-pubsub](https://github.com/wmo-im/pywis-pubsub)
