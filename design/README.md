# design/

Esta pasta é a **fonte da verdade visual** do Alfred. Tudo que precisa estar consistente entre o app, materiais externos e o trabalho do Claude Design vive aqui.

## Para o Claude Design — ordem de leitura

1. **`../DESIGN.md`** (raiz do projeto) — manifesto: porquê das decisões, tom, regras de não-fazer.
2. **`logo-brief.md`** — racional do wyvern, regras de uso e variantes obrigatórias.
3. **`logo/preview.html`** — abrir no navegador para ver o logo final em todos os contextos e tamanhos.
4. **`tokens/design-tokens.json`** — fonte da verdade. Toda regra CSS deve consumir destes nomes.
5. **`tokens/variables.css`** — versão pronta para `<link>` ou `@import`.

## Estrutura

```
design/
├── README.md                           ← você está aqui
├── logo-brief.md                       ← racional + regras de uso do logo
├── recraft-prompts.md                  ← prompts usados pra gerar o wyvern (histórico)
├── logo/
│   ├── preview.html                    ← preview do logo final — ABRIR NO NAVEGADOR
│   ├── alfred-symbol.svg               ← wyvern de corpo inteiro (default azul)
│   ├── alfred-symbol-blue.svg          ← variante azul explícita
│   ├── alfred-symbol-white.svg         ← variante branca (para fundo escuro/azul)
│   ├── alfred-symbol-black.svg         ← variante preta (impressão monocromática)
│   ├── alfred-favicon.svg              ← cabeça do wyvern (otimizado 16-32px)
│   ├── alfred-favicon-blue/white/black.svg
│   ├── alfred-lockup.svg               ← símbolo + wordmark "ALFRED" em Cinzel
│   ├── alfred-lockup-blue/white/black.svg
│   ├── tmb/                            ← logos TMB para co-branding institucional
│   │   ├── tmb-logo-black.png          ← usar em fundos claros (tema light)
│   │   └── tmb-logo-white.png          ← usar em fundos escuros (tema dark)
│   └── sources/                        ← originais do Recraft (PNG + SVG raw)
│       ├── wyvern-final.png/svg
│       └── wyvern-head-final.png/svg
├── tokens/
│   ├── design-tokens.json              ← fonte da verdade (cores, tipografia, layout)
│   └── variables.css                   ← versão CSS pronta para uso
└── references/                         ← capturas/referências externas (vazio inicialmente)
```

## Decisões fechadas

| Decisão | Valor final |
|---|---|
| Símbolo | Wyvern em pose rampant, gerado no Recraft + refinado iterativamente |
| Wordmark | "ALFRED" em Cinzel Bold (caixa alta) |
| Cor primária | TMB Blue `#0066FF` (light), ajustada para `#4D8FFF` em dark |
| Cor do logo | 3 variantes estáticas: blue, white, black + opção via CSS `currentColor` |
| Fonte da UI (corpo) | Inter (Google Fonts) |
| Tema | Light (default) + Dark Charcoal Notion-style — toggle persistente |
| Favicon | `alfred-favicon.svg` (cabeça do wyvern, não corpo inteiro) |

## Quando usar qual variante de cor

| Contexto | Variante |
|---|---|
| App em fundo claro (light theme) | `alfred-lockup.svg` (azul) — ou via CSS `currentColor` |
| App em fundo escuro (dark theme) | `alfred-lockup-white.svg` — ou via CSS `currentColor` com token dark |
| Botão/banner em fundo TMB Blue | `alfred-lockup-white.svg` |
| PDF preto-e-branco / impressão fax | `alfred-lockup-black.svg` |
| E-mail HTML (CSS variables não confiáveis) | Variante estática correspondente ao fundo |

## Fluxo de trabalho — o que o Claude Design vai fazer

A partir destes arquivos, o Claude Design deve:

1. Substituir `ui/static/alfred-favicon.svg` pelo novo `design/logo/alfred-favicon.svg`.
2. Substituir o texto "alfred" no header de `ui/index.html` pelo lockup completo (SVG inline preferencialmente).
3. Substituir os valores hardcoded de cor/tipografia/spacing no `ui/index.html` por `var(--token)` lidos de `design/tokens/variables.css`.
4. Implementar o toggle light/dark conforme `../DESIGN.md`.
5. Garantir que Cinzel e Inter sejam carregados do Google Fonts.
6. Aplicar todas as regras de componente do `DESIGN.md` (bolhas, header, input bar, etc).

## Regras de manutenção

- **Não editar tokens em mais de um lugar.** Se um valor mudar, alterar em `tokens/design-tokens.json` primeiro, depois regenerar/atualizar `variables.css`.
- **Não criar nova cor sem registrar em `design-tokens.json`.** Se aparecer um caso de uso novo, adicionar como token semântico, não como literal.
- **Logo é asset final.** Não regenerar a menos que haja uma decisão deliberada de redesign. Se precisar gerar variantes adicionais (vertical, monocromático especial, etc.), partir do mesmo path do wyvern em `logo/sources/`.
- **Adicionar referências visuais externas em `references/`** com nome descritivo (ex: `linear-header.png`, `notion-sidebar.png`) e referenciá-las no `DESIGN.md` ou `logo-brief.md`.
