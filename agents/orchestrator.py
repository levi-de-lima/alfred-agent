"""
orchestrator.py — único entry point da interface.

Pipeline: ContextAgent → DataAgent → RetentionAgent e/ou AcquisitionAgent → ReportAgent.

Para perguntas simples, apenas um agente especialista é disparado.
Para perguntas mistas (retention + acquisition), os dois rodam em paralelo via ThreadPoolExecutor.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from agents import context_agent, data_agent, retention_agent, acquisition_agent, report_agent
from agents.analytics_agent import AnalyticsAgentError, AnalyticsInput, AnalyticsResult
from agents.data_agent import DataAgentError, DataAgentInput
from agents.report_agent import ReportInput
from config import settings

logger = settings.logger


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorResponse:
    markdown_response: str
    data_citation: str
    session_id: str
    pipeline_duration_ms: int
    agents_called: list[str] = field(default_factory=list)
    error: str | None = None
    # Metadados de identidade — usados pelo app para atualizar o session state
    identified_user: str | None = None
    ask_identity_for: str | None = None
    last_discussed_gestor: str | None = None
    # Cache de resultados para reformatação sem re-execução de agents de dados
    analytics_results_cache: list | None = None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run(
    user_query: str,
    session_id: str | None = None,
    force_refresh: bool = False,
    conversation_history: list[dict] | None = None,
    current_user_gestor: str | None = None,
    awaiting_identity_for: str | None = None,
    last_discussed_gestor: str | None = None,
    analytics_results_cache: list | None = None,
) -> OrchestratorResponse:
    if not session_id:
        session_id = str(uuid.uuid4())[:8]

    t0 = time.time()
    agents_called: list[str] = []

    _log(session_id, "pipeline_started", query=user_query[:120], force_refresh=force_refresh)

    # ------------------------------------------------------------------
    # Estágio 1 — ContextAgent
    # ------------------------------------------------------------------
    _data_ref_preview = date.today()  # data de hoje para resolver expressões temporais

    agents_called.append("ContextAgent")
    contract = context_agent.run(
        user_query=user_query,
        current_user_gestor=current_user_gestor,
        awaiting_identity_for=awaiting_identity_for,
        last_discussed_gestor=last_discussed_gestor,
        session_id=session_id,
        data_reference_date=_data_ref_preview,
    )

    effective_gestor = current_user_gestor
    if contract.nome_identificado:
        effective_gestor = contract.nome_identificado

    # ------------------------------------------------------------------
    # Atalho: reformatação — reutiliza cache sem re-executar agents de dados
    # ------------------------------------------------------------------
    if contract.is_reformat_request and analytics_results_cache:
        _log(session_id, "reformat_shortcut", cached_results=len(analytics_results_cache))
        agents_called.append("ReportAgent")
        primary_cached = analytics_results_cache[0]
        _meta_c = primary_cached.summary_stats.get("_meta", {})
        report = report_agent.run(
            ReportInput(
                analytics_results=analytics_results_cache,
                user_query=user_query,
                is_speaking_to_gestor=bool(_meta_c.get("is_speaking_to_gestor")),
                identified_user=_meta_c.get("identified_user") or current_user_gestor,
                conversation_history=conversation_history or [],
            ),
            session_id=session_id,
        )
        duration = int((time.time() - t0) * 1000)
        return OrchestratorResponse(
            markdown_response=report.markdown_response,
            data_citation=report.data_citation,
            session_id=session_id,
            pipeline_duration_ms=duration,
            agents_called=agents_called,
            analytics_results_cache=analytics_results_cache,
        )

    # ------------------------------------------------------------------
    # Estágio 2 — DataAgent
    # ------------------------------------------------------------------
    try:
        agents_called.append("DataAgent")
        ctx = data_agent.run(
            DataAgentInput(user_query=user_query, force_refresh=force_refresh, areas=contract.areas),
            session_id=session_id,
        )
    except DataAgentError as exc:
        return _error_response(
            session_id=session_id,
            agents_called=agents_called,
            t0=t0,
            message="Não foi possível carregar os dados. Verifique o Metabase ou aguarde o cache ser restaurado.",
            detail=str(exc),
        )

    # ------------------------------------------------------------------
    # Estágio 3 — Agentes especialistas (paralelo se misto)
    # ------------------------------------------------------------------
    analytics_input = AnalyticsInput(
        context=ctx,
        user_query=user_query,
        conversation_history=conversation_history or [],
        current_user_gestor=effective_gestor,
        awaiting_identity_for=awaiting_identity_for,
        last_discussed_gestor=last_discussed_gestor,
        intent_contract=contract,
    )

    results: list[AnalyticsResult] = []
    try:
        results = _run_specialists(contract.areas, analytics_input, session_id, agents_called)
    except AnalyticsAgentError as exc:
        return _error_response(
            session_id=session_id,
            agents_called=agents_called,
            t0=t0,
            message=exc.user_facing_message,
            detail=str(exc),
        )

    # Reexecuta a pergunta pendente se o usuário acabou de se identificar
    if contract.nome_identificado and awaiting_identity_for:
        _log(session_id, "reexecuting_pending_query",
             gestor=contract.nome_identificado, query_type=awaiting_identity_for)
        try:
            pending_input = AnalyticsInput(
                context=ctx,
                user_query=awaiting_identity_for,
                conversation_history=conversation_history or [],
                current_user_gestor=contract.nome_identificado,
                awaiting_identity_for=None,
                last_discussed_gestor=last_discussed_gestor,
                intent_contract=contract,
            )
            results = _run_specialists(["retention"], pending_input, session_id, agents_called, suffix="(reexec)")
        except AnalyticsAgentError:
            pass

    # Extrai metadados de identidade do primeiro resultado disponível
    primary = results[0] if results else _empty_result(ctx)
    _meta = primary.summary_stats.get("_meta", {})
    _is_speaking = bool(_meta.get("is_speaking_to_gestor"))
    _identified = _meta.get("identified_user") or contract.nome_identificado
    _ask_for = _meta.get("ask_identity_for") if not contract.nome_identificado else None
    _last_gestor = _meta.get("last_discussed_gestor")

    # ------------------------------------------------------------------
    # Estágio 4 — ReportAgent
    # ------------------------------------------------------------------
    agents_called.append("ReportAgent")
    report = report_agent.run(
        ReportInput(
            analytics_results=results,
            user_query=user_query,
            is_speaking_to_gestor=_is_speaking,
            identified_user=(_identified or current_user_gestor) if _is_speaking else None,
            conversation_history=conversation_history or [],
        ),
        session_id=session_id,
    )

    duration = int((time.time() - t0) * 1000)
    _log(session_id, "pipeline_completed", duration_ms=duration, agents=",".join(agents_called))

    return OrchestratorResponse(
        markdown_response=report.markdown_response,
        data_citation=report.data_citation,
        session_id=session_id,
        pipeline_duration_ms=duration,
        agents_called=agents_called,
        error=None,
        identified_user=_identified,
        ask_identity_for=_ask_for,
        last_discussed_gestor=_last_gestor,
        analytics_results_cache=results if results else None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_specialists(
    areas: list[str],
    inp: AnalyticsInput,
    session_id: str,
    agents_called: list[str],
    suffix: str = "",
) -> list[AnalyticsResult]:
    """Dispara RetentionAgent e/ou AcquisitionAgent conforme as áreas."""
    tasks = {}
    if "retention" in areas:
        tasks["RetentionAgent"] = retention_agent.run
    if "acquisition" in areas:
        tasks["AcquisitionAgent"] = acquisition_agent.run
    if not tasks:
        # greeting: nenhum agente especialista necessário
        return []

    for name in tasks:
        agents_called.append(f"{name}{suffix}")

    if len(tasks) == 1:
        name, fn = next(iter(tasks.items()))
        return [fn(inp, session_id)]

    # Dois agentes em paralelo
    results: list[AnalyticsResult] = [None, None]
    order = list(tasks.items())
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(fn, inp, session_id): i for i, (_, fn) in enumerate(order)}
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()  # propaga exceção se houver

    return results


def _empty_result(ctx) -> AnalyticsResult:
    return AnalyticsResult(
        query_type="greeting",
        summary_stats={},
        tabular_data=[],
        data_reference_date=ctx.data_reference_date,
        data_source=ctx.data_source,
        warnings=[],
        pandas_operations_log=[],
    )


def _error_response(
    session_id: str,
    agents_called: list[str],
    t0: float,
    message: str,
    detail: str,
) -> OrchestratorResponse:
    duration = int((time.time() - t0) * 1000)
    _log(session_id, "pipeline_error", error=detail[:200], duration_ms=duration)
    return OrchestratorResponse(
        markdown_response=message,
        data_citation="",
        session_id=session_id,
        pipeline_duration_ms=duration,
        agents_called=agents_called,
        error=message,
    )


def _log(session_id: str, event: str, **kwargs) -> None:
    parts = [
        f"SESSION={session_id}",
        "AGENT=Orchestrator",
        f"EVENT={event}",
        *[f"{k}={v}" for k, v in kwargs.items()],
    ]
    msg = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} | {' | '.join(parts)}"
    if event == "pipeline_error":
        logger.error(msg)
    else:
        logger.info(msg)
