import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL  = os.getenv("METABASE_URL", "")
USER      = os.getenv("METABASE_USER")
PASSWORD  = os.getenv("METABASE_PASSWORD")

# ── 1. Login ─────────────────────────────────────────────────────
print("1. Fazendo login...")
resp = requests.post(
    f"{BASE_URL}/api/session",
    json={"username": USER, "password": PASSWORD},
    timeout=15,
)
resp.raise_for_status()
token = resp.json()["id"]
print(f"   Token: {token[:8]}...")

# ── 2. Consulta tabela 645 ────────────────────────────────────────
print("\n2. Consultando tabela 645 (vw_relatorio_gmv_analitico)...")
resp = requests.post(
    f"{BASE_URL}/api/dataset",
    headers={"X-Metabase-Session": token},
    json={
        "database": 3,
        "type": "query",
        "query": {
            "source-table": 645,
            "limit": 5,
        },
    },
    timeout=30,
)
resp.raise_for_status()
data = resp.json()

# ── 3. Imprimir resultado tabela 645 ─────────────────────────────
print("\n3. Resultado tabela 645:")
print(json.dumps(data, ensure_ascii=False, indent=2))

# ── 4. Consulta tabela 626 ────────────────────────────────────────
print("\n4. Consultando tabela 626 (vw_analise_produtor)...")
resp2 = requests.post(
    f"{BASE_URL}/api/dataset",
    headers={"X-Metabase-Session": token},
    json={
        "database": 3,
        "type": "query",
        "query": {
            "source-table": 626,
            "limit": 5,
        },
    },
    timeout=30,
)
resp2.raise_for_status()
data2 = resp2.json()

# ── 5. Imprimir resultado tabela 626 ─────────────────────────────
print("\n5. Resultado tabela 626:")
print(json.dumps(data2, ensure_ascii=False, indent=2))
