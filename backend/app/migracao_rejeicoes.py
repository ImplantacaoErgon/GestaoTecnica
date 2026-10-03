"""
Persistência/consulta da carga de rejeitados da migração (116ª rodada) --
ver app/migracao_rejeicoes_import.py pro parser/classificador, e
db/migration_038_migracao_rejeicoes.sql pro esquema. Rotas em app/main.py
(prefixo /api/migracao-rejeicoes).
"""
import json

from . import db
from .migracao_rejeicoes_import import FASE2_DEPARA, FASE3_CARGA_FINAL, processar_planilha

CHUNK_INSERT = 500  # linhas por INSERT -- evita um único statement gigante


def _json_sql(v):
    return db.q(json.dumps(v, ensure_ascii=False, default=str)) + "::jsonb"


def encontrar_ou_criar_ciclo(item_migracao_id, numero_ciclo, data_execucao=None):
    """Reaproveita o conceito já existente de ciclos_migracao (pedido
    verbatim: "Não tratamos essa migração por competência, seriam os
    ciclos da migração") -- se o ciclo já existe pra esse item, usa ele;
    senão cria um novo (data_execucao default = hoje, quando não
    informada), do mesmo jeito que "+ Registrar ciclo" já faz na tela
    manual de Migração de Dados."""
    ciclo = db.fetch_one(
        "SELECT * FROM ciclos_migracao WHERE item_migracao_id = "
        f"{db.q(item_migracao_id)} AND numero_ciclo = {db.q(numero_ciclo)}"
    )
    if ciclo:
        return ciclo
    return db.execute_returning_one(
        "INSERT INTO ciclos_migracao (item_migracao_id, numero_ciclo, data_execucao) "
        f"VALUES ({db.q(item_migracao_id)}, {db.q(numero_ciclo)}, "
        f"{db.q(data_execucao) if data_execucao else 'CURRENT_DATE'}) RETURNING *"
    )


def _recalcular_rejeicoes_ciclo(ciclo_migracao_id):
    """qtd_rejeicoes "preenchido sozinho" (pedido verbatim) a partir do
    detalhe importado -- autoridade é a Fase 3 (carga final: é a rejeição
    de verdade, o que não entrou no Ergon); na ausência dela, usa a Fase
    2 (De-Para) como número provisório, já que ela acontece antes e dá um
    primeiro sinal do tamanho do problema. Sem nenhuma carga pra esse
    ciclo (ex: a única carga foi excluída), volta pro comportamento
    padrão (extraídos - carregados, calculado em create_ciclo/
    update_ciclo) zerando o flag qtd_rejeicoes_detalhada."""
    cargas = db.fetch_all(
        "SELECT fase, total_linhas FROM migracao_rejeicoes_cargas "
        f"WHERE ciclo_migracao_id = {db.q(ciclo_migracao_id)}"
    )
    por_fase = {c["fase"]: c["total_linhas"] for c in cargas}
    if FASE3_CARGA_FINAL in por_fase:
        qtd = por_fase[FASE3_CARGA_FINAL]
    elif FASE2_DEPARA in por_fase:
        qtd = por_fase[FASE2_DEPARA]
    else:
        qtd = None

    if qtd is None:
        ciclo = db.fetch_one(f"SELECT qtd_registros_extraidos, qtd_registros_carregados FROM ciclos_migracao WHERE id = {db.q(ciclo_migracao_id)}")
        extraidos = ciclo.get("qtd_registros_extraidos") if ciclo else None
        carregados = ciclo.get("qtd_registros_carregados") if ciclo else None
        novo_qtd = max(0, int(extraidos) - int(carregados)) if (extraidos is not None and carregados is not None) else None
        db.execute(
            f"UPDATE ciclos_migracao SET qtd_rejeicoes = {db.q(novo_qtd)}, qtd_rejeicoes_detalhada = FALSE "
            f"WHERE id = {db.q(ciclo_migracao_id)}"
        )
    else:
        db.execute(
            f"UPDATE ciclos_migracao SET qtd_rejeicoes = {db.q(qtd)}, qtd_rejeicoes_detalhada = TRUE "
            f"WHERE id = {db.q(ciclo_migracao_id)}"
        )


def importar(projeto_id, item_migracao_id, ciclo_migracao_id, fase, nome_arquivo, conteudo_bytes,
              usuario_id=None, usuario_nome=None):
    """Processa o arquivo, substitui (DELETE + INSERT, nunca acumula) a
    carga anterior dessa combinação (item, ciclo, fase) -- pedido
    verbatim: "Caso seja carregada uma nova versão mas do mesmo ciclo já
    carregado, limpa e carrega de novo" -- e recalcula qtd_rejeicoes do
    ciclo. Devolve a carga criada (dict) já com os totais."""
    linhas = processar_planilha(conteudo_bytes, fase)
    total_linhas = len(linhas)
    total_classificadas = sum(1 for l in linhas if l["tipo_erro_chave"] != "_OUTRO")

    # Substitui a carga antiga dessa MESMA combinação (item, ciclo, fase)
    # -- a cascata (ON DELETE CASCADE) já apaga as linhas de detalhe
    # antigas; outras combinações (outro ciclo, outra fase) não são
    # tocadas, preservando o histórico delas.
    db.execute(
        "DELETE FROM migracao_rejeicoes_cargas WHERE item_migracao_id = "
        f"{db.q(item_migracao_id)} AND ciclo_migracao_id = {db.q(ciclo_migracao_id)} AND fase = {db.q(fase)}"
    )

    carga = db.execute_returning_one(
        "INSERT INTO migracao_rejeicoes_cargas "
        "(projeto_id, item_migracao_id, ciclo_migracao_id, fase, nome_arquivo, total_linhas, "
        "total_classificadas, iniciado_por, iniciado_por_nome) VALUES ("
        f"{db.q(projeto_id)}, {db.q(item_migracao_id)}, {db.q(ciclo_migracao_id)}, {db.q(fase)}, "
        f"{db.q(nome_arquivo)}, {db.q(total_linhas)}, {db.q(total_classificadas)}, "
        f"{db.q(usuario_id)}, {db.q(usuario_nome)}) RETURNING *"
    )

    for inicio in range(0, len(linhas), CHUNK_INSERT):
        bloco = linhas[inicio:inicio + CHUNK_INSERT]
        valores = ", ".join(
            "(" + ", ".join([
                db.q(carga["id"]), db.q(l["linha_planilha"]), db.q(l["tipo_erro_chave"]),
                db.q(l["msg_erro"]), db.q(l["identificador"]), _json_sql(l["dados"]),
            ]) + ")"
            for l in bloco
        )
        db.execute(
            "INSERT INTO migracao_rejeicoes "
            "(carga_id, linha_planilha, tipo_erro_chave, msg_erro, identificador, dados) "
            f"VALUES {valores}"
        )

    _recalcular_rejeicoes_ciclo(ciclo_migracao_id)
    return carga


def excluir_carga(carga_id):
    carga = db.fetch_one(f"SELECT * FROM migracao_rejeicoes_cargas WHERE id = {db.q(carga_id)}")
    if not carga:
        return None
    db.execute(f"DELETE FROM migracao_rejeicoes_cargas WHERE id = {db.q(carga_id)}")
    _recalcular_rejeicoes_ciclo(carga["ciclo_migracao_id"])
    return carga


CARGA_SELECT = """
SELECT c.*, im.nome_tabela_legado, im.nome_tabela_destino,
       cm.numero_ciclo, cm.data_execucao AS ciclo_data_execucao
FROM migracao_rejeicoes_cargas c
JOIN itens_migracao im ON im.id = c.item_migracao_id
JOIN ciclos_migracao cm ON cm.id = c.ciclo_migracao_id
"""


def listar_cargas(projeto_id, item_migracao_id=None, fase=None):
    where = [f"c.projeto_id = {db.q(projeto_id)}"]
    if item_migracao_id:
        where.append(f"c.item_migracao_id = {db.q(item_migracao_id)}")
    if fase:
        where.append(f"c.fase = {db.q(fase)}")
    sql = CARGA_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY c.criado_em DESC"
    return db.fetch_all(sql)


def resumo(carga_id):
    """KPIs + detalhamento por tipo_erro_chave (contagem, %, descrição e
    ação sugerida já vinculadas) -- é a "análise dos tipos de erros" e as
    "sugestões de ação" pedidas pelo usuário pra essa tela."""
    carga = db.fetch_one(CARGA_SELECT + f" WHERE c.id = {db.q(carga_id)}")
    if not carga:
        return None
    breakdown = db.fetch_all(f"""
        SELECT r.tipo_erro_chave, count(*) AS qtd,
               round(100.0 * count(*) / {max(carga["total_linhas"], 1)}, 2) AS percentual,
               a.id AS acao_id, a.descricao_erro, a.acao_sugerida
        FROM migracao_rejeicoes r
        LEFT JOIN migracao_rejeicoes_acoes_sugeridas a
               ON a.fase = {db.q(carga["fase"])} AND a.tipo_erro_chave = r.tipo_erro_chave
        WHERE r.carga_id = {db.q(carga_id)}
        GROUP BY r.tipo_erro_chave, a.id, a.descricao_erro, a.acao_sugerida
        ORDER BY qtd DESC
    """)
    return {"carga": carga, "breakdown": breakdown}


def detalhe(carga_id, tipo_erro_chave=None, limit=200, offset=0):
    where = [f"carga_id = {db.q(carga_id)}"]
    if tipo_erro_chave:
        where.append(f"tipo_erro_chave = {db.q(tipo_erro_chave)}")
    sql = (
        "SELECT id, linha_planilha, tipo_erro_chave, msg_erro, identificador, dados "
        f"FROM migracao_rejeicoes WHERE {' AND '.join(where)} "
        f"ORDER BY linha_planilha LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    return db.fetch_all(sql)


def listar_acoes_sugeridas(fase=None):
    sql = "SELECT * FROM migracao_rejeicoes_acoes_sugeridas"
    if fase:
        sql += f" WHERE fase = {db.q(fase)}"
    sql += " ORDER BY fase, tipo_erro_chave"
    return db.fetch_all(sql)


def criar_acao_sugerida(fase, tipo_erro_chave, descricao_erro=None, acao_sugerida=None):
    """Usada pela tela quando o breakdown mostra um tipo_erro_chave sem
    nenhuma entrada ainda no catálogo (ex: um código ERG-NNNNN nunca visto
    antes, fora dos 12 já cadastrados na migração 038) -- ON CONFLICT
    porque dois uploads concorrentes poderiam tentar criar a mesma
    combinação (fase, tipo_erro_chave) ao mesmo tempo."""
    return db.execute_returning_one(
        "INSERT INTO migracao_rejeicoes_acoes_sugeridas (fase, tipo_erro_chave, descricao_erro, acao_sugerida) "
        f"VALUES ({db.q(fase)}, {db.q(tipo_erro_chave)}, {db.q(descricao_erro)}, {db.q(acao_sugerida)}) "
        "ON CONFLICT (fase, tipo_erro_chave) DO UPDATE SET "
        "descricao_erro = COALESCE(EXCLUDED.descricao_erro, migracao_rejeicoes_acoes_sugeridas.descricao_erro), "
        "acao_sugerida = COALESCE(EXCLUDED.acao_sugerida, migracao_rejeicoes_acoes_sugeridas.acao_sugerida), "
        "atualizado_em = now() RETURNING *"
    )


def atualizar_acao_sugerida(id, acao_sugerida=None, descricao_erro=None):
    sets = ["atualizado_em = now()"]
    if acao_sugerida is not None:
        sets.append(f"acao_sugerida = {db.q(acao_sugerida)}")
    if descricao_erro is not None:
        sets.append(f"descricao_erro = {db.q(descricao_erro)}")
    return db.execute_returning_one(
        f"UPDATE migracao_rejeicoes_acoes_sugeridas SET {', '.join(sets)} "
        f"WHERE id = {db.q(id)} RETURNING *"
    )
