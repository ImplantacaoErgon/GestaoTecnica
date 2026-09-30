"""
Leitura da tela de Comparação Folha (55ª rodada) — listagem paginada,
resumo/KPIs e exportação CSV da tabela `comparacao_folha`.

A CARGA (importação da planilha) fica em app/comparacao_folha_import.py — este
módulo só lê o que já foi importado. Separação deliberada, mesmo espírito de
auditoria.py (escrita) vs. main.py (leitura) — aqui os dois lados já nascem
em arquivos separados porque a carga por si só já é grande (streaming/COPY).

Padrão de paginação e filtros copiado de app/auditoria.py
(_where_filtros/listar/contar/resumo/exportar_csv) — é o único precedente no
projeto de tela com dado grande demais pra carregar tudo de uma vez no
JavaScript (as demais telas carregam tudo e filtram no cliente; aqui, com
~300 mil linhas por competência, isso não é viável).

Confirmado com o usuário (AskUserQuestion, 55ª rodada): a tela é só consulta
(sem edição manual por linha) — "filtros fortes e exportação" pra começar.
"""
from . import db

# 94ª rodada — pedido do usuário: no quadro "Comparação por Tipo de Rubrica"
# do Dashboard, os valores de SITUACAO abaixo passaram a contar como uma
# categoria própria ("Não programada/cadastrada", ver dashboard() mais
# abaixo), deduzida da contagem de Divergente — são casos em que a rubrica
# nem chegou a ser comparada de verdade (não está programada/cadastrada no
# lado Ergon), diferente de uma divergência de VALOR. Confirmado com o
# usuário (AskUserQuestion) quais valores reais de SITUACAO entram aqui —
# lista fechada de propósito (não um padrão tipo "contém NÃO PROGRAMADA"),
# pra não capturar nenhum outro valor de SITUACAO por engano.
SITUACOES_NAO_PROGRAMADA = ("RUBRICA SIGEP NÃO CADASTRADA", "RUBRICA SIGEP NÃO PROGRAMADA")
_SITUACOES_NAO_PROGRAMADA_SQL = "(" + ", ".join(db.q(s) for s in SITUACOES_NAO_PROGRAMADA) + ")"


def _where_filtros(projeto_id=None, mesano=None, situacao=None, tipo_comparacao=None,
                    tiporubr=None, empresa=None, tipovinc=None, rubrica_ergon=None,
                    verba_consist=None, busca=None, so_divergentes=False):
    cond = []
    if projeto_id:
        cond.append(f"projeto_id = {db.q(projeto_id)}")
    if mesano:
        # mesano vem do filtro como "AAAA-MM" — compara só ano/mês, não o dia
        # exato (a coluna sempre guarda o 1º dia do mês, mas não custa ser
        # tolerante a "AAAA-MM-DD" também).
        cond.append(f"to_char(mesano, 'YYYY-MM') = {db.q(str(mesano)[:7])}")
    if situacao:
        cond.append(f"situacao = {db.q(situacao)}")
    if tipo_comparacao:
        cond.append(f"tipo_comparacao = {db.q(tipo_comparacao)}")
    if tiporubr:
        cond.append(f"tiporubr = {db.q(tiporubr)}")
    # 65ª rodada: Empresa/TipoVINC/Rubrica Ergon/Verba Consist — os dois
    # últimos são filtros de igualdade exata (não ILIKE) de propósito: o
    # combo no front-end só oferece códigos que já existem de verdade nos
    # dados (ver resumo() abaixo — rubricas_ergon_disponiveis/
    # verbas_consist_disponiveis), pra não deixar o usuário filtrar por um
    # código "de mentirinha" que nunca vai bater com nada.
    if empresa:
        cond.append(f"empresa_consist = {db.q(empresa)}")
    if tipovinc:
        cond.append(f"tipovinc = {db.q(tipovinc)}")
    if rubrica_ergon:
        cond.append(f"rubrica_ergon = {db.q(rubrica_ergon)}")
    if verba_consist:
        cond.append(f"verba_consist = {db.q(verba_consist)}")
    if so_divergentes:
        cond.append("UPPER(situacao) <> 'NÃO DIVERGENTE'")
    if busca:
        termo = db.q(f"%{busca}%")
        cond.append(
            f"(nome ILIKE {termo} OR matricula ILIKE {termo} OR cpf ILIKE {termo} "
            f"OR rubrica_ergon ILIKE {termo} OR rubrica_nome_ergon ILIKE {termo} "
            f"OR verba_consist ILIKE {termo} OR nomeabrev_consist ILIKE {termo})"
        )
    return " WHERE " + " AND ".join(cond) if cond else ""


def listar(pagina=1, tamanho_pagina=50, **filtros):
    where = _where_filtros(**filtros)
    offset = max(pagina - 1, 0) * tamanho_pagina
    return db.fetch_all(
        f"SELECT * FROM comparacao_folha{where} "
        f"ORDER BY mesano DESC, matricula, rubrica_ergon "
        f"LIMIT {int(tamanho_pagina)} OFFSET {int(offset)}"
    )


def contar(**filtros):
    where = _where_filtros(**filtros)
    row = db.fetch_one(f"SELECT COUNT(*) AS total FROM comparacao_folha{where}")
    return (row or {}).get("total", 0)


def competencias_disponiveis(projeto_id):
    """93ª rodada — pedido do usuário: as abas Comparação Folha e Dashboard
    não podem mais carregar nada automaticamente ao abrir — só depois que o
    usuário escolher uma Competência. Até aqui, as duas telas populavam o
    <select> de Competência chamando resumo()/dashboard() SEM filtro de mês
    (pra saber quais meses existem antes mesmo do usuário escolher algo),
    o que disparava a agregação pesada (COUNT/SUM/GROUP BY em cima da
    tabela inteira, todas as competências) exatamente no momento que o
    pedido quer evitar. Esta consulta serve só pra isso — popular o
    combo — e é enxuta de propósito: usa o índice
    idx_comparacao_folha_projeto_mesano (ver migration_030), sem nenhuma
    agregação além do DISTINCT."""
    meses = db.fetch_all(f"""
        SELECT DISTINCT to_char(mesano, 'YYYY-MM') AS mesano
        FROM comparacao_folha WHERE projeto_id = {db.q(projeto_id)} AND mesano IS NOT NULL
        ORDER BY 1 DESC
    """)
    return [m["mesano"] for m in meses]


def resumo(projeto_id=None, **filtros):
    """KPIs pro topo da tela: total de linhas, quantas são divergentes
    (situação diferente de "Não Divergente"), soma da diferença, e as listas
    de meses/situações/tipos de rubrica disponíveis pra popular os filtros —
    calculadas dentro do mesmo projeto (e mês, se filtrado), não fixas.

    65ª rodada: mais 4 listas de "válidos" pros novos filtros (Empresa,
    TipoVINC, Rubrica Ergon, Verba Consist) — sempre calculadas a partir do
    PROJETO inteiro (where_projeto, ignorando mês/situação/etc já
    selecionados), mesmo critério que meses_disponiveis/
    tipos_rubrica_disponiveis já usavam: o combo precisa mostrar toda opção
    que existe em algum ponto dos dados, não só as que sobram depois do
    filtro atual (senão o usuário nunca conseguiria trocar de filtro pra
    "voltar" a ver uma opção que ficou escondida). Rubrica Ergon e Verba
    Consist vêm como {codigo, nome} (MAX do nome associado a cada código —
    qualquer um serve de rótulo, já que na prática um código sempre carrega
    o mesmo nome) pra o combo mostrar "código — nome", igual já aparece na
    tabela e no modal de detalhe.

    84ª rodada — correção: até aqui, `resumo()` só aceitava `mesano` (o
    `**filtros` abaixo é novo), então os KPIs do topo ("Linhas (filtro
    atual)" etc.) ficavam sempre com o total do mês, ignorando Empresa,
    Rubrica Ergon, Situação e todos os outros filtros que a tela já manda
    pra cá (ver cfFiltrosAtuais()/cfQueryString() no front-end) — o rótulo
    "filtro atual" já prometia isso, só não cumpria. Ficou visível ao testar
    o drill-down do Dashboard (empresa+rubrica aplicados na grade, mas o
    KPI continuava mostrando o total geral do projeto). Os filtros agora
    valem pra `where` (totais e por_situacao); `where_projeto` continua só
    com projeto_id, de propósito, pra não esconder opção nenhuma dos
    combos (ver parágrafo acima).

    89ª rodada — pedido do usuário: o KPI único "Diferença (Consist −
    Ergon)" (soma de `dif_consist_ergon`, coluna que já vem PRONTA do
    arquivo de origem — ver comparacao_folha_import.py) misturava Vantagem
    e Desconto numa soma só, sem muito significado prático (naturezas
    opostas — um desconto "sobrando" no Consist e uma vantagem "faltando"
    no Consist empurram o total pro mesmo lado, mascarando um problema com
    o outro). Virou dois KPIs, um por tipo de rubrica (Vantagem/Desconto),
    e cada um agora é calculado por NÓS — soma de `valor_consist` menos
    soma de `valor_ergon`, filtrado por tiporubr — em vez de somar a
    coluna `dif_consist_ergon` pronta, pra não depender de aquela coluna
    do arquivo de origem representar exatamente essa mesma conta (nunca
    conferimos isso). Linhas sem tiporubr 'VANTAGEM' nem 'DESCONTO'
    (outros tipos, ou tipo não informado) não entram em nenhum dos dois —
    ficam de fora desses dois KPIs (mas continuam contadas normalmente em
    "Linhas"/"Divergentes" e na grade)."""
    where = _where_filtros(projeto_id=projeto_id, **filtros)
    row = db.fetch_one(f"""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE') AS total_divergentes,
          COALESCE(SUM(valor_consist) FILTER (WHERE UPPER(tiporubr) = 'VANTAGEM'), 0)
            - COALESCE(SUM(valor_ergon) FILTER (WHERE UPPER(tiporubr) = 'VANTAGEM'), 0) AS diferenca_vantagem,
          COALESCE(SUM(valor_consist) FILTER (WHERE UPPER(tiporubr) = 'DESCONTO'), 0)
            - COALESCE(SUM(valor_ergon) FILTER (WHERE UPPER(tiporubr) = 'DESCONTO'), 0) AS diferenca_desconto
        FROM comparacao_folha{where}
    """)
    where_projeto = _where_filtros(projeto_id=projeto_id)
    conector = " AND " if where_projeto else " WHERE "
    por_situacao = db.fetch_all(f"""
        SELECT situacao, COUNT(*) AS total
        FROM comparacao_folha{where}
        GROUP BY situacao ORDER BY total DESC
    """)
    meses = db.fetch_all(f"""
        SELECT DISTINCT to_char(mesano, 'YYYY-MM') AS mesano
        FROM comparacao_folha{where_projeto}{conector}mesano IS NOT NULL ORDER BY 1 DESC
    """)
    tipos_rubrica = db.fetch_all(f"""
        SELECT DISTINCT tiporubr FROM comparacao_folha{where_projeto}{conector}tiporubr IS NOT NULL ORDER BY 1
    """)
    empresas = db.fetch_all(f"""
        SELECT DISTINCT empresa_consist FROM comparacao_folha{where_projeto}{conector}empresa_consist IS NOT NULL ORDER BY 1
    """)
    tipovinc = db.fetch_all(f"""
        SELECT DISTINCT tipovinc FROM comparacao_folha{where_projeto}{conector}tipovinc IS NOT NULL ORDER BY 1
    """)
    rubricas_ergon = db.fetch_all(f"""
        SELECT rubrica_ergon AS codigo, MAX(rubrica_nome_ergon) AS nome
        FROM comparacao_folha{where_projeto}{conector}rubrica_ergon IS NOT NULL
        GROUP BY rubrica_ergon ORDER BY 1
    """)
    verbas_consist = db.fetch_all(f"""
        SELECT verba_consist AS codigo, MAX(nomeabrev_consist) AS nome
        FROM comparacao_folha{where_projeto}{conector}verba_consist IS NOT NULL
        GROUP BY verba_consist ORDER BY 1
    """)
    # 92ª rodada — lista de Tipo de Comparação disponíveis, pro combo da
    # nova "Consulta dinâmica" (ver consulta_dinamica() abaixo). tipo_comparacao
    # já era aceito por _where_filtros desde o início (55ª rodada), só nunca
    # tinha um combo na tela pra usá-lo como filtro.
    tipos_comparacao = db.fetch_all(f"""
        SELECT DISTINCT tipo_comparacao FROM comparacao_folha{where_projeto}{conector}tipo_comparacao IS NOT NULL ORDER BY 1
    """)
    return {
        "total": (row or {}).get("total", 0),
        "total_divergentes": (row or {}).get("total_divergentes", 0),
        "diferenca_vantagem": (row or {}).get("diferenca_vantagem", 0),
        "diferenca_desconto": (row or {}).get("diferenca_desconto", 0),
        "por_situacao": por_situacao,
        "meses_disponiveis": [m["mesano"] for m in meses],
        "tipos_rubrica_disponiveis": [t["tiporubr"] for t in tipos_rubrica],
        "empresas_disponiveis": [e["empresa_consist"] for e in empresas],
        "tipovinc_disponiveis": [t["tipovinc"] for t in tipovinc],
        "rubricas_ergon_disponiveis": rubricas_ergon,
        "verbas_consist_disponiveis": verbas_consist,
        "tipos_comparacao_disponiveis": [t["tipo_comparacao"] for t in tipos_comparacao],
    }


# ---- Consulta dinâmica de quantidades (92ª rodada) ----
# Pedido do usuário (verbatim): "quero informar o tipo de comparação, a
# empresa, o tipo da rubrica, a verba consist ou rubrica ergon, enfim, quero
# informar alguns campos e receber as quantidades." Com dois exemplos: (1)
# informando Tipo de Rubrica + Verba Consist mas NÃO Tipo de Comparação, o
# retorno deve trazer a quantidade de CADA Tipo de Comparação pra esse
# filtro; (2) informando só Tipo de Comparação, o retorno deve trazer a
# quantidade (um número só) desse Tipo de Comparação.
#
# Confirmado com o usuário (AskUserQuestion): a regra é genérica, não
# específica do Tipo de Comparação — QUALQUER um dos 5 campos abaixo que for
# deixado em branco vira uma dimensão de agrupamento (GROUP BY) no
# resultado; os que forem preenchidos viram filtro (WHERE) e saem do
# agrupamento. Se os 5 forem preenchidos, não sobra nenhuma dimensão — o
# resultado é uma quantidade só (o total que bate com todos os filtros),
# generalizando o Exemplo 2. `mesano` (Competência) fica de fora dessa
# lista de propósito: é só mais um filtro de escopo (nunca vira dimensão),
# senão misturar competências diferentes na mesma quebra confundiria mais
# do que ajudaria.
_CAMPOS_CONSULTA_DINAMICA = [
    ("tipo_comparacao", "tipo_comparacao"),
    ("empresa", "empresa_consist"),
    ("tiporubr", "tiporubr"),
    ("rubrica_ergon", "rubrica_ergon"),
    ("verba_consist", "verba_consist"),
]

# Mesmo espírito de LIMITE_EMPRESA_RUBRICA/LIMITE_EXPORT_CSV acima — teto de
# segurança pro caso de o usuário deixar vários (ou todos) os 5 campos em
# branco ao mesmo tempo, o que pode gerar muitas combinações distintas.
LIMITE_CONSULTA_DINAMICA = 2000


def consulta_dinamica(projeto_id, mesano=None, tipo_comparacao=None, empresa=None,
                       tiporubr=None, rubrica_ergon=None, verba_consist=None):
    """Ver comentário do bloco acima pra regra completa. Devolve
    `campos_agrupados` (quais dos 5 campos viraram dimensão — lista vazia
    quando os 5 foram preenchidos) e `linhas` (uma linha por combinação
    encontrada, com `quantidade`; uma única linha só com `quantidade` quando
    não há dimensão nenhuma)."""
    valores = {
        "tipo_comparacao": tipo_comparacao, "empresa": empresa, "tiporubr": tiporubr,
        "rubrica_ergon": rubrica_ergon, "verba_consist": verba_consist,
    }
    campos_grupo = [(campo, coluna) for campo, coluna in _CAMPOS_CONSULTA_DINAMICA if not valores[campo]]

    where = _where_filtros(
        projeto_id=projeto_id, mesano=mesano, tipo_comparacao=tipo_comparacao,
        empresa=empresa, tiporubr=tiporubr, rubrica_ergon=rubrica_ergon, verba_consist=verba_consist,
    )

    if not campos_grupo:
        # Os 5 campos foram preenchidos — nenhuma dimensão sobra pra
        # agrupar, então o resultado é só a contagem total (Exemplo 2 do
        # pedido do usuário, generalizado pros 5 campos).
        row = db.fetch_one(f"SELECT COUNT(*) AS quantidade FROM comparacao_folha{where}")
        return {
            "campos_agrupados": [],
            "linhas": [{"quantidade": (row or {}).get("quantidade", 0)}],
            "truncado": False,
        }

    # Cada campo deixado em branco entra no SELECT/GROUP BY (aliado ao NOME
    # DO CAMPO, não ao nome da coluna no banco — "empresa", não
    # "empresa_consist" — pro front-end usar a mesma chave nos dois casos).
    # COALESCE evita que uma linha com o campo NULL simplesmente suma do
    # agrupamento (GROUP BY trata NULL como um grupo à parte normalmente,
    # mas o rótulo "(vazio)" deixa isso explícito pro usuário em vez de uma
    # célula em branco na tabela).
    selects = ", ".join(f"COALESCE({coluna}, '(vazio)') AS {campo}" for campo, coluna in campos_grupo)
    group_by = ", ".join(coluna for _, coluna in campos_grupo)
    linhas = db.fetch_all(f"""
        SELECT {selects}, COUNT(*) AS quantidade, COUNT(*) OVER() AS total_combinacoes
        FROM comparacao_folha{where}
        GROUP BY {group_by}
        ORDER BY quantidade DESC
        LIMIT {LIMITE_CONSULTA_DINAMICA}
    """)
    total_combinacoes = linhas[0]["total_combinacoes"] if linhas else 0
    for linha in linhas:
        linha.pop("total_combinacoes", None)
    return {
        "campos_agrupados": [campo for campo, _ in campos_grupo],
        "linhas": linhas,
        "truncado": total_combinacoes > LIMITE_CONSULTA_DINAMICA,
    }


# 84ª rodada — teto do quadro "Top divergências por Empresa × Rubrica Ergon"
# (ver dashboard() abaixo): decidido com o usuário que esse quadro mostra só
# as piores combinações, não a lista inteira (que pode chegar a milhares).
LIMITE_EMPRESA_RUBRICA = 25

# ---- Dashboard de Convergência (65ª rodada) ----
# Consultas pensadas pra nortear o trabalho de aumentar a convergência entre
# Ergon e o sistema legado (pedido do usuário) — não é só um espelho dos
# dados, cada consulta aqui tem uma pergunta de negócio por trás:
#   - "geral"/"por_situacao": de tudo que foi comparado, quanto já bate sem
#     divergência (mesmo critério de "divergente" já usado no resto da tela:
#     situação diferente de "Não Divergente")? Quebrado por situação pra
#     mostrar ONDE está o problema (ex: "Sem Provimento" pode ser uma
#     categoria inteira ainda não mapeada, não um monte de erros soltos).
#   - "por_empresa": quantos CÓDIGOS distintos de rubrica cada lado usa, por
#     empresa — não é sobre valor financeiro (o usuário pediu explicitamente
#     pra deixar de lado), é sobre COBERTURA: uma empresa com 40 rubricas
#     Ergon e só 12 verbas Consist aparecendo na comparação é sinal de que
#     o de-para daquela empresa provavelmente está incompleto.
#   - "cobertura_mapeamento": cruza com a tabela `rubricas` (parametrização
#     -- de-para oficial Ergon x legado, ver db/migration_029_rubricas.sql)
#     pra apontar DUAS lacunas diferentes, cada uma pedindo uma ação
#     diferente da equipe:
#       (a) rubrica/verba JÁ PARAMETRIZADA que nunca aparece na Comparação
#           — ou a regra ainda não está em produção, ou ninguém recebeu
#           esse provento/desconto ainda, ou o de-para está errado.
#       (b) rubrica/verba usada NA COMPARAÇÃO que não bate com NENHUM
#           código parametrizado — sinal de rubrica nova, ainda não
#           levantada, ou erro de digitação/código no legado.
#     Rubricas com status 'Excluída' não contam como "parametrizadas
#     válidas" pra este cruzamento (foram descartadas de propósito).
def dashboard(projeto_id, mesano=None):
    # (mesano só filtra "where" — as consultas de cobertura de mapeamento
    # abaixo usam projeto_id direto, sem `where`, porque parametrização de
    # Rubricas não é mensal: ver comentário do bloco.)
    where = _where_filtros(projeto_id=projeto_id, mesano=mesano)

    geral = db.fetch_one(f"""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE UPPER(situacao) = 'NÃO DIVERGENTE') AS total_nao_divergentes
        FROM comparacao_folha{where}
    """) or {"total": 0, "total_nao_divergentes": 0}
    total_geral = geral.get("total") or 0
    # total_divergentes/pct_divergente calculados aqui (não como "100 - pct_nao_divergente"
    # no front) pra bater exatamente com a contagem real de linhas divergentes, sem
    # arredondamento em cascata — 69ª/70ª rodada, pedido do usuário pro Dashboard de
    # Comparação de Folha mostrar também o % COM divergência, não só o % sem.
    geral["total_divergentes"] = total_geral - (geral.get("total_nao_divergentes") or 0)
    geral["pct_nao_divergente"] = round(100 * (geral.get("total_nao_divergentes") or 0) / total_geral, 1) if total_geral else None
    geral["pct_divergente"] = round(100 * geral["total_divergentes"] / total_geral, 1) if total_geral else None

    por_situacao = db.fetch_all(f"""
        SELECT
          COALESCE(situacao, '(sem situação)') AS situacao,
          COUNT(*) AS total,
          ROUND(100.0 * COUNT(*) / {total_geral or 1}, 1) AS pct_do_total
        FROM comparacao_folha{where}
        GROUP BY situacao ORDER BY total DESC
    """)

    # 81ª rodada: mesma pergunta de "onde focar primeiro" de por_situacao,
    # mas quebrada por Tipo de Rubrica (tiporubr) em vez de situação — pedido
    # do usuário pra ficar ao lado do quadro "% de comparação por Situação"
    # no dashboard. Diferente de por_situacao (que só tem o total e o % do
    # total geral), aqui o total sem divergência e o divergente vêm cada um
    # com sua própria contagem e percentual (sobre o total DAQUELE tipo de
    # rubrica, não do total geral) — é o que permite comparar tipos de
    # rubrica entre si por taxa de acerto, não só por volume.
    #
    # 94ª rodada — pedido do usuário: "Não programada/cadastradas" virou uma
    # terceira categoria neste quadro, DEDUZIDA da contagem/% de Divergente
    # (não somada à parte) — linhas cuja SITUACAO indica que a rubrica nem
    # chegou a ser comparada de verdade (não programada/cadastrada no lado
    # Ergon), diferente de uma divergência de VALOR de verdade. Confirmado
    # com o usuário (AskUserQuestion) os valores reais de SITUACAO que
    # entram aqui: "RUBRICA SIGEP NÃO CADASTRADA" e "RUBRICA SIGEP NÃO
    # PROGRAMADA" (ver SITUACOES_NAO_PROGRAMADA no topo do arquivo). O
    # rótulo "Sem divergência" também virou "Convergente" nesta rodada —
    # só no front-end (o nome do campo aqui, total_sem_divergencia, foi
    # mantido de propósito, pra não obrigar mudança em nenhum outro lugar
    # que já lê esse mesmo campo).
    por_tiporubr = db.fetch_all(f"""
        SELECT
          COALESCE(tiporubr, '(sem tipo)') AS tiporubr,
          COUNT(*) AS total_linhas,
          COUNT(*) FILTER (WHERE UPPER(situacao) = 'NÃO DIVERGENTE') AS total_sem_divergencia,
          COUNT(*) FILTER (WHERE UPPER(situacao) IN {_SITUACOES_NAO_PROGRAMADA_SQL}) AS total_nao_programada,
          COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE' AND UPPER(situacao) NOT IN {_SITUACOES_NAO_PROGRAMADA_SQL}) AS total_divergente,
          ROUND(100.0 * COUNT(*) FILTER (WHERE UPPER(situacao) = 'NÃO DIVERGENTE') / GREATEST(COUNT(*), 1), 1) AS pct_sem_divergencia,
          ROUND(100.0 * COUNT(*) FILTER (WHERE UPPER(situacao) IN {_SITUACOES_NAO_PROGRAMADA_SQL}) / GREATEST(COUNT(*), 1), 1) AS pct_nao_programada,
          ROUND(100.0 * COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE' AND UPPER(situacao) NOT IN {_SITUACOES_NAO_PROGRAMADA_SQL}) / GREATEST(COUNT(*), 1), 1) AS pct_divergente
        FROM comparacao_folha{where}
        GROUP BY tiporubr ORDER BY total_linhas DESC
    """)

    # 84ª rodada — pedido do usuário (verbatim): "Quadro rubricas por empresa:
    # Empresa, rubrica ergon, qtd, sem divergencia, qtd com divergencia %
    # sem divergencia, % com divergencia" e um segundo quadro ainda mais
    # granular (Empresa × Rubrica × Situação). Como uma empresa real tem
    # dezenas/centenas de rubricas Ergon distintas, a combinação (empresa,
    # rubrica_ergon) pode chegar a milhares de linhas — listar TODAS não
    # rende um "quadro gerencial" (quadro pra apontar onde agir), vira uma
    # descarga de dados. Decidido com o usuário (AskUserQuestion): este
    # quadro mostra só as combinações COM divergência (HAVING abaixo),
    # ordenadas pela quantidade divergente (impacto absoluto, não só taxa —
    # uma combinação com 3.000 linhas divergentes pesa mais no trabalho de
    # correção que uma com 2 linhas 100% divergentes), limitadas às
    # LIMITE_EMPRESA_RUBRICA piores. `COUNT(*) OVER()` (antes do LIMIT, veja
    # o CTE) devolve quantas combinações COM divergência existem ao todo,
    # pro front-end poder dizer "mostrando 25 de N" em vez de dar a entender
    # que aquela é a lista completa.
    #
    # O segundo quadro pedido (por Situação) virou um DRILL-DOWN em vez de
    # uma segunda tabela estática: clicar numa linha aqui abre a aba
    # "Comparação Folha" já filtrada por aquela empresa+rubrica (reaproveita
    # os filtros que já existem na tela — ver cfdAbrirDetalheEmpresaRubrica
    # no front-end), mostrando o detalhe por situação sob demanda, sem
    # pré-computar todas as combinações de empresa×rubrica×situação (que
    # seria ainda maior que esta aqui) de uma vez só.
    #
    # 86ª rodada — pedido do usuário: mostrar também o Tipo de Rubrica (antes
    # da coluna Rubrica Ergon) e ordenar priorizando VANTAGEM primeiro, depois
    # DESCONTO (dentro de cada grupo, mantém a ordenação por divergência já
    # existente). `MAX(tiporubr)` dentro do CTE (não entra no GROUP BY) — em
    # tese tiporubr é uma propriedade da própria rubrica, então todo mundo
    # numa combinação empresa+rubrica_ergon já compartilha o mesmo tipo; usar
    # MAX() em vez de adicionar ao GROUP BY evita que uma eventual
    # inconsistência de dados (mesma rubrica com tiporubr gravado diferente
    # em linhas diferentes) quebre a combinação em duas linhas separadas no
    # quadro. O CASE de prioridade usa UPPER() pra não depender de como o
    # arquivo de origem grava o valor (maiúsculo/minúsculo).
    por_empresa_rubrica = db.fetch_all(f"""
        WITH combinacoes AS (
          SELECT
            COALESCE(empresa_consist, '(sem empresa)') AS empresa,
            rubrica_ergon,
            MAX(rubrica_nome_ergon) AS rubrica_nome_ergon,
            COALESCE(MAX(tiporubr), '(sem tipo)') AS tiporubr,
            COUNT(*) AS total_linhas,
            COUNT(*) FILTER (WHERE UPPER(situacao) = 'NÃO DIVERGENTE') AS total_sem_divergencia,
            COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE') AS total_divergente
          FROM comparacao_folha{where} AND rubrica_ergon IS NOT NULL
          GROUP BY empresa_consist, rubrica_ergon
          HAVING COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE') > 0
        )
        SELECT
          empresa, tiporubr, rubrica_ergon, rubrica_nome_ergon, total_linhas, total_sem_divergencia, total_divergente,
          ROUND(100.0 * total_sem_divergencia / GREATEST(total_linhas, 1), 1) AS pct_sem_divergencia,
          ROUND(100.0 * total_divergente / GREATEST(total_linhas, 1), 1) AS pct_divergente,
          COUNT(*) OVER() AS total_combinacoes_com_divergencia
        FROM combinacoes
        ORDER BY
          CASE
            WHEN UPPER(tiporubr) = 'VANTAGEM' THEN 1
            WHEN UPPER(tiporubr) = 'DESCONTO' THEN 2
            ELSE 3
          END,
          total_divergente DESC, total_linhas DESC
        LIMIT {LIMITE_EMPRESA_RUBRICA}
    """)
    total_combinacoes_empresa_rubrica = (por_empresa_rubrica[0]["total_combinacoes_com_divergencia"]
                                          if por_empresa_rubrica else 0)
    for linha in por_empresa_rubrica:
        linha.pop("total_combinacoes_com_divergencia", None)

    por_empresa = db.fetch_all(f"""
        SELECT
          COALESCE(empresa_consist, '(sem empresa)') AS empresa,
          COUNT(*) AS total_linhas,
          COUNT(DISTINCT rubrica_ergon) FILTER (WHERE rubrica_ergon IS NOT NULL) AS qtd_rubricas_ergon,
          COUNT(DISTINCT verba_consist) FILTER (WHERE verba_consist IS NOT NULL) AS qtd_verbas_consist,
          COUNT(*) FILTER (WHERE UPPER(situacao) = 'NÃO DIVERGENTE') AS total_nao_divergentes,
          ROUND(100.0 * COUNT(*) FILTER (WHERE UPPER(situacao) = 'NÃO DIVERGENTE') / GREATEST(COUNT(*), 1), 1) AS pct_nao_divergente
        FROM comparacao_folha{where}
        GROUP BY empresa_consist
        ORDER BY total_linhas DESC
    """)

    # Cobertura de mapeamento — cruza com a parametrização de Rubricas do
    # MESMO projeto (ignora o filtro de mês: parametrização não é mensal).
    rubricas_parametrizadas_sem_uso = db.fetch_all(f"""
        SELECT r.codigo_ergon AS codigo, MAX(r.nome_abreviado) AS nome, COUNT(*) AS linhas_parametrizadas
        FROM rubricas r
        WHERE r.projeto_id = {db.q(projeto_id)} AND r.status <> 'Excluída' AND r.codigo_ergon IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM comparacao_folha cf
            WHERE cf.projeto_id = r.projeto_id AND cf.rubrica_ergon = r.codigo_ergon
          )
        GROUP BY r.codigo_ergon ORDER BY 1
    """)
    verbas_parametrizadas_sem_uso = db.fetch_all(f"""
        SELECT r.verba_legado AS codigo, MAX(r.descricao_legado) AS nome, COUNT(*) AS linhas_parametrizadas
        FROM rubricas r
        WHERE r.projeto_id = {db.q(projeto_id)} AND r.status <> 'Excluída' AND r.verba_legado IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM comparacao_folha cf
            WHERE cf.projeto_id = r.projeto_id AND cf.verba_consist = r.verba_legado
          )
        GROUP BY r.verba_legado ORDER BY 1
    """)
    rubricas_sem_parametrizacao = db.fetch_all(f"""
        SELECT cf.rubrica_ergon AS codigo, MAX(cf.rubrica_nome_ergon) AS nome, COUNT(*) AS linhas
        FROM comparacao_folha cf
        WHERE cf.projeto_id = {db.q(projeto_id)} AND cf.rubrica_ergon IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM rubricas r
            WHERE r.projeto_id = cf.projeto_id AND r.status <> 'Excluída' AND r.codigo_ergon = cf.rubrica_ergon
          )
        GROUP BY cf.rubrica_ergon ORDER BY linhas DESC
    """)
    verbas_sem_parametrizacao = db.fetch_all(f"""
        SELECT cf.verba_consist AS codigo, MAX(cf.nomeabrev_consist) AS nome, COUNT(*) AS linhas
        FROM comparacao_folha cf
        WHERE cf.projeto_id = {db.q(projeto_id)} AND cf.verba_consist IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM rubricas r
            WHERE r.projeto_id = cf.projeto_id AND r.status <> 'Excluída' AND r.verba_legado = cf.verba_consist
          )
        GROUP BY cf.verba_consist ORDER BY linhas DESC
    """)

    return {
        "geral": geral,
        "por_situacao": por_situacao,
        "por_tiporubr": por_tiporubr,
        "por_empresa_rubrica": por_empresa_rubrica,
        "total_combinacoes_empresa_rubrica": total_combinacoes_empresa_rubrica,
        "por_empresa": por_empresa,
        "cobertura_mapeamento": {
            "rubricas_parametrizadas_sem_uso": rubricas_parametrizadas_sem_uso,
            "verbas_parametrizadas_sem_uso": verbas_parametrizadas_sem_uso,
            "rubricas_sem_parametrizacao": rubricas_sem_parametrizacao,
            "verbas_sem_parametrizacao": verbas_sem_parametrizacao,
        },
    }


# Exportação respeita os filtros, mas com um teto de segurança — diferente de
# auditoria.exportar_csv (sem teto): lá o volume é de eventos de sistema
# (milhares, não centenas de milhares), aqui uma exportação sem filtro
# nenhum facilmente passaria de 250 mil linhas. Quem precisar de mais do que
# isso deve filtrar mais (por competência, situação etc.) antes de exportar.
LIMITE_EXPORT_CSV = 100_000

_COLUNAS_EXPORT = [
    "tipo_comparacao", "situacao", "matricula", "nome", "cpf", "mesano", "folha",
    "orgao", "cargo", "funcao", "rubrica_ergon", "rubrica_nome_ergon", "valor_ergon",
    "verba_consist", "nomeabrev_consist", "valor_consist", "dif_consist_ergon",
    "tiporubr", "vantagem", "obs_evento", "obs_atributo",
]
_ROTULOS_EXPORT = [
    "Tipo Comparação", "Situação", "Matrícula", "Nome", "CPF", "Competência", "Folha",
    "Órgão", "Cargo", "Função", "Rubrica Ergon", "Rubrica Nome Ergon", "Valor Ergon",
    "Verba Consist", "Nome Abrev Consist", "Valor Consist", "Diferença (Consist - Ergon)",
    "Tipo Rubrica", "Vantagem/Desconto", "Obs. Evento", "Obs. Atributo",
]


def exportar_csv(**filtros):
    """Retorna (cabecalho, linhas, truncado) — truncado=True se o total de
    linhas que bateria com os filtros passa do teto (LIMITE_EXPORT_CSV), caso
    em que só as primeiras LIMITE_EXPORT_CSV (pela mesma ordenação da
    listagem) saem no CSV. Só as colunas mais úteis pra exportação saem aqui
    (não as 65) — quem precisar do dado bruto completo pode pedir uma
    exportação maior sob demanda."""
    where = _where_filtros(**filtros)
    total = contar(**filtros)
    linhas_raw = db.fetch_all(
        f"SELECT {', '.join(_COLUNAS_EXPORT)} FROM comparacao_folha{where} "
        f"ORDER BY mesano DESC, matricula, rubrica_ergon LIMIT {LIMITE_EXPORT_CSV}"
    )
    corpo = [[l.get(c) for c in _COLUNAS_EXPORT] for l in linhas_raw]
    return _ROTULOS_EXPORT, corpo, total > LIMITE_EXPORT_CSV
