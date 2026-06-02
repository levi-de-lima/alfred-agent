# METABASE_IMPORT.md — Como funciona a importação Metabase

> Documenta o pipeline de extração, construção e cache dos dados de retenção
> (fVendas e dProdutores) que alimentam o `RetentionAgent` do Alfred. Estado
> atual: um único módulo (`importers/metabase.py`) que sai do Metabase, monta
> os DataFrames em memória e materializa cache parquet em `data/metabase/`.

---

## 1. Visão geral

A camada Metabase é **on-demand com cache**: o `DataAgent` chama
`load_data()` no início de toda pergunta cuja área inclui `retention`.
Diferente do HubSpot (snapshot offline), os dados de vendas são puxados
em runtime e cacheados localmente para reduzir latência.

```
Metabase                  importers/metabase.py                    Runtime
─────────                 ──────────────────────                   ──────────────
Card 189 (vendas) ──┐
                    ├─► load_data()  ──► fVendas + dProdutores ──► DataAgent
Card 194 (carteira)─┘        │                  ▲                  (se "retention" ∈ areas)
                             │                  │
                             ▼                  │
              data/metabase/tmb_churn_cache_*.parquet
              (TTL configurável · padrão 4h · mantém últimos 3)
```

Não há scheduler — o cache se renova automaticamente quando expirado.
Em caso de falha do Metabase, a aplicação continua respondendo a partir
de um cache expirado (com label `cache (stale fallback)` nas citações).

---

## 2. Arquivo e papel

| Arquivo | Papel |
|---|---|
| `importers/metabase.py` | Autentica no Metabase, baixa os cards 189 e 194 como CSV, constrói `fVendas` (grid) e `dProdutores` (dimensão), e grava/lê cache parquet. |

A função pública é importada pelo runtime:

```python
from importers.metabase import load_data
payload = load_data(force_refresh=False)
```

`payload` é um `DataPayload` contendo `vendas`, `produtores`, `source`
(`metabase` | `cache`), `loaded_at`, `cache_file_path` e
`data_reference_date`.

---

## 3. Configuração

| Variável | Origem | Uso |
|---|---|---|
| `METABASE_URL` | `.env` | Base URL da instância Metabase |
| `METABASE_USER` | `.env` | E-mail para autenticação |
| `METABASE_PASSWORD` | `.env` | Senha |
| `CACHE_DIR` | `.env` (padrão `./data/metabase`) | Pasta dos parquets de cache |
| `CACHE_MAX_AGE_HOURS` | `.env` (padrão `4`) | TTL do cache em horas |

Cards Metabase **hardcoded** no código:

| ID | Conteúdo | Função |
|---|---|---|
| **189** | Vendas mensais agregadas por produtor | Base do fVendas |
| **194** | Dimensão de produtores (Código, Nome, Gestor, Cluster) | Base do dProdutores |

Se os IDs mudarem, editar diretamente em `load_data()` (linhas que chamam
`_fetch_card_csv(token, card_id=189)` e `_fetch_card_csv(token, card_id=194)`).

---

## 4. Fluxo de execução

```
load_data(force_refresh=False)
  │
  ├─► Caminho 1: Cache válido (não force_refresh)
  │     └── Lê data/metabase/tmb_churn_cache_<ts>_{vendas,produtores}.parquet
  │         Source = "cache"
  │
  ├─► Caminho 2: Metabase OK
  │     ├── _get_metabase_token()
  │     │     POST /api/session com username/password → session_id (cached em memória)
  │     │
  │     ├── _fetch_card_csv(token, card_id=189)
  │     │     POST /api/card/189/query/csv → CSV
  │     │     Retry 3x com backoff de 2s
  │     │
  │     ├── _fetch_card_csv(token, card_id=194)
  │     │     POST /api/card/194/query/csv → CSV
  │     │
  │     ├── _build_fvendas(df_189, df_194)     ─► fVendas
  │     ├── _build_dprodutores(df_194, df_189) ─► dProdutores
  │     └── _save_to_cache(vendas, produtores)
  │           Grava 2 parquets com timestamp · evicta caches além dos 3 mais recentes
  │           Source = "metabase"
  │
  └─► Caminho 3: Metabase falhou + cache disponível
        └── Usa cache expirado · Source = "cache" (rotulado "cache (stale fallback)" downstream)
        OU
        DataUnavailableError se não houver cache nenhum.
```

---

## 5. Construção do `fVendas`

`fVendas` **não é a tabela bruta do Metabase** — é um grid construído em
memória pelo `_build_fvendas()`.

### 5.1. Entrada (card 189)

Colunas esperadas (renomeadas internamente via `_COL_MAP_189`):

| Coluna do card | Renomeada para | Tipo |
|---|---|---|
| `Produtor ID` | `produtor_id` | int64 |
| `Produtor` | `produtor_nome` | str |
| `Data: Mês` | `mes` | datetime |
| `Soma de Valor Principal` | `valor` | float64 |
| `Máximo de Efetivado em: Dia` | `ultima_venda_no_mes` | datetime |

⚠️ O card 189 retorna **apenas meses com vendas** — produtores em meses
sem venda não aparecem como linhas com `valor=0`, simplesmente não existem.
Por isso o passo seguinte é gerar um grid completo.

### 5.2. Passos

1. **Tipagem.** Renomeia colunas, converte tipos com `pd.to_datetime` e
   `pd.to_numeric` (erros → coerção segura).
2. **União com card 194.** Inclui produtores cadastrados em `dProdutores`
   que nunca venderam — eles aparecem como linhas em todos os meses com
   `Status = Inativo`.
3. **Grid produtor × mês.** Cria `MultiIndex` de todos os
   produtores cruzado com todos os meses entre o `mes.min()` do card 189
   e o mês corrente (`pd.Timestamp.now().to_period("M")`).
4. **Merge.** Preenche `valor=0` para meses sem venda.
5. **Forward-fill da última venda.** `groupby("produtor_id")["ultima_venda_no_mes"].ffill()`
   propaga a data da última venda conhecida para os meses subsequentes.
6. **Cálculo de `dias_sem_venda`.** `fim_mes - última_venda` (em dias).
7. **Status vetorizado.** Quatro condições mutuamente exclusivas:

   | Condição | Status |
   |---|---|
   | nunca teve venda | `Inativo` |
   | `dias_sem_venda ≤ 61` | `Ativo` |
   | `61 < dias_sem_venda ≤ 121` | `Pré-Churn` |
   | `dias_sem_venda > 121` | `Churn` |

8. **`Status_Anterior`.** `groupby("produtor_id")["Status"].shift(1)` —
   `NaN` no primeiro registro de cada produtor (sinaliza "primeira linha",
   não dado faltante).
9. **Schema final.** Renomeia para `Código`, `Produtor`, `Data`, `Valor`,
   ordena por `(Produtor, Data)`.

### 5.3. Schema do `fVendas`

| Coluna | Tipo | Origem |
|---|---|---|
| `Código` | int64 | Card 189 + 194 |
| `Produtor` | str | Card 189 + 194 |
| `Data` | datetime | Início do mês (todos os meses do grid) |
| `Valor` | float64 | Card 189 (0 se sem venda no mês) |
| `Status` | str | Calculado |
| `Status_Anterior` | str ou NaN | `Status.shift(1)` por produtor |

---

## 6. Construção do `dProdutores`

`_build_dprodutores()` é o equivalente "dimensão" — uma linha por produtor.

### 6.1. Entrada (card 194 + reuso do 189)

Colunas esperadas no card 194 (via `_COL_MAP_194`):
`Código`, `Produtor`, `Gestor`, `Cluster`.

Adicionalmente, lê `Máximo de Efetivado em: Dia` do card 189 para
calcular `Data 1ª Venda`.

### 6.2. Passos

1. Extrai colunas do card 194, tipa `Código` como int64.
2. Limpa strings (`Gestor`, `Produtor` → `.strip()`).
3. Normaliza `Cluster` via `CLUSTER_MAP`:

   | Bruto | Normalizado |
   |---|---|
   | Energium | PP/P |
   | Palladium | M |
   | Titanium | G |
   | Rhodium | GG/EG |
   | PP, P | PP/P |
   | GG, EG | GG/EG |
   | G | G |
   | M | M |
   | Desativado | Desativado |
   | S/C | S/C |
   | qualquer outro | Outros |

4. Calcula `Data 1ª Venda` agregando `min(Máximo de Efetivado em: Dia)`
   por produtor a partir do card 189.
5. `Data Parceria` reservada como `NaT` — sem fonte hoje.
6. Deduplica por `Código`.

### 6.3. Schema do `dProdutores`

| Coluna | Tipo | Notas |
|---|---|---|
| `Código` | int64 | Chave |
| `Produtor` | str | |
| `Cluster` | str | Valores normalizados: PP/P, M, G, GG/EG, Desativado, S/C, Outros |
| `Gestor` | str | |
| `Data Parceria` | NaT | Sem fonte — sempre nulo |
| `Data 1ª Venda` | datetime ou NaT | Calculada a partir do card 189 |

---

## 7. Cache

### 7.1. Estrutura em disco

```
data/metabase/
├── tmb_churn_cache_20260512_1733_vendas.parquet
├── tmb_churn_cache_20260512_1733_produtores.parquet
├── tmb_churn_cache_20260512_1242_vendas.parquet
├── tmb_churn_cache_20260512_1242_produtores.parquet
└── ...
```

Timestamp no formato `YYYYMMDD_HHMM`. Sufixos `_vendas` e `_produtores`
**sempre em par** — gerar um sem o outro causa falha de leitura.

### 7.2. Política

- **TTL**: `CACHE_MAX_AGE_HOURS` (padrão 4h). Após esse tempo, a próxima
  chamada a `load_data()` força refresh do Metabase.
- **Eviction**: mantém os 3 caches mais recentes (`_evict_old_cache(keep=3)`).
- **Stale fallback**: se Metabase cair e houver cache expirado, o cache é
  usado mesmo assim — o `DataAgent` rotula a fonte como
  `cache (stale fallback)` para que o ReportAgent informe o usuário.

### 7.3. Token Metabase

Não é persistido em disco — fica em memória (`_session_token` global) e
expira junto com o processo. A primeira chamada de cada execução paga o
custo da autenticação; as subsequentes reutilizam o token.

---

## 8. Considerações operacionais

### 8.1. Pontos de atenção

- **Cards hardcoded.** Mudança de ID no Metabase exige edição do código.
- **Schema do card 189 é rígido.** Renomeação ou remoção das colunas
  esperadas levanta `DataNormalizationError`. Se for necessário adaptar,
  começar pelo `_COL_MAP_189`.
- **Status depende dos thresholds (61, 121 dias).** Hardcoded em
  `_build_fvendas()`. Mudança de regra (ex: pré-churn aos 45 dias) exige
  alteração no código e invalidação manual do cache.
- **Grid de meses inclui o mês corrente.** `pd.Timestamp.now()` define
  o teto. Se rodar no dia 1 do mês, o mês recém-aberto já entra como
  linha com `valor=0` para todos.
- **Card 189 só traz quem vendeu.** A inclusão de produtores "sem venda"
  depende exclusivamente do card 194. Se um produtor estiver no 189 mas
  não no 194, ele aparece em `fVendas` mas **não** em `dProdutores` —
  filtragens por `Gestor`/`Cluster` vão omiti-lo.
- **Token sem retry de auth.** `401` aborta o processo. `MetabaseError`
  só é capturado para falhas de query, não de login.
- **Sem normalização de timezone.** Datas chegam como `Data: Mês` (string)
  e são convertidas com `pd.to_datetime` sem `tz` — assume horário local.

### 8.2. Diagnóstico rápido

| Sintoma | Suspeita |
|---|---|
| `DataUnavailableError` na inicialização | Metabase fora E sem cache local |
| `DataNormalizationError: Card 189 faltando colunas...` | Card mudou de schema — atualizar `_COL_MAP_189` |
| `cache (stale fallback)` aparecendo nas respostas | Metabase indisponível há mais de `CACHE_MAX_AGE_HOURS` |
| Cluster `Outros` aparecendo em volume | Card 194 com valor novo que não está em `CLUSTER_MAP` |
| Status inconsistente entre meses | Linha do `ultima_venda_no_mes` vazia onde deveria ter venda — checar o card 189 no Metabase |
| Faturamento zerado mesmo com venda real | Conversão de `valor` falhou — checar `Soma de Valor Principal` no card |

### 8.3. Forçar refresh

```python
from importers.metabase import load_data
payload = load_data(force_refresh=True)
```

Ou via HTTP:

```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "...", "session_id": "x", "force_refresh": true}'
```

---

## 9. Onde os dados entram no pipeline Alfred

O `agents/data_agent.py` chama `load_data()` apenas quando o
`ContextAgent` classifica a pergunta com área `"retention"`. Para
perguntas exclusivas de aquisição, o Metabase nem é consultado.

`fVendas` e `dProdutores` chegam até as tools via `ToolContext` em
`agents/tools.py`. As tools que efetivamente os consomem:

| Tool | Lê |
|---|---|
| `status_distribuicao`, `taxa_churn`, `transicoes` | `fVendas` + `dProdutores` |
| `produtores`, `faturamento`, `ltv`, `cohort` | `fVendas` + `dProdutores` |
| `resumo_churn`, `churn_streak` | `fVendas` + `dProdutores` |
| `track_produtor_funil` | `fVendas` + `dProdutores` + `hs_closer` + `hs_growth` |
| `cohort_closer_churn` | `fVendas` + `hs_closer` |

A lógica de cálculo está em `_calc_*` em `agents/analytics_agent.py`,
chamadas pelos wrappers `w_*` em `agents/tools.py`.
