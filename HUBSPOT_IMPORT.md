# HUBSPOT_IMPORT.md — Como funciona a importação HubSpot

> Documenta o pipeline de extração, enriquecimento e normalização dos dados
> de HubSpot que alimentam o `AcquisitionAgent` do Alfred. Estado atual:
> dois importers offline + um orquestrador, materializando dois parquets
> em `data/`.

---

## 1. Visão geral

A camada HubSpot no Alfred é **offline e baseada em snapshots**. Em vez de
chamar a API durante o atendimento, o `DataAgent` lê dois arquivos parquet
em `data/`, que são reconstruídos manualmente por scripts standalone:

```
HubSpot API                       Parquets em data/                Runtime
─────────────────                 ─────────────────────────         ─────────────────
Pipeline Closer ──► importers/hubspot_closer.py    ──► hs_closer_pipeline.parquet ─┐
                                                                                ├──► DataAgent
Objeto Leads    ──► importers/hubspot_growth.py    ──► hs_growth_leads.parquet    ─┘   (se "acquisition" ∈ areas)
   ↑
   └── enriquecimento cross-pipeline: deal_id_closer via /associations/deals
       + cache em data/hubspot/associations_cache.json
```

Os dois importers podem rodar isoladamente ou em sequência via
`importers/refresh.py`. Não há scheduler — o refresh é **manual** (ou
agendado externamente: cron/Task Scheduler/GitHub Action). Recomendado
semanal, ou antes de análises de funil relevantes.

---

## 2. Arquivos e papéis

| Arquivo | Papel |
|---|---|
| `importers/hubspot_closer.py` | Extrai e normaliza o **Pipeline de Closer** (deals). Saída: `data/hubspot/hs_closer_pipeline.parquet`. |
| `importers/hubspot_growth.py` | Extrai e normaliza o **funil de Growth** (leads TMB + TMR). Saída: `data/hubspot/hs_growth_leads.parquet`. Também enriquece cada lead com o `deal_id_closer` associado. |
| `importers/refresh.py` | Orquestrador. Roda os dois importers em sequência, em subprocessos. |
| `data/hubspot/associations_cache.json` | Cache local das associações `lead_id → deal_id_closer` (acelera reexecuções do Growth importer). |

A função pública de cada importer é importável diretamente:

```python
from importers.hubspot_closer import fetch_closer_pipeline
from importers.hubspot_growth import fetch_growth_leads
```

Mas hoje o consumo é **via parquet** — o `DataAgent` faz `pd.read_parquet()`,
não chama essas funções no runtime.

---

## 3. Configuração

Ambos os importers compartilham as mesmas variáveis e padrões:

| Variável | Origem | Uso |
|---|---|---|
| `HUBSPOT_TOKEN` | `.env` (via `os.getenv`) | Bearer token enviado em todas as chamadas |
| Base URL | Hardcoded `https://api.hubapi.com` | — |
| Timeout HTTP | 30s (leads/deals) · 15s (associações) · 10s (owners) | — |
| Paginação | 100 por página (limite máximo da API HubSpot) | — |

**Falha de autenticação não tem retry automático** — se o token estiver
inválido, o `requests.raise_for_status()` derruba o processo. Erros 5xx
da HubSpot também propagam, sem retry.

---

## 4. Importer Closer — `importers/hubspot_closer.py`

### 4.1. Fonte

- Endpoint: `POST /crm/v3/objects/deals/search`
- Filtro fixo: `pipeline = 832504973` (Pipeline de Closer)
- Propriedades solicitadas: **~45 campos** definidos em `PROPERTIES`,
  cobrindo identificação, resultado, timeline de estágios, dados de reunião,
  atividade de prospecção, enriquecimento e respostas de qualificação.

### 4.2. Estágios do pipeline

Mapeamento `dealstage_id → nome` em `STAGE_NAMES`:

| ID | Nome |
|---|---|
| 1235306848 | Novo Qualificado |
| 1235306849 | Cadência |
| 1235306850 | Interações |
| 1235306851 | Agendamento |
| 1235306852 | Reunião |
| 1235306853 | Aguardando Cadastro |
| 1235306854 | Ganho |
| 1235226499 | Perda |

Para cada estágio existe uma propriedade `hs_v2_date_entered_<id>`
materializada em coluna `dt_<estágio>` no parquet.

### 4.3. Fluxo de execução

```
fetch_closer_pipeline()
  │
  ├─► fetch_all_deals()
  │     └── loop POST /deals/search · paginação por "after" cursor
  │         (imprime "[HubSpot] Página N: M deals" a cada batch)
  │
  ├─► normalize_deals(raw)
  │     ├── extrai cada propriedade tratando "", None e "None" como nulo
  │     ├── tipa: _to_dt (UTC→naive), _to_date (normalize), _to_float, _to_int
  │     ├── derivadas:
  │     │     ganho                          (1 se dealstage_id == Ganho)
  │     │     reuniao_realizada              (1 se dt_reuniao não-nulo)
  │     │     dealstage_nome                 (mapeado de STAGE_NAMES)
  │     │     mes_ano                        (período YYYY-MM da criação)
  │     │     dias_qualificado_ate_reuniao
  │     │     dias_reuniao_ate_ganho
  │     │     tempo_total_ciclo_dias         (ganho ou perda - criação)
  │     │     dt_extracao                    (timestamp da execução)
  │     └── reordena colunas por grupo lógico
  │
  └─► loga: total · reuniões · ganhos · perdas
```

### 4.4. Schema final do parquet

Colunas agrupadas por bloco lógico (ordem preservada no parquet):

| Grupo | Colunas |
|---|---|
| **Identificação** | `deal_id`, `codigo_produtor`, `dealname`, `gestor_contas`, `cluster`, `closer` |
| **Resultado** | `dealstage_id`, `dealstage_nome`, `ganho`, `motivo_de_perda`, `classificacao_venda`, `amount` |
| **Timeline** | `dt_criacao`, `dt_novo_qualificado`, `dt_cadencia`, `dt_interacoes`, `dt_agendamento`, `dt_reuniao`, `dt_aguardando_cadastro`, `dt_ganho`, `dt_perda`, `dt_fechamento` |
| **Reunião** | `reuniao_realizada`, `data_reuniao_agendada`, `canal_reuniao`, `noshow`, `dias_qualificado_ate_reuniao`, `dias_reuniao_ate_ganho`, `tempo_total_ciclo_dias` |
| **Prospecção** | `dt_primeiro_contato`, `dt_1a_conexao`, `canal_1a_conexao`, `qtd_dias_atividade`, `qtd_contatos_por_dia`, `contacte_rate`, `taxa_conexao`, `taxa_agendamento`, `taxa_fechamento` |
| **Qualificação** | `modelo_vendas`, `objetivo_parceria_tmb`, `estrutura_operacional`, `experiencia_boleto`, `onde_vende` |
| **Metadados** | `telefone`, `lead_score`, `mes_ano`, `dt_extracao` |

`reuniao_realizada` é derivado de `dt_reuniao.notna()` — depende do
estágio "Reunião" ter sido pisado em algum momento.

---

## 5. Importer Growth — `importers/hubspot_growth.py`

### 5.1. Fontes

- **Endpoint primário**: `GET /crm/v3/objects/leads` (objeto Leads do
  HubSpot, sem filtro de pipeline — retorna todos os leads do portal).
- **Endpoint de enriquecimento**: `GET /crm/v3/objects/leads/{lead_id}/associations/deals`
  seguido de `GET /crm/v3/objects/deals/{deal_id}?properties=pipeline,createdate`
  para identificar qual deal associado pertence ao Pipeline de Closer.
- **Endpoint de owners**: `GET /crm/v3/owners/{owner_id}` para resolver
  nome do `hubspot_owner_id`.

### 5.2. Pipelines e estágios

Dois pipelines são tratados juntos, distinguidos por `hs_pipeline`:

**Leads TMB** (`lead-pipeline-id`):

| ID | Nome |
|---|---|
| 1307449126 | Novo Lead |
| new_stage_id_1318266061 | Backlog - Leadscore |
| attempting_stage_id_745667965 | Ativado |
| connected_stage_id_2058487257 | Interagiu |
| 1270709937 | Agendado |
| qualified_stage_id_233247981 | Qualificado |
| unqualified_stage_id_1675714327 | Desqualificado |

**Leads TMR** (`leads-tmr-pipeline`):

| ID | Nome |
|---|---|
| 1242729229 | Novo |
| 1242729230 | Tentativa |
| 1242729231 | Conectado |
| 1242729232 | Qualificado |
| 1242729233 | Desqualificado |

`status_lead` é derivado: estágio Qualificado de TMB → `Ganho`;
estágios Desqualificado de TMB ou TMR → `Perda`; qualquer outro →
`Aberto`. **Atenção:** o `Qualificado` de TMR (`1242729232`) **não**
mapeia para Ganho hoje — ver §8.1.

### 5.3. Propriedades

~50 campos em `PROPERTIES`, divididos em:

- Identificação (`hs_lead_name`, `email`, `phone`, `hs_createdate`, …)
- Respostas do formulário de qualificação (vende info, área de atuação,
  faturamento, tempo de implementação)
- LeadScore (5 componentes + total + final + cluster)
- Timeline TMB (7 datas `hs_v2_date_entered_*`)
- Timeline TMR (5 datas `hs_v2_date_entered_*`)
- Tempo acumulado (`hs_v2_cumulative_time_in_*`) para alguns estágios
- UTMs (`utm_source_last_hr`, `utm_campaign_last_hr`, …)
- Flags (`lead_ativado_por_ia`, `hs_lead_is_disqualified`,
  `hs_lead_associated_deals_count`, `hs_lead_closed_won_deals_amount`)

### 5.4. Fluxo de execução

```
fetch_growth_leads()
  │
  ├─► fetch_all_leads()
  │     └── loop GET /leads?limit=100&properties=… · paginação por "after"
  │
  ├─► enrich_with_closer_deals(leads, partial_output_path=OUTPUT_PATH)
  │     │
  │     ├── carrega cache JSON (lead_id → deal_id_closer)
  │     │
  │     ├── para cada lead:
  │     │     se cache hit → reusa
  │     │     senão       → get_closer_deal_id(lead_id):
  │     │                     1. GET /leads/{id}/associations/deals
  │     │                     2. para cada deal_id retornado, GET /deals/{id}
  │     │                     3. filtra pelos que pertencem ao Pipeline de Closer
  │     │                     4. retorna o mais recente (max createdate)
  │     │                   time.sleep(0.05)  # rate limit manual
  │     │
  │     └── a cada 50 leads:
  │           - regrava o cache em data/hubspot/associations_cache.json
  │           - salva parquet parcial em data/hubspot/hs_growth_leads.parquet
  │             (proteção contra falha no meio do enriquecimento)
  │
  ├─► normalize_leads(raw)
  │     ├── extrai propriedades com tratamento de nulos
  │     ├── resolve hubspot_owner_id → nome via /crm/v3/owners/{id}
  │     │     (cache em memória _owner_cache, não persistido em disco)
  │     ├── derivadas:
  │     │     pipeline_nome    (Leads TMB / Leads TMR / Desconhecido)
  │     │     stage_atual_nome (mapeado de ALL_STAGES)
  │     │     status_lead      (Ganho / Perda / Aberto via _calc_status)
  │     │     dias_novo_ate_ativado, dias_ativado_ate_interagiu,
  │     │     dias_interagiu_ate_agendado, dias_novo_ate_qualificado,
  │     │     dias_novo_ate_desqualificado
  │     │     dt_extracao
  │     └── reordena colunas por grupo
  │
  └─► loga: total · TMB vs TMR · ganhos/perdas · % com deal_id_closer
```

### 5.5. Schema final do parquet

| Grupo | Colunas |
|---|---|
| **Identificação** | `lead_id`, `nome`, `email`, `telefone`, `contact_id`, `deal_id_closer`, `pipeline_id`, `pipeline_nome`, `stage_atual_id`, `stage_atual_nome`, `status_lead`, `proprietario`, `dt_criacao`, `dt_ultima_atualizacao`, `dt_fechamento_tmb` |
| **Qualificação / LeadScore** | `vende_info`, `area_atuacao`, `faturamento_ultimo_ano`, `tempo_implementacao`, `score_vende_info`, `score_area_atuacao`, `score_faturamento_ano`, `score_tempo_implantacao`, `score_total_lp`, `cluster_leadscore`, `cluster_faturamento`, `motivo_desqualificacao` |
| **Timeline TMB** | `dt_novo_lead`, `dt_backlog_leadscore`, `dt_ativado`, `dt_interagiu`, `dt_agendado`, `dt_qualificado`, `dt_desqualificado` |
| **Timeline TMR** | `dt_tmr_novo`, `dt_tmr_tentativa`, `dt_tmr_conectado`, `dt_tmr_qualificado`, `dt_tmr_desqualificado` |
| **Tempo derivado** | `dias_novo_ate_ativado`, `dias_ativado_ate_interagiu`, `dias_interagiu_ate_agendado`, `dias_novo_ate_qualificado`, `dias_novo_ate_desqualificado`, `tempo_em_ativado_ms`, `tempo_em_interagiu_ms` |
| **UTMs** | `utm_source`, `utm_campaign`, `utm_medium`, `utm_content`, `utm_term` |
| **Metadados** | `lead_ativado_por_ia`, `criacao_manual_closer`, `qtd_deals_associados`, `valor_deals_ganhos`, `dt_extracao` |

### 5.6. LeadScore — referência de thresholds

Não é aplicado no importer (apenas referência documental):

| Cluster | Faixa |
|---|---|
| A | ≥ 202 |
| B | 153 – 201.99 |
| C | 0 – 152.99 |
| D | (sem range) |

O cluster real é lido direto da propriedade `cluster_leadscore` que vem
da HubSpot, sem recálculo local.

---

## 6. Caches

| Cache | Local | Escopo | Quando é invalidado |
|---|---|---|---|
| **Associações lead→deal** | `data/hubspot/associations_cache.json` | Disco, persistente entre execuções | Nunca automaticamente — apagar manualmente para forçar re-fetch |
| **Owners** | `_owner_cache` (dict global em `importers/hubspot_growth.py`) | Memória do processo | Termina junto com o processo |

O cache de associações é o que torna re-runs viáveis em produção: a
primeira execução faz N+1 chamadas (1 lista + 1 detalhe por deal
associado) para cada lead, mas runs subsequentes só pagam o custo da
API para **leads novos**.

⚠️ **Cuidado:** o cache não distingue "deal não existe" de "deal não
buscado ainda" — se um lead estiver no cache com `None`, **nunca** será
reconsultado, mesmo que um deal Closer tenha sido criado depois. Para
re-enriquecer, apague o JSON ou edite-o manualmente.

---

## 7. Orquestrador — `importers/refresh.py`

Script enxuto que roda os dois importers em **subprocessos** isolados,
propagando `HUBSPOT_TOKEN` via env:

```python
run("hubspot_importer.py")
run("hubspot_growth_importer.py")
```

Cada `run()` captura stdout/stderr e levanta `RuntimeError` se o
returncode for não-zero. Logs vão para stderr via `logging.basicConfig`,
não para o `settings.logger` do projeto.

Uso típico:

```bash
python -m importers.refresh            # roda os dois
python -m importers.hubspot_closer           # só Closer
python -m importers.hubspot_growth    # só Growth
```

---

## 8. Considerações operacionais

### 8.1. Pontos de atenção

- **Mapeamento Qualificado TMR.** `_calc_status` só trata
  `qualified_stage_id_233247981` (TMB) como Ganho. O `1242729232`
  (Qualificado de TMR) cai em `Aberto`. Confirmar com o time de Growth
  se esse é o comportamento esperado.
- **Cache nunca expira.** `hs_closer_associations_cache.json` não tem
  TTL. Se associações forem alteradas no HubSpot (lead movido entre
  deals, deal recriado), o cache fica desatualizado silenciosamente.
- **Owner cache é volátil.** Cada execução do importer Growth refaz
  ~N chamadas a `/owners/{id}` (uma por owner único). Em portals com
  poucos owners isso é trivial, mas o ideal seria persistir em disco
  igual ao cache de associações.
- **Rate limit manual.** Apenas o enriquecimento Growth tem
  `time.sleep(0.05)` entre chamadas. Os endpoints de search/list não
  têm sleep — confiam no limit padrão da HubSpot (100 req/10s no plano
  Pro/Enterprise). Se o token for de plano com cota menor, vai
  estourar 429.
- **Sem retry em 5xx.** Qualquer falha intermitente da HubSpot derruba
  o importer inteiro. O parquet parcial salvo a cada 50 leads no
  Growth atenua isso parcialmente — mas não há retomada automática.

### 8.2. Quando rodar o refresh

| Cenário | Recomendação |
|---|---|
| Operação normal | Semanal (segunda de manhã) |
| Análise de funil pontual | Antes da análise, se o último refresh foi há mais de 3 dias |
| Mudança de stage no HubSpot | Imediato — e atualizar `STAGE_NAMES` / `STAGES_TMB` / `STAGES_TMR` no código |
| Reorganização de pipelines | Imediato — e atualizar `PIPELINE_ID` / `PIPELINES` |

### 8.3. Diagnóstico rápido

Sintomas comuns e onde olhar:

| Sintoma | Suspeita |
|---|---|
| `HTTPError 401` | `HUBSPOT_TOKEN` ausente ou expirado |
| `HTTPError 403` | Token sem escopo de leads/deals/owners |
| `HTTPError 429` | Estourou rate limit — aumentar `time.sleep` ou aguardar |
| `stage_atual_nome == "Desconhecido"` em muitos leads | Stage novo no HubSpot que não está em `ALL_STAGES` |
| `deal_id_closer` nulo em todos os leads | Cache corrompido OU `PIPELINE_CLOSER_ID` mudou |
| Datas inconsistentes | Conferir `_to_dt` — converte UTC para naive (local-agnostic) |

---

## 9. Onde os dados entram no pipeline Alfred

Após o refresh, os parquets vivem em `data/`:

```
data/
├── hs_closer_pipeline.parquet
├── hs_growth_leads.parquet
└── hs_closer_associations_cache.json   (cache do enrich, não consumido em runtime)
```

O `agents/data_agent.py` carrega esses dois parquets **apenas quando**
o `ContextAgent` classifica a pergunta como tendo área `"acquisition"`.
Para perguntas só de retenção, os arquivos sequer são abertos.

Os DataFrames `hs_closer` e `hs_growth` chegam até as tools via
`ToolContext` em `agents/tools.py`. Quem efetivamente os consome:

| Tool | Lê |
|---|---|
| `pipeline_closer` | `hs_closer` |
| `detalhe_deal` | `hs_closer` |
| `funil_crescimento` | `hs_growth` |
| `track_lead_ate_deal` | `hs_closer` + `hs_growth` (via `deal_id_closer`) |
| `track_produtor_funil` | `hs_closer` + `hs_growth` + `fVendas` |
| `cohort_closer_churn` | `hs_closer` + `fVendas` |

A lógica de cálculo está em `agents/hubspot_analytics.py`, chamada
pelos `_calc_*` correspondentes em `agents/analytics_agent.py`.
