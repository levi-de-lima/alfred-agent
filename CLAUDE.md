# CLAUDE.md — Contexto do Projeto TMB Churn Analyzer

## Sobre a TMB
Fintech que presta serviços financeiros para infoprodutores.
Produto principal: suporte de cobrança (boleto e pix parcelado) em lançamentos.

## Sistema de design

Antes de fazer qualquer alteração visual em `ui/` (HTML, CSS, novos componentes,
novas páginas/features), LER nesta ordem:

1. `DESIGN.md` (raiz) — manifesto da identidade visual, paleta, tipografia, componentes
2. `design/README.md` — índice e decisões fechadas
3. `design/tokens/design-tokens.json` — fonte da verdade dos tokens
4. `design/tokens/variables.css` — versão CSS pronta

**Regras invioláveis ao tocar em `ui/`:**
- Não usar valores hex hardcoded — sempre `var(--token)` de `variables.css`
- Não usar fontes fora de Inter (UI), Cinzel (só wordmark do logo), JetBrains Mono (código)
- Não substituir o logo do header por texto plano "Alfred" — usar SVG do lockup
- Toda nova feature/página deve respeitar a paleta e os componentes existentes
- Light + dark theme devem funcionar para qualquer coisa nova

## Estrutura de dados

### fVendas (tabela de vendas)
| Coluna | Tipo | Descrição |
|---|---|---|
| Código | int | ID da venda |
| Produtor | str | Nome completo |
| Data | date | Granularidade mensal |
| Status | str | Ativo / Pré-churn / Churn / Inativo |
| Status_Anterior | str | Status do mês anterior |
| Valor | float | Valor total vendido pelo produtor naquele mês |

### dProdutores (tabela de produtores)
| Coluna | Tipo | Descrição |
|---|---|---|
| Código | int | ID do produtor |
| Produtor | str | Nome completo |
| Cluster | str | Classificação por tamanho |
| Gestor | str | Gestor de contas TMB |
| Data Parceria | date | Entrada na TMB |
| Data 1ª Venda | date | Primeira venda realizada |

## Regras de negócio críticas
- Status é calculado com base na data de última venda da tabela diária
- Um produtor pode mudar de Ativo para Pré-churn sem aparecer em fVendas naquele mês
- Inativo ≠ Churn: Inativo nunca vendeu, Churn vendeu mas parou
- Sempre filtrar pelo mês mais recente disponível como "estado atual"

## Padrões de código
- Sempre usar pandas para manipulação de dados
- Funções de leitura do Metabase isoladas em data_loader.py (fallback: parquet em cache/)
- Agentes definidos em agents/ com um arquivo por agente
- Logs de execução em cada chamada de agente
- Entry point: ui/app.py (FastAPI, POST /chat)
- Prompts de todos os agentes centralizados em prompts.py

## O que NÃO fazer
- Não hardcodar caminhos de arquivo — usar variáveis de ambiente
- Não retornar dados brutos ao usuário — sempre sumarizar
- Não fazer suposições sobre status — sempre calcular a partir dos dados

## Regras críticas de consulta temporal

- A tabela fVendas tem uma linha por produtor por mês. Isso significa que
  o histórico completo já está na tabela — não existe necessidade de dados externos
  para consultas de períodos passados.
- Quando o usuário perguntar sobre um mês específico, SEMPRE filtrar
  vendas["Data"] pelo mês e ano exatos antes de qualquer cálculo.
- NUNCA responder com o mês mais recente quando o usuário especificou outro período.
- A coluna Status do Excel é apenas referência — o status real de um produtor
  em um dado mês é o valor da coluna Status na linha correspondente àquele mês.
- Para cruzar com dProdutores, sempre fazer merge em Produtor ou Código
  antes de calcular métricas. Cluster e Gestor vêm sempre de dProdutores.

## Arquitetura de agentes

Fluxo de uma pergunta: Orchestrator → Context Agent → Data Agent → Retention ou Acquisition Agent → Analytics Engine → Report Agent → usuário.

| Agente | Arquivo | Responsabilidade |
|---|---|---|
| Orchestrator | agents/orchestrator.py | Coordena o pipeline completo |
| Context Agent | agents/context_agent.py | Classifica intenção (churn/closer/growth/saudação), extrai período, resolve usuário |
| Data Agent | agents/data_agent.py | Carrega fVendas e dProdutores do Metabase (ou cache parquet) |
| Retention Agent | agents/retention_agent.py | ReAct loop — análise de churn, LTV, cohort |
| Acquisition Agent | agents/acquisition_agent.py | ReAct loop — Closer pipeline e Growth funil |
| Analytics Engine | agents/analytics_agent.py | Funções `_calc_*` em pandas; despacha para tools.py |
| HubSpot Analytics | agents/hubspot_analytics.py | Cálculos específicos dos dados HubSpot |
| Report Agent | agents/report_agent.py | Formata resposta final em markdown via Claude |

Tools disponíveis para os agentes ReAct ficam em agents/tools.py (registradas em `CLAUDE_TOOLS` e `TOOL_AREAS`).

## Modelos

- **Sonnet** (`CLAUDE_MODEL`, padrão `claude-sonnet-4-6`): análise principal — Retention, Acquisition, Report Agent
- **Haiku** (`CLAUDE_HAIKU_MODEL`, padrão `claude-haiku-4-5-20251001`): classificação de intenção no Context Agent (mais rápido e barato)

## Dados HubSpot

Fonte secundária de dados, usada pelo Acquisition Agent para análise de funil.

| Arquivo | Gerado por | Conteúdo |
|---|---|---|
| data/hs_closer_pipeline.parquet | hubspot_importer.py | Pipeline do time Closer |
| data/hs_growth_leads.parquet | hubspot_growth_importer.py | Leads do time Growth |

Refresh manual: `python refresh_hubspot.py`. Recomendado semanalmente ou antes de análises de funil.