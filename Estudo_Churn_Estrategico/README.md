# Estudo de Churn — Visão Estratégica (por bloco, cluster, safra, gestor e motivo)

> Estudo fundamentado nos dados reais do **Metabase** (via Data_Hub) cruzados com os
> **motivos de churn do HubSpot** (pipeline de CS). Entregável principal: um dashboard
> HTML **filtrável e self-contained** (sem API nem dado sensível embutido).
> Gerado em 07/07/2026. Dado de referência: **jun/2026**.

## Conteúdo da pasta

```
Estudo_Churn_Estrategico/
├── Dashboard_Churn_Estrategico.html   ← ABRA ESTE (dashboard interativo, dark, marca TMB)
├── README.md                          ← este arquivo
├── dados/
│   ├── dash_data.json                 agregados + 3.656 registros anonimizados (o que o HTML consome)
│   ├── gestor_base_p5.json            última página dos 922 códigos sob gestores (pull HubSpot)
│   ├── gestor_churn_p2.json           2ª página dos deals de churn dos gestores
│   └── logos_b64.json                 logos oficiais TMB em base64 (embutidos no HTML)
└── scripts/
    ├── mega.py                        pipeline principal: fVendas/dProdutores + HubSpot → dash_data.json
    ├── consolida.py                   tabela por gestor (churn, cobertura, top motivos)
    ├── agg_churn.py                   agrega os 360 deals com motivo e cruza com o Status do Metabase
    └── emit_data.py                   versão inicial do emissor de dados
```

## Definições e regras (fonte da verdade)

- **Churn** = Status do produtor no `fVendas` do Metabase: **> 121 dias sem vender**
  (a régua canônica do projeto AI Churn / Alfred). Validada contra o `status_calculado`
  do HubSpot (6+ meses) — concordam **~100%** no nível do produtor.
- **Dois blocos** (pela pipeline de **CS `842108729`** no HubSpot):
  - **Sem gestor** = owner **TMB Educação** (id `88240459`) — base coletiva.
  - **Com gestor** = owners nomeados (10 gestores, base 922).
- **Chave de ligação** HubSpot ↔ Metabase: `codigo_produtor` ↔ `Código` (casou 360/360 nos documentados).
- **Cluster** = tier por **faturamento de pico em 12 meses** (Energium ≤100k · Palladium ≤1M ·
  Titanium ≤5M · Rhodium 5M+). Usa-se o **pico** e não o estrito últimos-12m porque um
  churnado tem faturamento recente ≈ 0 — o pico representa o tier de capacidade perdido.
- **GMV em risco** = faturamento de pico 12m dos churnados (proxy de run-rate anual recuperável).
- **Motivo de churn** = campo `motivos_de_churn` (multisseleção) do card *Informações de
  Relacionamento* no HubSpot. Só **7,6%** do churn tem causa documentada hoje.

## Principais achados

| | Sem gestor (TMB Educação) | Com gestor |
|---|---|---|
| Produtores em churn | 3.434 | 222 |
| **Taxa de churn** | **76,3%** | **24,1%** |
| Cobertura de motivo | 4,2% | 62,2% |
| GMV anual em risco | ~R$ 329 mi | ~R$ 106 mi |

- **GMV total em risco: ~R$ 435,6 mi.** Concentração em **Rhodium** (5M+): 11 produtores = ~R$ 182 mi.
- **Causa raiz nº 1 = Financeiro/Crédito** (Inadimplência Alta lidera), confirmando a hipótese
  do time de Produto. Base coletiva é dominada por inadimplência; gestores mostram mix mais
  acionável (sem retorno, janela de lançamento, fluxo de caixa, antecipação).
- **Onde há gestor, documenta-se ~15× mais** — a maior parte do churn coletivo é invisível.

## v4 — seção High Ticket (Motor de Risco) + simulador de recuperação segmentável

- **Nova seção à parte "🎯 High Ticket — Motor de Risco"** (não interfere no estudo de churn nem no simulador de recuperação). Link no card Motor de Risco leva até ela (`#ht-top`).
- **Estudo** reproduz 1:1 a planilha `Inadimplencia_HighTicket_TMB_v11` (base `vwm_parcelas_base`, ticket ≥ R$15k, efetivadas 12m): composição da carteira, 3 métodos de inadimplência (Original 23,1% / **Financeira 28,3% headline** / Projetada 45,5%), inadimplência por **Score** (000-299 = 41,9% vs 700-999 = 6,6%), por Risco, por Parcela, coorte Safra×Parcela, quebras e top produtores em risco. Scripts: `ht_dump.py`, `ht_extract.py` (nível-pedido, validado contra os totais oficiais), `ht_study.py` → `dados/ht_data.json` (2.399 pedidos + tabelas).
- **Simulador Motor de Risco por score:** cenários Agressivo / Moderado (≤200) / Conservador (≤600) + corte livre 0–999 + toggle sem-score + filtros (tipo/risco/safra). Saídas: inadimplência antes→depois (3 métodos), GMV bloqueado (venda perdida), inadimplência evitada, recebido perdido, trade-off "R$1 barrado ⇄ R$X evitado" e narrativa de apresentação ao produtor. Ex.: ≤200 bloqueia 301 pedidos (13% do GMV) e reduz a financeira ~2,8 p.p.; ≤600 bloqueia 67% do GMV.
- **Simulador de recuperação (bloco de churn)** agora respeita os filtros de cluster/safra/gestor/motivo.

## v3 — pós-revisão adversarial (3 agentes, `scripts/revisao_adversarial.json`)

Corrigido: filtro de **Status** (Churn/Pré-Churn/Ambos) destrava os 496 pré-churn; **gráficos navegáveis** (clique no mês do status → quem entrou em churn; donut e motivos → gaveta); **coerência financeira** (perda = receita + operação + antecipação, mesma base do simulador); rótulos **"/ano" só em Pico 12m e Últimos 12m** (Total histórico = estoque, não anualizável); filtros que não quebram mais; nomes de motivo completos; cores CVD-safe (clusters em rampa azul, Relacionamento em magenta); lente aplicada na tabela de gestores; valores cheios (R$) na gaveta; caveat de dupla contagem receita×antecipação. Validado em runtime (jsdom, 0 erros).

## v2 — o que mudou (base coletiva completa + drill-down)

- **Base coletiva completa puxada** (4.498 códigos sob Gestor TMB) → série temporal por bloco destravada.
- **Evolução de status estilo BI**: Ativo/Pré-Churn/Churn empilhado, histórico, por bloco (nº e 100%).
- **Recuperação mensal** (reativações Churn/Pré→Ativo): plena vs leve, com/sem gestor, com tendência 6m.
- **Drill-down por produtor**: clique em qualquer gráfico/KPI → gaveta com tabela (código interno, bloco, gestor, cluster, safra, status, GMV total, GMV 12m, run-rate, projeção de perda/ano, última venda, motivos). Sem nomes/CNPJ.
- **3 lentes de GMV** selecionáveis: Total histórico (R$512,5mi) · Últimos 12m (R$47,6mi) · Pico 12m/run-rate (R$435,6mi).
- **Financeiro explícito**: "já vendeu X · X nos últimos 12m · projeção de perda Y (20% take)" + **simulador de recuperação** (recupera X% do bloco com/sem gestor → receita devolvida).
- **"TMB Educação" → "Gestor TMB"** em todo o painel. Filtros redesenhados (segmentos + chips + gaveta).

### Definição de GMV (resposta ao pedido do Gabriel)
Todo GMV vem do **Metabase (card 189, "Soma de Valor Principal")**, somado **por produtor via Código**. "GMV em risco" default = **pico 12m** (run-rate anual de capacidade), pois um churnado tem faturamento recente ≈ 0. ⚠️ **A confirmar com o Levi**: se o card 189 já desconta cancelamentos (hoje tratado como Valor Principal efetivado, bruto). Projeção de perda = GMV da lente × take de receita (20%).

## Como regenerar os dados

Os scripts leem os parquets do Data_Hub e os pulls do HubSpot já salvos em `dados/`.
O `mega.py` é o pipeline principal (requer `pandas`, `pyarrow`). Para atualizar os
motivos/base do HubSpot, refazer os pulls do pipeline CS `842108729` (ver comentários nos scripts).

> ⚠️ Estudo pontual (snapshot jun/2026). Não é um pipeline vivo — os números envelhecem.
> Módulo financeiro do dashboard usa **premissas editáveis** (receita 20%, R$1/boleto,
> antecipação 8,5%, adesão 30%) — ticket médio e adesão são estimativas a calibrar com Produto.
