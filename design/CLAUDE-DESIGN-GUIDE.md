# Guia — Como usar o Claude Design no Alfred

Tudo que o Claude Design precisa saber para implementar o redesign do `ui/index.html` está em `design/`. Este guia é o passo-a-passo prático.

---

## Antes de abrir o Claude Design — checklist

- [ ] Abra `design/logo/preview.html` no navegador e confira que tudo está como você quer (logo, tamanhos, variantes de cor)
- [ ] Confira que `design/tokens/design-tokens.json` tem todas as cores certas (especialmente o brand-accent dark `#4D8FFF`)
- [ ] Confira que o `DESIGN.md` na raiz reflete suas decisões finais
- [ ] Faça commit do estado atual no git antes de deixar o Claude Design alterar o `ui/`

## Prompt inicial para o Claude Design

Cole este prompt na primeira mensagem do Claude Design (ele é genérico o bastante pra funcionar e específico o bastante pra evitar viagem):

```
Quero refazer o design do site Alfred (TMB Churn Analyzer). O contexto
completo está em DESIGN.md e na pasta design/.

ANTES DE ESCREVER QUALQUER CÓDIGO:
1. Leia DESIGN.md inteiro
2. Leia design/README.md
3. Leia design/logo-brief.md
4. Abra design/logo/preview.html mentalmente (ele mostra como o logo deve aparecer)
5. Leia design/tokens/design-tokens.json
6. Leia design/tokens/variables.css
7. Olhe a estrutura atual de ui/index.html e ui/app.py
8. Olhe ui/static/ para entender o que já existe

DEPOIS, EXECUTE:

A. Substitua ui/static/alfred-favicon.svg pelo conteudo de
   design/logo/alfred-favicon.svg.

B. Copie design/logo/alfred-lockup.svg para ui/static/alfred-lockup.svg.

C. Importe design/tokens/variables.css no ui/index.html (ou inline o conteúdo
   no <style>). Use as CSS variables --bg, --surface, --brand-accent, etc.
   no lugar dos valores hexadecimais hardcoded que já existem.

D. Carregue as fontes Google Fonts no <head> de ui/index.html:
   - Inter (400, 500, 600, 700)
   - Cinzel (700)
   - JetBrains Mono (400, 700) — só se houver código nas respostas

E. No header de ui/index.html, substitua o texto plano "alfred" pelo SVG
   do lockup. Importar inline para permitir troca de cor via currentColor +
   --brand-accent. Altura 32px.

F. Adicione o toggle de tema light/dark no header (lado direito, antes do
   badge "TMB"). Comportamento conforme DESIGN.md (localStorage, prefers-color-scheme).

G. Aplique TODAS as regras dos componentes (bolhas, input bar, tabelas, loading)
   conforme DESIGN.md.

H. Garanta que o app continua funcionando: ui/app.py é FastAPI com POST /chat,
   não toque na lógica do backend, só na camada visual.

REGRAS:
- Não invente cores fora dos tokens
- Não use Cinzel em nada além do wordmark do logo
- Não use sombras pesadas nem gradientes
- Não quebre o JavaScript existente que faz POST /chat e renderiza mensagens
- Se algo do DESIGN.md conflitar com design-tokens.json, o JSON vence

ENTREGUE:
- ui/index.html refatorado
- ui/static/ atualizado com os assets novos
- Um resumo curto do que mudou e como testar
```

## Passo a passo no Claude Design

1. **Abra o Claude Design.** Anexe a pasta do projeto inteira ou conecte ao repositório (depende de qual interface você está usando).

2. **Cole o prompt inicial acima.** Aguarde ele responder com o plano de leitura.

3. **Confira se ele leu tudo.** Antes de aprovar qualquer mudança, peça pra ele resumir o que entendeu sobre: (a) a identidade visual, (b) a estrutura de componentes, (c) o que NÃO fazer. Se a resposta dele estiver alinhada com o DESIGN.md, prossiga. Se ele estiver "inventando" detalhes, corrija antes de deixar codar.

4. **Implementação em etapas, não tudo de uma vez.** Peça que ele entregue assim:
   - Etapa 1: apenas tokens importados + paleta no app
   - Etapa 2: tipografia (Inter + Cinzel) carregada e aplicada
   - Etapa 3: header com lockup SVG + toggle de tema
   - Etapa 4: bolhas, input bar, tabelas
   - Etapa 5: dark mode funcionando ponta-a-ponta
   - Cada etapa: revisar antes de seguir
   - Motivo: se algo quebrar, você sabe exatamente em qual etapa

5. **Teste em cada etapa.** Rode `ui/app.py` localmente, abra o navegador, mande uma pergunta de teste pro Alfred, veja se a resposta renderiza certo, troque o tema, recarregue. Se tudo OK, próxima etapa.

6. **Quando terminar tudo:** peça pra ele te mostrar antes/depois (screenshot ou descrição) e gerar um commit message descritivo.

## Pontos prováveis de problema (e como resolver)

- **Cinzel não carrega.** Confirme que o `<link>` ao Google Fonts está no `<head>` e que o `font-family` no CSS do wordmark é `'Cinzel', serif` (com aspas simples ou duplas envolvendo Cinzel — espaço no nome quebra senão).

- **Logo vira borrão em 32px no header.** Provável que o SVG esteja sendo escalado com `image-rendering: pixelated` ou `width/height` em valores estranhos. Garanta `width="auto"` + `height="32"`. SVG renderiza nítido em qualquer escala.

- **Dark mode quebra parcialmente.** Algum valor ficou hardcoded. Procure `#FFFFFF`, `#0066FF`, `#1A1A1A` no CSS e substitua por `var(--surface)`, `var(--brand-accent)`, `var(--bg)`.

- **Bolha do Alfred com markdown renderiza errado.** Garanta que ele está usando a mesma lib de markdown que já existe no projeto (não troque por outra) e que os estilos de tabela/código vêm dos tokens.

- **Toggle de tema flicka no carregamento.** A leitura de localStorage tem que acontecer **antes** do CSS aplicar — coloque um script bem pequeno e bloqueante no `<head>` que seta `data-theme` antes do CSS carregar.

## Critério de "pronto"

O redesign está pronto quando:

- [ ] Favicon mostra o wyvern (cabeça) na aba do navegador
- [ ] Header mostra o lockup completo (wyvern + ALFRED em Cinzel), 32px de altura
- [ ] Header mostra "TMB" como badge discreto à direita
- [ ] Tem toggle de tema funcional, persistente entre reloads
- [ ] Light theme: paleta charcoal/branco do DESIGN.md
- [ ] Dark theme: paleta charcoal Notion-style, logo trocando para azul ajustado ou branco
- [ ] Bolhas, tabelas, input bar seguindo os specs do DESIGN.md
- [ ] Loading com 3 pontos em fade (não bounce)
- [ ] Mensagens renderizando em Inter, código em JetBrains Mono
- [ ] Nada de hex hardcoded — tudo via CSS variables dos tokens
- [ ] App.py continua funcionando, /chat continua respondendo
- [ ] Responsivo: bolhas em 95% em telas <600px

## Depois do Claude Design

Quando ele entregar e estiver tudo OK:
1. Faça commit com mensagem descritiva
2. Atualize o CLAUDE.md mencionando que o redesign foi aplicado
3. Se algo da identidade mudar no futuro, edite primeiro `design/`, depois peça ao Claude Design pra reaplicar
