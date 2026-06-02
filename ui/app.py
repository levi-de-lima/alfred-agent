"""
app.py — FastAPI backend para o Alfred (TMB Churn Analyzer).

Rotas legadas:
  GET  /          → serve ui/index.html
  POST /chat      → recebe {message, session_id} e chama orchestrator.run()
  GET  /health    → retorna status dos dados

Rotas de histórico de chats:
  GET    /chats               → lista chats (sem messages)
  GET    /chats/{id}          → chat completo
  POST   /chats               → cria chat vazio
  PATCH  /chats/{id}          → renomeia chat
  DELETE /chats/{id}          → apaga chat
  POST   /chats/{id}/messages → envia mensagem, chama orchestrator, salva troca
"""

import asyncio
import sys
import os
from functools import partial
from pathlib import Path

# Garante que o root do projeto está no path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic as _anthropic

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents import orchestrator
from importers.metabase import load_data
from config import settings
import ui.storage as storage

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


@app.on_event("startup")
async def startup():
    storage.init_db()


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


# Chats persistence schemas
class ChatSummary(BaseModel):
    id: str
    title: str
    updated_at: str
    message_count: int


class ChatDetail(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict]


class ChatCreateResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ChatRenameRequest(BaseModel):
    title: str


class MessageRequest(BaseModel):
    message: str
    force_refresh: bool = False


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

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        partial(
            orchestrator.run,
            user_query=req.message,
            session_id=req.session_id,
            force_refresh=req.force_refresh,
            conversation_history=history,
            current_user_gestor=state.get("current_user_gestor"),
            awaiting_identity_for=state.get("awaiting_identity_for"),
            last_discussed_gestor=state.get("last_discussed_gestor"),
            analytics_results_cache=state.get("analytics_results_cache"),
        ),
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


# ---------------------------------------------------------------------------
# Chat history — CRUD
# ---------------------------------------------------------------------------

@app.get("/chats", response_model=list[ChatSummary])
async def list_chats():
    return storage.list_chats()


@app.get("/chats/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: str):
    chat = storage.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat não encontrado")
    return chat


@app.post("/chats", response_model=ChatCreateResponse, status_code=201)
async def create_chat():
    return storage.create_chat()


@app.patch("/chats/{chat_id}")
async def rename_chat(chat_id: str, req: ChatRenameRequest):
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Título não pode ser vazio")
    if not storage.update_title(chat_id, req.title.strip()):
        raise HTTPException(status_code=404, detail="Chat não encontrado")
    return {"ok": True}


@app.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str):
    if not storage.delete_chat(chat_id):
        raise HTTPException(status_code=404, detail="Chat não encontrado")


# ---------------------------------------------------------------------------
# Chat history — send message (substitui /chat para chats persistidos)
# ---------------------------------------------------------------------------

def _generate_title(first_message: str) -> str:
    """Gera título curto com Haiku. Chamado em background thread."""
    try:
        client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.claude_haiku_model,
            max_tokens=32,
            messages=[{
                "role": "user",
                "content": (
                    f"Resuma esta pergunta em no máximo 6 palavras para ser título de uma conversa. "
                    f"Responda APENAS com o título, sem pontuação final:\n\n{first_message}"
                ),
            }],
        )
        title = resp.content[0].text.strip().rstrip(".").strip()
        return title if title else "Nova conversa"
    except Exception:
        return "Nova conversa"


@app.post("/chats/{chat_id}/messages", response_model=ChatResponse)
async def chat_message(chat_id: str, req: MessageRequest, request: Request):
    chat = storage.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat não encontrado")

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Mensagem vazia")

    logger.info(
        f"POST /chats/{chat_id}/messages | "
        f"force_refresh={req.force_refresh} | query_len={len(req.message)}"
    )

    is_first_message = storage.message_count(chat_id) == 0

    history = storage.get_last_n_turns(chat_id)
    state = _get_session_state(chat_id)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        partial(
            orchestrator.run,
            user_query=req.message,
            session_id=chat_id,
            force_refresh=req.force_refresh,
            conversation_history=history,
            current_user_gestor=state.get("current_user_gestor"),
            awaiting_identity_for=state.get("awaiting_identity_for"),
            last_discussed_gestor=state.get("last_discussed_gestor"),
            analytics_results_cache=state.get("analytics_results_cache"),
        ),
    )

    if not result.error:
        if await request.is_disconnected():
            logger.info(
                f"POST /chats/{chat_id}/messages | "
                f"cliente desconectou antes de persistir — descartando"
            )
            return ChatResponse(
                markdown_response="",
                data_citation="",
                session_id=chat_id,
                pipeline_duration_ms=result.pipeline_duration_ms,
                error="aborted",
            )
        storage.append_message(chat_id, "user", req.message)
        storage.append_message(chat_id, "assistant", result.markdown_response)
        _update_session_state(
            chat_id,
            identified_user=result.identified_user,
            ask_identity_for=result.ask_identity_for,
            last_discussed_gestor=result.last_discussed_gestor,
            analytics_results_cache=result.analytics_results_cache,
        )
        if is_first_message:
            # Gera título em background sem bloquear a resposta
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _set_title_async, chat_id, req.message)

    return ChatResponse(
        markdown_response=result.markdown_response,
        data_citation=result.data_citation,
        session_id=result.session_id,
        pipeline_duration_ms=result.pipeline_duration_ms,
        error=result.error,
    )


def _set_title_async(chat_id: str, first_message: str) -> None:
    title = _generate_title(first_message)
    storage.update_title(chat_id, title)


# ---------------------------------------------------------------------------
# Explorer — rotas de data exploration
# ---------------------------------------------------------------------------

def _get_explorer_con():
    import duckdb

    def _posix(p: Path) -> str:
        return p.resolve().as_posix()

    con = duckdb.connect(":memory:")
    metabase_dir = ROOT / "data" / "metabase"

    if metabase_dir.exists():
        vendas = sorted(
            list(metabase_dir.glob("tmb_churn_cache_*_vendas.parquet")) +
            list(metabase_dir.glob("fvendas.parquet")),
            key=lambda p: p.stat().st_mtime,
        )
        if vendas:
            con.execute(f"CREATE VIEW fVendas AS SELECT * FROM read_parquet('{_posix(vendas[-1])}')")

        prods = sorted(
            list(metabase_dir.glob("tmb_churn_cache_*_produtores.parquet")) +
            list(metabase_dir.glob("dprodutores.parquet")),
            key=lambda p: p.stat().st_mtime,
        )
        if prods:
            con.execute(f"CREATE VIEW dProdutores AS SELECT * FROM read_parquet('{_posix(prods[-1])}')")

    closer = ROOT / "data" / "hubspot" / "hs_closer_pipeline.parquet"
    if closer.exists():
        con.execute(f"CREATE VIEW hs_closer_pipeline AS SELECT * FROM read_parquet('{_posix(closer)}')")

    growth = ROOT / "data" / "hubspot" / "hs_growth_leads.parquet"
    if growth.exists():
        con.execute(f"CREATE VIEW hs_growth_leads AS SELECT * FROM read_parquet('{_posix(growth)}')")

    # Tabelas auxiliares (views calculadas — requerem fVendas + dProdutores)
    # Gera colunas mensais dinamicamente com base na data atual —
    # inclui automaticamente novos meses a cada restart do servidor.
    try:
        from datetime import date as _date
        _start = (_date(2022, 1, 1).year, _date(2022, 1, 1).month)
        _today = _date.today()
        _valor_cols, _status_cols, _year_cols = [], [], set()
        _y, _m = _start
        while (_y, _m) <= (_today.year, _today.month):
            _ds = f"{_y:04d}-{_m:02d}-01"
            _lv = f"{_m:02d}/{_y:04d}"
            _valor_cols.append(
                f'        COALESCE(SUM(CASE WHEN CAST(v."Data" AS DATE) = \'{_ds}\''
                f' THEN v."Valor" ELSE 0 END), 0) AS "{_lv}"'
            )
            _status_cols.append(
                f'        MAX(CASE WHEN CAST(v."Data" AS DATE) = \'{_ds}\''
                f' THEN v."Status" END) AS "Status {_lv}"'
            )
            _year_cols.add(_y)
            _m += 1
            if _m > 12:
                _m = 1
                _y += 1
        _annual = "\n".join(
            f'        COALESCE(SUM(CASE WHEN YEAR(v."Data") = {yr}'
            f' THEN v."Valor" ELSE 0 END), 0) AS "{yr}",'
            for yr in sorted(_year_cols)
        )
        _valor_block  = ",\n".join(_valor_cols)
        _status_block = ",\n".join(_status_cols)

        _base_cte = f"""
WITH primeira_venda AS (
    SELECT "Código", "Valor" AS "$ 1ª Venda"
    FROM fVendas WHERE "Valor" > 0
    QUALIFY ROW_NUMBER() OVER (PARTITION BY "Código" ORDER BY "Data") = 1
)
SELECT
    p."Código", p."Produtor", p."Cluster", p."Gestor",
    p."Data Parceria", p."Data 1ª Venda",
    CASE WHEN p."Data Parceria" IS NOT NULL AND p."Data 1ª Venda" IS NOT NULL
         THEN DATEDIFF('day', p."Data Parceria", p."Data 1ª Venda") END AS "Dias p/ 1ª Venda",
    pv."$ 1ª Venda",
    COALESCE(SUM(v."Valor"), 0) AS "$ Total TMB",
{_annual}
"""

        con.execute(
            f'CREATE OR REPLACE VIEW "Produtores Consulta" AS\n'
            + _base_cte
            + _valor_block
            + '\nFROM dProdutores p'
            + '\nLEFT JOIN fVendas v ON v."Código" = p."Código"'
            + '\nLEFT JOIN primeira_venda pv ON pv."Código" = p."Código"'
            + '\nGROUP BY p."Código", p."Produtor", p."Cluster", p."Gestor",'
            + '\n         p."Data Parceria", p."Data 1ª Venda", pv."$ 1ª Venda"'
            + '\nORDER BY SUM(v."Valor") DESC NULLS LAST'
        )

        con.execute(
            f'CREATE OR REPLACE VIEW "Produtores Status" AS\n'
            + _base_cte
            + _status_block
            + '\nFROM dProdutores p'
            + '\nLEFT JOIN fVendas v ON v."Código" = p."Código"'
            + '\nLEFT JOIN primeira_venda pv ON pv."Código" = p."Código"'
            + '\nGROUP BY p."Código", p."Produtor", p."Cluster", p."Gestor",'
            + '\n         p."Data Parceria", p."Data 1ª Venda", pv."$ 1ª Venda"'
            + '\nORDER BY SUM(v."Valor") DESC NULLS LAST'
        )

    except Exception as e:
        logger.warning(f"Erro ao criar Tabelas Auxiliares: {e}")

    return con


@app.get("/explorer", response_class=HTMLResponse)
async def explorer_page():
    html_path = UI_DIR / "explorer.html"
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/explorer/tables")
async def explorer_tables():
    try:
        con = _get_explorer_con()
        tables = []
        for name in ["fVendas", "dProdutores", "hs_closer_pipeline", "hs_growth_leads"]:
            try:
                count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                cols = [r[0] for r in con.execute(f'DESCRIBE "{name}"').fetchall()]
                tables.append({"name": name, "rows": count, "columns": cols, "type": "table"})
            except Exception:
                tables.append({"name": name, "rows": None, "columns": [], "type": "table", "unavailable": True})
        for name in ["Produtores Consulta", "Produtores Status"]:
            try:
                count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                cols = [r[0] for r in con.execute(f'DESCRIBE "{name}"').fetchall()]
                tables.append({"name": name, "rows": count, "columns": cols, "type": "aux"})
            except Exception:
                tables.append({"name": name, "rows": None, "columns": [], "type": "aux", "unavailable": True})
        con.close()
        return {"tables": tables}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/explorer/query")
async def explorer_query(request: Request):
    import pandas as pd
    body = await request.json()
    sql = body.get("sql", "").strip()
    if not sql:
        return JSONResponse(status_code=400, content={"error": "SQL vazio"})
    sql_upper = sql.upper().strip()
    if sql_upper.startswith("SELECT") and "LIMIT" not in sql_upper:
        sql = f"SELECT * FROM ({sql}) AS _q LIMIT 500"
    try:
        con = _get_explorer_con()
        df = con.execute(sql).fetchdf()
        con.close()
        columns = list(df.columns)
        rows = [
            [("" if str(v) in ("nan", "NaT", "None") else str(v)) for v in row]
            for row in df.itertuples(index=False, name=None)
        ]
        return {"columns": columns, "rows": rows}
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.post("/explorer/browse")
async def explorer_browse(request: Request):
    body = await request.json()
    table      = body.get("table", "")
    filters    = body.get("filters", {}) or {}
    sort_col   = body.get("sort_col") or None
    sort_dir   = body.get("sort_dir", "asc")
    offset     = int(body.get("offset", 0))
    limit      = min(int(body.get("limit", 300)), 500)

    known = ["fVendas", "dProdutores", "hs_closer_pipeline", "hs_growth_leads",
             "Produtores Consulta", "Produtores Status"]
    if table not in known:
        return JSONResponse(status_code=400, content={"error": f"Tabela desconhecida: {table}"})

    try:
        con = _get_explorer_con()
        try:
            cols = [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
        except Exception:
            con.close()
            return JSONResponse(status_code=400, content={"error": f"Tabela não disponível: {table}"})

        for col in filters:
            if col not in cols:
                con.close()
                return JSONResponse(status_code=400, content={"error": f"Coluna inválida: {col}"})
        if sort_col is not None and sort_col not in cols:
            con.close()
            return JSONResponse(status_code=400, content={"error": f"Coluna de ordenação inválida: {sort_col}"})

        parts = [f'SELECT * FROM "{table}"']
        active_filters = {}
        for c, v in filters.items():
            if isinstance(v, list) and v:
                active_filters[c] = v
            elif v and not isinstance(v, list):
                active_filters[c] = v
        if active_filters:
            clauses = []
            for col, val in active_filters.items():
                if isinstance(val, list):
                    escaped = [str(item).replace("'", "''") for item in val]
                    placeholders = ", ".join(f"'{item}'" for item in escaped)
                    clauses.append(f'CAST("{col}" AS VARCHAR) IN ({placeholders})')
                else:
                    safe = str(val).replace("'", "''")
                    clauses.append(f'CAST("{col}" AS VARCHAR) ILIKE \'%{safe}%\'')
            if clauses:
                parts.append("WHERE " + " AND ".join(clauses))
        if sort_col:
            dir_str = "ASC" if sort_dir.lower() == "asc" else "DESC"
            parts.append(f'ORDER BY "{sort_col}" {dir_str}')
        parts.append(f"LIMIT {limit} OFFSET {offset}")

        df = con.execute(" ".join(parts)).fetchdf()
        con.close()
        columns = list(df.columns)
        rows = []
        for row in df.itertuples(index=False, name=None):
            safe_row = []
            for v in row:
                s = str(v)
                safe_row.append("" if s in ("nan", "NaT", "None", "<NA>", "NaN") else s)
            rows.append(safe_row)
        return JSONResponse(content={"columns": columns, "rows": rows, "has_more": len(rows) == limit})
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.get("/explorer/distinct")
async def explorer_distinct(table: str, column: str, prefix: str = ""):
    known = ["fVendas", "dProdutores", "hs_closer_pipeline", "hs_growth_leads",
             "Produtores Consulta", "Produtores Status"]
    if table not in known:
        return JSONResponse(status_code=400, content={"error": "Tabela inválida"})
    try:
        con = _get_explorer_con()
        cols = [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
        if column not in cols:
            con.close()
            return JSONResponse(status_code=400, content={"error": "Coluna inválida"})
        where = f'''
            WHERE "{column}" IS NOT NULL
              AND CAST("{column}" AS VARCHAR) NOT IN ('', 'nan', 'None', 'NaT', 'NaN', '<NA>')
        '''
        if prefix:
            safe = prefix.replace("'", "''")
            where += f" AND CAST(\"{column}\" AS VARCHAR) ILIKE '{safe}%'"
        sql = f'''
            SELECT DISTINCT CAST("{column}" AS VARCHAR) AS v
            FROM "{table}"
            {where}
            ORDER BY v
            LIMIT 5000
        '''
        rows = con.execute(sql).fetchall()
        con.close()
        return JSONResponse(content={"values": [r[0] for r in rows]})
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.post("/explorer/dashboard/churn")
async def explorer_dashboard_churn(request: Request):
    body         = await request.json()
    gestores     = body.get("gestores") or []
    data_inicio  = body.get("data_inicio", "2022-01-01")
    data_fim_raw = body.get("data_fim") or ""

    try:
        from datetime import date as _date
        parts    = data_fim_raw[:7].split("-")
        data_fim = f"{parts[0]}-{parts[1]}-01"
    except Exception:
        from datetime import date as _date
        t = _date.today()
        data_fim = f"{t.year}-{t.month:02d}-01"

    def _sql_str(s): return "'" + str(s).replace("'", "''") + "'"
    if gestores:
        gestor_filter = f'p."Gestor" IN ({", ".join(_sql_str(g) for g in gestores)})'
    else:
        gestor_filter = "p.\"Gestor\" != 'TMB Educação'"

    try:
        con = _get_explorer_con()

        gest_rows = con.execute(
            "SELECT DISTINCT \"Gestor\" FROM dProdutores "
            "WHERE \"Gestor\" != 'TMB Educação' ORDER BY \"Gestor\""
        ).fetchall()
        gestores_disp = [r[0] for r in gest_rows]

        mes_rec = con.execute(f"""
            SELECT MAX(CAST(v."Data" AS DATE))
            FROM fVendas v JOIN dProdutores p ON p."Código" = v."Código"
            WHERE CAST(v."Data" AS DATE) BETWEEN '{data_inicio}' AND '{data_fim}'
              AND {gestor_filter}
        """).fetchone()[0]
        if not mes_rec:
            con.close()
            return JSONResponse(status_code=400, content={"error": "Sem dados no range selecionado"})

        mes_ant_sql = f"CAST(DATE '{mes_rec}' - INTERVAL '1 month' AS DATE)"

        totais = con.execute(f"""
            SELECT COUNT(DISTINCT v."Código") FROM fVendas v
            JOIN dProdutores p ON p."Código" = v."Código"
            WHERE CAST(v."Data" AS DATE) = DATE '{mes_rec}'
              AND v."Status" != 'Inativo' AND {gestor_filter}
        """).fetchone()[0] or 0

        totais_ant = con.execute(f"""
            SELECT COUNT(DISTINCT v."Código") FROM fVendas v
            JOIN dProdutores p ON p."Código" = v."Código"
            WHERE CAST(v."Data" AS DATE) = {mes_ant_sql}
              AND v."Status" != 'Inativo' AND {gestor_filter}
        """).fetchone()[0] or 0
        produtores_ativos_diff = totais - totais_ant

        atuais_churn = con.execute(f"""
            SELECT COUNT(DISTINCT v."Código") FROM fVendas v
            JOIN dProdutores p ON p."Código" = v."Código"
            WHERE CAST(v."Data" AS DATE) = DATE '{mes_rec}'
              AND v."Status" = 'Churn' AND {gestor_filter}
        """).fetchone()[0] or 0

        novos_churns = con.execute(f"""
            SELECT COUNT(DISTINCT v."Código") FROM fVendas v
            JOIN dProdutores p ON p."Código" = v."Código"
            WHERE CAST(v."Data" AS DATE) = DATE '{mes_rec}'
              AND v."Status" = 'Churn' AND v."Status_Anterior" = 'Pré-Churn'
              AND {gestor_filter}
        """).fetchone()[0] or 0

        base_inicio_mes = con.execute(f"""
            SELECT COUNT(DISTINCT v."Código") FROM fVendas v
            JOIN dProdutores p ON p."Código" = v."Código"
            WHERE CAST(v."Data" AS DATE) = {mes_ant_sql}
              AND v."Status" IN ('Ativo', 'Pré-Churn') AND {gestor_filter}
        """).fetchone()[0] or 0

        taxa_atual  = round(novos_churns / base_inicio_mes, 4) if base_inicio_mes else 0.0
        meta_valor  = round(base_inicio_mes * 0.05)

        hist_rows = con.execute(f"""
            WITH meses AS (
                SELECT DISTINCT CAST("Data" AS DATE) AS mes
                FROM fVendas
                WHERE CAST("Data" AS DATE) BETWEEN '{data_inicio}' AND '{data_fim}'
                ORDER BY mes DESC LIMIT 12
            ),
            churn_m AS (
                SELECT CAST(v."Data" AS DATE) AS mes, COUNT(DISTINCT v."Código") AS qtd
                FROM fVendas v JOIN dProdutores p ON p."Código" = v."Código"
                WHERE v."Status" = 'Churn' AND v."Status_Anterior" = 'Pré-Churn'
                  AND {gestor_filter} GROUP BY 1
            ),
            base_m AS (
                SELECT CAST(v."Data" AS DATE) AS mes, COUNT(DISTINCT v."Código") AS qtd
                FROM fVendas v JOIN dProdutores p ON p."Código" = v."Código"
                WHERE v."Status" IN ('Ativo', 'Pré-Churn') AND {gestor_filter} GROUP BY 1
            )
            SELECT
                STRFTIME(m.mes, '%Y-%m') AS mes,
                COALESCE(c.qtd, 0) AS produtores_churn,
                COALESCE(b.qtd, 0) AS produtores_totais,
                ROUND(COALESCE(c.qtd, 0) * 1.0 / NULLIF(b.qtd, 0), 4) AS taxa
            FROM meses m
            LEFT JOIN churn_m c ON c.mes = m.mes
            LEFT JOIN base_m  b ON b.mes = CAST(m.mes - INTERVAL '1 month' AS DATE)
            ORDER BY m.mes DESC
        """).fetchall()

        tabela = [{"mes": r[0], "produtores_churn": r[1],
                   "produtores_totais": r[2], "taxa": float(r[3] or 0)} for r in hist_rows]

        taxa_media12m = round(sum(r["taxa"] for r in tabela) / len(tabela), 4) if tabela else 0.0
        atuais_churn_perc_total = round(atuais_churn / totais, 4) if totais else 0.0
        if tabela:
            melhor = min(tabela, key=lambda r: r["taxa"])
            melhor_taxa_12m, melhor_taxa_12m_mes = melhor["taxa"], melhor["mes"]
        else:
            melhor_taxa_12m, melhor_taxa_12m_mes = 0.0, ""

        graf_rows = con.execute(f"""
            SELECT
                STRFTIME(CAST(v."Data" AS DATE), '%Y-%m') AS mes,
                COUNT(DISTINCT CASE WHEN v."Status" = 'Ativo'     THEN v."Código" END) AS ativos,
                COUNT(DISTINCT CASE WHEN v."Status" = 'Pré-Churn' THEN v."Código" END) AS pre_churn,
                COUNT(DISTINCT CASE WHEN v."Status" = 'Churn'     THEN v."Código" END) AS churn,
                COUNT(DISTINCT v."Código") AS total
            FROM fVendas v JOIN dProdutores p ON p."Código" = v."Código"
            WHERE CAST(v."Data" AS DATE) BETWEEN '{data_inicio}' AND '{data_fim}'
              AND v."Status" != 'Inativo' AND {gestor_filter}
            GROUP BY 1 ORDER BY 1
        """).fetchall()

        grafico = [{"mes": r[0], "ativos": r[1], "pre_churn": r[2],
                    "churn": r[3], "total": r[4]} for r in graf_rows]

        con.close()
        return JSONResponse(content={
            "gestores_disponiveis": gestores_disp,
            "kpis": {
                "produtores_totais":       totais,
                "produtores_ativos_diff":  produtores_ativos_diff,
                "atuais_churn":            atuais_churn,
                "atuais_churn_perc_total": atuais_churn_perc_total,
                "novos_churns":            novos_churns,
                "base_inicio_mes":         base_inicio_mes,
                "taxa_churn_atual":        taxa_atual,
                "meta_churn_valor":        meta_valor,
                "meta_churn_perc":         0.05,
                "taxa_churn_media_12m":    taxa_media12m,
                "melhor_taxa_12m":         melhor_taxa_12m,
                "melhor_taxa_12m_mes":     melhor_taxa_12m_mes,
            },
            "tabela_historica": tabela,
            "grafico_status": grafico,
        })
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


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
