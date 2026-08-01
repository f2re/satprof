# 🛡️ Astra Linux: сборка, развёртывание и офлайн-установка

## Поддерживаемый контур

Основной профиль — x86_64, Astra Linux 1.6 и 1.7, systemd, без Docker. SatDump берётся только из `f2re/SatDump`, ветка `release/1.2.2`. SatProf требует Python 3.10+, поэтому на старой системе установщик может собрать изолированный CPython в `/opt/satprof/toolchain/python` без замены `/usr/bin/python3`.

## Полная автоматическая установка

```bash
git clone https://github.com/f2re/satprof.git /tmp/satprof-src
cd /tmp/satprof-src
bash scripts/install_astra.sh --satdump-install-deps
```

Последовательность:

1. проверка Astra и архитектуры;
2. установка компилятора, ecCodes, NetCDF/HDF5 и научных библиотек;
3. bootstrap Python 3.11 при отсутствии Python 3.10+;
4. checkout `f2re/SatDump:release/1.2.2`;
5. вызов штатных `scripts/astra/check-system.sh`, `install-deps.sh`, `build.sh` SatDump;
6. установка SatDump в `/opt/satdump/releases/<version>-<astra>-<profile>-<sha>`;
7. smoke-test `satdump version` и атомарное переключение `/opt/satdump/current`;
8. сборка нового release SatProf и отдельной venv;
9. pytest, compileall, JavaScript-проверки;
10. сохранение существующей конфигурации и Workspace;
11. переключение `/opt/satprof/current` и `/opt/satprof/.venv`;
12. перезапуск служб и проверка `/health/ready`;
13. автоматический возврат на предыдущий release при ошибке.

## Отдельная сборка SatDump

```bash
bash scripts/astra/build-satdump.sh --install-deps
```

Закрепление конкретного состояния:

```bash
bash scripts/astra/build-satdump.sh \
  --branch release/1.2.2 \
  --commit FULL_GIT_SHA \
  --profile headless
```

Сценарий отказывается работать с грязным деревом, проверяет ветку, использует versioned prefix и не очищает произвольные системные каталоги. Для desktop:

```bash
bash scripts/astra/build-satdump.sh --profile desktop --install-deps
```

## Версионированное развёртывание SatProf

```bash
bash scripts/astra/deploy.sh --source "$PWD"
```

Постоянные данные не входят в release:

```text
/etc/satprof/config.yaml
/etc/satprof/satprof.env
/opt/satprof/workspace
/opt/rttov/...
```

Код и окружения:

```text
/opt/satprof/.releases/<release-id>/
/opt/satprof/current -> .releases/<release-id>
/opt/satprof/.venv -> current/.venv
```

Ручной откат:

```bash
bash scripts/astra/rollback.sh
bash scripts/astra/rollback.sh --to RELEASE_ID
```

## Обновление

```bash
cd /opt/satprof-src
bash scripts/update.sh
```

С пересборкой SatDump:

```bash
bash scripts/update.sh --with-satdump
```

Обновление не выполняет миграцию путём удаления БД. Новый release запускается с прежним Workspace; при неуспешной readiness-проверке venv и current возвращаются на предыдущий release.

## Офлайн-бандл

Бандл собирается на совместимой линии Astra и архитектуре:

```bash
bash scripts/astra/build-offline-bundle.sh --output dist
```

Состав:

- исходники SatProf;
- нативные wheels всех Python-зависимостей;
- manifest платформы и commit;
- `SHA256SUMS`;
- исходники SatDump в Git bundle;
- установленный SatDump runtime.

На закрытой машине:

```bash
tar -xzf satprof-offline-*.tar.gz
cd satprof-offline-*
bash install.sh
```

Для обновления без SatDump:

```bash
bash install.sh --without-satdump
```

## Self-hosted CI

`.github/workflows/astra-self-hosted.yml` ожидает runners с labels:

```text
self-hosted, linux, x64, astra-1.6
self-hosted, linux, x64, astra-1.7
```

На реальных Astra runners workflow проверяет среду, разворачивает SatProf в временный prefix, собирает SatDump и публикует нативный офлайн-бандл. Обычный GitHub-hosted Ubuntu не считается доказательством совместимости с Astra.

## Предварительная диагностика

```bash
bash scripts/astra/check-system.sh --strict
bash scripts/astra/check-system.sh --json
```

Проверяются версия Astra, архитектура, Python, ecCodes, инструменты, права Workspace, ветка SatDump и полнота установленного prefix.
