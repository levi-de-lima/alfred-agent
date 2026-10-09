"""
data_agent.py — bridge entre importers.metabase e o restante do pipeline.

Responsabilidade: invocar load_data(), validar o resultado e empacotar
AnalyticsContext para o AnalyticsAgent. Não chama Claude, não roda pandas.
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from config import settings, DATA_HUB
from importers.metabase import DataNormalizationError, DataUnavailableError, load_data

_HS_CLOSER_PARQUET = DATA_HUB / "hubspot" / "hs_closer_pipeline.parquet"
_HS_GROWTH_PARQUET = DATA_HUB / "hubspot" / "hs_growth_leads.parquet"

logger = settings.logger


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

class DataAgentError(Exception):
    """Falha irrecuperável na camada de dados."""


class EmptyDataError(DataAgentError):
    """fVendas retornou zero linhas após normalização."""


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class DataAgentInput:
    user_query: str
    force_refresh: bool = False
    areas: list[str] | None = None  # áreas classificadas pelo ContextAgent


@dataclass
class AnalyticsContext:
    vendas: pd.DataFrame
    produtores: pd.DataFrame
    hs_closer: pd.DataFrame
    hs_growth: pd.DataFrame
    data_reference_date: date
    data_source: str        # "metabase" | "cache" | "cache (stale fallback)" | "none"
    loaded_at: datetime
    query_hint: str         # user_query normalizado, passado ao AnalyticsAgent


# ---------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------

def run(inp: DataAgentInput, session_id: str) -> AnalyticsContext:
    areas = set(inp.areas or [])
    _log(session_id, "started", query=inp.user_query[:120], areas=",".join(sorted(areas)) or "greeting")
    t0 = time.time()

    # Saudações não precisam de dados
    if not areas:
        _log(session_id, "skipped", reason="greeting")
        return AnalyticsContext(
            vendas=pd.DataFrame(),
            produtores=pd.DataFrame(),
            hs_closer=pd.DataFrame(),
            hs_growth=pd.DataFrame(),
            data_reference_date=date.today(),
            data_source="none",
            loaded_at=datetime.utcnow(),
            query_hint=inp.user_query.strip(),
        )

    load_metabase = "retention" in areas
    load_hubspot  = "acquisition" in areas

    vendas = pd.DataFrame()
    produtores = pd.DataFrame()
    source_label = "none"
    ref_date = date.today()
    loaded_at = datetime.utcnow()

    if load_metabase:
        try:
            payload = load_data(force_refresh=inp.force_refresh)
        except DataUnavailableError as exc:
            _log(session_id, "error", error=str(exc))
            raise DataAgentError(str(exc)) from exc
        except DataNormalizationError as exc:
            _log(session_id, "error", error=f"Normalização: {exc}")
            raise DataAgentError(
                f"O arquivo Excel não tem o formato esperado: {exc}"
            ) from exc
        except Exception as exc:
            _log(session_id, "error", error=f"Erro inesperado: {type(exc).__name__}: {exc}")
            raise DataAgentError(
                f"Erro inesperado ao carregar os dados: {type(exc).__name__}: {exc}"
            ) from exc

        if payload.vendas.empty:
            msg = "A tabela fVendas está vazia. Verifique os dados no Metabase."
            _log(session_id, "error", error=msg)
            raise EmptyDataError(msg)

        vendas = payload.vendas
        produtores = payload.produtores
        ref_date = payload.data_reference_date
        loaded_at = payload.loaded_at
        source_label = payload.source
        if payload.source == "cache":
            import time as _time
            from pathlib import Path as _Path
            cache_age_h = (_time.time() - _Path(payload.cache_file_path).stat().st_mtime) / 3600
            if cache_age_h > settings.cache_max_age_hours:
                source_label = "cache (stale fallback)"

    hs_closer = _load_parquet(session_id, _HS_CLOSER_PARQUET, "hs_closer") if load_hubspot else pd.DataFrame()
    hs_growth = _load_parquet(session_id, _HS_GROWTH_PARQUET, "hs_growth") if load_hubspot else pd.DataFrame()

    ctx = AnalyticsContext(
        vendas=vendas,
        produtores=produtores,
        hs_closer=hs_closer,
        hs_growth=hs_growth,
        data_reference_date=ref_date,
        data_source=source_label,
        loaded_at=loaded_at,
        query_hint=inp.user_query.strip(),
    )

    _log(
        session_id, "completed",
        areas=",".join(sorted(areas)),
        source=source_label,
        rows_vendas=len(ctx.vendas),
        rows_produtores=len(ctx.produtores),
        rows_hs_closer=len(ctx.hs_closer),
        rows_hs_growth=len(ctx.hs_growth),
        data_ref=str(ctx.data_reference_date),
        duration_ms=int((time.time() - t0) * 1000),
    )
    return ctx


# ---------------------------------------------------------------------------
# Parquet loader helper
# ---------------------------------------------------------------------------

def _load_parquet(session_id: str, path: Path, label: str) -> pd.DataFrame:
    """Carrega um parquet se disponível; retorna DataFrame vazio caso contrário."""
    if not path.exists():
        _log(session_id, f"{label}_unavailable", path=str(path))
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        _log(session_id, f"{label}_loaded", rows=len(df))
        return df
    except Exception as exc:
        _log(session_id, f"{label}_error", error=str(exc))
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Helper de log
# ---------------------------------------------------------------------------

def _log(session_id: str, event: str, **kwargs) -> None:
    parts = [
        f"SESSION={session_id}",
        "AGENT=DataAgent",
        f"EVENT={event}",
        *[f"{k}={v}" for k, v in kwargs.items()],
    ]
    msg = f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} | {' | '.join(parts)}"
    if event == "error":
        logger.error(msg)
    else:
        logger.info(msg)
