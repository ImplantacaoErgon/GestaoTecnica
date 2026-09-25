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

Desde o Adendo 4 (55ª rodada): aceita tanto .xlsx/.xlsm quanto .csv — visto
em produção que o arquivo de verdade que o sistema legado exporta pra essa
integração é um .csv, não uma planilha Excel. O formato é detectado pelo
CONTEÚDO do arquivo (assinatura binária de ZIP no início = .xlsx/.xlsm;
senão, tratado como CSV — ver _parece_xlsx), não pela extensão do nome, que
não é confiável (o nome real visto foi "LISTA_COMPARA FOLHAS_7475.csv").
Todo o resto do parser (cabeçalho, detecção de MESANO, geração das linhas
COPY) funciona igual pros dois formatos, através do adaptador _CsvComoAba,
que expõe a mesma interface `iter_rows()` que uma aba do openpyxl.
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
# Formato ISO (AAAA-MM-DD) — não visto ainda na planilha .xlsx (que usa
# célula de data nativa, sem passar por texto), mas comum em exports de
# CSV direto de banco de dados; aceito como alternativa ao formato acima,
# sem substituir nada (Adendo 4, 55ª rodada — suporte a .csv).
_DATA_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


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
    s = str(v).strip()
    m = _DATA_TEXTO_RE.match(s)
    if m:
        dia, mes, ano = m.groups()
        ano_i = int(ano) + 2000 if len(ano) == 2 else int(ano)
        try:
            return date(ano_i, int(mes), int(dia)).isoformat()
        except ValueError:
            return None
    m = _DATA_ISO_RE.match(s)
    if m:
        ano, mes, dia = m.groups()
        try:
            return date(int(ano), int(mes), int(dia)).isoformat()
        except ValueError:
            return None
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


def _parece_xlsx(conteudo_bytes):
    """Arquivos .xlsx/.xlsm são, por baixo dos panos, um ZIP — sempre
    começam com a assinatura binária "PK\\x03\\x04". Detecta o formato pelo
    CONTEÚDO (não pelo nome/extensão do arquivo, que pode mentir) — é assim
    que decidimos entre abrir via openpyxl ou tratar como CSV (Adendo 4, 55ª
    rodada: o arquivo real da pasta de Comparação Folha no Drive é um .csv
    exportado direto do sistema legado, não uma planilha Excel)."""
    return conteudo_bytes[:4] == b"PK\x03\x04"


def _detectar_encoding(conteudo_bytes, tamanho_amostra=65536):
    """Tenta alguns encodings comuns em exports de sistema legado brasileiro
    — utf-8 (com ou sem BOM) é o mais provável vindo de um export recente,
    mas latin-1 (cp1252-like) ainda aparece em exports mais antigos/Windows.
    latin-1 nunca levanta UnicodeDecodeError (mapeia todo byte pra um
    caractere), então serve de último recurso garantido.

    Diferente da versão anterior (61ª rodada — ver _CsvComoAba): decide o
    encoding olhando só uma AMOSTRA do início do arquivo (64KB), não o
    arquivo inteiro — decodificar ~130MB só pra "adivinhar" o encoding era
    desperdício de memória exatamente no momento mais crítico (bug de
    produção que derrubou o servidor por falta de memória, ver
    _CsvComoAba). A amostra é cortada no último "\\n" completo antes do
    limite (nunca no meio do arquivo) — "\\n" (0x0A) nunca aparece como byte
    de continuação em utf-8, então cortar ali é seguro e não arrisca partir
    um caractere multi-byte ao meio, o que faria a detecção falhar por um
    motivo errado (byte cortado, não encoding errado)."""
    amostra = conteudo_bytes[:tamanho_amostra]
    ultimo_nl = amostra.rfind(b"\n")
    if ultimo_nl > 0:
        amostra = amostra[:ultimo_nl]
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            amostra.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


class _CsvComoAba:
    """Adaptador mínimo que dá ao resto do parser (_mapear_cabecalho,
    _detectar_mesano_alvo, _linhas_copy) a MESMA interface que eles já usam
    numa aba do openpyxl — só o método `iter_rows(min_row=, max_row=,
    values_only=True)` — pra o resto da lógica de leitura funcionar igual
    tanto pra .xlsx/.xlsm quanto pra .csv, sem duplicar código.

    `conteudo_bytes` já está inteiro na memória de qualquer jeito (baixado
    do Drive, ou recebido por upload — ver google_drive.py/main.py), então
    guardar SÓ os bytes aqui (em vez de também decodificar tudo pra uma
    `str` Python logo de cara) evita manter duas a três cópias do conteúdo
    inteiro na memória ao mesmo tempo — o `str` decodificado E a cópia
    interna que um `io.StringIO(str)` faz por baixo dos panos. Cada
    `iter_rows()` decodifica em streaming, direto dos bytes já guardados,
    via `io.TextIOWrapper` (o "arquivo de texto" fica só na cabeça do
    Python — cada linha é decodificada sob demanda conforme o csv.reader
    avança, não tudo de uma vez).

    Motivo desta mudança (61ª rodada): um arquivo real de Comparação Folha
    (~130MB, ~300 mil linhas) chegou a estourar o limite de memória do
    servidor (512MB no Render) e derrubar o CONTÊINER inteiro — 502 sem
    nenhum traceback (processo morto pelo sistema operacional antes de
    conseguir logar qualquer coisa; confirmado nos logs/métricas do Render:
    reinícios completos do gunicorn, sem erro nenhum registrado entre eles,
    bem no horário da tentativa de importação). Isso não elimina de vez o
    limite de memória (os bytes brutos do arquivo continuam precisando
    caber na memória de qualquer forma — reduzir isso também exigiria mudar
    como o upload/download chega até aqui, um trabalho maior, não feito
    nesta rodada), mas corta a duplicação desnecessária que a versão
    anterior deste adaptador fazia especificamente na etapa de leitura."""

    def __init__(self, conteudo_bytes):
        self._bytes = conteudo_bytes
        self._encoding = _detectar_encoding(conteudo_bytes)
        amostra_texto = conteudo_bytes[:65536].decode(self._encoding, errors="replace")
        primeira_linha = amostra_texto.split("\n", 1)[0] if amostra_texto else ""
        # Exports de sistemas legados brasileiros frequentemente usam ";"
        # como separador (evita conflito com a vírgula decimal) — detecta
        # pelo que aparece mais na primeira linha.
        self._delimitador = ";" if primeira_linha.count(";") > primeira_linha.count(",") else ","

    def iter_rows(self, min_row=1, max_row=None, values_only=True):
        import csv
        import io
        # errors="replace" (em vez de deixar propagar UnicodeDecodeError):
        # o encoding já foi o melhor detectado pela amostra, mas um arquivo
        # de ~300 mil linhas pode ter algum byte isolado fora do padrão bem
        # mais à frente — preferimos substituir esse caractere pontual
        # (mesmo comportamento "nunca falha" que o latin-1 já garantia na
        # versão anterior) a derrubar a importação inteira por causa de 1
        # caractere estranho numa única célula.
        texto_stream = io.TextIOWrapper(io.BytesIO(self._bytes), encoding=self._encoding, errors="replace", newline="")
        try:
            leitor = csv.reader(texto_stream, delimiter=self._delimitador)
            for i, linha in enumerate(leitor, start=1):
                if i < min_row:
                    continue
                if max_row is not None and i > max_row:
                    return
                yield tuple(v if v != "" else None for v in linha)
        finally:
            texto_stream.close()


def _abrir_primeira_aba_com_dados(conteudo_bytes):
    """Retorna a primeira aba/fonte que tem cabeçalho + ao menos 1 linha de
    dados — de um arquivo .xlsx/.xlsm (via openpyxl) ou .csv (via
    _CsvComoAba), detectando qual é pelo conteúdo (ver _parece_xlsx).

    No caso .xlsx, não usa `ws.max_row` pra decidir isso — esse atributo
    depende da tag XML <dimension>, que nem toda planilha .xlsx preenche
    (confirmado: arquivos salvos em modo write_only do openpyxl, usados
    aqui só pra gerar massa de teste em escala, saem com max_row = None
    mesmo tendo dezenas de milhares de linhas reais). Em vez disso, tenta
    ler de fato as duas primeiras linhas (cabeçalho + 1ª linha de dados)
    via iter_rows — funciona igual tanto pra planilhas com <dimension>
    preenchida (caso normal, ex: Excel/LibreOffice) quanto sem."""
    if not _parece_xlsx(conteudo_bytes):
        aba = _CsvComoAba(conteudo_bytes)
        linhas = aba.iter_rows(min_row=1, max_row=2, values_only=True)
        tem_cabecalho = next(linhas, None) is not None
        tem_dado = next(linhas, None) is not None
        if tem_cabecalho and tem_dado:
            return aba
        raise ComparacaoFolhaImportError("O arquivo CSV não tem cabeçalho + ao menos 1 linha de dados.")
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


def validar_estrutura(conteudo_bytes):
    """Validação RÁPIDA e barata, pensada originalmente (54ª/55ª rodada)
    pra ESCOLHER qual arquivo de uma pasta fixa do Drive era o certo (ver
    google_drive.baixar_arquivo_mais_recente, parâmetro `validar`) — sem
    chamador ativo desde a 60ª rodada, quando Comparação Folha passou a usar
    o Google Picker (o próprio usuário escolhe o arquivo, sem precisar
    adivinhar entre candidatos de uma pasta). Mantida por se mostrar útil de
    novo se outra integração precisar do mesmo padrão de busca automática.
    Não itera as ~300 mil linhas de dados,
    só confere que a primeira aba com dados tem cara de Comparação Folha
    (cabeçalho na linha 1 com pelo menos 3 das colunas essenciais). Reusa
    _abrir_primeira_aba_com_dados (já é streaming/read_only) e
    _mapear_cabecalho (só lê a linha 1), então baixar e validar um
    candidato errado custa pouco mesmo sendo um arquivo grande — bem mais
    barato que rodar importar_comparacao_folha() inteiro só pra descartar
    um arquivo errado. Levanta ComparacaoFolhaImportError se não bater; não
    tem nenhum efeito no banco."""
    ws = _abrir_primeira_aba_com_dados(conteudo_bytes)
    _mapear_cabecalho(ws)


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
