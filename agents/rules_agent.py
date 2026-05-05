"""
rules_agent.py — interpreta a intenção da pergunta antes do ReActAgent.

Responsabilidades:
  1. Identificar shortcircuits (saudação, identidade, relatório padrão)
  2. Extrair período temporal explicitado na pergunta
  3. Resolver identidade e pronomes via session state
  4. Rotear para o domínio correto (metabase / hubspot / misto)
  5. Selecionar ferramentas candidatas (subconjunto relevante)
  6. Sinalizar regras de negócio aplicáveis

O ReActAgent recebe o QueryContract no system prompt e começa com contexto
já resolvido, sem precisar inferir período, identidade ou domínio.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anthropic

from config import settings
from prompts import RULES_SYSTEM_PROMPT

logger = settings.logger
_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# Ferramentas que pulam o loop Gemini inteiramente
SHORTCIRCUIT_TOOLS = {"saudacao", "pedir_identidade"}

# Padrões de shortcircuit detectáveis sem LLM
_GREETING_RE = re.compile(
    r"^\s*(oi|olá|ola|bom\s+dia|boa\s+tarde|boa\s+noite|tudo\s+bem|e\s+aí|eai|hey)\W*$",
    re.IGNORECASE,
)
_REPORT_RE = re.compile(
    r"(relat[oó]rio\s+(geral|de\s+churn|completo)|resumo\s+de\s+churn|vis[aã]o\s+geral\s+de\s+churn)",
    re.IGNORECASE,
)


@dataclass
class QueryContract:
    dominio: str                              # metabase | hubspot | misto | shortcircuit
    shortcircuit_tool: str | None             # tool a chamar diretamente (pula ReActAgent)
    periodo_resolvido: str | None             # "YYYY-MM" ou "YYYY-MM:YYYY-MM"
    identidade_resolvida: dict[str, Any]      # {"gestor": "Nome"} ou {}
    requer_identidade: bool                   # true = Alfred deve perguntar quem é o usuário
    requer_merge: bool                        # true = precisa de dProdutores para filtrar
    regras_aplicaveis: list[str]              # regras de negócio a injetar no ReActAgent
    ambiguidades: list[str]                   # pontos que o LLM não conseguiu resolver
    ferramentas_candidatas: list[str]         # subconjunto de ferramentas relevantes
    meta_taxa_churn: float | None             # 0.05 se pergunta envolve taxa de churn


def run(
    user_query: str,
    current_user_gestor: str | None = None,
    awaiting_identity_for: str | None = None,
    last_discussed_gestor: str | None = None,
    session_id: str = "?",
) -> QueryContract:
    _log(session_id, "started", query=user_query[:120])
    t0 = time.time()

    # Shortcircuit heurístico sem LLM (latência zero)
    heuristic = _heuristic_shortcircuit(user_query, awaiting_identity_for)
    if heuristic:
        _log(session_id, "shortcircuit_heuristic", tool=heuristic, duration_ms=int((time.time() - t0) * 1000))
        return _shortcircuit_contract(heuristic, current_user_gestor, last_discussed_gestor)

    # Chamada LLM para classificação completa
    session_ctx = _build_session_context(current_user_gestor, last_discussed_gestor)
    prompt = f"{session_ctx}\n\nPergunta: {user_query}"

    try:
        response = _client.messages.create(
            model=settings.claude_haiku_model,
            system=RULES_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.0,
        )
        raw = response.content[0].text if response.content else "{}"
        data = json.loads(raw)
    except Exception as exc:
        _log(session_id, "error", error=str(exc)[:200])
        # Fallback seguro: contrato vazio, ReActAgent funciona normalmente
        return _fallback_contract()

    contract = _parse_contract(data, current_user_gestor, last_discussed_gestor)
    _log(
        session_id, "completed",
        dominio=contract.dominio,
        shortcircuit=contract.shortcircuit_tool or "none",
        periodo=contract.periodo_resolvido or "none",
        duration_ms=int((time.time() - t0) * 1000),
    )
    return contract


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _heuristic_shortcircuit(query: str, awaiting_identity_for: str | None) -> str | None:
    """Detecta shortcircuits óbvios sem chamar o LLM."""
    stripped = query.strip()
    if _GREETING_RE.match(stripped):
        return "saudacao"
    if awaiting_identity_for:
        return "pedir_identidade"
    return None


def _build_session_context(current_user_gestor: str | None, last_discussed_gestor: str | None) -> str:
    parts = []
    if current_user_gestor:
        parts.append(f"current_user_gestor: {current_user_gestor}")
    if last_discussed_gestor:
        parts.append(f"last_discussed_gestor: {last_discussed_gestor}")
    if not parts:
        return ""
    return "Contexto da sessão: " + " | ".join(parts)


def _shortcircuit_contract(
    tool: str,
    current_user_gestor: str | None,
    last_discussed_gestor: str | None,
) -> QueryContract:
    return QueryContract(
        dominio="shortcircuit",
        shortcircuit_tool=tool,
        periodo_resolvido=None,
        identidade_resolvida={"gestor": current_user_gestor or last_discussed_gestor},
        requer_identidade=(tool == "pedir_identidade"),
        requer_merge=False,
        regras_aplicaveis=[],
        ambiguidades=[],
        ferramentas_candidatas=[],
        meta_taxa_churn=None,
    )


def _parse_contract(
    data: dict,
    current_user_gestor: str | None,
    last_discussed_gestor: str | None,
) -> QueryContract:
    # Valida shortcircuit_tool
    sc_tool = data.get("shortcircuit_tool")
    if sc_tool and sc_tool not in SHORTCIRCUIT_TOOLS:
        sc_tool = None

    # Resolve identidade: prefere o que o LLM extraiu, mas usa sessão como fallback
    identidade = data.get("identidade_resolvida") or {}
    if isinstance(identidade, dict):
        gestor_val = identidade.get("gestor")
        if not gestor_val or str(gestor_val).lower() in ("null", "none", ""):
            # Tenta resolver via session state
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

    return QueryContract(
        dominio=data.get("dominio", "metabase"),
        shortcircuit_tool=sc_tool,
        periodo_resolvido=data.get("periodo_resolvido") or None,
        identidade_resolvida=identidade,
        requer_identidade=bool(data.get("requer_identidade", False)),
        requer_merge=bool(data.get("requer_merge", False)),
        regras_aplicaveis=data.get("regras_aplicaveis") or [],
        ambiguidades=data.get("ambiguidades") or [],
        ferramentas_candidatas=data.get("ferramentas_candidatas") or [],
        meta_taxa_churn=meta,
    )


def _fallback_contract() -> QueryContract:
    """Contrato vazio seguro — ReActAgent funciona normalmente sem contexto extra."""
    return QueryContract(
        dominio="metabase",
        shortcircuit_tool=None,
        periodo_resolvido=None,
        identidade_resolvida={"gestor": None},
        requer_identidade=False,
        requer_merge=False,
        regras_aplicaveis=[],
        ambiguidades=[],
        ferramentas_candidatas=[],
        meta_taxa_churn=None,
    )


def _log(session_id: str, event: str, **kwargs) -> None:
    parts = [
        f"SESSION={session_id}",
        "AGENT=BusinessRulesAgent",
        f"EVENT={event}",
        *[f"{k}={v}" for k, v in kwargs.items()],
    ]
    msg = f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} | {' | '.join(parts)}"
    if event == "error":
        logger.error(msg)
    else:
        logger.info(msg)
