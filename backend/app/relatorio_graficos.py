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


# 140ª rodada — pedido do usuário (verbatim, sobre o PDF do Dashboard criado na
# 139ª rodada): "Resumo geral trocar o que aparece na imagem 2 pela imagem 3"
# — a imagem 3 era a própria tela da Visão Executiva, com a Curva S (Avanço
# do cronograma — planejado × realizado). Replica fielmente
# frontend/index.html:_vexRenderCurvaS() (dados vindos de
# app/dashboard_pdf._curva_s(), porta Python de _vexCurvaSCalcular()): 1 linha
# cinza (Planejado, só contexto) + 1 linha laranja --series-2 (Realizado, é o
# destaque — mesmo critério de "uma série é o ponto, a outra é contexto" da
# skill de dataviz), com uma linha vertical tracejada em "hoje" quando ainda
# sobra pelo menos 1 mês de plano depois dela.
def grafico_curva_s(meses, planejado, realizado, hoje_mes, hoje_data=None):
    if not meses or len(meses) < 2:
        return None
    n = len(meses)
    x = list(range(n))
    fig, ax = plt.subplots(figsize=(7.4, 2.9))

    try:
        hoje_idx = meses.index(hoje_mes)
    except ValueError:
        hoje_idx = -1
    if 0 <= hoje_idx < n - 1:
        ax.axvline(hoje_idx, color=_COR_TEXT_FAINT, linewidth=1, linestyle=(0, (3, 3)), zorder=1)
        # 142ª rodada — pedido do usuário: mostrar a data ao lado de "hoje"
        # (só "hoje" sozinho não dizia qual dia, pra quem olha o PDF depois).
        # bbox branco: o rótulo ficou bem mais largo que só "hoje", e sem o
        # fundo branco a linha tracejada passa visível pelos vãos entre os
        # caracteres (ex: entre "07" e "/10") — mesmo cuidado já tomado nos
        # rótulos de valor no fim das linhas, um pouco acima.
        rotulo_hoje = f"hoje ({hoje_data})" if hoje_data else "hoje"
        ax.text(hoje_idx, 103, rotulo_hoje, ha="center", va="bottom", fontsize=7.6, color=_COR_TEXT_FAINT,
                zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=1.5))

    ax.plot(x, planejado, color=_COR_TEXT_FAINT, linewidth=2, zorder=3, solid_capstyle="round")
    ax.plot(x, realizado, color=_COR_SERIES_2, linewidth=2.5, zorder=4, solid_capstyle="round")
    ax.scatter([n - 1], [planejado[-1]], color=_COR_TEXT_FAINT, s=26, zorder=5, edgecolor="white", linewidth=1.2)
    ax.scatter([n - 1], [realizado[-1]], color=_COR_SERIES_2, s=26, zorder=5, edgecolor="white", linewidth=1.2)

    y_plan, y_real = planejado[-1], realizado[-1]
    if abs(y_plan - y_real) < 6:
        meio = (y_plan + y_real) / 2
        y_plan, y_real = (meio + 3, meio - 3) if planejado[-1] >= realizado[-1] else (meio - 3, meio + 3)
    # bbox branco + zorder acima da linha: sem isso, quando o trecho final da série
    # é "chato" (sem variação até o fim), a própria linha passa por cima do rótulo
    # e cria um efeito de "texto riscado" (visto na revisão da 140ª rodada).
    _rotulo_bbox = dict(facecolor="white", edgecolor="none", pad=1.5)
    ax.text(n - 1.15, y_plan, f"{planejado[-1]}%", ha="right", va="center", fontsize=8, fontweight="bold",
            color=_COR_TEXT_MUTED, zorder=6, bbox=_rotulo_bbox)
    ax.text(n - 1.15, y_real, f"{realizado[-1]}%", ha="right", va="center", fontsize=8, fontweight="bold",
            color=_COR_SERIES_2, zorder=6, bbox=_rotulo_bbox)

    passo = max(1, -(-n // 8))  # ceil(n/8)
    xticks = [i for i in x if i == 0 or i == n - 1 or i % passo == 0]
    ax.set_xticks(xticks)
    ax.set_xticklabels([_formatar_mes(meses[i]) for i in xticks], fontsize=8, color=_COR_TEXT_FAINT)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels([f"{v}%" for v in (0, 25, 50, 75, 100)], fontsize=8, color=_COR_TEXT_FAINT)
    ax.set_ylim(-4, 112)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_COR_BORDER)
    ax.grid(axis="y", color=_COR_BORDER, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(left=False, bottom=False)

    handles = [Patch(color=_COR_TEXT_FAINT, label="Planejado"), Patch(color=_COR_SERIES_2, label="Realizado")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=2, frameon=False, fontsize=8.5)
    fig.tight_layout()
    return _fig_para_png(fig)


# 140ª rodada — pedido do usuário: "NA frente de trabalho usar o gráfico como
# na imagem 4" — a imagem 4 era o próprio card "Progresso por Frente de
# Trabalho" da Visão Executiva (ver frontend/index.html:_vexRenderFrentes()):
# 1 barra horizontal por frente, com a cor PRÓPRIA da frente (frentes_trabalho.
# cor_hex, identidade categórica real, cadastrada pelo usuário — não um token
# de status), largura = % concluído, rótulo "concluídas/total" no fim da barra.
def grafico_frentes(frentes):
    """frentes: lista (JÁ na ordem a desenhar, de cima pra baixo — mesma
    ordem de frentes_trabalho.ordem usada na tela) de
    {"frente", "cor_hex", "total", "concluidas"}."""
    if not frentes:
        return None
    n = len(frentes)
    nomes = [f["frente"] for f in frentes]
    pcts = [100 * f["concluidas"] / f["total"] if f["total"] else 0 for f in frentes]
    cores = [f.get("cor_hex") or _COR_SERIES_1 for f in frentes]
    rotulos_fim = [f"{_fmt_int(f['concluidas'])}/{_fmt_int(f['total'])}" for f in frentes]

    altura = max(2.0, 0.46 * n + 0.5)
    fig, ax = plt.subplots(figsize=(7.4, altura))
    y = list(range(n))
    ax.barh(y, pcts, color=cores, height=0.58, zorder=3)
    for i, (p, rot) in enumerate(zip(pcts, rotulos_fim)):
        ax.text(min(p, 100) + 2, i, rot, va="center", fontsize=8, color=_COR_TEXT)
    ax.set_yticks(y)
    ax.set_yticklabels(nomes, fontsize=8.6, color=_COR_TEXT)
    ax.invert_yaxis()  # primeiro item da lista fica no topo, igual à tela
    ax.set_xlim(0, 112)
    ax.set_xticks([])
    for spine in ("top", "right", "bottom", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False, bottom=False)
    fig.tight_layout()
    return _fig_para_png(fig)


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
