# DESIGN.md — Alfred (TMB Churn Analyzer)

## Identidade Visual
O Alfred é uma ferramenta interna da TMB. O visual deve transmitir
profissionalismo e clareza — interface limpa e clara (estilo Notion),
com o azul elétrico da TMB como cor de destaque e identidade.

---

## Paleta de Cores

### Cores primárias
| Nome | Hex | Uso |
|---|---|---|
| TMB Blue | `#0066FF` | Botões, links, destaques, ícone de envio |
| TMB Blue Light | `#E8F0FF` | Hover de botões, fundo de badges |
| Preto TMB | `#0A0A0A` | Nunca usar como fundo — apenas para logotipo se necessário |

### Cores de interface (tema claro)
| Nome | Hex | Uso |
|---|---|---|
| Background | `#F7F7F5` | Fundo da página |
| Surface | `#FFFFFF` | Fundo do chat, cards, input |
| Border | `#E5E5E3` | Bordas suaves |
| Text Primary | `#1A1A1A` | Texto principal |
| Text Secondary | `#6B6B6B` | Texto de suporte, timestamps, notas |
| Text Muted | `#A0A0A0` | Placeholders, rodapés |

### Cores semânticas
| Nome | Hex | Uso |
|---|---|---|
| Success | `#22C55E` | Taxa abaixo da meta, ✅ |
| Warning | `#F59E0B` | Taxa próxima da meta, alertas |
| Danger | `#EF4444` | Taxa acima da meta, ⚠️ |
| Info | `#0066FF` | Informações neutras (usa TMB Blue) |

---

## Tipografia
- **Fonte principal:** Inter (Google Fonts) — moderna, legível, profissional
- **Fallback:** -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
- **Tamanhos:**
  - Nome do app (header): 18px, weight 600
  - Mensagens do chat: 15px, weight 400, line-height 1.6
  - Timestamps e notas: 12px, weight 400
  - Títulos de seção nas respostas: renderizados via Markdown

---

## Layout

### Estrutura geral
```
┌─────────────────────────────────────┐
│  Header (Alfred + TMB)              │
├─────────────────────────────────────┤
│                                     │
│   Área de mensagens (scrollável)    │
│                                     │
│   [Bolha usuário — direita]         │
│   [Bolha Alfred — esquerda]         │
│                                     │
├─────────────────────────────────────┤
│  Input bar (fixo no rodapé)         │
└─────────────────────────────────────┘
```

- Largura máxima do chat: 780px, centralizado na página
- Sem sidebar
- Header fixo no topo
- Input fixo no rodapé

---

## Componentes

### Header
- Fundo branco (`#FFFFFF`), border-bottom `#E5E5E3`
- Lado esquerdo: logo/nome "Alfred" em TMB Blue (`#0066FF`), weight 700
- Lado direito: badge discreto "TMB" em Text Secondary
- Height: 56px

### Bolhas de mensagem
**Usuário:**
- Alinhamento: direita
- Fundo: TMB Blue (`#0066FF`)
- Texto: branco
- Border-radius: 18px 18px 4px 18px
- Padding: 12px 16px
- Largura máxima: 75% do chat

**Alfred:**
- Alinhamento: esquerda
- Fundo: branco (`#FFFFFF`)
- Borda: `1px solid #E5E5E3`
- Texto: Text Primary (`#1A1A1A`)
- Border-radius: 18px 18px 18px 4px
- Padding: 16px 20px
- Largura máxima: 85% do chat
- Markdown renderizado dentro da bolha

### Tabelas nas respostas do Alfred
- Header: fundo `#F7F7F5`, texto Text Primary, weight 600
- Linhas alternadas: branco e `#FAFAFA`
- Borda: `1px solid #E5E5E3`
- Border-radius: 8px no container
- Fonte: 13px

### Input bar
- Fundo: branco, border-top `#E5E5E3`
- Campo de texto: fundo `#F7F7F5`, border `#E5E5E3`, border-radius 12px
- Padding interno: 12px 16px
- Placeholder: "Pergunte sobre o churn da TMB..."
- Botão enviar: círculo com ícone de seta, fundo TMB Blue, sem texto
- Enter para enviar, Shift+Enter para nova linha

### Estado de loading (Alfred digitando)
- Três pontos animados dentro de uma bolha Alfred vazia
- Animação: fade sequencial nos três pontos (não bounce)

### Indicador de fonte dos dados
- Pequeno badge fixo no topo direito do chat (abaixo do header)
- "● Dados de MM/YYYY — SharePoint" em verde quando atualizado
- "● Dados de MM/YYYY — cache local" em amarelo quando cache

---

## Comportamentos

- Scroll automático para a última mensagem ao receber resposta
- Input desabilitado enquanto Alfred está respondendo
- Mensagem de boas-vindas ao abrir:
  "Olá! Sou o Alfred, assistente de análise de churn da TMB.
  Como posso te ajudar hoje?"
- Ao pressionar Enter com input vazio: nada acontece
- Responsivo: em telas menores que 600px, bolhas ocupam 95% da largura

---

## O que NÃO fazer
- Não usar gradientes ou sombras pesadas
- Não usar cores além da paleta definida
- Não usar animações além do loading dos três pontos
- Não mostrar elementos de debug, logs ou JSON bruto na interface
- Não usar bordas arredondadas excessivas (máximo 18px nas bolhas)
- Não centralizar texto nas bolhas — sempre alinhado à esquerda
- Não usar font-size abaixo de 12px