# AI de Churn

## Visão geral
**Objetivo:** criar modelo de churn para prever produtores em risco e camada de consulta de dados para acionamento proativo. Em junho, foco em **transformar o dash de churn em uma leitura vinculada à base real da TMB, com leituras automatizadas** (sem atualização manual). Projeto SEPARADO do HealthScore V2.
**KPI:** KPI 2 — Projetos
**Bucket Planner:** Dados & IA
**Onda:** 1 (inicia imediatamente)
**Prioridade (junho):** frente de dados do Levi.

## Owner e colaboradores
| Papel | Pessoa |
|---|---|
| Owner principal | Levi Gurgel de Lima |
| Tutoria técnica | Diego Bonassa (CRO) |
| Aprovação | Gabriel Biban |

## Datas
- **Início:** 04/05/2026
- **Prazo:** 30/06/2026

## Contexto técnico
- Base de trabalho: `agente-churn.xlsx` em `02 - Base de Dados RevOps/`
- Destino dos dados: tmb-data-sync → Supabase
- **Regra importante:** AI de Churn é projeto separado do HealthScore V2
  > "Health Score é individual, não é atrelado a AI Churn" — Gabriel

## Entregas
- [ ] Modelo de churn (sobre agente-churn.xlsx)
- [ ] Camada de consulta de dados (tmb-data-sync → Supabase)
- [ ] Interface ou relatório para acionamento pelo time

## Frente de junho — Dash de Churn na base real (automatizado)
- [ ] Vincular o **dash de churn à base real da TMB** (não mais planilha manual)
- [ ] **Automatizar as leituras** (atualização sem intervenção manual)
- [ ] Garantir paridade dos números (manual vs automático) antes de aposentar a versão manual
- [ ] Consumir o Status do Produtor canônico (Ativo/Pré-Churn/Churn) do pipeline

## Próximos passos (junho)
1. Apontar o dash de churn para a base real (Supabase/Metabase) em vez da planilha.
2. Automatizar as leituras e validar contra a versão atual.
3. Alinhar com a frente de Histórico de Vendas (Automação de Dados) para reaproveitar querys.
4. **Linkar a IA de consulta dentro do Sistema Comercial (P018)** — esta IA passa a ser a camada de consulta de dados do sistema único (ADR-009).

## Depende de
- Nada — pode iniciar imediatamente (sinergia com Automação de Dados)

## Bloqueios atuais
- Nenhum

## Log de colaboração
| Data | Quem | O que foi feito |
|---|---|---|
| 01/05/2026 | Gabriel + Claude | Card criado no Planner com owner, prazo e descrição |
| 29/05/2026 | Gabriel + Claude | Adicionada **frente de junho**: dash de churn vinculado à base real TMB com leituras automatizadas; sinergia com Automação de Dados registrada |

---

## 🔌 Skills recomendadas (RevOps)
> Para o Claude: ao começar a trabalhar neste contexto, verifique via `list_plugins` / `list_skills` se os itens abaixo já estão instalados. Se já estiverem, **não comente**. Se faltar algum, ofereça a instalação **uma vez** (card `suggest_plugin_install`) e não repita caso o usuário recuse nesta sessão.
- `product-management@knowledge-work-plugins` — specs/PRD, roadmap, sprint, métricas, update p/ diretoria
- `productivity@knowledge-work-plugins` — tarefas + memória compartilhada da equipe
- `qodo-skills@knowledge-work-plugins` — code review / resolver comentários de PR
- `security-review` e `review` — já nativas (não instalar); usar ao mexer em RLS/Auth e em revisões
- (a construir) **"RevOps — Governança & Glossário"** — padrões de glossário, pastas e criação de projetos
<!-- skills-reco-block v1 -->
