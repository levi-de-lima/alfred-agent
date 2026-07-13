# -*- coding: utf-8 -*-
import json, glob, os, sys
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

TR = r"C:\Users\TMB1.NOTE-TMB46\.claude\projects\C--Users-TMB1-NOTE-TMB46\1272d1c2-416e-4631-907f-564d564efdef\tool-results"
files = [
    os.path.join(TR, "mcp-claude_ai_HubSpot-search_crm_objects-1783455475244.txt"),  # page 1
    os.path.join(TR, "mcp-claude_ai_HubSpot-search_crm_objects-1783455510611.txt"),  # page 2
]

rows = []
for f in files:
    with open(f, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for r in data.get("results", []):
        p = r.get("properties", {})
        rows.append({
            "deal_id": r.get("id"),
            "codigo": p.get("codigo_produtor"),
            "motivos": p.get("motivos_de_churn"),
            "owner": p.get("hubspot_owner_id"),
            "status_hs": p.get("status_calculado"),
            "dealname": p.get("dealname"),
            "dealstage": p.get("dealstage"),
        })

hs = pd.DataFrame(rows)
print("### Deals com motivo documentado (CS):", len(hs))
print("Owner counts:")
print(hs["owner"].value_counts(dropna=False).to_string())
print("\nStatus_calculado (HS) dos que têm motivo:")
print(hs["status_hs"].value_counts(dropna=False).to_string())

# ---- Tabular motivos (multi-seleção separada por ';') ----
TMB_OWNER = "88240459"
hs["is_gestor"] = hs["owner"] != TMB_OWNER   # True = gestor nomeado; False = TMB Educação (coletivo)

def explode_motivos(df):
    m = []
    for _, r in df.iterrows():
        val = r["motivos"]
        if not val:
            continue
        for token in str(val).split(";"):
            token = token.strip()
            if token:
                m.append(token)
    return pd.Series(m).value_counts()

print("\n### MOTIVOS DE CHURN — GERAL (contagem por ocorrência; produtor pode ter +1):")
print(explode_motivos(hs).to_string())

print("\n### MOTIVOS — TMB EDUCAÇÃO (coletivo, sem gestor):")
print(explode_motivos(hs[~hs["is_gestor"]]).to_string())

print("\n### MOTIVOS — GESTORES NOMEADOS:")
print(explode_motivos(hs[hs["is_gestor"]]).to_string())

print("\nDistinct owner IDs (para buscar nomes):", sorted(hs["owner"].dropna().unique().tolist()))

# ---- Cruzar com Metabase pelo codigo ----
DH = r"C:\Users\TMB1.NOTE-TMB46\OneDrive - TMB Educação\TMB - Documentos\Comercial\5 - Rev Ops\05_Projetos\Data_Hub"
fv = pd.read_parquet(os.path.join(DH, "metabase", "fvendas.parquet"))
dp = pd.read_parquet(os.path.join(DH, "metabase", "dprodutores.parquet"))

def col(df, starts):
    for c in df.columns:
        if c.startswith(starts):
            return c
    return None

c_cod_fv = col(fv, "C")      # Código
c_data   = col(fv, "Data")
c_status = "Status"
c_cod_dp = col(dp, "C")
c_gestor = "Gestor"
c_cluster = "Cluster"

ultimo = fv[c_data].max()
snap = fv[fv[c_data] == ultimo][[c_cod_fv, c_status]].rename(columns={c_cod_fv: "codigo", c_status: "status_meta"})
snap["codigo"] = snap["codigo"].astype("Int64")

hs["codigo"] = pd.to_numeric(hs["codigo"], errors="coerce").astype("Int64")
merged = hs.merge(snap, on="codigo", how="left")

print("\n### CRUZAMENTO: dos 360 com motivo documentado, qual o Status no Metabase (mês", str(ultimo.date()), ")?")
print(merged["status_meta"].value_counts(dropna=False).to_string())
n_match = merged["status_meta"].notna().sum()
print(f"Casaram pelo código no Metabase: {n_match}/{len(merged)}")

# salvar base cruzada para o entregável
gmap = dp[[c_cod_dp, c_gestor, c_cluster]].rename(columns={c_cod_dp:"codigo", c_gestor:"gestor_meta", c_cluster:"cluster_meta"})
gmap["codigo"] = gmap["codigo"].astype("Int64")
out = merged.merge(gmap, on="codigo", how="left")
outpath = os.path.join(os.path.dirname(__file__), "motivos_cruzado.csv")
out.to_csv(outpath, index=False, encoding="utf-8-sig")
print("\nBase cruzada salva em:", outpath, "linhas:", len(out))
