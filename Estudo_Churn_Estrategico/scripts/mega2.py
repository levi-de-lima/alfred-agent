# -*- coding: utf-8 -*-
import json, os, sys, glob
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
TR=r"C:\Users\TMB1.NOTE-TMB46\.claude\projects\C--Users-TMB1-NOTE-TMB46\1272d1c2-416e-4631-907f-564d564efdef\tool-results"
SP=os.path.dirname(__file__)
DH=r"C:\Users\TMB1.NOTE-TMB46\OneDrive - TMB Educação\TMB - Documentos\Comercial\5 - Rev Ops\05_Projetos\Data_Hub"
NAMES={"88240459":"Sem Gestor TMB","85897724":"Nicole Coelho","88559648":"Maisa Cavalcanti","86257559":"Pedro Davi","85897723":"Marcus Vinicius","92940911":"Edison Neto","85897726":"Danielle Prado","85897722":"Rafaela Pinheiro","84371708":"Nathan Rebecchi","85897725":"Raquel Carvalho","86017305":"Renato Pavan"}
TMB="88240459"

# ---- owner map de TODOS os arquivos de search + jsons próprios ----
owner={}   # codigo(int)->owner
for f in glob.glob(os.path.join(TR,"mcp-claude_ai_HubSpot-search_crm_objects-*.txt")):
    try: d=json.load(open(f,encoding="utf-8"))
    except: continue
    for r in d.get("results",[]):
        p=r.get("properties",{})
        c=p.get("codigo_produtor"); o=p.get("hubspot_owner_id")
        if c not in (None,"") and o:
            try: owner[int(float(c))]=o
            except: pass
for r in json.load(open(os.path.join(SP,"gestor_base_p5.json"),encoding="utf-8"))["results"]:
    owner[int(r["codigo"])]=r["owner"]
gestor_set={c for c,o in owner.items() if o!=TMB}
tmb_set={c for c,o in owner.items() if o==TMB}
cs_universe=gestor_set|tmb_set
print("owner map:",len(owner),"| gestores:",len(gestor_set),"| tmb:",len(tmb_set),"| CS universe:",len(cs_universe))

# ---- motivos (360) ----
mrows=[]
for fid in ["1783455475244","1783455510611"]:
    d=json.load(open(os.path.join(TR,f"mcp-claude_ai_HubSpot-search_crm_objects-{fid}.txt"),encoding="utf-8"))
    for r in d["results"]:
        p=r["properties"]
        mrows.append({"codigo":int(float(p["codigo_produtor"])) if p.get("codigo_produtor") else None,"motivos":p.get("motivos_de_churn"),"owner":p.get("hubspot_owner_id"),"st":p.get("status_calculado")})
mot=pd.DataFrame(mrows)
motmap={}
for _,r in mot.iterrows():
    if pd.notna(r["codigo"]) and isinstance(r["motivos"],str) and r["motivos"]:
        motmap[int(r["codigo"])]=[t.strip() for t in r["motivos"].split(";") if t.strip()]

# ---- Metabase ----
_SPX=os.path.dirname(__file__)
fv=pd.read_parquet(os.path.join(_SPX,"fvendas_fresh.parquet"))
dp=pd.read_parquet(os.path.join(_SPX,"dprodutores_fresh.parquet"))
def find(df,k):
    for c in df.columns:
        if k in c.lower(): return c
fv=fv.rename(columns={find(fv,"digo"):"codigo",find(fv,"data"):"data"})
fv["codigo"]=fv["codigo"].astype(int)
_dcod=find(dp,"digo"); _dnome=[c for c in dp.columns if c.lower()=="produtor"][0]
namemap=dict(zip(dp[_dcod].astype(int), dp[_dnome].astype(str)))
_cur=pd.Timestamp.now().normalize().replace(day=1)   # 1º dia do mês corrente
ult=max(m for m in fv["data"].unique() if m<_cur)     # último mês COMPLETO (exclui mês parcial)
fv=fv[fv["data"]<=ult].copy()                         # descarta o mês corrente parcial
print("Ref (mês completo):",ult.date(),"| meses descartados (parciais):",[str(pd.Timestamp(m).date()) for m in fv["data"].unique() if m>ult])
last12_start=ult-pd.DateOffset(months=11)

piv=fv.pivot_table(index="codigo",columns="data",values="Valor",aggfunc="sum",fill_value=0.0).sort_index(axis=1)
meses=list(piv.columns); vals=piv.values
gmv_total=vals.sum(axis=1)
last12=[m for m in meses if m>=last12_start]
fat12=piv[last12].sum(axis=1).values
rollT=pd.DataFrame(vals.T).rolling(12,min_periods=1).sum(); peak12=rollT.max(axis=0).values
prod=pd.DataFrame({"codigo":piv.index,"gmv_total":gmv_total,"fat12":fat12,"peak12":peak12})
snap=fv[fv["data"]==ult][["codigo","Status"]].rename(columns={"Status":"status"})
prod=prod.merge(snap,on="codigo",how="left")
firstsale=fv[fv["Valor"]>0].groupby("codigo")["data"].min().rename("first_sale")
lastsale=fv[fv["Valor"]>0].groupby("codigo")["data"].max().rename("last_sale")
prod=prod.merge(firstsale,on="codigo",how="left").merge(lastsale,on="codigo",how="left")
prod["safra"]=prod["first_sale"].dt.year
firstchurn=fv[fv["Status"]=="Churn"].groupby("codigo")["data"].min().rename("first_churn")
prod=prod.merge(firstchurn,on="codigo",how="left")
def bucket(f):
    if f<=100_000: return "Energium"
    if f<=1_000_000: return "Palladium"
    if f<=5_000_000: return "Titanium"
    return "Rhodium"
prod["cluster"]=prod["peak12"].apply(bucket)
prod["bloco"]=np.where(prod["codigo"].isin(gestor_set),"com","sem")  # binário: com se gestor, senão sem
prod["in_cs"]=prod["codigo"].isin(cs_universe)
CLUS=["Energium","Palladium","Titanium","Rhodium"]

churn=prod[prod["status"]=="Churn"].copy()
pre=prod[prod["status"]=="Pré-Churn"].copy()
print("churn:",len(churn),"| com:",(churn.bloco=='com').sum(),"| sem:",(churn.bloco=='sem').sum())

# ---- blocos: BASE = produtores que já fizeram ≥1 venda (não o pipeline de CS); split por gestor ----
prod_sold=prod[prod["first_sale"].notna()].copy()   # produtores com ao menos 1 venda
base_com=int(prod_sold["codigo"].isin(gestor_set).sum())
base_sem=int(len(prod_sold)-base_com)
print("BASE produtores c/ venda:",len(prod_sold),"| com gestor:",base_com,"| sem gestor (tech-touch):",base_sem)
blocks={
 "com":{"base":base_com,"churn":int((churn.bloco=='com').sum()),"gmv_opp":float(churn[churn.bloco=='com']['peak12'].sum())},
 "sem":{"base":base_sem,"churn":int((churn.bloco=='sem').sum()),"gmv_opp":float(churn[churn.bloco=='sem']['peak12'].sum())},
}
for b in blocks: blocks[b]["rate"]=round(100*blocks[b]["churn"]/blocks[b]["base"],1)
motC=mot[mot["st"]=="Churn"]
blocks["com"]["doc"]=int((motC["owner"]!=TMB).sum()); blocks["sem"]["doc"]=int((motC["owner"]==TMB).sum())
blocks["com"]["cov"]=round(100*blocks["com"]["doc"]/max(1,blocks["com"]["churn"]),1)
blocks["sem"]["cov"]=round(100*blocks["sem"]["doc"]/max(1,blocks["sem"]["churn"]),1)

# ---- clusters churned ----
clusters=[]
for cl in CLUS:
    s=churn[churn.cluster==cl]
    base=prod[(prod.cluster==cl)&(prod.status.isin(["Ativo","Pré-Churn"]))&(prod.in_cs)]
    clusters.append({"cluster":cl,"churn_com":int((s.bloco=='com').sum()),"churn_sem":int((s.bloco=='sem').sum()),
      "gmv_com":float(s[s.bloco=='com']['peak12'].sum()),"gmv_sem":float(s[s.bloco=='sem']['peak12'].sum()),"base_ativa":int(len(base))})

# ---- safra churned ----
safra=[{"ano":int(a),"com":int(((churn.safra==a)&(churn.bloco=='com')).sum()),"sem":int(((churn.safra==a)&(churn.bloco=='sem')).sum())} for a in sorted(churn.safra.dropna().unique())]

# ---- temporal status flow — BASE COMPLETA (produtores com venda), split por gestor (owner do deal) ----
cs = fv[fv["codigo"].isin(cs_universe)].copy()
cs["bloco"]=np.where(cs["codigo"].isin(gestor_set),"com","sem")   # (mantido p/ recovery abaixo)
fvsf = fv.copy(); fvsf["bloco"]=np.where(fvsf["codigo"].isin(gestor_set),"com","sem")  # com=owner nomeado; sem=tech-touch (coletivo/sem deal)
def flow(df):
    g=df.groupby(["data","Status"]).size().unstack(fill_value=0)
    return g
def flow_rows(df):
    g=flow(df); out=[]
    for dt,row in g.iterrows():
        if dt< pd.Timestamp("2022-01-01"): continue
        out.append({"mes":dt.strftime("%Y-%m"),"A":int(row.get("Ativo",0)),"P":int(row.get("Pré-Churn",0)),"C":int(row.get("Churn",0))})
    return out
status_flow={"todos":flow_rows(fv),"com":flow_rows(fvsf[fvsf.bloco=='com']),"sem":flow_rows(fvsf[fvsf.bloco=='sem'])}

# ---- recuperação mensal (reativações) — BASE COMPLETA ----
fvb=fv.copy(); fvb["bloco"]=np.where(fvb["codigo"].isin(gestor_set),"com","sem")
rec=fvb[(fvb["Status"]=="Ativo")&(fvb["Status_Anterior"].isin(["Churn","Pré-Churn"]))].copy()
rec["full"]=rec["Status_Anterior"]=="Churn"
recovery=[]
for dt in sorted(rec["data"].unique()):
    if pd.Timestamp(dt)<pd.Timestamp("2022-01-01"): continue
    sub=rec[rec["data"]==dt]
    recovery.append({"mes":pd.Timestamp(dt).strftime("%Y-%m"),
      "full":int(sub["full"].sum()),"light":int((~sub["full"]).sum()),
      "com":int((sub.bloco=='com').sum()),"sem":int((sub.bloco=='sem').sum())})

# ---- novos ativos: produtores que fazem a 1ª venda por mês (base histórica) ----
_fs=fv[fv["Valor"]>0].groupby("codigo")["data"].min()
_nac=_fs.dt.to_period("M").value_counts().sort_index()
newactives=[{"mes":str(p),"n":int(v)} for p,v in _nac.items() if pd.Timestamp(p.start_time)>=pd.Timestamp("2022-01-01")]
_na12=[o["n"] for o in newactives[-12:]]; newactives_12m=round(sum(_na12)/max(1,len(_na12)),1)

# ---- entrada em churn (onset) mensal — BASE COMPLETA (dos que ESTÃO em churn hoje) ----
on=churn.dropna(subset=["first_churn"]).copy()
onset=[]
for dt in sorted(on["first_churn"].unique()):
    if pd.Timestamp(dt)<pd.Timestamp("2022-01-01"): continue
    sub=on[on["first_churn"]==dt]
    onset.append({"mes":pd.Timestamp(dt).strftime("%Y-%m"),
      "com":int((sub.bloco=='com').sum()),"sem":int((sub.bloco=='sem').sum()),"tot":int(len(sub))})

# ---- taxas de transição 3 estados (Ativo→Pré→Churn) p/ projeção — base completa, médias 12m ----
import statistics as _st
sf_t=status_flow["todos"]
_onm={o["mes"]:o["tot"] for o in onset}; _rem={r["mes"]:(r["full"],r["light"]) for r in recovery}
_rap=[];_rpc=[];_rpa=[];_rca=[]
for i in range(1,len(sf_t)):
    prev,cur=sf_t[i-1],sf_t[i]; m=cur["mes"]
    if m not in _onm or m not in _rem: continue
    A0f,P0f,C0f=prev["A"],prev["P"],prev["C"]; ons=_onm[m]; full,light=_rem[m]
    ap=max(0.0,(cur["P"]-prev["P"])+ons+light)   # A→P por conservação de P
    if A0f>0:_rap.append(ap/A0f)
    if P0f>0:_rpc.append(ons/P0f);_rpa.append(light/P0f)
    if C0f>0:_rca.append(full/C0f)
def _mean12(x,dv): return round(_st.mean(x[-12:]),5) if len(x)>=1 else dv
transrates={"rAP":_mean12(_rap,0.15),"rPC":_mean12(_rpc,0.28),"rPA":_mean12(_rpa,0.10),"rCA":_mean12(_rca,0.015)}
# fator sazonal do onset por mês-calendário (média do mês ÷ média geral), últimos 24m
_recent=[s["mes"] for s in sf_t if s["mes"] in _onm][-24:]
_onall=[_onm[m] for m in _recent]; _onavg=_st.mean(_onall) if _onall else 1
_seas={}
for _mn in range(1,13):
    _vs=[_onm[m] for m in _recent if int(m[5:7])==_mn]
    _seas[str(_mn)]=round(_st.mean(_vs)/_onavg,3) if _vs and _onavg else 1.0
transrates["onset_seasonal"]=_seas
print("TRANSRATES:",transrates)

# ---- motivos ----
motj=mot.copy()
motj["cluster"]=motj["codigo"].map(prod.set_index("codigo")["cluster"])
motj["bloco"]=motj["codigo"].map(lambda c: "com" if c in gestor_set else "sem")
def occ(df):
    m=[]
    for v in df["motivos"].dropna():
        m+=[t.strip() for t in str(v).split(";") if t.strip()]
    return pd.Series(m).value_counts()
def olist(df): return [{"m":k,"n":int(v)} for k,v in occ(df).items()]
motivos={"geral":olist(motj),"com":olist(motj[motj.owner!=TMB]),"sem":olist(motj[motj.owner==TMB]),
  "por_cluster":{cl:olist(motj[motj.cluster==cl]) for cl in CLUS}}

# ---- gestores ----
gestores=[]
for o,nm in NAMES.items():
    if o==TMB: continue
    codes=[c for c,ow in owner.items() if ow==o]
    sub=prod_sold[prod_sold.codigo.isin(codes)]   # só produtores que já venderam (consistente com a base)
    base=len(sub); ch=int((sub.status=="Churn").sum())
    docc=int((motC["owner"]==o).sum())
    top=olist(motj[(motj.owner==o)&(motj.st=="Churn")])[:3]
    gestores.append({"nome":nm,"base":base,"churn":ch,"rate":round(100*ch/base,1) if base else 0,
      "doc":docc,"cov":round(100*docc/ch,1) if ch else 0,"gmv_opp":float(sub[sub.status=="Churn"]["peak12"].sum()),"top":top})
gestores.sort(key=lambda x:-x["churn"])

# ---- registros (churn + pré-churn) enriquecidos ----
def mk_records(df):
    out=[]
    for _,r in df.iterrows():
        cod=int(r.codigo); o=owner.get(cod)
        g=NAMES.get(o,"Sem Gestor TMB") if r.bloco=="com" else "Sem Gestor TMB"
        out.append({"id":cod,"nm":namemap.get(cod,""),"b":r.bloco,"g":g,"c":r.cluster,"y":int(r.safra) if pd.notna(r.safra) else None,
          "st":r.status,"gt":round(float(r.gmv_total)),"g12":round(float(r.fat12)),"pk":round(float(r.peak12)),
          "cm":r.first_churn.strftime("%Y-%m") if pd.notna(r.first_churn) else None,
          "ls":r.last_sale.strftime("%Y-%m") if pd.notna(r.last_sale) else None,
          "m":motmap.get(cod,[])})
    return out
records=mk_records(pd.concat([churn,pre]))
print("records (churn+pré):",len(records))

data={"ref":ult.strftime("%Y-%m"),"cs_total":int(len(prod_sold)),"base_com":base_com,"base_sem":base_sem,"records":records,
 "cluster_targets":{"Energium":"0–100 mil","Palladium":"100 mil–1 mi","Titanium":"1–5 mi","Rhodium":"5 mi+"},
 "churn_total":int(len(churn)),"pre_total":int(len(pre)),"blocks":blocks,"clusters":clusters,"safra":safra,
 "status_flow":status_flow,"recovery":recovery,"onset":onset,"newactives":newactives,"newactives_12m":newactives_12m,"meta_acq_2026":493,
 "meta_acq_monthly":{"2026-01":534.6,"2026-02":588,"2026-03":617,"2026-04":568,"2026-05":690,"2026-06":380,"2026-07":400,"2026-08":438,"2026-09":387,"2026-10":425,"2026-11":505,"2026-12":388},
 "conv_primeira_venda":0.565,   # conversão real ganho→1ª venda (estudo de conversão 1ª venda / Jarvis)
 "transrates":transrates,"motivos":motivos,"gestores":gestores,
 "gmv_opp_total":float(churn["peak12"].sum()),
 "gmv_lentes":{ # lentes de GMV (agregado dos churnados)
   "total_hist":float(churn["gmv_total"].sum()),"ult12m":float(churn["fat12"].sum()),"pico12m":float(churn["peak12"].sum())},
 "fin":{"receita_pct":20,"ticket_medio":500,"antecip_pct":8.5,"antecip_uptake":30}}
open(os.path.join(SP,"dash_data2.json"),"w",encoding="utf-8").write(json.dumps(data,ensure_ascii=False))
print("\nBLOCOS:",json.dumps(blocks,ensure_ascii=False))
print("GMV lentes (mi): total_hist %.1f | ult12m %.1f | pico12m %.1f"%(data["gmv_lentes"]["total_hist"]/1e6,data["gmv_lentes"]["ult12m"]/1e6,data["gmv_lentes"]["pico12m"]/1e6))
print("status_flow meses:",len(status_flow["todos"]),"| recovery meses:",len(recovery))
print("recovery último 6m:",recovery[-6:])
print("onset meses:",len(onset),"| onset último 6m:",onset[-6:])
print("onset média últimos 12m (tot):",round(sum(o['tot'] for o in onset[-12:])/12,1))
print("JSON:",os.path.getsize(os.path.join(SP,"dash_data2.json")),"bytes")
