# Feature — Sidebar de Histórico de Chats

Este documento contém a especificação completa da sidebar de chats + um prompt pronto pra colar no Claude Code implementar a feature.

---

## Especificação visual

### Layout geral
- Sidebar fixa à esquerda, **largura 280px**, do topo ao rodapé da viewport.
- Área de chat (atual) ocupa o restante do espaço, com max-width 780px centralizado nessa nova área.
- Em telas **< 768px**, a sidebar vira drawer off-canvas (escondida por padrão, abre por botão hambúrguer).

### Sidebar — tema claro
- Background: `var(--surface-alt)` (#FAFAFA)
- Border-right: `1px solid var(--border)`
- Padding: 0 (children fazem seu próprio padding)

### Sidebar — tema dark
- Background: `var(--surface)` (#242424) — sim, mais escuro que o main no dark, inverte intuição do light
- Border-right: `1px solid var(--border)` (#3A3A3A)

### Estrutura interna (top to bottom)

**1. Header da sidebar (altura 56px, casa com header do app)**
- Padding: 0 16px
- Display: flex, align-items: center, justify-content: space-between
- Esquerda: ícone de hambúrguer (Lucide `Menu`, 20px) — só visível em mobile (oculto em desktop com `@media >= 768px`)
- Centro/Esquerda: texto "Conversas" em Inter 14px weight 600, cor `var(--text-secondary)`
- Direita: botão "+" (32x32, ícone Lucide `Plus` 16px) — fundo `var(--brand-accent)`, ícone branco, border-radius 8px, hover: opacity 0.9

**2. Lista de chats (scroll vertical)**
- Padding: 8px
- Cada item de chat (`<button>`, não `<div>`):
  - Padding: 10px 12px
  - Border-radius: 8px
  - Gap interno: 4px
  - Display: flex column
  - Cursor: pointer
  - Transição: background 120ms ease
  
  - **Estado default:** background transparent
  - **Estado hover:** background `var(--brand-accent-light)` (com opacidade 0.5 no dark)
  - **Estado ativo (chat aberto):** background `var(--brand-accent-light)`, border-left `2px solid var(--brand-accent)` (vira box-shadow inset se border quebrar layout)
  
  - **Conteúdo:**
    - Linha 1: título do chat em Inter 14px weight 500, cor `var(--text-primary)`, truncado com ellipsis em 1 linha
    - Linha 2: preview da última mensagem do usuário (ou primeira) em Inter 12px weight 400, cor `var(--text-secondary)`, truncado em 1 linha
    - Linha 3 (opcional): timestamp relativo ("há 2h", "ontem", "5 mai") em 11px weight 400, cor `var(--text-muted)`

  - **Menu de ações** (aparece no hover, lado direito): três pontinhos (Lucide `MoreHorizontal`) 16px, abre dropdown com "Renomear" e "Apagar"

**3. Footer da sidebar (opcional, altura 48px)**
- Padding: 12px 16px
- Border-top: `1px solid var(--border)`
- Conteúdo: nome do usuário ou identificação institucional. Ex: "TMB Sales Ops" em 12px text-secondary
- Pode ser omitido na primeira versão

### Estados especiais

**Empty state (nenhum chat ainda):**
- Centralizado verticalmente
- Ícone Lucide `MessageSquare` 32px em `var(--text-muted)`
- Texto: "Nenhuma conversa ainda" em 14px weight 500 `var(--text-secondary)`
- Subtexto: "Clique em + pra começar" em 12px `var(--text-muted)`

**Loading state (carregando lista):**
- 3-4 skeleton placeholders (retângulos cinza com animação shimmer suave de 1.2s)

**Mobile drawer:**
- Fechada por padrão
- Botão hambúrguer aparece no header do app (esquerda do lockup) em telas < 768px
- Ao abrir: slide-in da esquerda em 200ms ease
- Overlay escuro `rgba(0,0,0,0.4)` cobre o conteúdo principal — clicar nele fecha
- Largura no mobile: min(280px, 85vw)

---

## Especificação funcional

### Modelo de dados
```
Chat:
  - id: uuid string
  - title: string (gerado automaticamente da 1ª pergunta, ou editável pelo usuário)
  - created_at: ISO datetime
  - updated_at: ISO datetime
  - messages: list of { role: "user"|"assistant", content: string, timestamp: ISO datetime }
```

### Endpoints REST (FastAPI, adicionar em ui/app.py)
- `GET /chats` → lista de chats sem messages (só id, title, updated_at, message_count)
- `GET /chats/{id}` → chat completo com messages
- `POST /chats` → cria novo chat vazio, retorna o id
- `PATCH /chats/{id}` → renomeia (body: `{title: string}`)
- `DELETE /chats/{id}` → apaga
- `POST /chats/{id}/messages` → adiciona mensagem ao chat e retorna a resposta do Alfred
  - Esse endpoint **substitui** o `/chat` atual ou pode coexistir. Recomendado: refatorar `/chat` pra chamar `/chats/{id}/messages` internamente

### Persistência
- **Sugestão:** SQLite via `sqlmodel` ou `sqlite3` puro
- Localização: `data/chats.db` (mesma pasta dos parquets de cache)
- Tabela única `chats` com colunas `id, title, created_at, updated_at, messages_json` (campo TEXT armazenando JSON serializado das mensagens)
- Não precisa de auth multiusuário nesta versão — assumir usuário único (TMB interno)

### Geração automática de título
- Quando o chat tem 0 mensagens e o usuário manda a primeira, gerar o título com Haiku (`CLAUDE_HAIKU_MODEL`):
  - Prompt: "Resuma esta pergunta em no máximo 6 palavras pra ser título de uma conversa: {pergunta}"
  - Salvar o resultado como `title`
  - Mostrar "Nova conversa" enquanto o título não foi gerado ainda (200-500ms)

### Comportamento do frontend
- Ao carregar a página: chamar `GET /chats`, popular a sidebar, abrir o último chat editado por default (ou empty state se não houver chats)
- Click em item da sidebar: chamar `GET /chats/{id}`, popular a área de chat principal
- Click em "+": chamar `POST /chats`, abrir o chat novo vazio, focar no input
- Ao enviar mensagem: chamar `POST /chats/{id}/messages`, append resposta na UI
- Renomear: prompt inline OU dialog modal, chamar `PATCH /chats/{id}`
- Apagar: confirm dialog ("Apagar esta conversa? Não dá pra desfazer."), chamar `DELETE /chats/{id}`
- Atualizar a sidebar (lista) sempre que: chat novo criado, título mudou, chat apagado, mensagem nova (move pro topo da lista)

---

## Prompt pra colar no Claude Code

```
Quero adicionar uma sidebar de histórico de chats no Alfred. A especificação
completa está em design/features/chats-sidebar.md — leia ela inteira antes
de começar.

CONTEXTO OBRIGATÓRIO PRA LER ANTES DE TOCAR EM CÓDIGO:
1. DESIGN.md (raiz) — manifesto do sistema visual
2. design/README.md — índice de assets
3. design/tokens/design-tokens.json — tokens
4. design/tokens/variables.css — CSS vars
5. design/features/chats-sidebar.md — spec completa desta feature
6. CLAUDE.md (raiz) — regras gerais do projeto
7. ui/index.html — código atual
8. ui/app.py — backend FastAPI atual
9. agents/orchestrator.py — pra entender o fluxo de uma pergunta

PRÉ-REQUISITO: O sistema de design já deve estar aplicado (lockup no header,
tokens via CSS vars, dark mode). Se algo disso NÃO estiver aplicado em
ui/index.html, AVISE antes de começar — provavelmente devo rodar o
CLAUDE-DESIGN-GUIDE.md primeiro.

DEPOIS DE LER TUDO, ME RESUMA:
- Como você entendeu a arquitetura atual (FastAPI + frontend)
- Como você pretende persistir os chats (sugiro SQLite com sqlite3 puro)
- A ordem de implementação que você sugere

Espere meu OK antes de codar.

QUANDO EU APROVAR, EXECUTE NESTA ORDEM:

ETAPA 1 — Backend
- Cria data/chats.db com tabela chats(id, title, created_at, updated_at, messages_json)
- Adiciona endpoints REST em ui/app.py: GET /chats, GET /chats/{id}, POST /chats,
  PATCH /chats/{id}, DELETE /chats/{id}, POST /chats/{id}/messages
- O POST /chats/{id}/messages deve invocar o orchestrator existente e salvar
  a troca user→assistant no JSON do chat
- Geração de título com Haiku quando o chat tem 0 mensagens e recebe a primeira
- Testa os endpoints com curl ou httpie e me mostra os outputs

ETAPA 2 — Layout (HTML/CSS)
- Refatora ui/index.html: agora tem flex container raiz com sidebar (280px) + main
- Sidebar segue todos os specs visuais de design/features/chats-sidebar.md
- TODOS os valores via var(--token) — zero hex hardcoded
- Funciona em light e dark
- Responsivo: em <768px vira drawer com botão hambúrguer

ETAPA 3 — JavaScript
- Ao carregar: GET /chats, popular sidebar, abrir último chat editado
- Click no item: GET /chats/{id}, renderizar mensagens
- Click no "+": POST /chats, foca no input
- Enviar mensagem: POST /chats/{id}/messages (substitui o /chat atual)
- Menu de ações (3 pontinhos): renomear (PATCH) e apagar (DELETE com confirm)
- Mover chat editado pro topo da lista após nova mensagem
- Empty state quando lista vazia
- Loading state com skeletons enquanto carrega

ETAPA 4 — Polish
- Trunca títulos longos com ellipsis
- Timestamps relativos ("há 2h", "ontem", "5 mai") em pt-BR
- Animação de slide-in da drawer em mobile (200ms ease)
- Foco automático no input ao abrir/criar chat

ENTRE CADA ETAPA, ESPERA EU TESTAR E APROVAR. Não pula etapas.

REGRAS:
- Não usar nenhuma cor fora dos tokens
- Não usar Cinzel em lugar nenhum (Cinzel é só pra o wordmark do logo)
- Não quebrar a lógica do orchestrator/agents
- Não criar arquivos novos sem necessidade — preferir editar ui/app.py e ui/index.html
- Se for criar uma nova lib helper de persistência, mantém em ui/storage.py
- Não usar localStorage pra persistir os chats — usar SQLite no servidor
  (motivo: usuário pode trocar de máquina/navegador e quer ver histórico)
- Testar antes de me entregar: criar 2 chats, mandar perguntas, recarregar
  a página, conferir se as conversas voltaram, apagar uma, conferir.

DELIVERABLES:
- ui/app.py com novos endpoints
- ui/index.html refatorado com sidebar
- ui/storage.py (se criar) com a camada de persistência
- data/chats.db criado (vazio ou com 1 chat de teste)
- Resumo curto no final do que mudou e como testar
```
