# Эксплуатация SatProf

## Службы

```text
satprof-web.service     API и OpenLayers
satprof-worker.service  очередь, SatDump, RTTOV, обучение
satprof-wis2.service    постоянная MQTT(S)-подписка TEMP
```

WIS2 включайте только после `sources.wis2.enabled: true`.

```bash
sudo systemctl enable --now satprof-worker satprof-web
sudo systemctl enable --now satprof-wis2
```

## Диагностика

```bash
systemctl status satprof-worker satprof-web satprof-wis2
journalctl -u satprof-worker -f
journalctl -u satprof-wis2 -f
curl -s http://127.0.0.1:8088/api/v1/health | python -m json.tool
curl -s http://127.0.0.1:8088/api/v1/sources | python -m json.tool
.venv/bin/satprof jobs --config /etc/satprof/config.yaml
```

Ошибка отдельного BUFR регистрируется как `source.file_error` и не прерывает пакет. Ошибка DWD, одной станции IGRA или notification WIS2 изолируется и остаётся в events.

## Резервная копия

Сохраняйте `catalog.sqlite`, `soundings/`, `satellite/`, `matchups/`, `models/`, `reports/`, WIS2 sidecar, конфигурацию и коэффициенты RTTOV.

## Production

Перед активным применением поправок накопите несколько недель Level‑1C/TEMP, проверьте O−B по каналу, скану, углу, поверхности и орбите, подтвердите коэффициенты на независимом периоде/GRUAN и начинайте WRFDA с 1–2 устойчивых температурных каналов.
