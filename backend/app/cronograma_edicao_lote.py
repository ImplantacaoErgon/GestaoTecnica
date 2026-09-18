"""
Edição em lote do cronograma, com propagação de dependências (39ª rodada) e,
a partir da 40ª rodada, edição de qualquer outro campo da atividade na mesma
grade (Nome, Etapa, Frente, Prioridade, Observações, ★Master, 💰Entregável,
% concluído, Início/Fim real, Recursos/participantes) — pedido do usuário
verbatim: "nessa planilha de alteração em massa pode ter os demais campos,
pois podemos proceder a diversas alterações simultâneas. Numas alterar
datas, noutras os recursos, noutras ainda marcar ou desmarcar Master".

Tela ("Editar em massa"), em formato planilha: o usuário abre várias
atividades de uma vez, edita quantos campos/linhas quiser, e só grava tudo
ao confirmar.

Diferente do Replanejamento (`cronograma_replanejamento.py`, que recalcula
o projeto INTEIRO a partir de uma data-âncora), aqui só entram no recálculo
de DATAS as atividades que o usuário realmente editou (Início/Fim previsto/
Esforço) e as SUCESSORAS delas no grafo de dependências — o resto do
cronograma fica exatamente como estava. Reaproveita as mesmas funções de
dia útil de `cpm.py` para os três motores (Caminho Crítico, Replanejamento,
Edição em lote) ficarem consistentes entre si.

Regras da cascata de datas (definidas na 39ª rodada, aplicadas em
`calcular()`, sem nenhuma mudança nesta rodada):
- Atividades concluídas ou canceladas nunca podem ser editadas por aqui
  (rejeitado com erro amigável) — servem só de referência fixa para as
  sucessoras, igual ao Replanejamento. Essa trava vale pra QUALQUER campo
  desta tela, não só datas — uma atividade concluída não tem mais nada
  a editar em lote, é editável só no modal individual (correções pontuais).
- A duração (em dias úteis) deriva de `prazo_horas`, exceto quando o
  usuário edita o Fim previsto: nesse caso a DATA manda, e o esforço
  equivalente é recalculado e gravado, pra não deixar os dois campos
  inconsistentes (editar só o Início ou só o Esforço, ao contrário,
  preserva a duração/data que não foi tocada).
- Uma atividade editada diretamente pelo usuário NUNCA tem o próprio
  Início recalculado a partir das predecessoras dela — o valor digitado
  (ou derivado do que foi digitado) sempre vale, mesmo que "atropele" uma
  dependência; só as SUCESSORAS dela são recalculadas.
- Uma sucessora que NÃO foi editada tem a duração preservada e só a
  posição (Início/Fim) recalculada a partir das predecessoras, pela mesma
  fórmula do Caminho Crítico (FS soma ao fim da predecessora, SS ao
  início, FF/SF subtraem a própria duração) — podendo mover tanto pra
  frente quanto pra trás, replicando fielmente o que mudou lá atrás na
  cadeia.
- Marcos não entram (a tabela `atividade_dependencia` só liga atividades,
  igual ao Replanejamento) e não aparecem nesta tela.

Campos novos da 40ª rodada e como cada grupo se comporta:
- **Campos simples** (Nome, Etapa, Frente, Prioridade, Observações,
  ★Master, 💰Entregável): não entram no grafo de dependências — mudam só
  na própria linha editada, nunca se propagam pra sucessoras (não existe
  "efeito em cascata" de Prioridade, por exemplo).
- **Progresso** (% concluído, Início real, Fim real): também não cascateiam
  (são fatos sobre a própria atividade, não afetam o planejamento das
  outras) — mas têm uma regra própria de RELATO, pedida pelo usuário:
  * % SUBINDO, ou uma data real indo pra MAIS CEDO que a anterior
    (antecipação) → é uma melhora: registrar relato é OPCIONAL — se o
    usuário não escrever nada, entra sozinho um relato padrão
    ("Ajuste de completude da atividade" / "Antecipação da execução da
    atividade").
  * % DESCENDO, ou uma data real indo pra MAIS TARDE que a anterior
    (atraso) → é uma piora: o TEXTO do relato é OBRIGATÓRIO, rejeitado em
    `aplicar()` se vier vazio (o preview já avisa qual linha precisa).
  * Preenchendo uma data real pela primeira vez (estava vazia): neutro,
    opcional, com um relato padrão genérico — não dá pra classificar como
    "antes" ou "depois" de nada que ainda não existia.
  Além disso, se o resultado da edição já fecha a atividade (100% + Fim
  Real preenchidos), o Status é recalculado sozinho pelas mesmas
  `classificar_conclusao`/`validar_consistencia_conclusao` de
  `main.py` usadas no modal individual (passadas por parâmetro pra evitar
  import circular) — sem isso, o campo Status ficaria dessincronizado do
  que os cards de Dashboard/Relatório Executivo leem. Status em si
  continua NÃO editável diretamente nesta tela (assim como % e datas
  reais de uma atividade concluída/cancelada — ver trava acima): a
  automação só entra quando o RESULTADO da edição já fecha a conta.
- **Recursos/participantes**: uma linha pode vir com `recursos_adicionar`/
  `recursos_remover` (listas de id de `recursos`) — grava/apaga em
  `atividade_recurso` sem tocar em `horas_alocadas` por pessoa (isso
  continua só no modal individual, aba Responsáveis).
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


def _validar_campos_simples(a, edicao_raw, prioridade_validas):
    """Nome/Etapa/Frente/Prioridade/Observações/Master/Entregável — nenhum
    deles entra no recálculo de datas, só é copiado pro resultado. Só
    aparece no dict devolvido o que REALMENTE mudou (evita gravar/mostrar
    "alteração" numa linha onde o valor enviado já é igual ao atual —
    pode acontecer numa edição em massa que reenvia a linha inteira)."""
    simples = {}

    if "nome" in edicao_raw:
        novo = (edicao_raw["nome"] or "").strip()
        if not novo:
            raise ValueError(f'"{a["nome"]}": o nome da atividade não pode ficar em branco.')
        if novo != (a.get("nome") or ""):
            simples["nome"] = {"atual": a.get("nome"), "novo": novo}

    if "observacoes" in edicao_raw:
        novo = edicao_raw["observacoes"] or None
        if novo != a.get("observacoes"):
            simples["observacoes"] = {"atual": a.get("observacoes"), "novo": novo}

    if "etapa_id" in edicao_raw:
        novo = edicao_raw["etapa_id"] or None
        if novo != a.get("etapa_id"):
            simples["etapa_id"] = {"atual": a.get("etapa_id"), "novo": novo}

    if "frente_trabalho_id" in edicao_raw:
        novo = edicao_raw["frente_trabalho_id"] or None
        if novo != a.get("frente_trabalho_id"):
            simples["frente_trabalho_id"] = {"atual": a.get("frente_trabalho_id"), "novo": novo}

    if "prioridade" in edicao_raw:
        novo = edicao_raw["prioridade"] or None
        if novo and novo not in prioridade_validas:
            raise ValueError(f'"{a["nome"]}": prioridade inválida.')
        if novo != a.get("prioridade"):
            simples["prioridade"] = {"atual": a.get("prioridade"), "novo": novo}

    if "eh_atividade_master" in edicao_raw:
        novo = bool(edicao_raw["eh_atividade_master"])
        if novo != bool(a.get("eh_atividade_master")):
            simples["eh_atividade_master"] = {"atual": bool(a.get("eh_atividade_master")), "novo": novo}

    if "eh_entregavel" in edicao_raw:
        novo = bool(edicao_raw["eh_entregavel"])
        if novo != bool(a.get("eh_entregavel")):
            simples["eh_entregavel"] = {"atual": bool(a.get("eh_entregavel")), "novo": novo}

    return simples


def _avaliar_progresso(a, edicao_raw):
    """% concluído / Início real / Fim real — calcula o que mudou e decide
    se a linha precisa de relato, e se ele é obrigatório (piora: % caindo
    ou data real adiada) ou só sugerido com texto padrão (melhora: %
    subindo ou data real antecipada; ou neutro, 1ª vez preenchendo a data).
    Não LEVANTA erro por relato faltando — isso só é cobrado em `aplicar()`,
    depois que o preview já teve chance de avisar qual linha precisa."""
    progresso = {}
    obrigatorio = False
    sugestoes = []

    if "percentual_concluido" in edicao_raw and edicao_raw["percentual_concluido"] not in (None, ""):
        try:
            novo = int(edicao_raw["percentual_concluido"])
        except (TypeError, ValueError):
            raise ValueError(f'"{a["nome"]}": % concluído inválido.')
        if not (0 <= novo <= 100):
            raise ValueError(f'"{a["nome"]}": % concluído precisa estar entre 0 e 100.')
        atual = int(a.get("percentual_concluido") or 0)
        if novo != atual:
            progresso["percentual_concluido"] = {"atual": atual, "novo": novo}
            if novo < atual:
                obrigatorio = True
            else:
                sugestoes.append("Ajuste de completude da atividade")

    for campo, rotulo in (("dtini_real", "Início real"), ("dtfim_real", "Fim real")):
        if campo in edicao_raw:
            novo = edicao_raw[campo] or None
            atual = a.get(campo)
            if novo == atual:
                continue
            if novo:
                try:
                    date.fromisoformat(novo)
                except ValueError:
                    raise ValueError(f'"{a["nome"]}": {rotulo} inválido.')
            progresso[campo] = {"atual": atual, "novo": novo}
            if not atual or not novo:
                sugestoes.append("Atualização de execução da atividade")
            elif _to_date(novo) < _to_date(atual):
                sugestoes.append("Antecipação da execução da atividade")
            else:
                obrigatorio = True

    if not progresso:
        return {}, None, None

    ini_novo = progresso.get("dtini_real", {}).get("novo")
    fim_novo = progresso.get("dtfim_real", {}).get("novo")
    if ini_novo and fim_novo and _to_date(fim_novo) < _to_date(ini_novo):
        raise ValueError(f'"{a["nome"]}": Fim real não pode ser anterior ao Início real.')

    relato_necessario = "obrigatorio" if obrigatorio else "opcional"
    sugestao = "; ".join(dict.fromkeys(sugestoes)) + "." if (not obrigatorio and sugestoes) else None
    return progresso, relato_necessario, sugestao


def _validar_recursos(a, edicao_raw, recursos_validos):
    """recursos_adicionar/recursos_remover — ids de `recursos` a
    incluir/excluir como participante da atividade (sem horas_alocadas por
    pessoa, isso continua só no modal individual)."""
    adicionar = [rid for rid in (edicao_raw.get("recursos_adicionar") or []) if rid]
    remover = [rid for rid in (edicao_raw.get("recursos_remover") or []) if rid]
    for rid in adicionar + remover:
        if rid not in recursos_validos:
            raise ValueError(f'"{a["nome"]}": recurso informado não existe no cadastro.')
    if set(adicionar) & set(remover):
        raise ValueError(f'"{a["nome"]}": o mesmo recurso não pode ser adicionado e removido ao mesmo tempo.')
    return adicionar, remover


def _validar_edicao(atividade_id, by_id, edicao_raw, status_familia_concluida, prioridade_validas, recursos_validos):
    """Valida e normaliza uma linha de `edicoes` (payload do front-end) —
    levanta ValueError com mensagem amigável em qualquer inconsistência.
    Devolve um dict com os quatro grupos de campos (cascata/simples/
    progresso/recursos), cada um só com o que realmente está presente."""
    a = by_id.get(atividade_id)
    if not a:
        raise ValueError("Uma das atividades enviadas não pertence a este projeto (ou foi excluída).")
    if a["status"] in status_familia_concluida or a["status"] == "Cancelada":
        rotulo = (f'{a["codigo_wbs"]} — ' if a.get("codigo_wbs") else "") + a["nome"]
        raise ValueError(f'"{rotulo}" está concluída/cancelada e não pode ser editada nesta tela.')

    cascata = {}
    if edicao_raw.get("dtini_prev"):
        cascata["dtini_prev"] = edicao_raw["dtini_prev"]
    if edicao_raw.get("dtfim_prev"):
        cascata["dtfim_prev"] = edicao_raw["dtfim_prev"]
    if edicao_raw.get("prazo_horas") not in (None, ""):
        try:
            prazo = float(edicao_raw["prazo_horas"])
        except (TypeError, ValueError):
            raise ValueError(f'Esforço previsto inválido em "{a["nome"]}".')
        if prazo <= 0:
            raise ValueError(f'O esforço previsto de "{a["nome"]}" precisa ser maior que zero.')
        cascata["prazo_horas"] = prazo

    simples = _validar_campos_simples(a, edicao_raw, prioridade_validas)
    progresso, relato_necessario, relato_sugerido = _avaliar_progresso(a, edicao_raw)
    recursos_add, recursos_rem = _validar_recursos(a, edicao_raw, recursos_validos)

    return {
        "cascata": cascata,
        "simples": simples,
        "progresso": progresso,
        "relato_necessario": relato_necessario,
        "relato_texto_sugerido": relato_sugerido,
        "relato_texto": (edicao_raw.get("relato_texto") or "").strip(),
        "recursos_adicionar": recursos_add,
        "recursos_remover": recursos_rem,
    }


def calcular(projeto_id, edicoes, status_familia_concluida, prioridade_validas,
             classificar_conclusao=None, validar_consistencia_conclusao=None):
    """Simula (sem gravar nada) o efeito das edições pedidas: a cascata de
    datas pelas sucessoras (só quando Início/Fim previsto/Esforço são
    editados) + os campos simples/progresso/recursos de cada linha editada
    (nunca cascateiam). `edicoes` é a lista crua vinda do front-end — só os
    campos realmente alterados em cada linha aparecem no dict; o resto vem
    ausente (não como null), pra distinguir "não mexi nisso" de "limpei o
    campo".

    `classificar_conclusao`/`validar_consistencia_conclusao` são as funções
    de `main.py` (recebidas por parâmetro pra evitar import circular) que
    recalculam o Status sozinho quando uma edição de progresso já fecha a
    atividade (100% + Fim Real) — mesma regra do modal individual."""
    projeto = db.fetch_one(f"SELECT * FROM projetos WHERE id = {db.q(projeto_id)}")
    if not projeto:
        raise ValueError("Projeto não encontrado")
    horas_dia_util = float(projeto.get("horas_dia_util") or 8.0)

    atividades = db.fetch_all(
        "SELECT id, codigo_wbs, nome, status, percentual_concluido, prazo_horas, horas_realizadas, "
        "dtini_prev, dtfim_prev, dtini_real, dtfim_real, etapa_id, frente_trabalho_id, "
        "prioridade, observacoes, eh_atividade_master, eh_entregavel "
        f"FROM atividades WHERE projeto_id = {db.q(projeto_id)}"
    )
    by_id = {a["id"]: a for a in atividades}
    ids = list(by_id.keys())
    recursos_validos = {r["id"] for r in db.fetch_all("SELECT id FROM recursos")}

    validado_por_id = {}
    for e in edicoes:
        v = _validar_edicao(e.get("id"), by_id, e, status_familia_concluida, prioridade_validas, recursos_validos)
        if v["cascata"] or v["simples"] or v["progresso"] or v["recursos_adicionar"] or v["recursos_remover"]:
            validado_por_id[e["id"]] = v
    if not validado_por_id:
        return {"itens": []}

    # -------- 1) motor de cascata de datas (Início/Fim previsto/Esforço) --------
    # Só entram aqui as linhas com algo em "cascata" — exatamente o mesmo
    # motor da 39ª rodada, sem nenhuma mudança.
    edicoes_por_id = {id_: v["cascata"] for id_, v in validado_por_id.items() if v["cascata"]}

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

    itens = []
    if edicoes_por_id:
        # afetados = as editadas (em cascata) + todas as sucessoras delas
        # (diretas e indiretas) — é só nesse conjunto que alguma data pode
        # mudar; o resto do cronograma fica intocado, mesmo que apareça
        # como predecessora de algo afetado.
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

    # -------- 2) campos simples / progresso+relato / recursos — não cascateiam --------
    # Só se aplica às linhas que o usuário realmente tocou (validado_por_id) —
    # nunca a uma sucessora que só apareceu por causa da cascata de datas acima.
    itens_by_id = {i["id"]: i for i in itens}
    for id_, v in validado_por_id.items():
        tem_extra = v["simples"] or v["progresso"] or v["recursos_adicionar"] or v["recursos_remover"]
        if not tem_extra:
            continue
        item = itens_by_id.get(id_)
        if item is None:
            a = by_id[id_]
            prazo_f = float(a["prazo_horas"]) if a.get("prazo_horas") is not None else None
            item = {
                "id": id_, "codigo_wbs": a.get("codigo_wbs"), "nome": a.get("nome"),
                "editado": True,
                "dtini_prev_atual": a.get("dtini_prev"), "dtini_prev_novo": a.get("dtini_prev"),
                "dtfim_prev_atual": a.get("dtfim_prev"), "dtfim_prev_novo": a.get("dtfim_prev"),
                "prazo_horas_atual": prazo_f, "prazo_horas_novo": prazo_f,
                "mudou": False,
            }
            itens_by_id[id_] = item
            itens.append(item)
        item["editado"] = True  # está em validado_por_id => o usuário tocou nesta linha

        if v["simples"]:
            item["simples"] = v["simples"]
            item["mudou"] = True

        if v["progresso"]:
            item["progresso"] = v["progresso"]
            item["relato_necessario"] = v["relato_necessario"]
            item["relato_texto_sugerido"] = v["relato_texto_sugerido"]
            item["relato_texto"] = v["relato_texto"]
            item["mudou"] = True
            # Status derivado sozinho quando o resultado já fecha a atividade
            # (100% + Fim Real) — mesma automação do modal individual (ver
            # main.py). Status em si não é campo editável nesta tela.
            if classificar_conclusao:
                a = by_id[id_]
                data_status = {campo: vv["novo"] for campo, vv in v["progresso"].items()}
                classificar_conclusao(a, data_status)
                if validar_consistencia_conclusao:
                    try:
                        validar_consistencia_conclusao(a, data_status)
                    except ValueError as e:
                        rotulo = (f'{a.get("codigo_wbs")} — ' if a.get("codigo_wbs") else "") + a["nome"]
                        raise ValueError(f'"{rotulo}": {e}')
                novo_status = data_status.get("status")
                if novo_status and novo_status != a.get("status"):
                    item["status_atual"] = a.get("status")
                    item["status_novo"] = novo_status

        if v["recursos_adicionar"] or v["recursos_remover"]:
            item["recursos_adicionar"] = v["recursos_adicionar"]
            item["recursos_remover"] = v["recursos_remover"]
            item["mudou"] = True

    return {"itens": itens}


def aplicar(projeto_id, edicoes, status_familia_concluida, prioridade_validas,
            classificar_conclusao=None, validar_consistencia_conclusao=None,
            autor_id=None, autor_nome=None):
    """Roda `calcular()` e grava de fato — em lote, numa única transação
    (mesmo padrão de `cpm.recalcular`/`cronograma_replanejamento.aplicar`:
    nunca uma UPDATE por linha). `autor_id`/`autor_nome` identificam quem
    está confirmando a edição, usado só nos relatos de progresso criados
    automaticamente por esta tela (ver `_avaliar_progresso`)."""
    resultado = calcular(projeto_id, edicoes, status_familia_concluida, prioridade_validas,
                          classificar_conclusao, validar_consistencia_conclusao)
    itens = [i for i in resultado["itens"] if i["mudou"]]
    if not itens:
        return {"atividades_editadas": 0, "atividades_cascata": 0, "ids_alterados": [], "relatos_criados": 0}

    stmts = ["BEGIN;"]
    relatos_criados = 0
    for i in itens:
        sets = [
            f"dtini_prev={db.q(i['dtini_prev_novo'])}",
            f"dtfim_prev={db.q(i['dtfim_prev_novo'])}",
            f"prazo_horas={db.q(i['prazo_horas_novo'])}",
        ]
        for campo, vv in (i.get("simples") or {}).items():
            sets.append(f"{campo}={db.q(vv['novo'])}")
        if i.get("progresso"):
            for campo, vv in i["progresso"].items():
                sets.append(f"{campo}={db.q(vv['novo'])}")
            if i.get("status_novo"):
                sets.append(f"status={db.q(i['status_novo'])}")
        stmts.append(f"UPDATE atividades SET {', '.join(sets)} WHERE id={db.q(i['id'])};")

        if i.get("progresso"):
            texto_digitado = (i.get("relato_texto") or "").strip()
            if i.get("relato_necessario") == "obrigatorio" and not texto_digitado:
                rotulo = (f'{i["codigo_wbs"]} — ' if i.get("codigo_wbs") else "") + i["nome"]
                raise ValueError(
                    f'"{rotulo}": a mudança de % concluído/data real pedida é um retrocesso (percentual caindo '
                    f'ou data real adiada) — registre um relato explicando o motivo antes de confirmar.'
                )
            texto_final = texto_digitado or i.get("relato_texto_sugerido")
            if texto_final:
                stmts.append(
                    "INSERT INTO atividade_relato (atividade_id, autor_id, autor_nome, texto) VALUES ("
                    f"{db.q(i['id'])}, {db.q(autor_id)}, {db.q(autor_nome)}, {db.q(texto_final)});"
                )
                relatos_criados += 1

        for rid in i.get("recursos_remover") or []:
            stmts.append(
                f"DELETE FROM atividade_recurso WHERE atividade_id={db.q(i['id'])} AND recurso_id={db.q(rid)};"
            )
        for rid in i.get("recursos_adicionar") or []:
            stmts.append(
                "INSERT INTO atividade_recurso (atividade_id, recurso_id) VALUES "
                f"({db.q(i['id'])}, {db.q(rid)}) ON CONFLICT (atividade_id, recurso_id) DO NOTHING;"
            )

    stmts.append("COMMIT;")
    db.execute("\n".join(stmts))
    editadas = sum(1 for i in itens if i["editado"])
    return {
        "atividades_editadas": editadas, "atividades_cascata": len(itens) - editadas,
        "ids_alterados": [i["id"] for i in itens], "relatos_criados": relatos_criados,
    }
