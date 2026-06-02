<div align="center">

# Alfred

**O analista de dados do comercial da TMB.**

Pergunte em linguagem natural sobre churn, pipeline, leads ou receita —
o Alfred entende, busca os dados certos e devolve a análise pronta.

<img src="images/interface-white.png" alt="Alfred — tela inicial (light)" width="100%" />

</div>

---

## Por que o Alfred existe

A **TMB** é uma fintech de serviços financeiros para infoprodutores (boleto e
Pix parcelado em lançamentos). A operação comercial roda em duas frentes:

- **Retenção** — manter a carteira ativa, controlar churn, acompanhar LTV.
- **Aquisição** — alimentar o funil com novos parceiros via Closer e Growth.

A análise dessas frentes vivia em **Metabase, HubSpot e planilhas**. Cada
pergunta exigia abrir três ferramentas, escrever SQL, cruzar exportações.
O time perdia horas para chegar a respostas que cabem em um parágrafo.

O Alfred substitui esse trabalho por uma conversa. Você pergunta:

> *"Como está a taxa de churn dos últimos 6 meses?"*
> *"Quem entrou em pré-churn em abril na carteira da Rafaela?"*
> *"Dos produtores que fecharam pelo Closer no Q1, quantos churnaram?"*

E recebe uma resposta direta em markdown — com a metodologia TMB,
o filtro temporal correto e a regra de negócio aplicada.

---

## O que o Alfred faz

| Frente | Perguntas que resolve |
|---|---|
| **Churn / Retenção** | Taxa de churn por gestor · transições de status · resumo do mês · streak de gestores acima da meta · valor em risco |
| **LTV e ciclos de vida** | LTV por cluster/gestor · reativações · ciclos de vida · cohorts de primeira venda |
| **Pipeline Closer (HubSpot)** | KPIs do pipeline · ciclo mediano · performance por closer · motivos de perda · detalhe de um deal |
| **Funil Growth (HubSpot)** | Total de leads · MQL/SQL · taxas de conversão · tempo por estágio |
| **Jornada cross-data** | Linha do tempo Growth → Closer → cliente TMB → churn de um produtor específico |

Para a lista completa de tools registradas, ver `agents/tools.py`.

---

## Arquitetura em uma imagem

```mermaid
flowchart LR
    User([👤 Usuário])

    User --> API[FastAPI<br/>POST /chats/&#123;id&#125;/messages]
    API --> Ctx[ContextAgent<br/><i>Haiku</i>]
    Ctx --> Data[DataAgent]

    Data --> Specialists{áreas?}
    Specialists -->|retention| Ret[RetentionAgent<br/><i>Sonnet · ReAct</i>]
    Specialists -->|acquisition| Acq[AcquisitionAgent<br/><i>Sonnet · ReAct</i>]

    Ret --> Report[ReportAgent<br/><i>Sonnet</i>]
    Acq --> Report
    Report --> User

    Data -.lê.-> Sources[(Metabase · HubSpot<br/>+ cache parquet)]
```

**Quatro estágios:**

1. **Classificar** — o ContextAgent (Haiku) lê a pergunta e devolve
   um `IntentContract`: quais áreas (retention/acquisition/ambas),
   período resolvido, identidade do gestor, regras de negócio aplicáveis.
2. **Carregar** — o DataAgent traz apenas o que a intenção exige.
   Metabase para retention, parquet do HubSpot para acquisition.
3. **Analisar** — Retention e/ou Acquisition rodam um loop ReAct (Sonnet)
   chamando tools que executam pandas via `analytics_agent`. Para perguntas
   mistas, os dois rodam em paralelo.
4. **Formatar** — o ReportAgent transforma o `AnalyticsResult` em markdown
   em pt-BR, com tabelas e narrativa.

Para o detalhamento de cada agente, regras de status, modelos e estado de
sessão, ver [CLAUDE.md](CLAUDE.md).

---

## Interface

<div align="center">

| Light | Dark |
|---|---|
| <img src="images/interface-white.png" alt="Interface light" width="100%" /> | <img src="images/interface-dark.png" alt="Interface dark" width="100%" /> |

</div>

O frontend é uma SPA em HTML + CSS + JS vanilla servida pelo FastAPI.
Tem histórico de conversas (sidebar com busca), troca de tema light/dark e
título de chat gerado automaticamente pelo Haiku. Identidade visual em
`DESIGN.md` e `design/`.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | FastAPI + Uvicorn |
| LLMs | Anthropic Claude — **Sonnet 4.x** (análise) + **Haiku 4.5** (classificação) |
| Dados de retenção | Metabase API (cards 189 e 194) → pandas |
| Dados de aquisição | HubSpot API → parquet local |
| Cache | Parquet em `data/metabase/` com TTL configurável (padrão 4h) |
| Persistência de chats | SQLite (`data/chats.db`) |
| Frontend | HTML/CSS/JS vanilla, tokens em `design/` |

---

## Estrutura de arquivos

```
.
├── ui/
│   ├── app.py                  FastAPI · entry point (uvicorn ui.app:app)
│   ├── index.html              Frontend (SPA single-file)
│   ├── storage.py              SQLite — histórico de chats
│   └── static/                 Favicon, logo, assets
│
├── agents/
│   ├── orchestrator.py         Pipeline principal
│   ├── context_agent.py        Classificação de intenção (Haiku)
│   ├── data_agent.py           Empacotamento de DataFrames
│   ├── retention_agent.py      ReAct — churn, LTV, cohort
│   ├── acquisition_agent.py    ReAct — Closer, Growth, jornada
│   ├── analytics_agent.py      Engine pandas (_calc_* functions)
│   ├── hubspot_analytics.py    Cálculos específicos do HubSpot
│   ├── tools.py                Wrappers de tools (CLAUDE_TOOLS, TOOL_AREAS)
│   └── report_agent.py         Formatação markdown final
│
├── importers/                  Extração de dados externos
│   ├── metabase.py             Cards 189/194 → fVendas + dProdutores + cache parquet
│   ├── hubspot_closer.py       Pipeline de Closer → data/hubspot/hs_closer_pipeline.parquet
│   ├── hubspot_growth.py       Funil de Growth (TMB+TMR) → data/hubspot/hs_growth_leads.parquet
│   ├── merge_growth_legado.py  União HubSpot + Pipedrive Legado — SOBRESCREVE
│   │                           data/hubspot/hs_growth_leads.parquet com coluna `fonte`
│   └── refresh.py              Orquestrador: Closer → Growth → merge legado
│
├── data/                       Dados gerados em runtime (gitignored)
│   ├── chats.db                SQLite — histórico de chats
│   ├── hubspot/                Snapshots dos importers HubSpot (Growth já unificado com legado)
│   ├── metabase/               Cache rotativo de fVendas/dProdutores
│   └── Base Legado Growth.xlsx Exportação do Pipedrive legado (input do merge)
│
├── design/                     Tokens, logo, regras visuais
├── logs/                       Logs rotativos
├── images/                     Screenshots usados neste README
│
├── prompts.py                  System prompts de todos os agentes
├── config.py                   Settings + logger
├── CLAUDE.md                   Contexto completo para o agente Claude
├── DESIGN.md                   Manifesto da identidade visual
├── HUBSPOT_IMPORT.md           Pipeline detalhado de importação HubSpot
├── METABASE_IMPORT.md          Pipeline detalhado de importação Metabase
├── Procfile                    Deploy (Heroku/Railway)
└── requirements.txt
```

---

## Rodando localmente

### 1. Variáveis de ambiente

Copie `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Chave da API Anthropic |
| `METABASE_URL` | ✅ | URL da instância Metabase |
| `METABASE_USER` | ✅ | E-mail de login no Metabase |
| `METABASE_PASSWORD` | ✅ | Senha do Metabase |
| `HUBSPOT_TOKEN` | — | Token HubSpot (só para `importers/refresh.py`) |
| `CLAUDE_MODEL` | — | Modelo de análise (padrão `claude-sonnet-4-6`) |
| `CLAUDE_HAIKU_MODEL` | — | Modelo de classificação (padrão `claude-haiku-4-5-20251001`) |
| `CACHE_DIR` | — | Pasta do cache parquet (padrão `./data/metabase`) |
| `CACHE_MAX_AGE_HOURS` | — | TTL do cache em horas (padrão `4`) |
| `LOG_DIR` | — | Pasta de logs (padrão `./logs`) |
| `LOG_LEVEL` | — | Nível de log (padrão `INFO`) |

### 2. Instalar dependências

```bash
pip install -r requirements.txt
```

### 3. Subir o servidor

```bash
uvicorn ui.app:app --reload --port 8000
```

Acesse [http://localhost:8000](http://localhost:8000).

---

## Refresh dos dados HubSpot

Os arquivos `data/hubspot/hs_closer_pipeline.parquet` e
`data/hubspot/hs_growth_leads.parquet` são gerados offline e lidos pela
aplicação como cache estático.

```bash
python -m importers.refresh              # pipeline completo: Closer + Growth + merge legado
python -m importers.hubspot_closer       # só Closer
python -m importers.hubspot_growth       # só Growth (HubSpot puro, sem legado)
python -m importers.merge_growth_legado  # só o merge (precisa do Growth atualizado)
```

Recomendado semanalmente (ou antes de análises de funil relevantes).
Os dados do Metabase são atualizados automaticamente pelo
`importers/metabase.py` respeitando o TTL do cache.

> O `refresh` termina sempre com o parquet de Growth **já unificado** —
> o passo `merge_growth_legado` sobrescreve `hs_growth_leads.parquet`
> acrescentando os leads do Pipedrive antigo e a coluna `fonte`
> (`hubspot` | `pipedrive`). Para obter o "HubSpot puro", rode
> `hubspot_growth` isoladamente. Detalhes em HUBSPOT_IMPORT.md §6.

Pipeline detalhado em [HUBSPOT_IMPORT.md](HUBSPOT_IMPORT.md) e
[METABASE_IMPORT.md](METABASE_IMPORT.md).

---

## Dados em uma linha

- **fVendas** — grid completo `produtor × mês` com `Status` calculado
  (Ativo ≤ 61 dias · Pré-Churn 61–121 · Churn > 121 · Inativo nunca vendeu).
- **dProdutores** — dimensão de produtores: `Código`, `Produtor`, `Cluster`
  (PP/P, M, G, GG/EG, Desativado, S/C), `Gestor`, `Data 1ª Venda`.
- **HubSpot** — `data/hubspot/hs_closer_pipeline.parquet` e
  `data/hubspot/hs_growth_leads.parquet`.

Schema completo, regras de status, exclusões da taxa de churn e
considerações temporais em [CLAUDE.md](CLAUDE.md).

---

## Deploy

```
# Procfile (Heroku / Railway)
web: uvicorn ui.app:app --host 0.0.0.0 --port $PORT
```

O `importers/metabase.py` tem fallback para cache parquet local quando o
Metabase está indisponível, então a aplicação continua respondendo (com
label `cache (stale fallback)` nas citações) mesmo durante incidentes da
fonte.
