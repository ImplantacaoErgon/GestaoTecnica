"""
Edição em lote do cronograma, com propagação de dependências (39ª rodada).

Tela nova ("Editar em massa"), em formato planilha: o usuário abre várias
atividades de uma vez, edita Início previsto / Fim previsto / Esforço
previsto (horas) de quantas linhas quiser, e só grava tudo ao confirmar.

Diferente do Replanejamento (`cronograma_replanejamento.py`, que recalcula
o projeto INTEIRO a partir de uma data-âncora), aqui só entram no recálculo
as atividades que o usuário realmente editou e as SUCESSORAS delas no
grafo de dependências — o resto do cronograma fica exatamente como estava.
Reaproveita as mesmas funções de dia útil de `cpm.py` para os três motores
(Caminho Crítico, Replanejamento, Edição em lote) ficarem consistentes
entre si.

Regras (definidas junto com o desenho da tela, aplicadas em `calcular()`):
- Atividades concluídas ou canceladas nunca podem ser editadas por aqui
  (rejeitado com erro amigável) — servem só de referência fixa para as
  sucessoras, igual ao Replanejamento.
- A duração (em dias úteis) deriva de `prazo_horas`, exceto quando o
  usuário edita o Fim previsto: nesse caso a DATA manda, e o esforço
  equivalente é recalculado e gravado, pra não deixar os dois campos
  inconsistentes (editar só o Início ou só o Esforço, ao contrário,
  preserva a duração/data que não foi tocada).
- Uma atividade editada diretamente pelo usuário NUNCA tem o próprio
  Início recalculado a partir das predecessoras dela — o valor digitado
  (ou derivado do que foi digitado) sempre vale, mesmo que "atropele" uma
  dependência; só as SUCESSORAS dela são recalculadas. Se uma sucessora
  também foi editada diretamente, o valor editado dela também vence.
- Uma sucessora que NÃO foi editada tem a duração preservada e só a
  posição (Início/Fim) recalculada a partir das predecessoras, pela mesma
  fórmula do Caminho Crítico (FS soma ao fim da predecessora, SS ao
  início, FF/SF subtraem a própria duração) — podendo mover tanto pra
  frente quanto pra trás, replicando fielmente o que mudou lá atrás na
  cadeia.
- Marcos não entram (a tabela `atividade_dependencia` só liga atividades,
  igual ao Replanejamento) e não aparecem nesta tela.
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
    para as sucessoras dela no grafo — mesma regra do replanejamento:
    prefere a data REAL, cai pra prevista atual na falta dela."""
    ini = atividade.get("dtini_real") or atividade.get("dtini_prev")
    fim = atividade.get("dtfim_real") or atividade.get("dtfim_prev")
    return _to_date(ini), _to_date(fim)


def _non_working(projeto_id):
    excecoes = db.fetch_all(
        f"SELECT data FROM calendario_util WHERE projeto_id = {db.q(projeto_id)} AND util = FALSE"
    )
    return {_to_date(e["data"]) for e in excecoes}


def _validar_edicao(atividade_id, by_id, edicao_raw, status_familia_concluida):
    """Valida e normaliza uma linha de `edicoes` (payload do front-end) —
    levanta ValueError com mensagem amigável em qualquer inconsistência.
    Retorna só os campos realmente presentes/preenchidos (dtini_prev/
    dtfim_prev/prazo_horas), prontos para uso em `calcular()`."""
    a = by_id.get(atividade_id)
    if not a:
        raise ValueError("Uma das atividades enviadas não pertence a este projeto (ou foi excluída).")
    if a["status"] in status_familia_concluida or a["status"] == "Cancelada":
        rotulo = (f'{a["codigo_wbs"]} — ' if a.get("codigo_wbs") else "") + a["nome"]
        raise ValueError(f'"{rotulo}" está concluída/cancelada e não pode ser editada nesta tela.')

    campos = {}
    if edicao_raw.get("dtini_prev"):
        campos["dtini_prev"] = edicao_raw["dtini_prev"]
    if edicao_raw.get("dtfim_prev"):
        campos["dtfim_prev"] = edicao_raw["dtfim_prev"]
    if edicao_raw.get("prazo_horas") not in (None, ""):
        try:
            prazo = float(edicao_raw["prazo_horas"])
        except (TypeError, ValueError):
            raise ValueError(f'Esforço previsto inválido em "{a["nome"]}".')
        if prazo <= 0:
            raise ValueError(f'O esforço previsto de "{a["nome"]}" precisa ser maior que zero.')
        campos["prazo_horas"] = prazo
    return campos


def calcular(projeto_id, edicoes, status_familia_concluida):
    """Simula (sem gravar nada) o efeito das edições pedidas + a propagação
    em cascata pelas sucessoras. `edicoes` é a lista crua vinda do
    front-end: [{"id": "...", "dtini_prev": "2026-01-01", ...}, ...] — só
    os campos realmente alterados em cada linha aparecem no dict; o resto
    vem ausente (não como null), pra distinguir "não mexi nisso" de "limpei
    o campo" (limpar não é suportado por esta tela)."""
    projeto = db.fetch_one(f"SELECT * FROM projetos WHERE id = {db.q(projeto_id)}")
    if not projeto:
        raise ValueError("Projeto não encontrado")
    horas_dia_util = float(projeto.get("horas_dia_util") or 8.0)

    atividades = db.fetch_all(
        "SELECT id, codigo_wbs, nome, status, percentual_concluido, prazo_horas, "
        "dtini_prev, dtfim_prev, dtini_real, dtfim_real "
        f"FROM atividades WHERE projeto_id = {db.q(projeto_id)}"
    )
    by_id = {a["id"]: a for a in atividades}
    ids = list(by_id.keys())

    edicoes_por_id = {}
    for e in edicoes:
        campos = _validar_edicao(e.get("id"), by_id, e, status_familia_concluida)
        if campos:
            edicoes_por_id[e["id"]] = campos
    if not edicoes_por_id:
        return {"itens": []}

    deps = db.fetch_all(
        "SELECT ad.atividade_id, ad.predecessora_id, ad.tipo, ad.lag_horas "
        "FROM atividade_dependencia ad "
        f"JOIN atividades a ON a.id = ad.atividade_id AND a.projeto_id = {db.q(projeto_id)}"
    )
    g = nx.DiGraph()
    g.add_nodes_from(ids)
    for d in deps:
        if d["atividade_id"] in by_id and d["predecessora_id"] in by_id:
            lag = _dur_dias(d["lag_horas"], horas_dia_util) if d["lag_horas"] else 0
            g.add_edge(d["predecessora_id"], d["atividade_id"], tipo=d["tipo"], lag=lag)

    try:
        ordem = list(nx.topological_sort(g))
    except nx.NetworkXUnfeasible:
        raise ValueError("Existe um ciclo de dependências entre atividades — corrija antes de editar em lote.")

    # afetados = as editadas + todas as sucessoras delas (diretas e indiretas) —
    # é só nesse conjunto que alguma data pode mudar; o resto do cronograma
    # fica intocado, mesmo que apareça como predecessora de algo afetado.
    afetados = set(edicoes_por_id.keys())
    fronteira = list(afetados)
    while fronteira:
        atual = fronteira.pop()
        for _, succ in g.out_edges(atual):
            if succ not in afetados:
                afetados.add(succ)
                fronteira.append(succ)

    non_working = _non_working(projeto_id)

    # Referência fixa e arbitrária pra converter data <-> deslocamento em dias
    # úteis com as mesmas funções do CPM — sem significado próprio (ao
    # contrário da âncora do Replanejamento), só um ponto em comum pra fazer
    # a aritmética de calendário. Usa a menor data conhecida do projeto pra
    # nunca gerar deslocamento negativo.
    todas_datas = [
        _to_date(a[c]) for a in atividades for c in ("dtini_prev", "dtfim_prev", "dtini_real", "dtfim_real")
        if a.get(c)
    ]
    epoca = min(todas_datas) if todas_datas else date.today()

    def offset(d):
        return dias_uteis_entre(epoca, d, non_working) if d else 0

    def para_data(off):
        return _business_day_offset(epoca, non_working, int(round(off)))

    ES, EF = {}, {}
    itens = []
    for n in ordem:
        a = by_id[n]
        concluida_ou_cancelada = a["status"] in status_familia_concluida or a["status"] == "Cancelada"
        edit = edicoes_por_id.get(n)

        if concluida_ou_cancelada:
            ini, fim = _ref_dates(a)
            ES[n] = offset(ini) if ini else 0
            EF[n] = (offset(fim) + 1) if fim else ES[n]
            continue

        if n not in afetados:
            ini, fim = _to_date(a.get("dtini_prev")), _to_date(a.get("dtfim_prev"))
            ES[n] = offset(ini) if ini else 0
            EF[n] = (offset(fim) + 1) if fim else ES[n]
            continue

        dur_atual = _dur_dias(a.get("prazo_horas"), horas_dia_util)

        if edit:
            # Edição direta: o que foi digitado sempre vence, sem olhar pras
            # predecessoras desta própria atividade (ver regra no cabeçalho).
            ini_editado = _to_date(edit.get("dtini_prev"))
            fim_editado = _to_date(edit.get("dtfim_prev"))
            prazo_editado = edit.get("prazo_horas")
            ini = ini_editado or _to_date(a.get("dtini_prev"))
            if ini is None:
                raise ValueError(f'"{a["nome"]}" ainda não tem Início previsto — informe também o início.')
            es = offset(ini)
            if fim_editado:
                # Fim editado (com ou sem início/esforço junto): a data manda,
                # o esforço equivalente é recalculado — nunca os dois brigando.
                ef = offset(fim_editado) + 1
                if ef <= es:
                    raise ValueError(f'Em "{a["nome"]}", o Fim previsto não pode ser anterior ao Início previsto.')
                dur = ef - es
                novo_prazo = round(dur * horas_dia_util, 2)
            else:
                dur = _dur_dias(prazo_editado, horas_dia_util) if prazo_editado is not None else dur_atual
                ef = es + dur
                novo_prazo = prazo_editado if prazo_editado is not None else a.get("prazo_horas")
            ES[n], EF[n] = es, ef
        else:
            # Sucessora pura (não editada diretamente): duração preservada, só a
            # posição é recalculada a partir das predecessoras — mesma fórmula
            # do Caminho Crítico, podendo mover a atividade pra frente ou pra
            # trás conforme o que mudou lá atrás na cadeia.
            candidatos = [0]
            for pred, _, edata in g.in_edges(n, data=True):
                tipo, lag = edata["tipo"], edata["lag"]
                if tipo == "FS":
                    candidatos.append(EF[pred] + lag)
                elif tipo == "SS":
                    candidatos.append(ES[pred] + lag)
                elif tipo == "FF":
                    candidatos.append(EF[pred] + lag - dur_atual)
                elif tipo == "SF":
                    candidatos.append(ES[pred] + lag - dur_atual)
            es = max(candidatos)
            ef = es + dur_atual
            ES[n], EF[n] = es, ef
            novo_prazo = a.get("prazo_horas")

        novo_ini = para_data(ES[n])
        novo_fim = para_data(EF[n] - 1)
        prazo_atual_f = float(a["prazo_horas"]) if a.get("prazo_horas") is not None else None
        novo_prazo_f = float(novo_prazo) if novo_prazo is not None else None
        mudou = (
            a.get("dtini_prev") != novo_ini.isoformat()
            or a.get("dtfim_prev") != novo_fim.isoformat()
            or prazo_atual_f != novo_prazo_f
        )
        if mudou or edit:
            itens.append({
                "id": n, "codigo_wbs": a.get("codigo_wbs"), "nome": a.get("nome"),
                "editado": bool(edit),
                "dtini_prev_atual": a.get("dtini_prev"), "dtini_prev_novo": novo_ini.isoformat(),
                "dtfim_prev_atual": a.get("dtfim_prev"), "dtfim_prev_novo": novo_fim.isoformat(),
                "prazo_horas_atual": prazo_atual_f, "prazo_horas_novo": novo_prazo_f,
                "mudou": mudou,
            })

    return {"itens": itens}


def aplicar(projeto_id, edicoes, status_familia_concluida):
    """Roda `calcular()` e grava de fato — em lote, numa única transação
    (mesmo padrão de `cpm.recalcular`/`cronograma_replanejamento.aplicar`:
    nunca uma UPDATE por linha)."""
    resultado = calcular(projeto_id, edicoes, status_familia_concluida)
    itens = [i for i in resultado["itens"] if i["mudou"]]
    if not itens:
        return {"atividades_editadas": 0, "atividades_cascata": 0, "ids_alterados": []}
    stmts = ["BEGIN;"]
    for i in itens:
        stmts.append(
            f"UPDATE atividades SET dtini_prev={db.q(i['dtini_prev_novo'])}, "
            f"dtfim_prev={db.q(i['dtfim_prev_novo'])}, prazo_horas={db.q(i['prazo_horas_novo'])} "
            f"WHERE id={db.q(i['id'])};"
        )
    stmts.append("COMMIT;")
    db.execute("\n".join(stmts))
    editadas = sum(1 for i in itens if i["editado"])
    return {
        "atividades_editadas": editadas, "atividades_cascata": len(itens) - editadas,
        "ids_alterados": [i["id"] for i in itens],
    }
