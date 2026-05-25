"""
importers/hubspot_growth.py — importa os Leads do funil de Growth do HubSpot.

Uso standalone:
    python -m importers.hubspot_growth

Uso como módulo (pelo DataAgent):
    from importers.hubspot_growth import fetch_growth_leads
    df = fetch_growth_leads()
"""

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
HUBSPOT_BASE_URL = "https://api.hubapi.com"
PIPELINE_CLOSER_ID = "832504973"
OUTPUT_PATH = Path("data/hubspot/hs_growth_leads.parquet")
CACHE_ASSOC_PATH = Path("data/hubspot/associations_cache.json")

# Pipelines
PIPELINES = {
    "lead-pipeline-id": "Leads TMB",
    "leads-tmr-pipeline": "Leads TMR",
}

# Mapeamento de stages — Leads TMB
STAGES_TMB = {
    "1307449126":                       "Novo Lead",
    "new_stage_id_1318266061":          "Backlog - Leadscore",
    "attempting_stage_id_745667965":    "Ativado",
    "connected_stage_id_2058487257":    "Interagiu",
    "1270709937":                       "Agendado",
    "qualified_stage_id_233247981":     "Qualificado",
    "unqualified_stage_id_1675714327":  "Desqualificado",
}

# Mapeamento de stages — Leads TMR
STAGES_TMR = {
    "1242729229": "Novo",
    "1242729230": "Tentativa",
    "1242729231": "Conectado",
    "1242729232": "Qualificado",
    "1242729233": "Desqualificado",
}

ALL_STAGES = {**STAGES_TMB, **STAGES_TMR}

PROPERTIES = [
    # Identificação
    "hs_lead_name",
    "firstname",
    "email",
    "phone",
    "hs_createdate",
    "hs_lastmodifieddate",
    "hs_pipeline",
    "hs_pipeline_stage",
    "id_do_registro_lp",
    "hubspot_owner_id",
    "hs_primary_contact_id",
    # Qualificação — respostas do formulário
    "voce_vende_cursos_online_e_ou_mentorias_e_ou_imersoes_",
    "qual_area_de_atuacao_do_seu_projeto_",
    "faixa_de_faturamento_do_ultimo_ano",
    "quao_rapido_deseja_implementar_o_boleto_parcelado_",
    # LeadScore
    "score_vende_info",
    "score_area_atuacao",
    "score_faturamento_ano",
    "score_tempo_impl",
    "score_total_lp",
    "lead_score_final",
    # Classificação
    "cluster_leadscore",
    "cluster_faturamento_da_empresa",
    # Timeline Leads TMB — datas de entrada
    "hs_v2_date_entered_1307449126",
    "hs_v2_date_entered_new_stage_id_1318266061",
    "hs_v2_date_entered_attempting_stage_id_745667965",
    "hs_v2_date_entered_connected_stage_id_2058487257",
    "hs_v2_date_entered_1270709937",
    "hs_v2_date_entered_qualified_stage_id_233247981",
    "hs_v2_date_entered_unqualified_stage_id_1675714327",
    # Timeline Leads TMR — datas de entrada
    "hs_v2_date_entered_1242729229",
    "hs_v2_date_entered_1242729230",
    "hs_v2_date_entered_1242729231",
    "hs_v2_date_entered_1242729232",
    "hs_v2_date_entered_1242729233",
    # Tempo acumulado em cada stage
    "hs_v2_cumulative_time_in_1307449126",
    "hs_v2_cumulative_time_in_attempting_stage_id_745667965",
    "hs_v2_cumulative_time_in_connected_stage_id_2058487257",
    "hs_v2_cumulative_time_in_qualified_stage_id_233247981",
    # UTMs de origem
    "utm_source_last_hr",
    "utm_campaign_last_hr",
    "utm_medium_last_hr",
    "utm_content_last_hr",
    "utm_term_last_hr",
    # Flags e metadados
    "lead_ativado_por_ia",
    "auxiliar_criacao_manual_de_closer",
    "hs_lead_is_disqualified",
    "hs_lead_disqualification_reason",
    "hs_lead_associated_deals_count",
    "hs_lead_closed_won_deals_amount",
    "data_de_fechamento__tmb",
]

# Score thresholds (referência)
SCORE_THRESHOLDS = {
    "A": (202, float("inf")),
    "B": (153, 201.99),
    "C": (0, 152.99),
    "D": None,
}


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}


# ---------------------------------------------------------------------------
# Extração — leads
# ---------------------------------------------------------------------------

def fetch_all_leads() -> list[dict]:
    """Pagina /crm/v3/objects/leads e retorna todos os leads do portal."""
    all_leads: list[dict] = []
    after: str | None = None

    print("[HubSpot Growth] Buscando leads do objeto Leads...")
    while True:
        params: dict = {
            "limit": 100,
            "properties": ",".join(PROPERTIES),
        }
        if after:
            params["after"] = after

        resp = requests.get(
            f"{HUBSPOT_BASE_URL}/crm/v3/objects/leads",
            headers=_headers(),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("results", [])
        all_leads.extend(batch)
        print(f"[HubSpot Growth] {len(all_leads)} leads extraídos...")

        if "paging" in data and "next" in data["paging"]:
            after = data["paging"]["next"]["after"]
        else:
            break

    print(f"[HubSpot Growth] Total extraído: {len(all_leads)} leads")
    return all_leads


# ---------------------------------------------------------------------------
# Enriquecimento — deal_id_closer com cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    if CACHE_ASSOC_PATH.exists():
        with open(CACHE_ASSOC_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_ASSOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_ASSOC_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def get_closer_deal_id(lead_id: str) -> str | None:
    """Retorna o deal_id do Pipeline de Closer associado ao lead."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/leads/{lead_id}/associations/deals"
    r = requests.get(url, headers=_headers(), timeout=15)
    if r.status_code != 200:
        return None

    deal_ids = [item["id"] for item in r.json().get("results", [])]
    if not deal_ids:
        return None

    closer_deals = []
    for deal_id in deal_ids:
        r2 = requests.get(
            f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/{deal_id}",
            headers=_headers(),
            params={"properties": "pipeline,createdate"},
            timeout=15,
        )
        if r2.status_code == 200:
            props = r2.json().get("properties", {})
            if props.get("pipeline") == PIPELINE_CLOSER_ID:
                closer_deals.append({
                    "deal_id": deal_id,
                    "createdate": props.get("createdate", ""),
                })

    if not closer_deals:
        return None

    closer_deals.sort(key=lambda x: x["createdate"], reverse=True)
    return closer_deals[0]["deal_id"]


def enrich_with_closer_deals(leads: list[dict], partial_output_path=None) -> list[dict]:
    """Adiciona deal_id_closer a cada lead, usando cache para evitar reprocessamento."""
    cache = _load_cache()
    print(f"[HubSpot Growth] Cache carregado: {len(cache)} associações")

    processed = []
    for i, lead in enumerate(leads):
        lead_id = lead["id"]
        if lead_id in cache:
            lead["deal_id_closer"] = cache[lead_id]
        else:
            lead["deal_id_closer"] = get_closer_deal_id(lead_id)
            cache[lead_id] = lead["deal_id_closer"]
            time.sleep(0.05)  # rate limit

        processed.append(lead)

        if i % 50 == 0:
            _save_cache(cache)
            print(f"[HubSpot Growth]   {i}/{len(leads)} leads processados")
            if partial_output_path and i > 0:
                partial_df = normalize_leads(list(processed))
                partial_output_path.parent.mkdir(parents=True, exist_ok=True)
                partial_df.to_parquet(partial_output_path, index=False)
                print(f"[HubSpot Growth]   Parquet parcial salvo ({len(processed)} leads)")

    _save_cache(cache)
    return leads


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _to_dt(value) -> pd.Timestamp:
    if not value:
        return pd.NaT
    try:
        return pd.Timestamp(value, tz="UTC").tz_localize(None)
    except Exception:
        return pd.NaT


def _to_date(value) -> pd.Timestamp:
    if not value:
        return pd.NaT
    try:
        return pd.Timestamp(value).normalize()
    except Exception:
        return pd.NaT


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return None if f is None else int(f)


_owner_cache: dict[str, str] = {}


def _resolve_owner(owner_id: str | None) -> str | None:
    if not owner_id:
        return None
    if owner_id in _owner_cache:
        return _owner_cache[owner_id]
    try:
        r = requests.get(
            f"{HUBSPOT_BASE_URL}/crm/v3/owners/{owner_id}",
            headers=_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            name = f"{data.get('firstName', '')} {data.get('lastName', '')}".strip()
            _owner_cache[owner_id] = name or owner_id
        else:
            _owner_cache[owner_id] = owner_id
    except Exception:
        _owner_cache[owner_id] = owner_id
    return _owner_cache[owner_id]


def _calc_status(stage_id: str | None) -> str:
    if stage_id == "qualified_stage_id_233247981":
        return "Ganho"
    if stage_id in ("unqualified_stage_id_1675714327", "1242729233"):
        return "Perda"
    return "Aberto"


def normalize_leads(raw_leads: list[dict]) -> pd.DataFrame:
    """Transforma a lista de objetos brutos da API no schema final."""
    rows = []
    for lead in raw_leads:
        p = lead.get("properties", {})

        def prop(key):
            v = p.get(key)
            return v if v not in (None, "", "None") else None

        row = {
            # ── Identificação ─────────────────────────────────────────────
            "lead_id":               str(lead["id"]),
            "nome":                  prop("hs_lead_name") or prop("firstname"),
            "email":                 prop("email"),
            "telefone":              prop("phone"),
            "contact_id":            prop("hs_primary_contact_id"),
            "deal_id_closer":        lead.get("deal_id_closer"),
            "pipeline_id":           prop("hs_pipeline"),
            "stage_atual_id":        prop("hs_pipeline_stage"),
            "dt_criacao":            _to_dt(prop("hs_createdate")),
            "dt_ultima_atualizacao": _to_dt(prop("hs_lastmodifieddate")),
            "dt_fechamento_tmb":     _to_dt(prop("data_de_fechamento__tmb")),
            "proprietario":          _resolve_owner(prop("hubspot_owner_id")),

            # ── Qualificação e LeadScore ───────────────────────────────────
            "vende_info":              prop("voce_vende_cursos_online_e_ou_mentorias_e_ou_imersoes_"),
            "area_atuacao":            prop("qual_area_de_atuacao_do_seu_projeto_"),
            "faturamento_ultimo_ano":  prop("faixa_de_faturamento_do_ultimo_ano"),
            "tempo_implementacao":     prop("quao_rapido_deseja_implementar_o_boleto_parcelado_"),
            "score_vende_info":        _to_float(prop("score_vende_info")),
            "score_area_atuacao":      _to_float(prop("score_area_atuacao")),
            "score_faturamento_ano":   _to_float(prop("score_faturamento_ano")),
            "score_tempo_implantacao": _to_float(prop("score_tempo_impl")),   # nome diferente do Contact
            "score_total_lp":          _to_float(prop("score_total_lp")),
            "cluster_leadscore":       prop("cluster_leadscore"),
            "cluster_faturamento":     prop("cluster_faturamento_da_empresa"),

            # ── Flags de qualificação ──────────────────────────────────────
            "motivo_desqualificacao":       prop("hs_lead_disqualification_reason"),

            # ── Timeline TMB ───────────────────────────────────────────────
            "dt_novo_lead":      _to_dt(prop("hs_v2_date_entered_1307449126")),
            "dt_backlog_leadscore": _to_dt(prop("hs_v2_date_entered_new_stage_id_1318266061")),
            "dt_ativado":        _to_dt(prop("hs_v2_date_entered_attempting_stage_id_745667965")),
            "dt_interagiu":      _to_dt(prop("hs_v2_date_entered_connected_stage_id_2058487257")),
            "dt_agendado":       _to_dt(prop("hs_v2_date_entered_1270709937")),
            "dt_qualificado":    _to_dt(prop("hs_v2_date_entered_qualified_stage_id_233247981")),
            "dt_desqualificado": _to_dt(prop("hs_v2_date_entered_unqualified_stage_id_1675714327")),

            # ── Timeline TMR ───────────────────────────────────────────────
            "dt_tmr_novo":          _to_dt(prop("hs_v2_date_entered_1242729229")),
            "dt_tmr_tentativa":     _to_dt(prop("hs_v2_date_entered_1242729230")),
            "dt_tmr_conectado":     _to_dt(prop("hs_v2_date_entered_1242729231")),
            "dt_tmr_qualificado":   _to_dt(prop("hs_v2_date_entered_1242729232")),
            "dt_tmr_desqualificado": _to_dt(prop("hs_v2_date_entered_1242729233")),

            # ── Tempo acumulado ────────────────────────────────────────────
            "tempo_em_ativado_ms":   _to_float(prop("hs_v2_cumulative_time_in_attempting_stage_id_745667965")),
            "tempo_em_interagiu_ms": _to_float(prop("hs_v2_cumulative_time_in_connected_stage_id_2058487257")),

            # ── UTMs ───────────────────────────────────────────────────────
            "utm_source":   prop("utm_source_last_hr"),
            "utm_campaign": prop("utm_campaign_last_hr"),
            "utm_medium":   prop("utm_medium_last_hr"),
            "utm_content":  prop("utm_content_last_hr"),
            "utm_term":     prop("utm_term_last_hr"),

            # ── Metadados ──────────────────────────────────────────────────
            "lead_ativado_por_ia":    prop("lead_ativado_por_ia"),
            "criacao_manual_closer":  prop("auxiliar_criacao_manual_de_closer"),
            "qtd_deals_associados":   _to_int(prop("hs_lead_associated_deals_count")),
            "valor_deals_ganhos":     _to_float(prop("hs_lead_closed_won_deals_amount")),
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    print("[HubSpot Growth] Calculando classificações...")

    # ── Pipeline, stage e status legíveis ───────────────────────────────────
    df["pipeline_nome"] = df["pipeline_id"].map(PIPELINES).fillna("Desconhecido")
    df["stage_atual_nome"] = df["stage_atual_id"].map(ALL_STAGES).fillna("Desconhecido")
    df["status_lead"] = df["stage_atual_id"].apply(_calc_status)

    # ── Métricas de tempo derivadas ──────────────────────────────────────────
    df["dias_novo_ate_ativado"]       = (df["dt_ativado"]       - df["dt_novo_lead"]).dt.days
    df["dias_ativado_ate_interagiu"]  = (df["dt_interagiu"]     - df["dt_ativado"]).dt.days
    df["dias_interagiu_ate_agendado"] = (df["dt_agendado"]      - df["dt_interagiu"]).dt.days
    df["dias_novo_ate_qualificado"]   = (df["dt_qualificado"]   - df["dt_novo_lead"]).dt.days
    df["dias_novo_ate_desqualificado"]= (df["dt_desqualificado"]- df["dt_novo_lead"]).dt.days

    df["dt_extracao"] = pd.Timestamp.now()

    # ── Reordenar colunas ────────────────────────────────────────────────────
    column_order = [
        # Identificação
        "lead_id", "nome", "email", "telefone", "contact_id", "deal_id_closer",
        "pipeline_id", "pipeline_nome", "stage_atual_id", "stage_atual_nome",
        "status_lead", "proprietario",
        "dt_criacao", "dt_ultima_atualizacao", "dt_fechamento_tmb",
        # Qualificação e LeadScore
        "vende_info", "area_atuacao", "faturamento_ultimo_ano", "tempo_implementacao",
        "score_vende_info", "score_area_atuacao", "score_faturamento_ano",
        "score_tempo_implantacao", "score_total_lp",
        "cluster_leadscore", "cluster_faturamento",
        "motivo_desqualificacao",
        # Timeline TMB
        "dt_novo_lead", "dt_backlog_leadscore", "dt_ativado", "dt_interagiu",
        "dt_agendado", "dt_qualificado", "dt_desqualificado",
        # Timeline TMR
        "dt_tmr_novo", "dt_tmr_tentativa", "dt_tmr_conectado",
        "dt_tmr_qualificado", "dt_tmr_desqualificado",
        # Métricas de tempo derivadas
        "dias_novo_ate_ativado", "dias_ativado_ate_interagiu",
        "dias_interagiu_ate_agendado", "dias_novo_ate_qualificado",
        "dias_novo_ate_desqualificado",
        "tempo_em_ativado_ms", "tempo_em_interagiu_ms",
        # UTMs
        "utm_source", "utm_campaign", "utm_medium", "utm_content", "utm_term",
        # Metadados
        "lead_ativado_por_ia", "criacao_manual_closer",
        "qtd_deals_associados", "valor_deals_ganhos", "dt_extracao",
    ]
    df = df[[c for c in column_order if c in df.columns]]

    return df


# ---------------------------------------------------------------------------
# Função pública — importável pelo DataAgent
# ---------------------------------------------------------------------------

def fetch_growth_leads() -> pd.DataFrame:
    """Extrai, enriquece e normaliza o DataFrame do funil de Growth (objeto Leads)."""
    raw = fetch_all_leads()
    raw = enrich_with_closer_deals(raw, partial_output_path=OUTPUT_PATH)

    print("[HubSpot Growth] Normalizando colunas...")
    df = normalize_leads(raw)

    # ── Resumo ───────────────────────────────────────────────────────────────
    n = len(df)
    pct = lambda x: f"{x / n * 100:.1f}%" if n else "0%"

    n_tmb    = int((df["pipeline_nome"] == "Leads TMB").sum())
    n_tmr    = int((df["pipeline_nome"] == "Leads TMR").sum())
    n_ganho  = int((df["status_lead"] == "Ganho").sum())
    n_perda  = int((df["status_lead"] == "Perda").sum())
    n_closer = int(df["deal_id_closer"].notna().sum())

    print(f"[HubSpot Growth] --- Resumo ---")
    print(f"[HubSpot Growth] Total de leads: {n}")
    print(f"[HubSpot Growth] Pipeline Leads TMB: {n_tmb} | Leads TMR: {n_tmr}")
    print(f"[HubSpot Growth] Ganhos: {n_ganho} ({pct(n_ganho)}) | Perdas: {n_perda} ({pct(n_perda)})")
    print(f"[HubSpot Growth] Com deal_id_closer: {n_closer} ({pct(n_closer)})")

    return df


# ---------------------------------------------------------------------------
# Entry point para execução direta
# ---------------------------------------------------------------------------

def main():
    df = fetch_growth_leads()

    # Power BI rejeita tipo Arrow 'null' — colunas 100% vazias ficam sem tipo
    # definido no schema e causam "dataType nao pode ser nulo" na importacao.
    for col in df.columns:
        if df[col].isna().all():
            df[col] = df[col].astype(str).replace("nan", "")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"[HubSpot Growth] Salvo em {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
