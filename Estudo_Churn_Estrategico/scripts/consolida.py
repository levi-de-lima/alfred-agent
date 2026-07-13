# -*- coding: utf-8 -*-
import json, os, sys
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

TR = r"C:\Users\TMB1.NOTE-TMB46\.claude\projects\C--Users-TMB1-NOTE-TMB46\1272d1c2-416e-4631-907f-564d564efdef\tool-results"
SP = os.path.dirname(__file__)

NAMES = {
 "88240459":"TMB Educação","85897724":"Nicole Coelho","88559648":"Maisa Cavalcanti",
 "86257559":"Pedro Davi","85897723":"Marcus Vinicius","92940911":"Edison Neto",
 "85897726":"Danielle Prado","85897722":"Rafaela Pinheiro","84371708":"Nathan Rebecchi",
 "85897725":"Raquel Carvalho","86017305":"Renato Pavan",
}
TMB="88240459"

# ---------- 1) 360 deals com motivo ----------
mot_files=["mcp-claude_ai_HubSpot-search_crm_objects-1783455475244.txt",
           "mcp-claude_ai_HubSpot-search_crm_objects-1783455510611.txt"]
rows=[]
for f in mot_files:
    d=json.load(open(os.path.join(TR,f),encoding="utf-8"))
    for r in d["results"]:
        p=r["properties"]
        rows.append({"codigo":p.get("codigo_produtor"),"motivos":p.get("motivos_de_churn"),
                     "owner":p.get("hubspot_owner_id"),"status_hs":p.get("status_calculado")})
mot=pd.DataFrame(rows)
assert len(mot)==360, len(mot)

# ---------- 2) 232 deals de churn dos gestores (denominador) ----------
gp1=json.load(open(os.path.join(TR,"mcp-claude_ai_HubSpot-search_crm_objects-1783455623869.txt"),encoding="utf-8"))
den=[{"codigo":r["properties"].get("codigo_produtor"),"owner":r["properties"].get("hubspot_owner_id")} for r in gp1["results"]]
gp2=json.load(open(os.path.join(SP,"gestor_churn_p2.json"),encoding="utf-8"))
den+=[{"codigo":r["codigo"],"owner":r["owner"]} for r in gp2["results"]]
den=pd.DataFrame(den)
assert len(den)==232, len(den)

# denominador de churn por owner
churn_den = den["owner"].value_counts().to_dict()
churn_den[TMB]=3474   # da query de total (owner EQ TMB)

# ---------- 3) numerador: documentado E em churn-status, por owner ----------
mot_churn = mot[mot["status_hs"]=="Churn"]
doc_churn = mot_churn["owner"].value_counts().to_dict()   # 282 no total
assert mot_churn.shape[0]==282, mot_churn.shape[0]

# ---------- 4) tabela por gestor ----------
def top_motivos(df, n=3):
    m=[]
    for v in df["motivos"].dropna():
        m+=[t.strip() for t in str(v).split(";") if t.strip()]
    vc=pd.Series(m).value_counts()
    return "; ".join(f"{k} ({v})" for k,v in vc.head(n).items())

print("="*90)
print("CHURN NO PIPELINE DE CS (HubSpot 842108729) — POR GESTOR")
print("="*90)
print(f"{'Gestor':<20}{'Churn':>7}{'C/motivo':>10}{'Cobertura':>11}   Top motivos documentados")
print("-"*90)
# ordenar: TMB primeiro, depois gestores por volume de churn
order=[TMB]+[o for o in sorted(churn_den, key=lambda x:-churn_den.get(x,0)) if o!=TMB]
tot_churn=tot_doc=0
for o in order:
    if o not in churn_den: continue
    cd=churn_den[o]; dc=doc_churn.get(o,0)
    tot_churn+=cd; tot_doc+=dc
    docdf=mot_churn[mot_churn["owner"]==o]
    cov=f"{100*dc/cd:.1f}%" if cd else "-"
    print(f"{NAMES.get(o,o):<20}{cd:>7}{dc:>10}{cov:>11}   {top_motivos(docdf)}")
print("-"*90)
print(f"{'TOTAL':<20}{tot_churn:>7}{tot_doc:>10}{100*tot_doc/tot_churn:>10.1f}%")
print(f"\nGestores (sem TMB): churn={tot_churn-3474}  documentado={tot_doc-doc_churn.get(TMB,0)}  cobertura={100*(tot_doc-doc_churn.get(TMB,0))/(tot_churn-3474):.1f}%")
print(f"TMB Educação: churn=3474  documentado={doc_churn.get(TMB,0)}  cobertura={100*doc_churn.get(TMB,0)/3474:.1f}%")

# ---------- 5) validação de consistência com o total isolado ----------
print("\nChecagem: soma churn gestores (deveria=232):", tot_churn-3474)
