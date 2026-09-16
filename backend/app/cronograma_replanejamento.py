"""
Replanejamento do cronograma — recalcula as datas PREVISTAS de atividades
incompletas a partir de uma data-âncora (normalmente hoje), respeitando o
grafo de dependências e o quanto já foi executado de verdade.

Diferente do Caminho Crítico (`cpm.py`, que recalcula um cronograma
TEÓRICO a partir da data de início fixa do projeto, ignora o que já foi
executado, e grava só nas colunas internas `cpm_*`), este módulo calcula
um cronograma REALISTA — "o que falta, reprogramado a partir de agora" —
e grava direto em `dtini_prev`/`dtfim_prev`, as datas que aparecem no
Cronograma/Gantt. Reaproveita as mesmas funções de dia útil de `cpm.py`
para os dois motores ficarem consistentes entre si.

Regras (definidas com o usuário antes de implementar):
- Atividades já concluídas (`STATUS_FAMILIA_CONCLUIDA`) e canceladas
  nunca têm as datas tocadas — servem só de referência (usando a data
  REAL quando existe, senão a prevista atual) para calcular as
  sucessoras no grafo.
- As demais — incluindo "Bloqueada", que entra como sugestão a ser
  revisada depois de desbloqueada — são recalculadas a partir da
  data-âncora, respeitando FS/SS/FF/SF + lag e o calendário útil.
- Usa a duração RESTANTE (`prazo_horas * (1 - percentual_concluido/100)`),
  não a original — uma atividade 40% feita reprograma só os 60% que
  faltam.
- Nunca mexe em status, percentual, horas ou datas REAIS — só nas
  PREVISTAS. É uma sobreposição normal (como editar uma data à mão), não
  gera histórico sozinha — quem quiser preservar o estado atual antes de
  aplicar usa o botão "Gerar versão" (ver `cronograma_versoes.py`).
- Marcos não entram no grafo de dependências (a tabela `atividade_dependencia`
  só liga atividades) — tratamento à parte, mais simples: um marco ainda
  não realizado (`data_real` vazio) cuja data prevista já passou é só
  empurrado para a data-âncora, sem nenhuma lógica de precedência.
"""
from datetime import date

import networkx as nx

from . import db
from .cpm import _dur_dias, _business_day_offset, dias_uteis_entre


def _to_date(d):
    if d is None:
        return None
    if isinstance(d, str):
        return date.fromisoformat(d)
    return d


def _ref_dates(atividade):
    """Datas de referência de uma atividade concluída/cancelada, usadas só
    para as sucessoras dela no grafo: preferem a data REAL; na falta dela
    (ex: cancelada sem nunca ter rodado), caem para a prevista atual."""
    ini = atividade.get("dtini_real") or atividade.get("dtini_prev")
    fim = atividade.get("dtfim_real") or atividade.get("dtfim_prev")
    return _to_date(ini), _to_date(fim)


def _non_working(projeto_id):
    excecoes = db.fetch_all(
        f"SELECT data FROM calendario_util WHERE projeto_id = {db.q(projeto_id)} AND util = FALSE"
    )
    return {_to_date(e["data"]) for e in excecoes}


def calcular(projeto_id, data_ancora, status_familia_concluida):
    """Calcula (sem gravar nada) o novo `dtini_prev`/`dtfim_prev` de cada
    atividade incompleta do projeto e a nova `data_prevista` de marcos
    pendentes vencidos, a partir de `data_ancora`. Não grava no banco —
    usado tanto pela prévia quanto pela confirmação (que reaproveita este
    cálculo antes de gravar)."""
    projeto = db.fetch_one(f"SELECT * FROM projetos WHERE id = {db.q(projeto_id)}")
    if not projeto:
        raise ValueError("Projeto não encontrado")
    horas_dia_util = float(projeto.get("horas_dia_util") or 8.0)

    atividades = db.fetch_all(
        "SELECT id, codigo_wbs, nome, status, percentual_concluido, prazo_horas, "
        "dtini_prev, dtfim_prev, dtini_real, dtfim_real "
        f"FROM atividades WHERE projeto_id = {db.q(projeto_id)}"
    )
    non_working = _non_working(projeto_id)
    data_ancora = _to_date(data_ancora)
    if not data_ancora:
        raise ValueError("Data-âncora é obrigatória")
    D0 = _business_day_offset(data_ancora, non_working, 0)

    resultado_marcos = []
    marcos = db.fetch_all(
        f"SELECT id, nome, data_prevista, data_real FROM marcos WHERE projeto_id = {db.q(projeto_id)}"
    )
    for m in marcos:
        if m.get("data_real"):
            continue
        dp = _to_date(m.get("data_prevista"))
        if dp and dp < D0:
            resultado_marcos.append({
                "id": m["id"], "nome": m.get("nome"),
                "data_prevista_atual": m.get("data_prevista"),
                "data_prevista_nova": D0.isoformat(),
            })

    if not atividades:
        return {"itens": [], "marcos": resultado_marcos, "data_ancora_normalizada": D0.isoformat()}

    deps = db.fetch_all(
        "SELECT ad.atividade_id, ad.predecessora_id, ad.tipo, ad.lag_horas "
        "FROM atividade_dependencia ad "
        f"JOIN atividades a ON a.id = ad.atividade_id AND a.projeto_id = {db.q(projeto_id)}"
    )

    by_id = {a["id"]: a for a in atividades}
    ids = list(by_id.keys())

    g = nx.DiGraph()
    g.add_nodes_from(ids)
    for d in deps:
        if d["atividade_id"] in by_id and d["predecessora_id"] in by_id:
            # Mesma conversão de lag do cpm.py (inclusive a mesma limitação conhecida:
            # lag negativo — antecipação — vira +1 dia em vez de -1, por causa do
            # `max(1, ...)` de `_dur_dias`). Mantido idêntico de propósito, para os
            # dois motores (Caminho Crítico e este) nunca divergirem no mesmo grafo.
            lag = _dur_dias(d["lag_horas"], horas_dia_util) if d["lag_horas"] else 0
            g.add_edge(d["predecessora_id"], d["atividade_id"], tipo=d["tipo"], lag=lag)

    try:
        ordem = list(nx.topological_sort(g))
    except nx.NetworkXUnfeasible:
        raise ValueError("Existe um ciclo de dependências entre atividades — corrija antes de replanejar.")

    def offset(d):
        return dias_uteis_entre(D0, d, non_working) if d else None

    ES, EF = {}, {}
    itens = []
    for n in ordem:
        a = by_id[n]
        concluida_ou_cancelada = a["status"] in status_familia_concluida or a["status"] == "Cancelada"
        if concluida_ou_cancelada:
            ini, fim = _ref_dates(a)
            ES[n] = offset(ini) if ini else 0
            EF[n] = (offset(fim) + 1) if fim else ES[n]
            continue

        prazo = float(a["prazo_horas"]) if a.get("prazo_horas") else 0.0
        pct = a.get("percentual_concluido") or 0
        horas_restantes = prazo * (1 - pct / 100.0) if pct else prazo
        dur = _dur_dias(horas_restantes, horas_dia_util)

        candidatos = [0]
        for pred, _, edata in g.in_edges(n, data=True):
            if pred not in ES:
                continue
            tipo, lag = edata["tipo"], edata["lag"]
            if tipo == "FS":
                candidatos.append(EF[pred] + lag)
            elif tipo == "SS":
                candidatos.append(ES[pred] + lag)
            elif tipo == "FF":
                candidatos.append(EF[pred] + lag - dur)
            elif tipo == "SF":
                candidatos.append(ES[pred] + lag - dur)
        es = max(candidatos)
        ef = es + dur
        ES[n], EF[n] = es, ef

        novo_ini = _business_day_offset(D0, non_working, es)
        novo_fim = _business_day_offset(D0, non_working, ef - 1)
        mudou = (a.get("dtini_prev") != novo_ini.isoformat()) or (a.get("dtfim_prev") != novo_fim.isoformat())

        itens.append({
            "id": n, "codigo_wbs": a.get("codigo_wbs"), "nome": a.get("nome"), "status": a.get("status"),
            "percentual_concluido": pct,
            "dtini_prev_atual": a.get("dtini_prev"), "dtfim_prev_atual": a.get("dtfim_prev"),
            "dtini_prev_novo": novo_ini.isoformat(), "dtfim_prev_novo": novo_fim.isoformat(),
            "mudou": mudou,
        })

    return {"itens": itens, "marcos": resultado_marcos, "data_ancora_normalizada": D0.isoformat()}


def aplicar(projeto_id, data_ancora, status_familia_concluida):
    """Roda `calcular()` e grava de fato as datas recalculadas — em lote,
    numa única transação (mesmo padrão de `cpm.recalcular`/`cronograma_import`:
    nunca uma UPDATE por linha, para não estourar timeout contra um banco
    remoto)."""
    resultado = calcular(projeto_id, data_ancora, status_familia_concluida)
    itens = [i for i in resultado["itens"] if i["mudou"]]
    marcos = resultado["marcos"]
    if not itens and not marcos:
        return {
            "atividades_alteradas": 0, "marcos_alterados": 0,
            "data_ancora_normalizada": resultado["data_ancora_normalizada"],
        }
    stmts = ["BEGIN;"]
    for i in itens:
        stmts.append(
            f"UPDATE atividades SET dtini_prev={db.q(i['dtini_prev_novo'])}, "
            f"dtfim_prev={db.q(i['dtfim_prev_novo'])} WHERE id={db.q(i['id'])};"
        )
    for m in marcos:
        stmts.append(
            f"UPDATE marcos SET data_prevista={db.q(m['data_prevista_nova'])} WHERE id={db.q(m['id'])};"
        )
    stmts.append("COMMIT;")
    db.execute("\n".join(stmts))
    return {
        "atividades_alteradas": len(itens), "marcos_alterados": len(marcos),
        "data_ancora_normalizada": resultado["data_ancora_normalizada"],
    }
