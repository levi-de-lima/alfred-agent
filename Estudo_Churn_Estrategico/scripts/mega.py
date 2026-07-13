# -*- coding: utf-8 -*-
import json, os, sys, glob
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
TR=r"C:\Users\TMB1.NOTE-TMB46\.claude\projects\C--Users-TMB1-NOTE-TMB46\1272d1c2-416e-4631-907f-564d564efdef\tool-results"
SP=os.path.dirname(__file__)
DH=r"C:\Users\TMB1.NOTE-TMB46\OneDrive - TMB Educação\TMB - Documentos\Comercial\5 - Rev Ops\05_Projetos\Data_Hub"
NAMES={"88240459":"TMB Educação","85897724":"Nicole Coelho","88559648":"Maisa Cavalcanti","86257559":"Pedro Davi","85897723":"Marcus Vinicius","92940911":"Edison Neto","85897726":"Danielle Prado","85897722":"Rafaela Pinheiro","84371708":"Nathan Rebecchi","85897725":"Raquel Carvalho","86017305":"Renato Pavan"}
TMB="88240459"

# ---------- gestor base (922) ----------
gest={}   # codigo(int) -> owner
gfiles=["1783456189723","1783456191838","1783456194335","1783456196769"]
for fid in gfiles:
    d=json.load(open(os.path.join(TR,f"mcp-claude_ai_HubSpot-search_crm_objects-{fid}.txt"),encoding="utf-8"))
    for r in d["results"]:
        c=r["properties"].get("codigo_produtor"); o=r["properties"].get("hubspot_owner_id")
        if c not in (None,""): gest[int(float(c))]=o
for r in json.load(open(os.path.join(SP,"gestor_base_p5.json"),encoding="utf-8"))["results"]:
    gest[int(r["codigo"])]=r["owner"]
print("Gestor base códigos:",len(gest),"(esperado ~922)")
gest_set=set(gest.keys())

# ---------- motivos (360) ----------
mrows=[]
for fid in ["1783455475244","1783455510611"]:
    d=json.load(open(os.path.join(TR,f"mcp-claude_ai_HubSpot-search_crm_objects-{fid}.txt"),encoding="utf-8"))
    for r in d["results"]:
        p=r["properties"]
        mrows.append({"codigo":int(float(p["codigo_produtor"])) if p.get("codigo_produtor") else None,
                      "motivos":p.get("motivos_de_churn"),"owner":p.get("hubspot_owner_id"),"st":p.get("status_calculado")})
mot=pd.DataFrame(mrows)

# ---------- Metabase ----------
fv=pd.read_parquet(os.path.join(DH,"metabase","fvendas.parquet"))
dp=pd.read_parquet(os.path.join(DH,"metabase","dprodutores.parquet"))
def find(df,key):
    for c in df.columns:
        if key in c.lower(): return c
cod_fv=find(fv,"digo"); C_data=find(fv,"data");
cod_dp=find(dp,"digo"); C_clu=find(dp,"luster"); C_ges=find(dp,"estor"); C_1v=[c for c in dp.columns if "1" in c and "enda" in c.lower()][0]
fv=fv.rename(columns={cod_fv:"codigo",C_data:"data"})
fv["codigo"]=fv["codigo"].astype(int)
ult=fv["data"].max()
print("Mês ref:",ult.date())

# faturamento: pivot codigo x mês
piv=fv.pivot_table(index="codigo",columns="data",values="Valor",aggfunc="sum",fill_value=0.0).sort_index(axis=1)
meses=list(piv.columns)
vals=piv.values
gmv_total=vals.sum(axis=1)
# últimos 12m
last12=[m for m in meses if m> (ult - pd.DateOffset(months=12))]
fat12=piv[last12].sum(axis=1).values
# pico rolling 12m (rolling em axis=0 sobre meses transpostos)
rollT=pd.DataFrame(vals.T).rolling(12,min_periods=1).sum()
peak12=rollT.max(axis=0).values
prod=pd.DataFrame({"codigo":piv.index,"gmv_total":gmv_total,"fat12":fat12,"peak12":peak12})
# status atual
snap=fv[fv["data"]==ult][["codigo","Status"]].rename(columns={"Status":"status"})
prod=prod.merge(snap,on="codigo",how="left")
# safra: primeiro mês com venda
firstsale=fv[fv["Valor"]>0].groupby("codigo")["data"].min().rename("first_sale")
prod=prod.merge(firstsale,on="codigo",how="left")
prod["safra"]=prod["first_sale"].dt.year
# primeiro mês em churn
firstchurn=fv[fv["Status"]=="Churn"].groupby("codigo")["data"].min().rename("first_churn")
prod=prod.merge(firstchurn,on="codigo",how="left")
# cluster por pico 12m (tier de capacidade)
def bucket(f):
    if f<=100_000: return "Energium"
    if f<=1_000_000: return "Palladium"
    if f<=5_000_000: return "Titanium"
    return "Rhodium"
prod["cluster"]=prod["peak12"].apply(bucket)
prod["cluster_atual"]=prod["fat12"].apply(bucket)
# bloco
prod["bloco"]=np.where(prod["codigo"].isin(gest_set),"com","sem")
CLUS=["Energium","Palladium","Titanium","Rhodium"]

churn=prod[prod["status"]=="Churn"].copy()
print("Churned (fVendas >121d):",len(churn),"| com gestor:",(churn['bloco']=='com').sum(),"| sem:",(churn['bloco']=='sem').sum())

# ---------- blocos ----------
def blk(df,b): return df[df["bloco"]==b]
blocks={
 "com":{"base":922,"churn":int((churn['bloco']=='com').sum()),"gmv_opp":float(blk(churn,'com')['peak12'].sum())},
 "sem":{"base":4498,"churn":int((churn['bloco']=='sem').sum()),"gmv_opp":float(blk(churn,'sem')['peak12'].sum())},
}
for b in blocks: blocks[b]["rate"]=round(100*blocks[b]["churn"]/blocks[b]["base"],1)
# doc/cov por bloco (do 360, status Churn)
motC=mot[mot["st"]=="Churn"]
blocks["com"]["doc"]=int((motC["owner"]!=TMB).sum()); blocks["sem"]["doc"]=int((motC["owner"]==TMB).sum())
blocks["com"]["cov"]=round(100*blocks["com"]["doc"]/blocks["com"]["churn"],1)
blocks["sem"]["cov"]=round(100*blocks["sem"]["doc"]/blocks["sem"]["churn"],1)

# ---------- cluster x bloco (churned) ----------
clusters=[]
for cl in CLUS:
    row={"cluster":cl}
    for b in ["com","sem"]:
        sub=churn[(churn["cluster"]==cl)&(churn["bloco"]==b)]
        row[f"churn_{b}"]=int(len(sub)); row[f"gmv_{b}"]=float(sub["peak12"].sum())
    # base ativa por cluster (todos os produtores ativos/pré)
    base=prod[(prod["cluster"]==cl)&(prod["status"].isin(["Ativo","Pré-Churn"]))]
    row["base_ativa"]=int(len(base))
    clusters.append(row)

# ---------- safra x bloco (churned) ----------
safra=[]
for ano in sorted([a for a in churn["safra"].dropna().unique()]):
    sub=churn[churn["safra"]==ano]
    safra.append({"ano":int(ano),"com":int((sub['bloco']=='com').sum()),"sem":int((sub['bloco']=='sem').sum())})

# ---------- temporal: status por mês (base toda) ----------
tmp=fv.groupby(["data","Status"]).size().unstack(fill_value=0).reset_index()
temporal=[]
for _,r in tmp.iterrows():
    temporal.append({"mes":r["data"].strftime("%Y-%m"),"Ativo":int(r.get("Ativo",0)),"Pré-Churn":int(r.get("Pré-Churn",0)),"Churn":int(r.get("Churn",0)),"Inativo":int(r.get("Inativo",0))})

# ---------- churn onset por mês x bloco ----------
on=churn.dropna(subset=["first_churn"]).copy()
on["m"]=on["first_churn"].dt.strftime("%Y-%m")
onset=[]
for m in sorted(on["m"].unique()):
    sub=on[on["m"]==m]
    onset.append({"mes":m,"com":int((sub['bloco']=='com').sum()),"sem":int((sub['bloco']=='sem').sum())})

# ---------- motivos ----------
motj=mot.merge(prod[["codigo","cluster","bloco"]],on="codigo",how="left")
def occ(df):
    m=[]
    for v in df["motivos"].dropna():
        m+=[t.strip() for t in str(v).split(";") if t.strip()]
    return pd.Series(m).value_counts()
def occ_list(df): return [{"m":k,"n":int(v)} for k,v in occ(df).items()]
motivos={"geral":occ_list(motj),
 "com":occ_list(motj[motj["owner"]!=TMB]),
 "sem":occ_list(motj[motj["owner"]==TMB]),
 "por_cluster":{cl:occ_list(motj[motj["cluster"]==cl]) for cl in CLUS}}

# ---------- gestores ----------
gest_owner=pd.Series(gest).rename("owner");
gdf=pd.DataFrame({"codigo":list(gest.keys()),"owner":list(gest.values())}).merge(prod[["codigo","status","cluster","peak12"]],on="codigo",how="left")
gestores=[]
for o,nm in NAMES.items():
    if o==TMB: continue
    sub=gdf[gdf["owner"]==o]
    base=len(sub); ch=int((sub["status"]=="Churn").sum())
    docc=int(((motC["owner"]==o)).sum())
    top=occ_list(motj[(motj["owner"]==o)&(motj["st"]=="Churn")])[:3]
    gestores.append({"nome":nm,"base":base,"churn":ch,"rate":round(100*ch/base,1) if base else 0,
                     "doc":docc,"cov":round(100*docc/ch,1) if ch else 0,"gmv_opp":float(sub[sub['status']=='Churn']['peak12'].sum()),"top":top})
gestores.sort(key=lambda x:-x["churn"])

# ---------- registros anonimizados dos churnados (para filtro no HTML; sem código/nome de produtor) ----------
recs_df=churn.merge(mot[["codigo","motivos"]],on="codigo",how="left")
records=[]
for _,r in recs_df.iterrows():
    cod=int(r["codigo"]); own=gest.get(cod)
    gname=NAMES.get(own,"TMB Educação") if r["bloco"]=="com" else "TMB Educação"
    mv=r["motivos"]
    mlist=[t.strip() for t in str(mv).split(";") if t.strip()] if isinstance(mv,str) and mv else []
    records.append({"b":r["bloco"],"c":r["cluster"],"y":int(r["safra"]) if pd.notna(r["safra"]) else None,
                    "g":gname,"v":round(float(r["peak12"])),
                    "cm":r["first_churn"].strftime("%Y-%m") if pd.notna(r["first_churn"]) else None,
                    "m":mlist})
print("Registros anonimizados:",len(records))

data={"ref":ult.strftime("%Y-%m"),"cs_total":5420,"records":records,
 "cluster_targets":{"Energium":"0–100 mil","Palladium":"100 mil–1 mi","Titanium":"1–5 mi","Rhodium":"5 mi+"},
 "churn_total":int(len(churn)),"blocks":blocks,"clusters":clusters,"safra":safra,
 "temporal":temporal,"onset":onset,"motivos":motivos,"gestores":gestores,
 "gmv_opp_total":float(churn["peak12"].sum()),
 "fin":{"receita_pct":20,"ticket_medio":500,"antecip_pct":8.5,"antecip_uptake":30}}
open(os.path.join(SP,"dash_data.json"),"w",encoding="utf-8").write(json.dumps(data,ensure_ascii=False))
# resumo
print("\n== BLOCOS =="); print(json.dumps(blocks,ensure_ascii=False,indent=1))
print("\n== CLUSTERS (churned) ==");
for c in clusters: print(c)
print("\n== GMV opp total (R$): {:,.0f}".format(data["gmv_opp_total"]))
print("== Safra churned =="); print(safra[-8:])
print("== Motivos geral top6 =="); print(motivos["geral"][:6])
print("== Gestores ==");
for g in gestores: print(g["nome"],"base",g["base"],"churn",g["churn"],"rate",g["rate"],"cov",g["cov"])
print("\nJSON salvo. Tamanho:",os.path.getsize(os.path.join(SP,"dash_data.json")),"bytes")
