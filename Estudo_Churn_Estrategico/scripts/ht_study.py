# -*- coding: utf-8 -*-
import openpyxl, json, sys
sys.stdout.reconfigure(encoding="utf-8")
F=r"ht_src.xlsx"
wb=openpyxl.load_workbook(F, read_only=True, data_only=True)
def rows(name): return list(wb[name].iter_rows(values_only=True))
def f(x):
    try: return round(float(x),6)
    except: return None
def brk(name):
    rr=rows(name); hi=next(i for i,r in enumerate(rr) if r and str(r[0]).strip()=='Rank')
    out=[]
    for r in rr[hi+1:]:
        if r[1] is None: continue
        out.append({'cat':str(r[1]).strip(),'ped':f(r[2]),'gmv':f(r[8]),'rec':f(r[9]),'ven':f(r[10]),'atr':f(r[11]),'agu':f(r[12]),'risco':f(r[13]),'io':f(r[14]),'if':f(r[15]),'ip':f(r[16]),'farol':(str(r[17]).strip() if r[17] else None)})
    return out
# Por Parcela
def parcela():
    rr=rows('📉 Por Parcela'); hi=next(i for i,r in enumerate(rr) if r and str(r[0]).strip()=='Parcela')
    out=[]
    for r in rr[hi+1:]:
        if r[0] is None or str(r[0]).strip()=='TOTAL': continue
        out.append({'p':f(r[0]),'qtd':f(r[1]),'pagas':f(r[2]),'ven':f(r[3]),'atr':f(r[4]),'agu':f(r[5]),'io':f(r[9]),'if':f(r[10]),'param':f(r[11])})
    return out
# Coorte
def coorte():
    rr=rows('🧬 Coorte Safra x Parcela'); hi=next(i for i,r in enumerate(rr) if r and 'Safra' in str(r[0]))
    hdr=[c for c in rr[hi][1:] if c is not None]
    out=[]
    for r in rr[hi+1:]:
        if r[0] is None: continue
        out.append({'safra':str(r[0]).strip(),'vals':[f(c) for c in r[1:1+len(hdr)]]})
    return {'parcelas':[str(h) for h in hdr],'linhas':out}
# Projeção top produtores
def proj():
    rr=rows('🔮 Projeção'); hi=next((i for i,r in enumerate(rr) if r and str(r[0]).strip()=='Produtor'),None)
    out=[]
    if hi is not None:
        for r in rr[hi+1:]:
            if r[0] is None: continue
            out.append({'prod':str(r[0]).strip(),'ven':f(r[1]),'io':f(r[2]),'agu_risco':f(r[3]),'atr_risco':f(r[4]),'ip':f(r[5])})
    return out
study={
 'ref':'2026-07','ticket_min':15000,'periodo':'efetivadas últimos 12m',
 'totais':{'ped':2399,'parc':26729,'gmv':93351663,'rec':28974435,'ven':8717346,'atr':2721937,'agu':38764140,'risco':15444801,'io':0.2313,'if':0.2831,'ip':0.4547},
 'por_score':brk('🎓 Por Score'),'por_risco':brk('⚠️ Por Risco'),'por_tipo':brk('🎯 Por Tipo Produto'),
 'por_genero':brk('🚻 Por Gênero'),'por_safra':brk('📅 Por Safra'),'por_produtor':brk('🏆 Por Produtor')[:15],
 'por_parcela':parcela(),'coorte':coorte(),'proj_top':proj(),
}
wb.close()
sfr=sorted([r['cat'] for r in study['por_safra'] if r['cat']!='TOTAL'])
study['periodo']={'de':sfr[0],'ate':sfr[-1],'snapshot':'2026-07-07'}
recs=json.load(open('ht_records.json',encoding='utf-8'))
study['records']=recs['records']; study['n_ped']=recs['n_ped']
study['totais']['agu_risco']=recs.get('agu_risco'); study['totais']['atr_risco']=recs.get('atr_risco')
study['totais']['agu_saudavel']=study['totais']['agu']-(recs.get('agu_risco') or 0)
json.dump(study,open('ht_data.json','w',encoding='utf-8'),ensure_ascii=False)
import os
print("ht_data.json:",os.path.getsize('ht_data.json'),"bytes | pedidos:",study['n_ped'])
print("por_score cats:",[r['cat'] for r in study['por_score']])
print("por_risco cats:",[r['cat'] for r in study['por_risco']])
print("por_safra n:",len(study['por_safra']),"| parcela n:",len(study['por_parcela']),"| coorte linhas:",len(study['coorte']['linhas']),"| proj_top:",len(study['proj_top']))
