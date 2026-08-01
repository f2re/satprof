"""Совместимый фасад эксплуатационных операций SatProf."""

from .sounding_ops import ingest_sounding_file, store_profiles, sync_soundings
from .instrument_ops import make_rtm, update_all_statistics, update_instrument

__all__ = [
    "ingest_sounding_file",
    "store_profiles",
    "sync_soundings",
    "make_rtm",
    "update_all_statistics",
    "update_instrument",
]
