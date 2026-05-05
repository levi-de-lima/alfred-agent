"""
retention_agent.py — analisa ciclo de vida de produtores (churn, LTV, cohort, gestores).

Especializado em dados do Metabase. Tom analítico, linguagem de carteira e risco.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import anthropic

from agents.analytics_agent import AnalyticsInput, AnalyticsResult, AnalyticsAgentError
from agents.tools import ToolContext, get_claude_tools, TOOL_TO_QUERY_TYPE, execute_tool
from config import settings
from prompts import RETENTION_SYSTEM_PROMPT

logger = settings.logger
_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

MAX_STEPS = 4

_TOOL_AREAS = ["churn", "cohort", "parcelo", "primeira_venda", "misto", "greeting"]

_REGRAS_DESCRICAO: dict[str, str] = {
    "inativo_nao_e_churn": "Inativo ≠ Churn: Inativo nunca vendeu; Churn vendeu e parou.",
    "status_fim_do_mes": "Status é o estado no fim do mês — máximo uma mudança por mês por produtor.",
    "status_anterior_nulo_e_primeiro_registro": "Status_Anterior nulo = primeiro registro do produtor, não dado faltante.",
    "reativacao_leve_vs_plena": "Pré-churn→Ativo é reativação leve; Churn→Ativo é reativação plena — são métricas diferentes.",
    "taxa_churn_exclui_tmb_educacao": "Taxa de churn EXCLUI produtores gerenciados por TMB Educação.",
    "filtro_temporal_obrigatorio": "O período foi especificado na pergunta — NÃO use a data de referência padrão.",
}


def _regras_para_texto(regras: list[str]) -> str:
    linhas = [f"  - {_REGRAS_DESCRICAO[r]}" for r in regras if r in _REGRAS_DESCRICAO]
    return "\n".join(linhas)


def _log(session_id: str, event: str, **kwargs) -> None:
    parts = [
        f"SESSION={session_id}",
        "AGENT=RetentionAgent",
        f"EVENT={event}",
        *[f"{k}={v}" for k, v in kwargs.items()],
    ]
    msg = f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} | {' | '.join(parts)}"
    if event == "error":
        logger.error(msg)
    else:
        logger.info(msg)


def _to_claude_history(messages: list[dict]) -> list[dict]:
    result = []
    for msg in messages:
        role = "assistant" if msg["role"] == "assistant" else "user"
        result.append({"role": role, "content": msg["content"]})
    return result


def run(inp: AnalyticsInput, session_id: str) -> AnalyticsResult:
    _log(session_id, "started", query=inp.user_query[:120])
    t0 = time.time()

    ctx = ToolContext(
        vendas=inp.context.vendas,
        produtores=inp.context.produtores,
        data_reference_date=inp.context.data_reference_date,
        data_source=inp.context.data_source,
        hs_closer=inp.context.hs_closer,
        hs_growth=inp.context.hs_growth,
    )

    warnings: list[str] = []
    if "stale" in (inp.context.data_source or ""):
        warnings.append(f"Dados carregados de {inp.context.data_source}.")

    system = RETENTION_SYSTEM_PROMPT
    ctx_parts: list[str] = []
    if inp.context.data_reference_date:
        ctx_parts.append(
            f"DATA DE REFERÊNCIA DOS DADOS: {inp.context.data_reference_date}. "
            "Este é o 'hoje' para todas as perguntas relativas (mês atual, último mês, trimestre, etc.)."
        )
    if inp.current_user_gestor:
        ctx_parts.append(
            f"O usuário desta sessão é o gestor '{inp.current_user_gestor}'. "
            "Quando ele disser 'meu relatório' ou 'minha carteira', use esse nome."
        )
    if inp.last_discussed_gestor:
        ctx_parts.append(
            f"Último gestor consultado: '{inp.last_discussed_gestor}'. "
            "Pronomes como 'dela', 'dele', 'desse gestor' referem-se a ele."
        )
    if ctx_parts:
        system = system + "\n\n---\nContexto da sessão: " + " ".join(ctx_parts)

    contract = inp.intent_contract
    if contract is not None:
        contract_parts: list[str] = []
        if contract.periodo_resolvido:
            contract_parts.append(f"- Período identificado na pergunta: **{contract.periodo_resolvido}** — use este filtro obrigatoriamente.")
        gestor_val = contract.identidade_resolvida.get("gestor") if contract.identidade_resolvida else None
        if gestor_val:
            contract_parts.append(f"- Gestor identificado: **{gestor_val}**.")
        if contract.requer_merge:
            contract_parts.append("- Esta pergunta requer cruzamento com dProdutores (filtro por Gestor ou Cluster).")
        if contract.regras_aplicaveis:
            regras_texto = _regras_para_texto(contract.regras_aplicaveis)
            if regras_texto:
                contract_parts.append(f"- Regras de negócio relevantes:\n{regras_texto}")
        if contract.meta_taxa_churn is not None:
            contract_parts.append(f"- Meta de taxa de churn: {contract.meta_taxa_churn * 100:.0f}%.")
        if contract.ambiguidades:
            contract_parts.append(f"- Ambiguidades detectadas: {'; '.join(contract.ambiguidades)}.")
        if contract_parts:
            system = system + "\n\n---\n## Contexto da pergunta (pré-processado)\n" + "\n".join(contract_parts)

    messages: list[Any] = _to_claude_history(inp.conversation_history or [])
    messages.append({"role": "user", "content": inp.user_query})

    tools_called: list[dict] = []
    _tools = get_claude_tools(_TOOL_AREAS)

    for step in range(MAX_STEPS):
        response = None
        last_exc = None
        for attempt in range(1, 4):
            try:
                response = _client.messages.create(
                    model=settings.claude_model,
                    system=system,
                    tools=_tools,
                    messages=messages,
                    max_tokens=16384,
                )
                break
            except anthropic.APIStatusError as exc:
                last_exc = exc
                logger.error(f"Claude APIStatusError {exc.status_code}: {exc}")
                if exc.status_code == 529 and attempt < 3:
                    wait = attempt * 3
                    _log(session_id, "claude_retry", step=step + 1, attempt=attempt, wait_s=wait, error=str(exc)[:100])
                    time.sleep(wait)
                    continue
                if exc.status_code == 529:
                    msg = "⚠️ **API Claude sobrecarregada**\n\nAguarde 10–20 segundos e tente novamente."
                else:
                    msg = f"⚠️ **Erro na API Claude ({exc.status_code})**\n\nTente novamente."
                raise AnalyticsAgentError(str(exc), user_facing_message=msg) from exc
            except anthropic.RateLimitError as exc:
                last_exc = exc
                logger.error(f"Claude RateLimitError: {exc}")
                raise AnalyticsAgentError(str(exc), user_facing_message=(
                    "⚠️ **Limite de requisições atingido**\n\nAguarde alguns instantes e tente novamente."
                )) from exc
            except anthropic.AuthenticationError as exc:
                last_exc = exc
                logger.error(f"Claude AuthenticationError: {exc}")
                raise AnalyticsAgentError(str(exc), user_facing_message=(
                    "⚠️ **Erro de autenticação**\n\nChave de API inválida. Verifique o arquivo `.env`."
                )) from exc
            except Exception as exc:
                last_exc = exc
                logger.error(f"Erro inesperado no Claude: {type(exc).__name__}: {exc}")
                raise AnalyticsAgentError(str(exc), user_facing_message=(
                    f"⚠️ **Erro inesperado**\n\n`{str(exc)[:120]}`\n\nTente novamente."
                )) from exc

        _log(
            session_id, "claude_step",
            step=step + 1,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
        )

        fn_calls = [b for b in response.content if b.type == "tool_use"]

        messages.append({"role": "assistant", "content": response.content})

        if not fn_calls:
            _log(session_id, "no_tool_call", step=step + 1)
            break

        tool_results = []
        for fc in fn_calls:
            tool_name = fc.name
            tool_args = dict(fc.input or {})
            _log(session_id, "tool_call", tool=tool_name, args=str(tool_args)[:200])
            try:
                result = execute_tool(tool_name, tool_args, ctx)
            except Exception as exc:
                result = {"query_type": tool_name, "summary": {"erro": str(exc)}, "tabular": [], "ops": []}
                _log(session_id, "tool_error", tool=tool_name, error=str(exc)[:200])

            tools_called.append({"name": tool_name, "args": tool_args, "result": result})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": fc.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })
            _log(session_id, "tool_executed", tool=tool_name)

        messages.append({"role": "user", "content": tool_results})

        if tools_called:
            break

    else:
        _log(session_id, "max_steps_reached", steps=MAX_STEPS)

    duration = int((time.time() - t0) * 1000)

    if len(tools_called) == 1:
        query_type = TOOL_TO_QUERY_TYPE.get(tools_called[0]["name"], tools_called[0]["name"])
    elif tools_called:
        query_type = "react_composite"
    else:
        query_type = "greeting"

    identity_meta: dict = {}
    for tc in tools_called:
        s = tc["result"].get("summary", {})
        if "_meta" in s:
            identity_meta = s["_meta"]
            break

    if len(tools_called) == 1:
        summary_stats: dict[str, Any] = tools_called[0]["result"].get("summary", {})
        tabular_data: list[dict] = tools_called[0]["result"].get("tabular", [])
        ops: list[str] = tools_called[0]["result"].get("ops", [])
    elif tools_called:
        summary_stats = {}
        tabular_data = []
        ops = []
        for tc in tools_called:
            r = tc["result"]
            summary_stats[tc["name"]] = r.get("summary", {})
            tabular_data.extend(r.get("tabular", []))
            ops.extend(r.get("ops", []))
    else:
        summary_stats = {}
        tabular_data = []
        ops = []

    if identity_meta:
        summary_stats["_meta"] = identity_meta
    else:
        summary_stats.setdefault("_meta", {
            "is_speaking_to_gestor": bool(inp.current_user_gestor),
            "identified_user": inp.current_user_gestor,
            "ask_identity_for": None,
            "last_discussed_gestor": inp.last_discussed_gestor,
        })

    _log(session_id, "completed", query_type=query_type, tools_called=len(tools_called), duration_ms=duration)

    return AnalyticsResult(
        query_type=query_type,
        summary_stats=summary_stats,
        tabular_data=tabular_data[:50],
        data_reference_date=inp.context.data_reference_date,
        data_source=inp.context.data_source,
        warnings=warnings,
        pandas_operations_log=ops,
    )
