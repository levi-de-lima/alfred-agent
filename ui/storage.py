"""
storage.py — Camada de persistência SQLite para histórico de chats.

Localização do banco: <project_root>/data/chats.db
Tabela única: chats(id TEXT PK, title TEXT, created_at TEXT, updated_at TEXT, messages_json TEXT)
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data" / "chats.db"
_MAX_TURNS = 5
_MAX_RESPONSE_CHARS = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL DEFAULT 'Nova conversa',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                messages_json TEXT NOT NULL DEFAULT '[]'
            )
        """)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def list_chats() -> list[dict]:
    """Retorna lista de chats sem messages, ordenada do mais recente."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT id, title, updated_at,
                   (SELECT COUNT(*) FROM json_each(messages_json)) AS message_count
            FROM chats
            ORDER BY updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_chat(chat_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at, messages_json FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["messages"] = json.loads(data.pop("messages_json"))
    return data


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

def create_chat() -> dict:
    chat_id = str(uuid.uuid4())
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chats (id, title, created_at, updated_at, messages_json) VALUES (?,?,?,?,?)",
            (chat_id, "Nova conversa", now, now, "[]"),
        )
    return {"id": chat_id, "title": "Nova conversa", "created_at": now, "updated_at": now}


def update_title(chat_id: str, title: str) -> bool:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, chat_id),
        )
    return cur.rowcount > 0


def delete_chat(chat_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return cur.rowcount > 0


def append_message(chat_id: str, role: str, content: str) -> bool:
    """Adiciona uma mensagem à lista JSON do chat e atualiza updated_at."""
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT messages_json FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if row is None:
            return False
        messages: list[dict] = json.loads(row["messages_json"])
        messages.append({"role": role, "content": content, "timestamp": now})
        conn.execute(
            "UPDATE chats SET messages_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(messages, ensure_ascii=False), now, chat_id),
        )
    return True


def get_last_n_turns(chat_id: str, n: int = _MAX_TURNS) -> list[dict[str, str]]:
    """
    Retorna as últimas n trocas (user+assistant) no formato esperado pelo orchestrator.
    Respostas do assistente são truncadas a _MAX_RESPONSE_CHARS como o histórico em memória fazia.
    """
    row = None
    with _connect() as conn:
        row = conn.execute(
            "SELECT messages_json FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
    if row is None:
        return []
    messages: list[dict] = json.loads(row["messages_json"])
    # Filtra só as trocas completas, pega os últimos n*2 items
    truncated = messages[-(n * 2):]
    result = []
    for msg in truncated:
        content = msg["content"]
        if msg["role"] == "assistant":
            content = content[:_MAX_RESPONSE_CHARS] + ("…" if len(content) > _MAX_RESPONSE_CHARS else "")
        result.append({"role": msg["role"], "content": content})
    return result


def message_count(chat_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT (SELECT COUNT(*) FROM json_each(messages_json)) AS cnt FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
    return row["cnt"] if row else 0
