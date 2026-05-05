# CLAUDE.md — Contexto do Projeto TMB Churn Analyzer

## Sobre a TMB
Fintech que presta serviços financeiros para infoprodutores.
Produto principal: suporte de cobrança (boleto e pix parcelado) em lançamentos.

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
- Funções de leitura do SharePoint isoladas em data_loader.py
- Agentes definidos em agents/ com um arquivo por agente
- Logs de execução em cada chamada de agente
- Tratar erros de conexão com SharePoint com fallback para cache local

## O que NÃO fazer
- Não hardcodar caminhos de arquivo — usar variáveis de ambiente
- Não retornar dados brutos ao usuário — sempre sumarizar
- Não fazer suposições sobre status — sempre calcular a partir dos dados

## Regras críticas de consulta temporal" com o seguinte conteúdo:

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