# -*- coding: utf-8 -*-
import json, os, sys
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
TR=r"C:\Users\TMB1.NOTE-TMB46\.claude\projects\C--Users-TMB1-NOTE-TMB46\1272d1c2-416e-4631-907f-564d564efdef\tool-results"
SP=os.path.dirname(__file__)
NAMES={"88240459":"TMB Educação","85897724":"Nicole Coelho","88559648":"Maisa Cavalcanti",
 "86257559":"Pedro Davi","85897723":"Marcus Vinicius","92940911":"Edison Neto","85897726":"Danielle Prado",
 "85897722":"Rafaela Pinheiro","84371708":"Nathan Rebecchi","85897725":"Raquel Carvalho","86017305":"Renato Pavan"}
TMB="88240459"
mot_files=["mcp-claude_ai_HubSpot-search_crm_objects-1783455475244.txt","mcp-claude_ai_HubSpot-search_crm_objects-1783455510611.txt"]
rows=[]
for f in mot_files:
    d=json.load(open(os.path.join(TR,f),encoding="utf-8"))
    for r in d["results"]:
        p=r["properties"]; rows.append({"motivos":p.get("motivos_de_churn"),"owner":p.get("hubspot_owner_id"),"status_hs":p.get("status_calculado")})
mot=pd.DataFrame(rows)
def occ(df):
    m=[]
    for v in df["motivos"].dropna():
        m+=[t.strip() for t in str(v).split(";") if t.strip()]
    return pd.Series(m).value_counts()
col=occ(mot[mot["owner"]==TMB]); ges=occ(mot[mot["owner"]!=TMB])
Ncol=int(col.sum()); Nges=int(ges.sum())
motivos_order=["Inadimplência Alta","Outro Motivo","Sem retorno do produtor","Janela de Lançamentos","Encerrou/Pausou o Projeto","Falta de Antecipação","Problemas de Fluxo de Caixa","Problemas de Relacionamento com a TMB","Taxa do Produtor TMB"]
comp=[{"motivo":m,"coletivo_pct":round(100*col.get(m,0)/Ncol,1),"gestor_pct":round(100*ges.get(m,0)/Nges,1),"coletivo_n":int(col.get(m,0)),"gestor_n":int(ges.get(m,0))} for m in motivos_order]
# gestor table
gp1=json.load(open(os.path.join(TR,"mcp-claude_ai_HubSpot-search_crm_objects-1783455623869.txt"),encoding="utf-8"))
den=[r["properties"].get("hubspot_owner_id") for r in gp1["results"]]
den+=[r["owner"] for r in json.load(open(os.path.join(SP,"gestor_churn_p2.json"),encoding="utf-8"))["results"]]
churn_den=pd.Series(den).value_counts().to_dict(); churn_den[TMB]=3474
mot_churn=mot[mot["status_hs"]=="Churn"]; doc_churn=mot_churn["owner"].value_counts().to_dict()
def top3(o):
    dd=mot_churn[mot_churn["owner"]==o]; vc=occ(dd)
    return [{"m":k,"n":int(v)} for k,v in vc.head(3).items()]
order=[TMB]+[o for o in sorted(churn_den,key=lambda x:-churn_den.get(x,0)) if o!=TMB]
tbl=[{"gestor":NAMES.get(o,o),"churn":int(churn_den[o]),"doc":int(doc_churn.get(o,0)),
      "cov":round(100*doc_churn.get(o,0)/churn_den[o],1) if churn_den[o] else 0,"top":top3(o)} for o in order if o in churn_den]
data={"ref":"jun/2026","cs_total":5420,"churn_hs":3706,"churn_meta":3656,"pre_hs":491,"pre_meta":496,
 "doc_total":282,"cov_total":round(100*282/3706,1),
 "coletivo":{"churn":3474,"doc":int(doc_churn.get(TMB,0)),"cov":round(100*doc_churn.get(TMB,0)/3474,1)},
 "gestores":{"churn":232,"doc":int(sum(v for k,v in doc_churn.items() if k!=TMB)),"cov":round(100*sum(v for k,v in doc_churn.items() if k!=TMB)/232,1)},
 "concord_churn":282,"Ncol":Ncol,"Nges":Nges,"comp":comp,"tbl":tbl}
open(os.path.join(SP,"dash_data.json"),"w",encoding="utf-8").write(json.dumps(data,ensure_ascii=False,indent=1))
print(json.dumps(data,ensure_ascii=False,indent=1))
