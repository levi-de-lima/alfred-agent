# DESIGN.md — Alfred (TMB Churn Analyzer)

> **Para o Claude Design:** este é o ponto de entrada. Leia também, nesta ordem:
> 1. `design/README.md` — índice e visão geral
> 2. `design/logo-brief.md` — racional do logo + regras de uso
> 3. `design/tokens/design-tokens.json` — fonte da verdade dos tokens
> 4. `design/tokens/variables.css` — tokens prontos para usar em CSS
> 5. `design/logo/preview.html` — preview visual do logo final em todos os contextos
>
> **Regra de ouro:** todos os valores em `design-tokens.json` são autoritativos.
> Se este documento e o JSON divergirem, o JSON vence.

## Identidade Visual

O Alfred é uma ferramenta interna da TMB. O visual deve transmitir profissionalismo e clareza — interface limpa e clara (estilo Notion), com o azul elétrico da TMB como cor de destaque e identidade.

O nome "Alfred" referencia **Alfred, o Grande** — rei de Wessex (849-899), conhecido por consolidar informação dispersa, traduzir conhecimento, e codificar leis. O símbolo da marca é um **wyvern** (dragão alado bípede, símbolo histórico de Wessex), em pose rampant com a língua bífida característica terminada em seta — assinatura do wyvern heráldico.

A tipografia do wordmark é **Cinzel Bold** — tipografia romana monumental, baseada em inscrições do período clássico. A escolha é deliberada: símbolo heráldico + tipografia clássica criam uma narrativa visual coerente ("marca com herança histórica que faz software moderno"). O resto da UI usa **Inter** — sans-serif moderna e neutra, para garantir legibilidade.

Veja `design/logo-brief.md` para o racional completo e regras de uso do logo.

---

## Paleta de Cores

### Cores primárias
| Nome | Hex | Uso |
|---|---|---|
| TMB Blue | `#0066FF` | Botões, links, destaques, logo em fundo claro |
| TMB Blue Light | `#E8F0FF` | Hover de botões, fundo de badges |
| Preto TMB | `#0A0A0A` | Apenas para logo em impressão monocromática |

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

### Cores de interface (tema escuro) — Charcoal Notion-style
| Nome | Hex | Uso |
|---|---|---|
| Background | `#1A1A1A` | Fundo da página |
| Surface | `#242424` | Fundo do chat, cards, input |
| Surface Alt | `#2E2E2E` | Hover, linhas alternadas, código |
| Border | `#3A3A3A` | Bordas e divisores |
| Border Strong | `#4A4A4A` | Borda em foco |
| Text Primary | `#ECECEC` | Texto principal |
| Text Secondary | `#A0A0A0` | Texto de suporte |
| Text Muted | `#6B6B6B` | Placeholders |
| Brand Accent (dark) | `#4D8FFF` | TMB Blue ajustado para contraste WCAG AA em fundo escuro |
| Brand Accent Light (dark) | `#1A2E5C` | Fundo de badges/hover |
| Code Background | `#0F0F0F` | Mais escuro que o surface |

> No tema escuro o TMB Blue muda de `#0066FF` para `#4D8FFF`. Deliberado: `#0066FF` em fundo `#1A1A1A` falha WCAG AA para textos pequenos. O olho ainda lê como "azul TMB" — só está calibrado.

---

## Tipografia

| Família | Uso | Pesos |
|---|---|---|
| **Cinzel** (Google Fonts) | Wordmark do logo ("ALFRED" caixa alta) | 700 |
| **Inter** (Google Fonts) | UI geral — mensagens, header, tabelas, botões | 400, 500, 600, 700 |
| **JetBrains Mono** (Google Fonts) | Trechos de código nas respostas do Alfred | 400, 700 |

Fallback para Inter: `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
Fallback para Cinzel: `serif`.

### Hierarquia de tamanhos (UI em Inter)
- Header do app: lockup SVG com altura **32px** em header de 56px
- Mensagens do chat: **15px**, weight 400, line-height 1.6
- Texto secundário, badges: **13px**, weight 400
- Timestamps e notas: **12px**, weight 400

---

## Layout

```
┌─────────────────────────────────────┐
│  Header (Alfred lockup + TMB)       │
├─────────────────────────────────────┤
│   Área de mensagens (scrollável)    │
│   [Bolha usuário — direita]         │
│   [Bolha Alfred — esquerda]         │
├─────────────────────────────────────┤
│  Input bar (fixo no rodapé)         │
└─────────────────────────────────────┘
```

- Largura máxima do chat: 780px, centralizado
- Sem sidebar
- Header e input fixos

---

## Componentes

### Header
- Fundo branco (`#FFFFFF`), border-bottom `#E5E5E3`, altura 56px
- Esquerda: lockup do Alfred (SVG em `design/logo/alfred-lockup.svg`) com altura 32px
- Direita (ordem):
  1. Toggle de tema (32x32)
  2. Separador vertical `1px solid var(--border)`, altura 24px, opacidade 0.5
  3. Logo TMB (PNG em `design/logo/tmb/`): `tmb-logo-black.png` no tema claro, `tmb-logo-white.png` no tema dark. Altura 24px, opacidade 0.7 (discreto, é co-branding institucional)
- Implementação do Alfred: importar SVG inline com `fill="currentColor"` e `color: var(--brand-accent)`
- Implementação do TMB: tag `<img>` com `src` trocado por JS quando o tema muda, OU dois `<img>` com `display: none` controlado por CSS `[data-theme]`

### Bolhas de mensagem
**Usuário:** direita, fundo TMB Blue, texto branco, border-radius `18px 18px 4px 18px`, max-width 75%.
**Alfred:** esquerda, fundo branco, borda `1px solid #E5E5E3`, border-radius `18px 18px 18px 4px`, max-width 85%, markdown renderizado dentro.

### Tabelas (dentro das respostas)
Header fundo `#F7F7F5` weight 600. Linhas alternadas branco/`#FAFAFA`. Borda `1px solid #E5E5E3`. Border-radius 8px no container. Fonte 13px.

### Input bar
Fundo branco, border-top `#E5E5E3`. Campo: fundo `#F7F7F5`, border `#E5E5E3`, border-radius 12px. Placeholder: "Pergunte sobre o comercial da TMB...". Botão enviar: círculo TMB Blue. Enter envia, Shift+Enter quebra linha.

### Loading
Três pontos em fade sequencial (não bounce) dentro de uma bolha Alfred vazia.

### Favicon
Usar `design/logo/alfred-favicon.svg` (cabeça otimizada para 16-32px). `<link rel="icon" type="image/svg+xml" href="/static/alfred-favicon.svg">`.

---

## O que NÃO fazer
- Não usar gradientes ou sombras pesadas
- Não usar cores além da paleta definida
- Não usar animações além do loading dos três pontos
- Não mostrar elementos de debug, logs ou JSON bruto na interface
- Não usar bordas arredondadas excessivas (máximo 18px nas bolhas)
- Não centralizar texto nas bolhas — sempre alinhado à esquerda
- Não usar font-size abaixo de 12px
- Não usar `#0066FF` direto em fundo escuro — usar `--brand-accent` que se ajusta
- Não usar texto plano "Alfred" no header — sempre o lockup SVG
- Não usar Cinzel para nada além do wordmark — todo o resto é Inter

---

## Tema dark — comportamento

### Toggle
Botão discreto no header (direita, antes do badge "TMB"). Ícone lua/sol via lucide-react. 32x32, sem fundo, hover com `--brand-accent-light`.

### Persistência
`localStorage` chave `alfred-theme` com valor `light|dark|system`. No carregamento: ler localStorage; se ausente ou `system`, respeitar `prefers-color-scheme`. Aplicar via `document.documentElement.setAttribute('data-theme', ...)`.

### Transição
Mudança imediata (sem animação). Apenas o ícone faz fade rápido (120ms).

### Logo no tema dark
Estratégia preferida: usar o mesmo `alfred-lockup.svg` (versão azul) e trocar a cor via CSS:
```css
.logo svg { fill: var(--brand-accent); }
```
Como `--brand-accent` é `#0066FF` no light e `#4D8FFF` no dark, o logo se adapta automaticamente. Se SVG inline não for possível (ex: usado como `<img>`), usar `alfred-lockup-white.svg` no dark.

---

## Arquivos de design

```
design/
├── README.md                 # Índice e ordem de leitura
├── logo-brief.md             # Racional do logo + regras de uso
├── recraft-prompts.md        # Prompts usados (histórico)
├── logo/
│   ├── preview.html          # Preview completo (abrir no navegador)
│   ├── alfred-symbol.svg     # Wyvern de corpo inteiro (default azul)
│   ├── alfred-symbol-blue/white/black.svg
│   ├── alfred-favicon.svg    # Cabeça otimizada 16-32px (default azul)
│   ├── alfred-favicon-blue/white/black.svg
│   ├── alfred-lockup.svg     # Símbolo + "ALFRED" em Cinzel (default azul)
│   ├── alfred-lockup-blue/white/black.svg
│   └── sources/              # Originais Recraft (PNG + SVG)
├── tokens/
│   ├── design-tokens.json    # Fonte da verdade
│   └── variables.css         # Versão CSS pronta
└── references/               # Capturas/referências externas
```

Este `DESIGN.md` é o **manifesto** (porquê). Os arquivos em `design/` carregam o **o quê**.
