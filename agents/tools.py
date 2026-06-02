"""
tools.py — registro de ferramentas (Tools) para o ReAct agent.

Cada Tool é um wrapper fino sobre uma _calc_* do analytics_agent.
O wrapper aceita parâmetros diretos (sem QueryPlan) e constrói
QueryPlan internamente antes de despachar para a função de cálculo.

As funções _calc_* NÃO foram modificadas — toda a lógica pandas
permanece intacta. Só a camada de roteamento mudou.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from agents import analytics_agent
from agents.analytics_agent import QueryPlan, _to_native


# ---------------------------------------------------------------------------
# Contexto de dados passado para cada tool
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    vendas: pd.DataFrame
    produtores: pd.DataFrame
    data_reference_date: Any = None
    data_source: str = "cache"
    hs_closer: pd.DataFrame = field(default_factory=pd.DataFrame)
    hs_growth: pd.DataFrame = field(default_factory=pd.DataFrame)


# ---------------------------------------------------------------------------
# Helper: constrói QueryPlan limpo a partir de kwargs
# ---------------------------------------------------------------------------

def _plan(query_type: str, **filters) -> QueryPlan:
    clean = {k: v for k, v in filters.items() if v is not None}
    return QueryPlan(query_type=query_type, filters=clean, metrics=[], group_by=None)


def _period_filters(periodo: str | None) -> dict:
    """Converte 'YYYY-MM' ou 'YYYY-MM:YYYY-MM' para filtros month/year ou month_start/month_end."""
    if not periodo:
        return {}
    if ":" in periodo:
        start, end = periodo.split(":", 1)
        return {"month_start": start.strip(), "month_end": end.strip()}
    # Mês único → month + year
    parts = periodo.strip().split("-")
    if len(parts) == 2:
        return {"year": int(parts[0]), "month": int(parts[1])}
    return {}


# ---------------------------------------------------------------------------
# Wrappers — um por ferramenta
# ---------------------------------------------------------------------------

def _run(plan: QueryPlan, ctx: ToolContext) -> dict:
    """Executa _dispatch e serializa o resultado."""
    summary, tabular, ops = analytics_agent._dispatch(
        plan,
        ctx.vendas,
        ctx.produtores,
        ctx.hs_closer if ctx.hs_closer is not None else pd.DataFrame(),
        ctx.hs_growth if ctx.hs_growth is not None else pd.DataFrame(),
    )
    return {
        "query_type": plan.query_type,
        "summary": summary,
        "tabular": tabular[:50],
        "ops": ops,
    }


def w_status_distribuicao(ctx: ToolContext, gestor=None, cluster=None, periodo=None,
                           group_by=None, incluir_faturamento=False):
    p = _plan("status_distribuicao", gestor=gestor, cluster=cluster,
              group_by=group_by, incluir_faturamento=incluir_faturamento,
              **_period_filters(periodo))
    return _run(p, ctx)


def w_taxa_churn(ctx: ToolContext, gestor=None, periodo=None, group_by=None,
                 serie=False, months=6):
    p = _plan("taxa_churn_v2", gestor=gestor, group_by=group_by,
              serie=serie, months=months, **_period_filters(periodo))
    return _run(p, ctx)


def w_transicoes(ctx: ToolContext, from_status=None, to_status=None,
                 gestor=None, cluster=None, periodo=None, incluir_valor=True):
    p = _plan("transicoes", from_status=from_status, to_status=to_status,
              gestor=gestor, cluster=cluster, incluir_valor=incluir_valor,
              **_period_filters(periodo))
    return _run(p, ctx)


def w_produtores(ctx: ToolContext, produtor=None, status=None, gestor=None,
                 cluster=None, periodo=None, order_by="valor", top_n=20):
    p = _plan("produtores", produtor=produtor, status=status, gestor=gestor,
              cluster=cluster, order_by=order_by, top_n=top_n,
              **_period_filters(periodo))
    return _run(p, ctx)


def w_faturamento(ctx: ToolContext, gestor=None, cluster=None, produtor=None,
                  status=None, periodo=None, group_by=None, top_n=10):
    p = _plan("faturamento", gestor=gestor, cluster=cluster, produtor=produtor,
              status=status, group_by=group_by, top_n=top_n,
              **_period_filters(periodo))
    return _run(p, ctx)


def w_ltv(ctx: ToolContext, gestor=None, cluster=None, status=None, min_ciclos=None):
    p = _plan("ltv_analysis", gestor=gestor, cluster=cluster,
              status=status, min_ciclos=min_ciclos)
    return _run(p, ctx)


def w_cohort(ctx: ToolContext, cohort_by=None):
    p = _plan("cohort_analysis", cohort_by=cohort_by or "primeira_venda")
    return _run(p, ctx)


def w_resumo_churn(ctx: ToolContext):
    p = _plan("churn_report_summary")
    return _run(p, ctx)


def w_churn_streak(ctx: ToolContext):
    p = _plan("churn_rate_streak")
    return _run(p, ctx)


def w_pipeline_closer(ctx: ToolContext, cluster=None, periodo=None):
    p = _plan("closer_pipeline", cluster=cluster, **_period_filters(periodo))
    return _run(p, ctx)


def w_funil_crescimento(ctx: ToolContext, cluster=None, periodo=None):
    p = _plan("growth_funnel", cluster=cluster, **_period_filters(periodo))
    return _run(p, ctx)


def w_detalhe_deal(ctx: ToolContext, produtor=None, deal_id=None):
    p = _plan("detalhe_deal", produtor=produtor, deal_id=deal_id)
    return _run(p, ctx)


def w_track_lead_ate_deal(ctx: ToolContext, produtor=None, lead_id=None):
    p = _plan("track_lead_ate_deal", produtor=produtor, lead_id=lead_id)
    return _run(p, ctx)


def w_track_produtor_funil(ctx: ToolContext, produtor: str):
    p = _plan("track_produtor_funil", producer_name=produtor)
    return _run(p, ctx)


def w_cohort_closer_churn(ctx: ToolContext, cluster=None, gestor=None, periodo=None):
    p = _plan("cohort_closer_churn", cluster=cluster, gestor=gestor, **_period_filters(periodo))
    return _run(p, ctx)


def w_saudacao(ctx: ToolContext):
    p = _plan("greeting")
    return _run(p, ctx)


def w_pedir_identidade(ctx: ToolContext, pedido_pendente=None):
    p = _plan("ask_identity", _pending_request=pedido_pendente or "manager_report")
    return _run(p, ctx)



# ---------------------------------------------------------------------------
# Dispatcher: nome da tool → função wrapper
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS: dict[str, Any] = {
    "status_distribuicao":  w_status_distribuicao,
    "taxa_churn":           w_taxa_churn,
    "transicoes":           w_transicoes,
    "produtores":           w_produtores,
    "faturamento":          w_faturamento,
    "ltv":                  w_ltv,
    "cohort":               w_cohort,
    "resumo_churn":         w_resumo_churn,
    "churn_streak":         w_churn_streak,
    "pipeline_closer":      w_pipeline_closer,
    "funil_crescimento":    w_funil_crescimento,
    "detalhe_deal":         w_detalhe_deal,
    "track_lead_ate_deal":  w_track_lead_ate_deal,
    "track_produtor_funil": w_track_produtor_funil,
    "cohort_closer_churn":  w_cohort_closer_churn,
    "saudacao":             w_saudacao,
    "pedir_identidade":     w_pedir_identidade,
}

# Mapeamento tool → query_type para o ReportAgent saber como renderizar casos especiais
TOOL_TO_QUERY_TYPE: dict[str, str] = {
    "saudacao":          "greeting",
    "pedir_identidade":  "ask_identity",
    "cohort":            "cohort_analysis",
    "resumo_churn":      "churn_report_summary",
}


# ---------------------------------------------------------------------------
# Classificação de tools por área de negócio
# ---------------------------------------------------------------------------

TOOL_AREAS: dict[str, str] = {
    # churn (9)
    "status_distribuicao": "churn",
    "taxa_churn": "churn",
    "transicoes": "churn",
    "produtores": "churn",
    "faturamento": "churn",
    "ltv": "churn",
    "cohort": "churn",
    "resumo_churn": "churn",
    "churn_streak": "churn",
    # closer (2)
    "pipeline_closer": "closer",
    "detalhe_deal": "closer",
    # growth (1)
    "funil_crescimento": "growth",
    # misto (3)
    "track_lead_ate_deal": "misto",
    "track_produtor_funil": "misto",
    "cohort_closer_churn": "misto",
    # greeting (2)
    "saudacao": "greeting",
    "pedir_identidade": "greeting",
}


def get_claude_tools(areas: list[str]) -> list[dict]:
    """Retorna lista de ferramentas no formato Claude filtrada pelas áreas solicitadas."""
    allowed = {name for name, a in TOOL_AREAS.items() if a in set(areas)}
    return [t for t in CLAUDE_TOOLS if t["name"] in allowed]


def execute_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    """Executa uma tool pelo nome com os args fornecidos pelo modelo."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {
            "query_type": "unknown_query",
            "summary": {"erro": f"Tool '{name}' não encontrada"},
            "tabular": [],
            "ops": [],
        }
    # Remove None e injeta contexto
    clean_args = {k: v for k, v in args.items() if v is not None}
    result = fn(ctx, **clean_args)
    return _to_native(result)


# ---------------------------------------------------------------------------
# Declarações de função para a API Claude (tool use)
# ---------------------------------------------------------------------------

_PERIODO_DESC = "Período no formato 'YYYY-MM' (mês específico) ou 'YYYY-MM:YYYY-MM' (intervalo). Omitir = mês mais recente."


CLAUDE_TOOLS: list[dict] = [
    {
        "name": "status_distribuicao",
        "description": (
            "Distribuição da base de produtores por status no período. "
            "Use para: 'como está a base?', 'quantos ativos?', 'distribuição por gestor', "
            "'comparar gestores', 'distribuição por cluster'. "
            "group_by='Gestor' para ranking de gestores, group_by='Cluster' para segmentos, "
            "None para totais gerais."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gestor":              {"type": "string",  "description": "Filtrar por gestor específico (opcional)"},
                "cluster":             {"type": "string",  "description": "Filtrar por cluster: PP, Palladium, G, M ou P (opcional)"},
                "periodo":             {"type": "string",  "description": _PERIODO_DESC},
                "group_by":            {"type": "string",  "description": "Agrupar por: 'Gestor', 'Cluster', ou omitir para totais gerais"},
                "incluir_faturamento": {"type": "boolean", "description": "Se True, adiciona faturamento do período por grupo (padrão False)"},
            },
        },
    },
    {
        "name": "taxa_churn",
        "description": (
            "Taxa de churn TMB (Pré-Churn→Churn / base Ativo+Pré-Churn). Exclui TMB Educação. "
            "Modo pontual: taxa de um mês. Modo série (serie=True): histórico de N meses (padrão 6). "
            "Use para: 'qual a taxa de churn?', 'histórico de churn', 'tendência', 'por gestor'. "
            "Para diagnóstico de streak (gestores consecutivos acima da meta), use churn_streak."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gestor":  {"type": "string",  "description": "Filtrar por gestor específico (opcional)"},
                "periodo": {"type": "string",  "description": _PERIODO_DESC},
                "group_by":{"type": "string",  "description": "'Gestor' para breakdown por gestor, omitir para total"},
                "serie":   {"type": "boolean", "description": "True para série histórica de meses (padrão False)"},
                "months":  {"type": "integer", "description": "Quantos meses na série (padrão 6, só usado com serie=True)"},
            },
        },
    },
    {
        "name": "transicoes",
        "description": (
            "Produtores que mudaram de status em um período. "
            "Churns novos: from_status='Pré-Churn', to_status='Churn'. "
            "Recuperações: to_status='Ativo' (de Pré-Churn ou Churn). "
            "Entradas em risco: to_status='Pré-Churn'. "
            "Use para: 'quem churnou?', 'quem se recuperou?', 'quem entrou em pré-churn?', "
            "'transições do gestor X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_status":   {"type": "string",  "description": "Status de origem (ex: 'Pré-Churn', 'Ativo', 'Churn')"},
                "to_status":     {"type": "string",  "description": "Status de destino (ex: 'Churn', 'Ativo', 'Pré-Churn')"},
                "gestor":        {"type": "string",  "description": "Filtrar por gestor (opcional)"},
                "cluster":       {"type": "string",  "description": "Filtrar por cluster (opcional)"},
                "periodo":       {"type": "string",  "description": _PERIODO_DESC},
                "incluir_valor": {"type": "boolean", "description": "True para incluir faturamento 12m por produtor (padrão True)"},
            },
            "required": ["from_status", "to_status"],
        },
    },
    {
        "name": "produtores",
        "description": (
            "Lista ou detalhe de produtores. "
            "Com 'produtor=X': histórico individual (status mês a mês, faturamento, gestor, cluster). "
            "Com 'status=[...]': lista de produtores naquele status, ordenados e limitados. "
            "Use para: 'como está o produtor X?', 'quem está em Pré-Churn?', "
            "'top 10 em risco por valor', 'detalhes do produtor Y'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produtor": {"type": "string", "description": "Nome do produtor para detalhe individual (busca parcial)"},
                "status":   {"type": "array", "items": {"type": "string"}, "description": "Lista de status a filtrar, ex: ['Pré-Churn','Churn']"},
                "gestor":   {"type": "string", "description": "Filtrar por gestor (opcional)"},
                "cluster":  {"type": "string", "description": "Filtrar por cluster (opcional)"},
                "periodo":  {"type": "string", "description": _PERIODO_DESC},
                "order_by": {"type": "string", "description": "Ordenar por: 'valor' (padrão), 'meses_sem_venda', 'data'"},
                "top_n":    {"type": "integer", "description": "Limitar aos N primeiros (padrão 20)"},
            },
        },
    },
    {
        "name": "faturamento",
        "description": (
            "Análise financeira por dimensão (gestor, cluster, produtor). "
            "Com status=['Churn','Pré-Churn']: impacto financeiro do churn. "
            "Use para: 'faturamento por gestor', 'top produtores por receita', "
            "'quanto vale a carteira?', 'impacto financeiro do churn', 'valor em risco'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gestor":   {"type": "string", "description": "Filtrar por gestor (opcional)"},
                "cluster":  {"type": "string", "description": "Filtrar por cluster (opcional)"},
                "produtor": {"type": "string", "description": "Filtrar por produtor específico (opcional)"},
                "status":   {"type": "array", "items": {"type": "string"}, "description": "Filtrar por lista de status, ex: ['Churn','Pré-Churn']"},
                "periodo":  {"type": "string", "description": _PERIODO_DESC},
                "group_by": {"type": "string", "description": "Agrupar por: 'Gestor', 'Cluster', 'Produtor', ou omitir para total"},
                "top_n":    {"type": "integer", "description": "Limitar ranking de produtores (padrão 10)"},
            },
        },
    },
    {
        "name": "ltv",
        "description": (
            "Lifetime Value por produtor: meses ativos, % tempo aproveitado, "
            "ciclos de vida, faturamento total. Use para análises de LTV, retenção, ciclos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gestor":     {"type": "string",  "description": "Filtrar por gestor (opcional)"},
                "cluster":    {"type": "string",  "description": "Filtrar por cluster (opcional)"},
                "status":     {"type": "string",  "description": "Filtrar por status atual (opcional)"},
                "min_ciclos": {"type": "integer", "description": "Mínimo de ciclos de vida (opcional, ex: 2 para reativados)"},
            },
        },
    },
    {
        "name": "cohort",
        "description": (
            "Análise de cohort: % de churn por mês de primeira venda ou data de parceria. "
            "Gera tabela heatmap. Use para: 'análise de cohort', 'safra', 'coorte'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cohort_by": {"type": "string", "description": "'primeira_venda' (padrão) ou 'parceria'"},
            },
        },
    },
    {
        "name": "resumo_churn",
        "description": (
            "Resumo visual de churn do mês (card com KPIs): taxa, novos churns, pré-churn, ativos. "
            "Use para: 'resumo de churn', 'como está o churn hoje?', 'KPIs do mês'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "churn_streak",
        "description": (
            "Gestores com sequência consecutiva de meses acima da meta de 5% de churn. "
            "Use para: 'gestores com churn alto consecutivo', 'sequência de churn'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "pipeline_closer",
        "description": (
            "Análise agregada do pipeline de fechamento de novos parceiros no HubSpot Closer. "
            "KPIs: deals totais, ganhos, taxa de reunião, taxa de ganho, ciclo mediano, "
            "distribuição por estágio, performance por closer e motivos de perda. "
            "Use para: 'como está o pipeline do Closer?', 'taxa de conversão do Closer', "
            "'quantos deals ganhos esse mês?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {"type": "string", "description": "Filtrar por cluster do produtor (opcional)"},
                "periodo": {"type": "string", "description": _PERIODO_DESC},
            },
        },
    },
    {
        "name": "funil_crescimento",
        "description": (
            "Análise agregada do funil de leads de crescimento no HubSpot (pipelines TMB e TMR). "
            "KPIs: total de leads, MQLs, SQLs, qualificados, desqualificados, taxas de conversão "
            "e tempos medianos por estágio. "
            "Use para: 'como está o funil de growth?', 'quantos leads MQL esse mês?', "
            "'distribuição por área de atuação'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {"type": "string", "description": "Filtrar por cluster LeadScore A/B/C/D (opcional)"},
                "periodo": {"type": "string", "description": _PERIODO_DESC},
            },
        },
    },
    {
        "name": "detalhe_deal",
        "description": (
            "Detalhes completos de um deal específico no Closer: timeline de estágios, "
            "gestor, closer responsável, reunião, ciclo, e lead Growth associado. "
            "Use para: 'me conta o deal do produtor X', 'como está o processo do produtor X no Closer', "
            "'detalhes do deal de X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produtor": {"type": "string", "description": "Nome do produtor (busca parcial, case-insensitive)"},
                "deal_id":  {"type": "string", "description": "ID do deal HubSpot (alternativa ao nome)"},
            },
        },
    },
    {
        "name": "track_lead_ate_deal",
        "description": (
            "Jornada interna de um lead do funil Growth até o deal no Closer: "
            "timeline de estágios Growth → Closer, tempos por etapa, status de conversão. "
            "Use para: 'como foi o funil interno do lead X', 'quanto tempo levou do lead ao deal', "
            "'jornada Growth→Closer do produtor X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produtor": {"type": "string", "description": "Nome do lead/produtor (busca parcial)"},
                "lead_id":  {"type": "string", "description": "ID do lead HubSpot (alternativa ao nome)"},
            },
        },
    },
    {
        "name": "track_produtor_funil",
        "description": (
            "Timeline completa de um produtor desde a entrada como lead até eventual churn: "
            "cruza dados de Growth (HubSpot), Closer (HubSpot) e base de clientes TMB (status, vendas). "
            "Use para: 'me conta a jornada do produtor X', 'histórico completo de X', "
            "'quando X entrou, fechou e como está hoje?', 'X churnou depois de quanto tempo?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produtor": {"type": "string", "description": "Nome do produtor (obrigatório, busca parcial)"},
            },
            "required": ["produtor"],
        },
    },
    {
        "name": "cohort_closer_churn",
        "description": (
            "Análise de coorte cross-data: dos produtores ganhos no Closer, quantos churnam na base TMB? "
            "Retorna taxa de churn pós-aquisição, tempo mediano até churn e distribuição por cluster. "
            "Use para: 'dos que fecharam pelo Closer, quantos churnou?', "
            "'qual o tempo médio entre ganho no Closer e churn?', "
            "'taxa de churn por cluster nos produtores do Closer'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {"type": "string", "description": "Filtrar por cluster do produtor (opcional)"},
                "gestor":  {"type": "string", "description": "Filtrar por gestor de contas (opcional)"},
                "periodo": {"type": "string", "description": _PERIODO_DESC},
            },
        },
    },
    {
        "name": "saudacao",
        "description": (
            "Use quando o usuário mandar uma saudação ('oi', 'olá', 'bom dia') "
            "ou perguntar o que Alfred pode fazer."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "pedir_identidade",
        "description": (
            "Use quando o usuário pedir 'meu relatório' ou 'minha carteira' "
            "e a identidade ainda não for conhecida."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pedido_pendente": {"type": "string", "description": "Tipo de relatório que o usuário pediu"},
            },
        },
    },
]
