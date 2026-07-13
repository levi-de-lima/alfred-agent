# -*- coding: utf-8 -*-
import json, os, shutil, sys, re
sys.stdout.reconfigure(encoding="utf-8")
SP=os.path.dirname(__file__)
DEST=r"C:\Users\TMB1.NOTE-TMB46\OneDrive - TMB Educação\TMB - Documentos\Comercial\5 - Rev Ops\05_Projetos\AI_Churn\Estudo_Churn_Estrategico"
XLS_URL="https://tmbeducacao.sharepoint.com/:x:/r/Documentos%20Compartilhados/Comercial/5%20-%20Rev%20Ops/05_Projetos/AI_Churn/Estudo_Churn_Estrategico/Inadimplencia_HighTicket_TMB_v11.xlsx?d=wa814cc71736e40f4975cef4300a3f909&csf=1&web=1&e=FTavJZ"

tpl=open(os.path.join(SP,"dash3_template.html"),encoding="utf-8").read()
data=open(os.path.join(SP,"dash_data2.json"),encoding="utf-8").read()
htdata=open(os.path.join(SP,"ht_data.json"),encoding="utf-8").read()
logos=json.load(open(os.path.join(SP,"logos2_b64.json"),encoding="utf-8"))
json.loads(data); json.loads(htdata)   # valida
lw=logos["tmb_logo_tight.png"]["uri"]; ld=logos["tmb_logo_dark_tight.png"]["uri"]

html=(tpl.replace("__DATA__",data).replace("__HT_DATA__",htdata)
        .replace("__LOGO_WHITE__",lw).replace("__LOGO_DARK__",ld)
        .replace("__XLS_URL__",XLS_URL))
for ph in ["__DATA__","__HT_DATA__","__LOGO_WHITE__","__LOGO_DARK__","__XLS_URL__"]:
    assert ph not in html, "placeholder remanescente: "+ph

out=os.path.join(DEST,"Dashboard_Churn_Estrategico.html")
open(out,"w",encoding="utf-8").write(html)
open(os.path.join(SP,"Dashboard_Churn_Estrategico.html"),"w",encoding="utf-8").write(html)

# dump dos scripts p/ checagem jsdom (com dados reais)
scripts=re.findall(r"<script>(.*?)</script>",html,re.S)
open(os.path.join(SP,"_check3.js"),"w",encoding="utf-8").write("\n;\n".join(scripts))

# copiar insumos p/ pasta oficial
os.makedirs(os.path.join(DEST,"scripts"),exist_ok=True); os.makedirs(os.path.join(DEST,"dados"),exist_ok=True)
for f in ["mega2.py","ht_extract.py","ht_study.py","inject3.py","dash3_template.html"]:
    if os.path.exists(os.path.join(SP,f)): shutil.copy(os.path.join(SP,f),os.path.join(DEST,"scripts",f))
for f in ["dash_data2.json","ht_data.json"]:
    if os.path.exists(os.path.join(SP,f)): shutil.copy(os.path.join(SP,f),os.path.join(DEST,"dados",f))
print("HTML v16:",os.path.getsize(out),"bytes ->",out)
print("scripts extraídos:",len(scripts),"| _check3.js:",os.path.getsize(os.path.join(SP,"_check3.js")),"bytes")
