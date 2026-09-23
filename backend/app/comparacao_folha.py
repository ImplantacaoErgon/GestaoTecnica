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


def _where_filtros(projeto_id=None, mesano=None, situacao=None, tipo_comparacao=None,
                    tiporubr=None, busca=None, so_divergentes=False):
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


def resumo(projeto_id=None, mesano=None):
    """KPIs pro topo da tela: total de linhas, quantas são divergentes
    (situação diferente de "Não Divergente"), soma da diferença, e as listas
    de meses/situações/tipos de rubrica disponíveis pra popular os filtros —
    calculadas dentro do mesmo projeto (e mês, se filtrado), não fixas."""
    where = _where_filtros(projeto_id=projeto_id, mesano=mesano)
    row = db.fetch_one(f"""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE UPPER(situacao) <> 'NÃO DIVERGENTE') AS total_divergentes,
          COALESCE(SUM(dif_consist_ergon), 0) AS soma_diferenca
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
    return {
        "total": (row or {}).get("total", 0),
        "total_divergentes": (row or {}).get("total_divergentes", 0),
        "soma_diferenca": (row or {}).get("soma_diferenca", 0),
        "por_situacao": por_situacao,
        "meses_disponiveis": [m["mesano"] for m in meses],
        "tipos_rubrica_disponiveis": [t["tiporubr"] for t in tipos_rubrica],
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
