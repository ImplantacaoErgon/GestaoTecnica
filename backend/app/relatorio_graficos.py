"""
138ª rodada — pedido do usuário (verbatim): "o relatório gerencial contempla
as últimas novidades que incluímos no projeto, como a comparação da folha?
Gráficos das migrações (...) e Gráficos da Folha" — resposta era NÃO: o
Relatório Executivo (IA) nunca teve nenhuma capacidade de desenhar gráfico
(relatorio_pdf.py só sabe título/parágrafo/lista/tabela via ReportLab) e a
seção de Folha de Pagamento ficou travada num placeholder de "ainda sem
dados" escrito antes de existirem a Comparação Folha × Ergon e o funil de
Rubricas.

Este módulo gera, em Python puro e determinístico (a IA nunca vê nem produz
estes gráficos — só o texto ao redor deles), as mesmas visualizações que já
existem na Visão Executiva (ver frontend/index.html:
_vexDesenharEvolucaoMigracao, _vexGraficoConvergencia, _vexGraficoTipoRubrica),
como imagens PNG prontas para embutir no PDF do Relatório Executivo
(app/relatorio_pdf.py). Usa matplotlib com backend "Agg" (sem display, só
para renderizar em memória — nenhuma dependência de sistema extra além do
próprio pacote Python).

As cores reproduzem os tokens de status/categóricos do CSS do app (modo
claro — o PDF é sempre "modo claro", não tem alternância de tema):
--ok, --danger, --warn, --text/--text-muted/--text-faint, --border,
--series-1/--series-2 (ver frontend/index.html, bloco ":root").
"""
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

_COR_OK = "#1f7a4d"
_COR_DANGER = "#ab2f2f"
_COR_TEXT = "#1a2229"
_COR_TEXT_MUTED = "#5c6670"
_COR_TEXT_FAINT = "#8a939c"
_COR_BORDER = "#dde1e6"
_COR_SURFACE_2 = "#eef0f3"
_COR_SERIES_1 = "#2a78d6"
_COR_SERIES_2 = "#eb6834"

_MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _formatar_mes(mesano):
    """'2026-08' -> 'ago/26' — mesmo formato de frontend/index.html:_vexFormatarMes()."""
    y, m = mesano.split("-")
    return f"{_MESES_ABREV[int(m) - 1]}/{y[2:]}"


def _fmt_int(v):
    return f"{int(v):,}".replace(",", ".")


def _fmt_pct(v, casas=1):
    return f"{v:.{casas}f}%".replace(".", ",")


def _fig_para_png(fig, dpi=170):
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def grafico_evolucao_migracao(nome_item, ciclos):
    """ciclos: lista em ordem CRONOLÓGICA (mais antigo -> mais recente, até 5) de
    {numero, carregado, rejeitado, extraido}. Replica o gráfico de barras
    agrupadas (Migrados × Rejeitados) + total extraído e % por ciclo da Visão
    Executiva (131ª/136ª rodadas). Devolve None se não houver nenhum ciclo."""
    if not ciclos:
        return None
    n = len(ciclos)
    carregados = [int(c["carregado"] or 0) for c in ciclos]
    rejeitados = [int(c["rejeitado"] or 0) for c in ciclos]
    extraidos = [int(c["extraido"] or (carregados[i] + rejeitados[i])) for i, c in enumerate(ciclos)]

    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    width = 0.34
    x = list(range(n))
    ax.bar([i - width / 2 for i in x], carregados, width, color=_COR_OK, label="Migrados (carregados)", zorder=3)
    ax.bar([i + width / 2 for i in x], rejeitados, width, color=_COR_DANGER, label="Rejeitados", zorder=3)

    maior = max(carregados + rejeitados + [1])
    ax.set_ylim(0, maior * 1.32)
    for i in x:
        extr = extraidos[i]
        pct_mig = 100 * carregados[i] / extr if extr else None
        pct_rej = 100 * rejeitados[i] / extr if extr else None
        if pct_mig is not None:
            ax.text(i - width / 2, carregados[i] + maior * 0.02, f"{_fmt_int(carregados[i])}\n{_fmt_pct(pct_mig)}",
                     ha="center", va="bottom", fontsize=7.3, color=_COR_OK, fontweight="bold", linespacing=1.4)
        if pct_rej is not None:
            ax.text(i + width / 2, rejeitados[i] + maior * 0.02, f"{_fmt_int(rejeitados[i])}\n{_fmt_pct(pct_rej)}",
                     ha="center", va="bottom", fontsize=7.3, color=_COR_DANGER, fontweight="bold", linespacing=1.4)

    ax.set_xticks(x)
    ax.set_xticklabels([f"Ciclo {ciclos[i]['numero']}\n{_fmt_int(extraidos[i])} extraídos" for i in x],
                        fontsize=7.6, color=_COR_TEXT_FAINT)
    ax.set_title(nome_item, fontsize=10.5, color=_COR_TEXT, loc="left", fontweight="bold", pad=8)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_COR_BORDER)
    ax.tick_params(left=False, labelleft=False, bottom=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2, frameon=False, fontsize=8.2)
    fig.tight_layout()
    return _fig_para_png(fig)


def grafico_convergencia_folha(competencias, por_tiporubr):
    """Um donut por competência (Convergente/Divergente/Não programada), igual ao
    'Gráfico A' da Comparação Folha × Ergon na Visão Executiva (ver
    frontend/index.html:_vexGraficoConvergencia). `por_tiporubr`: linhas cruas
    de comparacao_folha.resumo_por_competencia() (mesana/tiporubr/total_linhas/
    total_sem_divergencia/total_nao_programada/total_divergente)."""
    if not competencias:
        return None
    por_mes = {m: {"total": 0, "conv": 0, "div": 0, "np": 0} for m in competencias}
    for l in por_tiporubr:
        r = por_mes.get(l.get("mesano"))
        if r is None:
            continue
        r["total"] += int(l.get("total_linhas") or 0)
        r["conv"] += int(l.get("total_sem_divergencia") or 0)
        r["div"] += int(l.get("total_divergente") or 0)
        r["np"] += int(l.get("total_nao_programada") or 0)

    n = len(competencias)
    fig, axes = plt.subplots(1, n, figsize=(2.3 * n, 2.7))
    if n == 1:
        axes = [axes]
    for ax, m in zip(axes, competencias):
        r = por_mes[m]
        total = r["total"]
        if total > 0:
            vals, cores = [r["conv"], r["div"], r["np"]], [_COR_OK, _COR_DANGER, _COR_TEXT_FAINT]
        else:
            vals, cores = [1], [_COR_SURFACE_2]
        ax.pie(vals, colors=cores, startangle=90, counterclock=False,
               wedgeprops=dict(width=0.38, edgecolor="white", linewidth=2))
        ax.text(0, 0, _fmt_int(total) if total else "—", ha="center", va="center",
                fontsize=12, fontweight="bold", color=_COR_TEXT)
        pConv = 100 * r["conv"] / total if total else 0
        pDiv = 100 * r["div"] / total if total else 0
        pNp = 100 * r["np"] / total if total else 0
        ax.set_title(_formatar_mes(m), fontsize=9.3, color=_COR_TEXT, pad=4, fontweight="bold")
        ax.set_xlabel(f"{pConv:.0f}% conv · {pDiv:.0f}% div · {pNp:.0f}% não prog.",
                       fontsize=7, color=_COR_TEXT_FAINT)
    fig.suptitle("Convergência geral por competência", fontsize=10.5, fontweight="bold",
                 color=_COR_TEXT, x=0.015, y=0.99, ha="left")
    # Legenda compartilhada (1 por imagem, não por donut), logo abaixo do título —
    # mesma posição de frontend/index.html:_vexGraficoConvergencia (legenda acima da
    # fileira de donuts). Os rótulos de % abaixo de cada donut já nomeiam as 3
    # categorias por extenso, mas a legenda com swatch de cor continua obrigatória
    # (skill de dataviz: legenda sempre presente com 2+ séries).
    handles = [
        Patch(color=_COR_OK, label="Convergente"),
        Patch(color=_COR_DANGER, label="Divergente"),
        Patch(color=_COR_TEXT_FAINT, label="Não programada"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=3, frameon=False, fontsize=8,
               bbox_to_anchor=(0.5, 0.92))
    fig.tight_layout(rect=(0, 0.02, 1, 0.83))
    return _fig_para_png(fig)


def grafico_tipo_rubrica_folha(competencias, por_tiporubr):
    """% convergente por Tipo de Rubrica (Vantagem × Desconto) ao longo das
    competências — 'Gráfico B' da Comparação Folha × Ergon (ver
    frontend/index.html:_vexGraficoTipoRubrica). No PDF sempre em barras
    agrupadas (até 5 competências, já limitado por resumo_por_competencia),
    mais simples de ler impresso do que uma linha."""
    if not competencias:
        return None
    mapa = {}
    for l in por_tiporubr:
        tipo = l.get("tiporubr") or "(sem tipo)"
        total_linhas = int(l.get("total_linhas") or 0)
        conv = int(l.get("total_sem_divergencia") or 0)
        mapa.setdefault(tipo, {})[l.get("mesano")] = (100 * conv / total_linhas) if total_linhas else None

    ordem_pref = ["VANTAGEM", "DESCONTO"]
    tipos = sorted(mapa.keys(), key=lambda t: (ordem_pref.index(t.upper()) if t.upper() in ordem_pref else 99, t))[:2]
    if not tipos:
        return None
    cores = [_COR_SERIES_1, _COR_SERIES_2]

    n = len(competencias)
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    width = 0.34
    x = list(range(n))
    for idx, tipo in enumerate(tipos):
        vals = [mapa[tipo].get(m) for m in competencias]
        offset = (idx - (len(tipos) - 1) / 2) * width
        xs = [i + offset for i in x]
        ys = [v if v is not None else 0 for v in vals]
        ax.bar(xs, ys, width, color=cores[idx], label=tipo.title(), zorder=3)
        for i, v in zip(xs, vals):
            if v is not None:
                ax.text(i, v + 2, f"{v:.0f}%", ha="center", va="bottom", fontsize=7.6,
                         fontweight="bold", color=cores[idx])
    ax.set_ylim(0, 118)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels([f"{v}%" for v in (0, 25, 50, 75, 100)], fontsize=7.6, color=_COR_TEXT_FAINT)
    ax.set_xticks(x)
    ax.set_xticklabels([_formatar_mes(m) for m in competencias], fontsize=8.2, color=_COR_TEXT_FAINT)
    ax.set_title("% convergente por Tipo de Rubrica", fontsize=10.5, fontweight="bold", color=_COR_TEXT,
                 loc="left", pad=8)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_COR_BORDER)
    ax.tick_params(left=False, bottom=False)
    ax.grid(axis="y", color=_COR_BORDER, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.26), ncol=2, frameon=False, fontsize=8.2)
    fig.tight_layout()
    return _fig_para_png(fig)
