"""
importers/hubspot_closer.py — importa o Pipeline de Closer do HubSpot e salva em parquet.

Uso standalone:
    python -m importers.hubspot_closer

Uso como módulo (pelo DataAgent):
    from importers.hubspot_closer import fetch_closer_pipeline
    df = fetch_closer_pipeline()
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
HUBSPOT_BASE_URL = "https://api.hubapi.com"
PIPELINE_ID = "832504973"
OUTPUT_PATH = Path("data/hubspot/hs_closer_pipeline.parquet")

PROPERTIES = [
    # Identificação
    "codigo_produtor",
    "dealname",
    "gestor_contas",
    "cluster",
    "closer",
    "hubspot_owner_id",
    # Resultado
    "dealstage",
    "closedate",
    "createdate",
    "motivo_de_perda",
    "classificacao_venda",
    "amount",
    # Timeline — datas de entrada em cada estágio
    "hs_v2_date_entered_1235306848",  # Novo Qualificado
    "hs_v2_date_entered_1235306849",  # Cadência
    "hs_v2_date_entered_1235306850",  # Interações
    "hs_v2_date_entered_1235306851",  # Agendamento
    "hs_v2_date_entered_1235306852",  # Reunião
    "hs_v2_date_entered_1235306853",  # Aguardando Cadastro
    "hs_v2_date_entered_1235306854",  # Ganho
    "hs_v2_date_entered_1235226499",  # Perda
    # Reunião
    "data_da_reuniao_agendada",
    "horario_da_reuniao_agendada",
    "canal_da_reuniao",
    "check_reuniao",
    "taxa_de_noshow",
    "taxa_de_show",
    # Atividade de prospecção
    "data_do_primeiro_contatado",
    "data_da_1a_conexao",
    "canal_da_1a_conexao",
    "quantidade_de_dias_de_atividade",
    "numero_de_contatos_no_dia",
    "contacte_rate",
    "taxa_de_conexao",
    "taxa_de_agendamento",
    "taxa_de_fechamento",
    # Enriquecimento
    "numero_de_telefone",
    "lead_score_final",
    "jornada_do_heroi",
    # Perguntas de qualificação
    "qual_seu_modelo_de_vendas_",
    "qual_e_o_seu_principal_objetivo_ao_buscar_uma_parceria_com_a_tmb_",
    "qual_estrutura_da_sua_equipe_atualmente_",
    "qual_sua_experiencia_com_boleto_parcelado_",
    "onde_voce_vende_os_cursos_online_",
]

STAGE_NAMES = {
    "1235306848": "Novo Qualificado",
    "1235306849": "Cadência",
    "1235306850": "Interações",
    "1235306851": "Agendamento",
    "1235306852": "Reunião",
    "1235306853": "Aguardando Cadastro",
    "1235306854": "Ganho",
    "1235226499": "Perda",
}


# ---------------------------------------------------------------------------
# Extração via API
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}


def fetch_all_deals() -> list[dict]:
    """Pagina pelo endpoint de search e retorna todos os deals do pipeline."""
    all_deals: list[dict] = []
    after: str | None = None
    page = 0

    while True:
        page += 1
        body: dict = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "pipeline",
                    "operator": "EQ",
                    "value": PIPELINE_ID,
                }]
            }],
            "properties": PROPERTIES,
            "limit": 100,
        }
        if after:
            body["after"] = after

        resp = requests.post(
            f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/search",
            json=body,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("results", [])
        all_deals.extend(batch)
        print(f"[HubSpot] Página {page}: {len(batch)} deals")

        if "paging" in data and "next" in data["paging"]:
            after = data["paging"]["next"]["after"]
        else:
            break

    return all_deals


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _prop(deal: dict, key: str):
    """Extrai propriedade do objeto deal; retorna None se ausente."""
    return deal.get("properties", {}).get(key) or None


def _to_dt(value) -> pd.Timestamp | None:
    """Converte string ISO para Timestamp; retorna NaT se nulo/inválido."""
    if not value:
        return pd.NaT
    try:
        return pd.Timestamp(value, tz="UTC").tz_localize(None)
    except Exception:
        return pd.NaT


def _to_date(value) -> pd.Timestamp | None:
    if not value:
        return pd.NaT
    try:
        return pd.Timestamp(value).normalize()
    except Exception:
        return pd.NaT


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    if f is None:
        return None
    return int(f)


def normalize_deals(raw_deals: list[dict]) -> pd.DataFrame:
    """Transforma a lista de objetos brutos da API no schema final."""
    rows = []
    for deal in raw_deals:
        p = deal.get("properties", {})

        def prop(key):
            v = p.get(key)
            return v if v not in (None, "", "None") else None

        row = {
            # ── Identificação ───────────────────────────────────────────────
            "deal_id":          str(deal["id"]),
            "codigo_produtor":  prop("codigo_produtor"),
            "dealname":         prop("dealname"),
            "gestor_contas":    prop("gestor_contas"),
            "cluster":          prop("cluster"),
            "closer":           prop("closer"),

            # ── Resultado ───────────────────────────────────────────────────
            "dealstage_id":          prop("dealstage"),
            "motivo_de_perda":       prop("motivo_de_perda"),
            "classificacao_venda":   prop("classificacao_venda"),
            "amount":                _to_float(prop("amount")),

            # ── Timeline ────────────────────────────────────────────────────
            "dt_criacao":              _to_dt(prop("createdate")),
            "dt_novo_qualificado":     _to_dt(prop("hs_v2_date_entered_1235306848")),
            "dt_cadencia":             _to_dt(prop("hs_v2_date_entered_1235306849")),
            "dt_interacoes":           _to_dt(prop("hs_v2_date_entered_1235306850")),
            "dt_agendamento":          _to_dt(prop("hs_v2_date_entered_1235306851")),
            "dt_reuniao":              _to_dt(prop("hs_v2_date_entered_1235306852")),
            "dt_aguardando_cadastro":  _to_dt(prop("hs_v2_date_entered_1235306853")),
            "dt_ganho":                _to_dt(prop("hs_v2_date_entered_1235306854")),
            "dt_perda":                _to_dt(prop("hs_v2_date_entered_1235226499")),
            "dt_fechamento":           _to_dt(prop("closedate")),

            # ── Reunião ─────────────────────────────────────────────────────
            "data_reuniao_agendada":  _to_date(prop("data_da_reuniao_agendada")),
            "canal_reuniao":          prop("canal_da_reuniao"),
            "noshow":                 _to_int(prop("taxa_de_noshow")),

            # ── Prospecção ──────────────────────────────────────────────────
            "dt_primeiro_contato":  _to_dt(prop("data_do_primeiro_contatado")),
            "dt_1a_conexao":        _to_date(prop("data_da_1a_conexao")),
            "canal_1a_conexao":     prop("canal_da_1a_conexao"),
            "qtd_dias_atividade":   _to_int(prop("quantidade_de_dias_de_atividade")),
            "qtd_contatos_por_dia": _to_int(prop("numero_de_contatos_no_dia")),
            "contacte_rate":        _to_float(prop("contacte_rate")),
            "taxa_conexao":         _to_int(prop("taxa_de_conexao")),
            "taxa_agendamento":     _to_int(prop("taxa_de_agendamento")),
            "taxa_fechamento":      _to_int(prop("taxa_de_fechamento")),

            # ── Metadados ───────────────────────────────────────────────────
            "telefone":   prop("numero_de_telefone"),
            "lead_score": _to_float(prop("lead_score_final")),

            # ── Perguntas de qualificação ────────────────────────────────────
            "modelo_vendas":         prop("qual_seu_modelo_de_vendas_"),
            "objetivo_parceria_tmb": prop("qual_e_o_seu_principal_objetivo_ao_buscar_uma_parceria_com_a_tmb_"),
            "estrutura_operacional": prop("qual_estrutura_da_sua_equipe_atualmente_"),
            "experiencia_boleto":    prop("qual_sua_experiencia_com_boleto_parcelado_"),
            "onde_vende":            prop("onde_voce_vende_os_cursos_online_"),
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    # ── Colunas derivadas ────────────────────────────────────────────────────
    df["ganho"] = (df["dealstage_id"] == "1235306854").astype(int)
    df["reuniao_realizada"] = df["dt_reuniao"].notna().astype(int)
    df["dealstage_nome"] = df["dealstage_id"].map(STAGE_NAMES).fillna("Desconhecido")

    df["mes_ano"] = df["dt_criacao"].dt.to_period("M").astype(str)
    # to_period retorna "NaT" string para NaT — limpar
    df.loc[df["dt_criacao"].isna(), "mes_ano"] = None

    df["dias_qualificado_ate_reuniao"] = (
        df["dt_reuniao"] - df["dt_novo_qualificado"]
    ).dt.days

    df["dias_reuniao_ate_ganho"] = (
        df["dt_ganho"] - df["dt_reuniao"]
    ).dt.days

    df["tempo_total_ciclo_dias"] = (
        df["dt_ganho"].fillna(df["dt_perda"]) - df["dt_criacao"]
    ).dt.days

    df["dt_extracao"] = pd.Timestamp.now()

    # ── Reordenar colunas por grupo ──────────────────────────────────────────
    column_order = [
        # Identificação
        "deal_id", "codigo_produtor", "dealname", "gestor_contas", "cluster", "closer",
        # Resultado
        "dealstage_id", "dealstage_nome", "ganho", "motivo_de_perda",
        "classificacao_venda", "amount",
        # Timeline
        "dt_criacao", "dt_novo_qualificado", "dt_cadencia", "dt_interacoes",
        "dt_agendamento", "dt_reuniao", "dt_aguardando_cadastro", "dt_ganho",
        "dt_perda", "dt_fechamento",
        # Reunião
        "reuniao_realizada", "data_reuniao_agendada", "canal_reuniao", "noshow",
        "dias_qualificado_ate_reuniao", "dias_reuniao_ate_ganho", "tempo_total_ciclo_dias",
        # Prospecção
        "dt_primeiro_contato", "dt_1a_conexao", "canal_1a_conexao",
        "qtd_dias_atividade", "qtd_contatos_por_dia", "contacte_rate",
        "taxa_conexao", "taxa_agendamento", "taxa_fechamento",
        # Perguntas de qualificação
        "modelo_vendas", "objetivo_parceria_tmb", "estrutura_operacional",
        "experiencia_boleto", "onde_vende",
        # Metadados
        "telefone", "lead_score", "mes_ano", "dt_extracao",
    ]
    # Mantém apenas colunas que existam (segurança contra API changes)
    df = df[[c for c in column_order if c in df.columns]]

    return df


# ---------------------------------------------------------------------------
# Função pública — importável pelo DataAgent
# ---------------------------------------------------------------------------

def fetch_closer_pipeline() -> pd.DataFrame:
    """Extrai, normaliza e retorna o DataFrame do Pipeline de Closer."""
    print("[HubSpot] Buscando deals do Pipeline de Closer...")
    raw = fetch_all_deals()
    print(f"[HubSpot] Total extraído: {len(raw)} deals")

    print("[HubSpot] Normalizando colunas...")
    df = normalize_deals(raw)

    # Estatísticas de log
    n = len(df)
    n_reuniao = int(df["reuniao_realizada"].sum())
    n_ganho = int(df["ganho"].sum())
    n_perda = int(df["dt_perda"].notna().sum())

    pct = lambda x: f"{x / n * 100:.1f}%" if n else "0%"
    print(f"[HubSpot] Deals com reunião realizada: {n_reuniao} ({pct(n_reuniao)})")
    print(f"[HubSpot] Deals ganhos: {n_ganho} ({pct(n_ganho)})")
    print(f"[HubSpot] Deals perdidos: {n_perda} ({pct(n_perda)})")

    return df


# ---------------------------------------------------------------------------
# Entry point para execução direta
# ---------------------------------------------------------------------------

def main():
    df = fetch_closer_pipeline()

    # Power BI rejeita tipo Arrow 'null' — colunas 100% vazias ficam sem tipo
    # definido no schema e causam "dataType não pode ser nulo" na importação.
    for col in df.columns:
        if df[col].isna().all():
            df[col] = df[col].astype(str).replace("nan", "")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"[HubSpot] Salvo em {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
