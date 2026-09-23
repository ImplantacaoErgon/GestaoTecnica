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

# índice de coluna (0-based, como as colunas do Excel enumeradas a partir de A=0)
# -> nome do campo em `rubricas`. Levantado direto do cabeçalho real da planilha
# do cliente (linha 2) — se o cliente reorganizar colunas, isso quebra e o
# aviso de cabeçalho não-batido em parse_rubricas_document() pega o problema
# antes de importar qualquer coisa errada.
COLUNAS = {
    0: "analista", 1: "verba_legado", 2: "descricao_legado", 3: "codigo_ergon",
    4: "nome_abreviado", 6: "nome_extenso", 8: "tipo", 9: "grupo_calculo",
    10: "ordem_grupo_calculo", 11: "legislacao", 12: "valor_formula",
    13: "quant_autorizado", 14: "fato_origem",
    15: "validacao_compatibilidade", 16: "validacao_incompatibilidade",
    17: "regime_vinculo_direito", 18: "categoria_cargo_direito", 19: "secretaria",
    20: "outras_condicoes", 21: "incompatibilidade", 22: "periodicidade",
    23: "totalizacao", 24: "regra_negocio", 25: "data_levantamento",
    26: "questionamentos_juridico", 27: "revisado_por", 28: "observacoes_finais",
    29: "situacao_planilha", 30: "data_envio_techne", 31: "data_liberacao_testes",
    32: "observacoes_liberacao", 33: "consultor_techne",
    34: "condicoes_minimas_testes", 35: "questionamentos_consultor",
    36: "responsavel_homologacao", 37: "data_inicio_homologacao",
    38: "data_homologacao", 39: "observacoes_homologacao",
}

# Flags "SIM"/"-" de quais empresas essa rubrica se aplica, e o código específico
# que ela tem em cada uma (colunas separadas na planilha — ver comentário na
# migração). Note que a ORDEM das siglas não é a mesma entre as duas faixas de
# coluna (E16 aparece por último nas flags e primeiro nos códigos) — é assim na
# planilha original, não é erro de digitação aqui.
EMPRESA_FLAG_COL = {
    40: "E01", 41: "E03", 42: "E04", 43: "E06", 44: "E07",
    45: "E08", 46: "E13", 47: "E18", 48: "E23", 49: "E24", 50: "E16",
}
EMPRESA_CODIGO_COL = {
    51: "E16", 52: "E01", 53: "E03", 54: "E04", 55: "E06",
    56: "E07", 57: "E08", 58: "E13", 59: "E18", 60: "E23", 61: "E24",
}

INCIDENCIA_COL = {
    62: "ir", 63: "rgps", 64: "rpps", 65: "fgts", 66: "decimo_terceiro",
    67: "compoe_remuneracao", 68: "incide_excedente", 69: "incorpora_aposentadoria",
    70: "base_consignacoes", 71: "base_quinquenio", 72: "base_quinquenio_proporcional",
    73: "base_hora_extra", 74: "base_complemento_sm", 75: "base_faltas",
    76: "um_terco_ferias", 77: "adiantamento_13", 78: "ressarcimento_cessao",
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


def _derivar_status(item):
    """A planilha não tem uma coluna única de status — tem uma data por etapa
    do funil (levantamento -> envio à Techne -> liberação p/ testes -> início
    da homologação -> homologação). Deriva um status inicial a partir da data
    mais avançada preenchida; é só o valor de partida na importação — a tela
    permite o usuário corrigir manualmente depois (ex: uma rubrica pode
    "voltar" para revisão mesmo já tendo uma data de homologação de uma
    tentativa anterior que não valeu)."""
    if item.get("data_homologacao"):
        return "Homologada"
    if item.get("data_inicio_homologacao"):
        return "Em homologação"
    if item.get("data_liberacao_testes"):
        return "Liberada para testes"
    if item.get("data_envio_techne"):
        return "Enviada à Techne"
    return "Em levantamento"


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

    cabecalho_verba = ws.cell(row=LINHA_CABECALHO, column=2).value
    if not (cabecalho_verba and "verba" in str(cabecalho_verba).lower()):
        raise RubricasImportError(
            f'O cabeçalho esperado na linha {LINHA_CABECALHO} da aba "{ABA_ESPERADA}" não bateu '
            "(a coluna B deveria ser \"Verba 07\") — a planilha pode ter mudado de layout desde a "
            "última vez. Confira antes de importar."
        )

    itens = []
    avisos = [aviso_aba] if aviso_aba else []
    for r in range(LINHA_PRIMEIRA_DADOS, ws.max_row + 1):
        verba = ws.cell(row=r, column=2).value
        descricao = ws.cell(row=r, column=3).value
        if verba is None and not _texto(descricao):
            continue  # linha em branco (a planilha tem formatação até o final da folha, sem dado nenhum)

        item = {"linha_planilha": r}
        notas = []
        for idx, campo in COLUNAS.items():
            v = ws.cell(row=r, column=idx + 1).value
            if campo in DATE_FIELDS:
                iso, nota = _data_com_nota(v)
                item[campo] = iso
                if nota:
                    notas.append(f"{DATE_FIELD_LABEL[campo]} (texto original da planilha): {nota}")
                    if iso is None:
                        avisos.append(
                            f"Linha {r}: não consegui extrair uma data válida de "
                            f'"{DATE_FIELD_LABEL[campo]}" — texto ficou em notas_importacao, revise manualmente.'
                        )
            elif campo in ("verba_legado", "codigo_ergon"):
                item[campo] = _numero_como_texto(v)
            elif campo == "ordem_grupo_calculo":
                item[campo] = int(v) if isinstance(v, (int, float)) else None
            else:
                item[campo] = _texto(v)

        empresas = {}
        for idx, sigla in EMPRESA_FLAG_COL.items():
            flag = _texto(ws.cell(row=r, column=idx + 1).value)
            empresas.setdefault(sigla, {})["aplica"] = bool(flag) and flag.upper() == "SIM"
        for idx, sigla in EMPRESA_CODIGO_COL.items():
            codigo = _texto(ws.cell(row=r, column=idx + 1).value)
            if codigo:
                empresas.setdefault(sigla, {})["codigo"] = codigo
        item["empresas"] = {k: v for k, v in empresas.items() if v.get("aplica") or v.get("codigo")}

        incidencias = {}
        for idx, chave in INCIDENCIA_COL.items():
            v = _texto(ws.cell(row=r, column=idx + 1).value)
            if v:
                incidencias[chave] = v
        item["incidencias"] = incidencias

        item["status"] = _derivar_status(item)
        item["notas_importacao"] = "; ".join(notas) or None

        if not item.get("nome_abreviado") and not item.get("descricao_legado"):
            avisos.append(f"Linha {r}: sem nome nem descrição — confira se não é uma linha em branco/mesclada.")

        itens.append(item)

    if not itens:
        raise RubricasImportError(
            f'Nenhuma linha de rubrica encontrada na aba "{ABA_ESPERADA}" — confira se os dados '
            f"realmente começam na linha {LINHA_PRIMEIRA_DADOS}."
        )

    resumo = {"total": len(itens), "por_status": {}}
    for it in itens:
        resumo["por_status"][it["status"]] = resumo["por_status"].get(it["status"], 0) + 1

    return {"itens": itens, "avisos": avisos, "resumo": resumo}
