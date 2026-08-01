# Архитектура SatProf

1. Приём: DWD/IGRA/GRUAN, SatDump, TLE.
2. Нормализация: `SoundingProfile` и `SatelliteGranule`, NPZ + SQLite.
3. Научная обработка: QC, траектория шара, 4D-коллокация, RTTOV, O−B.
4. Обучение: робастные поправки, дрейф, PCA/Ridge retrieval.
5. Представление: FastAPI, OpenLayers, JSON/NetCDF/HTML.

SQLite работает в WAL-режиме, входы дедуплицируются по SHA-256. `jobs` — постоянная очередь с атомарным захватом, повторными попытками и восстановлением после аварии. Web только читает каталог и ставит задания; Worker выполняет загрузки, SatDump, RTTOV и обучение. Версии моделей имеют состояния `production`, `candidate`, `archived`.
