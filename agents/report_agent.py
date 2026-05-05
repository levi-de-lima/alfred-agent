"""
report_agent.py — formata AnalyticsResult em resposta markdown em pt-BR.

Responsabilidade: chamar Claude com o resultado analítico e redigir
a resposta final para o usuário. Nunca toca em DataFrames.
"""

import json
import time
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime

import anthropic

from agents.analytics_agent import AnalyticsResult
from config import settings
from prompts import REPORT_SYSTEM_PROMPT

logger = settings.logger
_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class ReportInput:
    analytics_results: list[AnalyticsResult]  # 0 = greeting, 1 = simples, 2 = misto
    user_query: str
    response_language: str = "pt-BR"
    is_speaking_to_gestor: bool = False
    identified_user: str | None = None
    conversation_history: list[dict] = None


@dataclass
class ReportOutput:
    markdown_response: str
    data_citation: str
    response_tokens: int


# ---------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------

def run(inp: ReportInput, session_id: str) -> ReportOutput:
    _log(session_id, "started", results=len(inp.analytics_results))
    t0 = time.time()

    primary = inp.analytics_results[0] if inp.analytics_results else _empty_result()
    citation = _build_citation(primary)

    # greeting / ask_identity: fluxo leve sem blocos
    if primary.query_type in ("greeting", "ask_identity"):
        payload_json = _serialize_result(primary)
        user_message = f"Pergunta do usuário: {inp.user_query}\n\nResultado:\n{payload_json}"
        history = _to_claude_history(inp.conversation_history or [])
        messages = history + [{"role": "user", "content": user_message}]
        try:
            response = _client.messages.create(
                model=settings.claude_haiku_model,
                system=REPORT_SYSTEM_PROMPT,
                messages=messages,
                max_tokens=512,
            )
            blocks = json.loads(_extract_json(response.content[0].text))
            rendered = _render_blocks(blocks, primary)
            tokens = response.usage.output_tokens
        except Exception as exc:
            logger.error(f"ReportAgent | greeting Claude falhou: {exc}")
            rendered = _template_fallback(primary)
            tokens = 0
        _log(session_id, "completed", duration_ms=int((time.time() - t0) * 1000))
        return ReportOutput(
            markdown_response=f"{rendered}\n\n---\n_{citation}_",
            data_citation=citation,
            response_tokens=tokens,
        )

    # Monta payload — misto combina os dois resultados
    if len(inp.analytics_results) == 2:
        payload_json = json.dumps({
            "resultado_retencao": json.loads(_serialize_result(inp.analytics_results[0])),
            "resultado_aquisicao": json.loads(_serialize_result(inp.analytics_results[1])),
        }, ensure_ascii=False, default=str)
        cross_hint = (
            "\n\nNOTA: Esta pergunta cruzou dados de retenção (Metabase) e aquisição (HubSpot). "
            "Conecte os dois contextos narrativamente."
        )
    else:
        payload_json = _serialize_result(primary)
        cross_hint = ""

    identity_context = ""
    if inp.is_speaking_to_gestor and inp.identified_user:
        identity_context = (
            f"\n\nCONTEXTO DE IDENTIDADE: O usuário desta conversa É o próprio gestor '{inp.identified_user}'. "
            "Use 'você' e 'sua carteira'."
        )
    elif inp.identified_user:
        identity_context = (
            f"\n\nCONTEXTO DE IDENTIDADE: Este relatório foi solicitado por um terceiro sobre o gestor '{inp.identified_user}'. "
            "Use o nome do gestor na terceira pessoa."
        )

    user_message = (
        f"Pergunta do usuário: {inp.user_query}\n\n"
        f"Dados:\n{payload_json}"
        f"{identity_context}{cross_hint}"
    )

    history = _to_claude_history(inp.conversation_history or [])
    messages = history + [{"role": "user", "content": user_message}]

    try:
        response = _client.messages.create(
            model=settings.claude_model,
            system=REPORT_SYSTEM_PROMPT,
            messages=messages,
            max_tokens=4096,
        )
        blocks = json.loads(_extract_json(response.content[0].text))
        rendered = _render_blocks(blocks, primary)
        tokens = response.usage.output_tokens
        _log(session_id, "claude_call",
             tokens_in=response.usage.input_tokens,
             tokens_out=tokens, duration_ms=int((time.time() - t0) * 1000))
    except Exception as exc:
        logger.error(f"ReportAgent | Claude API falhou: {exc}")
        rendered = (
            "⚠️ **Servidor temporariamente indisponível**\n\n"
            "Não foi possível conectar à API Claude. "
            "Por favor, tente novamente em alguns instantes."
        )
        tokens = 0
        _log(session_id, "fallback_used", error=str(exc)[:120])

    full_response = f"{rendered}\n\n---\n_{citation}_"
    _log(session_id, "completed", duration_ms=int((time.time() - t0) * 1000))
    return ReportOutput(markdown_response=full_response, data_citation=citation, response_tokens=tokens)


def _extract_json(text: str) -> str:
    """Remove blocos de código markdown e retorna JSON limpo."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove primeira linha (```json ou ```) e última linha (```)
        start = 1
        end = len(lines)
        if lines[-1].strip() == "```":
            end = -1
        text = "\n".join(lines[start:end]).strip()
    return text


def _empty_result() -> AnalyticsResult:
    from datetime import date
    return AnalyticsResult(
        query_type="greeting",
        summary_stats={},
        tabular_data=[],
        data_reference_date=date.today(),
        data_source="none",
        warnings=[],
        pandas_operations_log=[],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PALETTE = ["#3b82f6","#ef4444","#22c55e","#f59e0b","#8b5cf6","#ec4899","#06b6d4","#84cc16"]
_COR_CHART = {"churn":"#ef4444","ativo":"#22c55e","prechurn":"#f59e0b","neutro":"#6b7280"}


def _render_blocks(blocks: list, result: AnalyticsResult) -> str:
    """Renderiza lista de blocos JSON em string final (markdown + HTML)."""
    if not isinstance(blocks, list):
        return _template_fallback(result)
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        tipo = block.get("tipo")
        if tipo == "text":
            content = block.get("conteudo", "")
            if content:
                parts.append(content)
        elif tipo == "card":
            campos = block.get("campos", [])
            if campos:
                parts.append(_render_card(campos))
        elif tipo == "tabela":
            rendered = _render_tabela(block)
            if rendered:
                parts.append(rendered)
        elif tipo == "cohort":
            parts.append(_render_cohort_table(result))
        elif tipo == "grafico":
            rendered = _render_grafico(block)
            if rendered:
                parts.append(rendered)
    return "\n\n".join(parts) if parts else _template_fallback(result)


def _render_grafico(block: dict) -> str:
    """Gera HTML com div .alfred-chart para renderização via Chart.js no frontend."""
    chart_id = f"chart_{_uuid.uuid4().hex[:8]}"
    config = _build_chartjs_config(block)
    config_json = json.dumps(config, ensure_ascii=False)
    titulo = block.get("titulo", "")
    titulo_html = (
        f'<p style="font-weight:600;font-size:14px;margin:0 0 8px 0;">{titulo}</p>'
        if titulo else ""
    )
    return (
        f'<div style="max-width:680px;margin:16px 0;">'
        f'{titulo_html}'
        f'<div class="alfred-chart" data-chart-id="{chart_id}"'
        f" data-config='{config_json}'>"
        f'<canvas id="{chart_id}" height="280"></canvas>'
        f'</div>'
        f'</div>'
    )


def _build_chartjs_config(block: dict) -> dict:
    """Traduz bloco 'grafico' para configuração Chart.js."""
    chart_type = block.get("chart_type", "bar")
    labels     = block.get("labels", [])
    datasets   = block.get("datasets", [])
    opcoes     = block.get("opcoes") or {}
    sufixo     = opcoes.get("eixo_y_sufixo", "")
    meta       = opcoes.get("meta_linha")

    cjs_type = {"line": "line", "bar": "bar", "donut": "doughnut", "bar_stacked": "bar"}.get(chart_type, "bar")
    stacked  = chart_type == "bar_stacked"

    cjs_datasets = []
    for i, ds in enumerate(datasets):
        cor  = _COR_CHART.get(ds.get("cor", ""), _PALETTE[i % len(_PALETTE)])
        base = {"label": ds.get("label", ""), "data": ds.get("data", [])}
        if cjs_type == "line":
            base.update({"borderColor": cor, "backgroundColor": cor + "33",
                         "tension": 0.3, "fill": False, "pointRadius": 4})
        elif cjs_type == "doughnut":
            # donut: lista de cores por fatia
            base.update({"backgroundColor": [_PALETTE[j % len(_PALETTE)] for j in range(len(ds.get("data", [])))]})
        else:
            base.update({"backgroundColor": cor, "borderRadius": 4})
        cjs_datasets.append(base)

    # Linha de meta como dataset extra (só em line/bar)
    if meta is not None and cjs_type != "doughnut":
        cjs_datasets.append({
            "label": f"Meta ({meta}{'%' if sufixo == '%' else ''})",
            "data": [meta] * len(labels),
            "type": "line",
            "borderColor": "#94a3b8",
            "borderDash": [6, 4],
            "borderWidth": 1.5,
            "pointRadius": 0,
            "fill": False,
        })

    scales = {}
    if cjs_type != "doughnut":
        y_ticks = {"callback": f"__SUFFIX_{sufixo}__"} if sufixo else {}
        scales = {
            "y": {"stacked": stacked, "ticks": y_ticks, "grid": {"color": "#f1f5f9"}},
            "x": {"stacked": stacked, "grid": {"display": False}},
        }

    return {
        "type": cjs_type,
        "data": {"labels": labels, "datasets": cjs_datasets},
        "options": {
            "responsive": True,
            "plugins": {
                "legend": {"position": "bottom", "labels": {"font": {"size": 12}}},
                "tooltip": {"mode": "index"},
            },
            "scales": scales,
        },
    }


def _render_card(campos: list[dict]) -> str:
    """Renderiza grid HTML de KPIs a partir de lista de campos definida pelo Gemini."""
    _COR_MAP = {
        "churn":    ("var(--status-churn-bg)",   "var(--status-churn-text)"),
        "ativo":    ("var(--status-ativo-bg)",    "var(--status-ativo-text)"),
        "prechurn": ("var(--status-prechurn-bg)", "var(--status-prechurn-text)"),
        "neutro":   ("var(--surface)",            "var(--ink)"),
    }
    cols = min(max(len(campos), 1), 4)
    kpi_cells = ""
    for campo in campos:
        bg, fg = _COR_MAP.get(campo.get("cor", "neutro"), _COR_MAP["neutro"])
        label    = campo.get("label", "")
        valor    = campo.get("valor", "—")
        subtexto = campo.get("subtexto", "")
        kpi_cells += (
            f'<div style="background:{bg};border-radius:10px;padding:16px;">\n'
            f'  <div style="font-size:11px;color:{fg};text-transform:uppercase;'
            f'letter-spacing:1px;margin-bottom:8px;">{label}</div>\n'
            f'  <div style="font-size:28px;font-weight:600;color:{fg};">{valor}</div>\n'
        )
        if subtexto:
            kpi_cells += (
                f'  <div style="font-size:12px;color:{fg};margin-top:4px;opacity:0.8;">{subtexto}</div>\n'
            )
        kpi_cells += '</div>\n'
    return (
        f'<div style="display:grid;grid-template-columns:repeat({cols},1fr);'
        f'gap:12px;margin-bottom:24px;">\n'
        + kpi_cells
        + '</div>'
    )


def _render_tabela(block: dict) -> str:
    """Renderiza tabela markdown a partir de {titulo, colunas, linhas}."""
    titulo  = block.get("titulo", "")
    colunas = block.get("colunas", [])
    linhas  = block.get("linhas", [])
    if not colunas or not linhas:
        return ""
    header = f"### {titulo}\n\n" if titulo else ""
    header_row = "| " + " | ".join(str(c) for c in colunas) + " |"
    sep_row    = "| " + " | ".join("---" for _ in colunas) + " |"
    data_rows  = "\n".join(
        "| " + " | ".join(str(v) for v in row) + " |"
        for row in linhas[:10]
    )
    return header + header_row + "\n" + sep_row + "\n" + data_rows


def _pct_to_bg_color(pct: float) -> str:
    """Escala azul (baixo) → branco (médio) → vermelho (alto) para heatmap de cohort."""
    if pct <= 0:
        return "rgb(239,246,255)"
    if pct <= 12:
        t = pct / 12
        r = int(219 + t * (255 - 219))
        g = int(234 + t * (255 - 234))
        b = int(254 + t * (255 - 254))
        return f"rgb({r},{g},{b})"
    if pct <= 25:
        t = (pct - 12) / 13
        r = 255
        g = int(255 - t * 29)   # 255 → 226
        b = int(255 - t * 29)
        return f"rgb({r},{g},{b})"
    t = min((pct - 25) / 30, 1.0)
    r = 255
    g = int(226 - t * 196)  # 226 → 30
    b = int(226 - t * 196)
    return f"rgb({r},{g},{b})"


def _render_cohort_table(result: AnalyticsResult) -> str:
    """Gera tabela HTML de cohort de churn (heatmap) sem chamar Claude."""
    s = result.summary_stats
    matrix: dict = s.get("cohort_matrix", {})
    cohort_sizes: dict = s.get("cohort_sizes", {})
    min_offset: int = s.get("min_offset", 3)
    max_offset: int = s.get("max_offset", 3)
    cohort_by: str = s.get("cohort_by", "primeira_venda")

    if not matrix:
        return "_Sem dados suficientes para construir a tabela de cohort._"

    cohort_label = "Primeira Venda" if cohort_by == "primeira_venda" else "Data de Parceria"
    offsets = list(range(min_offset, max_offset + 1))

    # Exibe apenas cohorts a partir de 2024-01 (travado por regra de display)
    cohorts = [c for c in sorted(matrix.keys()) if c >= "2024-01"]

    # ── Cabeçalho (sticky) ────────────────────────────────────────────────
    _th_base = (
        "background:#111827;color:#fff;padding:6px 8px;"
        "font-size:12px;position:sticky;top:0;z-index:2;"
    )
    header_cells = (
        f'<th style="{_th_base}text-align:left;white-space:nowrap;'
        f'padding:6px 12px;left:0;z-index:3;">Cohort</th>'
    )
    for off in offsets:
        header_cells += (
            f'<th style="{_th_base}text-align:center;min-width:36px;">{off}</th>'
        )

    # ── Linhas de dados ────────────────────────────────────────────────────
    rows_html = ""
    for cohort_str in cohorts:
        row_data: dict = matrix.get(cohort_str, {})

        row = (
            f'<td style="background:#1e293b;color:#fff;padding:5px 12px;'
            f'white-space:nowrap;font-weight:600;font-size:12px;'
            f'border-right:2px solid #374151;'
            f'position:sticky;left:0;z-index:1;">{cohort_str}</td>'
        )
        for off in offsets:
            pct = row_data.get(off)
            if pct is None:
                row += (
                    '<td style="background:#f1f5f9;padding:4px 6px;'
                    'border:1px solid #e2e8f0;"></td>'
                )
            else:
                bg = _pct_to_bg_color(pct)
                text_col = "#fff" if pct >= 36 else "#1e293b"
                row += (
                    f'<td style="background:{bg};padding:4px 6px;'
                    f'text-align:center;border:1px solid #e2e8f0;'
                    f'color:{text_col};font-size:11px;font-weight:500;">'
                    f'{pct:.0f}%</td>'
                )
        rows_html += f"<tr>{row}</tr>"

    table_html = (
        '<div style="max-height:420px;overflow-y:auto;overflow-x:auto;'
        'margin:12px 0;border:1px solid #e2e8f0;border-radius:8px;">'
        '<table style="border-collapse:collapse;'
        'font-family:Inter,system-ui,sans-serif;">'
        f'<thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table>'
        '</div>'
    )

    header = (
        f'## Tabela de Cohort — % em Churn por Mês de {cohort_label}\n\n'
        f'Cada célula indica o percentual de produtores daquele cohort que estavam '
        f'em **Churn** no respectivo mês desde a primeira venda. '
        f'Colunas em branco = mês ainda não ocorreu.'
    )
    legend = (
        '<p style="font-size:11px;color:var(--ink-30);font-style:italic;margin-top:8px;">'
        'Azul claro = baixo churn · branco = intermediário · vermelho = alto churn · '
        'coluna = meses desde a primeira venda (mínimo 3)'
        '</p>'
    )

    return header + "\n\n" + table_html + "\n" + legend


def _render_summary_card_legacy(result: AnalyticsResult) -> str:
    """Legado — mantido para compatibilidade; use _render_card() com blocos do Gemini."""
    s = result.summary_stats
    fonte = result.data_source or "Metabase"
    mes_ref = s.get("mes_referencia", "—")

    def _fmt(v: float, dec: int = 2) -> str:
        return f"{v:.{dec}f}".replace(".", ",")

    taxa           = float(s.get("taxa_churn", 0))
    n_novos_churns = int(s.get("n_novos_churns", 0))
    delta          = float(s.get("delta_vs_meta", 0))
    n_churn        = s.get("n_churn", 0)
    n_ativo   = s.get("n_ativo", 0)
    n_prechurn = s.get("n_prechurn", 0)
    pct_churn   = float(s.get("pct_churn", 0))
    pct_ativo   = float(s.get("pct_ativo", 0))
    pct_prechurn = float(s.get("pct_prechurn", 0))
    tendencia = s.get("tendencia_alert", "")
    gestor    = s.get("gestor_pior")

    delta_str = f"{'+' if delta >= 0 else ''}{_fmt(delta)}"

    alert_html = ""
    if taxa > 5.0 and tendencia:
        alert_html = (
            '<div style="background:var(--status-churn-bg); border-left:3px solid '
            'var(--status-churn-text); border-radius:0 8px 8px 0; padding:12px 16px; '
            'margin-bottom:24px; font-size:14px; color:var(--status-churn-text);">\n'
            f'  ⚠️ {tendencia}\n'
            '</div>\n'
        )

    gestor_html = ""
    if gestor:
        g_nome  = gestor.get("nome", "—")
        g_taxa  = _fmt(float(gestor.get("taxa", 0)), 1)
        g_novos = gestor.get("churns_novos", 0)
        g_cart  = gestor.get("carteira", 0)
        gestor_html = (
            '<div style="background:var(--surface); border:1px solid var(--border); '
            'border-radius:10px; padding:16px; margin-bottom:8px; display:flex; '
            'justify-content:space-between; align-items:center;">\n'
            '  <div>\n'
            '    <div style="font-size:11px; color:var(--ink-30); text-transform:uppercase; '
            'letter-spacing:1px; margin-bottom:4px;">Maior taxa — atenção imediata</div>\n'
            f'    <div style="font-size:15px; font-weight:600; color:var(--ink);">{g_nome}</div>\n'
            f'    <div style="font-size:12px; color:var(--ink-60); margin-top:2px;">'
            f'{g_novos} churns novos · carteira de {g_cart}</div>\n'
            '  </div>\n'
            f'  <span style="background:var(--status-churn-bg); color:var(--status-churn-text); '
            f'font-size:20px; font-weight:700; padding:8px 16px; border-radius:8px;">{g_taxa}%</span>\n'
            '</div>\n'
        )

    kpis = (
        '<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:24px;">\n'
        # Taxa de churn
        '  <div style="background:var(--status-churn-bg); border-radius:10px; padding:16px;">\n'
        '    <div style="font-size:11px; color:var(--status-churn-text); text-transform:uppercase; '
        'letter-spacing:1px; margin-bottom:8px;">Taxa de churn</div>\n'
        f'    <div style="font-size:28px; font-weight:600; color:var(--status-churn-text);">{_fmt(taxa)}%</div>\n'
        f'    <div style="font-size:12px; color:var(--status-churn-text); margin-top:4px; opacity:0.8;">'
        f'meta: 5,00% · {delta_str}pp</div>\n'
        f'    <div style="font-size:12px; color:var(--status-churn-text); margin-top:2px; opacity:0.7;">'
        f'{n_novos_churns} novos churns</div>\n'
        '  </div>\n'
        # Em churn
        '  <div style="background:var(--status-churn-bg); border-radius:10px; padding:16px;">\n'
        '    <div style="font-size:11px; color:var(--status-churn-text); text-transform:uppercase; '
        'letter-spacing:1px; margin-bottom:8px;">Em churn</div>\n'
        f'    <div style="font-size:28px; font-weight:600; color:var(--status-churn-text);">{n_churn}</div>\n'
        f'    <div style="font-size:12px; color:var(--status-churn-text); margin-top:4px; opacity:0.8;">'
        f'{_fmt(pct_churn, 1)}% da base</div>\n'
        '  </div>\n'
        # Ativos
        '  <div style="background:var(--status-ativo-bg); border-radius:10px; padding:16px;">\n'
        '    <div style="font-size:11px; color:var(--status-ativo-text); text-transform:uppercase; '
        'letter-spacing:1px; margin-bottom:8px;">Ativos</div>\n'
        f'    <div style="font-size:28px; font-weight:600; color:var(--status-ativo-text);">{n_ativo}</div>\n'
        f'    <div style="font-size:12px; color:var(--status-ativo-text); margin-top:4px; opacity:0.8;">'
        f'{_fmt(pct_ativo, 1)}% da base</div>\n'
        '  </div>\n'
        # Pré-churn
        '  <div style="background:var(--status-prechurn-bg); border-radius:10px; padding:16px;">\n'
        '    <div style="font-size:11px; color:var(--status-prechurn-text); text-transform:uppercase; '
        'letter-spacing:1px; margin-bottom:8px;">Pré-churn</div>\n'
        f'    <div style="font-size:28px; font-weight:600; color:var(--status-prechurn-text);">{n_prechurn}</div>\n'
        f'    <div style="font-size:12px; color:var(--status-prechurn-text); margin-top:4px; opacity:0.8;">'
        f'{_fmt(pct_prechurn, 1)}% da base</div>\n'
        '  </div>\n'
        '</div>\n'
    )

    footer = (
        f'<p style="font-size:12px; color:var(--ink-30); font-style:italic; margin-top:16px;">\n'
        f'  Taxa de churn e churns novos excluem TMB Educação\n'
        f'</p>'
    )

    followup = (
        "\n\n_Quer ver o relatório completo com análise detalhada por gestor, "
        "churns novos, recuperações e pré-churn crítico?_"
    )

    return kpis + alert_html + gestor_html + footer + followup


def _to_claude_history(messages: list[dict]) -> list[dict]:
    result = []
    for msg in messages:
        role = "assistant" if msg["role"] == "assistant" else "user"
        result.append({"role": role, "content": msg["content"]})
    return result


def _build_citation(result: AnalyticsResult) -> str:
    ref = result.data_reference_date
    month_label = f"{ref.month:02d}/{ref.year}"
    source_label = result.data_source or "Metabase"
    return f"Dados de {month_label} — fonte: {source_label}"


def _serialize_result(result: AnalyticsResult) -> str:
    """Converte AnalyticsResult para JSON compacto (sem DataFrames)."""
    data = {
        "query_type": result.query_type,
        "data_reference_date": str(result.data_reference_date),
        "data_source": result.data_source,
        "warnings": result.warnings,
        "summary_stats": result.summary_stats,
        "tabular_data": result.tabular_data,
    }
    try:
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        # Identifica qual campo contém o tipo não serializável
        for field, value in data.items():
            try:
                json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError) as field_exc:
                logger.error(
                    f"ReportAgent | Falha de serialização JSON | "
                    f"campo='{field}' | erro={field_exc} | tipo={type(value).__name__}"
                )
        logger.error(f"ReportAgent | Usando default=str como fallback: {exc}")
        return json.dumps(data, ensure_ascii=False, default=str)


def _template_fallback(result: AnalyticsResult) -> str:
    """Resposta de emergência quando a API Claude está indisponível."""
    lines = ["## Resumo da Análise"]
    lines.append(f"**Tipo de análise:** {result.query_type}")
    lines.append(f"**Data de referência:** {result.data_reference_date}")
    lines.append("")

    if result.summary_stats:
        lines.append("**Dados principais:**")
        for k, v in result.summary_stats.items():
            lines.append(f"- {k}: {v}")

    if result.tabular_data:
        lines.append(f"\n_{len(result.tabular_data)} registros encontrados._")

    if result.warnings:
        lines.append("")
        for w in result.warnings:
            lines.append(f"⚠️ {w}")

    lines.append("\n_Serviço de formatação temporariamente indisponível. Dados brutos exibidos._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper de log
# ---------------------------------------------------------------------------

def _log(session_id: str, event: str, **kwargs) -> None:
    parts = [
        f"SESSION={session_id}",
        "AGENT=ReportAgent",
        f"EVENT={event}",
        *[f"{k}={v}" for k, v in kwargs.items()],
    ]
    msg = f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} | {' | '.join(parts)}"
    if event == "error":
        logger.error(msg)
    else:
        logger.info(msg)
