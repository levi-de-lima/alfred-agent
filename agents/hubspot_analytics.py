"""
hubspot_analytics.py — funções analíticas HubSpot para o Alfred.

Isoladas do analytics_agent.py para manter a lógica HubSpot coesa
sem quebrar o ToolContext compartilhado — joins cross-data com
vendas/produtores continuam nativos (mesmo ToolContext, mesmo loop ReAct).
"""
from __future__ import annotations

import pandas as pd
import numpy as np

# QueryPlan é importado de analytics_agent. Para evitar import circular,
# analytics_agent deve importar este módulo com lazy import dentro de _dispatch.
from agents.analytics_agent import QueryPlan


# ---------------------------------------------------------------------------
# Closer Pipeline
# ---------------------------------------------------------------------------

def _calc_closer_pipeline(
    plan: QueryPlan,
    hs_closer: pd.DataFrame,
) -> tuple[dict, list[dict], list[str]]:
    ops: list[str] = ["Analisando Pipeline de Closer (HubSpot)"]

    if hs_closer.empty:
        return (
            {"aviso": "Dados do Pipeline de Closer ainda não disponíveis. Execute `python -m importers.refresh` para importar."},
            [],
            ops + ["hs_closer vazio — importer não executado"],
        )

    df = hs_closer.copy()

    closer_filter = plan.get_filter("closer")
    if closer_filter:
        df = df[df["closer"].str.contains(closer_filter, case=False, na=False)]
        ops.append(f"Filtro closer: '{closer_filter}' → {len(df)} deals")

    cluster_filter = plan.get_filter("cluster")
    if cluster_filter:
        df = df[df["cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtro cluster: '{cluster_filter}' → {len(df)} deals")

    stage_filter = plan.get_filter("dealstage")
    if stage_filter:
        df = df[df["dealstage_nome"].str.contains(stage_filter, case=False, na=False)]
        ops.append(f"Filtro estágio: '{stage_filter}' → {len(df)} deals")

    month_start = plan.get_filter("month_start")
    month_end   = plan.get_filter("month_end")
    month = plan.filters.get("month")
    year  = plan.filters.get("year")
    if month and year:
        try:
            m, y = int(month), int(year)
            df = df[(df["dt_criacao"].dt.month == m) & (df["dt_criacao"].dt.year == y)]
            ops.append(f"Filtro período: {m:02d}/{y} → {len(df)} deals")
        except (ValueError, TypeError):
            pass
    elif month_start or month_end:
        if month_start:
            df = df[df["dt_criacao"] >= pd.Timestamp(month_start)]
        if month_end:
            df = df[df["dt_criacao"] <= pd.Timestamp(month_end) + pd.offsets.MonthEnd(0)]
        ops.append(f"Filtro período: {month_start} → {month_end} → {len(df)} deals")

    n = len(df)
    if n == 0:
        return {"aviso": "Nenhum deal encontrado com os filtros aplicados."}, [], ops

    n_ganho   = int(df["ganho"].sum()) if "ganho" in df.columns else 0
    n_perda   = int(df["dt_perda"].notna().sum()) if "dt_perda" in df.columns else 0
    n_reuniao = int(df["reuniao_realizada"].sum()) if "reuniao_realizada" in df.columns else 0
    n_noshow  = int(df["noshow"].sum()) if "noshow" in df.columns else 0

    taxa_reuniao       = round(n_reuniao / n * 100, 1) if n else 0
    taxa_ganho         = round(n_ganho / n * 100, 1) if n else 0
    taxa_ganho_reuniao = round(n_ganho / n_reuniao * 100, 1) if n_reuniao else 0
    taxa_noshow        = round(n_noshow / n_reuniao * 100, 1) if n_reuniao else 0

    ciclo_medio = None
    if "tempo_total_ciclo_dias" in df.columns:
        ciclo_medio = df["tempo_total_ciclo_dias"].dropna().median()
        ciclo_medio = round(float(ciclo_medio), 1) if not pd.isna(ciclo_medio) else None

    estagios: list[dict] = []
    if "dealstage_nome" in df.columns:
        estagios = (
            df.groupby("dealstage_nome", dropna=False)
            .size()
            .reset_index(name="total")
            .sort_values("total", ascending=False)
            .to_dict("records")
        )

    por_closer: list[dict] = []
    if "closer" in df.columns:
        grp = df.groupby("closer", dropna=False).agg(
            total_deals=("deal_id", "count"),
            reunioes=("reuniao_realizada", "sum"),
            ganhos=("ganho", "sum"),
        ).reset_index()
        grp["taxa_reuniao_pct"]        = (grp["reunioes"] / grp["total_deals"] * 100).round(1)
        grp["taxa_ganho_pct"]          = (grp["ganhos"] / grp["total_deals"] * 100).round(1)
        grp["taxa_ganho_reuniao_pct"]  = (grp["ganhos"] / grp["reunioes"].replace(0, np.nan) * 100).round(1)
        por_closer = grp.sort_values("total_deals", ascending=False).to_dict("records")

    motivos: list[dict] = []
    if "motivo_de_perda" in df.columns:
        perdas = df[df["dt_perda"].notna() & df["motivo_de_perda"].notna()]
        if not perdas.empty:
            motivos = (
                perdas.groupby("motivo_de_perda")
                .size()
                .reset_index(name="total")
                .sort_values("total", ascending=False)
                .head(10)
                .to_dict("records")
            )

    canais: list[dict] = []
    if "canal_reuniao" in df.columns:
        reunioes_df = df[df["reuniao_realizada"] == 1] if "reuniao_realizada" in df.columns else df
        canais_df = reunioes_df["canal_reuniao"].dropna()
        if not canais_df.empty:
            canais = (
                canais_df.value_counts()
                .reset_index()
                .rename(columns={"canal_reuniao": "canal", "count": "total"})
                .to_dict("records")
            )

    summary_stats = {
        "total_deals": n,
        "deals_ganhos": n_ganho,
        "deals_perdidos": n_perda,
        "reunioes_realizadas": n_reuniao,
        "taxa_reuniao_pct": taxa_reuniao,
        "taxa_ganho_pct": taxa_ganho,
        "taxa_ganho_pos_reuniao_pct": taxa_ganho_reuniao,
        "taxa_noshow_pct": taxa_noshow,
        "ciclo_mediano_dias": ciclo_medio,
        "filtros_aplicados": {"closer": closer_filter, "cluster": cluster_filter},
    }
    tabular_data = [
        {"secao": "Distribuição por Estágio",  "dados": estagios},
        {"secao": "Performance por Closer",    "dados": por_closer},
        {"secao": "Motivos de Perda",          "dados": motivos},
        {"secao": "Canais de Reunião",         "dados": canais},
    ]
    ops.append(f"Total: {n} deals | Ganhos: {n_ganho} ({taxa_ganho}%) | Reuniões: {n_reuniao} ({taxa_reuniao}%)")
    return summary_stats, tabular_data, ops


# ---------------------------------------------------------------------------
# Growth Funnel
# ---------------------------------------------------------------------------

def _calc_growth_funnel(
    plan: QueryPlan,
    hs_growth: pd.DataFrame,
) -> tuple[dict, list[dict], list[str]]:
    ops: list[str] = ["Analisando Funil de Growth (HubSpot Contacts)"]

    if hs_growth.empty:
        return (
            {"aviso": "Dados do funil de Growth ainda não disponíveis. Execute `python -m importers.refresh` para importar."},
            [],
            ops + ["hs_growth vazio — importer não executado"],
        )

    df = hs_growth.copy()

    cluster_filter = plan.get_filter("cluster")
    if cluster_filter:
        df = df[df["cluster_leadscore"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtro cluster_leadscore: '{cluster_filter}' → {len(df)} contacts")

    area_filter = plan.get_filter("gestor")
    if area_filter:
        df = df[df["area_atuacao"].str.contains(area_filter, case=False, na=False)]
        ops.append(f"Filtro area_atuacao: '{area_filter}' → {len(df)} contacts")

    month = plan.filters.get("month")
    year  = plan.filters.get("year")
    month_start = plan.get_filter("month_start")
    month_end   = plan.get_filter("month_end")
    if month and year:
        try:
            m, y = int(month), int(year)
            df = df[(df["dt_criacao"].dt.month == m) & (df["dt_criacao"].dt.year == y)]
            ops.append(f"Filtro período: {m:02d}/{y} → {len(df)} contacts")
        except (ValueError, TypeError):
            pass
    elif month_start or month_end:
        if month_start:
            df = df[df["dt_criacao"] >= pd.Timestamp(month_start)]
        if month_end:
            df = df[df["dt_criacao"] <= pd.Timestamp(month_end) + pd.offsets.MonthEnd(0)]
        ops.append(f"Filtro período: {month_start} → {month_end} → {len(df)} contacts")

    n = len(df)
    if n == 0:
        return {"aviso": "Nenhum contact encontrado com os filtros aplicados."}, [], ops

    n_mql    = int(df["is_mql"].sum())         if "is_mql"           in df.columns else 0
    n_sql    = int(df["is_sql"].sum())         if "is_sql"           in df.columns else 0
    n_cust   = int(df["is_qualificado"].sum()) if "is_qualificado"   in df.columns else 0
    n_desq   = int(df["is_desqualificado"].sum()) if "is_desqualificado" in df.columns else 0
    n_closer = int(df["deal_id_closer"].notna().sum()) if "deal_id_closer" in df.columns else 0

    def pct(x, base=None):
        b = base if base is not None else n
        return round(x / b * 100, 1) if b else 0.0

    taxa_lead_mql  = pct(n_mql)
    taxa_mql_sql   = pct(n_sql, n_mql)
    taxa_sql_cust  = pct(n_cust, n_sql)
    taxa_lead_cust = pct(n_cust)

    def _mediana_dias(col):
        if col not in df.columns:
            return None
        v = df[col].dropna()
        return round(float(v.median()), 1) if not v.empty else None

    tempo_lead_mql  = _mediana_dias("dias_novo_ate_ativado")
    tempo_mql_cust  = _mediana_dias("dias_novo_ate_qualificado")

    score_por_cluster: list[dict] = []
    if "cluster_leadscore" in df.columns and "score_total_lp" in df.columns:
        score_por_cluster = (
            df[df["cluster_leadscore"].notna()]
            .groupby("cluster_leadscore")
            .agg(
                total=("contact_id", "count"),
                score_medio=("score_total_lp", "mean"),
                is_mql=("is_mql", "sum"),
                is_sql=("is_sql", "sum"),
                qualificados=("is_qualificado", "sum"),
            )
            .reset_index()
            .rename(columns={"cluster_leadscore": "cluster"})
            .assign(score_medio=lambda x: x["score_medio"].round(1))
            .sort_values("cluster")
            .to_dict("records")
        )

    por_area: list[dict] = []
    if "area_atuacao" in df.columns:
        por_area = (
            df[df["area_atuacao"].notna()]
            .groupby("area_atuacao")
            .agg(
                total=("contact_id", "count"),
                mqls=("is_mql", "sum"),
                sqls=("is_sql", "sum"),
                qualificados=("is_qualificado", "sum"),
            )
            .reset_index()
            .sort_values("total", ascending=False)
            .to_dict("records")
        )
        for row in por_area:
            row["taxa_conv_mql"]        = round(row["mqls"] / row["total"] * 100, 1) if row["total"] else 0
            row["taxa_conv_qualificado"] = round(row["qualificados"] / row["total"] * 100, 1) if row["total"] else 0

    por_faturamento: list[dict] = []
    if "faturamento_ultimo_ano" in df.columns:
        por_faturamento = (
            df[df["faturamento_ultimo_ano"].notna()]
            .groupby("faturamento_ultimo_ano")
            .agg(
                total=("contact_id", "count"),
                mqls=("is_mql", "sum"),
                sqls=("is_sql", "sum"),
                qualificados=("is_qualificado", "sum"),
            )
            .reset_index()
            .sort_values("total", ascending=False)
            .to_dict("records")
        )
        for row in por_faturamento:
            row["taxa_conv_sql"] = round(row["sqls"] / row["total"] * 100, 1) if row["total"] else 0

    evolucao_mensal: list[dict] = []
    if "mes_ano" in df.columns:
        evolucao_mensal = (
            df[df["mes_ano"].notna()]
            .groupby("mes_ano")
            .agg(
                total_leads=("contact_id", "count"),
                mqls=("is_mql", "sum"),
                sqls=("is_sql", "sum"),
                qualificados=("is_qualificado", "sum"),
            )
            .reset_index()
            .sort_values("mes_ano")
            .tail(12)
            .to_dict("records")
        )

    summary_stats = {
        "total_leads": n,
        "total_mqls": n_mql,
        "total_sqls": n_sql,
        "total_qualificados": n_cust,
        "total_desqualificados": n_desq,
        "com_deal_closer": n_closer,
        "taxa_lead_para_mql_pct": taxa_lead_mql,
        "taxa_mql_para_sql_pct": taxa_mql_sql,
        "taxa_sql_para_qualificado_pct": taxa_sql_cust,
        "taxa_lead_para_qualificado_pct": taxa_lead_cust,
        "tempo_mediano_lead_ate_ativado_dias": tempo_lead_mql,
        "tempo_mediano_lead_ate_qualificado_dias": tempo_mql_cust,
    }
    tabular_data = [
        {"secao": "KPIs por Cluster LeadScore",           "dados": score_por_cluster},
        {"secao": "Distribuição por Área de Atuação",     "dados": por_area},
        {"secao": "Distribuição por Faixa de Faturamento","dados": por_faturamento},
        {"secao": "Evolução Mensal (últimos 12 meses)",   "dados": evolucao_mensal},
    ]
    ops.append(
        f"Total: {n} leads | MQLs: {n_mql} ({taxa_lead_mql}%) | "
        f"SQLs: {n_sql} | Qualificados: {n_cust} ({taxa_lead_cust}%)"
    )
    return summary_stats, tabular_data, ops


# ---------------------------------------------------------------------------
# Detalhe de deal específico no Closer
# ---------------------------------------------------------------------------

def _calc_detalhe_deal(
    plan: QueryPlan,
    hs_closer: pd.DataFrame,
    hs_growth: pd.DataFrame,
) -> tuple[dict, list[dict], list[str]]:
    """Detalhes completos de um deal no Closer, incluindo lead Growth associado."""
    ops: list[str] = ["Buscando detalhe de deal no HubSpot Closer"]

    if hs_closer.empty:
        return {"aviso": "Dados do Closer não disponíveis."}, [], ops

    produtor_filter = plan.get_filter("produtor") or plan.get_filter("producer_name")
    deal_id_filter  = plan.get_filter("deal_id")

    df = hs_closer.copy()
    if deal_id_filter:
        df = df[df["deal_id"] == str(deal_id_filter)]
        ops.append(f"Filtro deal_id: {deal_id_filter}")
    elif produtor_filter:
        df = df[df["dealname"].str.contains(produtor_filter, case=False, na=False)]
        ops.append(f"Filtro produtor: '{produtor_filter}' → {len(df)} deals")
    else:
        return {"aviso": "Informe o nome do produtor ou deal_id."}, [], ops

    if df.empty:
        return {"aviso": "Nenhum deal encontrado."}, [], ops

    if len(df) > 1:
        df = df.sort_values("dt_criacao", ascending=False)
        ops.append(f"{len(df)} deals encontrados — exibindo o mais recente")

    row = df.iloc[0].to_dict()

    # Enriquece com lead Growth associado
    lead_info: dict = {}
    if not hs_growth.empty and "deal_id_closer" in hs_growth.columns:
        match = hs_growth[hs_growth["deal_id_closer"] == row.get("deal_id")]
        if not match.empty:
            lr = match.iloc[0]
            lead_info = {
                "lead_id":          lr.get("lead_id"),
                "nome":             lr.get("nome"),
                "pipeline_nome":    lr.get("pipeline_nome"),
                "stage_atual":      lr.get("stage_atual_nome"),
                "cluster_leadscore":lr.get("cluster_leadscore"),
                "is_mql":           int(lr.get("is_mql", 0)),
                "is_sql":           int(lr.get("is_sql", 0)),
                "is_qualificado":   int(lr.get("is_qualificado", 0)),
                "dt_criacao_lead":  lr.get("dt_criacao"),
                "dt_ativado":       lr.get("dt_ativado"),
                "dt_qualificado":   lr.get("dt_qualificado"),
            }
            ops.append(f"Lead Growth associado: {lr.get('nome')}")

    # Timeline de estágios do deal
    stage_cols = [
        ("dt_novo_qualificado",    "Novo Qualificado"),
        ("dt_cadencia",            "Cadência"),
        ("dt_interacoes",          "Interações"),
        ("dt_agendamento",         "Agendamento"),
        ("dt_reuniao",             "Reunião"),
        ("dt_aguardando_cadastro", "Aguardando Cadastro"),
        ("dt_ganho",               "Ganho"),
        ("dt_perda",               "Perda"),
    ]
    timeline = [
        {"estagio": label, "data": row[col]}
        for col, label in stage_cols
        if row.get(col) is not None and pd.notna(row.get(col))
    ]

    summary = {
        "deal_id":             row.get("deal_id"),
        "dealname":            row.get("dealname"),
        "gestor_contas":       row.get("gestor_contas"),
        "cluster":             row.get("cluster"),
        "closer":              row.get("closer"),
        "dealstage_nome":      row.get("dealstage_nome"),
        "ganho":               int(row.get("ganho", 0)),
        "motivo_de_perda":     row.get("motivo_de_perda"),
        "dt_criacao":          row.get("dt_criacao"),
        "dt_fechamento":       row.get("dt_fechamento"),
        "reuniao_realizada":   int(row.get("reuniao_realizada", 0)),
        "data_reuniao_agendada": row.get("data_reuniao_agendada"),
        "canal_reuniao":       row.get("canal_reuniao"),
        "tempo_total_ciclo_dias": row.get("tempo_total_ciclo_dias"),
        "lead_score":          row.get("lead_score"),
        "lead_growth_associado": lead_info or None,
    }
    tabular = [{"secao": "Timeline de Estágios no Closer", "dados": timeline}]
    ops.append(
        f"Deal: {row.get('dealname')} | Estágio: {row.get('dealstage_nome')} | "
        f"Ganho: {bool(row.get('ganho'))}"
    )
    return summary, tabular, ops


# ---------------------------------------------------------------------------
# Track lead → deal (funil interno Growth → Closer)
# ---------------------------------------------------------------------------

def _calc_track_lead_ate_deal(
    plan: QueryPlan,
    hs_growth: pd.DataFrame,
    hs_closer: pd.DataFrame,
) -> tuple[dict, list[dict], list[str]]:
    """Jornada de um lead no funil Growth até o deal no Closer."""
    ops: list[str] = ["Rastreando jornada lead → deal (Growth → Closer)"]

    if hs_growth.empty:
        return {"aviso": "Dados do Growth não disponíveis."}, [], ops

    produtor_filter = plan.get_filter("produtor") or plan.get_filter("producer_name")
    lead_id_filter  = plan.get_filter("lead_id")

    df = hs_growth.copy()
    if lead_id_filter:
        df = df[df["lead_id"] == str(lead_id_filter)]
    elif produtor_filter:
        df = df[df["nome"].str.contains(produtor_filter, case=False, na=False)]
        ops.append(f"Filtro nome: '{produtor_filter}' → {len(df)} leads")
    else:
        return {"aviso": "Informe o nome do produtor ou lead_id."}, [], ops

    if df.empty:
        return {"aviso": "Nenhum lead encontrado."}, [], ops

    if len(df) > 1:
        df = df.sort_values("dt_criacao", ascending=False)
        ops.append(f"{len(df)} leads encontrados — exibindo o mais recente")

    lead = df.iloc[0].to_dict()

    # Busca deal associado no Closer
    deal_info: dict = {}
    if not hs_closer.empty and lead.get("deal_id_closer"):
        match = hs_closer[hs_closer["deal_id"] == str(lead["deal_id_closer"])]
        if not match.empty:
            dr = match.iloc[0]
            deal_info = {
                "deal_id":               dr.get("deal_id"),
                "dealname":              dr.get("dealname"),
                "dealstage_nome":        dr.get("dealstage_nome"),
                "ganho":                 int(dr.get("ganho", 0)),
                "motivo_de_perda":       dr.get("motivo_de_perda"),
                "dt_criacao_deal":       dr.get("dt_criacao"),
                "dt_ganho":              dr.get("dt_ganho"),
                "dt_perda":              dr.get("dt_perda"),
                "tempo_total_ciclo_dias":dr.get("tempo_total_ciclo_dias"),
                "gestor_contas":         dr.get("gestor_contas"),
                "cluster":               dr.get("cluster"),
            }
            ops.append(f"Deal Closer: {dr.get('dealname')} ({dr.get('dealstage_nome')})")

    # Timeline unificada Growth → Closer
    events: list[dict] = []
    for col, label in [
        ("dt_novo_lead",    "Novo Lead"),
        ("dt_ativado",      "Ativado"),
        ("dt_interagiu",    "Interagiu"),
        ("dt_agendado",     "Agendado"),
        ("dt_qualificado",  "Qualificado (Growth)"),
        ("dt_desqualificado","Desqualificado"),
    ]:
        val = lead.get(col)
        if val is not None and pd.notna(val):
            events.append({"estagio": label, "data": val, "fonte": "Growth"})

    if deal_info:
        for col, label in [
            ("dt_criacao_deal", "Deal Aberto"),
            ("dt_ganho",        "Ganho"),
            ("dt_perda",        "Perda"),
        ]:
            val = deal_info.get(col)
            if val is not None and pd.notna(val):
                events.append({"estagio": label, "data": val, "fonte": "Closer"})

    events.sort(key=lambda x: pd.Timestamp(x["data"]) if x["data"] is not None else pd.Timestamp.min)

    summary = {
        "lead_id":                   lead.get("lead_id"),
        "nome":                      lead.get("nome"),
        "pipeline_nome":             lead.get("pipeline_nome"),
        "stage_atual_growth":        lead.get("stage_atual_nome"),
        "cluster_leadscore":         lead.get("cluster_leadscore"),
        "is_mql":                    int(lead.get("is_mql", 0)),
        "is_sql":                    int(lead.get("is_sql", 0)),
        "is_qualificado":            int(lead.get("is_qualificado", 0)),
        "tem_deal_closer":           bool(deal_info),
        "deal_closer":               deal_info or None,
        "dias_lead_ate_ativado":     lead.get("dias_novo_ate_ativado"),
        "dias_lead_ate_qualificado": lead.get("dias_novo_ate_qualificado"),
    }
    tabular = [{"secao": "Timeline Growth → Closer", "dados": events}]
    ops.append(
        f"Lead: {lead.get('nome')} | Stage: {lead.get('stage_atual_nome')} | "
        f"Deal Closer: {'sim' if deal_info else 'não'}"
    )
    return summary, tabular, ops


# ---------------------------------------------------------------------------
# Track completo: Growth → Closer → Base TMB (cross-data)
# ---------------------------------------------------------------------------

def _calc_track_produtor_funil(
    plan: QueryPlan,
    hs_closer: pd.DataFrame,
    hs_growth: pd.DataFrame,
    vendas: pd.DataFrame,
    produtores: pd.DataFrame,
) -> tuple[dict, list[dict], list[str]]:
    """Timeline completa de um produtor: lead → deal → parceiro TMB → eventual churn."""
    ops: list[str] = ["Rastreando jornada completa (Growth → Closer → Base TMB)"]

    produtor_filter = plan.get_filter("produtor") or plan.get_filter("producer_name")
    if not produtor_filter:
        return {"aviso": "Informe o nome do produtor para rastrear a jornada."}, [], ops

    # ── Base de produtores ───────────────────────────────────────────────────
    prod_row: dict = {}
    if not produtores.empty:
        match = produtores[produtores["Produtor"].str.contains(produtor_filter, case=False, na=False)]
        if not match.empty:
            prod_row = match.iloc[0].to_dict()
            ops.append(f"Produtor na base TMB: {prod_row.get('Produtor')}")

    # ── Closer ───────────────────────────────────────────────────────────────
    closer_row: dict = {}
    if not hs_closer.empty:
        match = hs_closer[hs_closer["dealname"].str.contains(produtor_filter, case=False, na=False)]
        if match.empty and prod_row.get("Código"):
            match = hs_closer[hs_closer["codigo_produtor"] == str(prod_row["Código"])]
        if not match.empty:
            closer_row = match.sort_values("dt_criacao", ascending=False).iloc[0].to_dict()
            ops.append(f"Deal Closer: {closer_row.get('dealname')} ({closer_row.get('dealstage_nome')})")

    # ── Growth ───────────────────────────────────────────────────────────────
    growth_row: dict = {}
    if not hs_growth.empty:
        match = hs_growth[hs_growth["nome"].str.contains(produtor_filter, case=False, na=False)]
        if match.empty and closer_row.get("deal_id"):
            match = hs_growth[hs_growth["deal_id_closer"] == str(closer_row["deal_id"])]
        if not match.empty:
            growth_row = match.sort_values("dt_criacao", ascending=False).iloc[0].to_dict()
            ops.append(f"Lead Growth: {growth_row.get('nome')} ({growth_row.get('stage_atual_nome')})")

    # ── Histórico de vendas ───────────────────────────────────────────────────
    vendas_hist: list[dict] = []
    if not vendas.empty:
        match = vendas[vendas["Produtor"].str.contains(produtor_filter, case=False, na=False)]
        if not match.empty:
            vendas_hist = (
                match[["Data", "Status", "Valor"]]
                .sort_values("Data")
                .to_dict("records")
            )
            ops.append(f"Histórico na base: {len(vendas_hist)} registros mensais")

    if not closer_row and not growth_row and not vendas_hist and not prod_row:
        return {"aviso": f"Produtor '{produtor_filter}' não encontrado em nenhuma fonte."}, [], ops

    # ── Timeline unificada ───────────────────────────────────────────────────
    events: list[dict] = []

    if growth_row:
        for col, label in [
            ("dt_novo_lead",   "Novo Lead (Growth)"),
            ("dt_ativado",     "Ativado (Growth)"),
            ("dt_interagiu",   "Interagiu (Growth)"),
            ("dt_agendado",    "Agendado (Growth)"),
            ("dt_qualificado", "Qualificado (Growth)"),
        ]:
            val = growth_row.get(col)
            if val is not None and pd.notna(val):
                events.append({"evento": label, "data": val, "fonte": "Growth"})

    if closer_row:
        for col, label in [
            ("dt_criacao",         "Deal Aberto (Closer)"),
            ("dt_novo_qualificado","Novo Qualificado (Closer)"),
            ("dt_reuniao",         "Reunião Realizada"),
            ("dt_ganho",           "Deal Ganho"),
            ("dt_perda",           "Deal Perdido"),
        ]:
            val = closer_row.get(col)
            if val is not None and pd.notna(val):
                events.append({"evento": label, "data": val, "fonte": "Closer"})

    if vendas_hist:
        events.append({
            "evento": "Primeira Venda TMB",
            "data": vendas_hist[0].get("Data"),
            "fonte": "Base TMB",
        })
        ultimo = vendas_hist[-1]
        events.append({
            "evento": f"Status atual: {ultimo.get('Status')}",
            "data": ultimo.get("Data"),
            "fonte": "Base TMB",
        })
        churns = [v for v in vendas_hist if v.get("Status") == "Churn"]
        if churns:
            events.append({
                "evento": "Primeiro Churn",
                "data": churns[0].get("Data"),
                "fonte": "Base TMB",
            })

    events.sort(
        key=lambda x: pd.Timestamp(x["data"]) if x["data"] is not None else pd.Timestamp.min
    )

    summary = {
        "produtor":               produtor_filter,
        "cluster_tmb":            prod_row.get("Cluster"),
        "gestor_tmb":             prod_row.get("Gestor"),
        "data_parceria_tmb":      prod_row.get("Data Parceria"),
        "status_atual":           vendas_hist[-1].get("Status") if vendas_hist else None,
        "meses_na_base":          len(vendas_hist),
        "primeira_venda":         vendas_hist[0].get("Data") if vendas_hist else None,
        "encontrado_em_growth":   bool(growth_row),
        "encontrado_em_closer":   bool(closer_row),
        "deal_stage_closer":      closer_row.get("dealstage_nome") if closer_row else None,
        "deal_ganho":             bool(closer_row.get("ganho")) if closer_row else None,
        "churnou":                any(v.get("Status") == "Churn" for v in vendas_hist),
    }
    tabular = [
        {"secao": "Timeline Completa",          "dados": events},
        {"secao": "Histórico de Status (últimos 12 meses)", "dados": vendas_hist[-12:]},
    ]
    ops.append(
        f"Fontes: Growth={'sim' if growth_row else 'não'} | "
        f"Closer={'sim' if closer_row else 'não'} | "
        f"Base={'sim' if vendas_hist else 'não'}"
    )
    return summary, tabular, ops


# ---------------------------------------------------------------------------
# Coorte Closer → Churn (análise agregada cross-data)
# ---------------------------------------------------------------------------

def _calc_cohort_closer_churn(
    plan: QueryPlan,
    hs_closer: pd.DataFrame,
    vendas: pd.DataFrame,
    produtores: pd.DataFrame,
) -> tuple[dict, list[dict], list[str]]:
    """Produtores ganhos no Closer: qual seu destino na base TMB (ativo, pré-churn, churn)?"""
    ops: list[str] = ["Analisando coorte Closer → Base TMB (cross-data)"]

    if hs_closer.empty:
        return {"aviso": "Dados do Closer não disponíveis."}, [], ops
    if vendas.empty:
        return {"aviso": "Dados de vendas não disponíveis."}, [], ops

    ganhos = hs_closer[hs_closer["ganho"] == 1].copy()
    if ganhos.empty:
        return {"aviso": "Nenhum deal ganho no Closer."}, [], ops

    cluster_filter = plan.get_filter("cluster")
    gestor_filter  = plan.get_filter("gestor")
    month_start    = plan.get_filter("month_start")
    month_end      = plan.get_filter("month_end")

    if cluster_filter:
        ganhos = ganhos[ganhos["cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtro cluster: '{cluster_filter}' → {len(ganhos)} ganhos")
    if gestor_filter:
        ganhos = ganhos[ganhos["gestor_contas"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtro gestor: '{gestor_filter}' → {len(ganhos)} ganhos")
    if month_start:
        ganhos = ganhos[ganhos["dt_ganho"] >= pd.Timestamp(month_start)]
    if month_end:
        ganhos = ganhos[ganhos["dt_ganho"] <= pd.Timestamp(month_end) + pd.offsets.MonthEnd(0)]

    ops.append(f"Deals ganhos no período: {len(ganhos)}")

    resultados: list[dict] = []
    for _, deal in ganhos.iterrows():
        nome = deal.get("dealname", "")
        if not nome:
            continue
        match = vendas[vendas["Produtor"].str.contains(nome, case=False, na=False)]
        if match.empty:
            resultados.append({
                "produtor":           nome,
                "cluster":            deal.get("cluster"),
                "gestor":             deal.get("gestor_contas"),
                "dt_ganho_closer":    deal.get("dt_ganho"),
                "encontrado_na_base": False,
                "status_atual":       None,
                "meses_ativo":        None,
                "dias_ate_churn":     None,
                "churnou":            False,
            })
            continue

        recente    = match.sort_values("Data").iloc[-1]
        dt_ganho   = deal.get("dt_ganho")
        apos_ganho = match[match["Data"] >= dt_ganho] if (dt_ganho is not None and pd.notna(dt_ganho)) else match
        churns     = match[match["Status"] == "Churn"].sort_values("Data")
        primeiro_churn = churns.iloc[0] if not churns.empty else None

        dias_ate_churn = None
        if primeiro_churn is not None and dt_ganho is not None and pd.notna(dt_ganho):
            dias_ate_churn = (pd.Timestamp(primeiro_churn["Data"]) - pd.Timestamp(dt_ganho)).days

        resultados.append({
            "produtor":           nome,
            "cluster":            deal.get("cluster"),
            "gestor":             deal.get("gestor_contas"),
            "dt_ganho_closer":    deal.get("dt_ganho"),
            "encontrado_na_base": True,
            "status_atual":       str(recente.get("Status", "")),
            "meses_ativo":        len(apos_ganho),
            "dias_ate_churn":     dias_ate_churn,
            "churnou":            bool(primeiro_churn is not None),
        })

    df_res     = pd.DataFrame(resultados)
    n_total    = len(df_res)
    n_na_base  = int(df_res["encontrado_na_base"].sum()) if not df_res.empty else 0
    n_churnou  = int(df_res["churnou"].sum()) if not df_res.empty else 0
    taxa_churn = round(n_churnou / n_na_base * 100, 1) if n_na_base else 0.0

    tempo_mediano_churn = None
    if not df_res.empty:
        dias_s = df_res["dias_ate_churn"].dropna()
        tempo_mediano_churn = round(float(dias_s.median()), 1) if not dias_s.empty else None

    dist_status: list[dict] = []
    if n_na_base:
        dist_status = (
            df_res[df_res["encontrado_na_base"]]
            .groupby("status_atual")
            .size()
            .reset_index(name="total")
            .to_dict("records")
        )

    por_cluster: list[dict] = []
    if not df_res.empty and "cluster" in df_res.columns:
        grp = (
            df_res[df_res["encontrado_na_base"]]
            .groupby("cluster", dropna=False)
            .agg(total=("produtor", "count"), churnou=("churnou", "sum"))
            .reset_index()
        )
        grp["taxa_churn_pct"] = (grp["churnou"] / grp["total"] * 100).round(1)
        por_cluster = grp.to_dict("records")

    summary = {
        "deals_ganhos_closer":        n_total,
        "encontrados_na_base_tmb":    n_na_base,
        "nao_encontrados_na_base":    n_total - n_na_base,
        "total_churnou":              n_churnou,
        "taxa_churn_pos_closer_pct":  taxa_churn,
        "tempo_mediano_ate_churn_dias": tempo_mediano_churn,
    }
    tabular = [
        {"secao": "Destino por Produtor",         "dados": resultados[:50]},
        {"secao": "Distribuição de Status Atual", "dados": dist_status},
        {"secao": "Taxa de Churn por Cluster",    "dados": por_cluster},
    ]
    ops.append(
        f"Ganhos: {n_total} | Na base TMB: {n_na_base} | "
        f"Churnou: {n_churnou} ({taxa_churn}%)"
    )
    return summary, tabular, ops
