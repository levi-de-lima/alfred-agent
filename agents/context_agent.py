"""
context_agent.py — classifica a intenção da pergunta antes do ReActAgent.

Responsabilidades:
  1. Classificar domínio e intenção da pergunta via Gemini
  2. Extrair período temporal explicitado na pergunta
  3. Resolver identidade e pronomes via session state
  4. Extrair nome do usuário quando a mensagem for auto-identificação
  5. Rotear para o agente correto (retention / acquisition / ambos / greeting)
  6. Selecionar ferramentas candidatas (subconjunto relevante)
  7. Sinalizar regras de negócio aplicáveis

O ReActAgent recebe o IntentContract no system prompt e começa com contexto
já resolvido, sem precisar inferir período, identidade ou domínio.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anthropic

from config import settings
from prompts import CONTEXT_SYSTEM_PROMPT

logger = settings.logger
_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)


@dataclass
class IntentContract:
    areas: list[str]                          # subconjunto de ["retention", "acquisition"]; [] = greeting
    periodo_resolvido: str | None             # "YYYY-MM" ou "YYYY-MM:YYYY-MM"
    identidade_resolvida: dict[str, Any]      # {"gestor": "Nome"} ou {"gestor": None}
    nome_identificado: str | None             # nome extraído quando usuário se apresenta
    requer_identidade: bool                   # true = Alfred deve perguntar quem é o usuário
    requer_merge: bool                        # true = precisa de dProdutores para filtrar
    regras_aplicaveis: list[str]              # regras de negócio a injetar no agente especialista
    ambiguidades: list[str]                   # pontos que o LLM não conseguiu resolver
    meta_taxa_churn: float | None             # 0.05 se pergunta envolve taxa de churn
    is_reformat_request: bool = False         # true = usuário pediu reformatação da resposta anterior


def run(
    user_query: str,
    current_user_gestor: str | None = None,
    awaiting_identity_for: str | None = None,
    last_discussed_gestor: str | None = None,
    session_id: str = "?",
    data_reference_date=None,
) -> IntentContract:
    _log(session_id, "started", query=user_query[:120])
    t0 = time.time()

    session_ctx = _build_session_context(
        current_user_gestor, awaiting_identity_for, last_discussed_gestor
    )
    date_ctx = (
        f"DATA DE REFERÊNCIA: {data_reference_date}. Use esta data como 'hoje' ao resolver períodos relativos.\n\n"
        if data_reference_date else ""
    )
    prompt = f"{date_ctx}{session_ctx}\n\nPergunta: {user_query}" if session_ctx else f"{date_ctx}Pergunta: {user_query}"

    try:
        response = _client.messages.create(
            model=settings.claude_haiku_model,
            system=CONTEXT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.0,
        )
        raw = response.content[0].text if response.content else "{}"
        data = json.loads(raw)
    except Exception as exc:
        _log(session_id, "error", error=str(exc)[:200])
        return _fallback_contract()

    contract = _parse_contract(data, current_user_gestor, last_discussed_gestor)
    _log(
        session_id, "completed",
        areas=",".join(contract.areas) or "greeting",
        periodo=contract.periodo_resolvido or "none",
        nome=contract.nome_identificado or "none",
        duration_ms=int((time.time() - t0) * 1000),
    )
    return contract


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _build_session_context(
    current_user_gestor: str | None,
    awaiting_identity_for: str | None,
    last_discussed_gestor: str | None,
) -> str:
    parts = []
    if current_user_gestor:
        parts.append(f"current_user_gestor: {current_user_gestor}")
    if awaiting_identity_for:
        parts.append(f"awaiting_identity_for: {awaiting_identity_for} (Alfred perguntou quem é o usuário)")
    if last_discussed_gestor:
        parts.append(f"last_discussed_gestor: {last_discussed_gestor}")
    if not parts:
        return ""
    return "Contexto da sessão: " + " | ".join(parts)


def _parse_contract(
    data: dict,
    current_user_gestor: str | None,
    last_discussed_gestor: str | None,
) -> IntentContract:
    # Resolve identidade: prefere o que o LLM extraiu, usa sessão como fallback
    identidade = data.get("identidade_resolvida") or {}
    if isinstance(identidade, dict):
        gestor_val = identidade.get("gestor")
        if not gestor_val or str(gestor_val).lower() in ("null", "none", ""):
            if current_user_gestor:
                identidade = {"gestor": current_user_gestor}
            elif last_discussed_gestor:
                identidade = {"gestor": last_discussed_gestor}
            else:
                identidade = {"gestor": None}
    else:
        identidade = {"gestor": None}

    meta = data.get("meta_taxa_churn")
    if meta is not None:
        try:
            meta = float(meta)
        except (ValueError, TypeError):
            meta = None

    nome = data.get("nome_identificado") or None
    if nome and str(nome).lower() in ("null", "none", ""):
        nome = None

    raw_areas = data.get("areas") or []
    valid = {"retention", "acquisition"}
    areas = [a for a in raw_areas if a in valid]

    return IntentContract(
        areas=areas,
        periodo_resolvido=data.get("periodo_resolvido") or None,
        identidade_resolvida=identidade,
        nome_identificado=nome,
        requer_identidade=bool(data.get("requer_identidade", False)),
        requer_merge=bool(data.get("requer_merge", False)),
        regras_aplicaveis=data.get("regras_aplicaveis") or [],
        ambiguidades=data.get("ambiguidades") or [],
        meta_taxa_churn=meta,
        is_reformat_request=bool(data.get("is_reformat_request", False)),
    )


def _fallback_contract() -> IntentContract:
    """Contrato vazio seguro — pipeline funciona normalmente sem contexto extra."""
    return IntentContract(
        areas=["retention"],
        periodo_resolvido=None,
        identidade_resolvida={"gestor": None},
        nome_identificado=None,
        requer_identidade=False,
        requer_merge=False,
        regras_aplicaveis=[],
        ambiguidades=[],
        meta_taxa_churn=None,
        is_reformat_request=False,
    )


def _log(session_id: str, event: str, **kwargs) -> None:
    parts = [
        f"SESSION={session_id}",
        "AGENT=ContextAgent",
        f"EVENT={event}",
        *[f"{k}={v}" for k, v in kwargs.items()],
    ]
    msg = f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} | {' | '.join(parts)}"
    if event == "error":
        logger.error(msg)
    else:
        logger.info(msg)
