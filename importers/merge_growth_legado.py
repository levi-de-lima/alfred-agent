"""
merge_growth_legado.py — une a base legado do Pipedrive com os leads do HubSpot.

Roda DEPOIS de importers.hubspot_growth: lê o parquet de Growth do HubSpot,
acrescenta as linhas da exportação manual do Pipedrive antigo e regrava o
MESMO arquivo. A partir daqui o `DataAgent` enxerga ambas as origens no
campo `fonte` ("hubspot" | "pipedrive"), sem precisar mudar o contrato de
leitura.

Lê:
    data/hubspot/hs_growth_leads.parquet   (saída do importers.hubspot_growth)
    data/Base Legado Growth.xlsx           (exportação manual do Pipedrive)

Salva (sobrescreve):
    data/hubspot/hs_growth_leads.parquet

Idempotência: se o parquet já contém uma coluna `fonte`, as linhas
`fonte == "pipedrive"` são descartadas antes do novo merge. Assim, rodar o
script duas vezes em sequência (sem rerodar o importer Growth) NÃO duplica
o legado.

Corte temporal (PDV_CUTOFF_DATE): linhas do Pipedrive com
`dt_criacao >= corte` são descartadas. Isso elimina a sobreposição com o
período em que o HubSpot já estava ativo (a migração foi gradual e os
dois funis conviveram por algumas semanas). Após o corte, qualquer
duplicata por email entre as duas fontes representa duas ENTRADAS
distintas no funil em momentos diferentes — oportunidades comerciais
legítimas, não erro de dados.

Valores aceitos para PDV_CUTOFF_DATE:
    "auto" (default) → usa o timestamp EXATO do primeiro lead do HubSpot
                       (`hs["dt_criacao"].min()`). Evita perder leads do
                       Pipedrive criados no mesmo dia que o HubSpot
                       arrancou, antes do primeiro registro HS.
    str (ISO date/datetime) → cutoff manual fixo, ex: "2026-03-10".
                              Atenção: "2026-03-10" significa 00:00:00,
                              então leads do Pipedrive entre meia-noite
                              e o primeiro HS daquele dia serão cortados.
                              Use timestamp completo se quiser precisão.
    None → desativa o corte (não recomendado em produção).
"""

import datetime
from pathlib import Path

import pandas as pd

from config import DATA_HUB
HS_PATH  = DATA_HUB / "hubspot" / "hs_growth_leads.parquet"
PDV_PATH = DATA_HUB / "Base Legado Growth.xlsx"
OUT_PATH = DATA_HUB / "hubspot" / "hs_growth_leads.parquet"

# Data a partir da qual o HubSpot passou a ser a fonte oficial de novos
# leads. Linhas do Pipedrive com `dt_criacao >= este corte` ficam fora
# do merge para evitar dupla-contagem com o HubSpot.
# Valores: "auto" (timestamp exato do 1º lead HubSpot) | "YYYY-MM-DD[ HH:MM:SS]" | None
PDV_CUTOFF_DATE: str | None = "auto"

# Mapeamento: coluna Pipedrive → coluna HubSpot
COLUMN_MAP = {
    "Negócio - Título":                                                            "nome",
    "Pessoa - E-mail - Trabalho":                                                  "email",
    "Pessoa - Telefone - Trabalho":                                                "telefone",
    "Negócio - Funil":                                                             "pipeline_nome",
    "Negócio - Etapa":                                                             "stage_atual_nome",
    "Negócio - Proprietário":                                                      "proprietario",
    "Negócio - Negócio criado em":                                                 "dt_criacao",
    "Negócio - Atualizado em":                                                     "dt_ultima_atualizacao",
    "Negócio - Negócio fechado em":                                                "dt_fechamento_tmb",
    "Negócio - Data de perda":                                                     "dt_desqualificado",
    "Negócio - Motivo da perda":                                                   "motivo_desqualificacao",
    "Negócio - Score Lead":                                                        "score_total_lp",
    "Negócio - Classificação ICP":                                                 "cluster_leadscore",
    "Cluster":                                                                     "cluster_faturamento",
    "Negócio - Você vende cursos online e/ou mentorias e/ou imersões?":            "vende_info",
    "Negócio - Qual sua área de atuação no projeto?":                              "area_atuacao",
    "Negócio - Qual foi seu faturamento no último ano?":                           "faturamento_ultimo_ano",
    "Negócio - Quão rapidamente você deseja implementar o boleto parcelado para impulsionar seu faturamento?": "tempo_implementacao",
    "Negócio - UTM Source":                                                        "utm_source",
    "Negócio - UTM Campaing":                                                      "utm_campaign",
    "Negócio - UTM Medium":                                                        "utm_medium",
    "Negócio - UTM Content":                                                       "utm_content",
    "Negócio - UTM Term":                                                          "utm_term",
    "Negócio - Código do Produtor na TMB":                                         "codigo_produtor",
}

# Colunas Pipedrive extras mantidas com nome renomeado
EXTRA_COLUMNS = {
    "Negócio - Qual seu principal modelo de vendas?":                              "modelo_vendas",
    "Negócio - Qual é o seu principal objetivo ao buscar uma parceria com a TMB?": "objetivo_parceria_tmb",
    "Negócio - Como está hoje sua estrutura operacional para crescer as vendas no digital?": "estrutura_operacional",
    "Negócio - Qual sua experiência com boleto parcelado?":                        "experiencia_boleto",
    "Negócio - Onde você vende os cursos online atualmente?":                      "onde_vende",
}

STATUS_MAP = {
    "Ganho":   "Ganho",
    "Perdido": "Perda",
}


def _to_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=False)


def mapear_pipedrive(pdv: pd.DataFrame) -> pd.DataFrame:
    renamed = pdv.rename(columns={**COLUMN_MAP, **EXTRA_COLUMNS})

    # status_lead derivado de Negócio - Status
    if "Negócio - Status" in pdv.columns:
        renamed["status_lead"] = pdv["Negócio - Status"].map(STATUS_MAP).fillna("Aberto")
    else:
        renamed["status_lead"] = "Aberto"

    # lead_id com prefixo para evitar colisão com IDs do HubSpot
    if "Negócio Pipe - ID" in pdv.columns:
        renamed["lead_id"] = "pdv_" + pdv["Negócio Pipe - ID"].astype(str)
    else:
        renamed["lead_id"] = "pdv_" + renamed.index.astype(str)

    # dt_novo_lead = mesma origem que dt_criacao
    if "dt_criacao" in renamed.columns:
        renamed["dt_novo_lead"] = renamed["dt_criacao"]

    # Converter datas
    for col in ["dt_criacao", "dt_ultima_atualizacao", "dt_fechamento_tmb",
                "dt_desqualificado", "dt_novo_lead"]:
        if col in renamed.columns:
            renamed[col] = _to_dt(renamed[col])

    renamed["dt_extracao"] = pd.Timestamp.now()
    renamed["fonte"] = "pipedrive"

    return renamed


def main():
    print("[Merge] Lendo HubSpot...")
    hs = pd.read_parquet(HS_PATH)
    # Idempotência: se o parquet já foi mesclado antes, dropa as linhas
    # antigas de Pipedrive para evitar duplicação no merge desta execução.
    if "fonte" in hs.columns:
        antes = len(hs)
        hs = hs[hs["fonte"] != "pipedrive"].copy()
        if len(hs) < antes:
            print(f"[Merge] Removidas {antes - len(hs)} linhas antigas de Pipedrive (idempotência)")
    hs["fonte"] = "hubspot"
    print(f"[Merge] HubSpot: {len(hs)} registros, {len(hs.columns)} colunas")

    print("[Merge] Lendo Pipedrive legado...")
    pdv_raw = pd.read_excel(PDV_PATH)
    print(f"[Merge] Pipedrive raw: {len(pdv_raw)} registros")

    pdv = mapear_pipedrive(pdv_raw)
    print(f"[Merge] Pipedrive mapeado: {len(pdv)} registros, {len(pdv.columns)} colunas")

    # Corte temporal: descarta linhas do Pipedrive a partir da data de
    # convivência com o HubSpot (evita dupla-contagem do mesmo lead).
    if PDV_CUTOFF_DATE is not None and "dt_criacao" in pdv.columns:
        if PDV_CUTOFF_DATE == "auto":
            cutoff = hs["dt_criacao"].min()
            cutoff_label = f"auto = {cutoff} (1º lead HubSpot)"
        else:
            cutoff = pd.Timestamp(PDV_CUTOFF_DATE)
            cutoff_label = str(PDV_CUTOFF_DATE)
        antes = len(pdv)
        pdv = pdv[pdv["dt_criacao"] < cutoff].copy()
        descartados = antes - len(pdv)
        if descartados > 0:
            print(f"[Merge] Corte temporal: descartadas {descartados} linhas do "
                  f"Pipedrive com dt_criacao >= {cutoff_label} (overlap com HubSpot)")

    print("[Merge] Concatenando...")
    result = pd.concat([hs, pdv], ignore_index=True)
    print(f"[Merge] Total unificado: {len(result)} registros, {len(result.columns)} colunas")
    print(f"[Merge] HubSpot: {(result['fonte'] == 'hubspot').sum()} | Pipedrive: {(result['fonte'] == 'pipedrive').sum()}")

    # Power BI rejeita tipo Arrow 'null' — colunas 100% nulas em ambas as
    # fontes (HubSpot + Pipedrive) voltariam a ser null-typed apos o concat.
    # Colunas object com tipos mistos (ex: utm_campaign com int+str vindo do
    # Pipedrive) também precisam ser normalizadas para str antes do to_parquet.
    for col in result.columns:
        if result[col].dtype == object:
            result[col] = result[col].where(result[col].isna(), result[col].astype(str)).replace("nan", "")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(OUT_PATH, index=False)
    print(f"[Merge] Salvo em {OUT_PATH}")


if __name__ == "__main__":
    main()
