"""
importers/metabase.py — camada de dados via API do Metabase.

Fontes:
  Card 189 → fVendas  (Valor por produtor/mês + última data de venda)
  Card 194 → dProdutores (Código, Produtor, Gestor, Cluster)

Interface pública:
    load_data(force_refresh=False) -> DataPayload
"""

import io
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
import requests

from config import settings

logger = settings.logger

# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

class MetabaseError(Exception):
    """Falha na conexão ou consulta ao Metabase."""

class DataNormalizationError(Exception):
    """Dados retornados não têm o schema esperado."""

class DataUnavailableError(Exception):
    """Metabase inacessível e sem cache local disponível."""


# ---------------------------------------------------------------------------
# Tipos de retorno
# ---------------------------------------------------------------------------

VALID_STATUSES = {"Ativo", "Pré-Churn", "Churn", "Inativo"}


@dataclass
class DataPayload:
    vendas: pd.DataFrame
    produtores: pd.DataFrame
    source: Literal["metabase", "cache"]
    loaded_at: datetime
    cache_file_path: str
    data_reference_date: date  # max(vendas["Data"])


# ---------------------------------------------------------------------------
# Cache (parquet) — nomes fixos: main + backup
# ---------------------------------------------------------------------------

def _main_vendas()  -> Path: return settings.cache_dir / "fvendas.parquet"
def _main_prod()    -> Path: return settings.cache_dir / "dprodutores.parquet"
def _bck_vendas()   -> Path: return settings.cache_dir / "fvendas_backup.parquet"
def _bck_prod()     -> Path: return settings.cache_dir / "dprodutores_backup.parquet"


def _main_cache_complete() -> bool:
    return _main_vendas().exists() and _main_prod().exists()


def _backup_cache_complete() -> bool:
    return _bck_vendas().exists() and _bck_prod().exists()


def _is_cache_valid() -> bool:
    if not _main_cache_complete():
        return False
    age_seconds = time.time() - _main_vendas().stat().st_mtime
    return age_seconds < settings.cache_max_age_hours * 3600


def _save_to_cache(vendas: pd.DataFrame, produtores: pd.DataFrame) -> None:
    if _main_cache_complete():
        shutil.copy2(_main_vendas(), _bck_vendas())
        shutil.copy2(_main_prod(), _bck_prod())
    vendas.to_parquet(_main_vendas(), index=False)
    produtores.to_parquet(_main_prod(), index=False)
    logger.info("Cache | salvo: fvendas.parquet + dprodutores.parquet")


def _load_main() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_parquet(_main_vendas()), pd.read_parquet(_main_prod())


def _load_backup() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_parquet(_bck_vendas()), pd.read_parquet(_bck_prod())


# ---------------------------------------------------------------------------
# Metabase API
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BACKOFF = 2  # segundos

_session_token: str | None = None


def _get_metabase_token() -> str:
    global _session_token
    if _session_token:
        return _session_token
    logger.info(f"Metabase | autenticando como {settings.metabase_user}")
    resp = requests.post(
        f"{settings.metabase_url}/api/session",
        json={"username": settings.metabase_user, "password": settings.metabase_password},
        timeout=15,
    )
    resp.raise_for_status()
    _session_token = resp.json()["id"]
    logger.info(f"Metabase | autenticado, token={_session_token[:8]}...")
    return _session_token


def _fetch_card_csv(token: str, card_id: int) -> pd.DataFrame:
    """Baixa um card do Metabase como CSV e retorna DataFrame. Retry 3x."""
    url = f"{settings.metabase_url}/api/card/{card_id}/query/csv"
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info(f"Metabase | card {card_id} | tentativa {attempt}/{_MAX_RETRIES}")
            resp = requests.post(url, headers={"X-Metabase-Session": token}, timeout=180)
            if resp.status_code not in (200, 202):
                raise MetabaseError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8", encoding_errors="replace")
            logger.info(f"Metabase | card {card_id} | {len(df)} linhas retornadas")
            return df
        except MetabaseError:
            raise
        except Exception as exc:
            last_exc = exc
            logger.warning(f"Metabase | card {card_id} | tentativa {attempt} falhou: {exc}")
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF)

    raise MetabaseError(
        f"Card {card_id} inacessível após {_MAX_RETRIES} tentativas: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Construção de fVendas (card 189)
# ---------------------------------------------------------------------------
#
# Colunas esperadas do card 189:
#   Produtor ID, Status, Produtor, CNPJ, Gestor, Cluster,
#   Data: Mês, Soma de Valor Principal, Mínimo de Efetivado em: Dia
#
# O card tem apenas meses COM VENDAS (sem linhas de valor=0).
# Geramos o grid completo (todos produtores × todos meses) e calculamos
# o Status histórico correto a partir de "Mínimo de Efetivado em: Dia".

_COL_MAP_189 = {
    "Produtor ID":                   "produtor_id",
    "Produtor":                      "produtor_nome",
    "Data: Mês":                     "mes",
    "Soma de Valor Principal":       "valor",
    "Mínimo de Efetivado em: Dia":   "ultima_venda_no_mes",
}

CLUSTER_MAP = {
    "Energium":  "PP/P",
    "Palladium": "M",
    "Titanium":  "G",
    "Rhodium":   "GG/EG",
    "PP":        "PP/P",
    "P":         "PP/P",
    "GG":        "GG/EG",
    "EG":        "GG/EG",
    "G":         "G",
    "M":         "M",
    "Desativado": "Desativado",
    "S/C":       "S/C",
}


def _build_fvendas(df_raw: pd.DataFrame, df_194: pd.DataFrame) -> pd.DataFrame:
    """
    Constrói fVendas completo a partir dos cards 189 e 194.

    Fluxo:
      1. Renomear e tipar colunas do card 189
      2. Incluir produtores do card 194 sem vendas (Inativo em todos os meses)
      3. Gerar grid: todos produtores × todos meses (desde min até hoje)
      4. Forward-fill última_venda para calcular Status histórico
      5. Calcular Status_Anterior (shift de 1 mês por produtor)
      6. Retornar DataFrame com schema: Código, Produtor, Data, Valor,
         Status, Status_Anterior
    """
    needed = set(_COL_MAP_189.keys())
    missing = needed - set(df_raw.columns)
    if missing:
        raise DataNormalizationError(f"Card 189 faltando colunas: {missing}")

    df = df_raw[list(_COL_MAP_189.keys())].rename(columns=_COL_MAP_189).copy()

    df["produtor_id"]         = df["produtor_id"].astype("int64")
    df["mes"]                 = pd.to_datetime(df["mes"], errors="coerce")
    df["valor"]               = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)
    df["ultima_venda_no_mes"] = pd.to_datetime(df["ultima_venda_no_mes"], errors="coerce")

    # Mapa id → nome (card 189 — produtores com vendas)
    id_to_nome = (
        df[["produtor_id", "produtor_nome"]]
        .drop_duplicates("produtor_id")
        .set_index("produtor_id")["produtor_nome"]
        .to_dict()
    )

    # Incluir produtores do card 194 que não aparecem no card 189
    cod_col = [c for c in df_194.columns if "digo" in c or c == "Código"][0]
    nom_col = [c for c in df_194.columns if "rodutor" in c][0]
    dp_ids  = pd.to_numeric(df_194[cod_col], errors="coerce").dropna().astype("int64")
    dp_nomes = df_194.set_index(
        pd.to_numeric(df_194[cod_col], errors="coerce").astype("Int64")
    )[nom_col].to_dict()

    sem_vendas = {int(k): str(v) for k, v in dp_nomes.items()
                  if pd.notna(k) and int(k) not in id_to_nome}
    id_to_nome.update(sem_vendas)
    logger.info(
        f"fVendas | {len(df['produtor_id'].unique())} produtores com vendas + "
        f"{len(sem_vendas)} sem vendas = {len(id_to_nome)} total"
    )

    # --- Grid: todos produtores × todos meses ---
    min_month = df["mes"].min()
    now_month = pd.Timestamp.now().to_period("M").to_timestamp()
    all_months = (
        pd.period_range(min_month.to_period("M"), now_month.to_period("M"), freq="M")
        .to_timestamp()
    )

    grid = (
        pd.MultiIndex.from_product(
            [sorted(id_to_nome.keys()), list(all_months)],
            names=["produtor_id", "mes"],
        )
        .to_frame(index=False)
    )

    # Merge com dados do card
    grid = grid.merge(
        df[["produtor_id", "mes", "valor", "ultima_venda_no_mes"]],
        on=["produtor_id", "mes"],
        how="left",
    )
    grid["valor"] = grid["valor"].fillna(0.0)
    grid = grid.sort_values(["produtor_id", "mes"])

    # Forward-fill última_venda para meses sem vendas
    grid["ultima_venda"] = (
        grid.groupby("produtor_id")["ultima_venda_no_mes"]
        .transform(lambda x: x.ffill())
    )

    # Fim do mês para calcular dias_sem_venda
    grid["fim_mes"] = grid["mes"] + pd.offsets.MonthEnd(0)
    grid["dias_sem_venda"] = (grid["fim_mes"] - grid["ultima_venda"]).dt.days

    # --- Status histórico (vetorizado) ---
    has_venda = grid["ultima_venda"].notna()
    grid["Status"] = "Inativo"
    grid.loc[has_venda & (grid["dias_sem_venda"] <= 61),  "Status"] = "Ativo"
    grid.loc[has_venda & (grid["dias_sem_venda"] >  61)
                       & (grid["dias_sem_venda"] <= 121), "Status"] = "Pré-Churn"
    grid.loc[has_venda & (grid["dias_sem_venda"] > 121),  "Status"] = "Churn"

    # --- Meses anteriores à data de parceria → Status nulo ---
    parc_col_raw = next((c for c in df_194.columns if "Parceria" in c), None)
    if parc_col_raw:
        dp_parc = df_194[[cod_col, parc_col_raw]].copy()
        dp_parc["produtor_id"] = pd.to_numeric(dp_parc[cod_col], errors="coerce")
        dp_parc = dp_parc.dropna(subset=["produtor_id"])
        dp_parc["produtor_id"] = dp_parc["produtor_id"].astype("int64")
        dp_parc["data_parceria"] = (
            pd.to_datetime(dp_parc[parc_col_raw], errors="coerce", format="ISO8601")
            .dt.to_period("M").dt.to_timestamp()
        )
        dp_parc = dp_parc[["produtor_id", "data_parceria"]].drop_duplicates("produtor_id")
        patch_map = {pid: ts.to_period("M").to_timestamp() for pid, ts in _DATA_PARCERIA_PATCH.items()}
        null_mask = dp_parc["data_parceria"].isna()
        dp_parc.loc[null_mask, "data_parceria"] = dp_parc.loc[null_mask, "produtor_id"].map(patch_map)
        grid = grid.merge(dp_parc, on="produtor_id", how="left")
        grid.loc[grid["data_parceria"].notna() & (grid["mes"] < grid["data_parceria"]), "Status"] = None
        grid = grid.drop(columns=["data_parceria"])

    # --- Status_Anterior ---
    grid["Status_Anterior"] = grid.groupby("produtor_id")["Status"].shift(1)

    # --- Schema final ---
    grid["Produtor"] = grid["produtor_id"].map(id_to_nome)
    fvendas = grid.rename(columns={"produtor_id": "Código", "mes": "Data", "valor": "Valor"})[
        ["Código", "Produtor", "Data", "Valor", "Status", "Status_Anterior"]
    ].copy()

    fvendas["Código"] = fvendas["Código"].astype("int64")
    fvendas["Valor"]  = fvendas["Valor"].astype("float64")
    fvendas["Data"]   = pd.to_datetime(fvendas["Data"])

    fvendas = fvendas.sort_values(["Produtor", "Data"]).reset_index(drop=True)

    logger.info(f"fVendas | construído: {len(fvendas)} linhas")
    return fvendas


# ---------------------------------------------------------------------------
# Construção de dProdutores (card 194)
# ---------------------------------------------------------------------------
#
# Colunas esperadas do card 194:
#   Código, Produtor, CNPJ, Gestor, Cluster
#
# Data 1ª Venda: calculada a partir do card 189 (min ultima_venda_no_mes por produtor).

_COL_MAP_194 = {
    "Código":        "Código",
    "Produtor":      "Produtor",
    "Gestor":        "Gestor",
    "Cluster":       "Cluster",
    "Data Parceria": "Data Parceria",
}

# Datas de parceria ausentes no Metabase — preenchidas manualmente.
# Código 665 (Denis Ursulino) intencionalmente omitido.
_DATA_PARCERIA_PATCH: dict[int, pd.Timestamp] = {
    1:  pd.Timestamp("2023-01-11"),  # TMB Educação
    2:  pd.Timestamp("2022-01-31"),  # O Corpo Explica - 2023
    4:  pd.Timestamp("2023-01-01"),  # INTERUP
    43: pd.Timestamp("2023-01-01"),  # Aline Salvi
    51: pd.Timestamp("2022-05-02"),  # Danielle Furtado
    54: pd.Timestamp("2022-05-16"),  # Metaforando
    55: pd.Timestamp("2022-08-09"),  # André Lima
}


def _build_dprodutores(df_194: pd.DataFrame, df_189: pd.DataFrame) -> pd.DataFrame:
    """
    Constrói dProdutores a partir dos cards 194 e 189.

    Fluxo:
      1. Extrair Código, Produtor, Gestor, Cluster do card 194
      2. Calcular Data 1ª Venda = min(ultima_venda_no_mes) por produtor (card 189)
      3. Retornar DataFrame com schema:
         Código, Produtor, Cluster, Gestor, Data Parceria, Data 1ª Venda
    """
    needed_194 = set(_COL_MAP_194.keys())
    missing = needed_194 - set(df_194.columns)
    if missing:
        raise DataNormalizationError(f"Card 194 faltando colunas: {missing}")

    dp = df_194[list(_COL_MAP_194.keys())].copy()
    dp["Código"] = pd.to_numeric(dp["Código"], errors="coerce")
    dp = dp.dropna(subset=["Código"])
    dp["Código"]   = dp["Código"].astype("int64")
    dp["Gestor"]   = dp["Gestor"].astype(str).str.strip()
    dp["Produtor"] = dp["Produtor"].astype(str).str.strip()
    dp["Cluster"]  = (
        dp["Cluster"].astype(str).str.strip()
        .map(lambda v: CLUSTER_MAP.get(v, "Outros") if v not in ("nan", "") else "Outros")
    )

    # Data 1ª Venda — min da última venda registrada no mês mais antigo do produtor
    col_uv = "Mínimo de Efetivado em: Dia"
    if col_uv in df_189.columns:
        primeira = (
            df_189[["Produtor ID", col_uv]]
            .rename(columns={"Produtor ID": "Código", col_uv: "Data 1ª Venda"})
            .assign(**{"Data 1ª Venda": lambda x: pd.to_datetime(x["Data 1ª Venda"], errors="coerce")})
            .groupby("Código")["Data 1ª Venda"]
            .min()
            .reset_index()
        )
        dp = dp.merge(primeira, on="Código", how="left")
    else:
        dp["Data 1ª Venda"] = pd.NaT

    dp["Data Parceria"] = pd.to_datetime(dp["Data Parceria"], errors="coerce", format="ISO8601").dt.normalize()

    patch_mask = dp["Código"].isin(_DATA_PARCERIA_PATCH) & dp["Data Parceria"].isna()
    dp.loc[patch_mask, "Data Parceria"] = dp.loc[patch_mask, "Código"].map(_DATA_PARCERIA_PATCH)

    dp = dp[["Código", "Produtor", "Cluster", "Gestor", "Data Parceria", "Data 1ª Venda"]]
    dp = dp.drop_duplicates(subset="Código").reset_index(drop=True)

    logger.info(f"dProdutores | construído: {len(dp)} linhas")
    return dp


# ---------------------------------------------------------------------------
# Interface pública
# ---------------------------------------------------------------------------

def load_data(force_refresh: bool = False) -> DataPayload:
    """
    Carrega fVendas e dProdutores. Fluxo:
      1. Cache main válido (e não force_refresh) → usa fvendas.parquet + dprodutores.parquet
      2. Consulta Metabase (cards 189 e 194) → constrói DataFrames, promove main→backup, salva main
      3. Metabase falhou → tenta main expirado → tenta backup → DataUnavailableError
    """
    # --- Caminho 1: cache main válido ---
    if not force_refresh and _is_cache_valid():
        logger.info("Cache | usando cache válido: fvendas.parquet + dprodutores.parquet")
        try:
            vendas, produtores = _load_main()
            return _make_payload(vendas, produtores, source="cache",
                                 cache_file_path=str(_main_vendas()))
        except Exception as exc:
            logger.warning(f"Cache | main inválido ({exc}), regenerando via Metabase")

    # --- Caminho 2: Metabase ---
    try:
        token = _get_metabase_token()

        logger.info("Metabase | buscando card 189 (fVendas base)...")
        df_189 = _fetch_card_csv(token, card_id=189)

        logger.info("Metabase | buscando card 194 (dProdutores)...")
        df_194 = _fetch_card_csv(token, card_id=194)

        vendas     = _build_fvendas(df_189, df_194)
        produtores = _build_dprodutores(df_194, df_189)

        try:
            _save_to_cache(vendas, produtores)
        except Exception as save_exc:
            logger.warning(f"Cache | falha ao salvar (não fatal): {save_exc}")
        return _make_payload(vendas, produtores, source="metabase",
                             cache_file_path=str(_main_vendas()))

    except MetabaseError as exc:
        logger.warning(f"Metabase | falha: {exc}")
        # Fallback 1: main expirado
        if _main_cache_complete():
            logger.warning("Cache | usando main expirado como fallback")
            try:
                vendas, produtores = _load_main()
                return _make_payload(vendas, produtores, source="cache",
                                     cache_file_path=str(_main_vendas()))
            except Exception as load_exc:
                logger.warning(f"Cache | main ilegível ({load_exc}), tentando backup")
        # Fallback 2: backup
        if _backup_cache_complete():
            logger.warning("Cache | usando backup como fallback")
            try:
                vendas, produtores = _load_backup()
                return _make_payload(vendas, produtores, source="cache",
                                     cache_file_path=str(_bck_vendas()))
            except Exception as bck_exc:
                logger.warning(f"Cache | backup ilegível ({bck_exc})")
        raise DataUnavailableError(
            "Metabase inacessível e sem cache local disponível.\n"
            "Verifique METABASE_URL, METABASE_USER e METABASE_PASSWORD no .env"
        ) from exc

def _make_payload(
    vendas: pd.DataFrame,
    produtores: pd.DataFrame,
    source: Literal["metabase", "cache"],
    cache_file_path: str,
) -> DataPayload:
    if "Data" not in vendas.columns:
        raise DataNormalizationError(
            f"Schema inválido: coluna 'Data' ausente (colunas encontradas: {list(vendas.columns)})"
        )
    data_reference_date = vendas["Data"].max().date()
    logger.info(
        f"DataPayload | source={source} | "
        f"vendas={len(vendas)} linhas | produtores={len(produtores)} linhas | "
        f"data_ref={data_reference_date}"
    )
    return DataPayload(
        vendas=vendas,
        produtores=produtores,
        source=source,
        loaded_at=datetime.utcnow(),
        cache_file_path=cache_file_path,
        data_reference_date=data_reference_date,
    )
