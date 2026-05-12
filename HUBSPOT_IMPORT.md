# HUBSPOT_IMPORT.md — Como funciona a importação HubSpot

> Documenta o pipeline de extração, enriquecimento e normalização dos dados
> de HubSpot que alimentam o `AcquisitionAgent` do Alfred. Estado atual:
> dois importers HubSpot + um merge com base legado do Pipedrive +
> um orquestrador, materializando dois parquets em `data/hubspot/`.

---

## 1. Visão geral

A camada HubSpot no Alfred é **offline e baseada em snapshots**. Em vez de
chamar a API durante o atendimento, o `DataAgent` lê dois arquivos parquet
em `data/`, que são reconstruídos manualmente por scripts standalone:

```
HubSpot API                       Parquets em data/hubspot/         Runtime
─────────────────                 ─────────────────────────          ─────────────────
Pipeline Closer ──► importers/hubspot_closer.py    ──► hs_closer_pipeline.parquet ──┐
                                                                                    │
Objeto Leads    ──► importers/hubspot_growth.py    ─┐                                ├─► DataAgent
                                                    ├─► hs_growth_leads.parquet ─────┘   (se "acquisition" ∈ areas)
data/Base Legado Growth.xlsx ──► importers/merge_growth_legado.py ─┘  (SOBRESCREVE — coluna `fonte`)
   ↑
   └── enriquecimento cross-pipeline no Growth: deal_id_closer via /associations/deals
       + cache em data/hubspot/associations_cache.json
```

O orquestrador `importers/refresh.py` roda os três passos em sequência;
cada um também pode rodar isoladamente via `python -m importers.<modulo>`.
Não há scheduler — o refresh é **manual** (ou agendado externamente:
cron/Task Scheduler/GitHub Action). Recomendado semanal, ou antes de
análises de funil relevantes.

---

## 2. Arquivos e papéis

| Arquivo | Papel |
|---|---|
| `importers/hubspot_closer.py` | Extrai e normaliza o **Pipeline de Closer** (deals). Saída: `data/hubspot/hs_closer_pipeline.parquet`. |
| `importers/hubspot_growth.py` | Extrai e normaliza o **funil de Growth** (leads TMB + TMR). Saída: `data/hubspot/hs_growth_leads.parquet`. Também enriquece cada lead com o `deal_id_closer` associado. |
| `importers/merge_growth_legado.py` | **Une o parquet do Growth com a base legado do Pipedrive** (`data/Base Legado Growth.xlsx`) e **sobrescreve** `data/hubspot/hs_growth_leads.parquet`. Acrescenta a coluna `fonte` (`hubspot` × `pipedrive`). Detalhe completo em §6. |
| `importers/refresh.py` | Orquestrador. Roda Closer → Growth → merge legado em sequência, em subprocessos. |
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
mapeia para Ganho hoje — ver §9.1.

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

## 6. Merge com base legado do Pipedrive — `importers/merge_growth_legado.py`

Etapa final do pipeline HubSpot: roda **depois** do `hubspot_growth` e
sobrescreve o mesmo parquet, acrescentando os leads vindos da exportação
manual do Pipedrive antigo. Resultado: um único arquivo
`data/hubspot/hs_growth_leads.parquet` com a coluna `fonte`
(`hubspot` | `pipedrive`).

### 6.1. Fontes

| Origem | Caminho | Observação |
|---|---|---|
| HubSpot (saída do importer Growth) | `data/hubspot/hs_growth_leads.parquet` | Lido pelo merge logo após o Growth importer terminar |
| Pipedrive legado (Excel) | `data/Base Legado Growth.xlsx` | Exportação manual do Pipedrive antigo. Esquema é o nome das colunas no UI do Pipedrive (em pt-BR, com prefixo `Negócio - …`, `Pessoa - …`). |
| Saída (sobrescreve) | `data/hubspot/hs_growth_leads.parquet` | Concat dos dois DataFrames, com coluna `fonte` distinguindo cada registro. É o mesmo path lido pelo `DataAgent` — nenhuma mudança no agente. |

### 6.2. Mapeamento Pipedrive → HubSpot

`COLUMN_MAP` traduz **22 colunas** do schema Pipedrive (UI) para o schema do
parquet de Growth do HubSpot. Resumo dos grupos:

| Bloco | Pipedrive | HubSpot |
|---|---|---|
| Identificação | `Negócio - Título`, `Pessoa - E-mail - Trabalho`, `Pessoa - Telefone - Trabalho` | `nome`, `email`, `telefone` |
| Pipeline | `Negócio - Funil`, `Negócio - Etapa`, `Negócio - Proprietário` | `pipeline_nome`, `stage_atual_nome`, `proprietario` |
| Datas | `Negócio - Negócio criado em`, `Negócio - Atualizado em`, `Negócio - Negócio fechado em`, `Negócio - Data de perda` | `dt_criacao`, `dt_ultima_atualizacao`, `dt_fechamento_tmb`, `dt_desqualificado` |
| Qualificação | `Negócio - Motivo da perda`, `Negócio - Score Lead`, `Negócio - Classificação ICP`, `Cluster` | `motivo_desqualificacao`, `score_total_lp`, `cluster_leadscore`, `cluster_faturamento` |
| Respostas form | `vende cursos…`, `área de atuação`, `faturamento último ano`, `tempo implementação` | `vende_info`, `area_atuacao`, `faturamento_ultimo_ano`, `tempo_implementacao` |
| UTM | `UTM Source/Campaing/Medium/Content/Term` | `utm_source`/`utm_campaign`/`utm_medium`/`utm_content`/`utm_term` |

`EXTRA_COLUMNS` mantém **5 colunas adicionais** do Pipedrive (sem
contraparte no schema HubSpot atual): `modelo_vendas`, `objetivo_parceria_tmb`,
`estrutura_operacional`, `experiencia_boleto`, `onde_vende`. Elas
aparecem no parquet unificado como colunas próprias (nulas para as
linhas vindas do HubSpot).

### 6.3. Campos derivados na linha Pipedrive

| Campo | Regra |
|---|---|
| `status_lead` | `Negócio - Status` mapeado via `STATUS_MAP` (`Ganho`→`Ganho`, `Perdido`→`Perda`, demais → `Aberto`) |
| `lead_id` | `"pdv_" + Negócio Pipe - ID` (string). Prefixo evita colisão com IDs numéricos do HubSpot. Fallback para `"pdv_" + index` se a coluna ID estiver ausente. |
| `dt_novo_lead` | Cópia direta de `dt_criacao` (no Pipedrive não existe stage equivalente a "Novo Lead" do TMB com timeline própria) |
| `dt_extracao` | `pd.Timestamp.now()` no momento do merge |
| `fonte` | Constante `"pipedrive"` |

Conversão de datas via `_to_dt()` em modo permissivo (`errors="coerce"`,
`utc=False`).

### 6.4. Fluxo de execução

```
main()
  │
  ├─► pd.read_parquet("data/hubspot/hs_growth_leads.parquet")
  │     ├── idempotência: se já tiver coluna `fonte`, dropa linhas
  │     │   onde fonte == "pipedrive" (legado de run anterior)
  │     └── hs["fonte"] = "hubspot"
  │
  ├─► pd.read_excel("data/Base Legado Growth.xlsx")
  │
  ├─► mapear_pipedrive(pdv_raw)
  │     ├── rename COLUMN_MAP + EXTRA_COLUMNS
  │     ├── deriva status_lead, lead_id, dt_novo_lead, dt_extracao, fonte
  │     └── tipa datas com _to_dt
  │
  ├─► corte temporal (PDV_CUTOFF_DATE)
  │     └── descarta linhas com dt_criacao >= cutoff (default 2026-03-10)
  │
  ├─► pd.concat([hs, pdv], ignore_index=True)
  │
  └─► result.to_parquet("data/hubspot/hs_growth_leads.parquet", index=False)
        # SOBRESCREVE o parquet do importer Growth
```

A concatenação é por união simples de schemas — pandas faz outer join
de colunas automaticamente, então quem existe só em um lado vira NaN no
outro.

**Corte temporal (`PDV_CUTOFF_DATE`).** Constante no topo do script.
Linhas do Pipedrive com `dt_criacao >= cutoff` são descartadas para
evitar dupla-contagem com o HubSpot — durante a transição, os dois
funis conviveram por algumas semanas e formulários ainda escreviam no
Pipedrive. Após o corte, **qualquer duplicata por email entre as duas
fontes representa duas ENTRADAS distintas no funil em momentos
diferentes** — uma oportunidade comercial em cada época. Não é erro de
dados; é o comportamento esperado.

Valores aceitos:

| Valor | Comportamento |
|---|---|
| `"auto"` (default) | Usa `hs["dt_criacao"].min()` — o timestamp **exato** do primeiro lead HubSpot. Preserva leads do Pipedrive criados no mesmo dia que o HubSpot arrancou, antes do primeiro registro HS. |
| `"YYYY-MM-DD"` ou `"YYYY-MM-DD HH:MM:SS"` | Cutoff fixo. Atenção: data sem hora vira `00:00:00` — leads do Pipedrive entre meia-noite e o primeiro HS daquele dia serão cortados (foi o que aconteceu na primeira versão com `"2026-03-10"`: 27 leads únicos perdidos). |
| `None` | Desativa o filtro. Não recomendado em produção. |

**Idempotência.** Rodar o merge duas vezes em sequência sem rerodar o
importer Growth não duplica o legado: o passo de filtro
`fonte == "pipedrive"` no início descarta os Pipedrives da execução
anterior antes de concatenar de novo.

### 6.5. Schema do parquet unificado

O parquet final contém **todas** as colunas do schema HubSpot (§5.5)
acrescidas das colunas exclusivas do Pipedrive (`EXTRA_COLUMNS`) e da
coluna `fonte` (`hubspot` | `pipedrive`). Linhas vindas do HubSpot ficam
com NaN nos campos exclusivos do Pipedrive e vice-versa.

### 6.6. Decisão de design e pendências

Adotada a **Opção A**: o merge sobrescreve `data/hubspot/hs_growth_leads.parquet`
diretamente. Vantagem: **zero mudança no `DataAgent` e nas tools**. O preço
é que o "HubSpot puro" deixa de existir em disco entre execuções — sempre
que o `refresh` roda, o parquet final já é a versão unificada. Para
recuperar o "puro" basta rodar `python -m importers.hubspot_growth`
isoladamente sem chamar o merge em seguida.

Pendências conhecidas que **não** foram resolvidas com a Opção A:

| Pendência | Impacto | Onde mexer (quando decidir resolver) |
|---|---|---|
| Sem deduplicação entre HubSpot e Pipedrive | Lead migrado pode contar 2x em métricas de funil/conversão | Definir regra com o time de Growth (provavelmente match por `email` normalizado + janela temporal de criação) e aplicar no fim do `mapear_pipedrive` ou após o `pd.concat` |
| System prompt do `AcquisitionAgent` (`prompts.py`, bloco `hs_growth_leads`) ainda descreve só o schema HubSpot | LLM não sabe que existe `fonte`, e não sabe filtrar por origem. Para perguntas tipo "apenas leads HubSpot" o agente pode trazer tudo. | `prompts.py:430+` — adicionar a coluna `fonte` no bloco de schema e uma instrução "filtre `fonte == 'hubspot'` quando o usuário pedir o funil sem o legado" |
| `importers/__init__.py` não reexporta o merge | Cosmético — o script é sempre chamado via `python -m …`, mas inconsistente com o padrão dos outros importers | `importers/__init__.py` |

---

## 7. Caches

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

## 8. Orquestrador — `importers/refresh.py`

Script enxuto que roda os três passos em **subprocessos** isolados,
propagando `HUBSPOT_TOKEN` via env e respeitando a ordem (o merge precisa
do parquet do Growth já gerado):

```python
run("importers.hubspot_closer")          # 1. Closer pipeline
run("importers.hubspot_growth")          # 2. Growth (HubSpot puro)
run("importers.merge_growth_legado")     # 3. Une com Pipedrive legado e sobrescreve o parquet do Growth
```

Cada `run()` captura stdout/stderr e levanta `RuntimeError` se o
returncode for não-zero. Logs vão para stderr via `logging.basicConfig`,
não para o `settings.logger` do projeto.

Uso típico:

```bash
python -m importers.refresh                  # pipeline completo (Closer + Growth + merge)
python -m importers.hubspot_closer           # só Closer
python -m importers.hubspot_growth           # só Growth (HubSpot puro; NÃO inclui legado)
python -m importers.merge_growth_legado      # só merge (precisa do parquet do Growth atualizado)
```

> Para obter "HubSpot puro" sem o legado, rode `hubspot_growth`
> isoladamente. Qualquer execução do `refresh` completo termina com o
> parquet já unificado.

---

## 9. Considerações operacionais

### 9.1. Pontos de atenção

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

### 9.2. Quando rodar o refresh

| Cenário | Recomendação |
|---|---|
| Operação normal | Semanal (segunda de manhã) |
| Análise de funil pontual | Antes da análise, se o último refresh foi há mais de 3 dias |
| Mudança de stage no HubSpot | Imediato — e atualizar `STAGE_NAMES` / `STAGES_TMB` / `STAGES_TMR` no código |
| Reorganização de pipelines | Imediato — e atualizar `PIPELINE_ID` / `PIPELINES` |

### 9.3. Diagnóstico rápido

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

## 10. Onde os dados entram no pipeline Alfred

Após o refresh, os parquets vivem em `data/`:

```
data/
├── hubspot/
│   ├── hs_closer_pipeline.parquet      (lido pelo DataAgent quando "acquisition" ∈ areas)
│   ├── hs_growth_leads.parquet         (UNIFICADO: HubSpot + Pipedrive legado; coluna `fonte` distingue)
│   └── associations_cache.json         (cache do enrich Growth, não consumido em runtime)
└── Base Legado Growth.xlsx             (input do merge_growth_legado — exportação manual do Pipedrive)
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
