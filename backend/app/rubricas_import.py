"""
Importador da planilha de Levantamento de Rubricas (regras de negócio da
folha de pagamento) do cliente — 53ª rodada.

Só a aba "Levantamento Rubricas" é suportada por enquanto. A planilha real
do cliente tem outras abas (backlog de rubricas do sistema legado ainda não
levantadas, tabelas de valor por cargo, rubricas excluídas, parâmetros de
domínio, cargos) — fora do escopo desta primeira versão, que foca no que já
foi levantado e está em algum ponto do funil de homologação.

Layout esperado (igual ao arquivo original do cliente): linha 2 é o
cabeçalho, os dados começam na linha 3. Colunas fora do range mapeado abaixo
(há uma nota solta na planilha além da coluna 79, sem cabeçalho definido) são
ignoradas.

Cada LINHA da planilha vira uma linha em `rubricas`, mesmo repetindo código
ERGON/verba do legado — a mesma regra de negócio pode ser levantada e
homologada separadamente por empresa (o cliente tem várias empresas/
autarquias na mesma implantação: E01, E03, E04, E06, E07, E08, E13, E16, E18,
E23, E24). `linha_planilha` (o número da linha no Excel) é a chave usada para
reimportar sem duplicar — ver db/migration_029_rubricas.sql.
"""
import io
import re
import unicodedata
from datetime import date, datetime

import openpyxl

# Algumas células de data na planilha real do cliente trazem mais de uma data
# (histórico de revisão digitado à mão, ex: "22/01/2026 - Revisão\n(28/11/2025)")
# ou erro de digitação sem dar pra converter (ex: "30/03/0206", "27/112025" —
# falta uma barra). _data_com_nota() abaixo NUNCA descarta esse texto — ele
# tenta extrair a data mais plausível pro campo de data de verdade, e guarda
# o texto original em "notas_importacao" pra revisão humana, sempre que a
# célula não era já um valor de data limpo do Excel.
_DATA_TEXTO_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
_ANO_MIN, _ANO_MAX = 2015, 2035  # janela plausível pra datas de um projeto de implantação

ABA_ESPERADA = "Levantamento Rubricas"
LINHA_CABECALHO = 2
LINHA_PRIMEIRA_DADOS = 3

# 167ª rodada — a aba "Excluídas" da planilha real do cliente tem exatamente
# as mesmas colunas da aba principal (mesmo cabeçalho: Verba 07 na coluna B,
# Código ERGON na D, Nome Abreviado na E etc.), só que SEM a linha de nota
# solta no topo — o cabeçalho fica na linha 1 e os dados começam na 2 (uma
# linha mais cedo que na aba principal). Usada só pra CONTAR (ver
# contar_aba_excluidas) — nenhuma rubrica é criada a partir dela.
ABA_EXCLUIDAS = "Excluídas"
LINHA_PRIMEIRA_DADOS_EXCLUIDAS = 2

# 170ª rodada — a planilha do cliente ganhou a coluna "Motivo de Envio à
# Techne" NO MEIO do cabeçalho (logo depois de "Data de envio à TECHNE"),
# deslocando em 1 posição todas as colunas seguintes (liberação, homologação,
# empresas E01..E24, códigos COD Exx, incidências). O mapeamento anterior era
# por POSIÇÃO FIXA de coluna (0=Análise, 1=Verba 07 ...) — com a planilha
# nova ele não dava erro nenhum: gravava, em silêncio, a coluna errada em
# cada campo (ex: a data de liberação lida do lugar do motivo, empresas e
# incidências trocadas, status de rubricas homologadas recalculado errado).
# Agora cada campo é localizado pelo TEXTO DO CABEÇALHO (linha 2): inserir,
# tirar ou reordenar colunas não afeta mais a importação, e uma coluna nova
# desconhecida é simplesmente ignorada. Cabeçalho comparado sem acento, sem
# diferença de maiúscula/minúscula e com espaços/quebras de linha colapsados.
#
# (campo em `rubricas`, cabeçalho normalizado, casa_por_prefixo) — prefixo
# vale pros cabeçalhos longos da planilha, que trazem uma explicação
# entre parênteses depois do nome ("Nome Abreviado (30 caracteres)").
CAMPOS_POR_CABECALHO = (
    ("analista", "analise", False),
    ("verba_legado", "verba 07", False),
    ("descricao_legado", "descricao e07", False),
    ("codigo_ergon", "rubrica ergon", False),
    ("nome_abreviado", "nome abreviado", True),
    ("nome_extenso", "nome por extenso", True),
    ("tipo", "tipo de rubrica", False),
    ("grupo_calculo", "grupo de calculo", False),
    ("ordem_grupo_calculo", "ordem grupo de calculo", False),
    ("legislacao", "legislacao", False),
    ("valor_formula", "r$ / %", False),
    ("quant_autorizado", "quant. autorizado", False),
    ("fato_origem", "fato origem", False),
    ("validacao_compatibilidade", "validacao compatibilidade", False),
    ("validacao_incompatibilidade", "validacao incompatibilidade", False),
    ("regime_vinculo_direito", "regime juridico", True),
    ("categoria_cargo_direito", "categoria/subcategoria", True),
    ("secretaria", "secretaria", False),
    ("outras_condicoes", "outras condicoes", True),
    ("incompatibilidade", "incompatibilidade", True),
    ("periodicidade", "periodicidade", True),
    ("totalizacao", "totalizacao", True),
    ("regra_negocio", "regra de negocio", False),
    ("data_levantamento", "data levantamento", False),
    ("questionamentos_juridico", "questionamentos ao juridico", False),
    ("revisado_por", "revisado por", True),
    ("observacoes_finais", "observacoes finais", False),
    ("situacao_planilha", "ep", False),
    ("data_envio_techne", "data de envio a techne", False),
    ("motivo_envio_techne", "motivo de envio a techne", False),
    ("data_liberacao_testes", "data de liberacao para testes", False),
    ("observacoes_liberacao", "observacoes da liberacao", False),
    ("consultor_techne", "techne consultor responsavel", False),
    ("condicoes_minimas_testes", "techne condicoes minimas para testes", False),
    ("questionamentos_consultor", "techne questionamentos e duvidas do consultor", False),
    ("responsavel_homologacao", "responsavel", False),
    ("data_inicio_homologacao", "data inicio dos testes de homologacao", False),
    ("data_homologacao", "data da homologacao", False),
    ("observacoes_homologacao", "observacoes", False),
)
# 174ª rodada — nomes alternativos aceitos pros campos essenciais (além do texto
# de CAMPOS_POR_CABECALHO, que continua sendo o preferido): o cliente às vezes
# reescreve o cabeçalho ("CÓDIGO ERGON" em vez de "RUBRICA ERGON", "Verba E07"...).
# Comparados após _normaliza_cabecalho; `prefixo` = basta começar com o texto.
ALIASES_CABECALHO = {
    "verba_legado": [("verba", True)],
    "codigo_ergon": [("codigo ergon", True), ("cod ergon", True), ("cod. ergon", True),
                     ("rubrica ergon", True), ("rubrica", False)],
    "nome_abreviado": [("nome abrev", True), ("nome curto", True)],
}
# Sem estes a planilha não é a esperada — a importação é barrada (os demais
# só geram um aviso e o campo fica vazio).
CAMPOS_ESSENCIAIS = ("verba_legado", "codigo_ergon", "nome_abreviado")

# Flags "SIM"/"-" de quais empresas essa rubrica se aplica (cabeçalho "E01") e
# o código/verba específico dela em cada uma (cabeçalho "COD E01") — ver
# comentário na migração 029. As siglas são descobertas pelo cabeçalho (uma
# empresa nova na planilha entra sozinha); a ORDEM entre as faixas não
# importa mais.
_RE_CAB_EMPRESA_FLAG = re.compile(r"^e(\d{2})$")
_RE_CAB_EMPRESA_CODIGO = re.compile(r"^cod e(\d{2})$")

# cabeçalho normalizado -> chave em `incidencias`
INCIDENCIA_POR_CABECALHO = {
    "incide ir": "ir", "incide rgps": "rgps", "incide rpps": "rpps", "incide fgts": "fgts",
    "incide 13o": "decimo_terceiro", "compoe remuneracao": "compoe_remuneracao",
    "incide excedente": "incide_excedente", "incorpora aposentadoria": "incorpora_aposentadoria",
    "base consignacoes": "base_consignacoes", "base quinquenio": "base_quinquenio",
    "base quinquenio proporcional": "base_quinquenio_proporcional",
    "base hora extra": "base_hora_extra", "base complemento sm": "base_complemento_sm",
    "base faltas": "base_faltas", "1/3 ferias": "um_terco_ferias",
    "adiantamento 13o salario": "adiantamento_13", "ressarcimento cessao": "ressarcimento_cessao",
}


def _normaliza_cabecalho(valor):
    """"Motivo de Envio à Techne" -> "motivo de envio a techne". NFKD também
    reduz "13º" a "13o"."""
    if valor is None:
        return ""
    s = unicodedata.normalize("NFKD", str(valor))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip().lower()


def mapear_colunas(ws, linha_cabecalho=None):
    """Lê o cabeçalho e devolve {"campos": {campo: coluna_1based},
    "empresa_flag": {sigla: col}, "empresa_codigo": {sigla: col},
    "incidencias": {chave: col}, "nao_encontrados": [campo, ...]}."""
    linha = linha_cabecalho or LINHA_CABECALHO
    cabecalhos = [
        (col, _normaliza_cabecalho(ws.cell(row=linha, column=col).value))
        for col in range(1, (ws.max_column or 0) + 1)
    ]
    cabecalhos = [(c, h) for c, h in cabecalhos if h]
    usadas = set()
    campos = {}
    for campo, esperado, por_prefixo in CAMPOS_POR_CABECALHO:
        achada = next((c for c, h in cabecalhos if c not in usadas and h == esperado), None)
        if achada is None and por_prefixo:
            achada = next((c for c, h in cabecalhos if c not in usadas and h.startswith(esperado)), None)
        if achada is None:
            for alias, prefixo_alias in ALIASES_CABECALHO.get(campo, []):
                achada = next(
                    (c for c, h in cabecalhos if c not in usadas
                     and (h.startswith(alias) if prefixo_alias else h == alias)
                     and (campo != "verba_legado" or len(h) <= 12)), None)  # não confunde com "Verbas salariais"
                if achada is not None:
                    break
        if achada is not None:
            campos[campo] = achada
            usadas.add(achada)
    flags, codigos, incidencias = {}, {}, {}
    for col, h in cabecalhos:
        m = _RE_CAB_EMPRESA_FLAG.match(h)
        if m:
            flags[f"E{m.group(1)}"] = col
            continue
        m = _RE_CAB_EMPRESA_CODIGO.match(h)
        if m:
            codigos[f"E{m.group(1)}"] = col
            continue
        if h in INCIDENCIA_POR_CABECALHO:
            incidencias[INCIDENCIA_POR_CABECALHO[h]] = col
    return {
        "campos": campos, "empresa_flag": flags, "empresa_codigo": codigos,
        "incidencias": incidencias,
        "nao_encontrados": [c for c, _e, _p in CAMPOS_POR_CABECALHO if c not in campos],
    }


DATE_FIELDS = {
    "data_levantamento", "data_envio_techne", "data_liberacao_testes",
    "data_inicio_homologacao", "data_homologacao",
}
DATE_FIELD_LABEL = {
    "data_levantamento": "Data Levantamento",
    "data_envio_techne": "Data de envio à Techne",
    "data_liberacao_testes": "Data de liberação para testes",
    "data_inicio_homologacao": "Data início dos testes de homologação",
    "data_homologacao": "Data da Homologação",
}

STATUS_VALIDOS = [
    "Em levantamento", "Enviada à Techne", "Liberada para testes",
    "Em homologação", "Homologada", "Excluída",
]


class RubricasImportError(Exception):
    pass


def _texto(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _numero_como_texto(v):
    """Preserva '1' em vez de '1.0' — o Excel guarda código/verba como número,
    mas aqui são identificadores/texto, não quantidade a somar."""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s or None


def _data_com_nota(v):
    """Retorna (data_iso_ou_None, nota_ou_None). Quando a célula já é um valor
    de data nativo do Excel, retorna direto (nota=None). Quando é texto,
    tenta achar a data mais recente e válida dentro do texto (a planilha às
    vezes tem mais de uma, ex: revisões sucessivas) e SEMPRE devolve o texto
    original como nota — mesmo quando conseguiu extrair uma data, porque o
    texto pode ter mais contexto (motivo da revisão, uma 2ª data etc) que não
    cabe no campo de data."""
    if v is None:
        return None, None
    if isinstance(v, datetime):
        return v.date().isoformat(), None
    if isinstance(v, date):
        return v.isoformat(), None
    s = str(v).strip()
    if not s:
        return None, None
    candidatos = []
    for dia, mes, ano in _DATA_TEXTO_RE.findall(s):
        try:
            ano_i = int(ano)
            if ano_i < 100:
                ano_i += 2000
            d = date(ano_i, int(mes), int(dia))
        except ValueError:
            continue
        if _ANO_MIN <= d.year <= _ANO_MAX:
            candidatos.append(d)
    iso = max(candidatos).isoformat() if candidatos else None
    return iso, s


# 151ª rodada — pedido do usuário (verbatim): "A situação da Rubrica deve
# seguir pela maior data entre Levantamento, Enviada para Techne e
# Homologada [...] o status da rubrica deve levar em consideração a maior
# data dentre essas 4." Exemplo dado: Código Ergon 348 tem data de
# Homologação, mas a data de Enviada à Techne é mais recente — nesse caso o
# status correto é "Enviada à Techne", não "Homologada" (a rubrica "voltou"
# pra Techne depois de já ter sido homologada numa tentativa anterior).
# Ordem do funil (da mais cedo pra mais avançada) — usada só como
# desempate quando duas datas empatam exatamente, e como fallback quando
# nenhuma data está preenchida (cai em "Em levantamento", primeiro item).
_STATUS_POR_CAMPO_DATA = [
    ("data_levantamento", "Em levantamento"),
    ("data_envio_techne", "Enviada à Techne"),
    ("data_liberacao_testes", "Liberada para testes"),
    ("data_inicio_homologacao", "Em homologação"),
    ("data_homologacao", "Homologada"),
]


def _linha_tachada(ws, r, colunas=(2, 3, 4, 5)):
    """167ª rodada — pedido do usuário (verbatim): "Se uma linha estiver
    'tachada' pode considerar excluída". O cliente usa formatação de
    tachado (strikethrough) na aba "Levantamento Rubricas" pra indicar,
    visualmente, que uma rubrica não vale mais — sem apagar a linha nem
    preencher nenhuma data/observação que o sistema já leia. Checa as
    colunas que de fato identificam a rubrica (Verba, Descrição, Código
    ERGON, Nome Abreviado — as mesmas quatro usadas pra decidir se a linha
    está em branco, acima) e considera a linha tachada se QUALQUER uma
    delas, com valor preenchido, estiver com tachado. `data_only=True` no
    load do workbook não impede o acesso a `cell.font` — só afeta se uma
    fórmula devolve o valor calculado ou o texto da fórmula."""
    for col in colunas:
        cel = ws.cell(row=r, column=col)
        if cel.value is not None and cel.font and cel.font.strike:
            return True
    return False


def _linha_tem_dado(ws, r):
    """Mesma regra de "linha em branco de verdade" usada no loop principal
    de parse_rubricas_document (pelo menos um entre Verba, Descrição, Código
    ERGON ou Nome Abreviado preenchido) — reaproveitada por
    contar_aba_excluidas (167ª rodada), que não tem o resto do contexto do
    loop principal (datas, empresas, incidências) porque só conta, não vira
    `rubricas`."""
    verba = ws.cell(row=r, column=2).value
    descricao = ws.cell(row=r, column=3).value
    codigo_ergon = ws.cell(row=r, column=4).value
    nome_abreviado = ws.cell(row=r, column=5).value
    return not (verba is None and not _texto(descricao)
                and codigo_ergon is None and not _texto(nome_abreviado))


def contar_aba_excluidas(wb):
    """167ª rodada — pedido do usuário (verbatim): "A ideia seria ter o
    número de Rubricas por linha que tenha pelo menos os campos Código Ergon
    ou Codigo ou Legado e nome apenas da aba principal. [...] Caso consiga
    usar essa aba EXCLUÍDAS ai sim pode ajustar esse número." E, depois de
    eu ter testado e confirmado (sem regressão): "a ideia é apenas contar os
    registros da aba de excluídas e atualizar o totalizador que tem na
    página somados às linhas tachadas da aba principal. O Total de rubricas
    deve ser sempre considerado apenas da aba principal."

    Ou seja: NÃO gera `rubricas` — é só uma contagem, somada ao total de
    "Excluídas" mostrado na tela (linhas com status Excluída na aba
    principal, por tachado ou por terem sumido — ver _linha_tachada e
    marcar_rubricas_ausentes_como_excluidas — MAIS esta contagem). O "Total
    de rubricas" continua vindo só da aba principal, sem nunca somar isto.

    Retorna (quantidade, aviso). `aviso` só vem preenchido quando a aba não
    existe nesta planilha — nesse caso `quantidade` é None, pra quem chamar
    (app/main.py) saber que deve MANTER o último valor conhecido em vez de
    zerar (a planilha pode ter sido resalva/reexportada sem essa aba, o que
    não quer dizer que a contagem real tenha zerado)."""
    if ABA_EXCLUIDAS not in wb.sheetnames:
        return None, (
            f'Esta planilha não tem uma aba "{ABA_EXCLUIDAS}" — a contagem de Excluídas vindas '
            "dessa aba não foi atualizada (ficou o último valor conhecido)."
        )
    ws = wb[ABA_EXCLUIDAS]
    qtd = sum(
        1 for r in range(LINHA_PRIMEIRA_DADOS_EXCLUIDAS, ws.max_row + 1)
        if _linha_tem_dado(ws, r)
    )
    return qtd, None


def _derivar_status(item):
    """A planilha não tem uma coluna única de status — tem uma data por etapa
    do funil (levantamento -> envio à Techne -> liberação p/ testes -> início
    da homologação -> homologação). Deriva um status inicial a partir da
    etapa cuja data é a MAIS RECENTE entre as preenchidas — não
    necessariamente a etapa mais avançada do funil, porque na prática uma
    rubrica pode "voltar" (ex: reenviada à Techne depois de já ter uma data
    de homologação de uma tentativa anterior que não valeu). Em caso de
    empate exato entre duas datas, desempata pela etapa mais avançada
    (mesmo critério usado antes desta rodada). É só o valor de partida na
    importação — a tela permite o usuário corrigir manualmente depois, mas
    uma reimportação (manual ou automática) recalcula de novo a partir das
    datas, sobrescrevendo uma correção manual que não tenha sido refletida
    também nas datas."""
    melhor = None  # (data, índice no funil, status)
    for idx, (campo, status) in enumerate(_STATUS_POR_CAMPO_DATA):
        valor = item.get(campo)
        if not valor:
            continue
        try:
            d = date.fromisoformat(valor)
        except (TypeError, ValueError):
            continue
        if melhor is None or (d, idx) > (melhor[0], melhor[1]):
            melhor = (d, idx, status)
    return melhor[2] if melhor else "Em levantamento"


def _resumo_cabecalhos(ws, linha=None, limite=14):
    """"B='Verba 07', C='DESCRIÇÃO E07', ..." — o que foi realmente lido na linha de
    cabeçalho, pra diagnosticar sem precisar do arquivo (primeiras `limite` células)."""
    linha = linha or LINHA_CABECALHO
    partes = []
    for col in range(1, (ws.max_column or 0) + 1):
        v = ws.cell(row=linha, column=col).value
        if v in (None, ""):
            continue
        txt = " ".join(str(v).split())[:28]
        partes.append(f"{openpyxl.utils.get_column_letter(col)}='{txt}'")
        if len(partes) >= limite:
            partes.append("...")
            break
    return ", ".join(partes) if partes else f"(linha {linha} vazia; dimensão da aba: {ws.max_row}x{ws.max_column})"


def _linha_tem_cabecalho_verba(ws, linha):
    for col in range(1, (ws.max_column or 0) + 1):
        h = _normaliza_cabecalho(ws.cell(row=linha, column=col).value)
        if h.startswith("verba") and len(h) <= 12:  # "verba 07"; não confunde com o dado "Verbas salariais"
            return True
    return False


def _detectar_linha_cabecalho(ws):
    """173/175ª rodada — procura (linhas 1 a 8) a linha que é de fato o cabeçalho:
    a primeira (começando pela esperada, LINHA_CABECALHO) em que dá pra mapear
    verba, código ERGON e nome abreviado e que tem o cabeçalho "Verba 07".
    Visto em produção (13/11/2026): alguém apagou a linha de nota solta que ficava
    acima do cabeçalho, e o cabeçalho passou da linha 2 pra 1 — as colunas continuavam
    idênticas, mas o sistema lia a 1ª rubrica como cabeçalho e barrava tudo."""
    for linha in [LINHA_CABECALHO] + [l for l in range(1, 9) if l != LINHA_CABECALHO]:
        if not _linha_tem_cabecalho_verba(ws, linha):
            continue
        campos = mapear_colunas(ws, linha)["campos"]
        if all(c in campos for c in CAMPOS_ESSENCIAIS):
            return linha
    return None


def _mensagem_cabecalho_nao_bateu(ws):
    """Mensagem de diagnóstico quando nenhuma das linhas 1-8 serve de cabeçalho."""
    return (
        f'Não consegui localizar o cabeçalho (Verba 07, Rubrica/Código ERGON e Nome Abreviado) nas 8 primeiras '
        f'linhas da aba "{ws.title}" — a planilha pode ter mudado de layout ou ser outro arquivo. Confira antes de importar.'
    )


def parse_rubricas_document(conteudo_bytes, nome_arquivo=""):
    """Lê a aba "Levantamento Rubricas" e devolve
    {"itens": [...], "avisos": [...], "resumo": {...}} — não grava nada no
    banco (isso é feito por upsert_rubricas em app/main.py, depois que o
    usuário revisa a prévia)."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo_bytes), data_only=True)
    except Exception as e:
        raise RubricasImportError(f"Não foi possível ler o arquivo como planilha Excel: {e}")

    aviso_aba = None
    if ABA_ESPERADA in wb.sheetnames:
        ws = wb[ABA_ESPERADA]
    elif len(wb.sheetnames) == 1:
        # Visto em produção (55ª rodada, botão "Atualizar Rubricas" via
        # Drive): a planilha real às vezes é resalva/reexportada e perde o
        # nome original da aba, virando o nome genérico que o Excel/Google
        # Sheets dá por padrão (ex: "Planilha1"/"Sheet1"). Em vez de barrar
        # de cara, aceita quando é a ÚNICA aba do arquivo — a validação do
        # cabeçalho logo abaixo (coluna B = "Verba 07") ainda pega o caso de
        # ser o arquivo errado. Fica um aviso explícito na prévia de
        # qualquer forma, pro usuário confirmar antes de importar.
        ws = wb[wb.sheetnames[0]]
        aviso_aba = (
            f'A aba se chama "{wb.sheetnames[0]}", não "{ABA_ESPERADA}" como esperado — usando '
            "ela mesmo assim, por ser a única aba do arquivo. Confirme que é o arquivo certo "
            "antes de importar."
        )
    else:
        raise RubricasImportError(
            f'A planilha não tem uma aba chamada "{ABA_ESPERADA}" (abas encontradas: '
            f'{", ".join(wb.sheetnames)}). Confirme se é o arquivo certo.'
        )

    # 173ª rodada — antes só aceitava "Verba" na coluna B. Agora aceita em QUALQUER
    # coluna da linha de cabeçalho (as colunas são mapeadas pelo nome desde a 170ª
    # rodada, então uma coluna inserida antes de "Verba 07" é inofensiva). A LINHA
    # do cabeçalho continua fixa de propósito: `linha_planilha` (número da linha) é a
    # chave do upsert — se o cabeçalho descer uma linha, TODA rubrica mudaria de
    # chave (duplicaria as existentes e marcaria as antigas como Excluídas), então
    # nesse caso a importação é barrada, com a mensagem dizendo onde o cabeçalho
    # foi encontrado.
    linha_cab = _detectar_linha_cabecalho(ws)
    if linha_cab is None:
        raise RubricasImportError(
            _mensagem_cabecalho_nao_bateu(ws)
            + f" Cabeçalhos lidos na linha {LINHA_CABECALHO}: {_resumo_cabecalhos(ws)}."
        )
    # `linha_planilha` (chave do upsert, ver upsert_rubricas) é sempre expressa no
    # sistema de numeração ORIGINAL (cabeçalho na linha 2): se o cabeçalho foi
    # achado em outra linha, todas as linhas ganham o deslocamento, pra uma rubrica
    # já cadastrada continuar com a MESMA chave (senão duplicaria tudo e marcaria as
    # antigas como Excluídas). Linha real da planilha = linha_planilha - deslocamento.
    deslocamento = LINHA_CABECALHO - linha_cab
    mapa = mapear_colunas(ws, linha_cab)
    campos_col = mapa["campos"]
    itens = []
    avisos = [aviso_aba] if aviso_aba else []
    if deslocamento:
        avisos.append(
            f"O cabeçalho da planilha está na linha {linha_cab} (o esperado era a {LINHA_CABECALHO}). Os números de linha "
            f"foram ajustados em {deslocamento:+d} para continuarem batendo com as rubricas já cadastradas "
            f"(\"linha N\" nas mensagens = linha real {'+' if deslocamento > 0 else '-'} {abs(deslocamento)})."
        )
    ausentes = [c for c in mapa["nao_encontrados"] if c not in CAMPOS_ESSENCIAIS]
    if ausentes:
        rotulos = {campo: cab for campo, cab, _p in CAMPOS_POR_CABECALHO}
        avisos.append(
            "Colunas não encontradas no cabeçalho da planilha (os campos ficam vazios nesta importação): "
            + ", ".join(f'"{rotulos[c].capitalize()}"' for c in ausentes) + "."
        )
    col_verba = campos_col["verba_legado"]
    col_descricao = campos_col.get("descricao_legado")
    col_codigo = campos_col["codigo_ergon"]
    col_nome = campos_col["nome_abreviado"]
    for r in range(linha_cab + 1, ws.max_row + 1):
        lp = r + deslocamento  # chave `linha_planilha` (ver acima)
        verba = ws.cell(row=r, column=col_verba).value
        descricao = ws.cell(row=r, column=col_descricao).value if col_descricao else None
        # 150ª rodada — pedido do usuário (verbatim): "Quero ver a linha 302
        # Código Ergon = 515 e não achei em nosso sistema." Causa raiz: essa
        # linha (e mais 130 outras na planilha real do cliente, levantado em
        # 08/10/2026) tem Código ERGON e Nome Abreviado preenchidos, mas SEM
        # Verba 07/Descrição E07 (não tem equivalente no sistema legado — ex:
        # rubrica nova, só existe no Ergon) — a checagem de "linha em branco"
        # olhava só pra essas duas colunas e descartava a linha inteira,
        # junto com as linhas realmente vazias (a planilha tem formatação até
        # o fim da folha, sem dado nenhum). Agora só é tratada como vazia se
        # TAMBÉM não tiver Código ERGON nem Nome Abreviado — as duas colunas
        # que de fato identificam a rubrica.
        codigo_ergon_bruto = ws.cell(row=r, column=col_codigo).value
        nome_abreviado_bruto = ws.cell(row=r, column=col_nome).value
        if (verba is None and not _texto(descricao)
                and codigo_ergon_bruto is None and not _texto(nome_abreviado_bruto)):
            continue  # linha em branco de verdade

        item = {"linha_planilha": lp}
        notas = []
        for campo, coluna in campos_col.items():
            v = ws.cell(row=r, column=coluna).value
            if campo in DATE_FIELDS:
                iso, nota = _data_com_nota(v)
                item[campo] = iso
                if nota:
                    notas.append(f"{DATE_FIELD_LABEL[campo]} (texto original da planilha): {nota}")
                    if iso is None:
                        avisos.append(
                            f"Linha {lp}: não consegui extrair uma data válida de "
                            f'"{DATE_FIELD_LABEL[campo]}" — texto ficou em notas_importacao, revise manualmente.'
                        )
            elif campo in ("verba_legado", "codigo_ergon"):
                item[campo] = _numero_como_texto(v)
            elif campo == "ordem_grupo_calculo":
                item[campo] = int(v) if isinstance(v, (int, float)) else None
            else:
                item[campo] = _texto(v)

        empresas = {}
        for sigla, coluna in mapa["empresa_flag"].items():
            flag = _texto(ws.cell(row=r, column=coluna).value)
            empresas.setdefault(sigla, {})["aplica"] = bool(flag) and flag.upper() == "SIM"
        for sigla, coluna in mapa["empresa_codigo"].items():
            codigo = _texto(ws.cell(row=r, column=coluna).value)
            if codigo:
                empresas.setdefault(sigla, {})["codigo"] = codigo
        item["empresas"] = {k: v for k, v in empresas.items() if v.get("aplica") or v.get("codigo")}

        incidencias = {}
        for chave, coluna in mapa["incidencias"].items():
            v = _texto(ws.cell(row=r, column=coluna).value)
            if v:
                incidencias[chave] = v
        item["incidencias"] = incidencias

        if _linha_tachada(ws, r, (col_verba, col_descricao or col_verba, col_codigo, col_nome)):
            item["status"] = "Excluída"
            identificacao = item.get("codigo_ergon") or item.get("nome_abreviado") or f"linha {lp}"
            avisos.append(
                f'Linha {lp} ("{identificacao}") está tachada (riscada) na planilha — marcada como '
                "Excluída."
            )
        else:
            item["status"] = _derivar_status(item)
        item["notas_importacao"] = "; ".join(notas) or None

        if not item.get("nome_abreviado") and not item.get("descricao_legado"):
            avisos.append(f"Linha {lp}: sem nome nem descrição — confira se não é uma linha em branco/mesclada.")

        itens.append(item)

    if not itens:
        raise RubricasImportError(
            f'Nenhuma linha de rubrica encontrada na aba "{ABA_ESPERADA}" — confira se os dados '
            f"realmente começam na linha {linha_cab + 1}."
        )

    resumo = {"total": len(itens), "por_status": {}}
    for it in itens:
        resumo["por_status"][it["status"]] = resumo["por_status"].get(it["status"], 0) + 1

    # 167ª rodada — ver contar_aba_excluidas: só conta, não gera `rubricas`.
    aba_excluidas_qtd, aviso_aba_excluidas = contar_aba_excluidas(wb)
    if aviso_aba_excluidas:
        avisos.append(aviso_aba_excluidas)
    resumo["aba_excluidas_qtd"] = aba_excluidas_qtd

    return {
        "itens": itens, "avisos": avisos, "resumo": resumo,
        "aba_excluidas_qtd": aba_excluidas_qtd,
    }
