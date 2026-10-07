"""
139ª rodada — pedido do usuário (verbatim): "Gere um PDF da pagina de
Dashboard. Sobre a migração, na hora de gerar o pdf pergunte quais até 5
tabelas devem ser gerados os gráficos no documento. Ainda sobre a migração
inclua a listagem em forma de quadro de todas as tabelas comparando os dois
últimos ciclos de cada uma e o % de rejeição do ultimo ciclo. Sobre a folha,
além do que ja é gerado no dashboard inclua as informações dos números
número e % por situação e as 10 rubricas com mais divergências."

PDF determinístico (SEM IA) da Visão Executiva — a tela que o front-end
chama de "Dashboard" (nav "📊 Dashboard" -> view "dashboard" -> renderiza
renderVisaoExecutiva(), ver frontend/index.html). Diferente do Relatório
Executivo (IA — app/relatorio_executivo.py + app/relatorio_pdf.py), que
manda esses mesmos números pra um modelo de IA escrever um texto corrido em
volta deles, este módulo NUNCA chama IA: os quadros saem direto dos números,
igual à própria tela.

Reaproveita o máximo possível do que já existe, de propósito (nada de
duplicar cálculo):
- relatorio_executivo.coletar_dados_projeto() já calcula quase tudo que a
  Visão Executiva mostra (kpis, atrasadas, resumo por frente, marcos,
  riscos, atividades master com previsão, anomalias de data fixada,
  migração de dados, folha de pagamento) — é a MESMA função usada pelo
  Relatório Executivo (IA) e por vários cards do Dashboard
  (/relatorios/atividades-master, /relatorios/anomalias-data-fixada etc.).
  Este módulo só acrescenta o que faltava pra cobrir os quadros que o
  Relatório Executivo (IA) não cobre: % esperado (hero), Financeiro,
  Pendências, Requisitos do TR e o novo ranking de rubricas com mais
  divergências.
- app/relatorio_pdf.py já tem os helpers de ReportLab (_estilos,
  _tabela_flowable, _imagem_flowable, _inline_to_reportlab) — reaproveitados
  aqui tal como estão, em vez de duplicar estilo de tabela/imagem.
- app/relatorio_graficos.py já tem os geradores de gráfico (evolução de
  migração, convergência da folha, tipo de rubrica) — reaproveitados sem
  mudança nenhuma.

Decisões confirmadas com o usuário (AskUserQuestion, 139ª rodada):
- Escopo do PDF: TUDO (todos os quadros da Visão Executiva), não só
  Migração/Folha.
- "10 rubricas com mais divergências": ranking só da competência mais
  recente de Comparação Folha × Ergon carregada (não soma várias
  competências).
"""
from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, ListFlowable, ListItem, Table, TableStyle,
    KeepTogether,
)

from . import db, relatorio_executivo, relatorio_graficos, relatorio_pdf

LIMITE_GRAFICOS_MIGRACAO = 5
LIMITE_TOP_RUBRICAS_DIVERGENTES = 10
LIMITE_ATRASADAS_NA_TABELA = 20  # a amostra de coletar_dados_projeto já vem limitada a 40; no PDF (impresso) 20 já é bastante

# 140ª rodada — pedido do usuário: "Resumo geral trocar o que aparece na
# imagem 2 pela imagem 3" (a própria Visão Executiva) / "NA folha de
# pagamento apresentar o quadro cmo na imagem 5" — os quadros "Indicador |
# Valor" em tabela viraram tiles coloridos, mesmos tokens de cor/limiares já
# usados na tela (ver frontend/index.html, COLOR_VAR/SOFT_VAR e os cálculos
# de renderVisaoExecutiva() — _TOKEN_BG/_TOKEN_FG abaixo são os valores hex
# por trás de var(--ok-soft)/var(--ok) etc., copiados do bloco :root do CSS).
_TOKEN_BG = {"ok": "#e4f3ea", "warn": "#faecda", "danger": "#faeaea", "accent": "#e6edf4", "faint": "#eef0f3"}
_TOKEN_FG = {"ok": "#1f7a4d", "warn": "#a15c0d", "danger": "#ab2f2f", "accent": "#2f5f8a", "faint": "#5c6670"}


def _token_progresso(diff_pp):
    """Mesma regra de 3 faixas da 135ª rodada (hero "Progresso geral do
    projeto"): < -10 p.p. vermelho, < 0 p.p. laranja, senão verde."""
    if diff_pp is None:
        return "faint"
    if diff_pp < -10:
        return "danger"
    if diff_pp < 0:
        return "warn"
    return "ok"


def _token_limiar(pct):
    """Mesmo critério dos tiles cruzados de renderVisaoExecutiva() (Migração,
    Rubricas, Financeiro): null -> cinza, >=90% verde, >=50% azul, senão laranja."""
    if pct is None:
        return "faint"
    if pct >= 90:
        return "ok"
    if pct >= 50:
        return "accent"
    return "warn"


def _token_limiar_requisitos(pct):
    """Requisitos usa limiares um pouco mais tolerantes na tela (>=80/>=50)."""
    if pct is None:
        return "faint"
    if pct >= 80:
        return "ok"
    if pct >= 50:
        return "accent"
    return "warn"


def _token_alerta(n):
    return "warn" if n else "faint"


def _token_aderencia_tile(diff_pp):
    """Regra do TILE cruzado "Aderência ao prazo" em renderVisaoExecutiva()
    — 2 faixas (diferente da regra de 3 faixas do HERO, _token_progresso
    acima, que usa o corte de -10 p.p.): negativo = vermelho, positivo =
    verde, zero = cinza."""
    if not diff_pp:
        return "faint"
    return "danger" if diff_pp < 0 else "ok"


class DashboardPdfError(Exception):
    pass


# ----------------------------------------------------------------- formatação

def _fmt_int(v):
    return f"{int(v or 0):,}".replace(",", ".")


def _fmt_pct(v, casas=1, sinal=False):
    if v is None:
        return "—"
    txt = f"{v:+.{casas}f}%" if sinal else f"{v:.{casas}f}%"
    return txt.replace(".", ",")


def _fmt_pp(v, casas=1):
    """Pontos percentuais (ex: aderência ao prazo) — sinal sempre, SEM "%" (a
    unidade já vem escrita como "p.p." ao lado, diferente de uma taxa)."""
    if v is None:
        return "—"
    return f"{v:+.{casas}f}".replace(".", ",")


def _fmt_moeda(v):
    if v is None:
        return "—"
    s = f"{float(v):,.2f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {s}"


def _fmt_data_br(d):
    if not d:
        return "—"
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return d.strftime("%d/%m/%Y")


def _fmt_evolucao(evolucao_percentual, evolucao_observacao):
    """Mesmo formato já usado no PROMPT_TEMPLATE do Relatório Executivo (IA)
    — "+12,3%"/"-8,5%" (_tabela_flowable/_celula_tabela de relatorio_pdf.py
    reconhece esse padrão e pinta de verde/vermelho automaticamente) — ou o
    texto de "evolucao_observacao" quando não há o que comparar."""
    if evolucao_percentual is not None:
        return _fmt_pct(evolucao_percentual, casas=1, sinal=True)
    return evolucao_observacao or "—"


# ----------------------------------------------------- dados extras (coleta)

def _pct_esperado(projeto_id, total_atividades):
    """Mesmo critério de frontend/index.html:calcularKpisCronograma() —
    "deveria estar concluída hoje": toda atividade cujo Fim previsto no
    PLANO (dtfim_prev) já passou ou é hoje, independente do status real."""
    if not total_atividades:
        return 0, 0
    row = db.fetch_one(f"""
        SELECT count(*) AS n FROM atividades
        WHERE projeto_id = {db.q(projeto_id)} AND dtfim_prev IS NOT NULL AND dtfim_prev <= CURRENT_DATE
    """)
    deveria = (row or {}).get("n") or 0
    return deveria, round(100 * deveria / total_atividades)


def _financeiro(projeto_id):
    """Mesmo cálculo de frontend/index.html:_vexRenderFinanceiro()."""
    row = db.fetch_one(f"""
        SELECT
          COALESCE(SUM(valor_total), 0) AS soma_total,
          COALESCE(SUM(valor_liquido), 0) AS soma_liquido,
          COALESCE(SUM(valor_liquido) FILTER (WHERE data_pagamento IS NOT NULL), 0) AS soma_paga,
          COUNT(*) FILTER (WHERE data_pagamento IS NULL AND data_pagamento_previsao IS NOT NULL
                            AND data_pagamento_previsao < CURRENT_DATE) AS atrasadas,
          COUNT(*) AS total_faturas
        FROM faturas WHERE projeto_id = {db.q(projeto_id)}
    """)
    if not row or not row["total_faturas"]:
        return {"disponivel": False}
    soma_liquido = float(row["soma_liquido"] or 0)
    soma_paga = float(row["soma_paga"] or 0)
    return {
        "disponivel": True,
        "soma_total": float(row["soma_total"] or 0), "soma_liquido": soma_liquido, "soma_paga": soma_paga,
        "pct_recebido": round(100 * soma_paga / soma_liquido) if soma_liquido else 0,
        "atrasadas": row["atrasadas"] or 0,
    }


def _pendencias(projeto_id):
    """Mesmo cálculo de frontend/index.html:_vexRenderRiscosPendencias() (metade
    "Pendências" do quadro — a metade "Riscos" já vem em riscos_abertos, de
    relatorio_executivo.coletar_dados_projeto())."""
    row = db.fetch_one(f"""
        SELECT
          COUNT(*) FILTER (WHERE status IN ('Aberta', 'Em andamento')) AS abertas,
          COUNT(*) FILTER (WHERE status NOT IN ('Resolvida', 'Cancelada') AND data_limite < CURRENT_DATE) AS atrasadas
        FROM pendencias WHERE projeto_id = {db.q(projeto_id)}
    """)
    return {"abertas": (row or {}).get("abertas") or 0, "atrasadas": (row or {}).get("atrasadas") or 0}


def _requisitos(projeto_id):
    """Mesmo cálculo de frontend/index.html:_vexRenderRequisitos()."""
    rows = db.fetch_all(f"""
        SELECT COALESCE(atendimento::text, '(sem atendimento)') AS atendimento, COUNT(*) AS total
        FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)} GROUP BY atendimento
    """)
    total = sum(r["total"] for r in rows)
    if not total:
        return {"disponivel": False}
    por_atend = {r["atendimento"]: r["total"] for r in rows}
    atendidos = por_atend.get("Nativo", 0) + por_atend.get("Parcial", 0)
    ordem = ["Nativo", "Parcial", "Customizado", "A analisar"]
    return {
        "disponivel": True, "total": total,
        "pct_atendido": round(100 * atendidos / total),
        "por_atendimento": [{"atendimento": a, "total": por_atend.get(a, 0)} for a in ordem if por_atend.get(a)],
    }


def _marcos_pendentes_total(projeto_id):
    """Mesmo critério de frontend/index.html:renderVisaoExecutiva() —
    "marcosPendentes" = MARCOS.filter(m=>!m.data_real).length (TODOS os
    pendentes, não só os próximos 8 de marcos_proximos)."""
    row = db.fetch_one(f"SELECT count(*) AS n FROM marcos WHERE projeto_id = {db.q(projeto_id)} AND data_real IS NULL")
    return (row or {}).get("n") or 0


def _curva_s(projeto_id):
    """Porta Python, fiel, de frontend/index.html:_vexCurvaSCalcular() — curva
    de avanço acumulado (Planejado × Realizado) mês a mês, janela dos últimos
    18 meses. "Planejado" conta toda atividade cujo Fim Previsto (dtfim_prev)
    já tenha passado até aquele mês; "Realizado" conta só a concluída que
    TAMBÉM tem Data Fim Real preenchida (mesma ressalva da tela: concluída
    sem essa data não entra aqui, mesmo contando como "Concluída" alhures)."""
    atividades = db.fetch_all(f"""
        SELECT dtfim_prev, dtfim_real, status FROM atividades WHERE projeto_id = {db.q(projeto_id)}
    """)
    com_prazo = [a for a in atividades if a.get("dtfim_prev")]
    if not com_prazo:
        return None

    def ym(v):
        return str(v)[:7]

    hoje = date.today()
    hoje_iso = hoje.isoformat()
    min_mes = min(ym(a["dtfim_prev"]) for a in com_prazo)
    max_mes = hoje_iso[:7]
    for a in atividades:
        if a.get("dtfim_prev") and ym(a["dtfim_prev"]) > max_mes:
            max_mes = ym(a["dtfim_prev"])
        if a.get("dtfim_real") and ym(a["dtfim_real"]) > max_mes:
            max_mes = ym(a["dtfim_real"])

    y, m = (int(v) for v in min_mes.split("-"))
    y_max, m_max = (int(v) for v in max_mes.split("-"))
    meses = []
    while y < y_max or (y == y_max and m <= m_max):
        meses.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    janela = meses[-18:] if len(meses) > 18 else meses

    total = len(atividades)
    concluida_familia = relatorio_executivo.STATUS_FAMILIA_CONCLUIDA
    planejado, realizado = [], []
    for mes in janela:
        corte = mes + "-31"
        n_plan = sum(1 for a in atividades if a.get("dtfim_prev") and str(a["dtfim_prev"]) <= corte)
        planejado.append(round(100 * n_plan / total) if total else 0)
        n_real = sum(1 for a in atividades
                     if a["status"] in concluida_familia and a.get("dtfim_real") and str(a["dtfim_real"]) <= corte)
        realizado.append(round(100 * n_real / total) if total else 0)
    concluidas_sem_data = sum(
        1 for a in atividades if a["status"] in concluida_familia and not a.get("dtfim_real")
    )
    return {"meses": janela, "planejado": planejado, "realizado": realizado,
            "hoje_mes": hoje_iso[:7], "hoje_data": _fmt_data_br(hoje),
            "concluidas_sem_data": concluidas_sem_data}


def _frentes_com_cor(projeto_id):
    """Mesma base de resumo_por_frente de relatorio_executivo.py, mas com a
    cor PRÓPRIA de cada frente (frentes_trabalho.cor_hex) e já na ordem de
    exibição da tela (frentes_trabalho.ordem, nome como desempate — mesmo
    critério da 137ª rodada em frontend/index.html:_vexRenderFrentes()),
    pro gráfico de barras (ver app/relatorio_graficos.grafico_frentes)."""
    linhas = db.fetch_all(f"""
        SELECT
          COALESCE(f.nome, 'Sem frente de trabalho') AS frente,
          f.cor_hex,
          COALESCE(f.ordem, 0) AS ordem,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE a.status IN (
            'Concluída', 'Concluída com atraso', 'Concluída com esforço maior', 'Concluída com atraso e esforço maior'
          )) AS concluidas
        FROM atividades a
        LEFT JOIN frentes_trabalho f ON f.id = a.frente_trabalho_id
        WHERE a.projeto_id = {db.q(projeto_id)}
        GROUP BY f.nome, f.cor_hex, f.ordem
        ORDER BY ordem, frente
    """)
    return linhas


def _pct_migracao_carregado(migracao_de_dados):
    """Mesmo cálculo do tile cruzado "Migração — % carregado" de
    renderVisaoExecutiva() — soma de meta (qtd_registros_estimada) × soma do
    carregado no ÚLTIMO ciclo de cada item (mesmos números já usados na
    tabela comparativa, nenhuma consulta nova)."""
    itens = (migracao_de_dados or {}).get("itens") or []
    total_estimado = sum(it.get("meta") or 0 for it in itens)
    total_carregado = sum((it.get("ultimo_ciclo") or {}).get("carregado") or 0 for it in itens)
    return round(100 * total_carregado / total_estimado) if total_estimado else None


def _pct_rubricas_homologadas(folha_de_pagamento):
    """Mesmo cálculo do tile cruzado "Rubricas — % homologadas"."""
    funil = (folha_de_pagamento or {}).get("rubricas_funil") or {}
    total = funil.get("total_levantadas") or 0
    if not total:
        return None
    homologadas = next((s["total"] for s in funil.get("por_status") or [] if s["status"] == "Homologada"), 0)
    return round(100 * homologadas / total)


def _top_rubricas_divergentes(projeto_id, limite=LIMITE_TOP_RUBRICAS_DIVERGENTES):
    """"10 rubricas com mais divergências" (139ª rodada) — ranking pela
    QUANTIDADE de linhas divergentes (mesmo critério de impacto absoluto já
    usado em comparacao_folha.dashboard()/por_empresa_rubrica: uma rubrica
    com 3.000 linhas divergentes pesa mais no trabalho de correção do que
    uma com 2 linhas 100% divergentes), olhando só pra competência MAIS
    RECENTE carregada — confirmado com o usuário (AskUserQuestion): não soma
    várias competências, reflete só a situação mais atual."""
    ultima = db.fetch_one(f"SELECT MAX(mesano) AS m FROM comparacao_folha WHERE projeto_id = {db.q(projeto_id)}")
    mesano = (ultima or {}).get("m")
    if not mesano:
        return {"disponivel": False}
    linhas = db.fetch_all(f"""
        SELECT
          rubrica_ergon, MAX(rubrica_nome_ergon) AS nome, COALESCE(MAX(tiporubr), '(sem tipo)') AS tipo,
          COUNT(*) AS total_linhas,
          COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE') AS total_divergente
        FROM comparacao_folha
        WHERE projeto_id = {db.q(projeto_id)} AND mesano = {db.q(mesano)} AND rubrica_ergon IS NOT NULL
        GROUP BY rubrica_ergon
        HAVING COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE') > 0
        ORDER BY total_divergente DESC, total_linhas DESC
        LIMIT {int(limite)}
    """)
    for l in linhas:
        l["pct_divergente"] = round(100 * l["total_divergente"] / l["total_linhas"], 1) if l["total_linhas"] else 0
    mesano_str = mesano.isoformat() if hasattr(mesano, "isoformat") else str(mesano)
    return {"disponivel": True, "competencia": mesano_str[:7], "linhas": linhas}


def coletar_dados_dashboard(projeto_id):
    """Monta o retrato completo da Visão Executiva — reaproveita
    relatorio_executivo.coletar_dados_projeto() (ver docstring do módulo) e
    acrescenta o que faltava: % esperado/aderência (hero), Financeiro,
    Pendências, Requisitos do TR e o ranking de rubricas com mais
    divergências. Levanta relatorio_executivo.RelatorioExecutivoError se o
    projeto não existir (mesma excessão, não precisa de duas)."""
    dados = relatorio_executivo.coletar_dados_projeto(projeto_id)
    total_atividades = dados["kpis"]["total_atividades"]
    deveria, pct_deveria = _pct_esperado(projeto_id, total_atividades)
    dados["kpis"]["deveria_estar_concluido_hoje"] = deveria
    dados["kpis"]["percentual_deveria_estar_concluido_hoje"] = pct_deveria
    # 140ª rodada — revisão visual: percentual_concluido_geral vem de
    # coletar_dados_projeto() com 1 casa decimal (ex: 68,7%), mas a tela
    # (calcularKpisCronograma()) arredonda pra inteiro ANTES de tirar a
    # diferença (Math.round primeiro, subtração depois) — então "Aderência ao
    # prazo" da tela nunca bate com round(68,7 - 48, 1) aqui. Pra o Resumo
    # Geral do PDF mostrar os MESMOS números da tela (objetivo desta seção,
    # ver _secao_resumo_geral), replica esse arredondamento: % inteiro antes,
    # diferença de inteiros depois.
    pct_concluido_int = round(100 * dados["kpis"]["concluidas"] / total_atividades) if total_atividades else 0
    dados["kpis"]["percentual_concluido_geral_dashboard"] = pct_concluido_int
    dados["kpis"]["aderencia_ao_prazo_pp"] = pct_concluido_int - pct_deveria
    dados["kpis"]["marcos_pendentes"] = _marcos_pendentes_total(projeto_id)

    dados["financeiro"] = _financeiro(projeto_id)
    dados["pendencias"] = _pendencias(projeto_id)
    dados["requisitos_tr"] = _requisitos(projeto_id)
    dados["folha_de_pagamento"]["top_rubricas_divergentes"] = _top_rubricas_divergentes(projeto_id)

    # 140ª rodada — dados extras só pro novo visual do Resumo Geral/Frentes
    # (tiles coloridos + gráficos, ver docstring das seções no final do arquivo).
    dados["_curva_s"] = _curva_s(projeto_id)
    dados["_frentes_com_cor"] = _frentes_com_cor(projeto_id)
    dados["kpis"]["pct_migracao_carregado"] = _pct_migracao_carregado(dados.get("migracao_de_dados"))
    dados["kpis"]["pct_rubricas_homologadas"] = _pct_rubricas_homologadas(dados.get("folha_de_pagamento"))
    return dados


# --------------------------------------------------------------- montar o PDF

def _tabela(flow, estilos, cabecalho, linhas_dados):
    """cabecalho: lista de strings; linhas_dados: lista de listas de
    strings já formatadas. Monta as linhas "cruas" de uma tabela markdown e
    reaproveita relatorio_pdf._tabela_flowable — mesmo estilo visual (cabeçalho
    escuro, zebra, grade, colorir evolução) de toda tabela deste sistema."""
    linhas = ["| " + " | ".join(cabecalho) + " |", "|" + "---|" * len(cabecalho)]
    for d in linhas_dados:
        linhas.append("| " + " | ".join(str(c).replace("|", "/") for c in d) + " |")
    flow.append(relatorio_pdf._tabela_flowable(linhas, estilos))
    flow.append(Spacer(1, 10))


def _lista(flow, estilos, itens):
    flow.append(ListFlowable(
        [ListItem(Paragraph(relatorio_pdf._inline_to_reportlab(i), estilos["item"]), leftIndent=8) for i in itens],
        bulletType="bullet", start="•", leftIndent=14, spaceBefore=2, spaceAfter=8,
    ))
    flow.append(Spacer(1, 4))


def _tile_row(flow, estilos, tiles, tam_valor=14, padding=9):
    """tiles: lista de {"valor": str, "rotulo": str, "token": "ok"/"warn"/
    "danger"/"accent"/"faint" (opcional, default "faint")} — monta uma linha
    de tiles coloridos (valor em destaque + rótulo abaixo), mesmo efeito
    visual dos quadros ".kpi"/".vex-status-tile" da Visão Executiva (ver
    frontend/index.html), usando um Table de 1 linha com fundo por célula
    (ReportLab não tem "card" nativo — a célula de tabela com padding e
    cor de fundo é o jeito mais simples de reproduzir o mesmo efeito)."""
    if not tiles:
        return
    n = len(tiles)
    gap = 6  # pt — coluna "vazia" entre tiles, pro mesmo efeito de gap:10px dos .kpi da tela
    largura_tile = (relatorio_pdf._LARGURA_UTIL - gap * (n - 1)) / n
    col_widths = []
    linha = []
    for i, t in enumerate(tiles):
        linha.append(Paragraph(
            f'<font size="{tam_valor}" color="{_TOKEN_FG.get(t.get("token", "faint"), _TOKEN_FG["faint"])}">'
            f'<b>{t["valor"]}</b></font><br/><font size="7" color="#5c6670">{t["rotulo"].upper()}</font>',
            estilos["tabela_cel"],
        ))
        col_widths.append(largura_tile)
        if i < n - 1:
            linha.append("")
            col_widths.append(gap)
    tabela = Table([linha], colWidths=col_widths)
    estilo = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), padding), ("RIGHTPADDING", (0, 0), (-1, -1), padding),
        ("TOPPADDING", (0, 0), (-1, -1), padding), ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
    ]
    for i, t in enumerate(tiles):
        col = i * 2
        estilo.append(("BACKGROUND", (col, 0), (col, 0), colors.HexColor(_TOKEN_BG.get(t.get("token", "faint"), _TOKEN_BG["faint"]))))
    tabela.setStyle(TableStyle(estilo))
    flow.append(tabela)
    flow.append(Spacer(1, 10))


def _secao_resumo_geral(flow, estilos, dados):
    """140ª rodada — pedido do usuário: "Resumo geral trocar o que aparece na
    imagem 2 pela imagem 3" (a imagem 3 era a própria Visão Executiva) — saiu
    da tabela "Indicador | Valor" e virou o mesmo visual da tela: hero (2
    números grandes) + sub-kpis + a fileira de tiles cruzados por módulo +
    o gráfico de Avanço do Cronograma (Curva S)."""
    flow.append(Paragraph("Resumo Geral", estilos["h2"]))
    k = dados["kpis"]
    aderencia = k["aderencia_ao_prazo_pp"]

    _tile_row(flow, estilos, [
        {"valor": f"{k['percentual_concluido_geral_dashboard']}%", "rotulo": "Progresso geral do projeto",
         "token": _token_progresso(aderencia)},
        {"valor": f"{k['percentual_deveria_estar_concluido_hoje']}%", "rotulo": "% esperado atual", "token": "faint"},
    ], tam_valor=20)

    _tile_row(flow, estilos, [
        {"valor": f"{_fmt_int(k['concluidas'])}/{_fmt_int(k['total_atividades'])}", "rotulo": "Atividades", "token": "faint"},
        {"valor": _fmt_int(k["em_andamento"]), "rotulo": "Em andamento", "token": "faint"},
        {"valor": _fmt_int(k["atrasadas"]), "rotulo": "Atrasadas", "token": _token_alerta(k["atrasadas"])},
        {"valor": _fmt_int(k["no_caminho_critico_do_plano"]), "rotulo": "No caminho crítico", "token": _token_alerta(k["no_caminho_critico_do_plano"])},
        {"valor": _fmt_int(k.get("marcos_pendentes")), "rotulo": "Marcos pendentes", "token": "faint"},
    ], tam_valor=13)

    riscos_pend_abertos = len(dados.get("riscos_abertos") or []) + ((dados.get("pendencias") or {}).get("abertas") or 0)
    pct_mig, pct_rub = k.get("pct_migracao_carregado"), k.get("pct_rubricas_homologadas")
    pct_fin = (dados.get("financeiro") or {}).get("pct_recebido") if (dados.get("financeiro") or {}).get("disponivel") else None
    pct_req = (dados.get("requisitos_tr") or {}).get("pct_atendido") if (dados.get("requisitos_tr") or {}).get("disponivel") else None
    # padding=6 (em vez do default 9): com 6 tiles na fileira, a largura útil de
    # texto por tile fica justa demais com o padding default e algumas palavras
    # ("HOMOLOGADAS", "+20,7 p.p.") quebram no meio — visto na revisão visual
    # da 140ª rodada. Com padding=6 tudo cabe em uma linha.
    _tile_row(flow, estilos, [
        {"valor": f"{_fmt_pp(aderencia, casas=0)} p.p.", "rotulo": "Aderência ao prazo", "token": _token_aderencia_tile(aderencia)},
        {"valor": f"{pct_mig}%" if pct_mig is not None else "—", "rotulo": "Migração — % carregado", "token": _token_limiar(pct_mig)},
        {"valor": f"{pct_rub}%" if pct_rub is not None else "—", "rotulo": "Rubricas — % homologadas", "token": _token_limiar(pct_rub)},
        {"valor": f"{pct_fin}%" if pct_fin is not None else "—", "rotulo": "Financeiro — % recebido", "token": _token_limiar(pct_fin)},
        {"valor": _fmt_int(riscos_pend_abertos), "rotulo": "Riscos + Pendências em aberto", "token": _token_alerta(riscos_pend_abertos)},
        {"valor": f"{pct_req}%" if pct_req is not None else "—", "rotulo": "Requisitos — % atendidos", "token": _token_limiar_requisitos(pct_req)},
    ], tam_valor=13, padding=6)

    curva = dados.get("_curva_s")
    flow.append(Paragraph("Avanço do cronograma — planejado × realizado (acumulado)", estilos["h3"]))
    if not curva:
        flow.append(Paragraph("Sem atividades com prazo previsto suficiente para montar a curva de avanço.", estilos["corpo"]))
    else:
        img = relatorio_pdf._imagem_flowable(relatorio_graficos.grafico_curva_s(
            curva["meses"], curva["planejado"], curva["realizado"], curva["hoje_mes"], curva.get("hoje_data"),
        ))
        if img is not None:
            flow.append(img)
        if curva.get("concluidas_sem_data"):
            n = curva["concluidas_sem_data"]
            flow.append(Paragraph(
                f"Atenção: {n} atividade{'s' if n>1 else ''} concluída{'s' if n>1 else ''} sem Data Fim Real preenchida "
                f"não entra{'m' if n>1 else ''} nesta curva.",
                estilos["rodape"],
            ))
        flow.append(Spacer(1, 6))


def _secao_frentes(flow, estilos, dados):
    """140ª rodada — pedido do usuário: "NA frente de trabalho usar o gráfico
    como na imagem 4" — saiu da tabela e virou o mesmo gráfico de barras
    horizontais (1 por frente, cor própria da frente) da Visão Executiva."""
    titulo = Paragraph("Progresso por Frente de Trabalho", estilos["h2"])
    frentes = [f for f in (dados.get("_frentes_com_cor") or []) if f["total"]]
    if not frentes:
        flow.append(titulo)
        flow.append(Paragraph("Nenhuma frente de trabalho cadastrada.", estilos["corpo"]))
        return
    img = relatorio_pdf._imagem_flowable(relatorio_graficos.grafico_frentes(frentes))
    if img is not None:
        # KeepTogether: sem isso o título pode cair sozinho no fim de uma
        # página e o gráfico "pular" pra próxima (visto na revisão visual da
        # 140ª rodada).
        flow.append(KeepTogether([titulo, img, Spacer(1, 6)]))
    else:
        flow.append(titulo)


def _secao_atrasos_bloqueios(flow, estilos, dados):
    flow.append(Paragraph("Atrasos e Bloqueios", estilos["h2"]))
    total_atrasadas = dados.get("atividades_atrasadas_total") or 0
    amostra = dados.get("atividades_atrasadas_amostra") or []
    if not total_atrasadas:
        flow.append(Paragraph("Nenhuma atividade atrasada no momento.", estilos["corpo"]))
    else:
        mostrando = amostra[:LIMITE_ATRASADAS_NA_TABELA]
        if total_atrasadas > len(mostrando):
            flow.append(Paragraph(
                f"Mostrando as {len(mostrando)} atividades mais atrasadas, de um total de {total_atrasadas}.",
                estilos["corpo"],
            ))
        _tabela(flow, estilos, ["Código", "Atividade", "Frente", "Fim previsto", "Atraso", "Responsáveis"], [
            [a.get("codigo_wbs") or "—", a["nome"], a["frente"], _fmt_data_br(a.get("fim_previsto")),
             f"{a['dias_atraso']}d", a.get("responsaveis") or "—"]
            for a in mostrando
        ])
    bloqueios = dados.get("bloqueios_ativos") or []
    if bloqueios:
        flow.append(Paragraph("Atividades bloqueadas:", estilos["h3"]))
        _lista(flow, estilos, [f"{b.get('codigo_wbs') or '—'} — {b['nome']} ({b['frente']})" for b in bloqueios])


def _secao_atividades_master(flow, estilos, dados):
    flow.append(Paragraph("Previsão da(s) Atividade(s) Master", estilos["h2"]))
    master = dados.get("atividades_master") or []
    if not master:
        flow.append(Paragraph(
            "Nenhuma atividade foi marcada como “atividade master” (entregável mestre do projeto, "
            "ex: Folha definitiva) — sem essa marcação não é possível estimar uma data de conclusão do projeto.",
            estilos["corpo"],
        ))
        return
    _tabela(flow, estilos, ["Atividade", "Status", "Fim previsto (plano)", "Fim projetado", "Confiança", "Atraso (dias úteis)"], [
        [m["nome"], m["status"], _fmt_data_br(m.get("fim_previsto_no_plano")), _fmt_data_br(m.get("fim_projetado")),
         m.get("confianca_da_previsao") or "—",
         str(m["atraso_projetado_dias_uteis"]) if m.get("atraso_projetado_dias_uteis") is not None else "—"]
        for m in master
    ])


def _secao_marcos(flow, estilos, dados):
    flow.append(Paragraph("Marcos", estilos["h2"]))
    atrasados = dados.get("marcos_atrasados") or []
    proximos = dados.get("marcos_proximos") or []
    if atrasados:
        flow.append(Paragraph("Marcos atrasados:", estilos["h3"]))
        _lista(flow, estilos, [f"{m['nome']} — previsto para {_fmt_data_br(m['data_prevista'])}" for m in atrasados])
    flow.append(Paragraph("Próximos marcos:", estilos["h3"]))
    if proximos:
        _lista(flow, estilos, [f"{m['nome']} — {_fmt_data_br(m['data_prevista'])}" for m in proximos])
    else:
        flow.append(Paragraph("Nenhum marco pendente.", estilos["corpo"]))


def _secao_anomalias(flow, estilos, dados):
    anomalias = dados.get("anomalias_data_fixada") or []
    if not anomalias:
        return  # mesma UX da tela (card só aparece quando há alguma) — não poluir o PDF com uma seção vazia
    flow.append(Paragraph("Anomalias de Data Fixada", estilos["h2"]))
    flow.append(Paragraph(
        "Atividades com a Data de Execução Fixada em conflito com o que uma predecessora exige hoje — "
        "o sistema NÃO altera essas datas sozinho; precisam de decisão manual.",
        estilos["corpo"],
    ))
    _tabela(flow, estilos, ["Atividade", "Predecessora", "Tipo", "Data fixada", "Data exigida", "Diferença"], [
        [f"{a.get('codigo_wbs') or '—'} {a['nome']}", f"{a.get('predecessora_codigo_wbs') or '—'} {a['predecessora_nome']}",
         a["tipo"], _fmt_data_br(a["data_fixada_atual"]), _fmt_data_br(a["data_exigida_pela_dependencia"]),
         f"{a['dias_uteis_de_diferenca']}d"]
        for a in anomalias
    ])


def _secao_riscos_pendencias(flow, estilos, dados):
    flow.append(Paragraph("Riscos & Pendências", estilos["h2"]))
    riscos = dados.get("riscos_abertos") or []
    pend = dados.get("pendencias") or {}
    altos = sum(1 for r in riscos if r.get("probabilidade") == "Alto" or r.get("impacto") == "Alto")
    _tabela(flow, estilos, ["Indicador", "Valor"], [
        ["Riscos abertos", _fmt_int(len(riscos))],
        ["Riscos de alto impacto/probabilidade", _fmt_int(altos)],
        ["Pendências em aberto", _fmt_int(pend.get("abertas"))],
        ["Pendências atrasadas", _fmt_int(pend.get("atrasadas"))],
    ])
    if riscos:
        flow.append(Paragraph("Riscos abertos:", estilos["h3"]))
        _tabela(flow, estilos, ["Descrição", "Categoria", "Probabilidade", "Impacto"], [
            [r["descricao"], r.get("categoria") or "—", r.get("probabilidade") or "—", r.get("impacto") or "—"]
            for r in riscos
        ])


def _secao_financeiro(flow, estilos, dados):
    flow.append(Paragraph("Financeiro", estilos["h2"]))
    fin = dados.get("financeiro") or {}
    if not fin.get("disponivel"):
        flow.append(Paragraph("Nenhuma fatura cadastrada.", estilos["corpo"]))
        return
    _tabela(flow, estilos, ["Indicador", "Valor"], [
        ["Valor total faturado", _fmt_moeda(fin["soma_total"])],
        ["Valor líquido", _fmt_moeda(fin["soma_liquido"])],
        ["Valor líquido recebido", f"{_fmt_moeda(fin['soma_paga'])} ({fin['pct_recebido']}%)"],
        ["Faturas atrasadas", _fmt_int(fin["atrasadas"])],
    ])


def _secao_requisitos(flow, estilos, dados):
    flow.append(Paragraph("Requisitos do Termo de Referência", estilos["h2"]))
    req = dados.get("requisitos_tr") or {}
    if not req.get("disponivel"):
        flow.append(Paragraph("Nenhum requisito levantado.", estilos["corpo"]))
        return
    flow.append(Paragraph(
        f"{req['pct_atendido']}% dos requisitos atendidos nativamente ou com parametrização "
        f"({_fmt_int(req['total'])} requisitos levantados no total).",
        estilos["corpo"],
    ))
    _tabela(flow, estilos, ["Atendimento", "Total"], [[a["atendimento"], _fmt_int(a["total"])] for a in req["por_atendimento"]])


def _secao_migracao(flow, estilos, dados, itens_grafico_ids):
    flow.append(Paragraph("Migração de Dados", estilos["h2"]))
    mig = dados.get("migracao_de_dados") or {}
    itens = mig.get("itens") or []
    if not itens:
        flow.append(Paragraph("Nenhum item de migração cadastrado.", estilos["corpo"]))
        return
    resumo = mig.get("resumo") or {}
    flow.append(Paragraph(
        f"Total carregado no último ciclo (todos os itens): <b>{_fmt_int(resumo.get('total_carregado_ultimo_ciclo'))}</b>"
        + (f" — evolução global de {_fmt_pct(resumo['evolucao_percentual_global'], sinal=True)} "
           f"em relação ao ciclo anterior (itens comparáveis)."
           if resumo.get("evolucao_percentual_global") is not None else "."),
        estilos["corpo"],
    ))
    _tabela(flow, estilos, ["Tabela do legado", "Destino", "Carregado (ciclo anterior)", "Carregado (último ciclo)",
                            "Evolução", "% Rejeição (último ciclo)"], [
        [it["tabela_legado"], it["tabela_destino"],
         _fmt_int(it["ciclo_anterior"]["carregado"]) if it.get("ciclo_anterior") else "—",
         _fmt_int(it["ultimo_ciclo"]["carregado"]) if it.get("ultimo_ciclo") else "—",
         _fmt_evolucao(it.get("evolucao_percentual"), it.get("evolucao_observacao")),
         _fmt_pct(it["ultimo_ciclo"].get("percentual_rejeicao")) if it.get("ultimo_ciclo") else "—"]
        for it in itens
    ])

    # Gráficos só dos itens escolhidos pelo usuário (até 5 — ver modal no front-end,
    # "quais tabelas devem ter gráfico no documento"), e só se o item de fato tiver
    # algum ciclo registrado (ciclos_recentes) pra desenhar.
    itens_grafico_ids = set(itens_grafico_ids or [])
    itens_com_grafico = [it for it in itens if it.get("_item_id") in itens_grafico_ids and it.get("ciclos_recentes")]
    if itens_com_grafico:
        flow.append(Paragraph("Gráficos de evolução por item (ciclos mais recentes)", estilos["h3"]))
        for it in itens_com_grafico:
            png = relatorio_graficos.grafico_evolucao_migracao(it["tabela_legado"], it["ciclos_recentes"])
            img = relatorio_pdf._imagem_flowable(png)
            if img is not None:
                flow.append(img)
                flow.append(Spacer(1, 10))


def _secao_folha(flow, estilos, dados):
    flow.append(Paragraph("Folha de Pagamento", estilos["h2"]))
    folha = dados.get("folha_de_pagamento") or {}
    funil = folha.get("rubricas_funil") or {}
    if not funil.get("total_levantadas"):
        flow.append(Paragraph("Nenhuma rubrica levantada ainda.", estilos["corpo"]))
        return
    # 140ª rodada — pedido do usuário: "NA folha de pagamento apresentar o
    # quadro cmo na imagem 5" (a imagem 5 era o próprio card "Folha de
    # Pagamento" da Visão Executiva — fileira de tiles, sem cor semântica,
    # igual ao card #vex-folha da tela) — saiu da tabela "Status | Total".
    _tile_row(flow, estilos, [
        {"valor": _fmt_int(funil["total_levantadas"]), "rotulo": "Total levantadas", "token": "faint"},
    ] + [
        {"valor": _fmt_int(s["total"]), "rotulo": s["status"], "token": "faint"} for s in funil["por_status"]
    ], tam_valor=13, padding=6)  # padding=6: rótulos de status (ex: "Em levantamento") são longos

    if not folha.get("comparacao_disponivel"):
        flow.append(Paragraph(
            folha.get("observacao") or "Nenhuma competência de Comparação Folha × Ergon importada ainda.",
            estilos["corpo"],
        ))
        return

    competencias = folha["competencias"]

    # 139ª rodada — pedido do usuário: "além do que já é gerado no dashboard
    # inclua as informações dos números e % por situação": a tela por padrão
    # mostra só os GRÁFICOS da Comparação Folha × Ergon (137ª rodada); aqui no
    # PDF entram as duas coisas — as tabelas de números (abaixo) E os gráficos
    # (mais adiante), sem exigir nenhum clique.
    flow.append(Paragraph("Comparação Folha × Ergon — por Situação", estilos["h3"]))
    _tabela(flow, estilos, ["Situação"] + competencias, [
        [l["situacao"]] + [f"{_fmt_int(c['total'])} ({c['percentual_do_mes']}%)" for c in l["por_competencia"]]
        for l in folha["comparacao_por_situacao"]
    ])

    flow.append(Paragraph("Comparação Folha × Ergon — por Tipo de Rubrica (% convergente)", estilos["h3"]))
    _tabela(flow, estilos, ["Tipo"] + competencias, [
        [l["tipo"]] + [(f"{c['percentual_convergente']}%" if c["percentual_convergente"] is not None else "—")
                       for c in l["por_competencia"]]
        for l in folha["comparacao_por_tipo_rubrica"]
    ])

    flow.append(Paragraph("Gráficos", estilos["h3"]))
    for png in (
        relatorio_graficos.grafico_convergencia_folha(competencias, folha.get("_por_tiporubr_grafico") or []),
        relatorio_graficos.grafico_tipo_rubrica_folha(competencias, folha.get("_por_tiporubr_grafico") or []),
    ):
        img = relatorio_pdf._imagem_flowable(png)
        if img is not None:
            flow.append(img)
            flow.append(Spacer(1, 10))

    # 139ª rodada — "as 10 rubricas com mais divergências" (novo).
    top = folha.get("top_rubricas_divergentes") or {}
    flow.append(Paragraph("10 Rubricas com Mais Divergências", estilos["h3"]))
    if not top.get("disponivel"):
        flow.append(Paragraph("Sem dados suficientes para montar este ranking.", estilos["corpo"]))
        return
    flow.append(Paragraph(f"Competência considerada: {top['competencia']} (a mais recente carregada).", estilos["corpo"]))
    linhas = top.get("linhas") or []
    if not linhas:
        flow.append(Paragraph("Nenhuma rubrica com divergência nesta competência.", estilos["corpo"]))
        return
    _tabela(flow, estilos, ["Rubrica Ergon", "Nome", "Tipo", "Total de linhas", "Divergentes", "% divergente"], [
        [l["rubrica_ergon"], l.get("nome") or "—", l.get("tipo") or "—",
         _fmt_int(l["total_linhas"]), _fmt_int(l["total_divergente"]), _fmt_pct(l["pct_divergente"])]
        for l in linhas
    ])


def gerar_pdf_bytes(dados, itens_migracao_grafico_ids=None):
    """dados: o que coletar_dados_dashboard() devolve. itens_migracao_grafico_ids:
    até LIMITE_GRAFICOS_MIGRACAO ids de itens_migracao escolhidos pelo usuário no
    modal do front-end ("quais tabelas devem ter gráfico no documento") — capado
    aqui de novo por segurança, mesmo já validado no endpoint."""
    itens_migracao_grafico_ids = list(itens_migracao_grafico_ids or [])[:LIMITE_GRAFICOS_MIGRACAO]
    estilos = relatorio_pdf._estilos()
    projeto = dados.get("projeto") or {}
    nome_projeto = projeto.get("nome") or "Projeto"
    cliente = projeto.get("cliente") or ""
    gerado_em = dados.get("gerado_em")
    try:
        gerado_em_fmt = date.fromisoformat(gerado_em[:10]).strftime("%d/%m/%Y") if gerado_em else ""
    except (ValueError, TypeError):
        gerado_em_fmt = str(gerado_em or "")

    buf = BytesIO()
    buf_doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=2.2 * cm, bottomMargin=2 * cm, leftMargin=2.2 * cm, rightMargin=2.2 * cm,
        title=f"Dashboard Executivo — {nome_projeto}",
    )

    flow = [
        Paragraph("Dashboard Executivo do Projeto", estilos["titulo"]),
        Paragraph(f"{nome_projeto}" + (f" — {cliente}" if cliente else ""), estilos["subtitulo"]),
        Paragraph(f"Gerado em {gerado_em_fmt} · retrato dos dados no momento da geração (sem IA — só números do sistema)",
                  estilos["subtitulo"]),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc")),
        Spacer(1, 4),
    ]

    _secao_resumo_geral(flow, estilos, dados)
    _secao_frentes(flow, estilos, dados)
    _secao_atrasos_bloqueios(flow, estilos, dados)
    _secao_atividades_master(flow, estilos, dados)
    _secao_marcos(flow, estilos, dados)
    _secao_anomalias(flow, estilos, dados)
    _secao_riscos_pendencias(flow, estilos, dados)
    _secao_financeiro(flow, estilos, dados)
    _secao_requisitos(flow, estilos, dados)
    _secao_migracao(flow, estilos, dados, itens_migracao_grafico_ids)
    _secao_folha(flow, estilos, dados)

    flow.append(Spacer(1, 14))
    flow.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#dddddd")))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(
        "Este documento foi gerado diretamente a partir dos dados cadastrados no sistema, sem intervenção de "
        "inteligência artificial. As previsões de prazo da(s) atividade(s) master são estimativas calculadas por "
        "extrapolação do ritmo de execução observado — não são um compromisso contratual.",
        estilos["rodape"],
    ))

    buf_doc.build(flow)
    return buf.getvalue()
