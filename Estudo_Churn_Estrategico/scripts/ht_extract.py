# -*- coding: utf-8 -*-
import pandas as pd, numpy as np, json, sys
sys.stdout.reconfigure(encoding="utf-8")
F=r"ht_src.xlsx"
df=pd.read_excel(F, sheet_name='Base de Dados', skiprows=2, header=0)
print("shape:",df.shape)
cols=list(df.columns)
print("cols:",[str(c)[:22] for c in cols])
# posicional A..AE
g=df.iloc[:,0:31]
g.columns=['pid','prod','produto','tipo','modal','score','risco','genero','defet','vorig','vparc','parcela','pri','status','vprincipal','vsemjuros','vparcela','Valor','safra','entra','statustmb','recebido','vencido','atrasado','aguardando','pedinad','riscototal','riscoag','riscoat','noperiodo','primper']
for c in ['recebido','vencido','atrasado','aguardando','riscototal','riscoag','riscoat','vprincipal','vsemjuros','vparcela','Valor','entra','noperiodo','score']:
    g[c]=pd.to_numeric(g[c],errors='coerce')
inc=g[(g['entra']==1)].copy()
print("\nparcelas incalc:",len(inc),"(esperado 26729) | pedidos:",inc['pid'].nunique(),"(esperado 2399)")
V,W,X,Y,AA=[inc[c].sum() for c in ['recebido','vencido','atrasado','aguardando','riscototal']]
print("Recebido %.0f (28.974.435) | Vencido %.0f (8.717.346) | Atrasado %.0f (2.721.937) | Aguard %.0f (38.764.140) | Risco %.0f (15.444.801)"%(V,W,X,Y,AA))
print("Inad Original %.4f (0.2313) | Financeira %.4f (0.2831) | Projetada %.4f (0.4547)"%(W/(W+V),(W+X)/(W+X+V),(W+AA)/(W+AA+V)))
# detectar GMV (match 93.351.663)
for c in ['vprincipal','vsemjuros','vparcela','Valor']:
    print("  Σ",c,"= %.0f"%inc[c].sum())
GMV_COL='vparcela' if abs(inc['vparcela'].sum()-93351663)<abs(inc['Valor'].sum()-93351663) else 'Valor'
print("GMV col escolhida:",GMV_COL,"Σ=%.0f"%inc[GMV_COL].sum())

# ---- nível pedido ----
def agg(gr):
    return pd.Series({'prod':gr['prod'].iloc[0],'produto':gr['produto'].iloc[0],'tipo':gr['tipo'].iloc[0],'modal':gr['modal'].iloc[0],
      'score':gr['score'].iloc[0],'risco':gr['risco'].iloc[0],'genero':gr['genero'].iloc[0],'safra':gr['safra'].iloc[0],
      'gmv':gr['vprincipal'].iloc[0],'rec':gr['recebido'].sum(),'ven':gr['vencido'].sum(),'atr':gr['atrasado'].sum(),
      'agu':gr['aguardando'].sum(),'risco_proj':gr['riscototal'].sum(),'np':len(gr)})
ped=inc.groupby('pid').apply(agg,include_groups=False).reset_index()
print("\npedidos agregados:",len(ped),"| GMV Σ %.0f | inad orig %.4f"%(ped['gmv'].sum(),ped['ven'].sum()/(ped['ven'].sum()+ped['rec'].sum())))
def scoreband(s):
    if pd.isna(s): return 'Sem score'
    if s<300: return '000-299'
    if s<500: return '300-499'
    if s<700: return '500-699'
    return '700-999'
ped['band']=ped['score'].apply(scoreband)
# records p/ simulador (score None -> null)
recs=[{'s':(None if pd.isna(r.score) else int(r.score)),'rk':r.risco if isinstance(r.risco,str) else 'N/I','t':r.tipo,'md':(r.modal if isinstance(r.modal,str) else 'N/I'),'g':r.genero if isinstance(r.genero,str) else 'N/I','sf':str(r.safra),
       'prod':(str(r.prod).strip() if isinstance(r.prod,str) and str(r.prod).strip() else 'N/I'),
       'gmv':round(float(r.gmv)),'rec':round(float(r.rec)),'ven':round(float(r.ven)),'atr':round(float(r.atr)),'agu':round(float(r.agu)),'rp':round(float(r.risco_proj))} for r in ped.itertuples()]
AB=float(inc['riscoag'].sum()); AC=float(inc['riscoat'].sum())
print("Risco Aguardando (AB): %.0f | Risco Atrasado (AC): %.0f | AA=AB+AC check: %.0f"%(AB,AC,AB+AC))
out={'n_ped':len(ped),'records':recs,'agu_risco':AB,'atr_risco':AC}
json.dump(out,open('ht_records.json','w',encoding='utf-8'),ensure_ascii=False)
print("ht_records.json salvo:",len(recs),"pedidos")
# validação por band
print("\nPor band (inad original):")
for b in ['000-299','300-499','500-699','700-999','Sem score']:
    s=ped[ped['band']==b]; print("  %s: ped %d gmv %.0f inadO %.4f"%(b,len(s),s['gmv'].sum(),(s['ven'].sum()/(s['ven'].sum()+s['rec'].sum()) if (s['ven'].sum()+s['rec'].sum()) else 0)))
