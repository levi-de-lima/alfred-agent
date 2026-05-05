"""
sql_fallback.py — fallback SQL via DuckDB para perguntas ad-hoc.

Usado pelo ReAct agent quando nenhuma ferramenta específica cobre a pergunta.
O DuckDB consulta os DataFrames pandas diretamente na memória — zero migração,
zero ETL, zero banco de dados externo.

Fluxo:
  1. Recebe a pergunta em linguagem natural
  2. Pede ao Gemini que gere SQL considerando o schema e regras de negócio
  3. Executa o SQL via DuckDB contra os DataFrames
  4. Em caso de erro de SQL, faz uma tentativa de correção com o Gemini
  5. Retorna resultado no formato padrão de tool: {query_type, summary, tabular, ops}
"""

from __future__ import annotations

import json

import pandas as pd
import anthropic

from config import settings

logger = settings.logger
_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

_SQL_SYSTEM_PROMPT = """\
Você é um gerador de SQL para análise de dados da TMB (fintech para infoprodutores).

## Schema das tabelas

tabela "vendas":
  - Código (INTEGER): ID do produtor
  - Produtor (TEXT): nome completo
  - Data (DATE): granularidade mensal (primeiro dia do mês)
  - Status (TEXT): 'Ativo', 'Pré-churn', 'Churn', 'Inativo'
  - Status_Anterior (TEXT): status do mês anterior
  - Valor (FLOAT): valor vendido naquele mês

tabela "produtores":
  - Código (INTEGER): ID do produtor (FK para vendas.Código)
  - Produtor (TEXT): nome completo
  - Cluster (TEXT): 'PP', 'Palladium', 'G', 'M', 'P'
  - Gestor (TEXT): nome do gestor de contas TMB
  - "Data Parceria" (DATE): data de entrada na TMB
  - "Data 1ª Venda" (DATE): data da primeira venda

## Regras de negócio importantes

- Inativo = nunca vendeu (não é churn)
- O mês mais recente nos dados é o "estado atual"
- Para calcular taxa de churn, exclua sempre o Gestor = 'TMB Educação'
- Nunca confunda "Inativo" com "Churn" nas contagens

## Instruções

Gere APENAS o SQL, sem explicações, sem markdown, sem blocos de código.
O SQL será executado via DuckDB — use sintaxe SQL padrão.
Use aspas duplas para nomes de colunas com espaços ou caracteres especiais.
"""


def executar_sql_adhoc(
    pergunta: str,
    vendas: pd.DataFrame,
    produtores: pd.DataFrame,
) -> dict:
    """
    Gera SQL a partir da pergunta, executa via DuckDB e retorna resultado.
    Faz 1 tentativa de autocorreção se o SQL falhar.
    """
    ops: list[str] = [f"SQL fallback acionado para: '{pergunta[:80]}'"]

    sql = _gerar_sql(pergunta)
    ops.append(f"SQL gerado: {sql[:200]}")

    try:
        import duckdb
    except ImportError:
        return {
            "query_type": "sql_adhoc",
            "summary": {"aviso": "DuckDB não instalado. Execute: pip install duckdb"},
            "tabular": [],
            "ops": ops,
        }

    conn = duckdb.connect()
    conn.register("vendas", vendas)
    conn.register("produtores", produtores)

    try:
        result_df = conn.execute(sql).df()
        ops.append(f"SQL executado com sucesso: {len(result_df)} linhas")
        return {
            "query_type": "sql_adhoc",
            "summary": {"total_linhas": len(result_df), "sql_usado": sql},
            "tabular": result_df.head(50).to_dict("records"),
            "ops": ops,
        }
    except Exception as exc:
        ops.append(f"SQL falhou: {exc} — tentando autocorreção")

        # Tentativa de correção: passa o erro de volta ao Gemini
        sql_corrigido = _corrigir_sql(pergunta, sql, str(exc))
        ops.append(f"SQL corrigido: {sql_corrigido[:200]}")

        try:
            result_df = conn.execute(sql_corrigido).df()
            ops.append(f"SQL corrigido executado: {len(result_df)} linhas")
            return {
                "query_type": "sql_adhoc",
                "summary": {"total_linhas": len(result_df), "sql_usado": sql_corrigido},
                "tabular": result_df.head(50).to_dict("records"),
                "ops": ops,
            }
        except Exception as exc2:
            ops.append(f"Autocorreção também falhou: {exc2}")
            return {
                "query_type": "sql_adhoc",
                "summary": {"aviso": f"Não foi possível gerar SQL válido para esta pergunta: {exc2}"},
                "tabular": [],
                "ops": ops,
            }


def _gerar_sql(pergunta: str) -> str:
    """Pede ao Claude para gerar SQL a partir da pergunta."""
    try:
        response = _client.messages.create(
            model=settings.claude_model,
            system=_SQL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Pergunta: {pergunta}"}],
            max_tokens=512,
        )
        sql = response.content[0].text.strip()
        if sql.startswith("```"):
            lines = sql.split("\n")
            sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return sql.strip()
    except Exception as exc:
        logger.error(f"sql_fallback | Falha ao gerar SQL: {exc}")
        return "SELECT 'Não foi possível gerar SQL' AS mensagem"


def _corrigir_sql(pergunta: str, sql_errado: str, erro: str) -> str:
    """Pede ao Claude para corrigir o SQL com base no erro recebido."""
    mensagem = (
        f"Pergunta: {pergunta}\n\n"
        f"SQL que falhou:\n{sql_errado}\n\n"
        f"Erro recebido:\n{erro}\n\n"
        "Corrija o SQL para que funcione. Retorne APENAS o SQL corrigido."
    )
    try:
        response = _client.messages.create(
            model=settings.claude_model,
            system=_SQL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": mensagem}],
            max_tokens=512,
        )
        sql = response.content[0].text.strip()
        if sql.startswith("```"):
            lines = sql.split("\n")
            sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return sql.strip()
    except Exception as exc:
        logger.error(f"sql_fallback | Falha ao corrigir SQL: {exc}")
        return sql_errado
