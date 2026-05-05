"""
analytics_agent.py — classifica a query via Claude e executa operações pandas.

Responsabilidade:
  1. Chamar Claude para obter um plano estruturado (QueryPlan) em JSON
  2. Despachar para a função pandas correspondente
  3. Retornar AnalyticsResult (sem DataFrames — apenas dicts/lists)

Não formata respostas para humanos.
"""

import difflib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import anthropic
import pandas as pd
from pydantic import BaseModel, ValidationError

from agents.data_agent import AnalyticsContext
from config import settings
from prompts import ANALYTICS_SYSTEM_PROMPT

logger = settings.logger
_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

class AnalyticsAgentError(Exception):
    def __init__(self, message: str, user_facing_message: str):
        super().__init__(message)
        self.user_facing_message = user_facing_message


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class AnalyticsInput:
    context: AnalyticsContext
    user_query: str
    conversation_history: list[dict] = field(default_factory=list)
    current_user_gestor: str | None = None
    awaiting_identity_for: str | None = None
    last_discussed_gestor: str | None = None
    intent_contract: "Any | None" = None         # IntentContract do ContextAgent


@dataclass
class AnalyticsResult:
    query_type: str
    summary_stats: dict[str, Any]
    tabular_data: list[dict[str, Any]]
    data_reference_date: date
    data_source: str
    warnings: list[str]
    pandas_operations_log: list[str]


class QueryPlan(BaseModel):
    query_type: str
    filters: dict[str, Any]
    metrics: list[str]
    group_by: str | None = None

    def get_filter(self, key: str) -> str | None:
        v = self.filters.get(key)
        return str(v).strip() if v and str(v).strip().lower() not in ("null", "none", "") else None


_VALID_QUERY_TYPES = {
    "current_status_summary",
    "producer_detail",
    "churn_rate_period",
    "at_risk_list",
    "trend_over_time",
    "cluster_breakdown",
    "manager_summary",
    "status_transitions",
    "cohort_analysis",
    "financial_summary",
    "churn_value_impact",
    "ltv_analysis",
    "cycle_analysis",
    "churn_rate_analysis",
    "churn_rate_streak",
    "churn_rate_trend",
    "churn_report",
    "churn_report_summary",
    "manager_report",
    "closer_pipeline",
    "growth_funnel",
    "detalhe_deal",
    "track_lead_ate_deal",
    "track_produtor_funil",
    "cohort_closer_churn",
    "greeting",
    "unknown_query",
    "ask_identity",   # Alfred pergunta quem é o usuário antes de gerar relatório pessoal
}

_FALLBACK_QUERY_TYPE = "current_status_summary"

# Palavras que indicam intenção analítica — usadas para detectar inputs fora do domínio
_ANALYTICS_KEYWORDS = frozenset({
    "churn", "pré-churn", "prechurn", "ativo", "inativo", "status",
    "produtor", "gestor", "cluster", "taxa", "relatório", "relatorio",
    "análise", "analise", "resumo", "ltv", "ciclo", "tendência", "tendencia",
    "risco", "recuperação", "recuperacao", "transição", "transicao",
    "cohort", "coorte", "financeiro", "faturamento", "valor", "histórico",
    "historico", "período", "periodo", "carteira", "ranking", "top",
    "distribuição", "distribuicao", "meta", "mês", "mes", "ano",
    "base", "produtores", "gestores",
})

META_CHURN_PCT = 5.0  # meta de churn mensal da TMB em pontos percentuais
GESTORES_EXCLUIDOS_CHURN = ["TMB Educação"]  # excluídos do cálculo de taxa de churn
GESTORES_EXCLUIDOS_RELATORIO = [  # excluídos da tabela de gestores no relatório
    "Gabriel Biban",
    "Marina Morena Aparecida Izidoro Rezende",
    "Sem gestor",
    "inativo Nathan Rebecchi",
    "TMB Banco",
    "Aaron Trementoza Novais da Silva",
]
MIN_CARTEIRA_GESTOR = 10  # gestores com carteira abaixo desse limite são removidos do relatório
_MONTH_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


# ---------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------

def _extract_identity_from_query(query: str) -> str | None:
    """
    Detecta quando o usuário se identifica explicitamente na mensagem.
    Suporta: "sou a/o X", "me chamo X", "meu nome é X", "eu sou X".
    Retorna o nome em formato título ou None.
    """
    q = query.strip()
    _INVALID = frozenset({
        "gestor", "gestora", "produtor", "produtora", "analista", "tmb",
        "alfred", "churn", "ativo", "inativo", "um", "uma", "o", "a",
    })
    patterns = [
        r"\bsou\s+(?:[ao]\s+)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}?)(?=\s*[,.]|\s+e\b|\s+me\b|\s+meu\b|\s+minha\b|$)",
        r"\beu\s+sou\s+(?:[ao]\s+)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}?)(?=\s*[,.]|\s+me\b|$)",
        r"\bme\s+chamo\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}?)(?=\s*[,.]|$)",
        r"\bmeu\s+nome\s+[eé]\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,40}?)(?=\s*[,.]|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip(".,!")
            words = name.split()
            if not words:
                continue
            if len(words) == 1 and words[0].lower() in _INVALID:
                continue
            if len(name) < 3:
                continue
            return name.title()
    return None


def _shortcircuit_plan(
    user_query: str,
    current_user_gestor: str | None = None,
    awaiting_identity_for: str | None = None,
) -> "QueryPlan | None":
    """
    Infere o QueryPlan diretamente do texto quando o tipo é inequívoco,
    evitando a chamada à API Gemini. Retorna None se não reconhecido.
    """
    q = user_query.lower().strip()
    _tokens = q.split()

    # ── Detectar identidade declarada nesta mensagem ──────────────────────
    identified_now = _extract_identity_from_query(user_query)

    # ── Se Alfred estava aguardando identificação ─────────────────────────
    # O usuário pode ter respondido com seu nome ou "sou a X"
    if awaiting_identity_for:
        name = identified_now
        if not name:
            # Mensagem curta sem keyword analítica → provavelmente só o nome
            _has_analytics = any(kw in q for kw in _ANALYTICS_KEYWORDS)
            if len(_tokens) <= 5 and not _has_analytics:
                # Não é saudação nem confirmação — assumir como nome
                _GREET = frozenset({"oi", "olá", "ola", "hi", "hello", "ei", "bom", "boa"})
                _CONF_SET = frozenset({"sim", "não", "nao", "ok", "claro"})
                if _tokens and _tokens[0] not in _GREET and _tokens[0] not in _CONF_SET:
                    name = user_query.strip().title()
        if name:
            return QueryPlan(
                query_type=awaiting_identity_for,
                filters={"gestor": name, "_identified_as_gestor": True},
                metrics=[],
                group_by=None,
            )

    # ── Confirmações curtas: usuário aceitou ver o relatório completo ─────
    _CONF = frozenset({
        "sim", "quero", "pode", "manda", "mande", "claro", "vai", "ok",
        "vamos", "por favor", "pf", "completo",
    })
    if (
        _tokens
        and len(_tokens) <= 5
        and _tokens[0] in _CONF
        and "gestor" not in q
        and "produtor" not in q
        and not re.search(r"\bdo\s+\w{3,}", q)
    ):
        return QueryPlan(query_type="churn_report", filters={}, metrics=[], group_by=None)

    # ── Relatório completo explícito ──────────────────────────────────────
    _tem_relatorio = "relatório" in q or "relatorio" in q
    if _tem_relatorio and "completo" in q:
        return QueryPlan(query_type="churn_report", filters={}, metrics=[], group_by=None)

    # ── Relatório genérico de churn → resumo (Modo 1) ────────────────────
    if "relatório de churn" in q or "relatorio de churn" in q:
        # Se tem "meu" antes → tratar como pessoal
        if "meu relatório de churn" not in q and "meu relatorio de churn" not in q:
            return QueryPlan(query_type="churn_report_summary", filters={}, metrics=[], group_by=None)

    # ── Relatório de gestor pessoal ("meu relatório", "minha carteira") ───
    _MY_REPORT = (
        "meu relatório" in q or "meu relatorio" in q
        or "minha carteira" in q
        or "meu relatório de churn" in q or "meu relatorio de churn" in q
    )
    if _MY_REPORT:
        gestor = identified_now or current_user_gestor
        if gestor:
            return QueryPlan(
                query_type="manager_report",
                filters={"gestor": gestor, "_identified_as_gestor": True},
                metrics=[],
                group_by=None,
            )
        else:
            # Não sabemos quem é o usuário — pedir identificação
            return QueryPlan(
                query_type="ask_identity",
                filters={"_pending_request": "manager_report"},
                metrics=[],
                group_by=None,
            )

    # ── Relatório de gestor específico ───────────────────────────────────
    m = re.search(r"relat[oó]rio do (.+)", q)
    if m:
        gestor = m.group(1).strip()
        gestor = re.sub(r"\s*(por favor|por gentileza|pf|please|agora|ok)$", "", gestor)
        gestor = re.sub(r"\s*\b(ativo|inativo)\b$", "", gestor, flags=re.IGNORECASE)
        gestor = gestor.rstrip("?.,!").strip()
        # Não marca como _identified_as_gestor — pedido de relatório de outra pessoa
        return QueryPlan(query_type="manager_report", filters={"gestor": gestor}, metrics=[], group_by=None)

    # ── "Sou a X" sem pedido de relatório — apenas estabelece identidade ──
    if identified_now and not any(kw in q for kw in ("relat", "churn", "taxa", "carteira", "ativo")):
        # Mensagem curtíssima de identificação standalone → aguardar próximo pedido
        if len(_tokens) <= 8:
            return QueryPlan(
                query_type="ask_identity",
                filters={"_pending_request": "manager_report", "_just_identified": identified_now},
                metrics=[],
                group_by=None,
            )

    # ── LTV / ciclos de vida ──────────────────────────────────────────────
    _LTV_SIGNALS = ("ltv", "lifetime", "ciclos de vida", "ciclo de vida", "tempo ativo")
    if any(sig in q for sig in _LTV_SIGNALS):
        filters: dict = {}
        if "reativad" in q:
            filters["min_ciclos"] = 2
        return QueryPlan(query_type="ltv_analysis", filters=filters, metrics=["resumo_geral"], group_by=None)

    # ── Cohort ────────────────────────────────────────────────────────────
    _COHORT_SIGNALS = ("cohort", "coorte", "safra", "primeira venda")
    if any(sig in q for sig in _COHORT_SIGNALS):
        return QueryPlan(query_type="cohort_analysis", filters={}, metrics=["resumo_geral"], group_by=None)

    # ── Saudações e perguntas sobre capacidades ──────────────────────────
    _GREETINGS = frozenset({
        "oi", "olá", "ola", "hello", "hi", "ei", "eai", "e aí",
        "bom dia", "boa tarde", "boa noite",
    })
    _CAPABILITY_PHRASES = (
        "o que posso fazer", "o que você faz", "o que voce faz",
        "o que você pode", "o que voce pode",
        "me ajuda", "me ajude", "como funciona",
    )
    _q_no_alfred = q.replace("alfred", "").strip()
    _is_greeting = (
        _q_no_alfred in _GREETINGS
        or any(phrase in q for phrase in _CAPABILITY_PHRASES)
        or (_tokens and _tokens[0] in _GREETINGS and len(_tokens) <= 3)
    )
    if _is_greeting:
        return QueryPlan(query_type="greeting", filters={}, metrics=[], group_by=None)

    # ── Entradas fora do domínio analítico ───────────────────────────────
    _has_analytics = any(kw in q for kw in _ANALYTICS_KEYWORDS)
    _is_nonsense = (
        len(q) <= 3
        or (len(_tokens) <= 2 and not _has_analytics)
    )
    if _is_nonsense and (not _tokens or _tokens[0] not in _CONF):
        return QueryPlan(query_type="unknown_query", filters={}, metrics=[], group_by=None)

    return None


def run(inp: AnalyticsInput, session_id: str) -> AnalyticsResult:
    _log(session_id, "started", query=inp.user_query[:120])
    t0 = time.time()

    plan = _shortcircuit_plan(
        inp.user_query,
        current_user_gestor=inp.current_user_gestor,
        awaiting_identity_for=inp.awaiting_identity_for,
    ) or _classify_query(
        inp.user_query,
        session_id,
        inp.conversation_history,
        current_user_gestor=inp.current_user_gestor,
        last_discussed_gestor=inp.last_discussed_gestor,
        data_reference_date=inp.context.data_reference_date,
    )

    # Extrair metadados de identidade do plano
    is_speaking_to_gestor = bool(plan.filters.get("_identified_as_gestor"))
    identified_user = plan.filters.get("gestor") if is_speaking_to_gestor else None
    ask_identity_for = (
        plan.filters.get("_pending_request") if plan.query_type == "ask_identity" else None
    )
    just_identified = plan.filters.get("_just_identified")  # identidade sem pedido ainda

    ops_log: list[str] = []
    warnings: list[str] = []

    if inp.context.data_source != "sharepoint":
        warnings.append(f"Dados carregados de {inp.context.data_source}.")

    try:
        summary_stats, tabular_data, ops_log = _dispatch(
            plan,
            inp.context.vendas,
            inp.context.produtores,
            inp.context.hs_closer,
            inp.context.hs_growth,
        )
    except Exception as exc:
        _log(session_id, "error", error=str(exc))
        raise AnalyticsAgentError(
            str(exc),
            user_facing_message="Não foi possível processar a análise solicitada. Tente reformular a pergunta.",
        ) from exc

    # Injeta metadados de identidade/contexto no resultado
    summary_stats["_meta"] = {
        "is_speaking_to_gestor": is_speaking_to_gestor,
        "identified_user": identified_user or just_identified,
        "ask_identity_for": ask_identity_for,
        "last_discussed_gestor": plan.get_filter("gestor"),
    }

    result = AnalyticsResult(
        query_type=plan.query_type,
        summary_stats=summary_stats,
        tabular_data=tabular_data[:50],
        data_reference_date=inp.context.data_reference_date,
        data_source=inp.context.data_source,
        warnings=warnings,
        pandas_operations_log=ops_log,
    )

    _log(
        session_id, "completed",
        query_type=plan.query_type,
        rows_result=len(tabular_data),
        duration_ms=int((time.time() - t0) * 1000),
    )
    return result


# ---------------------------------------------------------------------------
# Passo 1 — classificação via Claude
# ---------------------------------------------------------------------------

def _to_claude_messages(messages: list[dict]) -> list[dict]:
    result = []
    for msg in messages:
        role = "assistant" if msg["role"] == "assistant" else "user"
        result.append({"role": role, "content": msg["content"]})
    return result


def _classify_query(
    user_query: str,
    session_id: str,
    conversation_history: list[dict] | None = None,
    current_user_gestor: str | None = None,
    last_discussed_gestor: str | None = None,
    data_reference_date=None,
) -> QueryPlan:
    for attempt in range(1, 3):
        try:
            # Injeta contexto de sessão no FINAL do system prompt (não no início — quebra o Gemini)
            context_parts: list[str] = []
            if data_reference_date:
                context_parts.append(
                    f"DATA DE REFERÊNCIA DOS DADOS: {data_reference_date}. "
                    "Este é o 'hoje'. Resolva períodos relativos ('este mês', 'último mês', 'trimestre') em relação a esta data."
                )
            if current_user_gestor:
                context_parts.append(
                    f"O usuário desta sessão é o gestor '{current_user_gestor}'. "
                    "Pronomes de primeira pessoa ('meu', 'minha', 'meus', 'minhas') "
                    "e referências como 'minha carteira', 'meu churn', 'minha taxa' "
                    "devem ser resolvidos para este gestor."
                )
            if last_discussed_gestor:
                context_parts.append(
                    f"Último gestor consultado nesta conversa: '{last_discussed_gestor}'. "
                    "Pronomes como 'dela', 'dele', 'desse gestor', 'dessa gestora' "
                    "referem-se a este gestor, mesmo que o query_type mude."
                )

            system = ANALYTICS_SYSTEM_PROMPT
            if context_parts:
                system = system + "\n\n---\nNota de sessão: " + " ".join(context_parts)
            if attempt == 2:
                system += "\n\nIMPORTANTE: retorne APENAS o JSON, sem nenhum texto adicional."

            msgs = list(conversation_history or [])
            msgs.append({"role": "user", "content": user_query})
            claude_messages = _to_claude_messages(msgs)

            response = _client.messages.create(
                model=settings.claude_haiku_model,
                system=system,
                messages=claude_messages,
                max_tokens=512,
            )
            raw = response.content[0].text.strip()

            _log(
                session_id, "claude_call",
                attempt=attempt,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
            )

            # Remove bloco de código markdown se presente
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            data = json.loads(raw)
            plan = QueryPlan(**data)

            if plan.query_type not in _VALID_QUERY_TYPES:
                logger.warning(
                    f"query_type desconhecido: '{plan.query_type}' — "
                    f"usando fallback '{_FALLBACK_QUERY_TYPE}'"
                )
                plan.query_type = _FALLBACK_QUERY_TYPE

            return plan

        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
            logger.warning(f"Classificação Gemini tentativa {attempt} inválida: {exc}")
            if attempt == 2:
                raise AnalyticsAgentError(
                    f"Claude retornou JSON inválido após 2 tentativas: {exc}",
                    user_facing_message="Não foi possível interpretar a pergunta. Tente ser mais específico.",
                ) from exc

        except anthropic.RateLimitError as exc:
            logger.error(f"Claude RateLimitError: {exc}")
            raise AnalyticsAgentError(str(exc), user_facing_message=(
                "⚠️ **Limite de requisições atingido**\n\nAguarde alguns instantes e tente novamente."
            )) from exc
        except anthropic.AuthenticationError as exc:
            logger.error(f"Claude AuthenticationError: {exc}")
            raise AnalyticsAgentError(str(exc), user_facing_message=(
                "⚠️ **Erro de autenticação**\n\nChave de API inválida. Verifique o arquivo `.env`."
            )) from exc
        except anthropic.APIStatusError as exc:
            logger.error(f"Claude APIStatusError {exc.status_code}: {exc}")
            if exc.status_code == 529:
                msg = "⚠️ **API Claude sobrecarregada**\n\nAguarde 10–20 segundos e tente novamente."
            else:
                msg = f"⚠️ **Erro na API Claude ({exc.status_code})**\n\nTente novamente."
            raise AnalyticsAgentError(str(exc), user_facing_message=msg) from exc
        except Exception as exc:
            logger.error(f"Erro inesperado no Claude: {type(exc).__name__}: {exc}")
            raise AnalyticsAgentError(str(exc), user_facing_message=(
                f"⚠️ **Erro inesperado**\n\n`{str(exc)[:120]}`\n\nTente novamente."
            )) from exc


# ---------------------------------------------------------------------------
# Passo 2 — dispatcher pandas
# ---------------------------------------------------------------------------

def _to_native(obj: Any) -> Any:
    """Converte recursivamente tipos numpy para tipos Python nativos."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _dispatch(
    plan: QueryPlan,
    vendas: pd.DataFrame,
    produtores: pd.DataFrame,
    hs_closer: pd.DataFrame | None = None,
    hs_growth: pd.DataFrame | None = None,
) -> tuple[dict, list[dict], list[str]]:
    dispatcher = {
        "current_status_summary": _calc_current_status_summary,
        "producer_detail": _calc_producer_detail,
        "churn_rate_period": _calc_churn_rate,
        "at_risk_list": _calc_at_risk,
        "trend_over_time": _calc_trend,
        "cluster_breakdown": _calc_cluster_breakdown,
        "manager_summary": _calc_manager_summary,
        "status_transitions": _calc_status_transitions,
        "cohort_analysis": _calc_cohort_analysis,
        "financial_summary": _calc_financial_summary,
        "churn_value_impact": _calc_churn_value_impact,
        "ltv_analysis": _calc_ltv,
        "cycle_analysis": _calc_cycle_analysis,
        "churn_rate_analysis": _calc_churn_rate_analysis,
        "churn_rate_streak": _calc_churn_rate_streak,
        "churn_rate_trend": _calc_churn_rate_trend,
        "churn_report": _calc_churn_report,
        "churn_report_summary": _calc_churn_report_summary,
        "manager_report": _calc_manager_report,
        "churns_novos": _calc_churns_novos,
        "recuperacoes": _calc_recuperacoes,
        "status_distribuicao": _calc_status_distribuicao,
        "taxa_churn_v2": _calc_taxa_churn_composavel,
        "transicoes": _calc_transicoes,
        "produtores": _calc_produtores,
        "faturamento": _calc_faturamento,
        "greeting": _calc_greeting,
        "ask_identity": _calc_ask_identity,
        "unknown_query": _calc_unknown_query,
    }
    _HS_TYPES = {"closer_pipeline", "growth_funnel", "detalhe_deal", "track_lead_ate_deal", "track_produtor_funil", "cohort_closer_churn"}
    if plan.query_type in _HS_TYPES:
        from agents import hubspot_analytics as _hs
        _c = hs_closer if hs_closer is not None else pd.DataFrame()
        _g = hs_growth if hs_growth is not None else pd.DataFrame()
        if plan.query_type == "closer_pipeline":
            summary, tabular, ops = _hs._calc_closer_pipeline(plan, _c)
        elif plan.query_type == "growth_funnel":
            summary, tabular, ops = _hs._calc_growth_funnel(plan, _g)
        elif plan.query_type == "detalhe_deal":
            summary, tabular, ops = _hs._calc_detalhe_deal(plan, _c, _g)
        elif plan.query_type == "track_lead_ate_deal":
            summary, tabular, ops = _hs._calc_track_lead_ate_deal(plan, _g, _c)
        elif plan.query_type == "track_produtor_funil":
            summary, tabular, ops = _hs._calc_track_produtor_funil(plan, _c, _g, vendas, produtores)
        elif plan.query_type == "cohort_closer_churn":
            summary, tabular, ops = _hs._calc_cohort_closer_churn(plan, _c, vendas, produtores)
        return _to_native(summary), _to_native(tabular), ops

    fn = dispatcher.get(plan.query_type, _calc_current_status_summary)
    summary, tabular, ops = fn(plan, vendas, produtores)
    return _to_native(summary), _to_native(tabular), ops


def _latest_status_per_producer(vendas: pd.DataFrame) -> pd.DataFrame:
    """Retorna o status mais recente de cada produtor. Preserva Código para join."""
    return (
        vendas.sort_values("Data")
        .groupby("Código", as_index=False)
        .last()[["Código", "Produtor", "Status", "Data"]]
    )


def _apply_date_filter(df: pd.DataFrame, plan: QueryPlan) -> pd.DataFrame:
    month_start = plan.get_filter("month_start")
    month_end = plan.get_filter("month_end")
    if month_start:
        df = df[df["Data"] >= pd.to_datetime(month_start)]
    if month_end:
        df = df[df["Data"] <= pd.to_datetime(month_end) + pd.offsets.MonthEnd(0)]
    return df


def _filter_by_period(vendas: pd.DataFrame, plan: QueryPlan) -> pd.DataFrame:
    """
    Filtra vendas por período conforme os filtros do QueryPlan:
      - "month" + "year"       → mês/ano exato
      - "month_start/end"      → intervalo (lógica _apply_date_filter)
      - sem filtro de data     → apenas o mês mais recente disponível
    """
    month = plan.filters.get("month")
    year = plan.filters.get("year")

    # Converte para int se vierem como string
    try:
        month = int(month) if month and str(month).lower() not in ("null", "none", "") else None
        year = int(year) if year and str(year).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        month, year = None, None

    if month and year:
        filtered = vendas[(vendas["Data"].dt.month == month) & (vendas["Data"].dt.year == year)]
        return filtered

    month_start = plan.get_filter("month_start")
    month_end = plan.get_filter("month_end")
    if month_start or month_end:
        return _apply_date_filter(vendas, plan)

    # Sem filtro → mês mais recente
    max_date = vendas["Data"].max()
    return vendas[vendas["Data"] == max_date]


def _calc_greeting(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Retorna resultado vazio — ReportAgent renderiza a mensagem de boas-vindas com lista completa."""
    return {"greeting": True}, [], ["Saudação detectada — exibindo lista completa"]


def _calc_ask_identity(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Retorna sinal para ReportAgent perguntar quem é o usuário."""
    pending = plan.filters.get("_pending_request", "manager_report")
    just_identified = plan.filters.get("_just_identified")
    return (
        {"ask_identity": True, "pending_request": pending, "just_identified": just_identified},
        [],
        ["Identificação necessária — perguntando ao usuário"],
    )


def _calc_unknown_query(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Retorna resultado vazio — ReportAgent renderiza a mensagem de ajuda resumida."""
    return {"help": True}, [], ["Consulta fora do domínio — exibindo ajuda resumida"]


def _calc_current_status_summary(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    ops = ["Obtendo status por período"]
    df = _filter_by_period(vendas, plan)
    ops.append(f"Período filtrado: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    # Status de cada produtor dentro do período filtrado (último registro do período)
    latest = (
        df.sort_values("Data")
        .groupby("Código", as_index=False)
        .last()[["Código", "Produtor", "Status", "Data"]]
    )

    counts = latest.groupby("Status").size().reset_index(name="total")
    total = len(latest)

    summary = {
        "total_produtores": total,
        "data_referencia": str(latest["Data"].max().date()),
        "por_status": counts.set_index("Status")["total"].to_dict(),
    }

    ops.append(f"Total de produtores com status registrado: {total}")
    tabular = counts.rename(columns={"total": "Qtd"}).to_dict("records")
    return summary, tabular, ops


def _calc_producer_detail(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    ops = []
    name_filter = plan.get_filter("producer_name")

    if not name_filter:
        ops.append("Nenhum produtor especificado — retornando resumo geral")
        return _calc_current_status_summary(plan, vendas, produtores)

    ops.append(f"Filtrando produtor: '{name_filter}'")
    mask = vendas["Produtor"].str.contains(name_filter, case=False, na=False)
    df = vendas[mask].copy()

    if df.empty:
        return (
            {"aviso": f"Produtor '{name_filter}' não encontrado em fVendas."},
            [],
            ops,
        )

    prod_name = df["Produtor"].iloc[0]
    ops.append(f"Produtor encontrado: '{prod_name}' | {len(df)} registros mensais")

    # Info cadastral
    prod_info = produtores[
        produtores["Produtor"].str.contains(name_filter, case=False, na=False)
    ]
    cadastro: dict = {}
    if not prod_info.empty:
        row = prod_info.iloc[0]
        cadastro = {
            "Cluster": row.get("Cluster", ""),
            "Gestor": row.get("Gestor", ""),
            "Data Parceria": str(row.get("Data Parceria", ""))[:10],
            "Data 1ª Venda": str(row.get("Data 1ª Venda", ""))[:10],
        }

    latest = df.sort_values("Data").iloc[-1]
    summary = {
        "produtor": prod_name,
        "status_atual": latest["Status"],
        "status_anterior": latest["Status_Anterior"],
        "ultimo_registro": str(latest["Data"].date()),
        "cadastro": cadastro,
    }

    historico = (
        df.sort_values("Data", ascending=False)[["Data", "Status", "Status_Anterior"]]
        .head(12)
        .copy()
    )
    historico["Data"] = historico["Data"].dt.strftime("%Y-%m")
    return summary, historico.to_dict("records"), ops


def _calc_churn_rate(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    ops = ["Calculando taxa de churn por período"]
    df = _apply_date_filter(vendas, plan)
    ops.append(f"Linhas após filtro de data: {len(df)}")

    if df.empty:
        return {"aviso": "Nenhum dado encontrado para o período informado."}, [], ops

    monthly = (
        df.groupby(["Data", "Status"])
        .size()
        .reset_index(name="count")
    )

    churn_monthly = monthly[monthly["Status"].isin(["Churn", "Pré-churn"])].copy()
    total_monthly = monthly.groupby("Data")["count"].sum().reset_index(name="total")
    merged = churn_monthly.merge(total_monthly, on="Data")
    merged["taxa_pct"] = (merged["count"] / merged["total"] * 100).round(1)
    merged["Data"] = merged["Data"].dt.strftime("%Y-%m")

    total_churn = int(monthly[monthly["Status"] == "Churn"]["count"].sum())
    total_prechurn = int(monthly[monthly["Status"] == "Pré-churn"]["count"].sum())
    total_geral = int(monthly["count"].sum())

    summary = {
        "total_churn": total_churn,
        "total_prechurn": total_prechurn,
        "total_registros": total_geral,
        "taxa_churn_media_pct": round(total_churn / total_geral * 100, 1) if total_geral else 0,
    }

    ops.append(f"Churn total no período: {total_churn}")
    tabular = merged[["Data", "Status", "count", "taxa_pct"]].to_dict("records")
    return summary, tabular, ops


def _calc_at_risk(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    ops = ["Identificando produtores em risco (Pré-churn)"]

    gestor_filter = plan.get_filter("gestor")
    cluster_filter = plan.get_filter("cluster")

    df = _filter_by_period(vendas, plan)
    ops.append(f"Período filtrado: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    # Status de cada produtor dentro do período (último registro do período)
    latest = (
        df.sort_values("Data")
        .groupby("Código", as_index=False)
        .last()[["Código", "Produtor", "Status", "Data"]]
    )
    at_risk = latest[latest["Status"].isin(["Pré-churn", "Churn"])].copy()
    ops.append(f"Produtores em risco antes de filtros: {len(at_risk)}")

    # Join com cadastro para obter Cluster e Gestor
    merged = at_risk.merge(
        produtores[["Código", "Cluster", "Gestor"]],
        on="Código",
        how="left",
    )

    if gestor_filter:
        merged = merged[merged["Gestor"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtrado por gestor '{gestor_filter}': {len(merged)} produtores")

    if cluster_filter:
        merged = merged[merged["Cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtrado por cluster '{cluster_filter}': {len(merged)} produtores")

    # Valor médio histórico: média dos meses em que o produtor estava Ativo
    valor_medio = (
        vendas[vendas["Status"] == "Ativo"]
        .groupby("Código")["Valor"]
        .mean()
        .round(2)
        .rename("Valor Médio Histórico (R$)")
    )
    merged = merged.merge(valor_medio, on="Código", how="left")
    merged["Valor Médio Histórico (R$)"] = merged["Valor Médio Histórico (R$)"].fillna(0.0)

    valor_total_risco = round(merged["Valor Médio Histórico (R$)"].sum(), 2)
    summary = {
        "total_em_risco": len(merged),
        "prechurn": int((merged["Status"] == "Pré-churn").sum()),
        "churn": int((merged["Status"] == "Churn").sum()),
        "valor_total_em_risco": valor_total_risco,
    }

    merged["Último Registro"] = merged["Data"].dt.strftime("%Y-%m")
    tabular_cols = ["Produtor", "Status", "Cluster", "Gestor", "Último Registro", "Valor Médio Histórico (R$)"]

    # Ordenação e limite dinâmicos (suportados via ReAct tool params)
    sort_by = plan.filters.get("sort_by", "valor")
    sort_col = "Valor Médio Histórico (R$)" if sort_by != "data" else "Último Registro"
    result_df = merged[tabular_cols].sort_values(sort_col, ascending=(sort_by == "data"))

    top_n = plan.filters.get("top_n")
    try:
        top_n = int(top_n) if top_n and str(top_n).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        top_n = None
    if top_n:
        result_df = result_df.head(top_n)
        ops.append(f"Limitado a top {top_n} por '{sort_col}'")

    return summary, result_df.to_dict("records"), ops


def _calc_trend(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    ops = ["Calculando tendência mensal de status"]
    df = _apply_date_filter(vendas, plan)

    monthly = (
        df.groupby(["Data", "Status"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    monthly["Data"] = monthly["Data"].dt.strftime("%Y-%m")

    ops.append(f"Meses analisados: {len(monthly)}")
    summary = {"meses_analisados": len(monthly)}
    return summary, monthly.to_dict("records"), ops


def _calc_cluster_breakdown(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    ops = ["Calculando breakdown por cluster"]
    latest = _latest_status_per_producer(vendas)
    merged = latest.merge(
        produtores[["Código", "Cluster"]],
        on="Código",
        how="left",
    )
    merged["Cluster"] = merged["Cluster"].fillna("Não classificado")

    breakdown = (
        merged.groupby(["Cluster", "Status"])
        .size()
        .reset_index(name="Qtd")
    )
    ops.append(f"Clusters encontrados: {merged['Cluster'].nunique()}")

    # Faturamento do mês mais recente por cluster
    _latest_date = vendas["Data"].max()
    _vendas_mes = vendas[
        (vendas["Data"].dt.month == _latest_date.month) &
        (vendas["Data"].dt.year == _latest_date.year)
    ].merge(produtores[["Código", "Cluster"]], on="Código", how="left")
    _vendas_mes["Cluster"] = _vendas_mes["Cluster"].fillna("Não classificado")
    _fat_raw = _vendas_mes.groupby("Cluster")["Valor"].sum().round(2).to_dict()
    _fat_total = round(sum(_fat_raw.values()), 2)
    fat_por_cluster = {
        k: {
            "valor": v,
            "pct_total": round(v / _fat_total * 100, 1) if _fat_total else 0.0,
        }
        for k, v in sorted(_fat_raw.items(), key=lambda x: x[1], reverse=True)
    }
    ops.append(f"Faturamento mês {_latest_date.strftime('%Y-%m')}: R$ {_fat_total:,.2f}")

    summary = {
        "clusters": merged["Cluster"].nunique(),
        "por_cluster": breakdown.groupby("Cluster")["Qtd"].sum().to_dict(),
        "faturamento_mes_atual": {
            "mes_referencia": _latest_date.strftime("%Y-%m"),
            "total": _fat_total,
            "por_cluster": fat_por_cluster,
        },
    }
    return summary, breakdown.to_dict("records"), ops


def _calc_manager_summary(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    ops = ["Calculando resumo por gestor"]
    latest = _latest_status_per_producer(vendas)
    merged = latest.merge(
        produtores[["Código", "Gestor"]],
        on="Código",
        how="left",
    )
    merged["Gestor"] = merged["Gestor"].fillna("Sem gestor")
    merged = merged[merged["Gestor"] != "Sem gestor"]

    summary_df = (
        merged.groupby(["Gestor", "Status"])
        .size()
        .reset_index(name="Qtd")
    )
    ops.append(f"Gestores encontrados: {merged['Gestor'].nunique()}")

    summary = {
        "gestores": merged["Gestor"].nunique(),
        "por_gestor": summary_df.groupby("Gestor")["Qtd"].sum().to_dict(),
    }
    return summary, summary_df.to_dict("records"), ops


def _filter_transitions(df: pd.DataFrame, plan: QueryPlan, ops: list[str]) -> pd.DataFrame:
    """
    Lógica canônica de filtro de transições — usada por _calc_status_transitions
    e _calc_cohort_analysis para garantir resultados idênticos.

    Passos:
      1. Seleciona linhas onde Status != Status_Anterior
      2. Deduplica por Produtor (mantém a linha mais recente do período)
      3. Aplica from_status e to_status se fornecidos
    """
    transitions = df[df["Status"] != df["Status_Anterior"]].copy()
    ops.append(f"Transições brutas (Status != Status_Anterior): {len(transitions)}")

    # Deduplicação: um produtor pode ter mais de uma linha no período filtrado
    # (ex: mês com dados diários consolidados). Mantém o registro mais recente.
    before_dedup = len(transitions)
    transitions = (
        transitions.sort_values("Data")
        .drop_duplicates(subset=["Código"], keep="last")
    )
    if len(transitions) < before_dedup:
        ops.append(f"Deduplicação por Código: {before_dedup} → {len(transitions)} registros")

    from_status = plan.get_filter("from_status")
    to_status = plan.get_filter("to_status")

    if from_status:
        transitions = transitions[
            transitions["Status_Anterior"].str.lower() == from_status.lower()
        ]
        ops.append(f"Filtrado por Status_Anterior='{from_status}': {len(transitions)} transições")

    if to_status:
        transitions = transitions[
            transitions["Status"].str.lower() == to_status.lower()
        ]
        ops.append(f"Filtrado por Status='{to_status}': {len(transitions)} transições")

    return transitions


def _calc_status_transitions(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Identifica produtores que mudaram de status em um determinado período.
    Usa o campo Status_Anterior (status do mês anterior) já presente em fVendas.
    """
    ops = ["Identificando transições de status"]

    df = _filter_by_period(vendas, plan)
    ops.append(f"Período filtrado: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    transitions = _filter_transitions(df, plan, ops)

    # Enriquece com Cluster, Gestor, Data 1ª Venda e Data Parceria
    merged = transitions.merge(
        produtores[["Código", "Cluster", "Gestor", "Data 1ª Venda", "Data Parceria"]],
        on="Código",
        how="left",
    )

    # Contagem por tipo de transição
    transition_counts = (
        transitions.groupby(["Status_Anterior", "Status"])
        .size()
        .reset_index(name="Qtd")
        .rename(columns={"Status_Anterior": "De", "Status": "Para"})
    )

    summary = {
        "total_transicoes": len(transitions),
        "por_transicao": {
            f"{row['De']} → {row['Para']}": row["Qtd"]
            for _, row in transition_counts.iterrows()
        },
    }

    # Inclui Valor do mês da transição (vem de fVendas via transitions)
    if "Valor" in transitions.columns:
        merged = merged.merge(
            transitions[["Código", "Valor"]].rename(columns={"Valor": "Valor no Mês (R$)"}),
            on="Código",
            how="left",
        )
        merged["Valor no Mês (R$)"] = merged["Valor no Mês (R$)"].fillna(0.0)
        valor_total = round(merged["Valor no Mês (R$)"].sum(), 2)
        summary["valor_total_transicoes"] = valor_total

    merged["Data"] = merged["Data"].dt.strftime("%Y-%m")
    merged["Data 1ª Venda"] = merged["Data 1ª Venda"].dt.strftime("%Y-%m").fillna("")
    merged["Data Parceria"] = merged["Data Parceria"].dt.strftime("%Y-%m").fillna("")
    tabular_cols = ["Produtor", "Status_Anterior", "Status", "Data", "Valor no Mês (R$)", "Cluster", "Gestor", "Data 1ª Venda", "Data Parceria"]
    available_cols = [c for c in tabular_cols if c in merged.columns]
    return summary, merged[available_cols].to_dict("records"), ops


def _calc_cohort_analysis(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Tabela de cohort de churn: % de produtores em Status=Churn por mês desde a entrada.

    Linhas  = cohort (mês de Data 1ª Venda ou Data Parceria)
    Colunas = offset em meses desde o cohort (mínimo 3, por regra de negócio)
    Valor   = % de produtores do cohort com Status=="Churn" naquele mês específico
              dividido pelo tamanho total do cohort (denominador fixo)
    """
    # ── Resolve cohort_by ─────────────────────────────────────────────────
    cohort_by_raw = plan.get_filter("cohort_by") or "primeira_venda"
    cohort_by = cohort_by_raw.lower().strip()
    if cohort_by == "parceria":
        cohort_col = "Data Parceria"
    else:
        cohort_col = "Data 1ª Venda"
        cohort_by = "primeira_venda"

    ops = [f"Iniciando tabela de cohort por {cohort_col}"]

    # ── Produtores com data de cohort disponível ──────────────────────────
    prod_c = produtores[["Código", cohort_col]].dropna(subset=[cohort_col]).copy()
    prod_c["cohort_ym"] = (
        prod_c[cohort_col].dt.year * 12 + prod_c[cohort_col].dt.month
    )
    prod_c["cohort_str"] = (
        prod_c[cohort_col].dt.year.astype(str) + "-"
        + prod_c[cohort_col].dt.month.apply(lambda m: f"{m:02d}")
    )

    if prod_c.empty:
        return {"aviso": f"Nenhum produtor com {cohort_col} disponível."}, [], ops

    # Tamanho de cada cohort (denominador fixo)
    cohort_sizes = prod_c.groupby("cohort_str")["Código"].nunique().to_dict()

    # ── Merge vendas × cohort ─────────────────────────────────────────────
    vendas_c = vendas.merge(
        prod_c[["Código", "cohort_ym", "cohort_str"]], on="Código", how="inner"
    )
    vendas_c["obs_ym"] = vendas_c["Data"].dt.year * 12 + vendas_c["Data"].dt.month
    vendas_c["offset"] = vendas_c["obs_ym"] - vendas_c["cohort_ym"]

    # Apenas offsets >= 3 (regra de negócio: churn possível a partir do mês 3)
    vendas_c = vendas_c[vendas_c["offset"] >= 3].copy()

    if vendas_c.empty:
        return {"aviso": "Sem dados suficientes para a tabela de cohort."}, [], ops

    # ── Conta produtores em Churn por (cohort_str, offset) ────────────────
    churn_counts = (
        vendas_c[vendas_c["Status"] == "Churn"]
        .groupby(["cohort_str", "offset"])["Código"]
        .nunique()
        .to_dict()
    )

    # Mês de referência (último mês disponível nos dados)
    ref_date = vendas["Data"].max()
    ref_ym = int(ref_date.year * 12 + ref_date.month)

    max_offset = int(vendas_c["offset"].max())
    cohorts_sorted = sorted(cohort_sizes.keys())

    ops.append(f"Cohorts: {len(cohorts_sorted)} | Offset máximo: {max_offset} meses")

    # ── Constrói a matriz ─────────────────────────────────────────────────
    # Matriz: {cohort_str: {offset: pct_churn | None}}
    # None = mês ainda não ocorreu (futuro)
    matrix: dict[str, dict[int, float | None]] = {}
    for cohort_str in cohorts_sorted:
        year_c = int(cohort_str[:4])
        month_c = int(cohort_str[5:7])
        cohort_ym_val = year_c * 12 + month_c

        size = cohort_sizes.get(cohort_str, 0)
        if size == 0:
            continue

        matrix[cohort_str] = {}
        for offset in range(3, max_offset + 1):
            obs_ym = cohort_ym_val + offset
            if obs_ym > ref_ym:
                matrix[cohort_str][offset] = None  # mês futuro
            else:
                n_churn = int(churn_counts.get((cohort_str, offset), 0))
                matrix[cohort_str][offset] = round(n_churn / size * 100, 1)

    summary = {
        "cohort_by": cohort_by,
        "cohort_coluna": cohort_col,
        "cohort_matrix": matrix,
        "cohort_sizes": cohort_sizes,
        "min_offset": 3,
        "max_offset": max_offset,
        "n_cohorts": len(cohorts_sorted),
    }

    # tabular_data: versão plana (não usada para display, apenas referência)
    tabular = [
        {"Cohort": c, "Mês": off, "% Churn": pct}
        for c, offsets in matrix.items()
        for off, pct in offsets.items()
        if pct is not None
    ]

    return summary, tabular, ops


def _calc_financial_summary(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Análise financeira geral: valor total, médio, mediano, top produtores,
    distribuição por cluster e por gestor.
    """
    ops = ["Iniciando análise financeira"]

    df = _filter_by_period(vendas, plan)
    ops.append(f"Período filtrado: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    # Filtro opcional de status
    status_filter = plan.get_filter("status")
    if status_filter:
        df = df[df["Status"].str.lower() == status_filter.lower()]
        ops.append(f"Filtrado por Status='{status_filter}': {len(df)} linhas")

    if df.empty or "Valor" not in df.columns:
        return {"aviso": "Nenhum dado financeiro disponível para o período e filtros informados."}, [], ops

    # Valor total por produtor no período
    valor_por_prod = (
        df.groupby("Código")["Valor"].sum().reset_index(name="Valor Total (R$)")
    )
    # Preserva nome do produtor
    nomes = df[["Código", "Produtor"]].drop_duplicates(subset=["Código"])
    valor_por_prod = valor_por_prod.merge(nomes, on="Código", how="left")

    # Join com Cluster e Gestor
    valor_por_prod = valor_por_prod.merge(
        produtores[["Código", "Cluster", "Gestor"]],
        on="Código",
        how="left",
    )
    valor_por_prod["Cluster"] = valor_por_prod["Cluster"].fillna("Não classificado")
    valor_por_prod["Gestor"] = valor_por_prod["Gestor"].fillna("Sem gestor")

    total_geral = round(valor_por_prod["Valor Total (R$)"].sum(), 2)
    media = round(valor_por_prod["Valor Total (R$)"].mean(), 2)
    mediana = round(valor_por_prod["Valor Total (R$)"].median(), 2)

    # Top N por valor (dinâmico via ReAct tool params, padrão 10)
    _top_n_raw = plan.filters.get("top_n")
    try:
        _top_n = int(_top_n_raw) if _top_n_raw and str(_top_n_raw).lower() not in ("null", "none", "") else 10
    except (ValueError, TypeError):
        _top_n = 10
    top10 = (
        valor_por_prod.nlargest(_top_n, "Valor Total (R$)")[["Produtor", "Valor Total (R$)", "Cluster", "Gestor"]]
        .round(2)
    )

    # Distribuição por cluster
    por_cluster = (
        valor_por_prod.groupby("Cluster")["Valor Total (R$)"]
        .sum().round(2).to_dict()
    )

    # Distribuição por gestor
    por_gestor = (
        valor_por_prod.groupby("Gestor")["Valor Total (R$)"]
        .sum().round(2).to_dict()
    )

    ops.append(f"Produtores analisados: {len(valor_por_prod)} | Valor total: R$ {total_geral:,.2f}")

    summary = {
        "valor_total": total_geral,
        "valor_medio_por_produtor": media,
        "valor_mediano_por_produtor": mediana,
        "total_produtores": len(valor_por_prod),
        "por_cluster": por_cluster,
        "por_gestor": por_gestor,
    }

    return summary, top10.to_dict("records"), ops


def _calc_churn_value_impact(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Calcula o valor em risco por churn:
    - Identifica produtores que transitaram para Pré-churn ou Churn no período
    - Calcula o valor médio mensal histórico (meses Ativos) de cada um
    - Retorna valor total em risco, agrupado por cluster e gestor
    """
    ops = ["Calculando impacto financeiro do churn"]

    df = _filter_by_period(vendas, plan)
    ops.append(f"Período filtrado: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    # Produtores em risco no período (status final = Pré-churn ou Churn)
    latest = (
        df.sort_values("Data")
        .groupby("Código", as_index=False)
        .last()[["Código", "Produtor", "Status", "Data"]]
    )
    at_risk = latest[latest["Status"].isin(["Pré-churn", "Churn"])].copy()
    ops.append(f"Produtores em risco no período: {len(at_risk)}")

    if at_risk.empty:
        return {"aviso": "Nenhum produtor em risco no período informado."}, [], ops

    # Valor médio mensal histórico: média dos meses Ativos em todo o histórico
    valor_historico = (
        vendas[vendas["Status"] == "Ativo"]
        .groupby("Código")["Valor"]
        .mean()
        .round(2)
        .rename("Valor Médio Mensal Histórico (R$)")
    )
    at_risk = at_risk.merge(valor_historico, on="Código", how="left")
    at_risk["Valor Médio Mensal Histórico (R$)"] = at_risk["Valor Médio Mensal Histórico (R$)"].fillna(0.0)

    # Join com Cluster e Gestor
    at_risk = at_risk.merge(
        produtores[["Código", "Cluster", "Gestor"]],
        on="Código",
        how="left",
    )
    at_risk["Cluster"] = at_risk["Cluster"].fillna("Não classificado")
    at_risk["Gestor"] = at_risk["Gestor"].fillna("Sem gestor")

    valor_total_risco = round(at_risk["Valor Médio Mensal Histórico (R$)"].sum(), 2)

    # Agrupamento por cluster
    por_cluster = (
        at_risk.groupby("Cluster")["Valor Médio Mensal Histórico (R$)"]
        .agg(produtores_em_risco="count", valor_em_risco="sum")
        .round(2)
        .reset_index()
        .to_dict("records")
    )

    # Agrupamento por gestor
    por_gestor = (
        at_risk.groupby("Gestor")["Valor Médio Mensal Histórico (R$)"]
        .agg(produtores_em_risco="count", valor_em_risco="sum")
        .round(2)
        .reset_index()
        .to_dict("records")
    )

    ops.append(f"Valor total em risco: R$ {valor_total_risco:,.2f}")

    summary = {
        "total_produtores_em_risco": len(at_risk),
        "prechurn": int((at_risk["Status"] == "Pré-churn").sum()),
        "churn": int((at_risk["Status"] == "Churn").sum()),
        "valor_total_em_risco": valor_total_risco,
        "por_cluster": por_cluster,
        "por_gestor": por_gestor,
    }

    at_risk["Último Registro"] = at_risk["Data"].dt.strftime("%Y-%m")
    tabular_cols = ["Produtor", "Status", "Valor Médio Mensal Histórico (R$)", "Cluster", "Gestor", "Último Registro"]
    return summary, at_risk[tabular_cols].sort_values("Valor Médio Mensal Histórico (R$)", ascending=False).to_dict("records"), ops


# ---------------------------------------------------------------------------
# LTV e ciclos de vida
# ---------------------------------------------------------------------------

def _extract_cycles(producer_history: pd.DataFrame) -> list[dict]:
    """
    Extrai os ciclos de vida de um único produtor a partir do seu histórico
    ordenado por Data.

    Regras:
      - Novo ciclo começa quando: primeiro registro Ativo  OU
        Status == "Ativo" E Status_Anterior == "Churn"
      - Ciclo encerrado quando: Status == "Churn"
      - Valor acumulado = soma de Valor nos meses Ativo + Pré-churn do ciclo

    Retorna lista de dicts:
      [{"ciclo": int, "inicio": date, "fim": date|None,
        "meses": int, "valor_total": float, "encerrado": bool}]
    """
    sorted_hist = producer_history.sort_values("Data")
    rows = sorted_hist.itertuples(index=False)

    # Último mês disponível no histórico do produtor
    last_date_raw = sorted_hist["Data"].iloc[-1]
    last_date = last_date_raw.date() if hasattr(last_date_raw, "date") else last_date_raw

    cycles: list[dict] = []
    current: dict | None = None

    for row in rows:
        status = str(row.Status)
        data = row.Data
        valor = float(row.Valor) if not pd.isna(row.Valor) else 0.0

        # Detecta início de novo ciclo:
        #   - primeiro registro Ativo (current is None)
        #   - Ativo após Churn (reativação)
        start_new = False
        if status == "Ativo" and current is None:
            start_new = True

        if start_new:
            current = {
                "ciclo": len(cycles) + 1,
                "inicio": data.date() if hasattr(data, "date") else data,
                "fim": last_date,  # será sobrescrito se terminar em Churn
                "meses": 0,
                "valor_total": 0.0,
                "encerrado": False,
            }

        # Acumula dentro do ciclo atual (Ativo ou Pré-churn)
        if current is not None and status in ("Ativo", "Pré-churn"):
            current["meses"] += 1
            current["valor_total"] += valor

        # Encerra ciclo quando chega em Churn
        if current is not None and status == "Churn":
            current["fim"] = data.date() if hasattr(data, "date") else data
            current["encerrado"] = True
            cycles.append(current)
            current = None

    # Ciclo ainda aberto ao fim do histórico: fim = último mês disponível
    if current is not None:
        current["fim"] = last_date
        current["encerrado"] = False
        cycles.append(current)

    for c in cycles:
        c["valor_total"] = round(c["valor_total"], 2)

    return cycles


def _calc_ltv(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    LTV por produtor com suporte a múltiplos ciclos.

    Métricas principais (duração — foco do LTV):
      - ltv_meses_total: soma da duração em meses de todos os ciclos
      - ltv_meses_max_ciclo: duração em meses do ciclo mais longo

    Métricas secundárias (financeiro — complemento):
      - faturamento_total: soma do Valor de todos os ciclos
      - faturamento_max_ciclo: Valor do ciclo de maior faturamento

    Filtros: cluster, gestor, status atual, min_ciclos
    """
    ops = ["Iniciando análise de LTV com ciclos de vida"]

    cluster_filter = plan.get_filter("cluster")
    gestor_filter = plan.get_filter("gestor")
    status_filter = plan.get_filter("status")

    raw_min = plan.filters.get("min_ciclos")
    try:
        min_ciclos = int(raw_min) if raw_min and str(raw_min).lower() not in ("null", "none", "") else 1
    except (ValueError, TypeError):
        min_ciclos = 1

    # Mês de referência (último mês disponível — denominador de % Tempo Ativo)
    ref_date = vendas["Data"].max()
    ref_ym = int(ref_date.year * 12 + ref_date.month)

    # Extrai ciclos para cada produtor
    records = []
    for codigo, hist in vendas.groupby("Código"):
        cycles = _extract_cycles(hist)
        if not cycles:
            continue
        produtor_name = hist["Produtor"].iloc[-1]
        num = len(cycles)
        meses_total = sum(c["meses"] for c in cycles)
        meses_max = max(c["meses"] for c in cycles)
        fat_total = round(sum(c["valor_total"] for c in cycles), 2)
        fat_max = round(max(c["valor_total"] for c in cycles), 2)

        # % Tempo Ativo = meses ativos / meses desde 1ª venda até referência
        primeiro_inicio = cycles[0]["inicio"]  # date
        primeiro_ym = int(primeiro_inicio.year * 12 + primeiro_inicio.month)
        total_meses_sistema = max(ref_ym - primeiro_ym + 1, 1)
        pct_tempo_ativo = round(meses_total / total_meses_sistema * 100, 1)

        records.append({
            "Código": int(codigo),
            "Produtor": produtor_name,
            "LTV Meses Total": meses_total,
            "LTV Meses Maior Ciclo": meses_max,
            "% Tempo Ativo": pct_tempo_ativo,
            "Meses desde 1ª Venda": total_meses_sistema,
            "Faturamento Total (R$)": fat_total,
            "Faturamento Maior Ciclo (R$)": fat_max,
            "Nº Ciclos": num,
            "Reativado": num > 1,
        })

    if not records:
        return {"aviso": "Nenhum ciclo de vida identificado nos dados."}, [], ops

    df_ltv = pd.DataFrame(records)
    ops.append(f"Produtores com ciclos identificados: {len(df_ltv)}")

    # Join com Cluster, Gestor e Data 1ª Venda
    df_ltv = df_ltv.merge(
        produtores[["Código", "Cluster", "Gestor", "Data 1ª Venda"]],
        on="Código",
        how="left",
    )
    df_ltv["Cluster"] = df_ltv["Cluster"].fillna("Não classificado")
    df_ltv["Gestor"] = df_ltv["Gestor"].fillna("Sem gestor")
    df_ltv["Data 1ª Venda"] = df_ltv["Data 1ª Venda"].dt.strftime("%Y-%m").fillna("")

    # Filtro por status atual
    if status_filter:
        latest = _latest_status_per_producer(vendas)
        codigos_status = set(
            latest.loc[latest["Status"].str.lower() == status_filter.lower(), "Código"]
        )
        df_ltv = df_ltv[df_ltv["Código"].isin(codigos_status)]
        ops.append(f"Filtrado por status='{status_filter}': {len(df_ltv)} produtores")

    if cluster_filter:
        df_ltv = df_ltv[df_ltv["Cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtrado por cluster='{cluster_filter}': {len(df_ltv)} produtores")

    if gestor_filter:
        df_ltv = df_ltv[df_ltv["Gestor"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtrado por gestor='{gestor_filter}': {len(df_ltv)} produtores")

    if min_ciclos > 1:
        df_ltv = df_ltv[df_ltv["Nº Ciclos"] >= min_ciclos]
        ops.append(f"Filtrado por min_ciclos>={min_ciclos}: {len(df_ltv)} produtores")

    if df_ltv.empty:
        return {"aviso": "Nenhum produtor encontrado com os filtros aplicados."}, [], ops

    pct_reativados = round(df_ltv["Reativado"].mean() * 100, 1)

    # ── Top 5 por LTV Meses Total e por % Tempo Ativo ─────────────────────
    _top5_cols = [
        "Produtor", "LTV Meses Total", "% Tempo Ativo",
        "Meses desde 1ª Venda", "Faturamento Total (R$)", "Nº Ciclos",
        "Cluster", "Gestor",
    ]
    top5_meses = (
        df_ltv[_top5_cols]
        .sort_values("LTV Meses Total", ascending=False)
        .head(5).to_dict("records")
    )
    top5_pct = (
        df_ltv[_top5_cols]
        .sort_values("% Tempo Ativo", ascending=False)
        .head(5).to_dict("records")
    )

    # ── Análise por Cluster ────────────────────────────────────────────────
    _cluster_agg = (
        df_ltv.groupby("Cluster").agg(
            Produtores=("Produtor", "count"),
            ltv_meses_medio=("LTV Meses Total", "mean"),
            pct_tempo_ativo_medio=("% Tempo Ativo", "mean"),
            faturamento_medio=("Faturamento Total (R$)", "mean"),
            pct_reativados_cluster=("Reativado", "mean"),
        ).reset_index()
    )
    _cluster_agg["LTV Meses Médio"] = _cluster_agg["ltv_meses_medio"].round(1)
    _cluster_agg["% Tempo Ativo Médio"] = _cluster_agg["pct_tempo_ativo_medio"].round(1)
    _cluster_agg["Faturamento Médio (R$)"] = _cluster_agg["faturamento_medio"].round(2)
    _cluster_agg["% Reativados"] = (_cluster_agg["pct_reativados_cluster"] * 100).round(1)
    por_cluster = (
        _cluster_agg[["Cluster", "Produtores", "LTV Meses Médio",
                       "% Tempo Ativo Médio", "Faturamento Médio (R$)", "% Reativados"]]
        .sort_values("LTV Meses Médio", ascending=False)
        .to_dict("records")
    )

    # ── Análise por número de ciclos ──────────────────────────────────────
    df_ltv["ciclos_grupo"] = df_ltv["Nº Ciclos"].apply(lambda x: "4+" if x >= 4 else str(x))
    _ciclos_agg = (
        df_ltv.groupby("ciclos_grupo").agg(
            Produtores=("Produtor", "count"),
            ltv_meses_medio=("LTV Meses Total", "mean"),
            pct_tempo_ativo_medio=("% Tempo Ativo", "mean"),
            faturamento_medio=("Faturamento Total (R$)", "mean"),
        ).reset_index()
    )
    _ciclos_agg["LTV Meses Médio"] = _ciclos_agg["ltv_meses_medio"].round(1)
    _ciclos_agg["% Tempo Ativo Médio"] = _ciclos_agg["pct_tempo_ativo_medio"].round(1)
    _ciclos_agg["Faturamento Médio (R$)"] = _ciclos_agg["faturamento_medio"].round(2)
    _ordem = {"1": 0, "2": 1, "3": 2, "4+": 3}
    _ciclos_agg["_ord"] = _ciclos_agg["ciclos_grupo"].map(_ordem).fillna(99)
    _ciclos_agg = _ciclos_agg.sort_values("_ord")   # ordena antes de fatiar
    por_ciclos = (
        _ciclos_agg[["ciclos_grupo", "Produtores", "LTV Meses Médio",
                      "% Tempo Ativo Médio", "Faturamento Médio (R$)"]]
        .rename(columns={"ciclos_grupo": "Nº Ciclos"})
        .to_dict("records")
    )

    # ── Produtores com baixa % de Tempo Ativo (bottom 25%) ───────────────
    _pct_q25 = float(df_ltv["% Tempo Ativo"].quantile(0.25))

    # Status atual para análise de tendência
    _latest_s = _latest_status_per_producer(vendas)
    df_ltv_cs = df_ltv.merge(
        _latest_s[["Código", "Status"]].rename(columns={"Status": "Status Atual"}),
        on="Código",
        how="left",
    )
    df_ltv_cs["Status Atual"] = df_ltv_cs["Status Atual"].fillna("Desconhecido")
    _df_baixo = df_ltv_cs[df_ltv_cs["% Tempo Ativo"] <= _pct_q25]
    _df_alto  = df_ltv_cs[df_ltv_cs["% Tempo Ativo"] >  _pct_q25]

    # Distribuição do status atual entre produtores de baixa % Tempo Ativo
    _baixo_por_status = (
        _df_baixo.groupby("Status Atual")["Produtor"].count()
        .reset_index(name="Produtores")
        .sort_values("Produtores", ascending=False)
        .to_dict("records")
    )

    # Tendência: % em Churn hoje — baixa vs alta % Tempo Ativo
    def _pct_churn_hoje(df: pd.DataFrame) -> float:
        return round((df["Status Atual"] == "Churn").sum() / len(df) * 100, 1) if not df.empty else 0.0

    _pct_churn_baixo = _pct_churn_hoje(_df_baixo)
    _pct_churn_alto  = _pct_churn_hoje(_df_alto)

    _baixo_por_cluster = (
        _df_baixo.groupby("Cluster")["Produtor"].count()
        .reset_index(name="Produtores")
        .sort_values("Produtores", ascending=False)
        .to_dict("records")
    )
    _baixo_top5 = (
        _df_baixo[["Produtor", "LTV Meses Total", "% Tempo Ativo",
                    "Faturamento Total (R$)", "Nº Ciclos", "Cluster", "Gestor", "Status Atual"]]
        .sort_values("% Tempo Ativo")
        .head(5).to_dict("records")
    )
    baixo_ltv = {
        "threshold_pct": round(_pct_q25, 1),
        "total": len(_df_baixo),
        "pct_tempo_ativo_medio": round(_df_baixo["% Tempo Ativo"].mean(), 1) if not _df_baixo.empty else 0.0,
        "faturamento_medio": round(_df_baixo["Faturamento Total (R$)"].mean(), 2) if not _df_baixo.empty else 0.0,
        "por_status": _baixo_por_status,
        "por_cluster": _baixo_por_cluster,
        "top5_mais_baixos": _baixo_top5,
        "tendencia": {
            "pct_churn_entre_baixa_pct_ativo": _pct_churn_baixo,
            "pct_churn_entre_alta_pct_ativo": _pct_churn_alto,
            "total_baixa": len(_df_baixo),
            "total_alta": len(_df_alto),
        },
    }

    summary = {
        "total_produtores": len(df_ltv),
        "ltv_meses_total_medio": round(df_ltv["LTV Meses Total"].mean(), 1),
        "ltv_meses_max_ciclo_medio": round(df_ltv["LTV Meses Maior Ciclo"].mean(), 1),
        "pct_tempo_ativo_medio": round(df_ltv["% Tempo Ativo"].mean(), 1),
        "pct_tempo_ativo_mediana": round(df_ltv["% Tempo Ativo"].median(), 1),
        "faturamento_total_medio": round(df_ltv["Faturamento Total (R$)"].mean(), 2),
        "faturamento_max_ciclo_medio": round(df_ltv["Faturamento Maior Ciclo (R$)"].mean(), 2),
        "pct_reativados": pct_reativados,
        "top5_ltv_meses": top5_meses,
        "top5_pct_ativo": top5_pct,
        "por_cluster": por_cluster,
        "por_ciclos": por_ciclos,
        "baixo_ltv": baixo_ltv,
    }

    tabular_cols = [
        "Produtor",
        "LTV Meses Total", "LTV Meses Maior Ciclo",
        "% Tempo Ativo", "Meses desde 1ª Venda",
        "Faturamento Total (R$)", "Faturamento Maior Ciclo (R$)",
        "Nº Ciclos", "Reativado", "Cluster", "Gestor", "Data 1ª Venda",
    ]
    tabular = (
        df_ltv[tabular_cols]
        .sort_values("LTV Meses Total", ascending=False)
        .head(50)
        .to_dict("records")
    )

    ops.append(
        f"LTV médio: {summary['ltv_meses_total_medio']} meses "
        f"(R$ {summary['faturamento_total_medio']:,.2f}) | "
        f"% Tempo Ativo médio: {summary['pct_tempo_ativo_medio']}% | "
        f"Reativados: {pct_reativados}%"
    )
    return _to_native(summary), _to_native(tabular), ops


def _calc_cycle_analysis(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Análise dos ciclos de vida explodidos:
      - Distribuição por duração dos ciclos
      - Taxa de reativação: % de ciclos encerrados seguidos por novo ciclo
      - Stats por número do ciclo (1º, 2º, 3º+): duração média e valor médio
    """
    ops = ["Iniciando análise de ciclos de vida"]

    cluster_filter = plan.get_filter("cluster")
    gestor_filter = plan.get_filter("gestor")

    # Constrói lista plana de todos os ciclos
    all_cycles: list[dict] = []
    for codigo, hist in vendas.groupby("Código"):
        cycles = _extract_cycles(hist)
        produtor_name = hist["Produtor"].iloc[-1]
        for c in cycles:
            all_cycles.append({
                "Código": int(codigo),
                "Produtor": produtor_name,
                **c,
            })

    if not all_cycles:
        return {"aviso": "Nenhum ciclo identificado nos dados."}, [], ops

    df_cycles = pd.DataFrame(all_cycles)
    ops.append(f"Total de ciclos explodidos: {len(df_cycles)} de {df_cycles['Código'].nunique()} produtores")

    # Join com Cluster e Gestor
    df_cycles = df_cycles.merge(
        produtores[["Código", "Cluster", "Gestor"]],
        on="Código",
        how="left",
    )
    df_cycles["Cluster"] = df_cycles["Cluster"].fillna("Não classificado")
    df_cycles["Gestor"] = df_cycles["Gestor"].fillna("Sem gestor")

    if cluster_filter:
        df_cycles = df_cycles[df_cycles["Cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtrado por cluster='{cluster_filter}'")

    if gestor_filter:
        df_cycles = df_cycles[df_cycles["Gestor"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtrado por gestor='{gestor_filter}'")

    if df_cycles.empty:
        return {"aviso": "Nenhum ciclo encontrado com os filtros aplicados."}, [], ops

    # Taxa de reativação: ciclos encerrados cujo produtor tem ciclo posterior
    encerrados = df_cycles[df_cycles["encerrado"]].copy()
    reativacoes = encerrados[encerrados["ciclo"] < df_cycles.groupby("Código")["ciclo"].transform("max")]
    taxa_reativacao = round(len(reativacoes) / len(encerrados) * 100, 1) if len(encerrados) else 0.0

    # Stats por número do ciclo (1º, 2º, 3º+)
    df_cycles["ciclo_label"] = df_cycles["ciclo"].apply(
        lambda n: "1º ciclo" if n == 1 else ("2º ciclo" if n == 2 else "3º ciclo ou mais")
    )
    stats_ciclo = (
        df_cycles[df_cycles["meses"] > 0]
        .groupby("ciclo_label")
        .agg(
            total=("ciclo", "count"),
            duracao_media_meses=("meses", "mean"),
            valor_medio=("valor_total", "mean"),
        )
        .round(2)
        .reset_index()
    )

    # Distribuição por duração (em buckets)
    bins = [0, 1, 3, 6, 12, float("inf")]
    labels = ["1 mês", "2-3 meses", "4-6 meses", "7-12 meses", "13+ meses"]
    df_cycles["duracao_bucket"] = pd.cut(df_cycles["meses"], bins=bins, labels=labels, right=True)
    dist_duracao = (
        df_cycles.groupby("duracao_bucket", observed=True)
        .size()
        .reset_index(name="Qtd Ciclos")
    )
    dist_duracao.rename(columns={"duracao_bucket": "Duração"}, inplace=True)

    summary = {
        "total_ciclos": len(df_cycles),
        "total_produtores": df_cycles["Código"].nunique(),
        "ciclos_encerrados": int(df_cycles["encerrado"].sum()),
        "taxa_reativacao_pct": taxa_reativacao,
        "duracao_media_meses": round(df_cycles[df_cycles["meses"] > 0]["meses"].mean(), 1),
        "valor_medio_por_ciclo": round(df_cycles["valor_total"].mean(), 2),
        "por_numero_ciclo": stats_ciclo.to_dict("records"),
    }

    ops.append(
        f"Ciclos encerrados: {summary['ciclos_encerrados']} | "
        f"Taxa de reativação: {taxa_reativacao}%"
    )

    return summary, dist_duracao.to_dict("records"), ops


# ---------------------------------------------------------------------------
# Taxa de churn TMB
# ---------------------------------------------------------------------------

def _mes_label(month: int, year: int) -> str:
    return f"{_MONTH_PT[month - 1]}/{year}"


def _prev_month(month: int, year: int) -> tuple[int, int]:
    return (12, year - 1) if month == 1 else (month - 1, year)


def _calc_churn_rate_base(
    vendas: pd.DataFrame, month: int, year: int, produtores: pd.DataFrame
) -> dict:
    """
    Taxa de churn TMB para o mês M.

    Fórmula:
      Numerador   = produtores com Status=="Churn" AND Status_Anterior=="Pré-churn" em M
                    (contagem distinta por Código)
      Denominador = produtores com Status=="Ativo" OR Status=="Pré-churn" em M-1
                    (contagem distinta por Código)
      Taxa        = Numerador / Denominador * 100

    Exclui produtores cujo Gestor está em GESTORES_EXCLUIDOS_CHURN.
    """
    # Exclui gestores fora do escopo do cálculo de churn
    codigos_excluidos = set(
        produtores.loc[produtores["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN), "Código"]
    )
    vendas = vendas[~vendas["Código"].isin(codigos_excluidos)]

    pm, py = _prev_month(month, year)

    # Denominador: base do mês anterior (M-1)
    mes_anterior = vendas[(vendas["Data"].dt.month == pm) & (vendas["Data"].dt.year == py)]
    if mes_anterior.empty:
        return {
            "mes": _mes_label(month, year),
            "mes_data": f"{year}-{month:02d}",
            "aviso": f"Sem dados para {_mes_label(pm, py)} (mês anterior) — taxa não calculada.",
            "churns_novos": None,
            "base_mes_anterior": None,
            "taxa_pct": None,
            "acima_da_meta": None,
            "diferenca_meta_pp": None,
        }
    base = int(
        mes_anterior[mes_anterior["Status"].isin(["Ativo", "Pré-churn"])]["Código"].nunique()
    )

    # Numerador: novos churns em M (Pré-churn → Churn)
    mes_atual = vendas[(vendas["Data"].dt.month == month) & (vendas["Data"].dt.year == year)]
    churns_novos = int(
        mes_atual[
            (mes_atual["Status"] == "Churn") & (mes_atual["Status_Anterior"] == "Pré-churn")
        ]["Código"].nunique()
    )

    taxa_pct = round(churns_novos / base * 100, 2) if base > 0 else 0.0

    return {
        "mes": _mes_label(month, year),
        "mes_data": f"{year}-{month:02d}",
        "churns_novos": churns_novos,
        "base_mes_anterior": base,
        "taxa_pct": taxa_pct,
        "acima_da_meta": taxa_pct > META_CHURN_PCT,
        "diferenca_meta_pp": round(taxa_pct - META_CHURN_PCT, 2),
    }


def _calc_churn_rate_base_dim(
    vendas_dim: pd.DataFrame, month: int, year: int, dim_value: str
) -> dict:
    """Taxa de churn para um mês e uma dimensão (gestor ou cluster)."""
    pm, py = _prev_month(month, year)

    prev = vendas_dim[(vendas_dim["Data"].dt.month == pm) & (vendas_dim["Data"].dt.year == py)]
    base = int(prev[prev["Status"].isin(["Ativo", "Pré-churn"])]["Código"].nunique())

    curr = vendas_dim[(vendas_dim["Data"].dt.month == month) & (vendas_dim["Data"].dt.year == year)]
    churns_novos = int(curr[
        (curr["Status"] == "Churn") & (curr["Status_Anterior"] == "Pré-churn")
    ]["Código"].nunique())

    taxa_pct = round(churns_novos / base * 100, 2) if base > 0 else 0.0
    diferenca = round(taxa_pct - META_CHURN_PCT, 2)

    if taxa_pct > META_CHURN_PCT:
        semaforo = "acima_meta"
    elif taxa_pct >= META_CHURN_PCT - 1.0:  # entre 4% e 5%
        semaforo = "na_meta"
    else:
        semaforo = "abaixo_meta"

    return {
        "dimensao": dim_value,
        "mes": _mes_label(month, year),
        "churns_novos": churns_novos,
        "base_mes_anterior": base,
        "taxa_pct": taxa_pct,
        "semaforo": semaforo,
        "diferenca_meta_pp": diferenca,
    }


def _calc_churn_rate_analysis(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Taxa de churn TMB com três modos:
      Modo 1 — mês específico (month + year): detalhe + breakdown gestor
      Modo 2 — histórico (sem filtro): série temporal completa
      Modo 3 — por gestor (group_by="Gestor"): taxa por gestor com semáforo
    """
    ops = ["Calculando taxa de churn TMB (Pré-churn→Churn / base Ativo+Pré-churn mês anterior)"]

    # Exclui gestores fora do escopo do cálculo de churn
    codigos_excluidos = set(
        produtores.loc[produtores["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN), "Código"]
    )
    vendas = vendas[~vendas["Código"].isin(codigos_excluidos)]
    if codigos_excluidos:
        ops.append(f"Excluídos {len(codigos_excluidos)} produtores de: {GESTORES_EXCLUIDOS_CHURN}")

    raw_month = plan.filters.get("month")
    raw_year = plan.filters.get("year")
    try:
        month = int(raw_month) if raw_month and str(raw_month).lower() not in ("null", "none", "") else None
        year = int(raw_year) if raw_year and str(raw_year).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        month, year = None, None

    group_by = (plan.group_by or "").lower()

    latest_date = vendas["Data"].max()
    ref_month = month or latest_date.month
    ref_year = year or latest_date.year

    # Modo 3 — breakdown por gestor
    if group_by == "gestor":
        dim_col = "Gestor"
        vendas_dim = vendas.merge(produtores[["Código", dim_col]], on="Código", how="left")
        vendas_dim[dim_col] = vendas_dim[dim_col].fillna("Sem gestor")

        rows = []
        for dim_val, grp in vendas_dim.groupby(dim_col):
            if str(dim_val) == "Sem gestor":
                continue
            r = _calc_churn_rate_base_dim(grp, ref_month, ref_year, str(dim_val))
            rows.append(r)
        rows.sort(key=lambda r: r["taxa_pct"], reverse=True)

        acima = sum(1 for r in rows if r["semaforo"] == "acima_meta")
        na = sum(1 for r in rows if r["semaforo"] == "na_meta")
        abaixo = sum(1 for r in rows if r["semaforo"] == "abaixo_meta")

        summary = {
            "mes_referencia": _mes_label(ref_month, ref_year),
            "meta_pct": META_CHURN_PCT,
            "total_dimensoes": len(rows),
            "acima_da_meta": acima,
            "na_meta": na,
            "abaixo_da_meta": abaixo,
            "agrupamento": dim_col,
        }
        ops.append(f"{dim_col}: {acima} acima, {na} na meta, {abaixo} abaixo | {_mes_label(ref_month, ref_year)}")
        return summary, rows, ops

    # Modo 1 — mês específico
    if month and year:
        base_result = _calc_churn_rate_base(vendas, month, year, produtores)
        ops.append(
            f"Mês: {base_result['mes']} | "
            f"{base_result['churns_novos']} churns / {base_result['base_mes_anterior']} base = {base_result['taxa_pct']}%"
        )

        vendas_gest = vendas.merge(produtores[["Código", "Gestor"]], on="Código", how="left")
        vendas_gest["Gestor"] = vendas_gest["Gestor"].fillna("Sem gestor")
        breakdown_gestor = sorted(
            [_calc_churn_rate_base_dim(grp, month, year, str(gv))
             for gv, grp in vendas_gest.groupby("Gestor")
             if str(gv) != "Sem gestor"],
            key=lambda r: r["taxa_pct"], reverse=True,
        )

        summary = {
            **base_result,
            "meta_pct": META_CHURN_PCT,
            "breakdown_gestor": breakdown_gestor,
        }
        return summary, [base_result], ops

    # Modo 2 — série histórica
    available = vendas[["Data"]].drop_duplicates().sort_values("Data")
    series = []
    for _, row in available.iterrows():
        m, y = row["Data"].month, row["Data"].year
        r = _calc_churn_rate_base(vendas, m, y, produtores)
        if r.get("base_mes_anterior") and r["base_mes_anterior"] > 0:
            series.append(r)

    if not series:
        return {"aviso": "Dados insuficientes para calcular série histórica."}, [], ops

    taxas = [r["taxa_pct"] for r in series]
    meses_acima = sum(1 for t in taxas if t > META_CHURN_PCT)

    summary = {
        "meta_pct": META_CHURN_PCT,
        "total_meses": len(series),
        "taxa_media_pct": round(sum(taxas) / len(taxas), 2),
        "melhor_mes": min(series, key=lambda r: r["taxa_pct"])["mes"],
        "melhor_taxa_pct": min(taxas),
        "pior_mes": max(series, key=lambda r: r["taxa_pct"])["mes"],
        "pior_taxa_pct": max(taxas),
        "meses_acima_da_meta": meses_acima,
        "pct_meses_acima_meta": round(meses_acima / len(series) * 100, 1),
    }

    ops.append(f"Série histórica: {len(series)} meses | média {summary['taxa_media_pct']}% | {meses_acima} acima da meta")
    return summary, series, ops


def _calc_churn_rate_streak(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Meses consecutivos acima da meta de churn (5%) por gestor."""
    ops = ["Calculando streak de meses acima da meta por gestor"]

    # Exclui gestores fora do escopo do cálculo de churn
    codigos_excluidos = set(
        produtores.loc[produtores["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN), "Código"]
    )
    vendas = vendas[~vendas["Código"].isin(codigos_excluidos)]

    vendas_gest = vendas.merge(produtores[["Código", "Gestor"]], on="Código", how="left")
    vendas_gest["Gestor"] = vendas_gest["Gestor"].fillna("Sem gestor")

    available = sorted(vendas[["Data"]].drop_duplicates()["Data"].tolist())

    records = []
    for gestor, grp in vendas_gest.groupby("Gestor"):
        monthly = []
        for ts in available:
            r = _calc_churn_rate_base_dim(grp, ts.month, ts.year, str(gestor))
            if r["base_mes_anterior"] > 0:
                monthly.append(r)

        if not monthly:
            continue

        # Streak: meses consecutivos acima da meta contados do fim
        streak = 0
        for r in reversed(monthly):
            if r["taxa_pct"] > META_CHURN_PCT:
                streak += 1
            else:
                break

        melhor = min(monthly, key=lambda r: r["taxa_pct"])
        pior = max(monthly, key=lambda r: r["taxa_pct"])

        records.append({
            "Gestor": str(gestor),
            "Streak Acima da Meta (meses)": streak,
            "Taxa Atual (%)": monthly[-1]["taxa_pct"],
            "Melhor Mês": melhor["mes"],
            "Melhor Taxa (%)": melhor["taxa_pct"],
            "Pior Mês": pior["mes"],
            "Pior Taxa (%)": pior["taxa_pct"],
        })

    records.sort(key=lambda r: r["Streak Acima da Meta (meses)"], reverse=True)

    gestores_streak = sum(1 for r in records if r["Streak Acima da Meta (meses)"] > 0)
    summary = {
        "meta_pct": META_CHURN_PCT,
        "total_gestores": len(records),
        "gestores_em_streak": gestores_streak,
        "max_streak_meses": max((r["Streak Acima da Meta (meses)"] for r in records), default=0),
    }

    ops.append(f"Gestores com streak ativo: {gestores_streak} de {len(records)}")
    return summary, records, ops


def _calc_churn_rate_trend(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Série histórica de taxa de churn com média móvel de 3 meses e tendência."""
    ops = ["Calculando tendência histórica de taxa de churn"]

    # Exclui gestores fora do escopo do cálculo de churn
    codigos_excluidos = set(
        produtores.loc[produtores["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN), "Código"]
    )
    vendas = vendas[~vendas["Código"].isin(codigos_excluidos)]

    available = vendas[["Data"]].drop_duplicates().sort_values("Data")
    series = []
    for _, row in available.iterrows():
        m, y = row["Data"].month, row["Data"].year
        r = _calc_churn_rate_base(vendas, m, y, produtores)
        if r.get("base_mes_anterior") and r["base_mes_anterior"] > 0:
            series.append(r)

    if len(series) < 2:
        return {"aviso": "Dados insuficientes para calcular tendência."}, [], ops

    # Média móvel de 3 meses
    for i, r in enumerate(series):
        window = series[max(0, i - 2):i + 1]
        r["media_movel_3m"] = round(sum(w["taxa_pct"] for w in window) / len(window), 2)

    # Tendência: variação entre início e fim dos últimos 3 meses
    last3 = series[-3:] if len(series) >= 3 else series
    delta = last3[-1]["taxa_pct"] - last3[0]["taxa_pct"]
    tendencia = "melhora" if delta < 0 else ("piora" if delta > 0 else "estavel")

    taxas = [r["taxa_pct"] for r in series]
    summary = {
        "meta_pct": META_CHURN_PCT,
        "total_meses": len(series),
        "taxa_media_pct": round(sum(taxas) / len(taxas), 2),
        "tendencia_recente": tendencia,
        "variacao_ultimos_3m_pp": round(delta, 2),
        "ultimo_mes": series[-1]["mes"],
        "ultima_taxa_pct": series[-1]["taxa_pct"],
        "ultima_media_movel_3m": series[-1]["media_movel_3m"],
    }

    ops.append(f"Tendência: {tendencia} ({delta:+.1f}pp nos últimos 3 meses) | última taxa: {series[-1]['taxa_pct']}%")
    return summary, series, ops


# ---------------------------------------------------------------------------
# Resumo visual de churn (Modo 1)
# ---------------------------------------------------------------------------

def _calc_churn_report_summary(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Computa apenas os KPIs necessários para o card de resumo (Modo 1).
    Sem status_transitions, sem at_risk, sem loop de gestores completo.
    """
    ops = ["Iniciando resumo de churn (Modo 1)"]

    # ── Período de referência ──────────────────────────────────────────────
    latest = vendas["Data"].max()
    ref_month, ref_year = latest.month, latest.year
    ops.append(f"Referência: {_mes_label(ref_month, ref_year)}")

    # ── Exclui TMB Educação dos cálculos de taxa ───────────────────────────
    codigos_excluidos = set(
        produtores.loc[produtores["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN), "Código"]
    )
    vendas_f = vendas[~vendas["Código"].isin(codigos_excluidos)]

    # ── Status do mês (excl. Inativo) ─────────────────────────────────────
    vendas_ref = vendas[
        (vendas["Data"].dt.month == ref_month)
        & (vendas["Data"].dt.year == ref_year)
        & (vendas["Status"] != "Inativo")
    ]
    latest_per_prod = (
        vendas_ref.sort_values("Data")
        .groupby("Código", as_index=False)
        .last()[["Código", "Status"]]
    )
    counts = latest_per_prod["Status"].value_counts()
    total_base = int(counts.sum())

    n_churn    = int(counts.get("Churn", 0))
    n_ativo    = int(counts.get("Ativo", 0))
    n_prechurn = int(counts.get("Pré-churn", 0))
    pct_churn    = round(n_churn    / total_base * 100, 1) if total_base else 0.0
    pct_ativo    = round(n_ativo    / total_base * 100, 1) if total_base else 0.0
    pct_prechurn = round(n_prechurn / total_base * 100, 1) if total_base else 0.0

    # ── Taxa de churn do mês atual ────────────────────────────────────────
    rate = _calc_churn_rate_base(vendas_f, ref_month, ref_year, produtores)
    taxa_churn = rate.get("taxa_pct", 0.0)
    n_novos_churns = int(rate.get("churns_novos", 0) or 0)
    delta = round(taxa_churn - META_CHURN_PCT, 2)
    ops.append(f"Taxa de churn: {taxa_churn}% (delta: {delta:+.2f}pp)")

    # ── Tendência: taxa do mês anterior para o alerta ─────────────────────
    _vf_h = vendas_f.copy()
    _vf_h["_period"] = _vf_h["Data"].dt.to_period("M")
    _base_pp = (
        _vf_h[_vf_h["Status"].isin(["Ativo", "Pré-churn"])]
        .groupby("_period")["Código"].nunique()
    )
    _churn_pp = (
        _vf_h[(_vf_h["Status"] == "Churn") & (_vf_h["Status_Anterior"] == "Pré-churn")]
        .groupby("_period")["Código"].nunique()
    )
    pm1, py1 = _prev_month(ref_month, ref_year)
    pm0, py0 = _prev_month(pm1, py1)
    p_curr = pd.Period(f"{py1}-{pm1:02d}", freq="M")
    p_base = pd.Period(f"{py0}-{pm0:02d}", freq="M")
    base_m1  = int(_base_pp.get(p_base, 0))
    churn_m1 = int(_churn_pp.get(p_curr, 0))
    taxa_m1  = round(churn_m1 / base_m1 * 100, 2) if base_m1 > 0 else None

    def _fmtpp(v: float) -> str:
        return f"{'+' if v >= 0 else ''}{v:.2f}pp".replace(".", ",")

    if taxa_churn > META_CHURN_PCT:
        if taxa_m1 is not None and taxa_churn > taxa_m1:
            diff = f"{(taxa_churn - taxa_m1):.2f}".replace(".", ",")
            tendencia_alert = (
                f"Taxa em alta: subiu {diff}pp em relação ao mês anterior "
                f"e está {_fmtpp(delta)} acima da meta de 5%."
            )
        elif taxa_m1 is not None and taxa_churn < taxa_m1:
            diff = f"{(taxa_m1 - taxa_churn):.2f}".replace(".", ",")
            tendencia_alert = (
                f"Taxa em queda ({diff}pp vs. mês anterior), "
                f"mas ainda {_fmtpp(delta)} acima da meta de 5%."
            )
        else:
            taxa_fmt = f"{taxa_churn:.2f}".replace(".", ",")
            tendencia_alert = (
                f"Taxa de churn em {taxa_fmt}% — {_fmtpp(delta)} acima da meta."
            )
    else:
        tendencia_alert = ""

    # ── Gestor com maior taxa de churn no mês (churns novos / base mês anterior) ───
    _excluidos_rel = set(GESTORES_EXCLUIDOS_CHURN) | set(GESTORES_EXCLUIDOS_RELATORIO)

    # Histórico completo para que _calc_churn_rate_base_dim acesse o mês anterior
    _vendas_gest_h = vendas_f.merge(produtores[["Código", "Gestor"]], on="Código", how="left")
    _vendas_gest_h = _vendas_gest_h.copy()
    _vendas_gest_h["Gestor"] = _vendas_gest_h["Gestor"].fillna("Sem gestor")

    gestor_pior = None
    taxa_pior = -1.0
    for g, grp in _vendas_gest_h.groupby("Gestor"):
        if g in _excluidos_rel:
            continue
        r = _calc_churn_rate_base_dim(grp, ref_month, ref_year, str(g))
        base_ant = int(r.get("base_mes_anterior", 0))
        if base_ant < MIN_CARTEIRA_GESTOR:
            continue
        t = float(r.get("taxa_pct", 0.0))
        if t > taxa_pior:
            taxa_pior = t
            gestor_pior = {
                "nome": g,
                "taxa": round(t, 1),
                "churns_novos": int(r.get("churns_novos", 0)),
                "carteira": base_ant,
            }

    ops.append(f"Gestor pior: {gestor_pior['nome'] if gestor_pior else 'N/A'}")

    summary_stats = {
        "mes_referencia": _mes_label(ref_month, ref_year),
        "taxa_churn": taxa_churn,
        "n_novos_churns": n_novos_churns,
        "delta_vs_meta": delta,
        "n_churn": n_churn,
        "pct_churn": pct_churn,
        "n_ativo": n_ativo,
        "pct_ativo": pct_ativo,
        "n_prechurn": n_prechurn,
        "pct_prechurn": pct_prechurn,
        "tendencia_alert": tendencia_alert,
        "gestor_pior": gestor_pior,
    }
    return summary_stats, [], ops


# ---------------------------------------------------------------------------
# Relatório completo de churn
# ---------------------------------------------------------------------------

class _MockPlan:
    """QueryPlan mínimo para chamadas internas do churn_report."""
    def __init__(self, filters: dict, group_by: str | None = None):
        self.query_type = "internal"
        self.filters = filters
        self.group_by = group_by
        self.metrics = ["resumo_geral"]

    def get_filter(self, key: str) -> str | None:
        v = self.filters.get(key)
        return str(v).strip() if v and str(v).strip().lower() not in ("null", "none", "") else None


def _calc_churn_report(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Relatório completo de churn — consolida chamadas a funções existentes.
    Não reimplementa lógica; apenas orquestra e formata.
    """
    ops = ["Iniciando relatório completo de churn"]

    # ── Período de referência ──────────────────────────────────────────────
    raw_month = plan.filters.get("month")
    raw_year = plan.filters.get("year")
    try:
        ref_month = int(raw_month) if raw_month and str(raw_month).lower() not in ("null", "none", "") else None
        ref_year = int(raw_year) if raw_year and str(raw_year).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        ref_month, ref_year = None, None

    latest = vendas["Data"].max()
    ref_month = ref_month or latest.month
    ref_year = ref_year or latest.year
    ops.append(f"Período de referência: {_mes_label(ref_month, ref_year)}")

    month_filter = {"month": ref_month, "year": ref_year}

    # ── Vendas sem TMB Educação (para cálculos de churn rate) ─────────────
    codigos_excluidos = set(
        produtores.loc[produtores["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN), "Código"]
    )
    vendas_filtradas = vendas[~vendas["Código"].isin(codigos_excluidos)]

    # ── a) Status geral (sem Inativo) ──────────────────────────────────────
    vendas_ativos = vendas[vendas["Status"] != "Inativo"]
    plan_status = _MockPlan(month_filter)
    s_status, _, _ = _calc_current_status_summary(plan_status, vendas_ativos, produtores)
    por_status = s_status.get("por_status", {})
    total_base = s_status.get("total_produtores", 0)
    # Breakdown de Churn: total em Churn no mês, separado por TMB Educação vs outros
    _vendas_churn_ref = vendas[
        (vendas["Data"].dt.month == ref_month) &
        (vendas["Data"].dt.year == ref_year) &
        (vendas["Status"] == "Churn")
    ]
    _churn_ref_gestor = _vendas_churn_ref.merge(
        produtores[["Código", "Gestor"]], on="Código", how="left"
    )
    churn_total_x = int(_churn_ref_gestor["Código"].nunique())
    churn_tmb_edu_y = int(
        _churn_ref_gestor[
            _churn_ref_gestor["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN)
        ]["Código"].nunique()
    )
    churn_outros_z = churn_total_x - churn_tmb_edu_y

    status_section = {
        "total_base": total_base,
        "data_referencia": s_status.get("data_referencia"),
        "por_status": {
            k: {"qtd": v, "pct": round(v / total_base * 100, 1) if total_base else 0}
            for k, v in por_status.items()
        },
        "churn_breakdown": {
            "total": churn_total_x,
            "tmb_educacao": churn_tmb_edu_y,
            "outros_gestores": churn_outros_z,
        },
    }
    ops.append(f"Base total (sem Inativo): {total_base}")

    # ── b) Taxa de churn do mês + série 6 meses (vectorizada) ────────────
    churn_rate_mes = _calc_churn_rate_base(vendas_filtradas, ref_month, ref_year, produtores)

    # Pré-computa denominadores e numeradores em bulk (evita 6 chamadas individuais)
    _vf_h = vendas_filtradas.copy()
    _vf_h["_period"] = _vf_h["Data"].dt.to_period("M")
    _base_por_period = (
        _vf_h[_vf_h["Status"].isin(["Ativo", "Pré-churn"])]
        .groupby("_period")["Código"].nunique()
    )
    _churns_por_period = (
        _vf_h[(_vf_h["Status"] == "Churn") & (_vf_h["Status_Anterior"] == "Pré-churn")]
        .groupby("_period")["Código"].nunique()
    )

    pm, py = ref_month, ref_year
    serie_6m = []
    for _ in range(6):
        pm, py = _prev_month(pm, py)
        curr_p = pd.Period(f"{py}-{pm:02d}", freq="M")
        prev_pm, prev_py = _prev_month(pm, py)
        prev_p = pd.Period(f"{prev_py}-{prev_pm:02d}", freq="M")
        base = int(_base_por_period.get(curr_p, 0))  # base do próprio mês
        # base do mês anterior usa prev_p
        base_anterior = int(_base_por_period.get(prev_p, 0))
        churns = int(_churns_por_period.get(curr_p, 0))
        if base_anterior > 0:
            taxa_pct = round(churns / base_anterior * 100, 2)
            serie_6m.insert(0, {
                "mes": _mes_label(pm, py),
                "mes_data": f"{py}-{pm:02d}",
                "churns_novos": churns,
                "base_mes_anterior": base_anterior,
                "taxa_pct": taxa_pct,
                "acima_da_meta": taxa_pct > META_CHURN_PCT,
                "diferenca_meta_pp": round(taxa_pct - META_CHURN_PCT, 2),
            })

    churn_rate_section = {
        "mes_atual": churn_rate_mes,
        "meta_pct": META_CHURN_PCT,
        "serie_6m": serie_6m,
    }
    ops.append(f"Taxa de churn {_mes_label(ref_month, ref_year)}: {churn_rate_mes.get('taxa_pct')}%")

    # ── c) Churns novos no mês (Pré-churn → Churn) ────────────────────────
    plan_churns = _MockPlan({**month_filter, "from_status": "Pré-churn", "to_status": "Churn"})
    s_churns, t_churns, _ = _calc_status_transitions(plan_churns, vendas, produtores)

    churns_novos_total = s_churns.get("total_transicoes", 0)

    # Mapa nome → Código (usado nas seções c e f)
    _nome_para_codigo = (
        vendas[["Código", "Produtor"]]
        .drop_duplicates(subset=["Código"])
        .set_index("Produtor")["Código"]
        .to_dict()
    )

    # Faturamento últ. 12m por Código (para churns novos)
    cutoff_cn_12m = pd.Timestamp(f"{ref_year}-{ref_month:02d}-01") - pd.DateOffset(months=12)
    _valor_cn_12m = vendas[vendas["Data"] >= cutoff_cn_12m].groupby("Código")["Valor"].sum().round(2)

    churns_novos_lista = []
    for r in t_churns:
        codigo = _nome_para_codigo.get(r.get("Produtor", ""))
        churns_novos_lista.append({
            "Produtor": r.get("Produtor", ""),
            "Cluster": r.get("Cluster", ""),
            "Gestor": r.get("Gestor", ""),
            "Faturamento últ. 12m (R$)": float(_valor_cn_12m.get(codigo, 0.0)) if codigo else 0.0,
        })

    churns_novos_lista_sorted = sorted(
        churns_novos_lista,
        key=lambda r: -r["Faturamento últ. 12m (R$)"],
    )[:5]

    # Churns novos por gestor (para tabela de gestores — Ajuste 6)
    churns_novos_por_gestor: dict[str, int] = {}
    for r in t_churns:
        g = r.get("Gestor", "") or "Sem gestor"
        churns_novos_por_gestor[g] = churns_novos_por_gestor.get(g, 0) + 1

    # Distribuição de churns novos por gestor (para tabela no Tópico 3)
    _tmb_ed_cn = churns_novos_por_gestor.get("TMB Educação", 0)
    _outros_cn = sorted(
        [(g, n) for g, n in churns_novos_por_gestor.items() if g != "TMB Educação"],
        key=lambda x: -x[1],
    )
    _top5_cn = _outros_cn[:5]
    _restantes_cn = sum(n for _, n in _outros_cn[5:])

    _dist_cn: list[dict] = [{"Gestor": "TMB Educação", "Churns Novos": _tmb_ed_cn}]
    for _g, _n in _top5_cn:
        _dist_cn.append({"Gestor": _g, "Churns Novos": _n})
    if _restantes_cn > 0:
        _dist_cn.append({"Gestor": "Outros gestores", "Churns Novos": _restantes_cn})

    churns_novos_section = {
        "total": churns_novos_total,
        "lista": churns_novos_lista_sorted,
        "dist_por_gestor": _dist_cn,
    }
    ops.append(f"Churns novos no mês: {churns_novos_section['total']}")

    # ── d) Distribuição churns por cluster ────────────────────────────────
    plan_cluster = _MockPlan(month_filter)
    vendas_churn_mes = vendas[
        (vendas["Data"].dt.month == ref_month) & (vendas["Data"].dt.year == ref_year)
        & (vendas["Status"] == "Churn")
    ]
    # Usa _calc_cluster_breakdown apenas no subset de Churn do mês
    _, t_cluster, _ = _calc_cluster_breakdown(plan_cluster, vendas_churn_mes, produtores)

    # ── e) Recuperações no mês ────────────────────────────────────────────
    plan_rec1 = _MockPlan({**month_filter, "from_status": "Pré-churn", "to_status": "Ativo"})
    s_rec1, t_rec1, _ = _calc_status_transitions(plan_rec1, vendas, produtores)

    plan_rec2 = _MockPlan({**month_filter, "from_status": "Churn", "to_status": "Ativo"})
    s_rec2, t_rec2, _ = _calc_status_transitions(plan_rec2, vendas, produtores)

    def _top5_rec(rows: list[dict]) -> list[dict]:
        filtered = [
            {k: v for k, v in r.items() if k in ("Produtor", "Cluster", "Gestor", "Valor no Mês (R$)")}
            for r in rows
        ]
        return sorted(filtered, key=lambda r: -(r.get("Valor no Mês (R$)") or 0))[:5]

    recuperacoes_section = {
        "prechurn_para_ativo": {
            "total": s_rec1.get("total_transicoes", 0),
            "lista": _top5_rec(t_rec1),
        },
        "churn_para_ativo": {
            "total": s_rec2.get("total_transicoes", 0),
            "lista": _top5_rec(t_rec2),
        },
        "total": s_rec1.get("total_transicoes", 0) + s_rec2.get("total_transicoes", 0),
    }
    ops.append(f"Recuperações no mês: {recuperacoes_section['total']}")

    # ── f) Pré-churn em risco — top 5 ─────────────────────────────────────
    plan_risk = _MockPlan(month_filter)
    _, t_risk, _ = _calc_at_risk(plan_risk, vendas, produtores)

    # Exclui produtores que já viraram Churn no mês de referência (etapa c)
    codigos_churns_novos = set(
        vendas.loc[
            (vendas["Data"].dt.month == ref_month)
            & (vendas["Data"].dt.year == ref_year)
            & (vendas["Status"] == "Churn")
            & (vendas["Status_Anterior"] == "Pré-churn"),
            "Código",
        ]
    )
    # Calcula o set de nomes UMA VEZ antes do list comprehension (evita 3923 reavaliações)
    _nomes_churns_novos = set(
        vendas.loc[vendas["Código"].isin(codigos_churns_novos), "Produtor"].dropna()
    )
    t_risk = [r for r in t_risk if r.get("Produtor") not in _nomes_churns_novos]

    # Meses consecutivos em Pré-churn até o mês de referência (vectorizado)
    ref_ts = pd.Timestamp(f"{ref_year}-{ref_month:02d}-01")

    # Filtra apenas os Códigos dos produtores em risco — evita operar em 658k linhas
    codigos_risk = {
        _nome_para_codigo[r["Produtor"]]
        for r in t_risk
        if r.get("Produtor") in _nome_para_codigo
    }
    _vf_risk = (
        vendas[(vendas["Data"] <= ref_ts) & (vendas["Código"].isin(codigos_risk))]
        .sort_values(["Código", "Data"], ascending=[True, False])
        .copy()
    )
    _vf_risk["_not_pc"] = (_vf_risk["Status"] != "Pré-churn").astype(int)
    _vf_risk["_break"] = _vf_risk.groupby("Código")["_not_pc"].cumsum()
    _meses_pc_risk = (
        _vf_risk[_vf_risk["_break"] == 0]
        .groupby("Código")
        .size()
    )

    # Faturamento últ. 12m por Código
    cutoff_12m = ref_ts - pd.DateOffset(months=12)
    valor_12m_por_codigo = (
        vendas[vendas["Data"] >= cutoff_12m].groupby("Código")["Valor"].sum().round(2)
    )

    # Mapeia nome → Código (reutiliza _nome_para_codigo construído em seção c)
    for r in t_risk:
        prod_name = r.get("Produtor", "")
        codigo = _nome_para_codigo.get(prod_name)
        r["Faturamento últ. 12m (R$)"] = float(valor_12m_por_codigo.get(codigo, 0.0)) if codigo else 0.0
        r["Meses em Pré-churn"] = int(_meses_pc_risk.get(codigo, 0)) if codigo else 0

    t_risk_sorted = sorted(
        t_risk,
        key=lambda r: (-r.get("Meses em Pré-churn", 0), -r.get("Faturamento últ. 12m (R$)", 0)),
    )[:5]

    prechurn_section = {
        "total_em_risco": len(t_risk),
        "top5": t_risk_sorted,
    }
    ops.append(f"Produtores em Pré-churn (excluindo churns novos): {len(t_risk)}")

    # ── g) Análise por gestor ──────────────────────────────────────────────
    plan_gest_status = _MockPlan(month_filter)
    _, t_gest_status, _ = _calc_manager_summary(plan_gest_status, vendas, produtores)

    plan_gest_churn = _MockPlan(month_filter, group_by="Gestor")
    _, t_gest_churn, _ = _calc_churn_rate_analysis(plan_gest_churn, vendas, produtores)

    # Indexa taxa de churn por gestor
    taxa_por_gestor = {r["dimensao"]: r for r in t_gest_churn}

    # Agrega status por gestor
    gest_status_agg: dict[str, dict] = {}
    for r in t_gest_status:
        g = r.get("Gestor", "")
        if g not in gest_status_agg:
            gest_status_agg[g] = {"total": 0, "Churn": 0, "Pré-churn": 0, "Ativo": 0}
        gest_status_agg[g]["total"] += r.get("Qtd", 0)
        gest_status_agg[g][r.get("Status", "")] = r.get("Qtd", 0)

    # Busca top produtor em Pré-churn por gestor (pelo maior valor histórico)
    prechurn_por_gestor: dict[str, str] = {}
    for r in t_risk:
        g = r.get("Gestor", "")
        if g not in prechurn_por_gestor:
            prechurn_por_gestor[g] = r.get("Produtor", "")

    gestores_table = []
    for g, agg in gest_status_agg.items():
        if g in GESTORES_EXCLUIDOS_CHURN or g in GESTORES_EXCLUIDOS_RELATORIO:
            continue
        if agg["total"] < MIN_CARTEIRA_GESTOR:
            continue
        total = agg["total"] or 1
        taxa_info = taxa_por_gestor.get(g, {})
        gestores_table.append({
            "Gestor": g,
            "Carteira Total": agg["total"],
            "Churns Novos": churns_novos_por_gestor.get(g, 0),
            "% Churn": round(agg.get("Churn", 0) / total * 100, 1),
            "% Pré-churn": round(agg.get("Pré-churn", 0) / total * 100, 1),
            "Taxa Churn Mês (%)": taxa_info.get("taxa_pct"),
            "Diferença Meta (pp)": taxa_info.get("diferenca_meta_pp"),
            "Top Pré-churn": prechurn_por_gestor.get(g, "—"),
        })
    gestores_table.sort(key=lambda r: (r.get("Taxa Churn Mês (%)") or 0), reverse=True)

    # ── Consolida ─────────────────────────────────────────────────────────
    summary_stats = {
        "mes_referencia": _mes_label(ref_month, ref_year),
        "status": status_section,
        "churn_rate": churn_rate_section,
        "churns_novos": churns_novos_section,
        "recuperacoes": recuperacoes_section,
        "prechurn_risco": prechurn_section,
        "gestores": {"tabela": gestores_table},
    }

    tabular_data = [
        {"secao": "Taxa de Churn — Histórico 6 meses", "dados": serie_6m},
        {"secao": "Churns Novos no Mês", "dados": churns_novos_section["lista"]},
        {"secao": "Churns Novos — Distribuição por Gestor", "dados": _dist_cn},
        {"secao": "Distribuição Churns por Cluster", "dados": t_cluster},
        {"secao": "Recuperações — Pré-churn → Ativo", "dados": recuperacoes_section["prechurn_para_ativo"]["lista"]},
        {"secao": "Recuperações — Churn → Ativo", "dados": recuperacoes_section["churn_para_ativo"]["lista"]},
        {"secao": "Pré-churn em Risco — Top 5", "dados": t_risk_sorted},
        {"secao": "Análise por Gestor", "dados": gestores_table},
    ]

    ops.append(f"Relatório consolidado: {len(tabular_data)} seções")
    return summary_stats, tabular_data, ops


# ---------------------------------------------------------------------------
# Tools composáveis v2 — controle total via parâmetros
# ---------------------------------------------------------------------------

def _calc_status_distribuicao(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Distribuição de status com agrupamento configurável (None, Gestor, Cluster)."""
    ops = ["Calculando distribuição de status"]

    df = _filter_by_period(vendas, plan)
    ops.append(f"Período: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    gestor_filter = plan.get_filter("gestor")
    cluster_filter = plan.get_filter("cluster")
    group_by = (plan.group_by or plan.filters.get("group_by") or "").strip()
    incluir_faturamento = str(plan.filters.get("incluir_faturamento", "false")).lower() == "true"

    latest = (
        df.sort_values("Data")
        .groupby("Código", as_index=False)
        .last()[["Código", "Produtor", "Status", "Data"]]
    )
    merged = latest.merge(produtores[["Código", "Cluster", "Gestor"]], on="Código", how="left")
    merged["Cluster"] = merged["Cluster"].fillna("Não classificado")
    merged["Gestor"] = merged["Gestor"].fillna("Sem gestor")

    if gestor_filter:
        merged = merged[merged["Gestor"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtrado por gestor '{gestor_filter}': {len(merged)} produtores")
    if cluster_filter:
        merged = merged[merged["Cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtrado por cluster '{cluster_filter}': {len(merged)} produtores")

    total = len(merged)
    ref_date = str(merged["Data"].max().date()) if not merged.empty else ""

    if group_by.lower() in ("gestor",):
        breakdown = (
            merged.groupby(["Gestor", "Status"]).size().reset_index(name="Qtd")
        )
        totais = merged.groupby("Gestor").size().rename("Total").reset_index()
        tabular = breakdown.to_dict("records")
        summary = {
            "total_produtores": total,
            "data_referencia": ref_date,
            "group_by": "Gestor",
            "por_grupo": totais.set_index("Gestor")["Total"].to_dict(),
        }
    elif group_by.lower() in ("cluster",):
        breakdown = (
            merged.groupby(["Cluster", "Status"]).size().reset_index(name="Qtd")
        )
        totais = merged.groupby("Cluster").size().rename("Total").reset_index()
        tabular = breakdown.to_dict("records")
        summary = {
            "total_produtores": total,
            "data_referencia": ref_date,
            "group_by": "Cluster",
            "por_grupo": totais.set_index("Cluster")["Total"].to_dict(),
        }
    else:
        counts = merged.groupby("Status").size().reset_index(name="Qtd")
        tabular = counts.to_dict("records")
        summary = {
            "total_produtores": total,
            "data_referencia": ref_date,
            "por_status": counts.set_index("Status")["Qtd"].to_dict(),
        }

    if incluir_faturamento:
        _ref = df["Data"].max()
        _fat = (
            df[(df["Data"].dt.month == _ref.month) & (df["Data"].dt.year == _ref.year)]
            .merge(produtores[["Código", group_by.capitalize() if group_by else "Cluster"]], on="Código", how="left")
        )
        dim_col = group_by.capitalize() if group_by in ("gestor", "cluster") else "Cluster"
        _fat_raw = _fat.groupby(dim_col)["Valor"].sum().round(2).to_dict()
        _total = round(sum(_fat_raw.values()), 2)
        summary["faturamento"] = {
            "mes_referencia": _ref.strftime("%Y-%m"),
            "total": _total,
            "por_grupo": {k: {"valor": v, "pct": round(v / _total * 100, 1) if _total else 0.0} for k, v in _fat_raw.items()},
        }

    ops.append(f"Total produtores: {total} | group_by={group_by or 'total'}")
    return summary, tabular, ops


def _calc_taxa_churn_composavel(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Taxa de churn composável — controle total via parâmetros.
    serie=True → série histórica (months meses, default 6).
    group_by="Gestor" → breakdown por gestor.
    """
    ops = ["Calculando taxa de churn (composável)"]

    codigos_excluidos = set(
        produtores.loc[produtores["Gestor"].isin(GESTORES_EXCLUIDOS_CHURN), "Código"]
    )
    vendas_f = vendas[~vendas["Código"].isin(codigos_excluidos)]
    if codigos_excluidos:
        ops.append(f"Excluídos {len(codigos_excluidos)} produtores de: {GESTORES_EXCLUIDOS_CHURN}")

    raw_month = plan.filters.get("month")
    raw_year = plan.filters.get("year")
    try:
        ref_month = int(raw_month) if raw_month and str(raw_month).lower() not in ("null", "none", "") else None
        ref_year = int(raw_year) if raw_year and str(raw_year).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        ref_month, ref_year = None, None

    latest = vendas_f["Data"].max()
    ref_month = ref_month or latest.month
    ref_year = ref_year or latest.year

    gestor_filter = plan.get_filter("gestor")
    group_by = (plan.group_by or plan.filters.get("group_by") or "").lower()
    serie = str(plan.filters.get("serie", "false")).lower() == "true"

    raw_months = plan.filters.get("months", 6)
    try:
        n_months = int(raw_months) if raw_months and str(raw_months).lower() not in ("null", "none", "") else 6
    except (ValueError, TypeError):
        n_months = 6

    # Filtra para gestor específico se pedido
    if gestor_filter:
        codigos_gestor = set(
            produtores.loc[
                produtores["Gestor"].str.contains(gestor_filter, case=False, na=False), "Código"
            ]
        )
        vendas_f = vendas_f[vendas_f["Código"].isin(codigos_gestor)]
        ops.append(f"Filtrado por gestor '{gestor_filter}'")

    if serie:
        # Série histórica de n_months meses
        serie_data = []
        pm, py = ref_month, ref_year
        for _ in range(n_months):
            if group_by == "gestor":
                for g in produtores["Gestor"].dropna().unique():
                    if g in GESTORES_EXCLUIDOS_CHURN:
                        continue
                    cod_g = set(produtores.loc[produtores["Gestor"] == g, "Código"])
                    vf_g = vendas_f[vendas_f["Código"].isin(cod_g)]
                    r = _calc_churn_rate_base_dim(vf_g, pm, py, g)
                    r["mes_data"] = f"{py}-{pm:02d}"
                    r["mes"] = _mes_label(pm, py)
                    serie_data.append(r)
            else:
                r = _calc_churn_rate_base(vendas_f, pm, py, produtores)
                serie_data.append(r)
            pm, py = _prev_month(pm, py)

        serie_data.reverse()
        ops.append(f"Série de {n_months} meses gerada | group_by={group_by or 'geral'}")
        summary = {
            "mes_referencia": _mes_label(ref_month, ref_year),
            "n_months": n_months,
            "meta_pct": META_CHURN_PCT,
            "group_by": group_by or "geral",
        }
        return summary, serie_data, ops

    else:
        # Modo pontual — mês único
        if group_by == "gestor":
            rows = []
            for g in produtores["Gestor"].dropna().unique():
                if g in GESTORES_EXCLUIDOS_CHURN:
                    continue
                cod_g = set(produtores.loc[produtores["Gestor"] == g, "Código"])
                vf_g = vendas_f[vendas_f["Código"].isin(cod_g)]
                r = _calc_churn_rate_base_dim(vf_g, ref_month, ref_year, g)
                rows.append(r)
            rows.sort(key=lambda r: -(r.get("taxa_pct") or 0))
            acima = sum(1 for r in rows if r.get("acima_da_meta"))
            summary = {
                "mes_referencia": _mes_label(ref_month, ref_year),
                "meta_pct": META_CHURN_PCT,
                "gestores_acima_meta": acima,
                "gestores_total": len(rows),
            }
            ops.append(f"Taxa por gestor {_mes_label(ref_month, ref_year)}: {len(rows)} gestores")
            return summary, rows, ops
        else:
            r = _calc_churn_rate_base(vendas_f, ref_month, ref_year, produtores)
            ops.append(f"Taxa {_mes_label(ref_month, ref_year)}: {r.get('taxa_pct')}%")
            summary = {**r, "meta_pct": META_CHURN_PCT}
            return summary, [r], ops


def _calc_transicoes(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Transições de status composáveis — qualquer par from/to, filtros opcionais."""
    ops = ["Calculando transições de status (composável)"]

    df = _filter_by_period(vendas, plan)
    ops.append(f"Período: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    transitions = _filter_transitions(df, plan, ops)

    gestor_filter = plan.get_filter("gestor")
    cluster_filter = plan.get_filter("cluster")
    incluir_valor = str(plan.filters.get("incluir_valor", "true")).lower() != "false"

    merged = transitions.merge(
        produtores[["Código", "Cluster", "Gestor"]],
        on="Código", how="left",
    )
    merged["Cluster"] = merged["Cluster"].fillna("Não classificado")
    merged["Gestor"] = merged["Gestor"].fillna("Sem gestor")

    if gestor_filter:
        merged = merged[merged["Gestor"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtrado por gestor '{gestor_filter}': {len(merged)} transições")
    if cluster_filter:
        merged = merged[merged["Cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtrado por cluster '{cluster_filter}': {len(merged)} transições")

    if incluir_valor and not merged.empty:
        ref_ts = df["Data"].max()
        cutoff = ref_ts - pd.DateOffset(months=12)
        valor_12m = vendas[vendas["Data"] >= cutoff].groupby("Código")["Valor"].sum().round(2)
        merged = merged.merge(valor_12m.rename("Valor 12m (R$)"), on="Código", how="left")
        merged["Valor 12m (R$)"] = merged["Valor 12m (R$)"].fillna(0.0)

    por_gestor = merged.groupby("Gestor").size().reset_index(name="Qtd").sort_values("Qtd", ascending=False).to_dict("records")
    por_cluster = merged.groupby("Cluster").size().reset_index(name="Qtd").sort_values("Qtd", ascending=False).to_dict("records")

    from_s = plan.get_filter("from_status") or "qualquer"
    to_s = plan.get_filter("to_status") or "qualquer"
    ops.append(f"Transições {from_s}→{to_s}: {len(merged)}")

    summary = {
        "total": len(merged),
        "from_status": from_s,
        "to_status": to_s,
        "por_gestor": por_gestor,
        "por_cluster": por_cluster,
    }

    tabular_cols = ["Produtor", "Status_Anterior", "Status", "Gestor", "Cluster"]
    if incluir_valor and "Valor 12m (R$)" in merged.columns:
        tabular_cols.append("Valor 12m (R$)")
    tabular = merged[tabular_cols].sort_values(
        "Valor 12m (R$)" if "Valor 12m (R$)" in merged.columns else "Produtor",
        ascending=False,
    ).to_dict("records")

    return summary, tabular, ops


def _calc_produtores(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Ferramenta unificada de produtores:
    - produtor=X → detalhe individual (histórico 12m)
    - status=[...] → lista filtrada com valor médio histórico
    """
    ops = ["Consultando produtores (composável)"]

    produtor_filter = plan.get_filter("produtor") or plan.get_filter("producer_name")
    status_filter = plan.get_filter("status")
    gestor_filter = plan.get_filter("gestor")
    cluster_filter = plan.get_filter("cluster")
    order_by = plan.filters.get("order_by", "valor")

    raw_top_n = plan.filters.get("top_n", 20)
    try:
        top_n = int(raw_top_n) if raw_top_n and str(raw_top_n).lower() not in ("null", "none", "") else 20
    except (ValueError, TypeError):
        top_n = 20

    # ── Modo detalhe individual ─────────────────────────────────────────────
    if produtor_filter:
        return _calc_producer_detail(
            _MockPlan({"producer_name": produtor_filter}), vendas, produtores
        )

    # ── Modo lista com filtros ──────────────────────────────────────────────
    df = _filter_by_period(vendas, plan)
    ops.append(f"Período: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    latest = (
        df.sort_values("Data")
        .groupby("Código", as_index=False)
        .last()[["Código", "Produtor", "Status", "Data"]]
    )

    merged = latest.merge(produtores[["Código", "Cluster", "Gestor"]], on="Código", how="left")
    merged["Cluster"] = merged["Cluster"].fillna("Não classificado")
    merged["Gestor"] = merged["Gestor"].fillna("Sem gestor")

    if status_filter:
        status_list = [s.strip() for s in status_filter.split(",")] if isinstance(status_filter, str) else list(status_filter)
        merged = merged[merged["Status"].isin(status_list)]
        ops.append(f"Filtrado por status {status_list}: {len(merged)} produtores")
    if gestor_filter:
        merged = merged[merged["Gestor"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtrado por gestor '{gestor_filter}': {len(merged)} produtores")
    if cluster_filter:
        merged = merged[merged["Cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtrado por cluster '{cluster_filter}': {len(merged)} produtores")

    valor_medio = (
        vendas[vendas["Status"] == "Ativo"]
        .groupby("Código")["Valor"].mean().round(2)
        .rename("Valor Médio Histórico (R$)")
    )
    merged = merged.merge(valor_medio, on="Código", how="left")
    merged["Valor Médio Histórico (R$)"] = merged["Valor Médio Histórico (R$)"].fillna(0.0)
    merged["Último Registro"] = merged["Data"].dt.strftime("%Y-%m")

    if order_by == "meses_sem_venda":
        merged = merged.sort_values("Último Registro")
    else:
        merged = merged.sort_values("Valor Médio Histórico (R$)", ascending=False)

    merged = merged.head(top_n)

    summary = {
        "total": len(merged),
        "valor_total_em_risco": round(merged["Valor Médio Histórico (R$)"].sum(), 2),
        "prechurn": int((merged["Status"] == "Pré-churn").sum()),
        "churn": int((merged["Status"] == "Churn").sum()),
    }
    ops.append(f"Produtores retornados: {len(merged)} | order_by={order_by}")
    tabular_cols = ["Produtor", "Status", "Cluster", "Gestor", "Último Registro", "Valor Médio Histórico (R$)"]
    return summary, merged[tabular_cols].to_dict("records"), ops


def _calc_faturamento(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Faturamento composável — filtros por gestor, cluster, produtor, status e group_by."""
    ops = ["Calculando faturamento (composável)"]

    df = _filter_by_period(vendas, plan)
    ops.append(f"Período: {df['Data'].min().date()} → {df['Data'].max().date()} | {len(df)} linhas")

    gestor_filter = plan.get_filter("gestor")
    cluster_filter = plan.get_filter("cluster")
    produtor_filter = plan.get_filter("produtor")
    status_filter = plan.get_filter("status")
    group_by = (plan.group_by or plan.filters.get("group_by") or "").lower()

    raw_top_n = plan.filters.get("top_n", 10)
    try:
        top_n = int(raw_top_n) if raw_top_n and str(raw_top_n).lower() not in ("null", "none", "") else 10
    except (ValueError, TypeError):
        top_n = 10

    df = df.merge(produtores[["Código", "Cluster", "Gestor"]], on="Código", how="left")
    df["Cluster"] = df["Cluster"].fillna("Não classificado")
    df["Gestor"] = df["Gestor"].fillna("Sem gestor")

    if status_filter:
        status_list = [s.strip() for s in status_filter.split(",")] if isinstance(status_filter, str) else list(status_filter)
        # Para impacto financeiro: filtra por status final do produtor no período
        latest_status = (
            df.sort_values("Data").groupby("Código", as_index=False).last()[["Código", "Status"]]
        )
        latest_status = latest_status[latest_status["Status"].isin(status_list)]
        df = df[df["Código"].isin(latest_status["Código"])]
        ops.append(f"Filtrado por status {status_list}: {df['Código'].nunique()} produtores")
    if gestor_filter:
        df = df[df["Gestor"].str.contains(gestor_filter, case=False, na=False)]
        ops.append(f"Filtrado por gestor '{gestor_filter}'")
    if cluster_filter:
        df = df[df["Cluster"].str.contains(cluster_filter, case=False, na=False)]
        ops.append(f"Filtrado por cluster '{cluster_filter}'")
    if produtor_filter:
        df = df[df["Produtor"].str.contains(produtor_filter, case=False, na=False)]
        ops.append(f"Filtrado por produtor '{produtor_filter}'")

    if df.empty:
        return {"aviso": "Nenhum dado para os filtros informados."}, [], ops

    valor_por_prod = df.groupby(["Código", "Produtor", "Cluster", "Gestor"])["Valor"].sum().round(2).reset_index(name="Valor Total (R$)")

    total = round(valor_por_prod["Valor Total (R$)"].sum(), 2)
    media = round(valor_por_prod["Valor Total (R$)"].mean(), 2)
    mediana = round(valor_por_prod["Valor Total (R$)"].median(), 2)

    summary: dict = {
        "valor_total": total,
        "valor_medio_por_produtor": media,
        "valor_mediano_por_produtor": mediana,
        "total_produtores": len(valor_por_prod),
    }

    if group_by in ("gestor",):
        agg = valor_por_prod.groupby("Gestor")["Valor Total (R$)"].sum().round(2).to_dict()
        summary["por_grupo"] = agg
        summary["group_by"] = "Gestor"
    elif group_by in ("cluster",):
        agg = valor_por_prod.groupby("Cluster")["Valor Total (R$)"].sum().round(2).to_dict()
        summary["por_grupo"] = agg
        summary["group_by"] = "Cluster"

    top = valor_por_prod.nlargest(top_n, "Valor Total (R$)")[["Produtor", "Valor Total (R$)", "Cluster", "Gestor"]]
    ops.append(f"Total: R$ {total:,.2f} | {len(valor_por_prod)} produtores | group_by={group_by or 'none'}")
    return summary, top.to_dict("records"), ops


# ---------------------------------------------------------------------------
# Churns novos no mês (composável, com filtros opcionais)
# ---------------------------------------------------------------------------

def _calc_churns_novos(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Produtores que churnou no mês (Pré-churn → Churn), com filtros opcionais por gestor/cluster."""
    ops = ["Iniciando cálculo de churns novos"]

    raw_month = plan.filters.get("month")
    raw_year = plan.filters.get("year")
    try:
        ref_month = int(raw_month) if raw_month and str(raw_month).lower() not in ("null", "none", "") else None
        ref_year = int(raw_year) if raw_year and str(raw_year).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        ref_month, ref_year = None, None

    latest = vendas["Data"].max()
    ref_month = ref_month or latest.month
    ref_year = ref_year or latest.year

    gestor_filtro = plan.filters.get("gestor")
    cluster_filtro = plan.filters.get("cluster")

    month_filter: dict = {"month": ref_month, "year": ref_year, "from_status": "Pré-churn", "to_status": "Churn"}
    if gestor_filtro:
        month_filter["gestor"] = gestor_filtro
    if cluster_filtro:
        month_filter["cluster"] = cluster_filtro

    plan_churns = _MockPlan(month_filter)
    s_churns, t_churns, _ = _calc_status_transitions(plan_churns, vendas, produtores)

    _nome_para_codigo = (
        vendas[["Código", "Produtor"]].drop_duplicates(subset=["Código"])
        .set_index("Produtor")["Código"].to_dict()
    )
    cutoff_12m = pd.Timestamp(f"{ref_year}-{ref_month:02d}-01") - pd.DateOffset(months=12)
    _valor_12m = vendas[vendas["Data"] >= cutoff_12m].groupby("Código")["Valor"].sum().round(2)

    lista = []
    churns_por_gestor: dict[str, int] = {}
    churns_por_cluster: dict[str, int] = {}
    for r in t_churns:
        codigo = _nome_para_codigo.get(r.get("Produtor", ""))
        lista.append({
            "Produtor": r.get("Produtor", ""),
            "Cluster": r.get("Cluster", ""),
            "Gestor": r.get("Gestor", ""),
            "Valor 12m (R$)": float(_valor_12m.get(codigo, 0.0)) if codigo else 0.0,
        })
        g = r.get("Gestor") or "Sem gestor"
        churns_por_gestor[g] = churns_por_gestor.get(g, 0) + 1
        c = r.get("Cluster") or "Sem cluster"
        churns_por_cluster[c] = churns_por_cluster.get(c, 0) + 1

    lista_sorted = sorted(lista, key=lambda r: -r["Valor 12m (R$)"])

    por_gestor = sorted(
        [{"Gestor": g, "Churns Novos": n} for g, n in churns_por_gestor.items()],
        key=lambda r: -r["Churns Novos"],
    )
    por_cluster = sorted(
        [{"Cluster": c, "Churns Novos": n} for c, n in churns_por_cluster.items()],
        key=lambda r: -r["Churns Novos"],
    )

    total = s_churns.get("total_transicoes", 0)
    ops.append(f"Churns novos {_mes_label(ref_month, ref_year)}: {total}")

    summary_stats = {
        "mes_referencia": _mes_label(ref_month, ref_year),
        "total": total,
        "por_gestor": por_gestor,
        "por_cluster": por_cluster,
        "lista": lista_sorted,
    }
    tabular_data = [
        {"secao": "Churns Novos no Mês", "dados": lista_sorted},
        {"secao": "Churns Novos por Gestor", "dados": por_gestor},
        {"secao": "Churns Novos por Cluster", "dados": por_cluster},
    ]
    return summary_stats, tabular_data, ops


# ---------------------------------------------------------------------------
# Recuperações no mês (composável, com filtro opcional por gestor)
# ---------------------------------------------------------------------------

def _calc_recuperacoes(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """Produtores que se recuperaram no mês (Pré-churn→Ativo e Churn→Ativo), filtro opcional por gestor."""
    ops = ["Iniciando cálculo de recuperações"]

    raw_month = plan.filters.get("month")
    raw_year = plan.filters.get("year")
    try:
        ref_month = int(raw_month) if raw_month and str(raw_month).lower() not in ("null", "none", "") else None
        ref_year = int(raw_year) if raw_year and str(raw_year).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        ref_month, ref_year = None, None

    latest = vendas["Data"].max()
    ref_month = ref_month or latest.month
    ref_year = ref_year or latest.year

    gestor_filtro = plan.filters.get("gestor")
    month_filter: dict = {"month": ref_month, "year": ref_year}
    if gestor_filtro:
        month_filter["gestor"] = gestor_filtro

    plan_rec1 = _MockPlan({**month_filter, "from_status": "Pré-churn", "to_status": "Ativo"})
    s_rec1, t_rec1, _ = _calc_status_transitions(plan_rec1, vendas, produtores)

    plan_rec2 = _MockPlan({**month_filter, "from_status": "Churn", "to_status": "Ativo"})
    s_rec2, t_rec2, _ = _calc_status_transitions(plan_rec2, vendas, produtores)

    def _top5(rows: list[dict]) -> list[dict]:
        filtered = [
            {k: v for k, v in r.items() if k in ("Produtor", "Cluster", "Gestor", "Valor no Mês (R$)")}
            for r in rows
        ]
        return sorted(filtered, key=lambda r: -(r.get("Valor no Mês (R$)") or 0))[:5]

    total_pc = s_rec1.get("total_transicoes", 0)
    total_ch = s_rec2.get("total_transicoes", 0)
    ops.append(f"Recuperações {_mes_label(ref_month, ref_year)}: {total_pc + total_ch} (Pré-churn→Ativo: {total_pc}, Churn→Ativo: {total_ch})")

    summary_stats = {
        "mes_referencia": _mes_label(ref_month, ref_year),
        "total": total_pc + total_ch,
        "prechurn_para_ativo": {"total": total_pc, "lista": _top5(t_rec1)},
        "churn_para_ativo": {"total": total_ch, "lista": _top5(t_rec2)},
    }
    tabular_data = [
        {"secao": "Recuperações — Pré-churn → Ativo", "dados": _top5(t_rec1)},
        {"secao": "Recuperações — Churn → Ativo", "dados": _top5(t_rec2)},
    ]
    return summary_stats, tabular_data, ops


# ---------------------------------------------------------------------------
# Relatório individual de gestor
# ---------------------------------------------------------------------------

def _calc_manager_report(
    plan: QueryPlan, vendas: pd.DataFrame, produtores: pd.DataFrame
) -> tuple[dict, list[dict], list[str]]:
    """
    Relatório completo de um gestor específico.
    Não reimplementa lógica — chama funções existentes com vendas filtradas.
    """
    ops = ["Iniciando relatório de gestor"]

    # ── Identifica o gestor ────────────────────────────────────────────────
    gestor_query = plan.get_filter("gestor")
    if not gestor_query:
        return {"erro": "Nenhum gestor informado. Especifique o nome do gestor na pergunta."}, [], ops

    # Remove qualificadores que não fazem parte do nome
    gestor_clean = re.sub(r"\b(ativo|inativo)\b", "", gestor_query, flags=re.IGNORECASE)
    gestor_clean = re.sub(r"\s+", " ", gestor_clean).strip()

    gestores_disponiveis = produtores["Gestor"].dropna().unique().tolist()
    matches = [g for g in gestores_disponiveis if gestor_clean.lower() in g.lower()]

    # Prefere entradas sem prefixo "inativo " quando há ambiguidade
    if len(matches) > 1:
        sem_inativo = [g for g in matches if not g.lower().startswith("inativo ")]
        if len(sem_inativo) == 1:
            matches = sem_inativo

    # Fallback fuzzy quando nenhuma correspondência exata
    if not matches:
        _gestores_lower = {g.lower(): g for g in gestores_disponiveis}
        _close = difflib.get_close_matches(
            gestor_clean.lower(), _gestores_lower.keys(), n=1, cutoff=0.6
        )
        if _close:
            matches = [_gestores_lower[_close[0]]]

    if not matches:
        return {
            "erro": f"Nenhum gestor encontrado para '{gestor_query}'. "
                    f"Gestores disponíveis: {sorted(gestores_disponiveis)}",
        }, [], ops

    if len(matches) > 1:
        return {
            "erro": f"Mais de um gestor encontrado para '{gestor_query}'. "
                    f"Especifique qual destes: {matches}",
        }, [], ops

    gestor_nome = matches[0]
    ops.append(f"Gestor identificado: '{gestor_nome}'")

    # ── Período de referência ──────────────────────────────────────────────
    raw_month = plan.filters.get("month")
    raw_year = plan.filters.get("year")
    try:
        ref_month = int(raw_month) if raw_month and str(raw_month).lower() not in ("null", "none", "") else None
        ref_year = int(raw_year) if raw_year and str(raw_year).lower() not in ("null", "none", "") else None
    except (ValueError, TypeError):
        ref_month, ref_year = None, None

    latest = vendas["Data"].max()
    ref_month = ref_month or latest.month
    ref_year = ref_year or latest.year
    ref_ts = pd.Timestamp(f"{ref_year}-{ref_month:02d}-01")
    month_filter = {"month": ref_month, "year": ref_year}
    ops.append(f"Período de referência: {_mes_label(ref_month, ref_year)}")

    # ── Filtra produtores da carteira (sem Inativo) ────────────────────────
    codigos_gestor = set(
        produtores.loc[produtores["Gestor"] == gestor_nome, "Código"]
    )
    vendas_gest = vendas[
        (vendas["Código"].isin(codigos_gestor)) & (vendas["Status"] != "Inativo")
    ]
    ops.append(f"Produtores na carteira: {vendas_gest['Código'].nunique()}")

    # ── a) Status da carteira ─────────────────────────────────────────────
    plan_status = _MockPlan(month_filter)
    s_status, _, _ = _calc_current_status_summary(plan_status, vendas_gest, produtores)
    por_status = s_status.get("por_status", {})
    total_base = s_status.get("total_produtores", 0)
    status_section = {
        "total_base": total_base,
        "data_referencia": s_status.get("data_referencia"),
        "por_status": {
            k: {"qtd": v, "pct": round(v / total_base * 100, 1) if total_base else 0}
            for k, v in por_status.items()
        },
    }
    ops.append(f"Carteira: {total_base} produtores | {por_status}")

    # ── b) Faturamento ────────────────────────────────────────────────────
    mes_atual_df = vendas_gest[
        (vendas_gest["Data"].dt.month == ref_month) & (vendas_gest["Data"].dt.year == ref_year)
    ]
    fat_atual = round(float(mes_atual_df[mes_atual_df["Status"] == "Ativo"]["Valor"].sum()), 2)

    cutoff_12m = ref_ts - pd.DateOffset(months=12)
    vendas_12m_gest = vendas_gest[vendas_gest["Data"] >= cutoff_12m]

    hist_fat = (
        vendas_12m_gest.groupby("Data")
        .agg(
            valor_total=("Valor", "sum"),
            qtd_ativos=("Status", lambda s: (s == "Ativo").sum()),
        )
        .reset_index()
        .sort_values("Data")
    )
    hist_fat["mes"] = hist_fat["Data"].apply(lambda d: _mes_label(d.month, d.year))
    hist_fat["valor_total"] = hist_fat["valor_total"].round(2)

    faturamento_section = {
        "faturamento_atual": fat_atual,
        "historico_12m": hist_fat[["mes", "valor_total", "qtd_ativos"]].to_dict("records"),
    }
    ops.append(f"Faturamento atual (Ativos): R$ {fat_atual:,.2f}")

    # ── c) Clusters e top 5 ───────────────────────────────────────────────
    plan_cluster = _MockPlan(month_filter)
    _, t_cluster_raw, _ = _calc_cluster_breakdown(plan_cluster, vendas_gest, produtores)

    # Pivota para formato wide: Cluster | Ativo | Pré-churn | Churn
    _status_cols = ["Ativo", "Pré-churn", "Churn"]
    if t_cluster_raw:
        _df_tidy = pd.DataFrame(t_cluster_raw)
        _pivot = (
            _df_tidy.pivot_table(index="Cluster", columns="Status", values="Qtd", fill_value=0)
            .reset_index()
        )
        for col in _status_cols:
            if col not in _pivot.columns:
                _pivot[col] = 0
        _pivot = _pivot[["Cluster"] + _status_cols]
        for col in _status_cols:
            _pivot[col] = _pivot[col].astype(int)
        t_cluster = _pivot.to_dict("records")
    else:
        t_cluster = []

    valor_12m_por_codigo = (
        vendas_12m_gest.groupby("Código")["Valor"].sum().round(2)
    )
    latest_status = _latest_status_per_producer(vendas_gest)
    top5_df = (
        pd.DataFrame({
            "Código": valor_12m_por_codigo.index,
            "Valor 12m (R$)": valor_12m_por_codigo.values,
        })
        .merge(latest_status[["Código", "Produtor", "Status"]], on="Código", how="left")
        .merge(produtores[["Código", "Cluster"]], on="Código", how="left")
        .sort_values("Valor 12m (R$)", ascending=False)
        .head(5)
    )
    top5_df["Cluster"] = top5_df["Cluster"].fillna("Não classificado")

    clusters_section = {
        "distribuicao": t_cluster,
        "top5_valor_12m": top5_df[["Produtor", "Cluster", "Status", "Valor 12m (R$)"]].to_dict("records"),
    }

    # ── d) Churns novos e taxa de churn ───────────────────────────────────
    plan_churns = _MockPlan({**month_filter, "from_status": "Pré-churn", "to_status": "Churn"})
    s_churns, _, _ = _calc_status_transitions(plan_churns, vendas_gest, produtores)

    taxa_mes = _calc_churn_rate_base(vendas_gest, ref_month, ref_year, produtores)

    # Histórico 12m da taxa para este gestor
    pm, py = ref_month, ref_year
    taxa_hist = []
    for _ in range(12):
        pm, py = _prev_month(pm, py)
        r = _calc_churn_rate_base(vendas_gest, pm, py, produtores)
        if r.get("base_mes_anterior") and r["base_mes_anterior"] > 0:
            taxa_hist.insert(0, r)

    # Top 5 churns novos por valor histórico 12m
    codigos_churns = set(
        vendas_gest.loc[
            (vendas_gest["Data"].dt.month == ref_month)
            & (vendas_gest["Data"].dt.year == ref_year)
            & (vendas_gest["Status"] == "Churn")
            & (vendas_gest["Status_Anterior"] == "Pré-churn"),
            "Código",
        ]
    )

    def _meses_consec_prechurn_gest(codigo: int) -> int:
        hist = vendas_gest[vendas_gest["Código"] == codigo].sort_values("Data", ascending=False)
        count = 0
        for _, row in hist.iterrows():
            if row["Data"] > ref_ts:
                continue
            if row["Status"] == "Pré-churn":
                count += 1
            else:
                break
        return count

    churns_top5 = []
    for cod in codigos_churns:
        prod_row = produtores[produtores["Código"] == cod]
        prod_name = vendas_gest[vendas_gest["Código"] == cod]["Produtor"].iloc[-1] if not vendas_gest[vendas_gest["Código"] == cod].empty else ""
        cluster = prod_row["Cluster"].iloc[0] if not prod_row.empty else ""
        val_12m = round(float(valor_12m_por_codigo.get(cod, 0.0)), 2)
        meses_pc = _meses_consec_prechurn_gest(cod)
        churns_top5.append({
            "Produtor": prod_name,
            "Cluster": cluster,
            "Meses em Pré-churn": meses_pc,
            "Valor 12m (R$)": val_12m,
        })
    churns_top5.sort(key=lambda r: -r["Valor 12m (R$)"])
    churns_top5 = churns_top5[:5]

    churns_section = {
        "total": s_churns.get("total_transicoes", 0),
        "taxa_mes": taxa_mes,
        "taxa_historico_12m": taxa_hist,
        "top5_por_valor": churns_top5,
    }
    ops.append(f"Churns novos: {churns_section['total']} | Taxa: {taxa_mes.get('taxa_pct')}%")

    # ── e) Pré-churn em risco ─────────────────────────────────────────────
    # Apenas produtores com Status == "Pré-churn" no mês de referência,
    # excluindo quem já virou Churn nesse mesmo mês
    prechurn_mes = vendas_gest[
        (vendas_gest["Data"].dt.month == ref_month)
        & (vendas_gest["Data"].dt.year == ref_year)
        & (vendas_gest["Status"] == "Pré-churn")
        & (~vendas_gest["Código"].isin(codigos_churns))
    ][["Código", "Produtor"]].drop_duplicates(subset=["Código"])

    nome_para_codigo = (
        vendas_gest[["Código", "Produtor"]]
        .drop_duplicates(subset=["Código"])
        .set_index("Produtor")["Código"]
        .to_dict()
    )

    t_risk = []
    for _, row in prechurn_mes.iterrows():
        codigo = row["Código"]
        prod_name = row["Produtor"]
        cluster_row = produtores[produtores["Código"] == codigo]
        cluster = cluster_row["Cluster"].iloc[0] if not cluster_row.empty else "Não classificado"
        t_risk.append({
            "Produtor": prod_name,
            "Cluster": cluster,
            "Valor 12m (R$)": float(valor_12m_por_codigo.get(codigo, 0.0)),
            "Meses em Pré-churn": _meses_consec_prechurn_gest(codigo),
        })

    t_risk_sorted = sorted(
        t_risk,
        key=lambda r: (-r.get("Meses em Pré-churn", 0), -r.get("Valor 12m (R$)", 0)),
    )

    prechurn_section = {
        "total_em_risco": len(t_risk_sorted),
        "lista": [
            {k: v for k, v in r.items() if k in ("Produtor", "Cluster", "Meses em Pré-churn", "Valor 12m (R$)")}
            for r in t_risk_sorted
        ],
    }
    ops.append(f"Pré-churn em risco: {len(t_risk_sorted)}")

    # ── f) Recuperações ───────────────────────────────────────────────────
    plan_rec1 = _MockPlan({**month_filter, "from_status": "Pré-churn", "to_status": "Ativo"})
    s_rec1, t_rec1, _ = _calc_status_transitions(plan_rec1, vendas_gest, produtores)

    plan_rec2 = _MockPlan({**month_filter, "from_status": "Churn", "to_status": "Ativo"})
    s_rec2, t_rec2, _ = _calc_status_transitions(plan_rec2, vendas_gest, produtores)

    def _rec_lista(records: list[dict]) -> list[dict]:
        return [
            {k: v for k, v in r.items() if k in ("Produtor", "Cluster", "Valor no Mês (R$)")}
            for r in records
        ] or [{"mensagem": "Nenhuma recuperação neste mês"}]

    recuperacoes_section = {
        "prechurn_para_ativo": {"total": s_rec1.get("total_transicoes", 0), "lista": _rec_lista(t_rec1)},
        "churn_para_ativo": {"total": s_rec2.get("total_transicoes", 0), "lista": _rec_lista(t_rec2)},
        "total": s_rec1.get("total_transicoes", 0) + s_rec2.get("total_transicoes", 0),
    }
    ops.append(f"Recuperações: {recuperacoes_section['total']}")

    # ── Consolida ─────────────────────────────────────────────────────────
    summary_stats = {
        "gestor": gestor_nome,
        "mes_referencia": _mes_label(ref_month, ref_year),
        "status": status_section,
        "faturamento": faturamento_section,
        "clusters": clusters_section,
        "churns_novos": churns_section,
        "prechurn_risco": prechurn_section,
        "recuperacoes": recuperacoes_section,
    }

    tabular_data = [
        {"secao": "Status da Carteira", "dados": [
            {"Status": k, **v} for k, v in status_section["por_status"].items()
        ]},
        {"secao": "Faturamento — Histórico 12 meses", "dados": faturamento_section["historico_12m"]},
        {"secao": "Distribuição por Cluster", "dados": t_cluster},
        {"secao": "Top 5 Maiores Produtores (12m)", "dados": clusters_section["top5_valor_12m"]},
        {"secao": "Churns Novos — Taxa Histórica 12m", "dados": churns_section["taxa_historico_12m"]},
        {"secao": "Churns Novos — Top 5 por Valor", "dados": churns_top5},
        {"secao": "Pré-churn em Risco", "dados": prechurn_section["lista"]},
        {"secao": "Recuperações — Pré-churn → Ativo", "dados": recuperacoes_section["prechurn_para_ativo"]["lista"]},
        {"secao": "Recuperações — Churn → Ativo", "dados": recuperacoes_section["churn_para_ativo"]["lista"]},
    ]

    ops.append(f"Relatório do gestor '{gestor_nome}' consolidado: {len(tabular_data)} seções")
    return summary_stats, tabular_data, ops



# ---------------------------------------------------------------------------
# Helper de log
# ---------------------------------------------------------------------------

def _log(session_id: str, event: str, **kwargs) -> None:
    parts = [
        f"SESSION={session_id}",
        "AGENT=AnalyticsAgent",
        f"EVENT={event}",
        *[f"{k}={v}" for k, v in kwargs.items()],
    ]
    msg = f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} | {' | '.join(parts)}"
    if event == "error":
        logger.error(msg)
    else:
        logger.info(msg)
