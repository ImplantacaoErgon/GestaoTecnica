"""
"Minhas atividades" — grade semanal do consultor logado.

Cada usuário logado (tabela `usuarios`) enxerga aqui as atividades do
cronograma em que o profissional vinculado a ele (tabela `recursos`) é um dos
participantes (tabela `atividade_recurso`, N:N — desde a migração 012, uma
atividade pode ter quantos participantes forem necessários, de qualquer tipo
de vínculo). O vínculo "usuário logado -> profissional" não é uma coluna
nova: é resolvido em tempo de consulta comparando o e-mail de login com o
e-mail cadastrado no Recurso (ambos case-insensitive) — ver
`recursos_do_usuario()`. Um usuário cujo e-mail não bate com nenhum Recurso
cadastrado, ou que bate com um Recurso sem `controla_horas` habilitado
(migração 013), simplesmente não tem nada pra ver aqui (a rota devolve
`vinculado: False` com um `motivo`, e o front-end explica o que fazer em
cada caso).

Desde a 16ª rodada, a grade traz atividades de TODOS os projetos em que o
profissional está delegado, não só do projeto selecionado no cabeçalho —
"Minhas atividades" é o apontamento pessoal de horas do consultor na
empresa, e `recursos` sempre foi uma tabela global (sem `projeto_id`), então
não faz sentido restringir a um projeto só quando a mesma pessoa pode
atender vários clientes ao mesmo tempo, inclusive na mesma semana. Ver
`montar_grade()`.

Para cada atividade, a grade mostra os dias úteis da semana escolhida (por
padrão, a semana atual) com a hora prevista de cada dia. Essa quebra diária é
gerada automaticamente, uma única vez POR PARTICIPANTE (`garantir_dias()`),
dividindo igualmente pelos dias úteis entre `dtini_prev` e `dtfim_prev` a
quantidade de horas **alocada aquele participante especificamente**
(`atividade_recurso.horas_alocadas`, 18ª rodada de features "extra"); quando
ninguém informou uma hora alocada pra ele, cai no comportamento original —
usa `atividades.prazo_horas`, a atividade inteira. Ou seja, numa atividade
com vários participantes, cada um pode ter sua própria fatia (ex: 10h pra um
consultor, 6h pra outro, de um total de 16h) ou, se a alocação for deixada
em branco, continuar vendo a atividade inteira como antes. De qualquer
forma, cada participante passa a ter sua própria linha em
`atividade_horas_dia`, dali em diante a fonte de verdade daquele participante
naquela atividade, dia a dia (editar o total geral, a alocação ou as datas
da atividade depois não regenera a grade — ver comentário em db/schema.sql,
seção 18).

Cabe a cada consultor, em cada dia de CADA atividade em que participa: (a)
ajustar o número de horas previstas daquele dia (`ajustar_dia`), ou (b)
simplesmente confirmar a execução (`confirmar_dia`, que grava
`horas_realizadas` = o valor informado, ou o previsto atual se nada for
informado). Editar o previsto de um dia já confirmado desfaz a confirmação
(força reconfirmar), pra nunca deixar uma hora "confirmada" que não
corresponde mais ao número exibido.

`atividades.horas_realizadas` (o total agregado, editável manualmente no
modal da atividade desde a 4ª rodada) é INDEPENDENTE do que acontece
aqui — nenhuma ação nesta tela grava nesse campo. Isso já foi tentado uma vez
(16ª rodada, parte 3: um cálculo automático que somava os dias confirmados) e
revertido a pedido do usuário: uma atividade do cronograma pode ter vários
recursos envolvidos (ex: uma reunião gerencial com 4 pessoas da consultoria e
5 do cliente), e cada um aponta e confirma as PRÓPRIAS horas aqui, de forma
totalmente independente dos demais (`atividade_horas_dia` é por
`atividade_id` + `recurso_id`, não mais só por `atividade_id`) — mas isso não
deve alterar o total previsto/realizado da atividade no Cronograma, que
continua sendo um número único, editado à parte (ex: a reunião continua
prevista/realizada em 2h no Cronograma, independente de quantas pessoas
participaram ou de quantas horas cada uma apontou individualmente).
"""
import uuid
from datetime import date, timedelta

from . import db


class MinhasAtividadesError(Exception):
    """Erro esperado (autorização, validação) — sempre devolvido como 400/404
    pela rota, nunca um 500 genérico."""
    pass


def _parse_data(v):
    if v is None:
        return None
    return date.fromisoformat(v) if isinstance(v, str) else v


def recursos_do_usuario(email, apenas_com_controle_horas=True):
    """Ids de todos os Recursos cujo e-mail cadastrado bate (case-insensitive)
    com o e-mail de login do usuário. Normalmente 0 ou 1, mas nada impede mais
    de um cadastro de Recurso com o mesmo e-mail — nesse caso, atividades
    delegadas a qualquer um deles aparecem juntas.

    Por padrão só devolve recursos com `controla_horas = true` (migração
    013) — só esses podem registrar apontamento em "Minhas atividades" e
    aparecer no Relatório de Horas dos Recursos. Passe
    `apenas_com_controle_horas=False` para checar se existe ALGUM cadastro
    vinculado ao e-mail, independente da marca — usado só por `montar_grade()`
    para distinguir, na mensagem ao usuário, "não achei nenhum recurso com
    esse e-mail" de "achei, mas ele não tem controle de horas habilitado"."""
    email = (email or "").strip().lower()
    if not email:
        return []
    filtro = "AND controla_horas = TRUE" if apenas_com_controle_horas else ""
    linhas = db.fetch_all(
        f"SELECT id, nome, controla_horas FROM recursos WHERE lower(email) = {db.q(email)} {filtro}"
    )
    return linhas


def semana_de(data_ref: date):
    """Segunda a sexta (5 datas) da semana que contém data_ref."""
    segunda = data_ref - timedelta(days=data_ref.weekday())
    return [segunda + timedelta(days=i) for i in range(5)]


def _non_working(projeto_id):
    """Exceções ao calendário padrão (feriados/recessos) cadastradas para o
    projeto — mesma fonte usada pelo CPM (ver backend/app/cpm.py)."""
    excecoes = db.fetch_all(
        f"SELECT data FROM calendario_util WHERE projeto_id = {db.q(projeto_id)} AND util = FALSE"
    )
    return {_parse_data(e["data"]) for e in excecoes}


def _dias_uteis_periodo(data_ini, data_fim, non_working):
    """Lista de dias úteis (seg-sex, exceto exceções em calendario_util)
    entre data_ini e data_fim, inclusive. Se não sobrar nenhum (ex: janela
    cai inteira num fim de semana/feriado, ou datas invertidas), cai para
    [data_ini] como piso de segurança — sempre precisa de ao menos 1 dia
    para distribuir as horas previstas."""
    if data_fim < data_ini:
        data_ini, data_fim = data_fim, data_ini
    dias = []
    d = data_ini
    while d <= data_fim:
        if d.weekday() < 5 and d not in non_working:
            dias.append(d)
        d += timedelta(days=1)
    return dias or [data_ini]


def garantir_dias(atividade, recurso_id, non_working):
    """Gera a quebra diária (atividade_horas_dia) PARA UM PARTICIPANTE
    específico, uma única vez. Idempotente: se já existir qualquer linha
    para este par (atividade, recurso), não faz nada (ver docstring do
    módulo — a grade diária, uma vez criada, é a fonte de verdade daquele
    participante). Não gera nada se a atividade não tiver data de início/fim
    previstas ou nenhuma horas para distribuir (nem alocada, nem prevista).

    18ª rodada de features "extra": a quantidade de horas distribuída é a
    hora ALOCADA daquele participante (`atividade_recurso.horas_alocadas`,
    quando informada) — cada um passa a ver, na própria grade, só a
    parcela que lhe foi alocada, em vez da atividade inteira. Quando não
    informada (`None`), cai no comportamento de sempre: usa
    `atividades.prazo_horas` (a atividade inteira) — dito explicitamente
    pelo usuário como "se não informar nada, assumir as horas da
    atividade". `atividade` já vem com `horas_alocadas` do participante
    específico embutido pela consulta de `montar_grade()`."""
    ja_existe = db.fetch_one(
        f"SELECT 1 AS x FROM atividade_horas_dia "
        f"WHERE atividade_id = {db.q(atividade['id'])} AND recurso_id = {db.q(recurso_id)} LIMIT 1"
    )
    if ja_existe:
        return
    dtini, dtfim = atividade.get("dtini_prev"), atividade.get("dtfim_prev")
    horas_alocadas = atividade.get("horas_alocadas")
    prazo = horas_alocadas if horas_alocadas not in (None, "") else atividade.get("prazo_horas")
    if not dtini or not dtfim or not prazo:
        return
    dtini, dtfim = _parse_data(dtini), _parse_data(dtfim)
    dias = _dias_uteis_periodo(dtini, dtfim, non_working)
    prazo = float(prazo)
    horas_por_dia = round(prazo / len(dias), 2)
    acumulado = 0.0
    valores = []
    for i, d in enumerate(dias):
        # o último dia absorve a diferença de arredondamento, pra soma bater exatamente com prazo_horas
        h = round(prazo - acumulado, 2) if i == len(dias) - 1 else horas_por_dia
        acumulado += h
        valores.append(
            f"({db.q(str(uuid.uuid4()))}, {db.q(atividade['id'])}, {db.q(recurso_id)}, {db.q(d.isoformat())}, {db.q(h)})"
        )
    sql = (
        "INSERT INTO atividade_horas_dia (id, atividade_id, recurso_id, data, horas_previstas) VALUES "
        + ", ".join(valores)
    )
    db.execute(sql)


def montar_grade(usuario, data_ref: date):
    """Monta a resposta completa de GET /api/minhas-atividades: a semana
    (seg-sex) ao redor de data_ref, e cada atividade em que o(s)
    profissional(is) vinculado(s) ao usuário logado participa (tabela
    atividade_recurso), com os 5 dias da grade — cada linha retornada já é
    escopada a UM participante específico (`recurso_id`), então se a mesma
    atividade tiver outros participantes, cada um tem sua própria grade
    independente, vista apenas quando ELE está logado.

    16ª rodada: deixou de receber `projeto_id` e de filtrar por ele. "Minhas
    atividades" é um apontamento pessoal de horas trabalhadas na empresa —
    o mesmo profissional pode estar delegado em atividades de vários
    projetos (clientes) ao mesmo tempo, inclusive na mesma semana, já que
    `recursos` sempre foi uma tabela global (sem `projeto_id`), sem nenhuma
    trava impedindo isso no banco. Cada atividade retornada traz
    `projeto_id`/`projeto_sigla`/`projeto_nome` para o front-end identificar
    de qual projeto ela é.

    18ª rodada: quando `vinculado` dá False, a resposta também traz `motivo`
    ("sem_recurso_vinculado" ou "sem_controle_horas") pra o front-end mostrar
    a mensagem certa — ver `recursos_do_usuario()`."""
    email = usuario.get("email") if usuario else None
    recursos = recursos_do_usuario(email)
    dias_semana = semana_de(data_ref)
    inicio, fim = dias_semana[0], dias_semana[-1]
    semana_info = {
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "dias": [d.isoformat() for d in dias_semana],
    }
    if not recursos:
        motivo = "sem_controle_horas" if recursos_do_usuario(email, apenas_com_controle_horas=False) else "sem_recurso_vinculado"
        return {"vinculado": False, "motivo": motivo, "recurso_nomes": [], "atividades": [], "semana": semana_info}

    recurso_ids = [r["id"] for r in recursos]
    placeholders = ", ".join(db.q(rid) for rid in recurso_ids)
    atividades = db.fetch_all(f"""
        SELECT a.id, a.nome, a.codigo_wbs, a.status, a.prazo_horas, a.dtini_prev, a.dtfim_prev,
               a.projeto_id, p.sigla AS projeto_sigla, p.nome AS projeto_nome,
               e.numero AS etapa_numero, e.nome AS etapa_nome, f.nome AS frente_nome,
               ar.recurso_id AS recurso_id, ar.horas_alocadas AS horas_alocadas
        FROM atividades a
        JOIN atividade_recurso ar ON ar.atividade_id = a.id AND ar.recurso_id IN ({placeholders})
        JOIN projetos p ON p.id = a.projeto_id
        -- LEFT JOIN (migração 026) — etapa_id/frente_trabalho_id podem ser NULL agora;
        -- um INNER JOIN esconderia a atividade da grade do consultor sem aviso nenhum.
        LEFT JOIN etapas e ON e.id = a.etapa_id
        LEFT JOIN frentes_trabalho f ON f.id = a.frente_trabalho_id
        WHERE a.dtini_prev IS NOT NULL AND a.dtfim_prev IS NOT NULL AND a.prazo_horas IS NOT NULL
          AND a.dtini_prev <= {db.q(fim.isoformat())} AND a.dtfim_prev >= {db.q(inicio.isoformat())}
        ORDER BY p.sigla NULLS LAST, p.nome, e.numero, a.codigo_wbs NULLS LAST, a.nome
    """)

    # Calendário de feriados/recessos é por projeto (calendario_util) — com
    # atividades de vários projetos na mesma resposta agora, cada uma usa o
    # calendário do SEU projeto, não de um só; cache simples por projeto_id
    # pra não repetir a consulta a cada atividade do mesmo projeto.
    non_working_por_projeto = {}
    for a in atividades:
        pid = a["projeto_id"]
        if pid not in non_working_por_projeto:
            non_working_por_projeto[pid] = _non_working(pid)
        garantir_dias(a, a["recurso_id"], non_working_por_projeto[pid])

    por_atividade_recurso = {}
    if atividades:
        pares = ", ".join(
            f"({db.q(a['id'])}, {db.q(a['recurso_id'])})" for a in atividades
        )
        linhas = db.fetch_all(f"""
            SELECT * FROM atividade_horas_dia
            WHERE (atividade_id, recurso_id) IN ({pares})
              AND data BETWEEN {db.q(inicio.isoformat())} AND {db.q(fim.isoformat())}
        """)
        for l in linhas:
            chave = (l["atividade_id"], l["recurso_id"])
            por_atividade_recurso.setdefault(chave, {})[_parse_data(l["data"]).isoformat()] = l

    resultado = []
    for a in atividades:
        dtini, dtfim = _parse_data(a["dtini_prev"]), _parse_data(a["dtfim_prev"])
        non_working = non_working_por_projeto[a["projeto_id"]]
        dias_por_data = por_atividade_recurso.get((a["id"], a["recurso_id"]), {})
        dias_resp = []
        for d in dias_semana:
            linha = dias_por_data.get(d.isoformat())
            dias_resp.append({
                "data": d.isoformat(),
                "dentro_periodo": dtini <= d <= dtfim,
                "feriado": d in non_working,
                "id": linha["id"] if linha else None,
                "horas_previstas": linha["horas_previstas"] if linha else None,
                "horas_realizadas": linha["horas_realizadas"] if linha else None,
                "confirmado": bool(linha["confirmado"]) if linha else False,
            })
        resultado.append({**a, "dias": dias_resp})

    return {
        "vinculado": True,
        "recurso_nomes": [r["nome"] for r in recursos],
        "atividades": resultado,
        "semana": semana_info,
    }


def _linha_autorizada(horas_dia_id, recurso_ids):
    """Confirma que a linha existe E que ela pertence a um dos profissionais
    vinculados ao usuário logado — sem isso, qualquer usuário logado poderia
    editar as horas de qualquer atividade só sabendo o id da linha. Desde a
    migração 012, cada linha já pertence a UM participante específico
    (`recurso_id`), então essa checagem também garante que um participante
    nunca mexe nas horas apontadas por OUTRO participante da mesma
    atividade."""
    if not recurso_ids:
        return None
    placeholders = ", ".join(db.q(rid) for rid in recurso_ids)
    return db.fetch_one(f"""
        SELECT * FROM atividade_horas_dia
        WHERE id = {db.q(horas_dia_id)} AND recurso_id IN ({placeholders})
    """)


def _validar_horas(valor):
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        raise MinhasAtividadesError("Informe um número de horas válido.")
    if valor < 0:
        raise MinhasAtividadesError("As horas não podem ser negativas.")
    return round(valor, 2)


def ajustar_dia(horas_dia_id, usuario, horas_previstas):
    """Ajusta a hora prevista de um dia específico. Se o dia já estava
    confirmado, a confirmação é desfeita automaticamente — o valor exibido
    mudou, então uma confirmação anterior não vale mais para esse número."""
    recurso_ids = [r["id"] for r in recursos_do_usuario(usuario.get("email") if usuario else None)]
    linha = _linha_autorizada(horas_dia_id, recurso_ids)
    if not linha:
        raise MinhasAtividadesError("Dia não encontrado ou a atividade não está delegada a você.")
    valor = _validar_horas(horas_previstas)
    return db.execute_returning_one(f"""
        UPDATE atividade_horas_dia
        SET horas_previstas = {db.q(valor)}, confirmado = FALSE, confirmado_em = NULL, confirmado_por = NULL
        WHERE id = {db.q(horas_dia_id)}
        RETURNING *
    """)


def confirmar_dia(horas_dia_id, usuario, horas=None):
    """Confirma a execução de um dia: grava horas_realizadas com o valor
    informado, ou com a hora prevista atual se nada for informado (o caso de
    'apenas confirmar', sem digitar nada de novo)."""
    recurso_ids = [r["id"] for r in recursos_do_usuario(usuario.get("email") if usuario else None)]
    linha = _linha_autorizada(horas_dia_id, recurso_ids)
    if not linha:
        raise MinhasAtividadesError("Dia não encontrado ou a atividade não está delegada a você.")
    valor = _validar_horas(horas) if horas is not None else _validar_horas(linha["horas_previstas"])
    return db.execute_returning_one(f"""
        UPDATE atividade_horas_dia
        SET horas_realizadas = {db.q(valor)}, confirmado = TRUE,
            confirmado_em = now(), confirmado_por = {db.q(usuario["id"])}
        WHERE id = {db.q(horas_dia_id)}
        RETURNING *
    """)


def desconfirmar_dia(horas_dia_id, usuario):
    """Desfaz a confirmação de um dia, liberando pra editar de novo."""
    recurso_ids = [r["id"] for r in recursos_do_usuario(usuario.get("email") if usuario else None)]
    linha = _linha_autorizada(horas_dia_id, recurso_ids)
    if not linha:
        raise MinhasAtividadesError("Dia não encontrado ou a atividade não está delegada a você.")
    return db.execute_returning_one(f"""
        UPDATE atividade_horas_dia
        SET confirmado = FALSE, confirmado_em = NULL, confirmado_por = NULL
        WHERE id = {db.q(horas_dia_id)}
        RETURNING *
    """)
