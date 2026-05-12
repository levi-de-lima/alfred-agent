# CLAUDE.md — Contexto do projeto Alfred (TMB Churn Analyzer)

> Este arquivo é a fonte de verdade para o agente Claude que trabalha neste
> repositório. Descreve **o que o Alfred é**, **como o pipeline funciona de fato**
> e **quais regras precisam ser respeitadas** em qualquer alteração.

---

## 1. Sobre a TMB e o problema

A **TMB** é uma fintech que presta serviços financeiros para infoprodutores
(boleto e Pix parcelado em lançamentos). A operação comercial depende de
duas frentes:

- **Retenção** — manter a carteira ativa, evitar churn e maximizar LTV.
- **Aquisição** — alimentar o funil com novos parceiros via times Closer e Growth.

A análise dessas frentes vivia em planilhas e dashboards (Metabase, HubSpot),
exigindo SQL, conhecimento do schema e tempo. O **Alfred** resolve isso:
é um agente conversacional que recebe perguntas em linguagem natural e
devolve análises de churn, LTV, pipeline e funil — sem que o usuário precise
abrir nenhuma ferramenta.

---

## 2. Arquitetura — fluxo real de uma pergunta

```
Usuário
  │
  ▼  POST /chats/{id}/messages          (ui/app.py · FastAPI)
Orchestrator (agents/orchestrator.py)
  │
  ├─► ContextAgent (Haiku)               classifica intenção · período · identidade
  │
  ├─► [atalho] se is_reformat_request   → ReportAgent reutiliza cache
  │
  ├─► DataAgent                          carrega só o que a intenção exige:
  │     ├── Metabase (fVendas, dProdutores)   se "retention" ∈ areas
  │     └── HubSpot parquet                    se "acquisition" ∈ areas
  │
  ├─► Especialistas (Sonnet · ReAct, até 4 steps)
  │     ├── RetentionAgent      tools de churn/LTV/cohort/transições
  │     └── AcquisitionAgent    tools de Closer/Growth/jornada
  │       (rodam em paralelo via ThreadPoolExecutor se a pergunta for mista)
  │
  ├─► tools.py · analytics_agent._dispatch   executa pandas (`_calc_*`)
  │
  └─► ReportAgent (Sonnet · Haiku p/ greetings)
        formata markdown final em pt-BR
```

Os agentes não compartilham estado entre si — todo contexto trafega pelo
`IntentContract` (saída do ContextAgent) e `AnalyticsContext` (saída do DataAgent).

---

## 3. Agentes — responsabilidades

| Agente | Arquivo | Modelo | Responsabilidade |
|---|---|---|---|
| Orchestrator | `agents/orchestrator.py` | — | Coordena o pipeline; cuida do atalho de reformatação; paraleliza especialistas no caso misto |
| ContextAgent | `agents/context_agent.py` | Haiku | Devolve `IntentContract`: áreas (retention/acquisition/[]), período resolvido, identidade, regras aplicáveis, ambiguidades, flag de reformat |
| DataAgent | `agents/data_agent.py` | — | Carrega só o que a intenção pede. Metabase via `importers/metabase.load_data()`. HubSpot via parquet em `data/hubspot/`. |
| RetentionAgent | `agents/retention_agent.py` | Sonnet | ReAct loop com tools de churn/LTV/cohort. Injeta as `regras_aplicaveis` no system prompt. |
| AcquisitionAgent | `agents/acquisition_agent.py` | Sonnet | ReAct loop com tools de Closer/Growth/jornada cross-data. |
| Analytics Engine | `agents/analytics_agent.py` | — | Funções `_calc_*` em pandas. Recebe `QueryPlan` montado pelos wrappers em `tools.py`. |
| HubSpot Analytics | `agents/hubspot_analytics.py` | — | Cálculos específicos do funil HubSpot, chamados por `_calc_*`. |
| ReportAgent | `agents/report_agent.py` | Sonnet (Haiku p/ greeting) | Formata `AnalyticsResult` em markdown. Para perguntas mistas, combina os dois resultados narrativamente. |

`tools.py` registra **17 tools** (`CLAUDE_TOOLS`), classificadas em áreas
(`TOOL_AREAS`): churn, closer, growth, misto, greeting. `get_claude_tools(areas)`
filtra o subconjunto exposto a cada especialista.

---

## 4. Estado de sessão (server-side)

Mantido em `ui/app.py` (`_session_state`) e propagado a cada chamada do
Orchestrator. Persistência de mensagens em SQLite (`data/chats.db` via
`ui/storage.py`).

| Campo | Para que serve |
|---|---|
| `current_user_gestor` | Resolve "minha carteira", "meu relatório" |
| `awaiting_identity_for` | Alfred está esperando o usuário se identificar; o ContextAgent reexecuta a query pendente assim que o nome chega |
| `last_discussed_gestor` | Resolve pronomes ("dela", "dele", "desse gestor") |
| `analytics_results_cache` | Habilita o atalho de reformatação — pula DataAgent e especialistas |

---

## 5. Dados — schemas reais

### fVendas — construída em `importers/metabase._build_fvendas()`

Não é a tabela bruta do Metabase. É um **grid** gerado em memória:
todos os produtores × todos os meses desde o primeiro registro até o
mês corrente, com `Status` calculado por forward-fill da última venda.

| Coluna | Tipo | Origem |
|---|---|---|
| `Código` | int64 | Card 189 + 194 |
| `Produtor` | str | Card 189 + 194 |
| `Data` | datetime | Início do mês — todos os meses do grid |
| `Valor` | float64 | Card 189 (0 se sem venda no mês) |
| `Status` | str | Calculado: `Ativo / Pré-churn / Churn / Inativo` |
| `Status_Anterior` | str ou NaN | `Status.shift(1)` por produtor — NaN no primeiro registro |

**Cálculo de Status** (no fim de cada mês do grid):

| Condição | Status |
|---|---|
| produtor nunca teve venda | Inativo |
| dias desde última venda ≤ 61 | Ativo |
| 61 < dias ≤ 121 | Pré-churn |
| dias > 121 | Churn |

### dProdutores — construída em `importers/metabase._build_dprodutores()`

| Coluna | Tipo | Origem |
|---|---|---|
| `Código` | int64 | Card 194 |
| `Produtor` | str | Card 194 |
| `Cluster` | str | Card 194, normalizado via `CLUSTER_MAP` |
| `Gestor` | str | Card 194 |
| `Data Parceria` | datetime ou NaT | Reservada (sem fonte hoje — sempre NaT) |
| `Data 1ª Venda` | datetime ou NaT | min(Efetivado em: Dia) por produtor no card 189 |

**Valores válidos de `Cluster` após normalização**:
`PP/P`, `M`, `G`, `GG/EG`, `Desativado`, `S/C`, `Outros`.
Quaisquer outros valores brutos do card 194 caem em `Outros`.

### Dados HubSpot

| Arquivo | Gerado por | Conteúdo |
|---|---|---|
| `data/hubspot/hs_closer_pipeline.parquet` | `importers/hubspot_closer.py` | Pipeline do time Closer (deals, estágios, closers) |
| `data/hubspot/hs_growth_leads.parquet` | `importers/hubspot_growth.py` → `importers/merge_growth_legado.py` | Leads dos pipelines TMB e TMR do time Growth, **unificados com a base legado do Pipedrive** (lida de `data/Base Legado Growth.xlsx`). Coluna `fonte` distingue `hubspot` × `pipedrive`; IDs do legado recebem prefixo `pdv_` para evitar colisão. |
| `data/hubspot/associations_cache.json` | `importers/hubspot_growth.py` | Cache local das associações `lead_id → deal_id_closer` |
| `data/Base Legado Growth.xlsx` | Exportação manual do Pipedrive | Input do merge — atualizar manualmente quando houver nova exportação |

Refresh manual: `python -m importers.refresh`. O orquestrador roda em
sequência (1) Closer importer, (2) Growth importer, (3) merge legado —
o passo 3 sobrescreve `hs_growth_leads.parquet` com a versão unificada,
adicionando a coluna `fonte`. Recomendado semanal, ou antes de análises
de funil. Carregados apenas se `"acquisition" ∈ areas`.

Pipeline detalhado em [HUBSPOT_IMPORT.md](HUBSPOT_IMPORT.md) e
[METABASE_IMPORT.md](METABASE_IMPORT.md).

---

## 6. Fonte de dados e cache

- Metabase: cards **189** (base fVendas) e **194** (dProdutores), baixados
  como CSV via `importers/metabase._fetch_card_csv()`. 3 retries.
- Cache local em `data/metabase/tmb_churn_cache_<timestamp>_{vendas,produtores}.parquet`.
  TTL `CACHE_MAX_AGE_HOURS` (padrão 4h). Mantém os 3 caches mais recentes.
- Hierarquia em `load_data()`:
  1. Cache válido (e sem `force_refresh`) → usa cache
  2. Metabase OK → reconstrói e regrava cache
  3. Metabase falhou + cache expirado existe → usa cache (label `cache (stale fallback)`)
  4. Sem nada → `DataUnavailableError`

---

## 7. Regras de negócio críticas

- **Inativo ≠ Churn.** Inativo nunca vendeu; Churn vendeu e parou.
- **Status é estado de fim de mês.** Máximo uma mudança por produtor por mês.
- **`Status_Anterior` nulo = primeiro registro do produtor**, não dado faltante.
- **Reativação leve vs. plena.** Pré-churn → Ativo é leve; Churn → Ativo é plena.
  São métricas distintas — não somar.
- **Taxa de churn TMB** = `Pré-churn→Churn / base (Ativo + Pré-churn)`.
  Meta: **5%**. **Exclui** produtores gerenciados por **TMB Educação**.
- **Filtro temporal obrigatório.** Quando a pergunta especifica um período,
  filtrar `Data` pelo mês/ano exatos; nunca cair no mês mais recente por padrão.
- **Cluster e Gestor vêm sempre de `dProdutores`** — fazer merge por `Código`
  ou `Produtor` antes de calcular qualquer métrica segmentada.

Estas regras estão codificadas no `ContextAgent` (campo `regras_aplicaveis`)
e injetadas como bullets no system prompt do `RetentionAgent`. Para mudanças
de regra, atualizar `_REGRAS_DESCRICAO` em `retention_agent.py` e o prompt
`CONTEXT_SYSTEM_PROMPT` em `prompts.py`.

---

## 8. Modelos

| Modelo | Variável | Padrão | Uso |
|---|---|---|---|
| Sonnet | `CLAUDE_MODEL` | `claude-sonnet-4-6` | Retention, Acquisition, Report (raciocínio + escrita) |
| Haiku | `CLAUDE_HAIKU_MODEL` | `claude-haiku-4-5-20251001` | ContextAgent, Report em greetings, geração de título de chat |

---

## 9. Padrões de código

- **Pandas** para toda manipulação tabular — `_calc_*` em `analytics_agent.py`.
- **Prompts centralizados** em `prompts.py` — nenhum prompt dentro dos agentes.
- **Tools como wrappers finos** (`w_*` em `tools.py`) — montam `QueryPlan`
  e despacham para `_calc_*`. A camada pandas não conhece o LLM.
- **Importers isolados** em `importers/` — Metabase e HubSpot vivem juntos,
  como módulos do mesmo pacote. Importações via `from importers.metabase import …`.
- **Logs estruturados**: cada agente emite linhas no padrão
  `SESSION=... | AGENT=... | EVENT=... | k=v...` via `settings.logger`.
- **Caminhos via env** (`CACHE_DIR`, `LOG_DIR`, etc.) — nunca hardcodar.
- **Entry point único**: `ui.app:app` (FastAPI). Procfile aponta para ele.

### O que NÃO fazer

- Não retornar `DataFrame` cru ao usuário — sempre sumarizar via `AnalyticsResult`.
- Não inventar status de produtor — sempre derivar de `fVendas`.
- Não criar prompts dentro dos arquivos de agente.
- Não chamar Metabase ou HubSpot direto fora do pacote `importers/`.

---

## 10. Sistema de design — antes de tocar em `ui/`

Antes de qualquer alteração visual em `ui/` (HTML, CSS, novos componentes,
páginas), ler **nesta ordem**:

1. `DESIGN.md` (raiz) — manifesto da identidade visual
2. `design/README.md` — índice e decisões fechadas
3. `design/tokens/design-tokens.json` — fonte da verdade dos tokens
4. `design/tokens/variables.css` — versão CSS pronta

### Regras invioláveis em `ui/`

- Sem hex hardcoded — sempre `var(--token)` de `variables.css`.
- Fontes permitidas: **Inter** (UI), **Cinzel** (apenas o wordmark "Alfred"),
  **JetBrains Mono** (código).
- O logo do header é o SVG do lockup completo — nunca substituir por texto plano.
- Toda nova feature/página deve respeitar paleta, componentes e funcionar
  em **light + dark theme**.

---

## 11. Estrutura de pastas

```
ui/                     FastAPI + frontend (entry point ui.app:app)
agents/                 Um arquivo por agente; tools.py centraliza wrappers
importers/              Extração de dados externos
  metabase.py             Cards 189/194 → fVendas + dProdutores + cache parquet
  hubspot_closer.py       Pipeline de Closer → data/hubspot/hs_closer_pipeline.parquet
  hubspot_growth.py       Funil Growth → data/hubspot/hs_growth_leads.parquet
  merge_growth_legado.py  União HubSpot + Pipedrive Legado, SOBRESCREVE
                          data/hubspot/hs_growth_leads.parquet com coluna `fonte`
  refresh.py              Orquestrador: Closer → Growth → merge legado
data/                   Dados gerados em runtime (gitignored)
  chats.db                SQLite (histórico de chats)
  hubspot/                Snapshots Closer + Growth (já unificado) + associations_cache.json
  metabase/               Cache rotativo de fVendas/dProdutores (TTL configurável)
  Base Legado Growth.xlsx Planilha do Pipedrive legado (input do merge_growth_legado)
design/                 Tokens, logo, regras visuais
logs/                   Logs rotativos (5 MB × 3 backups)
prompts.py              Todos os system prompts
config.py               Settings + logger (lê .env)
CLAUDE.md               Este arquivo
DESIGN.md               Manifesto da identidade visual
HUBSPOT_IMPORT.md       Pipeline detalhado de importação HubSpot
METABASE_IMPORT.md      Pipeline detalhado de importação Metabase
```
