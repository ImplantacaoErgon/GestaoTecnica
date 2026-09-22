"""
Gera o PDF do Relatório Executivo a partir do markdown devolvido pela IA
(ver app/relatorio_executivo.py). Só entende o subconjunto de markdown que o
prompt pede à IA para usar — "## título", parágrafos, listas com "-" (mais
"**negrito**" e "*itálico*" dentro do texto) e, a partir da 50ª rodada, uma
tabela markdown simples (estilo GFM: linha de cabeçalho, linha separadora
"---", linhas de dados, tudo separado por "|") — não é um parser de markdown
genérico.

Usa reportlab (não weasyprint/wkhtmltopdf) de propósito: é puro Python, não
precisa de bibliotecas de sistema extras na imagem Docker (Pango/Cairo/
Chromium), o que mantém o Dockerfile simples.
"""
import re
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, HRFlowable, Table, TableStyle,
)

_INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_INLINE_ITALIC = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
# Linha de tabela markdown: começa e termina com "|" (com espaços em volta permitidos).
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
# Linha separadora do cabeçalho (a 2ª linha de toda tabela GFM): só "-", ":", "|" e espaços.
_TABLE_SEP_RE = re.compile(r"^\|?[\s:|-]+\|?\s*$")
# 51ª rodada — a IA às vezes devolve uma linha de tabela sem o "|" de abertura/fechamento
# (normalmente a última linha de uma tabela grande, por algum corte na geração). Com
# _TABLE_ROW_RE estrita, essa linha caía fora da tabela e era desenhada como texto solto
# com os "|" visíveis (bug reportado pelo usuário). _parece_linha_tabela() é tolerante: só
# exige 2+ ocorrências de "|" na linha, o suficiente pra reconhecer uma linha de tabela com
# várias colunas mesmo faltando uma borda — e _normaliza_linha_tabela() repõe as bordas
# antes de mandar pro _split_table_row (que já tolera não ter "|" nas pontas).
_PIPE_MIN_OCORRENCIAS = 2


def _parece_linha_tabela(s):
    return s.count("|") >= _PIPE_MIN_OCORRENCIAS


def _normaliza_linha_tabela(s):
    if not s.startswith("|"):
        s = "|" + s
    if not s.endswith("|"):
        s = s + "|"
    return s
# Célula de evolução/involução no formato "+12,3%" / "-8,5%" — pinta de verde/vermelho.
_EVOLUCAO_RE = re.compile(r"^([+-])\s*[\d.,]+\s*%")

# Largura útil da página A4 com as margens usadas em gerar_pdf_bytes (2.2cm cada lado).
_LARGURA_UTIL = A4[0] - 2 * 2.2 * cm


def _inline_to_reportlab(text):
    """Converte **negrito** e *itálico* para as tags <b>/<i> do reportlab, e escapa
    o resto (&, <, >) para não quebrar o parser de markup interno do Paragraph."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _INLINE_BOLD.sub(r"<b>\1</b>", text)
    text = _INLINE_ITALIC.sub(r"<i>\1</i>", text)
    return text


def _estilos():
    base = getSampleStyleSheet()
    estilos = {
        "titulo": ParagraphStyle("titulo", parent=base["Title"], fontSize=18, spaceAfter=4),
        "subtitulo": ParagraphStyle("subtitulo", parent=base["Normal"], fontSize=10.5,
                                     textColor=colors.HexColor("#555555"), alignment=TA_CENTER, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=13.5, spaceBefore=16, spaceAfter=6,
                              textColor=colors.HexColor("#1a3d5c")),
        "corpo": ParagraphStyle("corpo", parent=base["Normal"], fontSize=10, leading=14.5, spaceAfter=6,
                                 alignment=4),  # justify
        "item": ParagraphStyle("item", parent=base["Normal"], fontSize=10, leading=14),
        "rodape": ParagraphStyle("rodape", parent=base["Normal"], fontSize=7.5,
                                  textColor=colors.HexColor("#888888")),
        "tabela_cab": ParagraphStyle("tabela_cab", parent=base["Normal"], fontSize=9, leading=11.5,
                                      textColor=colors.white, fontName="Helvetica-Bold"),
        "tabela_cel": ParagraphStyle("tabela_cel", parent=base["Normal"], fontSize=9, leading=11.5),
    }
    return estilos


def _split_table_row(linha):
    """'| a | b | c |' -> ['a', 'b', 'c'] (aceita também sem os '|' nas pontas)."""
    s = linha.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _celula_tabela(texto, estilo_base, estilos):
    """Parágrafo de uma célula de dados — pinta de verde/vermelho quando o texto é uma
    evolução/involução percentual no formato '+12,3%'/'-8,5%' (ver PROMPT_TEMPLATE)."""
    m = _EVOLUCAO_RE.match(texto.strip())
    cor = "#b91c1c" if m and m.group(1) == "-" else ("#15803d" if m else None)
    corpo = _inline_to_reportlab(texto)
    if cor:
        corpo = f'<font color="{cor}"><b>{corpo}</b></font>'
    return Paragraph(corpo, estilo_base)


def _tabela_flowable(linhas_tabela, estilos):
    """linhas_tabela: lista de linhas cruas de uma tabela markdown (cabeçalho + separador +
    dados, já sem a linha em branco que a encerra). Monta um reportlab Table com a 1ª
    coluna mais larga (costuma ser um rótulo/nome) e as demais em largura igual."""
    cabecalho = _split_table_row(linhas_tabela[0])
    linhas_dados = [_split_table_row(l) for l in linhas_tabela[2:] if l.strip()]
    n_col = len(cabecalho)

    if n_col > 1:
        peso_primeira = 2.0
        largura_primeira = _LARGURA_UTIL * peso_primeira / (peso_primeira + (n_col - 1))
        largura_demais = (_LARGURA_UTIL - largura_primeira) / (n_col - 1)
        col_widths = [largura_primeira] + [largura_demais] * (n_col - 1)
    else:
        col_widths = [_LARGURA_UTIL]

    dados = [[Paragraph(_inline_to_reportlab(c), estilos["tabela_cab"]) for c in cabecalho]]
    for linha in linhas_dados:
        # tolera linha de dados com menos/mais células que o cabeçalho (a IA pode errar a
        # contagem) — completa com célula vazia ou trunca, em vez de estourar o Table
        linha = (linha + [""] * n_col)[:n_col]
        dados.append([_celula_tabela(c, estilos["tabela_cel"], estilos) for c in linha])

    tabela = Table(dados, colWidths=col_widths, repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3d5c")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f8")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tabela


def _markdown_para_flowables(conteudo_md, estilos):
    flowables = []
    linhas = conteudo_md.replace("\r\n", "\n").split("\n")
    buffer_lista = []
    buffer_tabela = []

    def fecha_lista():
        if buffer_lista:
            flowables.append(ListFlowable(
                [ListItem(Paragraph(_inline_to_reportlab(li), estilos["item"]), leftIndent=8) for li in buffer_lista],
                bulletType="bullet", start="•", leftIndent=14, spaceBefore=2, spaceAfter=8,
            ))
            buffer_lista.clear()

    def fecha_tabela():
        if buffer_tabela:
            if len(buffer_tabela) >= 2:  # cabeçalho + separador, no mínimo
                flowables.append(_tabela_flowable(buffer_tabela, estilos))
                flowables.append(Spacer(1, 10))
            buffer_tabela.clear()

    for linha in linhas:
        linha_strip = linha.strip()
        if not linha_strip:
            fecha_lista()
            fecha_tabela()
            continue
        # 2ª linha de uma tabela = separadora ("|---|---|...") — só é reconhecida assim
        # se a linha anterior já foi capturada como possível cabeçalho de tabela.
        if buffer_tabela and len(buffer_tabela) == 1 and _TABLE_SEP_RE.match(linha_strip):
            buffer_tabela.append(linha_strip)
            continue
        if _TABLE_ROW_RE.match(linha_strip) or (buffer_tabela and _parece_linha_tabela(linha_strip)):
            if not buffer_tabela:
                fecha_lista()
            buffer_tabela.append(_normaliza_linha_tabela(linha_strip))
            continue
        # linha não é de tabela: se tinha só uma linha bufferizada como possível cabeçalho
        # (sem separador confirmado logo depois), não era uma tabela de verdade — essa
        # linha era só texto normal que por acaso começava/terminava com "|", então
        # desenha ela como parágrafo em vez de simplesmente perder o conteúdo.
        if buffer_tabela and len(buffer_tabela) < 2:
            pendente = buffer_tabela.pop()
            flowables.append(Paragraph(_inline_to_reportlab(pendente), estilos["corpo"]))
        fecha_tabela()
        if linha_strip.startswith("## "):
            fecha_lista()
            flowables.append(Paragraph(_inline_to_reportlab(linha_strip[3:].strip()), estilos["h2"]))
        elif linha_strip.startswith("# "):
            fecha_lista()
            flowables.append(Paragraph(_inline_to_reportlab(linha_strip[2:].strip()), estilos["h2"]))
        elif linha_strip.startswith(("- ", "* ")):
            buffer_lista.append(linha_strip[2:].strip())
        else:
            fecha_lista()
            flowables.append(Paragraph(_inline_to_reportlab(linha_strip), estilos["corpo"]))
    fecha_lista()
    if len(buffer_tabela) == 1:  # mesmo caso do meio do loop, mas no fim do documento
        flowables.append(Paragraph(_inline_to_reportlab(buffer_tabela.pop()), estilos["corpo"]))
    fecha_tabela()
    return flowables


def gerar_pdf_bytes(relatorio_row):
    """relatorio_row: dict com pelo menos conteudo_md, gerado_em, dados_enviados
    (dados_enviados.projeto.{nome,cliente,sigla}) — como vem de relatorios_executivos."""
    estilos = _estilos()
    dados = relatorio_row.get("dados_enviados") or {}
    projeto = dados.get("projeto") or {}
    nome_projeto = projeto.get("nome") or "Projeto"
    cliente = projeto.get("cliente") or ""
    gerado_em = relatorio_row.get("gerado_em")
    if isinstance(gerado_em, str):
        try:
            gerado_em_fmt = datetime.fromisoformat(gerado_em.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            gerado_em_fmt = gerado_em
    else:
        gerado_em_fmt = str(gerado_em) if gerado_em else ""

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=2.2 * cm, bottomMargin=2 * cm, leftMargin=2.2 * cm, rightMargin=2.2 * cm,
        title=f"Relatório Executivo — {nome_projeto}",
    )

    flow = [
        Paragraph("Relatório Executivo do Projeto", estilos["titulo"]),
        Paragraph(f"{nome_projeto}" + (f" — {cliente}" if cliente else ""), estilos["subtitulo"]),
        Paragraph(f"Gerado em {gerado_em_fmt} · conteúdo produzido por IA a partir dos dados do cronograma",
                  estilos["subtitulo"]),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc")),
        Spacer(1, 4),
    ]
    flow.extend(_markdown_para_flowables(relatorio_row.get("conteudo_md") or "", estilos))
    flow.append(Spacer(1, 14))
    flow.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#dddddd")))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(
        "Este relatório foi redigido por um modelo de inteligência artificial a partir de dados extraídos "
        "automaticamente do cronograma do projeto na data indicada acima. As previsões de prazo são estimativas "
        "calculadas por extrapolação do ritmo de execução observado — não são um compromisso contratual. "
        "Recomenda-se revisão pelo gerente de projeto antes de qualquer uso oficial ou apresentação ao cliente.",
        estilos["rodape"],
    ))

    doc.build(flow)
    return buf.getvalue()
