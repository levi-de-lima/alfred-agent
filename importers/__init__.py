"""
importers — extração e normalização dos dados externos do Alfred.

Modulos:
  metabase         — cards 189/194 do Metabase, cache parquet, construção
                     de fVendas e dProdutores.
  hubspot_closer   — Pipeline de Closer (deals).
  hubspot_growth   — Funil de Growth (leads TMB + TMR), com enriquecimento
                     de deal_id_closer.
  refresh          — Orquestrador que roda os dois importers HubSpot
                     em sequência.

Reexportações para preservar a API pública usada pelo runtime:
"""

from importers.metabase import (
    DataNormalizationError,
    DataPayload,
    DataUnavailableError,
    MetabaseError,
    load_data,
)

__all__ = [
    "DataNormalizationError",
    "DataPayload",
    "DataUnavailableError",
    "MetabaseError",
    "load_data",
]
