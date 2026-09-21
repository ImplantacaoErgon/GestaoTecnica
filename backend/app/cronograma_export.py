"""
Exportação do Cronograma (atividades) em planilha Excel — botão "Exportar
Cronograma (Excel)" na tela de Cronograma, ao lado de Importar/Versões.

Diferente da importação (que lê uma planilha e grava atividades) e da
exportação de "Minhas atividades" (apontamento de horas, escopo todos os
projetos), esta é uma exportação simples e 100% determinística: pega as
atividades do projeto selecionado — já filtradas no backend por Etapa,
Frente de trabalho e/ou período previsto, todos opcionais — e devolve numa
única aba, achatadas (sem agrupamento), na mesma ordem/lógica de colunas já
usada na tabela do Cronograma no front-end (ver renderAtividadesTable em
frontend/index.html), com algumas colunas a mais que só cabem numa planilha
(datas reais, observações, lista de responsáveis).

`gerar_planilha_bytes(atividades)` recebe a lista de dicts já filtrada (cada
item no formato devolvido por ATIVIDADE_SELECT, ver main.py) e devolve os
bytes do .xlsx — nenhuma consulta ao banco acontece aqui, só formatação.
"""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

COR_CABECALHO_HEX = "1A3D5C"

CABECALHO = [
    "Código", "Atividade", "Etapa", "Frente", "Status", "Prioridade",
    "Início prev.", "Fim prev.", "Esforço prev. (h)",
    "Início real", "Fim real", "Horas realizadas (h)", "% concluído",
    "Atrasada", "Master", "Entregável", "Responsáveis", "Observações",
]
LARGURAS = [12, 36, 22, 20, 16, 11, 13, 13, 14, 13, 13, 15, 12, 10, 8, 10, 28, 34]


def _estilo_cabecalho(ws, linha, n_colunas):
    for col in range(1, n_colunas + 1):
        cel = ws.cell(row=linha, column=col)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill(start_color=COR_CABECALHO_HEX, end_color=COR_CABECALHO_HEX, fill_type="solid")
        cel.alignment = Alignment(vertical="center")


def _autofit(ws, larguras):
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura


def _fmt_data_br(d):
    if not d:
        return None
    # db.fetch_all devolve date/datetime já como string ISO ("YYYY-MM-DD") ou
    # objeto date, dependendo do driver — cobre os dois casos sem depender de
    # nenhum import extra (mesmo padrão defensivo usado no resto do projeto).
    s = d if isinstance(d, str) else d.isoformat()
    partes = s.split("-")
    return f"{partes[2]}/{partes[1]}/{partes[0]}" if len(partes) == 3 else s


def _responsaveis_txt(responsaveis):
    if not responsaveis:
        return ""
    return ", ".join(f"{r['nome']}" + (f" ({r['tipo_vinculo']})" if r.get("tipo_vinculo") else "") for r in responsaveis)


def gerar_planilha_bytes(atividades):
    wb = Workbook()
    ws = wb.active
    ws.title = "Cronograma"
    ws.append(CABECALHO)
    _estilo_cabecalho(ws, 1, len(CABECALHO))

    for a in atividades:
        ws.append([
            a.get("codigo_wbs") or "",
            a.get("nome") or "",
            (f"{a['etapa_numero']} — {a['etapa_nome']}" if a.get("etapa_nome") else ""),
            a.get("frente_nome") or "",
            a.get("status") or "",
            a.get("prioridade") or "",
            _fmt_data_br(a.get("dtini_prev")),
            _fmt_data_br(a.get("dtfim_prev")),
            float(a["prazo_horas"]) if a.get("prazo_horas") is not None else None,
            _fmt_data_br(a.get("dtini_real")),
            _fmt_data_br(a.get("dtfim_real")),
            float(a["horas_realizadas"]) if a.get("horas_realizadas") is not None else None,
            a.get("percentual_concluido"),
            "Sim" if a.get("atrasada") else "Não",
            "Sim" if a.get("eh_atividade_master") else "",
            "Sim" if a.get("eh_entregavel") else "",
            _responsaveis_txt(a.get("responsaveis")),
            a.get("observacoes") or "",
        ])

    _autofit(ws, LARGURAS)
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
