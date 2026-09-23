"""
Importação da planilha de Comparação Folha (55ª rodada) — resultado, já
calculado pelo cliente, da comparação entre o valor de cada rubrica no
Ergon e na ficha financeira do sistema legado, um por servidor/vínculo/
rubrica. Uma aba única, ~300 mil linhas por arquivo — um arquivo por
competência (MESANO) fechada.

Diferente de rubricas_import.py (planilha pequena, ~440 linhas, editada à
mão pela equipe): aqui o arquivo é grande demais (confirmado com o usuário:
~130MB, ~300 mil linhas) pra qualquer estratégia que materialize tudo na
memória de uma vez — nem como lista de dicts Python, nem como uma única
string SQL gigante. Por isso:

  - A planilha é lida em modo "read_only" do openpyxl (streaming — não
    carrega a árvore XML inteira na memória) e as linhas são processadas
    uma de cada vez, nunca todas juntas numa lista.
  - A carga no banco usa COPY (não INSERT) — é o jeito nativo do Postgres
    de carregar muitos dados de uma vez, bem mais rápido e compacto que
    milhares de "INSERT ... VALUES (...), (...), ...". Os dados vão sendo
    ESCRITOS incrementalmente no stdin do psql (ver db.execute_stream) —
    nunca viram uma string Python de 100+ MB de uma vez só.
  - Não existe um endpoint de "prévia com checkbox por linha" como em
    Rubricas — com 300 mil linhas isso nem renderizaria no navegador.
    Confirmado com o usuário: a tela é só consulta (sem edição manual por
    linha) e reimportar o mesmo mês SUBSTITUI as linhas antigas daquele mês
    (delete-then-copy, dentro de uma única transação) — ver
    substituir_comparacao_mes().
"""
import re
from datetime import date, datetime

from . import db

# A aba é sempre única (confirmado com o usuário) — ao contrário de
# rubricas_import.py, não travamos pelo NOME da aba (pode variar entre
# arquivos), só pegamos a primeira aba com dados.

# Cabeçalho: linha 1 (sem linha de título acima, diferente da planilha de
# Rubricas). Mapeia o nome da coluna EXATAMENTE como vem na planilha pro
# nome da coluna no banco (ver db/migration_030_comparacao_folha.sql).
COLUNAS = {
    "TIPO_COMPARACAO": "tipo_comparacao",
    "MATRICULA": "matricula",
    "PRONTUARIO": "prontuario",
    "EMPRESA_CONSIST": "empresa_consist",
    "CATEGORIA_CONSIST": "categoria_consist",
    "EMP_CODIGO": "emp_codigo",
    "NOME": "nome",
    "CPF": "cpf",
    "NUMFUNC": "numfunc",
    "NUMVINC": "numvinc",
    "NUMPENS": "numpens",
    "DTEXERC": "dtexerc",
    "DTAPOSENT": "dtaposent",
    "TIPOAPOS": "tipoapos",
    "DTVAC": "dtvac",
    "MODALIDADE": "modalidade",
    "TIPOPENS": "tipopens",
    "PERCENTUAL": "percentual",
    "TIPOVINC": "tipovinc",
    "REGIMEJUR": "regimejur",
    "CATEGORIA": "categoria",
    "NUMVINCANT": "numvincant",
    "NUMVINCPOS": "numvincpos",
    "ORGAO": "orgao",
    "SETORFUNC": "setorfunc",
    "CARGO": "cargo",
    "NOME_CARGO": "nome_cargo",
    "REFERENCIA": "referencia",
    "TABVENCCARGO": "tabvenccargo",
    "FUNCAO": "funcao",
    "NOME_FUNCAO": "nome_funcao",
    "REFERENCIA_DESIG": "referencia_desig",
    "TABVENCFUNCAO": "tabvencfuncao",
    "GRUPO": "grupo",
    "MESANO": "mesano",
    "FOLHA": "folha",
    "CONVENIO": "convenio",
    "VERBA_CONSIST": "verba_consist",
    "NOMEABREV_CONSIST": "nomeabrev_consist",
    "VALOR_CONSIST": "valor_consist",
    "CONTA_CONSIST": "conta_consist",
    "RUBRICA_ERGON": "rubrica_ergon",
    "NOME_ABREV": "nome_abrev",
    "RUBRICA_NOME_ERGON": "rubrica_nome_ergon",
    "VALOR_ERGON": "valor_ergon",
    "CONTA_ERGON": "conta_ergon",
    "DIF_CONSIST_ERGON": "dif_consist_ergon",
    "VANTAGEM": "vantagem",
    "OBS_EVENTO": "obs_evento",
    "OBS_ATRIBUTO": "obs_atributo",
    "TIPORUBR": "tiporubr",
    "CLASS_RUBRICA": "class_rubrica",
    "SITUACAO": "situacao",
    "DATA_CONVERGENCIA": "data_convergencia",
    "FATO_ORIGEM": "fato_origem",
    "COMPLEMENTO": "complemento",
    "COD_CALC": "cod_calc",
    "COD_LOTACAO_1": "cod_lotacao_1",
    "TIPOARQ": "tipoarq",
    "JORNADA": "jornada",
    "AFASTAMENTO": "afastamento",
    "DTINI_AFAST": "dtini_afast",
    "DTFIM_AFAST": "dtfim_afast",
    "OBS": "obs",
    "DATA_HORA_CONVERGENCIA": "data_hora_convergencia",
}
CAMPOS_NUMERICOS = {"percentual", "valor_consist", "valor_ergon", "dif_consist_ergon"}
CAMPOS_DATA = {"dtexerc", "dtaposent", "dtvac", "mesano", "data_convergencia", "dtini_afast", "dtfim_afast"}
CAMPOS_TIMESTAMP = {"data_hora_convergencia"}

# Colunas mínimas que precisam bater com o cabeçalho pra considerar que
# "isto parece mesmo um arquivo de Comparação Folha" — não trava se faltar
# uma coluna qualquer (planilha real pode variar), mas se NENHUMA dessas
# bater, é sinal de que o arquivo errado foi apontado na pasta do Drive.
COLUNAS_ESSENCIAIS = {"MATRICULA", "MESANO", "SITUACAO", "RUBRICA_ERGON", "VALOR_ERGON", "VALOR_CONSIST"}

_DATA_TEXTO_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")


class ComparacaoFolhaImportError(Exception):
    """Erro pensado pra aparecer direto pro usuário na tela (planilha no
    formato errado, aba vazia, etc.)."""
    pass


def _texto(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _numero(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)
    except ValueError:
        return None


def _data(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    m = _DATA_TEXTO_RE.match(str(v).strip())
    if not m:
        return None
    dia, mes, ano = m.groups()
    ano_i = int(ano) + 2000 if len(ano) == 2 else int(ano)
    try:
        return date(ano_i, int(mes), int(dia)).isoformat()
    except ValueError:
        return None


def _timestamp(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return _texto(v)


def _copy_escape(v):
    """Formato TEXT do COPY do Postgres: NULL vira \\N, e barra invertida/
    tab/quebra de linha precisam ser escapados — ver documentação do COPY
    (seção "File Formats" > "Text Format")."""
    if v is None:
        return "\\N"
    s = str(v)
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def _abrir_primeira_aba_com_dados(conteudo_bytes):
    """Retorna a primeira aba que tem cabeçalho + ao menos 1 linha de dados.

    Não usa `ws.max_row` pra decidir isso — esse atributo depende da tag XML
    <dimension>, que nem toda planilha .xlsx preenche (confirmado: arquivos
    salvos em modo write_only do openpyxl, usados aqui só pra gerar massa de
    teste em escala, saem com max_row = None mesmo tendo dezenas de milhares
    de linhas reais). Em vez disso, tenta ler de fato as duas primeiras
    linhas (cabeçalho + 1ª linha de dados) via iter_rows — funciona igual
    tanto pra planilhas com <dimension> preenchida (caso normal, ex: Excel/
    LibreOffice) quanto sem."""
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo_bytes), data_only=True, read_only=True)
    for nome in wb.sheetnames:
        ws = wb[nome]
        linhas = ws.iter_rows(min_row=1, max_row=2, values_only=True)
        tem_cabecalho = next(linhas, None) is not None
        tem_dado = next(linhas, None) is not None
        if tem_cabecalho and tem_dado:
            return ws
    raise ComparacaoFolhaImportError("A planilha não tem nenhuma aba com dados (linha de cabeçalho + ao menos 1 linha).")


def _mapear_cabecalho(ws):
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not header_row:
        raise ComparacaoFolhaImportError("Não foi possível ler a linha de cabeçalho (linha 1) da planilha.")
    cabecalhos = [(_texto(v) or "").upper() for v in header_row]
    encontrados = set(cabecalhos) & set(COLUNAS_ESSENCIAIS)
    if len(encontrados) < 3:
        raise ComparacaoFolhaImportError(
            "Esta planilha não parece ser um arquivo de Comparação Folha — o cabeçalho "
            "esperado (MATRICULA, MESANO, SITUACAO, RUBRICA_ERGON, VALOR_ERGON, "
            "VALOR_CONSIST, ...) não foi encontrado na linha 1. Confira se é o arquivo "
            "certo na pasta do Drive."
        )
    # índice (posição na linha) -> nome da coluna no banco, só pras colunas
    # que reconhecemos — colunas extras/desconhecidas na planilha são ignoradas.
    indice_para_coluna = {}
    for i, cab in enumerate(cabecalhos):
        col = COLUNAS.get(cab)
        if col:
            indice_para_coluna[i] = col
    return indice_para_coluna


def _detectar_mesano_alvo(ws, indice_para_coluna, linhas_amostra=200):
    """Espia as primeiras linhas de dados até achar um MESANO válido —
    single-pass por design (ver comentário no topo do arquivo sobre por que
    não fazemos duas passadas pelo arquivo inteiro). Não achando em
    `linhas_amostra` linhas, levanta erro (sem MESANO não dá pra saber o
    que substituir com segurança)."""
    idx_mesano = next((i for i, col in indice_para_coluna.items() if col == "mesano"), None)
    if idx_mesano is None:
        raise ComparacaoFolhaImportError("A coluna MESANO não foi encontrada no cabeçalho da planilha.")
    for i, row in enumerate(ws.iter_rows(min_row=2, max_row=1 + linhas_amostra, values_only=True)):
        if idx_mesano < len(row):
            d = _data(row[idx_mesano])
            if d:
                return d
    raise ComparacaoFolhaImportError(
        f"Não foi possível identificar a competência (MESANO) nas primeiras {linhas_amostra} "
        "linhas da planilha — confira se a coluna está preenchida."
    )


def _linhas_copy(ws, indice_para_coluna, projeto_id, mesano_alvo, contadores):
    """Generator: uma linha de texto (formato COPY) por linha de dados da
    planilha, na ordem de COLUNA_ORDEM (ver importar_comparacao_folha).
    Atualiza `contadores` (dict mutável) IN-PLACE conforme processa — é lido
    de volta pelo chamador só depois que o generator terminar de ser
    consumido por db.execute_stream."""
    coluna_ordem = contadores["_coluna_ordem"]
    idx_mesano = next((i for i, col in indice_para_coluna.items() if col == "mesano"), None)
    idx_situacao = next((i for i, col in indice_para_coluna.items() if col == "situacao"), None)
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row is None or all(v is None for v in row):
            continue  # linha totalmente vazia (comum no fim de planilhas exportadas)
        contadores["total"] += 1

        situacao = _texto(row[idx_situacao]) if idx_situacao is not None and idx_situacao < len(row) else None
        contadores["por_situacao"][situacao or "(sem situação)"] = (
            contadores["por_situacao"].get(situacao or "(sem situação)", 0) + 1
        )

        mesano_linha = _data(row[idx_mesano]) if idx_mesano is not None and idx_mesano < len(row) else None
        if mesano_linha and mesano_linha[:7] != mesano_alvo[:7]:
            contadores["mesano_divergente"] += 1

        valores = {}
        for i, col in indice_para_coluna.items():
            v = row[i] if i < len(row) else None
            if col in CAMPOS_NUMERICOS:
                valores[col] = _numero(v)
            elif col in CAMPOS_DATA:
                valores[col] = _data(v)
            elif col in CAMPOS_TIMESTAMP:
                valores[col] = _timestamp(v)
            else:
                valores[col] = _texto(v)

        campos = [_copy_escape(str(projeto_id))] + [_copy_escape(valores.get(col)) for col in coluna_ordem]
        yield "\t".join(campos) + "\n"


def importar_comparacao_folha(projeto_id, conteudo_bytes, timeout=900):
    """Ponto de entrada único (sem prévia — ver comentário no topo do
    arquivo): lê a planilha, detecta a competência (MESANO), substitui as
    linhas daquela competência (delete + copy, uma transação só) e devolve
    um resumo. Nunca materializa as ~300 mil linhas como lista de dicts
    Python nem como uma string SQL única — tudo é passado em streaming pro
    banco (ver db.execute_stream)."""
    ws = _abrir_primeira_aba_com_dados(conteudo_bytes)
    indice_para_coluna = _mapear_cabecalho(ws)
    mesano_alvo = _detectar_mesano_alvo(ws, indice_para_coluna)

    coluna_ordem = [c for c in COLUNAS.values() if c in set(indice_para_coluna.values())]
    contadores = {"total": 0, "por_situacao": {}, "mesano_divergente": 0, "_coluna_ordem": coluna_ordem}

    colunas_copy = ", ".join(["projeto_id"] + coluna_ordem)
    prefixo = (
        "BEGIN;\n"
        f"DELETE FROM comparacao_folha WHERE projeto_id = {db.q(projeto_id)} "
        f"AND date_trunc('month', mesano) = date_trunc('month', {db.q(mesano_alvo)}::date);\n"
        f"COPY comparacao_folha ({colunas_copy}) FROM STDIN WITH (FORMAT text);\n"
    )
    sufixo = "\\.\nCOMMIT;\n"

    linhas = _linhas_copy(ws, indice_para_coluna, projeto_id, mesano_alvo, contadores)
    db.execute_stream(prefixo, linhas, sufixo, timeout=timeout)

    avisos = []
    if contadores["mesano_divergente"]:
        avisos.append(
            f"{contadores['mesano_divergente']} linha(s) tinham um MESANO diferente do "
            f"detectado ({mesano_alvo[:7]}) — foram importadas mesmo assim, mas confira se "
            "o arquivo não mistura mais de uma competência."
        )
    return {
        "mesano": mesano_alvo,
        "total_linhas": contadores["total"],
        "por_situacao": contadores["por_situacao"],
        "avisos": avisos,
    }
