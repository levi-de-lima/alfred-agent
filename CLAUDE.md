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
| DataAgent | `agents/data_agent.py` | — | Carrega só o que a intenção pede. Metabase via `data_loader.load_data()`. HubSpot via parquet em `data/`. |
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

### fVendas — construída em `data_loader._build_fvendas()`

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

### dProdutores — construída em `data_loader._build_dprodutores()`

| Co