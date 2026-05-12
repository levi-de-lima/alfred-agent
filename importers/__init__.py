"""
importers — extração e normalização dos dados externos do Alfred.

Modulos:
  metabase             — cards 189/194 do Metabase, cache parquet, construção
                         de fVendas e dProdutores.
  hubspot_closer       — Pipeline de Closer (deals).
  hubspot_growth       — Funil de Growth (leads TMB + TMR), com enriquecimento
                         de deal_id_closer.
  merge_growth_legado  — União do parquet do Growth com a base legado do
                         Pipedrive. Sobrescreve hs_growth_leads.parquet
                         adicionando a coluna `fonte` (hubspot|pipedrive).
  refresh              — Orquestrador que roda Closer → Growth → merge legado
                         em sequência.

Reexportações para preservar a API pública usada pelo runtime.
"""

from importers.merge_growth_legado import (
    mapear_pipedrive,
    main as merge_growth_legado,
)
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
    "mapear_pipedrive",
    "merge_growth_legado",
]
