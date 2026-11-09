"""
Exportação do Cronograma para o monday.com (172ª rodada) — pedido do usuário
(verbatim): "Na aba de cronograma crie um botão para exportar o cronograma
completo para o formato que o monday aceita, preciso que sejam respeitadas as
sequencias, predecessoras e principalmente o % de completude das atividades".

O que o monday aceita (conferido na documentação do próprio monday): importação
de .xlsx/.xls/.csv — SÓ a primeira aba, no máximo 8.000 linhas e 100 colunas
(em board novo só as 50 primeiras colunas entram, e só viram colunas dos tipos
Número, Status, E-mail, Data ou Texto). O monday NÃO importa .mpp/XML do MS
Project e a importação de planilha NÃO traz:
  - dependências (a coluna "Dependência" não é importável);
  - grupos (todas as linhas caem num grupo novo único);
  - subitens (viram itens comuns, "converter depois" é manual).
Por isso este arquivo é feito em duas camadas:

1) Aba "monday" (a que o monday lê): uma linha por atividade/marco, na ordem
   DFS do WBS (pai antes dos filhos — a "sequência" do cronograma), com
   colunas de Texto/Número/Data/Status:
     Item (nome) = "{Código} - {Atividade}" · Código WBS · Tipo (Atividade/
     Resumo/Marco) · Nível · Pai (WBS) · Frente · Etapa · Status · Prioridade ·
     % concluído (NÚMERO 0-100, sem formatação de % — o formato não sobrevive
     à importação) · Início/Fim previsto · Início/Fim real · Esforço previsto
     (h) · Horas realizadas (h) · Responsáveis · Predecessoras (texto).
   "Frente" e "Pai (WBS)" existem pra uma segunda etapa (API do monday):
   mover cada item pro grupo da sua Frente e criar as dependências.
2) Aba "Dependências" (ignorada pelo monday, usada pela etapa de API): uma
   linha por predecessora — sucessora, predecessora (ambas pelo Código WBS, a
   chave de casamento), tipo (FS/SS/FF/SF) e lag em horas. A API do monday só
   cria o vínculo (não expõe tipo nem lag), então tipo/lag ficam registrados
   aqui e na coluna de texto "Predecessoras" do item.
Aba "Leia-me": passo a passo da importação.

Nenhuma consulta ao banco acontece aqui, só formatação — mesmo padrão de
app/cronograma_export.py. `atividades` já vem ordenada por
codigo_wbs_chave_ordenacao (ver rota em main.py).
"""
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

COR_CABECALHO_HEX = "1A3D5C"

CABECALHO = [
    "Item", "Código WBS", "Tipo", "Nível", "Pai (WBS)", "Frente", "Etapa",
    "Status", "Prioridade", "% concluído",
    "Início previsto", "Fim previsto", "Início real", "Fim real",
    "Esforço previsto (h)", "Horas realizadas (h)", "Responsáveis", "Predecessoras",
]
LARGURAS = [48, 12, 10, 7, 12, 22, 24, 18, 11, 12, 13, 13, 13, 13, 12, 12, 30, 34]

CABECALHO_DEPS = [
    "Sucessora (Código WBS)", "Sucessora (Item)", "Predecessora (Código WBS)",
    "Predecessora (Item)", "Tipo", "Lag (h)",
]
LARGURAS_DEPS = [22, 48, 24, 48, 8, 9]


def _estilo_cabecalho(ws, n_colunas):
    for col in range(1, n_colunas + 1):
        cel = ws.cell(row=1, column=col)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill(start_color=COR_CABECALHO_HEX, end_color=COR_CABECALHO_HEX, fill_type="solid")
        cel.alignment = Alignment(vertical="center")


def _larguras(ws, larguras):
    for i, w in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _data(d):
    """Data como `date` real com formato ISO (o monday exige ISO ao importar
    datas). Aceita str ISO, date ou datetime; vazio -> None."""
    if not d:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d)[:10])
    except ValueError:
        return None


def _num(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 2)


def _lag_txt(lag):
    return f"{float(lag):g}"


def _nome_item(codigo, nome):
    nome = (nome or "").strip()
    return f"{codigo} - {nome}" if codigo else nome


def _responsaveis_txt(responsaveis):
    if not responsaveis:
        return ""
    return ", ".join(r["nome"] for r in responsaveis if r.get("nome"))


def _nivel(codigo):
    return max(1, len(str(codigo).split("."))) if codigo else 1


def _predecessoras_txt(deps, codigo_por_id):
    itens = []
    for d in deps or []:
        cod = d.get("predecessora_codigo_wbs") or codigo_por_id.get(d.get("predecessora_id"))
        if not cod:
            continue
        tipo = d.get("tipo") or "FS"
        try:
            lag = float(d["lag_horas"]) if d.get("lag_horas") not in (None, "") else 0.0
        except (TypeError, ValueError):
            lag = 0.0
        itens.append(f"{cod} ({tipo}, lag {_lag_txt(lag)}h)" if lag else f"{cod} ({tipo})")
    return "; ".join(itens)


def gerar_planilha_bytes(atividades, marcos, deps_por_atividade, projeto=None):
    """atividades: dicts de ATIVIDADE_SELECT, JÁ ordenados por Código (DFS);
    marcos: linhas de `marcos`; deps_por_atividade: {atividade_id: [{
    predecessora_id, tipo, lag_horas}, ...]} (todas as predecessoras do
    projeto). Retorna (bytes_xlsx, resumo) — `resumo` leva contagens pra
    mensagem/teste (linhas, dependências, atividades sem código etc.)."""
    deps_por_atividade = deps_por_atividade or {}
    codigo_por_id = {a["id"]: a.get("codigo_wbs") for a in atividades}
    nome_por_id = {a["id"]: _nome_item(a.get("codigo_wbs"), a.get("nome")) for a in atividades}
    ids_pais = {a["atividade_pai_id"] for a in atividades if a.get("atividade_pai_id")}

    wb = Workbook()
    ws = wb.active
    ws.title = "monday"
    ws.append(CABECALHO)
    _estilo_cabecalho(ws, len(CABECALHO))

    sem_codigo = 0
    for a in atividades:
        codigo = a.get("codigo_wbs") or ""
        if not codigo:
            sem_codigo += 1
        pai_cod = codigo_por_id.get(a.get("atividade_pai_id")) or ""
        tipo = "Resumo" if a["id"] in ids_pais else "Atividade"
        etapa = f"{a['etapa_numero']} - {a['etapa_nome']}" if a.get("etapa_nome") else ""
        ws.append([
            _nome_item(codigo, a.get("nome")), codigo, tipo, _nivel(codigo), pai_cod,
            a.get("frente_nome") or "", etapa, a.get("status") or "", a.get("prioridade") or "",
            int(a.get("percentual_concluido") or 0),
            _data(a.get("dtini_prev")), _data(a.get("dtfim_prev")),
            _data(a.get("dtini_real")), _data(a.get("dtfim_real")),
            _num(a.get("prazo_horas")), _num(a.get("horas_realizadas")),
            _responsaveis_txt(a.get("responsaveis")),
            _predecessoras_txt(deps_por_atividade.get(a["id"]), codigo_por_id),
        ])

    for i, m in enumerate(marcos or [], start=1):
        concluido = bool(m.get("data_real"))
        ws.append([
            f"MARCO-{i} - {m.get('nome') or ''}".strip(), f"MARCO-{i}", "Marco", 1, "",
            "Marcos", "", "Concluída" if concluido else "Não iniciada", "",
            100 if concluido else 0,
            _data(m.get("data_prevista")), _data(m.get("data_prevista")),
            _data(m.get("data_real")), _data(m.get("data_real")),
            None, None, "", "",
        ])

    # formato de data ISO nas colunas de data (K:N) — ver docstring do módulo
    for row in ws.iter_rows(min_row=2, min_col=11, max_col=14):
        for cel in row:
            cel.number_format = "yyyy-mm-dd"
    _larguras(ws, LARGURAS)
    ws.freeze_panes = "B2"

    wd = wb.create_sheet("Dependências")
    wd.append(CABECALHO_DEPS)
    _estilo_cabecalho(wd, len(CABECALHO_DEPS))
    n_deps = 0
    for a in atividades:
        for d in deps_por_atividade.get(a["id"], []):
            pred_id = d.get("predecessora_id")
            if pred_id not in codigo_por_id:
                continue
            wd.append([
                a.get("codigo_wbs") or "", nome_por_id.get(a["id"], ""),
                codigo_por_id.get(pred_id) or "", nome_por_id.get(pred_id, ""),
                d.get("tipo") or "FS", _num(d.get("lag_horas")) or 0,
            ])
            n_deps += 1
    _larguras(wd, LARGURAS_DEPS)
    wd.freeze_panes = "A2"

    wl = wb.create_sheet("Leia-me")
    linhas = [
        ("Como importar este cronograma no monday.com", True),
        ("", False),
        ("1. No monday: Adicionar > Importar dados > Excel. Escolha este arquivo e selecione \"Novo board\".", False),
        ("2. O monday só lê a primeira aba (\"monday\"). As abas \"Dependências\" e \"Leia-me\" são ignoradas.", False),
        ("3. Mapeie as colunas: Item -> nome do item; \"% concluído\", \"Esforço previsto (h)\" e \"Horas realizadas (h)\" -> Números;", False),
        ("   \"Status\" -> Status; datas -> Data; as demais -> Texto. Importe \"% concluído\" como Números (0 a 100) e, no board, ajuste a unidade para %.", False),
        ("4. A importação NÃO cria dependências, grupos nem subitens. Depois de importar, envie o link do board:", False),
        ("   as dependências (aba \"Dependências\", casadas pelo \"Código WBS\") e a distribuição dos itens em grupos por \"Frente\" são feitas pela API do monday.", False),
        ("5. A API do monday cria o vínculo entre itens, mas não expõe tipo (FS/SS/FF/SF) nem lag — eles ficam registrados na aba \"Dependências\"", False),
        ("   e na coluna \"Predecessoras\" para conferência/ajuste manual onde não forem FS.", False),
        ("6. Limites do monday: 8.000 linhas; em board novo só as 50 primeiras colunas são importadas; Status com mais de 40 rótulos falha.", False),
        ("", False),
        ("Colunas: \"Tipo\" = Resumo quando a atividade tem filhas no WBS; \"Pai (WBS)\" aponta o Código da atividade-mãe; linhas já vêm na sequência do WBS.", False),
    ]
    for texto, negrito in linhas:
        wl.append([texto])
        if negrito:
            wl.cell(row=wl.max_row, column=1).font = Font(bold=True, size=13)
    wl.column_dimensions["A"].width = 150

    buf = BytesIO()
    wb.save(buf)
    resumo = {
        "linhas": len(atividades) + len(marcos or []),
        "atividades": len(atividades), "marcos": len(marcos or []),
        "dependencias": n_deps, "sem_codigo": sem_codigo,
    }
    return buf.getvalue(), resumo
