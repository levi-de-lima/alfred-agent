"""
Sanitiza parquets HubSpot para compatibilidade com Power BI Desktop.

O Power BI rejeita colunas com tipo Arrow 'null' puro (colunas 100% vazias
onde o PyArrow nao consegue inferir um tipo concreto), causando o erro:
  "Argumento 'dataType' nao pode ser nulo. Nome do parametro: dataType"

Este script detecta e converte essas colunas para string vazia, sobrescrevendo
o arquivo no lugar.

Uso:
    python tools/fix_parquet_powerbi.py                         # ambos os parquets
    python tools/fix_parquet_powerbi.py data/hubspot/foo.parquet  # arquivo especifico
"""

import sys
from pathlib import Path

import pyarrow.parquet as pq
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FILES = [
    ROOT / "data" / "hubspot" / "hs_closer_pipeline.parquet",
    ROOT / "data" / "hubspot" / "hs_growth_leads.parquet",
]


def sanitize(path: Path) -> None:
    if not path.exists():
        print(f"[SKIP] Nao encontrado: {path}")
        return

    table = pq.read_table(path)
    df = table.to_pandas()
    alteracoes: list[str] = []

    # 1. Colunas com tipo Arrow 'null' puro
    for field in table.schema:
        if str(field.type) in ("null", "null_"):
            df[field.name] = ""
            alteracoes.append(f"  null->string: {field.name!r}")

    # 2. Colunas object 100% nulas que escaparam do passo anterior
    for col in df.columns:
        if df[col].dtype == object and df[col].isna().all():
            df[col] = ""
            alteracoes.append(f"  object-null->string: {col!r}")

    # 3. Timestamps com timezone: converter para UTC sem tz
    for col in df.select_dtypes(include=["datetimetz"]).columns:
        df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)
        alteracoes.append(f"  tz->utc-naive: {col!r}")

    df.to_parquet(path, compression="snappy", index=False)

    print(f"\n{path.name}")
    print(f"  Linhas: {len(df)} | Colunas: {len(df.columns)}")
    if alteracoes:
        print(f"  Alteracoes ({len(alteracoes)}):")
        for a in alteracoes:
            print(a)
    else:
        print("  Sem alteracoes necessarias.")


if __name__ == "__main__":
    targets = [Path(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else DEFAULT_FILES
    for t in targets:
        p = t if t.is_absolute() else ROOT / t
        sanitize(p)
