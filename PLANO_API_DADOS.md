# Plano — API de Dados do Comercial TMB

> Extração da camada de dados/analítica para um serviço próprio, dono único do
> parquet, consumido por Alfred, Data Hub e futuros clientes. Baseado no estado
> real de `AI_Churn/` e `Data_Hub/` em jun/2026.

---

## 1. Estado-alvo

```
                ┌────────────────────────────────────┐
                │            tmb-data-api              │  ← DONA do dado
                │  ingestão → derivação → regra →      │
                │  parquet (lar único) → API/OpenAPI   │
                └───────────────────┬──────────────────┘
                                    │  HTTP (contrato OpenAPI)
            ┌───────────────────────┼───────────────────────┐
            │                       │                        │
        Alfred (AI_Churn)      Data Hub (site)          BI / outros
   agentes decidem qual      explorer + dashboards    (consomem datasets)
   tool/endpoint chamar      consomem a API
```

A API vira **dona da ingestão e do parquet**. Alfred e Data Hub viram
**consumidores**. É exatamente a promoção da camada de dados a serviço — só que
hospedada num projeto novo, não evoluindo o Data Hub no lugar.

---

## 2. Princípio inegociável

**Um parquet, um dono.** O dado tem um endereço físico só; todo projeto
referencia por config/env, ninguém duplica. A única cópia legítima é uma
**sandbox de dev descartável** (snapshot congelado) enquanto a API é construída.
Cópia como lar permanente = drift garantido.

---

## 3. Onde estamos hoje (mapa real)

| Peça | Vive em | Observação |
|---|---|---|
| Parquets (fvendas, dprodutores, closer, growth) | `Data_Hub/metabase/` e `Data_Hub/hubspot/` | **Já é fonte única.** `AI_Churn/config.py:21` aponta `DATA_HUB = parent/Data_Hub` e `cache_dir = DATA_HUB/metabase`. Sem duplicação hoje. |
| Código dos importers | `AI_Churn/importers/` (metabase, hubspot_closer, hubspot_growth, merge_growth_legado, refresh) | Roda no Alfred mas **grava no Data Hub**. Posse torta. |
| Derivação (grid fVendas, Status, cluster norm) | dentro de `AI_Churn/importers/metabase.py` (`_build_fvendas`, `_build_dprodutores`) | Lógica que dá sentido ao dado. |
| Analítica (31 funções `_calc_*`) | `AI_Churn/agents/analytics_agent.py` (3513 linhas) + `hubspot_analytics.py` (808) | O cérebro. Hoje 100% no Alfred. |
| Regras de cálculo (Inativo≠Churn, taxa, exclui TMB Educação) | `context_agent` (`regras_aplicaveis`) + `retention_agent` (`_REGRAS_DESCRICAO`) | Misturadas com regra de *conversa*. |
| Tools (17) + wrappers `w_*` + `QueryPlan` | `AI_Churn/agents/tools.py` | Glue LLM↔pandas. |
| API de leitura | `Data_Hub/ui/app.py` (FastAPI :8001, DuckDB) | Hoje só query/browse **genérica** + 1 dashboard de churn. Não expõe a analítica semântica. |

Conclusão: parquet já centralizado; **a lógica que dá sentido a ele ainda está no Alfred**. Esse é o gap real.

---

## 4. Esqueleto do novo projeto

`tmb-data-api/` (nome placeholder)

```
tmb-data-api/
├── core/                        # biblioteca pura, sem FastAPI, sem LLM
│   ├── ingestion/               ← AI_Churn/importers/{metabase,hubspot_closer,
│   │                              hubspot_growth,merge_growth_legado,refresh}.py
│   ├── derivation/              ← isolar de metabase.py: _build_fvendas,
│   │                              _build_dprodutores, Status, CLUSTER_MAP
│   ├── analytics/               ← AI_Churn/agents/analytics_agent.py (_calc_*)
│   │                              + hubspot_analytics.py
│   ├── rules.py                 ← SÓ a parte de CÁLCULO das regras hoje em
│   │                              context_agent/retention_agent
│   └── models.py                ← AnalyticsResult e tipos de domínio (não-LLM)
├── api/
│   ├── main.py                  # FastAPI — OpenAPI/Swagger = contrato/doc
│   ├── routers/
│   │   ├── analytics.py         # endpoints semânticos (substituem os w_*)
│   │   └── datasets.py          # bulk pull pra explorer/BI
│   └── schemas/                 # Pydantic req/resp (substituem params do QueryPlan)
├── data/                        # LAR ÚNICO dos parquets (após cutover)
│   ├── metabase/                ← de Data_Hub/metabase/
│   └── hubspot/                 ← de Data_Hub/hubspot/ + Base Legado Growth.xlsx
├── config.py
├── tests/                       # paridade de número (ver Fase 1)
├── requirements.txt
└── README.md
```

Regra de ouro do layout: `core/` é **biblioteca importável**. `api/` é uma casca
fina por cima. Assim o Alfred pode importar o core in-process (sem latência) OU
falar HTTP — você escolhe no deploy.

---

## 5. O que fica em cada projeto depois

**Alfred (`AI_Churn/`) — fica só o agente:**
- `orchestrator`, `context_agent` (parsing de intenção), agentes especialistas
  (ReAct), `report_agent`, `prompts.py`, `ui/`, `storage.py`.
- Lógica de **sessão/identidade** (`current_user_gestor`, "minha carteira",
  `awaiting_identity_for`) — a API é stateless; Alfred resolve "minha carteira" →
  nome do gestor e passa como parâmetro.
- `agents/tools.py` vira **wrappers HTTP finos** (ou gerados do OpenAPI). O
  `QueryPlan` encolhe/some.
- As 2 tools de conversa (`saudacao`, `pedir_identidade`) **ficam** — não são
  dados, não viram endpoint.
- `config.py`: remove `DATA_HUB`/`cache_dir`, adiciona `DATA_API_URL`.

**Data Hub — vira consumidor puro:**
- `explorer.html` + dashboards passam a ler da API.
- Perde os parquets locais e a dependência do importer.
- Alimenta o DuckDB via **bulk pull** dos datasets (não query linha-a-linha).
- `metadata.yml` segue como dicionário, mas a fonte de verdade do schema passa a
  ser os schemas Pydantic da API.
- `chats.db` **não é dado comercial** — sai do Data Hub, volta a ser do Alfred.

---

## 6. Contrato — a "lista fechada de tools"

Os 15 tools de dados viram endpoints versionados (`/v1/`). Os 2 de conversa não.
O OpenAPI gerado pelo FastAPI **é** a documentação — e dá pra gerar os schemas de
tool do Alfred a partir dele, mantendo tudo sincronizado.

**Retenção / churn** (hoje área `churn`):
`status-distribuicao`, `taxa-churn`, `transicoes`, `produtores`, `faturamento`,
`ltv`, `cohort`, `resumo-churn`, `churn-streak`

**Aquisição** (hoje closer/growth):
`pipeline-closer`, `funil-crescimento`, `detalhe-deal`, `track-lead`,
`track-produtor-funil`, `cohort-closer-churn`

**Datasets crus** (pra explorer/BI, bulk pull):
`/v1/datasets/fvendas`, `/dprodutores`, `/closer`, `/growth`

Cada endpoint: 1 modelo Pydantic de entrada + 1 de saída = 1 doc = 1 tool schema.
É isso que troca a bagunça de hoje (schema + wrapper + `_calc_` mantidos à mão em
3 lugares) por um contrato único.

---

## 7. Sequência de cutover

> Regra que rege tudo: **nunca dois escritores do parquet ao mesmo tempo.** A
> troca de posse do dado é um switch único, não um dual-write gradual.

**Fase 0 — Sandbox (não muda nada em produção).**
Copia os parquets pra `tmb-data-api/data/` como snapshot congelado. Desenvolve a
API contra ele. Alfred e Data Hub seguem intactos. (Os números do snapshot
envelhecem — normal; serve pra desenhar o *contrato*, não pra validar métrica.)

**Fase 1 — Extrair o `core/` como biblioteca + teste de paridade.**
Move `_calc_*`, importers, derivação e regras pro `core/`. Escreve testes de
regressão: mesmo input → **mesmo número** que o Alfred produz hoje. Crítico,
porque a regra é sutil (taxa = Pré-Churn→Churn / base Ativo+Pré-Churn, exclui TMB
Educação; Inativo≠Churn). Sem isso, a extração pode mudar métrica calado.

**Fase 2 — Subir a API (read-only sobre o snapshot).**
FastAPI no ar, valida contrato/OpenAPI, gera os schemas de tool do Alfred a
partir do spec.

**Fase 3 — Alfred consome a API (em paralelo ao caminho antigo).**
`agents/tools.py` vira wrappers HTTP; `data_agent` chama a API; greeting fica
local. Roda atrás de feature flag comparando resultado novo × antigo.

**Fase 4 — Transferir posse do dado (o switch único).**
1. Para o refresh antigo (importers do AI_Churn que gravam no Data_Hub).
2. Importers passam a rodar **só** na API, gravando em `tmb-data-api/data/`.
3. Apaga os parquets do `Data_Hub/`.
4. Data Hub passa a fazer bulk pull da API.

**Fase 5 — Limpeza.**
Remove importers e analítica do `AI_Churn`; remove `DATA_HUB` do `config.py`;
desacopla `chats.db` de volta pro Alfred.

---

## 8. Armadilhas

- **Path hardcoded:** `AI_Churn/config.py:21` fixa o caminho do Data_Hub. Quebra
  na Fase 4 — trocar por `DATA_API_URL`.
- **Dual-write:** dois importers escrevendo o mesmo parquet = corrupção/drift.
  Switch único na Fase 4.
- **Latência:** cada tool call vira round-trip HTTP no loop ReAct (até 4 steps ×
  2 agentes). Mitigar: `core/` importável + co-deploy do Alfred, ou aceitar o
  custo se o serviço for separado.
- **Paridade de número:** o teste da Fase 1 é o que garante que a migração não
  alterou nenhuma métrica. Não pular.
- **Explorer precisa bulk pull:** servir dataset inteiro, não JSON linha-a-linha,
  senão o DuckDB do Data Hub fica lento.
- **`chats.db`** não é dado comercial — não deveria viver no Data Hub nem na API.

---

## 9. Decisões em aberto

- Nome do projeto (`tmb-data-api`?).
- **REST vs MCP:** se os consumidores externos forem majoritariamente agentes
  LLM, expor o mesmo `core/` como MCP server (ou os dois). Decisão de casca, não
  de core.
- **Co-deploy vs serviço separado** do Alfred (decide se há latência HTTP no loop).
- Onde roda o agendamento do refresh depois que os importers mudam de casa.
