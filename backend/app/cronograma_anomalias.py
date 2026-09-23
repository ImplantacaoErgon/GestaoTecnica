"""
Anomalias de Data Fixada (57ª/58ª rodada — migração 031).

`atividades.data_execucao_fixada` trava dtini_prev/dtfim_prev de uma atividade contra
recálculo automático por dependência: os dois motores que recalculam datas em cascata
(cronograma_replanejamento.py e a cascata de dependências de cronograma_edicao_lote.py)
NUNCA tocam a data de uma atividade fixada — só edição manual direta (o modal individual,
via ATIVIDADE_FIELDS/update_atividade em main.py) pode mudá-la.

Pedido do usuário verbatim: "no cronograma crie um campo para fixar a data de execução.
Quando essa atividade estiver com essa 'marca' ela pode ser alterada apenas manualmente.
Caso uma atividade dessa for afetada pela dependência um alerta no relatório deve ser
apresentado como uma anomalia."

Este módulo é a parte do "alerta": para cada atividade com data fixada (não concluída
nem cancelada), confere se a data que está gravada ainda respeita o que cada
predecessora DIRETA dela exige (tipo FS/SS/FF/SF + lag, calculado a partir da data de
referência ATUAL da predecessora — real quando já aconteceu, senão a prevista). Quando
não respeita — a predecessora "puxaria" a fixada para uma data mais tarde, mas como ela
está fixada isso nunca é aplicado sozinho — é uma ANOMALIA.

Diferente do Replanejamento/Edição em Lote (que só recalculam quando o usuário pede
explicitamente, num preview pontual), esta é uma checagem "ao vivo": reflete o estado
atual do cronograma a qualquer momento, não importa como a predecessora mudou (edição
manual, replanejamento de outro trecho, reimportação do MS Project etc.) — é o que
alimenta o card do Dashboard e a seção correspondente do Relatório Executivo (IA).

Não grava nada — só leitura/diagnóstico.
"""
from datetime import date

from . import db
from .cpm import _dur_dias, _business_day_offset, dias_uteis_entre


def _to_date(d):
    if d is None:
        return None
    if isinstance(d, str):
        return date.fromisoformat(d)
    return d


def _ref_dates(atividade):
    """Data de referência atual de uma atividade (predecessora): prefere a data REAL,
    cai pra prevista atual na falta dela — mesma regra usada em
    cronograma_replanejamento.py/cronograma_edicao_lote.py."""
    ini = atividade.get("dtini_real") or atividade.get("dtini_prev")
    fim = atividade.get("dtfim_real") or atividade.get("dtfim_prev")
    return _to_date(ini), _to_date(fim)


def _non_working(projeto_id):
    excecoes = db.fetch_all(
        f"SELECT data FROM calendario_util WHERE projeto_id = {db.q(projeto_id)} AND util = FALSE"
    )
    return {_to_date(e["data"]) for e in excecoes}


def detectar(projeto_id, status_familia_concluida):
    """Lista as violações de dependência de atividades com data fixada — uma entrada
    por aresta (predecessora -> sucessora fixada) que hoje não bate, ordenada da maior
    diferença (em dias úteis) para a menor."""
    projeto = db.fetch_one(f"SELECT horas_dia_util FROM projetos WHERE id = {db.q(projeto_id)}")
    if not projeto:
        return []
    horas_dia_util = float(projeto.get("horas_dia_util") or 8.0)

    atividades = db.fetch_all(
        "SELECT id, codigo_wbs, nome, status, dtini_prev, dtfim_prev, dtini_real, dtfim_real, "
        "data_execucao_fixada "
        f"FROM atividades WHERE projeto_id = {db.q(projeto_id)}"
    )
    by_id = {a["id"]: a for a in atividades}

    # Só entram atividades fixadas, não concluídas/canceladas (mesma exclusão do
    # Replanejamento — uma atividade concluída/cancelada é fato histórico, não faz
    # sentido "exigir" que ela ainda respeite uma dependência pra frente) e com as duas
    # datas previstas preenchidas (não dá pra checar uma data fixada que não existe).
    fixadas = [
        a for a in atividades
        if a.get("data_execucao_fixada")
        and a["status"] not in status_familia_concluida and a["status"] != "Cancelada"
        and a.get("dtini_prev") and a.get("dtfim_prev")
    ]
    if not fixadas:
        return []

    deps = db.fetch_all(
        "SELECT ad.atividade_id, ad.predecessora_id, ad.tipo, ad.lag_horas "
        "FROM atividade_dependencia ad "
        f"JOIN atividades a ON a.id = ad.atividade_id AND a.projeto_id = {db.q(projeto_id)}"
    )
    deps_por_sucessora = {}
    for d in deps:
        deps_por_sucessora.setdefault(d["atividade_id"], []).append(d)

    non_working = _non_working(projeto_id)
    anomalias = []

    for a in fixadas:
        for d in deps_por_sucessora.get(a["id"], []):
            pred = by_id.get(d["predecessora_id"])
            if not pred:
                continue
            pred_ini, pred_fim = _ref_dates(pred)
            tipo = d["tipo"]
            # Mesma conversão de lag das duas cascatas (cronograma_replanejamento.py/
            # cronograma_edicao_lote.py) — mantida idêntica de propósito, pros três
            # motores nunca divergirem sobre o mesmo grafo.
            lag_dias = _dur_dias(d["lag_horas"], horas_dia_util) if d["lag_horas"] else 0

            if tipo == "FS":
                if not pred_fim:
                    continue
                exigida = _business_day_offset(pred_fim, non_working, lag_dias + 1)
                campo, atual = "dtini_prev", _to_date(a["dtini_prev"])
            elif tipo == "SS":
                if not pred_ini:
                    continue
                exigida = _business_day_offset(pred_ini, non_working, lag_dias)
                campo, atual = "dtini_prev", _to_date(a["dtini_prev"])
            elif tipo == "FF":
                if not pred_fim:
                    continue
                exigida = _business_day_offset(pred_fim, non_working, lag_dias)
                campo, atual = "dtfim_prev", _to_date(a["dtfim_prev"])
            elif tipo == "SF":
                if not pred_ini:
                    continue
                exigida = _business_day_offset(pred_ini, non_working, lag_dias)
                campo, atual = "dtfim_prev", _to_date(a["dtfim_prev"])
            else:
                continue

            # Só é anomalia quando a data fixada fica ANTES do que a dependência exige
            # (a atividade fixada "furaria" a predecessora). Ficar depois é normal —
            # toda folga/buffer de cronograma é uma data fixada "depois do mínimo", sem
            # nenhum problema.
            if atual is None or atual >= exigida:
                continue

            anomalias.append({
                "atividade_id": a["id"], "codigo_wbs": a.get("codigo_wbs"), "nome": a["nome"],
                "predecessora_id": pred["id"], "predecessora_codigo_wbs": pred.get("codigo_wbs"),
                "predecessora_nome": pred["nome"],
                "tipo": tipo, "lag_horas": float(d["lag_horas"] or 0),
                "campo_afetado": campo,
                "data_fixada_atual": atual.isoformat(),
                "data_exigida_pela_dependencia": exigida.isoformat(),
                "dias_uteis_de_diferenca": dias_uteis_entre(atual, exigida, non_working),
            })

    anomalias.sort(key=lambda x: x["dias_uteis_de_diferenca"], reverse=True)
    return anomalias
