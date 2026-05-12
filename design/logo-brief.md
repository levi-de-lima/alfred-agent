# Logo Brief — Alfred

## Por que um wyvern?

O nome **Alfred** é uma homenagem a **Alfred, o Grande** (Ælfred, c. 849-899) — rei de Wessex e o único monarca inglês a receber o epíteto "o Grande". Alfred é lembrado por três motivos que dialogam com o que o app faz:

1. **Defendeu seu reino consolidando informação dispersa** — reorganizou o sistema de defesa de Wessex em uma rede de burhs (fortes) interconectados, criando o primeiro sistema "de inteligência" coordenado da Inglaterra anglo-saxônica.
2. **Promoveu literacia e tradução** — mandou traduzir do latim para o inglês antigo as obras que considerava "mais necessárias para se conhecer". Tornou conhecimento acessível.
3. **Codificou leis** — compilou e reformou o código legal de Wessex (Doom Book).

O símbolo histórico de Wessex é o **Wyvern dourado em campo azul** — um dragão alado de duas patas, com cauda enrolada e barbada, e a língua bífida característica. É esse símbolo que herdamos (reinterpretado em TMB Blue).

Para um app cuja função é **consolidar dados dispersos, traduzir consultas em linguagem natural em análises, e proteger a base de clientes contra churn**, o paralelo é direto e o símbolo carrega significado real — não é decorativo.

---

## Decisão final

Depois de iterar entre 4 conceitos iniciais (geométrico minimalista, heráldico moderno, monoline, abstrato em A), o caminho escolhido foi o **heráldico moderno simplificado** — silhueta sólida única com curvas elegantes, sem ruído interno, com a língua bífida terminada em seta como assinatura visual.

A geração final foi feita no Recraft (ver `recraft-prompts.md` para os prompts e o processo de iteração) e refinada em duas etapas: primeiro a cabeça (que virou o favicon), depois o corpo inteiro (que virou o símbolo principal).

### Atributos da marca

A marca Alfred transmite, em ordem de prioridade:

1. **Confiabilidade** — é uma ferramenta de análise para decisões reais.
2. **Inteligência** — não é um chatbot genérico, é um analista.
3. **Sobriedade institucional** — está sob a TMB, fintech B2B.
4. **Personalidade com herança** — o wyvern + Cinzel dão caráter sem ser caricatural.

Não transmite: agressividade, fantasia/RPG, infantilidade, "tech bro" genérico.

---

## Assets finais

Todos em `design/logo/`:

| Arquivo | Conteúdo | Quando usar |
|---|---|---|
| `alfred-symbol.svg` | Wyvern de corpo inteiro, azul | Default em qualquer contexto |
| `alfred-symbol-blue/white/black.svg` | Mesmo símbolo, cores explícitas | Quando CSS variables não funcionam |
| `alfred-favicon.svg` | Só a cabeça do wyvern, otimizada para 16-32px | Favicon do navegador |
| `alfred-favicon-blue/white/black.svg` | Variantes do favicon | Mesma lógica |
| `alfred-lockup.svg` | Símbolo + wordmark "ALFRED" em Cinzel | Header do app, capas, e-mail |
| `alfred-lockup-blue/white/black.svg` | Variantes do lockup | Casos sem CSS |

### Versão para regenerar (sources)

`logo/sources/` contém os arquivos originais do Recraft em PNG e SVG:
- `wyvern-final.png/svg` — corpo inteiro
- `wyvern-head-final.png/svg` — cabeça (favicon)

Se for preciso gerar uma variante nova (ex: lockup vertical, versão para impressão grande, ícone de app mobile), partir destes arquivos.

---

## Tipografia do wordmark

**Cinzel Bold (700) em caixa alta** — `ALFRED`.

Cinzel é uma fonte serif baseada em inscrições romanas monumentais (período de Trajano), desenhada para ser usada em caixa alta. Foi escolhida pelo pareamento direto com o wyvern: ambos vêm do mesmo universo histórico (heráldica + epigrafia romana).

Especificações no lockup atual:
- **font-size:** 58px (em viewBox 420×128)
- **font-weight:** 700
- **letter-spacing:** 4 (caps em fontes romanas pedem espaçamento positivo para respirar)
- **font-family:** `'Cinzel', serif`
- **Carregamento:** Google Fonts (`@import` no SVG ou `<link>` no HTML)

Inter é a fonte do **resto da UI** — para garantir que o logo seja um "selo histórico" e o produto continue moderno e legível.

---

## Regras de uso

### Variantes obrigatórias geradas
- ✅ Símbolo monocromático azul (default) — `alfred-symbol-blue.svg`
- ✅ Símbolo branco (para fundo escuro) — `alfred-symbol-white.svg`
- ✅ Símbolo preto (para impressão) — `alfred-symbol-black.svg`
- ✅ Lockup horizontal nos 3 cores — `alfred-lockup-*.svg`
- ✅ Favicon nos 3 cores — `alfred-favicon-*.svg`

### Variantes a gerar pelo Claude Design quando necessário
- ⏳ Lockup vertical (símbolo em cima, "ALFRED" embaixo) — para splash screens e footer
- ⏳ Versão para impressão em alta resolução (8000px) — para capas de relatórios físicos
- ⏳ Símbolo em outline (apenas contorno) — se vier algum caso de uso pedindo

### Espaçamento (clear space)

Reservar ao redor do logo, em qualquer aplicação, no mínimo a altura da letra "A" do wordmark. Não posicionar texto, borda, ou outro elemento dentro dessa zona.

### Tamanhos mínimos

- Símbolo isolado: **16px** mínimo (favicon)
- Lockup horizontal: **96px** de largura mínima

### Cores permitidas

| Contexto | Cor do logo |
|---|---|
| Fundo claro (`#F7F7F5`, `#FFFFFF`) | TMB Blue (`#0066FF`) — variante azul |
| Fundo escuro (`#1A1A1A`, `#242424`) | Branco (`#FFFFFF`) — variante white, OU TMB Blue ajustado (`#4D8FFF`) via CSS |
| Fundo TMB Blue | Branco (`#FFFFFF`) — variante white |
| Documento mono (PDF preto e branco) | Preto (`#0A0A0A`) — variante black |

Nunca usar o logo em: gradientes, fundos coloridos fora da paleta, com sombras pesadas, com efeitos 3D.

### Co-branding com TMB

Quando ambas as marcas aparecerem (ex: header do app, capa de relatório):

```
[ Wyvern ALFRED ]   |   [ TMB-monograma TMB ]
```

**Logos TMB oficiais** disponíveis em `design/logo/tmb/`:
- `tmb-logo-black.png` — monograma + "TMB" em preto. Usar em fundos claros.
- `tmb-logo-white.png` — versão branca. Usar em fundos escuros e em fundo TMB Blue.

**Regras de hierarquia visual:**
- Sempre na ordem: **Alfred à esquerda (foco), TMB à direita (atribuição)**.
- Alfred é a marca do produto — deve ter peso visual maior (lockup completo, ~32px).
- TMB é a marca institucional — deve ser discreta (altura 24px, opacidade 0.7).
- Separador entre as duas: linha vertical 1px em `var(--border)` com 24px de altura, OU espaço em branco de 24px.
- No tema dark, alternar para `tmb-logo-white.png` (não inverter via filter — usar o arquivo certo).

**Não fazer:**
- Não colocar TMB à esquerda do Alfred (inverte hierarquia).
- Não colocar TMB do mesmo tamanho do Alfred (compete com a marca do produto).
- Não usar a logo TMB sozinha sem o Alfred no header — sempre co-branded.
- Não usar opacidade < 0.5 (vira invisível em telas com baixo brilho).

---

## Inspirações deliberadas

- **The New York Times** (typography editorial no logo + sans-serif no corpo) — modelo para a separação Cinzel+Inter.
- **Penguin Books** — uso de logo com personalidade histórica em produtos modernos.
- **Stripe Atlas** — disciplina e elegância no uso de símbolo + wordmark lockup.
- **Notion** — para o tom geral do app (onde o logo vive).

Inspiração explícita do símbolo: **bandeira moderna de Wessex** (wyvern dourado em campo azul). Não estamos copiando — estamos referenciando.

---

## Inspirações a evitar

- Logos de equipes de e-sports (dragão musculoso, cuspindo fogo).
- Logos de RPG / fantasia medieval (House Targaryen, Game of Thrones).
- Brasões reais cheios de detalhe (coroa, escudo, quartéis).
- Estética "crypto" (gradientes neon, formas isométricas).
- Wordmark em fontes "medievais decorativas" (Uncial Antiqua, Pirata One, etc.) — Cinzel é o limite arturiano apropriado.
