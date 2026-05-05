# PRD — Sistema Multiagente de Análise de Churn TMB

## Objetivo
Interface de chat que permite ao usuário perguntar sobre churn de infoprodutores
da TMB e receber respostas baseadas em dados reais e atualizados do SharePoint.

## Contexto de negócio
A TMB presta serviços financeiros (boleto/pix parcelado) para infoprodutores.
O controle de churn é feito via duas tabelas Excel no SharePoint:
- fVendas: granularidade mensal, com status por produtor (Ativo, Pré-churn, Churn, Inativo)
- dProdutores: cadastro com cluster, gestor, data parceria, data 1ª venda

## Definição de status
- Ativo: venda nos últimos 60 dias
- Pré-churn: sem venda há mais de 60 dias
- Churn: sem venda há mais de 120 dias
- Inativo: cadastrado, nunca vendeu

## Arquitetura alvo
Sistema multiagente com 3 subagentes orquestrados:
1. Agente de Dados: lê e normaliza os dados do SharePoint
2. Agente Analítico: executa análises e cálculos de KPIs
3. Agente de Relatório: formata a resposta para o usuário

## Interface
Chat web simples (HTML/JS ou Streamlit) com input de texto e área de resposta.

## Exemplos de perguntas suportadas
- "Faça um relatório do churn atual"
- "Quais produtores entraram em pré-churn esse mês?"
- "Mostre o churn por cluster"
- "Quem são os produtores do gestor X em risco de churn?"
- "Compare churn de março com fevereiro"
- "Qual o valor em risco de churn esse mês?"
- "Qual o LTV médio por cohort?"
- "Quais produtores em pré-churn têm maior valor histórico?"
- "Compare o valor vendido por cluster"
- "Qual o valor médio mensal dos produtores que deram churn em março?"

## Restrições
- Dados vêm exclusivamente do Excel no SharePoint (fVendas + dProdutores)
- Escopo restrito a infoprodutores
- Respostas devem sempre citar a data de atualização dos dados

## Stack técnica preferida
- Python 3.11+
- Claude API (claude-sonnet-4-20250514) para os agentes
- pandas para manipulação dos dados
- openpyxl ou SharePoint REST API para leitura do Excel
- Streamlit ou FastAPI + HTML simples para a interface