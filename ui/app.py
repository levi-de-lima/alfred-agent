"""
app.py — FastAPI backend para o Alfred (TMB Churn Analyzer).

Rotas:
  GET  /          → serve ui/index.html
  POST /chat      → recebe {message, session_id} e chama orchestrator.run()
  GET  /health    → retorna status dos dados
"""

import sys
import os
from pathlib import Path

# Garante que o root do projeto está no path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents import orchestrator
from data_loader import load_data
from config import settings

logger = settings.logger

UI_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Memória de sessão server-side (até 100 sessões, 5 turnos cada)
# ---------------------------------------------------------------------------

_session_history: dict[str, list[dict]] = {}
_session_state: dict[str, dict] = {}   # estado de identidade por sessão
_MAX_SESSIONS = 100
_MAX_TURNS = 5
_MAX_RESPONSE_CHARS = 600


def _get_history(session_id: str) -> list[dict]:
    return list(_session_history.get(session_id, []))


def _get_session_state(session_id: str) -> dict:
    return dict(_session_state.get(session_id, {}))


def _update_history(session_id: str, user_msg: str, assistant_msg: str) -> None:
    history = _session_history.get(session_id, [])
    history.append({"role": "user", "content": user_msg})
    assistant_short = assistant_msg[:_MAX_RESPONSE_CHARS] + (
        "…" if len(assistant_msg) > _MAX_RESPONSE_CHARS else ""
    )
    history.append({"role": "assistant", "content": assistant_short})
    # Mantém só os últimos N turnos
    _session_history[session_id] = history[-(_MAX_TURNS * 2):]
    # Descarta sessões mais antigas se ultrapassar o limite
    if len(_session_history) > _MAX_SESSIONS:
        oldest = next(iter(_session_history))
        del _session_history[oldest]


def _update_session_state(
    session_id: str,
    identified_user: str | None,
    ask_identity_for: str | None,
    last_discussed_gestor: str | None,
    analytics_results_cache: list | None = None,
) -> None:
    state = _session_state.get(session_id, {})
    if identified_user:
        state["current_user_gestor"] = identified_user
    if ask_identity_for:
        state["awaiting_identity_for"] = ask_identity_for
    elif "awaiting_identity_for" in state and not ask_identity_for:
        # Limpa o estado de espera quando respondido
        state.pop("awaiting_identity_for", None)
    if last_discussed_gestor:
        state["last_discussed_gestor"] = last_discussed_gestor
    if analytics_results_cache is not None:
        state["analytics_results_cache"] = analytics_results_cache
    _session_state[session_id] = state

app = FastAPI(title="Alfred — TMB Churn Analyzer", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str
    force_refresh: bool = False


class ChatResponse(BaseModel):
    markdown_response: str
    data_citation: str
    session_id: str
    pipeline_duration_ms: int
    error: str | None = None


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html não encontrado")
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Mensagem vazia")

    logger.info(
        f"POST /chat | session={req.session_id} | "
        f"force_refresh={req.force_refresh} | query_len={len(req.message)}"
    )

    history = _get_history(req.session_id)
    state = _get_session_state(req.session_id)

    result = orchestrator.run(
        user_query=req.message,
        session_id=req.session_id,
        force_refresh=req.force_refresh,
        conversation_history=history,
        current_user_gestor=state.get("current_user_gestor"),
        awaiting_identity_for=state.get("awaiting_identity_for"),
        last_discussed_gestor=state.get("last_discussed_gestor"),
        analytics_results_cache=state.get("analytics_results_cache"),
    )

    # Só persiste no histórico quando não houve erro de pipeline
    if not result.error:
        _update_history(req.session_id, req.message, result.markdown_response)
        _update_session_state(
            req.session_id,
            identified_user=result.identified_user,
            ask_identity_for=result.ask_identity_for,
            last_discussed_gestor=result.last_discussed_gestor,
            analytics_results_cache=result.analytics_results_cache,
        )

    return ChatResponse(
        markdown_response=result.markdown_response,
        data_citation=result.data_citation,
        session_id=result.session_id,
        pipeline_duration_ms=result.pipeline_duration_ms,
        error=result.error,
    )


@app.get("/health")
async def health():
    try:
        payload = load_data(force_refresh=False)
        return JSONResponse({
            "status": "ok",
            "data_source": payload.source,
            "data_reference_date": str(payload.data_reference_date),
            "loaded_at": payload.loaded_at.isoformat(),
            "rows_vendas": len(payload.vendas),
            "rows_produtores": len(payload.produtores),
        })
    except Exception as exc:
        logger.error(f"GET /health | erro: {exc}")
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
