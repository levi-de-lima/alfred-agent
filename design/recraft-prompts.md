# Recraft Prompts — Alfred Logo

Dois prompts independentes para gerar os logos no Recraft. Use o mesmo brand context nos dois, varie só a seção de **Style**. Recomendo gerar 4 variações de cada e iterar.

**Settings do Recraft a usar:**
- Style: **Vector Illustration** (ou "Logo / Brand Mark" se disponível)
- Background: **Transparent** (ou sólido branco `#FFFFFF`)
- Aspect ratio: **1:1** (square)
- Resolution: a maior possível
- Substyle: **Flat** / **Minimalist** (não "3D", não "Realistic")

---

## Prompt 1 — Geometric Minimalist

```
A minimalist geometric logo mark of a wyvern (a two-legged winged dragon with a barbed curled tail, the heraldic symbol of Wessex and of King Alfred the Great). The wyvern is shown in profile in a "rampant" pose — rearing up on its back leg with one foreleg raised, head pointing up and to the right, large triangular wing rising up and to the left, long serpentine tail curving down and to the left and ending in a sharp arrow-shaped barb.

Style: flat vector illustration. Constructed entirely from clean angular geometric shapes — triangles, trapezoids, sharp polygons. No curves, no gradients, no shadows, no outlines. The silhouette must be a single solid filled shape. Strong, confident, architectural feel, like a brand mark for a modern tech company (in the spirit of Linear, Vercel, or Stripe). Bauhaus-inspired construction.

Color: solid bright electric blue (#0066FF) on a pure white background (#FFFFFF). One color only. A small white negative-space dot for the eye.

Composition: centered, symmetrical bounding box, generous padding. The mark should read clearly at 16 pixels (favicon size) — silhouette must remain unambiguous when scaled down.

Brand context: this is the logo for "Alfred", an internal AI assistant for a Brazilian fintech called TMB, used by analysts to query churn and customer data in natural language. Named after Alfred the Great (849–899), the Anglo-Saxon king of Wessex who consolidated dispersed information, codified laws, and made knowledge accessible.

Negative prompt / avoid: photorealism, 3D rendering, gradients, drop shadows, glow effects, neon, crypto/web3 aesthetics, sports team mascot style, muscular dragon, fire breathing, cartoon, anime, fantasy/RPG art, medieval ornament, crown, shield, heraldic quarters, full coat of arms, text, letters, words, watermark.
```

---

## Prompt 2 — Heraldic Modern

```
A modern heraldic logo mark of a wyvern (a two-legged winged dragon with a barbed curled tail, the heraldic symbol of Wessex and of King Alfred the Great). The wyvern is shown in profile in a "rampant" pose — rearing up on its back leg with one foreleg raised, head with open jaw pointing up and to the right, large fan-shaped wing spread upward with visible membrane spokes, long serpentine tail curving down and to the left and ending in a barbed spade-shaped tip.

Style: flat vector illustration with smooth elegant curves (Bezier). More detailed than pure minimalism — visible wing membrane lines, a row of scales suggested across the chest, defined claws on both legs, a small flame or pointed tongue at the open jaw. Inspired by modernized sports team crests and contemporary brand refreshes of heritage marks (think Mailchimp's Freddie, Firefox's fox, or the modern updates of Premier League team badges) — character and personality, but disciplined and corporate-appropriate.

Color: two tones of blue only. Primary fill is bright electric blue (#0066FF). Secondary accent (wing membrane lines, scales, chest details, behind-the-wing shadow) in pale ice blue (#E8F0FF). Pure white background (#FFFFFF). A small white negative-space dot for the eye. No other colors.

Composition: centered in a square bounding box with generous padding. Designed to work at sizes 64px and above (presentations, document covers, large headers). Confident, expressive, but never aggressive.

Brand context: this is the logo for "Alfred", an internal AI assistant for a Brazilian fintech called TMB, used by analysts to query churn and customer data in natural language. Named after Alfred the Great (849–899), the Anglo-Saxon king of Wessex who reorganized his kingdom's defenses into a coordinated network, translated knowledge into the vernacular, and codified law. The mark should feel intelligent, trustworthy, and institutional — never fantasy or RPG.

Negative prompt / avoid: photorealism, 3D rendering, gradients, drop shadows, glow effects, neon, crypto/web3 aesthetics, e-sports mascot style, muscular angry dragon, fire breathing, cartoon, anime, Game of Thrones / Targaryen aesthetic, full coat of arms, crown, shield with quarters, ribbons, banners, text, letters, words, watermark, multiple colors beyond the two blues specified.
```

---

## Prompt 3 — Minimalist Silhouette (curvas + sem ruído interno)

> Meio-termo entre os dois anteriores. Mantém as **curvas elegantes** do heraldic (postura nobre, anatomia reconhecível) mas remove o ruído interno (membrana, escamas, garras detalhadas). Resultado: uma silhueta sólida única, instantaneamente legível em qualquer tamanho. Inspiração de tratamento: o pássaro do Twitter, o T da Tesla, a raposa da Mozilla — onde um animal complexo vira uma forma única e elegante.

```
A minimalist silhouette logo mark of a wyvern (a two-legged winged dragon with a barbed curled tail, the heraldic symbol of Wessex and of King Alfred the Great). The wyvern is shown in profile in a "rampant" pose — rearing up on its back leg with one foreleg raised, head pointing up and to the right, large wing rising up and to the left, long serpentine tail curving down and to the left and ending in a sharp barbed tip.

Style: a single solid filled silhouette built from smooth elegant flowing curves (Bezier). The OUTLINE is curved and confident, like classical heraldry — but the INTERIOR is completely flat and empty. No internal lines whatsoever: no wing membrane spokes, no scales, no chest detail, no separated claws, no muscle definition, no shading. Just one clean continuous shape. Think of the Twitter bird, the Tesla T, the Mozilla fox, or the WWF panda — a complex animal reduced to a single iconic silhouette. Confident, refined, mature, corporate-appropriate. Strong negative space around the wing and between the legs.

Color: solid bright electric blue (#0066FF) on a pure white background (#FFFFFF). One color only — no gradients, no second tone, no outlines. A tiny white negative-space dot for the eye (optional, only if it doesn't add clutter).

Composition: centered, square bounding box, generous padding. The mark must read clearly at 16 pixels (favicon size). Silhouette must be unambiguous and elegant at any size from 16px to 512px.

Brand context: this is the logo for "Alfred", an internal AI assistant for a Brazilian fintech called TMB, used by analysts to query churn and customer data in natural language. Named after Alfred the Great (849–899), the Anglo-Saxon king of Wessex who consolidated dispersed information, codified laws, and made knowledge accessible. The mark should feel intelligent, calm, institutional — never aggressive, never fantasy.

Negative prompt / avoid: internal detail of any kind, wing membrane lines, scale patterns, visible claws as separate elements, muscle outlines, two-tone fills, gradients, drop shadows, glow, outlines, photorealism, 3D, neon, crypto aesthetics, e-sports mascot, muscular angry dragon, fire breathing, cartoon, anime, fantasy/RPG art, Game of Thrones / Targaryen style, crown, shield, ribbons, text, letters, words, watermark, four legs (must be strictly bipedal, two legs only).
```

### Como pedir refinamento dentro do Recraft

Se o Recraft já gerou o heraldic detalhado de que você gostou, você pode pedir refinamento (botão "Como você gostaria de refiná-la?") usando algo como:

```
Simplify dramatically. Keep the exact same pose, anatomy, and curves, but REMOVE all internal detail: no wing membrane lines, no scales, no chest pattern, no claw separation. Make the entire wyvern a single solid silhouette in #0066FF — only the outline shape matters, the interior must be completely flat and empty. Style reference: Twitter bird, Tesla T, Mozilla fox.
```

Isso costuma funcionar melhor que gerar do zero, porque preserva a anatomia que você já curtiu.

### Refinamento: adicionar a língua bífida característica

A língua bífida saindo da boca aberta é assinatura visual do wyvern heráldico de Wessex (visível na bandeira histórica). Se o resultado simplificado não veio com ela, peça:

```
Keep everything exactly as it is — same pose, same silhouette, same proportions, same color #0066FF, same flat empty interior. The ONLY change: add a forked tongue extending from the open jaw. The tongue should be clearly forked (split into two pointed prongs at the tip), extending forward and slightly upward from the wyvern's open mouth, in the same solid #0066FF as the rest of the silhouette. The tongue must be a deliberate, sharp, assertive shape — not a small detail, but a clear signature element, like the tongue of the classical Wessex heraldic wyvern. Length: roughly one-third the length of the head. Do not change anything else.
```

---

## Depois de gerar

1. **Baixe os melhores resultados** de cada estilo (idealmente 2-3 variações por estilo).
2. **Salve em** `design/logo/sources/` com nomenclatura:
   - `geometric-v1.png`, `geometric-v2.png`, ...
   - `heraldic-v1.png`, `heraldic-v2.png`, ...
3. **Compare** lado a lado com os SVGs de referência (`01-geometric-minimalist.svg`, `02-heraldic-modern.svg`).
4. **Escolha um vencedor** de cada estilo e me passe — eu peço para o Claude Design vetorizar (SVG limpo), gerar as 5 variantes obrigatórias e aplicar no app.

## Dicas de iteração no Recraft

- Se o wyvern sair com 4 patas (estilo "dragão chinês"), adicione no prompt: **"strictly two legs, bipedal, never four-legged"**.
- Se vier muito "dragão genérico de RPG", reforce: **"heraldic British wyvern, Wessex flag style, profile view"**.
- Se a pose não ficar rampant, especifique: **"rampant pose, rearing up on hind leg, foreleg raised in the air"**.
- Se vier com texto/letras (Recraft às vezes inventa palavras), adicione no negative: **"no text, no typography, symbol only"**.
- Para garantir cor exata: o Recraft costuma respeitar hex codes — se ele desviar, reforce no início do prompt **"using ONLY the exact color #0066FF, no other shade of blue"**.
