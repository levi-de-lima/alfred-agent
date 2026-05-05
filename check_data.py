"""Diagnóstico temporário — verifica schema e coluna Valor da fVendas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from data_loader import load_data

print("=" * 60)
print("Carregando dados com force_refresh=True...")
print("=" * 60)

payload = load_data(force_refresh=True)
df = payload.vendas

print(f"\nFonte: {payload.source}")
print(f"Data de referência: {payload.data_reference_date}")
print(f"Linhas: {len(df)}")

print("\n--- COLUNAS ---")
print(list(df.columns))

print("\n--- DTYPES ---")
print(df.dtypes.to_string())

print("\n--- COLUNA VALOR ---")
if "Valor" in df.columns:
    print(df["Valor"].describe().to_string())
    print(f"\nNulos: {df['Valor'].isna().sum()}")
    print(f"Zeros: {(df['Valor'] == 0).sum()}")
    print(f"Dtype: {df['Valor'].dtype}")
else:
    print("COLUNA VALOR NÃO ENCONTRADA")

print("\n--- PRIMEIRAS 3 LINHAS ---")
print(df.head(3).to_string())
