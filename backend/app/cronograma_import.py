"""
Importação de cronograma (Excel ou CSV, exportado do MS Project) direto pela
interface da Gestão de Projetos — botão "Importar Cronograma" na aba Cronograma.

Fluxo de duas etapas (mesmo padrão da importação de requisitos do TR pelo
documento): `preview()` lê a planilha e devolve um resumo + a lista de
grupos de Etapa/Frente encontrados no WBS para o usuário conferir/ajustar
(sem gravar nada ainda); `confirmar()` recebe esse mapeamento já revisado e
grava de fato.

DECISÕES DE PRODUTO (combinadas com o usuário antes de implementar):
- Reimportar SEMPRE atualiza o projeto atual — nunca cria um projeto novo.
  Atividades e marcos já existentes são casados com a linha correspondente
  da planilha e ATUALIZADOS (nome, datas previstas, horas previstas,
  percentual concluído); campos manuais (recurso, tipo de atividade,
  status "Bloqueada"/"Cancelada", observações, item do TR) NUNCA são
  sobrescritos por uma reimportação.
- O casamento entre a planilha e uma atividade já existente usa, nesta
  ordem: (1) origem_importacao_id — o "Id" da tarefa no MS Project, mais
  robusto porque sobrevive a uma renumeração do WBS (inserir/mover tarefas
  no MS Project); (2) codigo_wbs — o EDT da planilha, usado como
  compatibilidade com atividades cadastradas antes de origem_importacao_id
  existir (ex: pelo script scripts/importar_cronograma_pcr.py). Toda
  atividade criada ou atualizada por aqui grava origem_importacao_id, então
  a partir da primeira importação por este caminho o casamento fica robusto
  a renumeração de WBS nas importações seguintes.
- Etapa/Frente de cada ramo do WBS não são mais um mapeamento fixo no
  código (como no script) — o preview sugere um nome (o nome da própria
  Etapa/grupo no WBS, ou uma Etapa/Frente já cadastrada com nome parecido)
  e o usuário confirma ou ajusta antes de gravar, porque a estrutura do WBS
  pode mudar entre uma exportação e outra.
- 37ª rodada: quando o preview não sugere (com confiança) uma Etapa/Frente já
  cadastrada pelo nome do ramo do WBS e o usuário também não pede
  explicitamente para criar uma nova na tela, a atividade É IMPORTADA MESMO
  ASSIM, só que SEM Etapa/Frente (etapa_id/frente_trabalho_id ficam NULL —
  ver migração 026) — em vez do comportamento antigo, que ou bloqueava a
  linha com erro ou inventava um grupo novo a partir do texto do WBS (o que
  não batia com a taxonomia real de Etapas/Frentes do cliente, cadastrada em
  Configurações). Numa reimportação, uma atividade que já tem Etapa/Frente
  (manual ou de importação anterior) nunca é voltada pra NULL só porque desta
  vez não foi identificada de novo — mesmo espírito "só adiciona, nunca
  apaga" já usado pra recursos e dependências.
- Mesmas regras de conversão já validadas no script: atividade "resumo" (que
  tem subatividades) não recebe prazo_horas (a Duração dela no MS Project é
  o prazo de calendário do ramo inteiro, não o esforço da atividade em si);
  duração em dias/semanas corridos, OU um número puro sem unidade (célula
  numérica solta), é convertida em horas por aproximação (×8h/dia,
  ×40h/semana); uma tarefa de duração "0 hrs" vira Marco em vez de
  Atividade; toda atividade que a planilha mostra "em andamento" ganha um
  relato automático antes de ter o status alterado (regra de negócio do
  sistema).
- 37ª rodada: colunas ★ Master / 💰 Entregável (atividades.eh_atividade_master
  / eh_entregavel), quando presentes na planilha ("Sim" na célula), marcam a
  atividade — mesmo espírito "só adiciona": em branco nunca desmarca uma
  atividade já marcada manualmente pela tela.
- 42ª rodada: o modo "flat" (planilha no formato da própria "Exportar
  Cronograma" — ver cronograma_export.py) passou a CRIAR uma atividade nova
  quando o Código da linha não é encontrado no projeto, em vez de só avisar
  "não encontrada" e pular a linha. Motivo: o caso de uso real não é só
  "exportei, ajustei 2 células, reimportei por cima" — é também "apaguei o
  cronograma (ou comecei um projeto do zero) e quero restaurar/popular a
  partir da própria planilha exportada", e nesse caso o comportamento antigo
  (só atualiza) deixava o usuário sem nenhuma forma de repovoar o projeto
  pelo modo flat. Ao criar, a Etapa/Frente indicadas na planilha também são
  criadas automaticamente se ainda não existirem no projeto (casando por
  número/nome — mesma lógica de _resolver_etapa_flat/_resolver_frente_flat
  usada pra atualização); Status/Prioridade em branco ou não reconhecidos na
  criação caem no default do sistema ('Não iniciada'/'Média') em vez de
  "mantido" (não existe valor anterior pra manter). Uma linha nova SEM
  Código também é criada (mesmo espírito), mas com aviso de que uma
  reimportação futura não vai conseguir casar aquela linha automaticamente
  (recomenda-se preencher o Código pela tela depois). O comportamento pra
  atividades JÁ EXISTENTES (casadas por Código) não muda em nada — continua
  só atualizando, nunca apagando um campo por causa de uma célula em branco.
"""
import csv
import io
import json
import os
import re
import time
import unicodedata
import uuid as uuid_lib
from datetime import date, datetime

import openpyxl

from . import db

TMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "tmp_cronograma")

STATUS_EXIGE_RELATO = {"Em andamento", "Bloqueada", "Cancelada"}
TIPO_DEPENDENCIA_CODE = {"": "FS", "TI": "FS", "II": "SS", "TT": "FF", "IT": "SF"}
CORES_FRENTE = ["#2f5f8a", "#7a4fa0", "#1f7a4d", "#a15c0d", "#0d8a99", "#ab2f2f",
                "#4a6b1f", "#8a5ca1", "#556b8a", "#8a6d3a", "#3a7a8a", "#5a5a5a"]

# Vocabulário de classificação do tipo de trabalho — genérico (não específico
# de um cronograma), reaproveitado do script original.
TIPO_KEYWORDS = [
    ("Homologação", ["homologa"]),
    ("Teste", ["teste", "testes da consultoria"]),
    ("Programação", ["programaç", "desenvolvimento"]),
    ("Levantamento", ["levantamento", "análise do levantamento"]),
    ("Especificação de Customização", ["especificaç"]),
    ("Parametrização", ["parametrizaç"]),
    ("Extração", ["extração"]),
    ("Carga", [" carga "]),
    ("Treinamento", ["treinamento"]),
    ("Reunião", ["reunião", "kick off", "kickoff"]),
    ("Entrega", ["entrega", "instalação", "go live"]),
    ("Comparação de Folha", ["comparação"]),
    ("Ajustes/Correções", ["ajuste", "correç"]),
]

COLUNA_ALIASES = {
    "edt": ["edt", "wbs"],
    "id": ["id"],
    "nome": ["nome da tarefa", "nome", "task name", "tarefa"],
    "inicio": ["inicio", "data inicio", "start"],
    "termino": ["termino", "data termino", "finish", "fim"],
    # "duracao dias" primeiro de propósito: quando a planilha tem as duas colunas
    # (ex: "Duração (dias)" e "Duração" repetindo o mesmo valor — caso real de uma
    # planilha exportada por aqui mesmo e depois editada à mão), prefere a
    # inequívoca ("(dias)" no cabeçalho já diz a unidade) — ver parse_duracao_horas.
    "duracao": ["duracao dias", "duracao", "duration"],
    # 40ª rodada (bugfix): a planilha do cliente veio com o cabeçalho "% Conclusão"
    # (não "% Concluída"), que o casamento por igualdade exata não reconhecia —
    # a coluna simplesmente não era mapeada e TODA a linha caía no default 0 em
    # parse_pct(None), com ou sem o sinal "%" no valor da célula (que o parse_pct
    # já tratava certo). Lista de aliases ampliada + fallback por "%" no cabeçalho
    # logo abaixo, em _mapear_colunas, pra não depender de prever cada variação.
    "pct": [
        "% concluida", "%concluida", "% concluido", "%concluido",
        "% conclusao", "%conclusao", "conclusao", "concluido",
        "percentual concluido", "percentual", "percent complete", "% complete",
        "% realizado", "%realizado", "realizado", "% completo", "%completo",
    ],
    "predecessoras": ["predecessoras", "predecessors"],
    "recursos": ["nomes dos recursos", "nome dos recursos", "recursos", "resource names"],
    # 37ª rodada: campos ★ Master e 💰 Entregável (atividades.eh_atividade_master /
    # eh_entregavel) passam a ser lidos da planilha, se vierem — ver Node e o uso
    # em confirmar(). Aceita tanto o nome exportado por este sistema ("Entregavel"/
    # "Master", sem acento — Excel às vezes normaliza) quanto variantes comuns.
    "entregavel": ["entregavel", "entregável", "eh entregavel", "e entregavel"],
    "master": ["master", "atividade master", "eh master", "e master"],
}
COLUNAS_OBRIGATORIAS = ("edt", "id", "nome", "duracao")

# ============================================================================
# Modo "flat" (round-trip) — reconhece a própria planilha gerada pelo botão
# "Exportar Cronograma (Excel)" (ver cronograma_export.py) quando o usuário a
# reimporta depois de editar. Diferente do modo acima (pensado para uma
# planilha do MS Project, com WBS/EDT e uma coluna "Id" própria de onde se
# monta a árvore de Etapa/Frente/Atividade), aqui cada linha já É uma
# atividade existente do projeto — identificada diretamente pela coluna
# "Código" (o codigo_wbs já gravado no banco) — então não há árvore pra
# montar nem Etapa/Frente novas pra criar: só ATUALIZA o que já existe,
# casado pelo Código. Detecção pelo "fingerprint" do cabeçalho (ver
# _eh_cabecalho_flat): a combinação Código+Atividade+Etapa+Frente+Status só
# aparece nesta exportação — uma planilha do MS Project não tem essas cinco
# colunas ao mesmo tempo (em especial "Frente" e "Código"), então não há
# risco de confundir os dois formatos.
FLAT_CABECALHO_FINGERPRINT = {"codigo", "atividade", "etapa", "frente", "status"}

FLAT_COLUNA_ALIASES = {
    "codigo": ["codigo"],
    "nome": ["atividade", "nome da tarefa", "nome", "tarefa"],
    "etapa": ["etapa"],
    "frente": ["frente"],
    "status": ["status"],
    "prioridade": ["prioridade"],
    "inicio_prev": ["inicio prev"],
    "fim_prev": ["fim prev"],
    "esforco_prev": ["esforco prev h"],
    "inicio_real": ["inicio real"],
    "fim_real": ["fim real"],
    "horas_realizadas": ["horas realizadas h"],
    "pct": ["% concluido", "concluido"],
    "master": ["master"],
    "entregavel": ["entregavel", "entregável"],
    "responsaveis": ["responsaveis", "responsáveis"],
    "observacoes": ["observacoes", "observações"],
}

STATUS_ATIVIDADE_VALIDOS = {
    "Não iniciada", "Em andamento", "Bloqueada",
    "Concluída", "Concluída com atraso", "Concluída com esforço maior",
    "Concluída com atraso e esforço maior", "Cancelada",
}
PRIORIDADE_VALIDOS = {"Urgente", "Alta", "Média", "Baixa"}


class ImportacaoError(ValueError):
    pass


# ============================================================================
# Leitura do arquivo (XLSX ou CSV) — dispensa depender da ordem das colunas,
# casando pelo NOME do cabeçalho (sem acento/maiúsculas), pra tolerar uma
# nova exportação do MS Project com colunas reordenadas ou extras.
# ============================================================================
def _normaliza_texto(s):
    s = str(s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s


def _normaliza_cabecalho(s):
    return re.sub(r"[^a-z0-9%]+", " ", _normaliza_texto(s)).strip()


def _mapear_colunas(headers_normalizados):
    idx_by_header = {}
    for i, h in enumerate(headers_normalizados):
        if h and h not in idx_by_header:
            idx_by_header[h] = i
    resultado = {}
    for chave, aliases in COLUNA_ALIASES.items():
        for alias in aliases:
            alias_norm = _normaliza_cabecalho(alias)
            if alias_norm in idx_by_header:
                resultado[chave] = idx_by_header[alias_norm]
                break
    # Fallback pra "pct": se nenhum alias bateu, mas existe um cabeçalho com o
    # sinal "%" (e ele ainda não foi usado por outra coluna), assume que é o
    # percentual concluído — cobre variações de nome que a lista de aliases
    # acima não previu (ex.: "% Conclusão"), sem risco de pegar coluna errada
    # porque nenhuma outra chave do COLUNA_ALIASES espera "%" no cabeçalho.
    if "pct" not in resultado:
        usados = set(resultado.values())
        for h, i in idx_by_header.items():
            if "%" in h and i not in usados:
                resultado["pct"] = i
                break
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in resultado]
    if faltando:
        raise ImportacaoError(
            "Não encontrei as colunas obrigatórias: " + ", ".join(faltando) + ". "
            "Confira se a planilha tem as colunas EDT, Id, Nome da tarefa e Duração "
            "(a ordem pode ser diferente, mas os nomes do cabeçalho precisam bater)."
        )
    return resultado


def _eh_cabecalho_flat(headers_normalizados):
    presentes = set(h for h in headers_normalizados if h)
    return FLAT_CABECALHO_FINGERPRINT.issubset(presentes)


def _mapear_colunas_flat(headers_normalizados):
    idx_by_header = {}
    for i, h in enumerate(headers_normalizados):
        if h and h not in idx_by_header:
            idx_by_header[h] = i
    resultado = {}
    for chave, aliases in FLAT_COLUNA_ALIASES.items():
        for alias in aliases:
            alias_norm = _normaliza_cabecalho(alias)
            if alias_norm in idx_by_header:
                resultado[chave] = idx_by_header[alias_norm]
                break
    if "pct" not in resultado:
        usados = set(resultado.values())
        for h, i in idx_by_header.items():
            if "%" in h and i not in usados:
                resultado["pct"] = i
                break
    return resultado


def _ler_xlsx(file_bytes):
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise ImportacaoError(f"Não consegui abrir o arquivo como .xlsx: {e}")
    nome_aba = None
    for name in wb.sheetnames:
        if wb[name].max_row > 1:
            nome_aba = name
            break
    if not nome_aba:
        raise ImportacaoError("Não encontrei nenhuma aba com dados na planilha.")
    ws = wb[nome_aba]
    headers = [_normaliza_cabecalho(ws.cell(1, c).value) for c in range(1, ws.max_column + 1)]
    modo = "flat" if _eh_cabecalho_flat(headers) else "hierarquico"
    colmap = _mapear_colunas_flat(headers) if modo == "flat" else _mapear_colunas(headers)
    linhas = []
    for r in range(2, ws.max_row + 1):
        vals = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        if all(v is None or str(v).strip() == "" for v in vals):
            continue
        linhas.append({chave: vals[idx] for chave, idx in colmap.items()})
    return linhas, modo


def _ler_csv(file_bytes):
    try:
        raw = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = file_bytes.decode("latin-1")
    if not raw.strip():
        raise ImportacaoError("Arquivo CSV vazio.")
    primeira_linha = raw.splitlines()[0]
    delimiter = ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","
    rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))
    if not rows:
        raise ImportacaoError("Não foi possível ler o CSV.")
    headers = [_normaliza_cabecalho(h) for h in rows[0]]
    modo = "flat" if _eh_cabecalho_flat(headers) else "hierarquico"
    colmap = _mapear_colunas_flat(headers) if modo == "flat" else _mapear_colunas(headers)
    linhas = []
    for row in rows[1:]:
        if all((c or "").strip() == "" for c in row):
            continue
        linhas.append({chave: (row[idx] if idx < len(row) else None) for chave, idx in colmap.items()})
    return linhas, modo


def carregar_linhas(file_bytes, filename):
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if ext in ("xlsx", "xlsm"):
        return _ler_xlsx(file_bytes)
    if ext == "csv":
        return _ler_csv(file_bytes)
    raise ImportacaoError(f"Formato de arquivo não suportado ({ext or 'desconhecido'}) — use .xlsx ou .csv.")


# ============================================================================
# Parsing de campos (duração, datas, percentual, predecessoras, recursos)
# ============================================================================
def _valor_texto_ou_num(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip() if v not in (None, "") else None


def edt_dots(edt):
    return str(edt).count(".") if edt not in (None, "") else 0


def parse_date(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = m.groups()
        try:
            return date(int(y), int(mo), int(d))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        try:
            return date(int(y), int(mo), int(d))
        except ValueError:
            return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{2})$", s)
    if m:
        d, mo, y = m.groups()
        try:
            return date(2000 + int(y), int(mo), int(d))
        except ValueError:
            return None
    return None


def parse_pct(v):
    if v in (None, ""):
        return 0
    if isinstance(v, (int, float)):
        f = float(v)
        return round(f * 100) if f <= 1 else round(f)
    s = str(v).strip().replace("%", "").replace(",", ".")
    if not s:
        return 0
    try:
        f = float(s)
    except ValueError:
        return 0
    return round(f * 100) if f <= 1 else round(f)


def parse_duracao_horas(v):
    """Retorna (horas, aproximado:bool) ou (None, False) se não reconhecido."""
    if v in (None, ""):
        return None, False
    txt = str(v).strip().replace(",", ".")
    m = re.match(r"([\d.]+)\s*(hrs?|dias?|dia|sem(?:s|anas)?)$", txt, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unidade = m.group(2).lower()
        if unidade.startswith("hr"):
            return val, False
        if unidade.startswith("dia"):
            return val * 8.0, True
        if unidade.startswith("sem"):
            return val * 5 * 8.0, True
        return None, False
    # Número puro, sem unidade (ex: célula numérica "6.75", sem "hrs"/"dias" no
    # texto) — a planilha pode vir assim quando o valor já foi calculado em dias
    # em outra ferramenta e colado como número. Trata como DIAS (mesma aproximação
    # ×8h/dia dos outros casos), que é a leitura mais comum pra uma coluna
    # "Duração" numérica solta — nunca como horas, pra não subestimar 8x o prazo.
    if re.match(r"^[\d.]+$", txt):
        return float(txt) * 8.0, True
    return None, False


def parse_bool_sim(v):
    """'Sim'/True/1 -> True; qualquer outra coisa (vazio, 'Não', 0...) -> False.
    Usado nas colunas ★ Master / 💰 Entregável (37ª rodada)."""
    if v in (None, ""):
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return _normaliza_texto(v) in ("sim", "s", "x", "true", "verdadeiro", "1")


def parse_lag_horas(txt):
    m = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*(hrs?|dias?|dia|sem(?:s|anas)?)", txt, re.IGNORECASE)
    if not m:
        return 0.0
    val = float(m.group(1).replace(",", "."))
    unidade = m.group(2).lower()
    if unidade.startswith("hr"):
        return val
    if unidade.startswith("dia"):
        return val * 8.0
    if unidade.startswith("sem"):
        return val * 5 * 8.0
    return 0.0


def parse_predecessoras(txt, avisos):
    out = []
    if not txt:
        return out
    for token in re.split(r"[;,]\s*", str(txt).strip()):
        if not token:
            continue
        m = re.match(r"(\d+)\s*(II|TT|IT|TI)?\s*([+-]\s*[\d.,]+\s*[a-zA-Zçã]+)?", token)
        if not m:
            avisos.append(f"predecessora não reconhecida: {token!r}")
            continue
        origem_id = m.group(1)
        tipo = TIPO_DEPENDENCIA_CODE.get(m.group(2) or "", "FS")
        lag = parse_lag_horas(m.group(3)) if m.group(3) else 0.0
        out.append({"origem_id": origem_id, "tipo": tipo, "lag_horas": lag})
    return out


def parse_recursos(txt, recurso_info, cliente_sigla):
    if not txt:
        return []
    out = []
    for token in str(txt).split(";"):
        token = token.strip()
        if not token:
            continue
        nome_limpo = re.sub(r"\[\d+%\]\s*$", "", token).strip()
        out.append(nome_limpo)
        if nome_limpo not in recurso_info:
            if cliente_sigla and nome_limpo.startswith(cliente_sigla):
                recurso_info[nome_limpo] = {"nome": nome_limpo, "tipo_vinculo": "Cliente", "empresa": cliente_sigla}
            elif nome_limpo.startswith("Consultoria") or nome_limpo.startswith("Techne"):
                # Aceita os dois prefixos de propósito: arquivos de cronograma já
                # existentes, exportados antes desta rodada de genericização, ainda
                # nomeiam os recursos da consultoria como "Techne - <nome>" — trocar
                # esse `startswith` por só "Consultoria" quebraria a importação
                # deles. Uma reimportação futura, feita a partir de um MS Project já
                # renomeado para "Consultoria - <nome>", também é reconhecida.
                recurso_info[nome_limpo] = {"nome": nome_limpo, "tipo_vinculo": "Consultoria", "empresa": "Consultoria"}
            else:
                recurso_info[nome_limpo] = {"nome": nome_limpo, "tipo_vinculo": "Terceirizado", "empresa": None}
    return out


def eh_marco(node):
    if node.is_summary:
        return False
    txt = str(node.duracao_txt or "").strip().lower()
    return bool(re.match(r"^0([.,]0+)?\s*hrs?$", txt))


# ============================================================================
# Estrutura em árvore (WBS via EDT — funciona igual pra xlsx e csv, e não
# depende de indentação de texto, que o export em CSV normalmente não tem)
# ============================================================================
class Node:
    def __init__(self, linha):
        self.edt = _valor_texto_ou_num(linha.get("edt"))
        self.origem_id = _valor_texto_ou_num(linha.get("id"))
        self.nome = str(linha.get("nome") or "").strip()
        self.dots = edt_dots(self.edt)
        self.dtini = parse_date(linha.get("inicio"))
        self.dtfim = parse_date(linha.get("termino"))
        self.duracao_txt = linha.get("duracao")
        self.pct = parse_pct(linha.get("pct"))  # já em 0..100
        self.predecessoras_txt = linha.get("predecessoras")
        self.recursos_txt = linha.get("recursos")
        # ★ Master / 💰 Entregável (37ª rodada) — ver atividades.eh_atividade_master /
        # eh_entregavel. "Sim" na planilha marca; em branco NÃO desmarca numa
        # reimportação (mesmo espírito "só adiciona" já usado pra recursos/dependências
        # — ver confirmar()), então False aqui só decide o valor em atividades NOVAS.
        self.eh_entregavel = parse_bool_sim(linha.get("entregavel"))
        self.eh_master = parse_bool_sim(linha.get("master"))
        self.children = []
        self.parent = None
        self.is_summary = False
        self.etapa_key = None
        self.frente_key = None
        self.atividade_pai_node = None


def _montar_arvore(nodes):
    by_edt = {n.edt: n for n in nodes if n.edt}
    for n in nodes:
        if not n.edt:
            continue
        partes = n.edt.split(".")
        if len(partes) > 1:
            pai = by_edt.get(".".join(partes[:-1]))
            if pai:
                n.parent = pai
                pai.children.append(n)
    for n in nodes:
        n.is_summary = len(n.children) > 0
    return by_edt


def _calcular_grupos_frente(nodes):
    """Frente = o nó de nível 2 do WBS que tem subatividades (um ramo de
    trabalho de verdade). Atividades soltas direto na Etapa (nível 2 sem
    filhos — comum em fases como Iniciação/Encerramento) caem no grupo
    sintético 'gestao' (frente padrão "Gestão do Projeto")."""
    grupos = {n.edt: n for n in nodes if n.dots == 2 and n.children}

    def nivel2_ancestor(n):
        cur = n
        while cur is not None and cur.dots > 2:
            cur = cur.parent
        return cur if (cur is not None and cur.dots == 2) else None

    for n in nodes:
        if n.dots < 2:
            continue
        anc = nivel2_ancestor(n)
        n.frente_key = anc.edt if (anc is not None and anc.edt in grupos) else "gestao"
    return grupos


# ============================================================================
# Modo "flat" (round-trip da própria exportação) — parsing de linha
# ============================================================================
def _parse_numero_flat(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _texto_ou_none(v):
    if v in (None, ""):
        return None
    s = str(v).strip()
    return s or None


def _parse_linha_flat(l):
    return {
        "codigo": _valor_texto_ou_num(l.get("codigo")),
        "nome": str(l.get("nome") or "").strip(),
        "etapa_txt": _texto_ou_none(l.get("etapa")),
        "frente_txt": _texto_ou_none(l.get("frente")),
        "status_txt": _texto_ou_none(l.get("status")),
        "prioridade_txt": _texto_ou_none(l.get("prioridade")),
        "dtini_prev": parse_date(l.get("inicio_prev")),
        "dtfim_prev": parse_date(l.get("fim_prev")),
        "prazo_horas": _parse_numero_flat(l.get("esforco_prev")),
        "dtini_real": parse_date(l.get("inicio_real")),
        "dtfim_real": parse_date(l.get("fim_real")),
        "horas_realizadas": _parse_numero_flat(l.get("horas_realizadas")),
        "pct": parse_pct(l.get("pct")) if l.get("pct") not in (None, "") else None,
        "eh_master": parse_bool_sim(l.get("master")),
        "eh_entregavel": parse_bool_sim(l.get("entregavel")),
        "responsaveis_txt": l.get("responsaveis"),
        "observacoes": _texto_ou_none(l.get("observacoes")),
    }


def _analisar_planilha_flat(linhas):
    avisos = []
    parsed = [_parse_linha_flat(l) for l in linhas]
    validas = [p for p in parsed if p["codigo"] or p["nome"]]
    ignoradas = len(parsed) - len(validas)
    if ignoradas:
        avisos.append(f"{ignoradas} linha(s) sem Código e sem Atividade — ignorada(s).")
    if not validas:
        raise ImportacaoError("Não encontrei nenhuma linha de dado válida na planilha.")
    return {"modo": "flat", "linhas": validas, "avisos": avisos}


def _resolver_etapa_flat(texto, etapas_existentes):
    """etapas_existentes: lista de {id, numero, nome}. Casa preferencialmente
    pelo número (formato exportado "N — Nome", robusto a uma Etapa renomeada
    depois da exportação) e, na falta de um número reconhecível, pelo nome
    (ignorando acento/caixa)."""
    if not texto:
        return None
    m = re.match(r"^\s*(\d+)\s*(?:—|-)", texto)
    if m:
        numero = int(m.group(1))
        for e in etapas_existentes:
            if e["numero"] == numero:
                return e["id"]
    nome_sem_numero = re.sub(r"^\s*\d+\s*(?:—|-)\s*", "", texto).strip()
    alvo = _normaliza_texto(texto)
    alvo_sem_numero = _normaliza_texto(nome_sem_numero)
    for e in etapas_existentes:
        nome_norm = _normaliza_texto(e["nome"])
        if nome_norm == alvo or nome_norm == alvo_sem_numero:
            return e["id"]
    return None


def _resolver_frente_flat(texto, frentes_existentes):
    if not texto:
        return None
    alvo = _normaliza_texto(texto)
    for f in frentes_existentes:
        if _normaliza_texto(f["nome"]) == alvo:
            return f["id"]
    return None


def _resolver_recursos_flat(texto):
    """'Fulano (Consultoria), Beltrano (Cliente)' -> ['Fulano', 'Beltrano'] —
    mesmo formato que _responsaveis_txt monta em cronograma_export.py."""
    if not texto:
        return []
    nomes = []
    for token in str(texto).split(","):
        token = token.strip()
        if not token:
            continue
        nome = re.sub(r"\s*\([^)]*\)\s*$", "", token).strip()
        if nome:
            nomes.append(nome)
    return nomes


def _proximo_numero_etapa_flat(etapas_existentes):
    usados = {e["numero"] for e in etapas_existentes if e.get("numero") is not None}
    n = 1
    while n in usados:
        n += 1
    return n


def _criar_etapa_flat(texto, projeto_id, etapas_existentes, statements):
    """Cria (acumula o INSERT de) uma Etapa nova a partir do texto da coluna
    'Etapa' da planilha flat, usada quando o modo flat cria uma atividade
    nova e a Etapa indicada não bate com nenhuma já cadastrada no projeto —
    ver 42ª rodada. Atualiza `etapas_existentes` (a lista compartilhada por
    toda a confirmação) IN PLACE, pra que outra linha da mesma planilha que
    cite a mesma Etapa reaproveite, em vez de criar duplicada. Casa/reaproveita
    o número quando a célula vem no formato exportado 'N — Nome' e esse
    número ainda está livre; senão usa o próximo número livre do projeto."""
    m = re.match(r"^\s*(\d+)\s*(?:—|-)\s*(.*)$", texto)
    nome = (m.group(2).strip() if m and m.group(2).strip() else texto.strip())[:250]
    numero_pedido = int(m.group(1)) if m else None
    ja_usado = any(e.get("numero") == numero_pedido for e in etapas_existentes)
    numero = numero_pedido if (numero_pedido and not ja_usado) else _proximo_numero_etapa_flat(etapas_existentes)
    novo_id = str(uuid_lib.uuid4())
    statements.append(_sql_insert("etapas", {
        "id": novo_id, "projeto_id": projeto_id, "numero": numero, "nome": nome,
        "descricao": "Criada automaticamente pela importação da planilha exportada (Etapa não encontrada no projeto).",
    }))
    etapas_existentes.append({"id": novo_id, "numero": numero, "nome": nome})
    return novo_id


def _criar_frente_flat(texto, projeto_id, frentes_existentes, statements):
    """Idem _criar_etapa_flat, para Frente de trabalho — não tem número, só nome."""
    nome = texto.strip()[:250]
    novo_id = str(uuid_lib.uuid4())
    ordem = len(frentes_existentes) + 1
    statements.append(_sql_insert("frentes_trabalho", {
        "id": novo_id, "projeto_id": projeto_id, "nome": nome,
        "cor_hex": CORES_FRENTE[(ordem - 1) % len(CORES_FRENTE)], "ordem": ordem,
    }))
    frentes_existentes.append({"id": novo_id, "nome": nome})
    return novo_id


# ============================================================================
# Análise da planilha (dispatcher: modo "flat" x modo "hierárquico")
# ============================================================================
def analisar_planilha(file_bytes, filename):
    linhas, modo = carregar_linhas(file_bytes, filename)
    if modo == "flat":
        return _analisar_planilha_flat(linhas)

    avisos = []
    nodes_validos = [Node(l) for l in linhas if l.get("edt") not in (None, "") and str(l.get("nome") or "").strip()]
    ignoradas = len(linhas) - len(nodes_validos)
    if ignoradas:
        avisos.append(f"{ignoradas} linha(s) sem EDT e/ou nome — ignorada(s).")
    if not nodes_validos:
        raise ImportacaoError("Não encontrei nenhuma linha de dado válida na planilha.")
    nodes = nodes_validos

    _montar_arvore(nodes)

    etapas_nodes = [n for n in nodes if n.dots == 1]
    for etapa_node in etapas_nodes:
        def walk(n, ek):
            n.etapa_key = ek
            for c in n.children:
                walk(c, ek)
        walk(etapa_node, etapa_node.edt)

    grupos_frente_nodes = _calcular_grupos_frente(nodes)

    for n in nodes:
        if n.dots < 2:
            continue
        anc = n.parent
        while anc is not None and anc.dots < 2:
            anc = anc.parent
        n.atividade_pai_node = anc

    milestone_nodes = [n for n in nodes if n.dots >= 2 and eh_marco(n)]
    ms_ids = set(id(n) for n in milestone_nodes)
    atividade_nodes = [n for n in nodes if n.dots >= 2 and id(n) not in ms_ids]

    if not etapas_nodes:
        raise ImportacaoError(
            "Não encontrei nenhuma linha de nível 1 do WBS (EDT com um só segmento, ex: \"1.1\") "
            "para virar Etapa — confira se a coluna EDT da planilha está preenchida."
        )

    return {
        "modo": "hierarquico",
        "nodes": nodes, "etapas_nodes": etapas_nodes, "grupos_frente_nodes": grupos_frente_nodes,
        "atividade_nodes": atividade_nodes, "milestone_nodes": milestone_nodes, "avisos": avisos,
    }


# ============================================================================
# Armazenamento temporário do arquivo entre o preview e a confirmação
# ============================================================================
def _salvar_temp(token, projeto_id, file_bytes, filename):
    os.makedirs(TMP_DIR, exist_ok=True)
    with open(os.path.join(TMP_DIR, f"{token}.bin"), "wb") as f:
        f.write(file_bytes)
    with open(os.path.join(TMP_DIR, f"{token}.json"), "w", encoding="utf-8") as f:
        json.dump({"projeto_id": projeto_id, "filename": filename}, f)


def _carregar_temp(token):
    bin_path, meta_path = os.path.join(TMP_DIR, f"{token}.bin"), os.path.join(TMP_DIR, f"{token}.json")
    if not (os.path.exists(bin_path) and os.path.exists(meta_path)):
        return None
    with open(bin_path, "rb") as f:
        file_bytes = f.read()
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return file_bytes, meta


def _remover_temp(token):
    for ext in (".bin", ".json"):
        try:
            os.remove(os.path.join(TMP_DIR, f"{token}{ext}"))
        except OSError:
            pass


def _normaliza_nome(s):
    """Normaliza pra comparação: remove acentos/caixa, um eventual prefixo
    numérico do WBS (ex: "03.01." ou "1.3.3 "), e pontuação — pra que
    "03.01. Levantamentos de Processos e Casos de Uso" tenha chance real de
    casar com uma Frente já cadastrada como "Levantamento de Processos"."""
    s = _normaliza_texto(s)
    s = re.sub(r"^[\d]+(?:[.\-][\d]+)*\.?\s*", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _palavras_stem(s):
    # stemming ingênuo (só tira um "s" final de palavras com 4+ letras) — o
    # bastante pra "levantamentos" casar com "levantamento" sem exagerar.
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in s.split()}


def _sugerir_match(nome_planilha, existentes):
    alvo = _normaliza_nome(nome_planilha)
    if not alvo:
        return None
    for e in existentes:
        if _normaliza_nome(e["nome"]) == alvo:
            return e["id"]
    alvo_palavras = _palavras_stem(alvo)
    melhor_id, melhor_score = None, 0.0
    for e in existentes:
        en_palavras = _palavras_stem(_normaliza_nome(e["nome"]))
        if not en_palavras:
            continue
        score = len(alvo_palavras & en_palavras) / max(1, min(len(alvo_palavras), len(en_palavras)))
        if score > melhor_score:
            melhor_score, melhor_id = score, e["id"]
    # exige que a maior parte das palavras do lado menor bata, pra não
    # sugerir grupos que só têm uma palavra genérica em comum (ex: "Folha")
    return melhor_id if melhor_score >= 0.7 else None


# ============================================================================
# Preview — não grava nada, só analisa e sugere
# ============================================================================
def _preview_flat(projeto_id, resultado, file_bytes, filename):
    avisos = [
        "Planilha reconhecida no formato de exportação desta tela (botão \"Exportar Cronograma\") — "
        "atualiza as atividades já existentes no projeto, casadas pela coluna Código, e CRIA uma "
        "atividade nova (criando também a Etapa/Frente indicadas, se ainda não existirem) para todo "
        "Código que não for encontrado — é o mesmo arquivo que serve pra restaurar/popular o "
        "cronograma inteiro num projeto vazio. Campos deixados em branco numa atividade JÁ EXISTENTE "
        "nunca apagam o que já está gravado (para limpar um campo, edite pela tela)."
    ] + list(resultado["avisos"])

    existentes = db.fetch_all(
        f"SELECT codigo_wbs FROM atividades WHERE projeto_id = {db.q(projeto_id)} AND codigo_wbs IS NOT NULL"
    )
    codigos_existentes = {r["codigo_wbs"] for r in existentes}
    etapas_existentes = db.fetch_all(f"SELECT id, numero, nome FROM etapas WHERE projeto_id = {db.q(projeto_id)}")
    frentes_existentes = db.fetch_all(f"SELECT id, nome FROM frentes_trabalho WHERE projeto_id = {db.q(projeto_id)}")
    etapas_novas_avisadas, frentes_novas_avisadas = set(), set()

    novas = atualizar = marcados_entregavel = marcados_master = 0
    for l in resultado["linhas"]:
        codigo_existe = bool(l["codigo"] and l["codigo"] in codigos_existentes)
        if not codigo_existe:
            if not l["nome"]:
                avisos.append(f"Linha com Código {l['codigo'] or '—'} sem nome de atividade — será ignorada, não será criada.")
                continue
            novas += 1
            if not l["codigo"]:
                avisos.append(f"Atividade '{l['nome']}' será criada sem Código — uma reimportação futura não vai conseguir casar essa linha automaticamente.")
        else:
            atualizar += 1

        if l["etapa_txt"] and not _resolver_etapa_flat(l["etapa_txt"], etapas_existentes):
            if codigo_existe:
                avisos.append(f"Etapa '{l['etapa_txt']}' (Código {l['codigo']}) não reconhecida — Etapa será mantida como está.")
            elif l["etapa_txt"] not in etapas_novas_avisadas:
                etapas_novas_avisadas.add(l["etapa_txt"])
                avisos.append(f"Etapa '{l['etapa_txt']}' não existe no projeto — será criada automaticamente.")
        if l["frente_txt"] and not _resolver_frente_flat(l["frente_txt"], frentes_existentes):
            if codigo_existe:
                avisos.append(f"Frente '{l['frente_txt']}' (Código {l['codigo']}) não reconhecida — Frente será mantida como está.")
            elif l["frente_txt"] not in frentes_novas_avisadas:
                frentes_novas_avisadas.add(l["frente_txt"])
                avisos.append(f"Frente '{l['frente_txt']}' não existe no projeto — será criada automaticamente.")
        if l["status_txt"] and l["status_txt"] not in STATUS_ATIVIDADE_VALIDOS:
            if codigo_existe:
                avisos.append(f"Status '{l['status_txt']}' (Código {l['codigo']}) não reconhecido — status será mantido como está.")
            else:
                avisos.append(f"Status '{l['status_txt']}' (Código {l['codigo'] or '—'}) não reconhecido — atividade será criada como 'Não iniciada'.")
        if l["prioridade_txt"] and l["prioridade_txt"] not in PRIORIDADE_VALIDOS:
            if codigo_existe:
                avisos.append(f"Prioridade '{l['prioridade_txt']}' (Código {l['codigo']}) não reconhecida — prioridade será mantida como está.")
            else:
                avisos.append(f"Prioridade '{l['prioridade_txt']}' (Código {l['codigo'] or '—'}) não reconhecida — atividade será criada com prioridade 'Média'.")
        if l["eh_entregavel"]:
            marcados_entregavel += 1
        if l["eh_master"]:
            marcados_master += 1

    token = uuid_lib.uuid4().hex
    _salvar_temp(token, projeto_id, file_bytes, filename)

    return {
        "token": token,
        "resumo": {
            "atividades_novas": novas, "atividades_atualizar": atualizar,
            "marcos_novos": 0, "marcos_atualizar": 0,
            "dependencias": 0, "horas_aproximadas": 0,
            "marcados_entregavel": marcados_entregavel, "marcados_master": marcados_master,
        },
        "avisos": avisos,
        "grupos_etapa": [],
        "grupos_frente": [],
    }


def preview(projeto_id, file_bytes, filename):
    resultado = analisar_planilha(file_bytes, filename)
    if resultado.get("modo") == "flat":
        return _preview_flat(projeto_id, resultado, file_bytes, filename)

    avisos = list(resultado["avisos"])

    etapas_existentes = db.fetch_all(f"SELECT id, nome FROM etapas WHERE projeto_id = {db.q(projeto_id)}")
    frentes_existentes = db.fetch_all(f"SELECT id, nome FROM frentes_trabalho WHERE projeto_id = {db.q(projeto_id)}")

    grupos_etapa = []
    for n in resultado["etapas_nodes"]:
        qtd = sum(1 for a in resultado["atividade_nodes"] if a.etapa_key == n.edt)
        grupos_etapa.append({
            "chave": n.edt, "nome_planilha": n.nome, "quantidade": qtd,
            "etapa_id_sugerida": _sugerir_match(n.nome, etapas_existentes),
        })

    grupos_frente = []
    for edt, n in sorted(resultado["grupos_frente_nodes"].items()):
        qtd = sum(1 for a in resultado["atividade_nodes"] if a.frente_key == edt)
        grupos_frente.append({
            "chave": edt, "nome_planilha": n.nome, "quantidade": qtd,
            "frente_id_sugerida": _sugerir_match(n.nome, frentes_existentes),
        })
    qtd_gestao = sum(1 for a in resultado["atividade_nodes"] if a.frente_key == "gestao")
    if qtd_gestao:
        grupos_frente.append({
            "chave": "gestao", "nome_planilha": "Gestão do Projeto", "quantidade": qtd_gestao,
            "frente_id_sugerida": (_sugerir_match("gestão", frentes_existentes)
                                   or _sugerir_match("gestão do projeto", frentes_existentes)),
        })

    existentes_atividades = db.fetch_all(
        f"SELECT origem_importacao_id, codigo_wbs FROM atividades WHERE projeto_id = {db.q(projeto_id)}"
    )
    por_origem = {r["origem_importacao_id"] for r in existentes_atividades if r["origem_importacao_id"]}
    por_wbs = {r["codigo_wbs"] for r in existentes_atividades if r["codigo_wbs"]}
    novas = atualizar = horas_aproximadas = marcados_entregavel = marcados_master = 0
    for n in resultado["atividade_nodes"]:
        casado = (n.origem_id in por_origem) or (n.edt in por_wbs)
        if casado:
            atualizar += 1
        else:
            novas += 1
        if not n.is_summary:
            _, aprox = parse_duracao_horas(n.duracao_txt)
            if aprox:
                horas_aproximadas += 1
        if n.eh_entregavel:
            marcados_entregavel += 1
        if n.eh_master:
            marcados_master += 1

    existentes_marcos = db.fetch_all(
        f"SELECT nome, data_prevista, origem_importacao_id FROM marcos WHERE projeto_id = {db.q(projeto_id)}"
    )
    marcos_origem = {r["origem_importacao_id"] for r in existentes_marcos if r["origem_importacao_id"]}
    # fallback pra marcos criados antes de origem_importacao_id existir (ex: pelo script antigo):
    # casa por nome + data prevista, já que marcos nunca tiveram um "código WBS" próprio.
    marcos_por_nome_data = {
        (r["nome"], r["data_prevista"]) for r in existentes_marcos if not r["origem_importacao_id"]
    }

    def _marco_casa(n):
        if n.origem_id in marcos_origem:
            return True
        data_prevista = n.dtfim or n.dtini
        if data_prevista and (n.nome[:250], data_prevista.isoformat()) in marcos_por_nome_data:
            return True
        return False

    marcos_atualizar = sum(1 for n in resultado["milestone_nodes"] if _marco_casa(n))
    marcos_novos = len(resultado["milestone_nodes"]) - marcos_atualizar

    total_dependencias = sum(
        len(parse_predecessoras(n.predecessoras_txt, avisos)) for n in resultado["atividade_nodes"]
    )

    token = uuid_lib.uuid4().hex
    _salvar_temp(token, projeto_id, file_bytes, filename)

    return {
        "token": token,
        "resumo": {
            "atividades_novas": novas, "atividades_atualizar": atualizar,
            "marcos_novos": marcos_novos, "marcos_atualizar": marcos_atualizar,
            "dependencias": total_dependencias, "horas_aproximadas": horas_aproximadas,
            "marcados_entregavel": marcados_entregavel, "marcados_master": marcados_master,
        },
        "avisos": avisos,
        "grupos_etapa": grupos_etapa,
        "grupos_frente": grupos_frente,
    }


# ============================================================================
# Confirmação — grava de fato, usando o mapeamento de Etapa/Frente revisado
# ============================================================================
#
# IMPORTANTE sobre desempenho (bug real, ver histórico do projeto): a versão
# original desta função gravava linha a linha — uma chamada Python (INSERT ou
# UPDATE) por atividade, mais uma consulta e um INSERT por dependência. Cada
# chamada em app/db.py abre um processo `psql` novo, ou seja, uma conexão TCP
# (+ TLS, no caso do Supabase) nova a cada linha. Contra o Postgres local isso
# é rápido (conexão no mesmo host, ~poucos ms) e passou despercebido em todos
# os testes deste ambiente — mas contra um banco remoto tipo Supabase, com
# ~300 atividades + ~220 dependências, isso significa mais de mil conexões
# novas numa única importação, e o tempo total pode facilmente estourar o
# timeout do gunicorn (--timeout 120, no Dockerfile) ou da própria conexão.
# Foi exatamente isso que aconteceu na prática: uma importação real parou em
# 42 de 300 atividades com "Erro de Requisição" genérico no navegador.
#
# A correção: montar o SQL de cada lote de linhas como texto e mandar tudo
# de uma vez para uma ÚNICA chamada ao psql (uma única conexão), em vez de
# uma chamada por linha. `_executar_em_lotes()` faz isso em lotes de
# `LOTE_TAMANHO` linhas (não tudo numa transação só) para que um problema
# pontual numa linha (ex: um valor que viola alguma constraint do banco)
# derrube só aquele lote, não a importação inteira — e para dar visibilidade
# real do progresso via `print()` (aparece em `docker compose logs backend`),
# que é o "log da carga" pedido depois desse incidente.
LOTE_TAMANHO = 50


def _sql_insert(table, data):
    """Monta o texto de um INSERT (não executa) — usado para acumular muitas
    linhas e mandar tudo de uma vez em `_executar_em_lotes`."""
    cols = [k for k, v in data.items() if v is not None]
    if not cols:
        raise ImportacaoError(f"Nenhum campo válido para inserir em {table}.")
    return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(db.q(data[k]) for k in cols)});"


def _sql_update(table, row_id, data):
    """Monta o texto de um UPDATE (não executa) — mesma ideia do `_sql_insert`."""
    sets = [f"{k} = {db.q(v)}" for k, v in data.items()]
    if not sets:
        return None
    return f"UPDATE {table} SET {', '.join(sets)} WHERE id = {db.q(row_id)};"


def _executar_em_lotes(statements, rotulo, tamanho=LOTE_TAMANHO, timeout=180):
    """Executa uma lista de statements SQL já prontos em lotes de até
    `tamanho`, cada lote como UMA chamada ao banco (BEGIN...COMMIT), em vez de
    uma chamada por statement — ver nota acima. `rotulo` é só para aparecer
    no log de progresso (`docker compose logs backend`)."""
    total = len(statements)
    if not total:
        return
    n_lotes = (total + tamanho - 1) // tamanho
    for i in range(0, total, tamanho):
        lote = statements[i:i + tamanho]
        numero_lote = i // tamanho + 1
        t0 = time.monotonic()
        script = "BEGIN;\n" + "\n".join(lote) + "\nCOMMIT;"
        db.execute(script, timeout=timeout)
        print(f"[cronograma_import] {rotulo}: lote {numero_lote}/{n_lotes} "
              f"({len(lote)} statement(s)) gravado em {time.monotonic() - t0:.1f}s", flush=True)


def _confirmar_flat(projeto_id, resultado):
    """Grava as atualizações do modo flat (round-trip da própria exportação).
    Atividade JÁ EXISTENTE (casada pela coluna Código) é só ATUALIZADA — nunca
    apaga um campo já gravado só porque a célula da planilha veio em branco
    (mesma cautela já aplicada a recursos/dependências/Master/Entregável no
    modo hierárquico, aqui estendida a todos os campos, já que este é o
    próprio formato de edição em massa do sistema — a planilha não carrega
    necessariamente TODOS os campos da atividade, só os que interessam
    editar). Uma linha cujo Código NÃO é encontrado no projeto CRIA uma
    atividade nova (42ª rodada — ver nota no topo do arquivo), criando também
    a Etapa/Frente indicadas se ainda não existirem; isso é o que permite
    restaurar/popular o cronograma inteiro a partir da própria planilha
    exportada, num projeto vazio ou num projeto do zero. Ainda não cria
    marco nem dependência (a planilha flat não carrega predecessoras, e
    marco/atividade nesse formato são a mesma linha — ver cronograma_export.py)."""
    avisos = list(resultado["avisos"])
    erros = []

    existentes = db.fetch_all(
        f"SELECT id, codigo_wbs, status FROM atividades WHERE projeto_id = {db.q(projeto_id)} AND codigo_wbs IS NOT NULL"
    )
    por_codigo = {r["codigo_wbs"]: r for r in existentes}
    etapas_existentes = db.fetch_all(f"SELECT id, numero, nome FROM etapas WHERE projeto_id = {db.q(projeto_id)}")
    frentes_existentes = db.fetch_all(f"SELECT id, nome FROM frentes_trabalho WHERE projeto_id = {db.q(projeto_id)}")
    recurso_id_by_nome = {r["nome"]: r["id"] for r in db.fetch_all("SELECT id, nome FROM recursos")}

    hoje = datetime.now().date().isoformat()
    statements = []
    atualizadas = criadas = 0

    for l in resultado["linhas"]:
        existente = por_codigo.get(l["codigo"]) if l["codigo"] else None

        # ---------------------------------------------------------------
        # Caminho 1: Código já existe no projeto -> ATUALIZA (comportamento
        # original, inalterado)
        # ---------------------------------------------------------------
        if existente:
            atividade_id = existente["id"]

            campos = {}
            if l["nome"]:
                campos["nome"] = l["nome"][:250]
            if l["etapa_txt"]:
                etapa_id = _resolver_etapa_flat(l["etapa_txt"], etapas_existentes)
                if etapa_id:
                    campos["etapa_id"] = etapa_id
                else:
                    avisos.append(f"Etapa '{l['etapa_txt']}' (Código {l['codigo']}) não reconhecida — Etapa mantida.")
            if l["frente_txt"]:
                frente_id = _resolver_frente_flat(l["frente_txt"], frentes_existentes)
                if frente_id:
                    campos["frente_trabalho_id"] = frente_id
                else:
                    avisos.append(f"Frente '{l['frente_txt']}' (Código {l['codigo']}) não reconhecida — Frente mantida.")

            status_novo = None
            if l["status_txt"]:
                if l["status_txt"] in STATUS_ATIVIDADE_VALIDOS:
                    status_novo = l["status_txt"]
                    if status_novo != existente.get("status"):
                        campos["status"] = status_novo
                else:
                    avisos.append(f"Status '{l['status_txt']}' (Código {l['codigo']}) não reconhecido — status mantido.")
            if l["prioridade_txt"]:
                if l["prioridade_txt"] in PRIORIDADE_VALIDOS:
                    campos["prioridade"] = l["prioridade_txt"]
                else:
                    avisos.append(f"Prioridade '{l['prioridade_txt']}' (Código {l['codigo']}) não reconhecida — prioridade mantida.")

            if l["dtini_prev"] is not None:
                campos["dtini_prev"] = l["dtini_prev"].isoformat()
            if l["dtfim_prev"] is not None:
                campos["dtfim_prev"] = l["dtfim_prev"].isoformat()
            if l["dtini_real"] is not None:
                campos["dtini_real"] = l["dtini_real"].isoformat()
            if l["dtfim_real"] is not None:
                campos["dtfim_real"] = l["dtfim_real"].isoformat()
            if l["prazo_horas"] is not None:
                campos["prazo_horas"] = l["prazo_horas"]
            if l["horas_realizadas"] is not None:
                campos["horas_realizadas"] = l["horas_realizadas"]
            if l["pct"] is not None:
                campos["percentual_concluido"] = l["pct"]
            if l["observacoes"] is not None:
                campos["observacoes"] = l["observacoes"]
            # ★ Master / 💰 Entregável: mesmo espírito "só adiciona" do modo
            # hierárquico — "Sim" na planilha liga a marcação; em branco nunca
            # desliga uma já marcada na tela.
            if l["eh_entregavel"]:
                campos["eh_entregavel"] = True
            if l["eh_master"]:
                campos["eh_atividade_master"] = True

            recursos_vinculados = 0
            for nome_recurso in _resolver_recursos_flat(l["responsaveis_txt"]):
                rid = recurso_id_by_nome.get(nome_recurso)
                if rid:
                    statements.append(
                        f"INSERT INTO atividade_recurso (atividade_id, recurso_id) "
                        f"VALUES ({db.q(atividade_id)}, {db.q(rid)}) "
                        f"ON CONFLICT (atividade_id, recurso_id) DO NOTHING;"
                    )
                    recursos_vinculados += 1
                else:
                    avisos.append(f"Recurso '{nome_recurso}' (Código {l['codigo']}) não encontrado em Configurações — não vinculado.")

            if status_novo and status_novo in STATUS_EXIGE_RELATO and status_novo != existente.get("status"):
                statements.append(_sql_insert("atividade_relato", {
                    "id": str(uuid_lib.uuid4()), "atividade_id": atividade_id,
                    "autor_nome": "Importação de cronograma (planilha exportada)",
                    "texto": f"Atualizado via reimportação da planilha exportada em {hoje}.",
                }))

            if campos:
                statements.append(_sql_update("atividades", atividade_id, campos))
            if campos or recursos_vinculados:
                atualizadas += 1
            continue

        # ---------------------------------------------------------------
        # Caminho 2: Código não encontrado -> CRIA (42ª rodada). Uma linha
        # sem nome nenhum não tem o que criar — ignorada com aviso, igual ao
        # comportamento de "linha inválida" já aplicado na análise da planilha.
        # ---------------------------------------------------------------
        if not l["nome"]:
            avisos.append(f"Linha com Código {l['codigo'] or '—'} sem nome de atividade — ignorada, não foi criada.")
            continue

        atividade_id = str(uuid_lib.uuid4())

        etapa_id = None
        if l["etapa_txt"]:
            etapa_id = _resolver_etapa_flat(l["etapa_txt"], etapas_existentes)
            if not etapa_id:
                etapa_id = _criar_etapa_flat(l["etapa_txt"], projeto_id, etapas_existentes, statements)
                avisos.append(f"Etapa '{l['etapa_txt']}' não existia no projeto — criada automaticamente para a atividade '{l['nome']}' (Código {l['codigo'] or '—'}).")

        frente_id = None
        if l["frente_txt"]:
            frente_id = _resolver_frente_flat(l["frente_txt"], frentes_existentes)
            if not frente_id:
                frente_id = _criar_frente_flat(l["frente_txt"], projeto_id, frentes_existentes, statements)
                avisos.append(f"Frente '{l['frente_txt']}' não existia no projeto — criada automaticamente para a atividade '{l['nome']}' (Código {l['codigo'] or '—'}).")

        status_novo = "Não iniciada"
        if l["status_txt"]:
            if l["status_txt"] in STATUS_ATIVIDADE_VALIDOS:
                status_novo = l["status_txt"]
            else:
                avisos.append(f"Status '{l['status_txt']}' (Código {l['codigo'] or '—'}) não reconhecido — atividade criada como 'Não iniciada'.")

        prioridade_nova = "Média"
        if l["prioridade_txt"]:
            if l["prioridade_txt"] in PRIORIDADE_VALIDOS:
                prioridade_nova = l["prioridade_txt"]
            else:
                avisos.append(f"Prioridade '{l['prioridade_txt']}' (Código {l['codigo'] or '—'}) não reconhecida — atividade criada com prioridade 'Média'.")

        campos = {
            "id": atividade_id, "projeto_id": projeto_id, "nome": l["nome"][:250],
            "codigo_wbs": l["codigo"], "etapa_id": etapa_id, "frente_trabalho_id": frente_id,
            "status": status_novo, "prioridade": prioridade_nova,
        }
        if l["dtini_prev"] is not None:
            campos["dtini_prev"] = l["dtini_prev"].isoformat()
        if l["dtfim_prev"] is not None:
            campos["dtfim_prev"] = l["dtfim_prev"].isoformat()
        if l["dtini_real"] is not None:
            campos["dtini_real"] = l["dtini_real"].isoformat()
        if l["dtfim_real"] is not None:
            campos["dtfim_real"] = l["dtfim_real"].isoformat()
        if l["prazo_horas"] is not None:
            campos["prazo_horas"] = l["prazo_horas"]
        if l["horas_realizadas"] is not None:
            campos["horas_realizadas"] = l["horas_realizadas"]
        if l["pct"] is not None:
            campos["percentual_concluido"] = l["pct"]
        if l["observacoes"] is not None:
            campos["observacoes"] = l["observacoes"]
        if l["eh_entregavel"]:
            campos["eh_entregavel"] = True
        if l["eh_master"]:
            campos["eh_atividade_master"] = True

        statements.append(_sql_insert("atividades", campos))

        if l["codigo"]:
            por_codigo[l["codigo"]] = {"id": atividade_id, "codigo_wbs": l["codigo"], "status": status_novo}
        else:
            avisos.append(f"Atividade '{l['nome']}' criada sem Código — uma reimportação futura não vai conseguir casar essa linha automaticamente; preencha o Código pela tela.")

        recursos_vinculados = 0
        for nome_recurso in _resolver_recursos_flat(l["responsaveis_txt"]):
            rid = recurso_id_by_nome.get(nome_recurso)
            if rid:
                statements.append(
                    f"INSERT INTO atividade_recurso (atividade_id, recurso_id) "
                    f"VALUES ({db.q(atividade_id)}, {db.q(rid)}) "
                    f"ON CONFLICT (atividade_id, recurso_id) DO NOTHING;"
                )
                recursos_vinculados += 1
            else:
                avisos.append(f"Recurso '{nome_recurso}' (Código {l['codigo'] or '—'}) não encontrado em Configurações — não vinculado.")

        if status_novo in STATUS_EXIGE_RELATO:
            statements.append(_sql_insert("atividade_relato", {
                "id": str(uuid_lib.uuid4()), "atividade_id": atividade_id,
                "autor_nome": "Importação de cronograma (planilha exportada)",
                "texto": f"Criada via importação da planilha exportada em {hoje}, já como status '{status_novo}'.",
            }))

        criadas += 1

    _executar_em_lotes(statements, "atividades (planilha exportada)")

    return {
        "atividades_criadas": criadas, "atividades_atualizadas": atualizadas,
        "marcos_criados": 0, "marcos_atualizados": 0,
        "dependencias_criadas": 0, "dependencias_existentes": 0,
        "horas_aproximadas": 0,
        "erros": erros, "avisos": avisos,
    }


def confirmar(token, projeto_id, mapeamento_etapas, mapeamento_frentes):
    inicio = time.monotonic()
    print(f"[cronograma_import] confirmar(): iniciando — projeto_id={projeto_id} token={token}", flush=True)
    temp = _carregar_temp(token)
    if not temp:
        raise ImportacaoError("Essa pré-visualização expirou ou já foi usada — refaça a análise da planilha.")
    file_bytes, meta = temp
    if meta.get("projeto_id") != projeto_id:
        raise ImportacaoError("Projeto não confere com o da pré-visualização — refaça a análise.")

    resultado = analisar_planilha(file_bytes, meta.get("filename"))

    if resultado.get("modo") == "flat":
        ret = _confirmar_flat(projeto_id, resultado)
        _remover_temp(token)
        print(f"[cronograma_import] confirmar() [flat]: concluído em {time.monotonic() - inicio:.1f}s — "
              f"{ret['atividades_criadas']} atividade(s) criada(s), "
              f"{ret['atividades_atualizadas']} atividade(s) atualizada(s)", flush=True)
        return ret

    avisos = list(resultado["avisos"])
    print(f"[cronograma_import] planilha reanalisada: {len(resultado['atividade_nodes'])} atividade(s), "
          f"{len(resultado['milestone_nodes'])} marco(s) candidato(s)", flush=True)

    projeto = db.fetch_one(f"SELECT * FROM projetos WHERE id = {db.q(projeto_id)}")
    if not projeto:
        raise ImportacaoError("Projeto não encontrado.")
    cliente_sigla = projeto.get("sigla") or projeto.get("cliente") or ""

    # -------- etapas, frentes, recursos e tipos: acumula os INSERTs novos e
    # grava tudo numa tacada só (poucas dezenas de linhas no total, não é
    # o gargalo, mas não custa nada aplicar o mesmo padrão) --------
    pre_statements = []

    # 37ª rodada: um grupo do WBS (ramo de Etapa ou de Frente) agora tem TRÊS
    # desfechos possíveis, não mais dois — e o padrão deixou de ser "cria uma nova
    # automaticamente com o nome do WBS":
    #   1. m["etapa_id"]/m["frente_id"] presente -> usa a Etapa/Frente já cadastrada
    #      escolhida (seja pela sugestão por nome, seja por escolha manual na tela).
    #   2. m["nome"] presente (sem id) -> o usuário pediu explicitamente para criar
    #      uma nova, com esse nome (ação deliberada, não mais o fallback silencioso).
    #   3. nem um nem outro -> DEIXA EM BRANCO (etapa_id/frente_trabalho_id = NULL
    #      nas atividades desse grupo) para o usuário configurar depois olhando o
    #      que já existe em Configurações — em vez de inventar um grupo novo a
    #      partir do texto do WBS, que muitas vezes não bate com a taxonomia real.
    mapa_etapas = {m["chave"]: m for m in (mapeamento_etapas or [])}
    etapa_id_by_key = {}
    n_etapas = (db.fetch_one(f"SELECT COUNT(*)::int AS n FROM etapas WHERE projeto_id = {db.q(projeto_id)}") or {}).get("n", 0)
    proximo_numero = n_etapas + 1
    for n in resultado["etapas_nodes"]:
        m = mapa_etapas.get(n.edt) or {}
        if m.get("etapa_id"):
            etapa_id_by_key[n.edt] = m["etapa_id"]
        elif m.get("nome"):
            nome = m["nome"].strip()[:250] or f"Etapa {n.edt}"
            novo_id = str(uuid_lib.uuid4())
            pre_statements.append(_sql_insert("etapas", {
                "id": novo_id, "projeto_id": projeto_id, "numero": proximo_numero, "nome": nome,
                "descricao": f"Criada pela importação de cronograma (EDT {n.edt}).",
            }))
            etapa_id_by_key[n.edt] = novo_id
            proximo_numero += 1
        else:
            etapa_id_by_key[n.edt] = None
            avisos.append(
                f"Etapa não identificada para o ramo '{n.nome}' (EDT {n.edt}) — atividade(s) "
                "importada(s) sem Etapa; configure manualmente no Cronograma."
            )

    mapa_frentes = {m["chave"]: m for m in (mapeamento_frentes or [])}
    frente_id_by_key = {}
    chaves_frente = set(resultado["grupos_frente_nodes"].keys())
    if any(a.frente_key == "gestao" for a in resultado["atividade_nodes"]):
        chaves_frente.add("gestao")
    n_frentes = (db.fetch_one(f"SELECT COUNT(*)::int AS n FROM frentes_trabalho WHERE projeto_id = {db.q(projeto_id)}") or {}).get("n", 0)
    proxima_ordem = n_frentes + 1
    for chave in sorted(chaves_frente):
        m = mapa_frentes.get(chave) or {}
        if m.get("frente_id"):
            frente_id_by_key[chave] = m["frente_id"]
        elif m.get("nome"):
            no = resultado["grupos_frente_nodes"].get(chave)
            nome_padrao = no.nome if no else "Gestão do Projeto"
            nome = m["nome"].strip()[:250] or nome_padrao
            novo_id = str(uuid_lib.uuid4())
            pre_statements.append(_sql_insert("frentes_trabalho", {
                "id": novo_id, "projeto_id": projeto_id, "nome": nome,
                "cor_hex": CORES_FRENTE[(proxima_ordem - 1) % len(CORES_FRENTE)], "ordem": proxima_ordem,
            }))
            frente_id_by_key[chave] = novo_id
            proxima_ordem += 1
        else:
            frente_id_by_key[chave] = None
            no = resultado["grupos_frente_nodes"].get(chave)
            nome_planilha = no.nome if no else "Gestão do Projeto"
            avisos.append(
                f"Frente de trabalho não identificada para o ramo '{nome_planilha}' — atividade(s) "
                "importada(s) sem Frente; configure manualmente no Cronograma."
            )

    recurso_info = {}
    for n in resultado["atividade_nodes"]:
        parse_recursos(n.recursos_txt, recurso_info, cliente_sigla)
    recurso_id_by_nome = {r["nome"]: r["id"] for r in db.fetch_all("SELECT id, nome FROM recursos")}
    for nome, info in recurso_info.items():
        if nome in recurso_id_by_nome:
            continue
        novo_id = str(uuid_lib.uuid4())
        pre_statements.append(_sql_insert("recursos", {
            "id": novo_id, "nome": info["nome"], "tipo_vinculo": info["tipo_vinculo"], "empresa": info["empresa"],
        }))
        recurso_id_by_nome[nome] = novo_id

    tipo_id_by_nome = {t["nome"]: t["id"] for t in db.fetch_all("SELECT id, nome FROM tipos_atividade_elementar")}
    for nome_tipo, _ in TIPO_KEYWORDS:
        if nome_tipo not in tipo_id_by_nome:
            novo_id = str(uuid_lib.uuid4())
            pre_statements.append(_sql_insert("tipos_atividade_elementar", {
                "id": novo_id, "nome": nome_tipo, "ordem": len(tipo_id_by_nome) + 1,
            }))
            tipo_id_by_nome[nome_tipo] = novo_id

    _executar_em_lotes(pre_statements, "etapas/frentes/recursos/tipos")

    def classificar_tipo(nome_tarefa):
        nome_lower = nome_tarefa.lower()
        for nome_tipo, kws in TIPO_KEYWORDS:
            for kw in kws:
                if kw in nome_lower:
                    return tipo_id_by_nome[nome_tipo]
        return None

    # -------- atividades existentes (casamento p/ update) --------
    existentes = db.fetch_all(
        f"SELECT id, codigo_wbs, origem_importacao_id, status FROM atividades WHERE projeto_id = {db.q(projeto_id)}"
    )
    por_origem = {r["origem_importacao_id"]: r for r in existentes if r["origem_importacao_id"]}
    por_wbs = {r["codigo_wbs"]: r for r in existentes if r["codigo_wbs"]}

    origem_id_to_atividade_id = {}
    criadas = atualizadas = horas_aproximadas = 0
    erros = []
    hoje = datetime.now().date().isoformat()
    # As 3 novas variantes de conclusão (29ª rodada/migração 020) ficam no mesmo rank 2
    # de "Concluída" — sem isso, uma atividade já classificada como "Concluída com
    # atraso" (rank ausente = 0 por padrão) seria REBAIXADA pra "Concluída" simples
    # (rank 2 > 0) só porque a planilha reimportada também mostra 100%, perdendo a
    # classificação de prazo/esforço já calculada pela tela.
    ordem_status = {
        "Não iniciada": 0, "Em andamento": 1,
        "Concluída": 2, "Concluída com atraso": 2, "Concluída com esforço maior": 2,
        "Concluída com atraso e esforço maior": 2,
    }

    # Statements de atividade/relato/recurso-extra são só ACUMULADOS neste
    # laço (nenhuma chamada ao banco aqui dentro) e gravados de uma vez logo
    # depois, em lotes — ver nota grande no início da seção "Confirmação".
    # Por isso o id de cada atividade nova precisa ser gerado aqui em Python
    # (em vez de vir do `DEFAULT gen_random_uuid()` da coluna via RETURNING):
    # sem isso não daria pra saber o id de uma atividade-pai/predecessora
    # ainda "só planejada para gravar" quando uma linha mais abaixo no WBS
    # precisa referenciá-la.
    statements_atividades = []

    for n in resultado["atividade_nodes"]:
        # 37ª rodada: etapa_id/frente_id podem vir None agora (grupo do WBS deixado
        # em branco no mapeamento — ver acima) — a atividade é importada mesmo
        # assim, só sem essa classificação, em vez de ser pulada com erro.
        frente_key = n.frente_key or "gestao"
        frente_id = frente_id_by_key.get(frente_key)
        etapa_id = etapa_id_by_key.get(n.etapa_key)

        horas, aprox = parse_duracao_horas(n.duracao_txt)
        if n.is_summary:
            horas = None
        elif aprox:
            horas_aproximadas += 1

        # Desde a migração 012, TODOS os recursos encontrados na planilha para esta
        # tarefa (não mais só "o primeiro da consultoria" e "o primeiro do cliente") viram
        # participantes da atividade em atividade_recurso — ver mais abaixo.
        recursos = parse_recursos(n.recursos_txt, recurso_info, cliente_sigla)

        pct = n.pct or 0
        if pct >= 100:
            status_sugerido = "Concluída"
        elif pct <= 0:
            status_sugerido = "Não iniciada"
        else:
            status_sugerido = "Em andamento"

        pai_atividade_id = None
        if n.atividade_pai_node is not None:
            pai_origem = n.atividade_pai_node.origem_id
            pai_atividade_id = (
                origem_id_to_atividade_id.get(pai_origem)
                or (por_origem.get(pai_origem) or {}).get("id")
                or (por_wbs.get(n.atividade_pai_node.edt) or {}).get("id")
            )

        existente = por_origem.get(n.origem_id) or por_wbs.get(n.edt)

        campos_base = {
            "atividade_pai_id": pai_atividade_id,
            "codigo_wbs": n.edt, "origem_importacao_id": n.origem_id, "nome": n.nome[:250],
            "prazo_horas": horas,
            "dtini_prev": n.dtini.isoformat() if n.dtini else None,
            "dtfim_prev": n.dtfim.isoformat() if n.dtfim else None,
            "percentual_concluido": pct,
        }
        # etapa_id/frente_trabalho_id só entram no dict quando RESOLVIDOS (não
        # None) — numa atividade já existente, omitir a chave preserva o que já
        # está gravado (manual ou de importação anterior) em vez de sobrescrever
        # com NULL só porque esta reimportação não conseguiu identificar de novo
        # (_sql_update, ao contrário de _sql_insert, não filtra None sozinho).
        # ★ Master / 💰 Entregável (eh_atividade_master/eh_entregavel): mesmo
        # espírito "só adiciona" já usado para recursos/dependências — "Sim" na
        # planilha LIGA a marcação; em branco nunca desliga uma já marcada na tela.
        if etapa_id:
            campos_base["etapa_id"] = etapa_id
        if frente_id:
            campos_base["frente_trabalho_id"] = frente_id
        if n.eh_entregavel:
            campos_base["eh_entregavel"] = True
        if n.eh_master:
            campos_base["eh_atividade_master"] = True

        try:
            if existente:
                atividade_id = existente["id"]
                campos_atividade = dict(campos_base)
                status_atual = existente.get("status")
                if status_atual in ("Bloqueada", "Cancelada"):
                    if status_sugerido != "Não iniciada":
                        avisos.append(
                            f"Atividade '{n.nome}' (EDT {n.edt}) está '{status_atual}' no sistema — a "
                            f"planilha sugere '{status_sugerido}', mas o status manual foi preservado."
                        )
                elif ordem_status.get(status_sugerido, 0) > ordem_status.get(status_atual, 0):
                    if status_sugerido in STATUS_EXIGE_RELATO:
                        statements_atividades.append(_sql_insert("atividade_relato", {
                            "id": str(uuid_lib.uuid4()), "atividade_id": atividade_id,
                            "autor_nome": "Importação de cronograma",
                            "texto": f"Atualizado via reimportação do cronograma em {hoje}. "
                                     f"Percentual concluído na planilha: {pct}%.",
                        }))
                    campos_atividade["status"] = status_sugerido
                # um único UPDATE com todos os campos (base + status, quando muda) — o
                # trigger de histórico de status compara OLD x NEW normalmente, mesmo
                # quando outros campos também mudam na mesma instrução.
                statements_atividades.append(_sql_update("atividades", atividade_id, campos_atividade))
                atualizadas += 1
                # Reimportação: só ADICIONA participantes novos encontrados na planilha
                # (nunca remove um que já esteja cadastrado manualmente — mesmo espírito
                # da importação de dependências, que também só adiciona).
                for nome_recurso in recursos:
                    rid = recurso_id_by_nome.get(nome_recurso)
                    if rid:
                        statements_atividades.append(
                            f"INSERT INTO atividade_recurso (atividade_id, recurso_id) "
                            f"VALUES ({db.q(atividade_id)}, {db.q(rid)}) "
                            f"ON CONFLICT (atividade_id, recurso_id) DO NOTHING;"
                        )
            else:
                atividade_id = str(uuid_lib.uuid4())
                status_inicial = status_sugerido if status_sugerido != "Não iniciada" else None
                nova = dict(campos_base)
                nova.update({
                    "id": atividade_id,
                    "projeto_id": projeto_id,
                    "tipo_atividade_elementar_id": classificar_tipo(n.nome),
                    "observacoes": (
                        f"Importado do cronograma (EDT {n.edt}, Id original {n.origem_id})."
                        + (" Duração original em dias/semanas corridos, convertida em horas por aproximação (×8h/dia)."
                           if aprox else "")
                    ),
                })
                # status "Não iniciada" é o DEFAULT da coluna — omite pra deixar o
                # banco preencher; qualquer outro status entra já na criação (o
                # gatilho de histórico registra NULL -> status_inicial normalmente).
                if status_inicial:
                    nova["status"] = status_inicial
                statements_atividades.append(_sql_insert("atividades", nova))
                criadas += 1
                for nome_recurso in recursos:
                    rid = recurso_id_by_nome.get(nome_recurso)
                    if rid:
                        statements_atividades.append(_sql_insert("atividade_recurso", {
                            "atividade_id": atividade_id, "recurso_id": rid,
                        }))
                if status_sugerido in STATUS_EXIGE_RELATO:
                    statements_atividades.append(_sql_insert("atividade_relato", {
                        "id": str(uuid_lib.uuid4()), "atividade_id": atividade_id,
                        "autor_nome": "Importação de cronograma",
                        "texto": f"Importado do cronograma (Excel/CSV) em {hoje}. "
                                 f"Percentual concluído na planilha: {pct}%.",
                    }))
        except ImportacaoError as e:
            erros.append({"edt": n.edt, "nome": n.nome, "erro": str(e)})
            continue

        origem_id_to_atividade_id[n.origem_id] = atividade_id

    _executar_em_lotes(statements_atividades, "atividades")
    print(f"[cronograma_import] atividades: {criadas} criada(s), {atualizadas} atualizada(s), "
          f"{len(erros)} erro(s) até aqui", flush=True)

    # -------- dependências (só adiciona — nunca remove uma já existente) --------
    # Uma única consulta traz TODAS as dependências já existentes do projeto de
    # uma vez (em vez de uma consulta por atividade, como na versão anterior).
    dep_criadas = dep_existentes = 0
    ja_tem_por_atividade = {}
    for r in db.fetch_all(
        "SELECT d.atividade_id, d.predecessora_id FROM atividade_dependencia d "
        "JOIN atividades a ON a.id = d.atividade_id "
        f"WHERE a.projeto_id = {db.q(projeto_id)}"
    ):
        ja_tem_por_atividade.setdefault(r["atividade_id"], set()).add(r["predecessora_id"])

    statements_dependencias = []
    for n in resultado["atividade_nodes"]:
        preds = parse_predecessoras(n.predecessoras_txt, avisos)
        if not preds:
            continue
        ativ_id = origem_id_to_atividade_id.get(n.origem_id)
        if not ativ_id:
            continue
        ja_tem = ja_tem_por_atividade.setdefault(ativ_id, set())
        for p in preds:
            pred_ativ_id = origem_id_to_atividade_id.get(p["origem_id"])
            if not pred_ativ_id:
                avisos.append(
                    f"dependência órfã: atividade Id {n.origem_id} -> predecessora Id {p['origem_id']} "
                    "(não encontrada na planilha)"
                )
                continue
            if pred_ativ_id in ja_tem:
                dep_existentes += 1
                continue
            statements_dependencias.append(
                "INSERT INTO atividade_dependencia (atividade_id, predecessora_id, tipo, lag_horas) VALUES ("
                f"{db.q(ativ_id)}, {db.q(pred_ativ_id)}, {db.q(p['tipo'])}, {db.q(p['lag_horas'])});"
            )
            ja_tem.add(pred_ativ_id)
            dep_criadas += 1

    _executar_em_lotes(statements_dependencias, "dependências")
    print(f"[cronograma_import] dependências: {dep_criadas} criada(s), {dep_existentes} já existente(s)", flush=True)

    # -------- marcos --------
    marcos_existentes = db.fetch_all(
        f"SELECT id, nome, data_prevista, origem_importacao_id FROM marcos WHERE projeto_id = {db.q(projeto_id)}"
    )
    marcos_por_origem = {r["origem_importacao_id"]: r for r in marcos_existentes if r["origem_importacao_id"]}
    # fallback pra marcos criados antes de origem_importacao_id existir (ex: pelo script antigo):
    # casa por nome + data prevista, já que marcos nunca tiveram um "código WBS" próprio.
    marcos_por_nome_data = {
        (r["nome"], r["data_prevista"]): r for r in marcos_existentes if not r["origem_importacao_id"]
    }
    marcos_criados = marcos_atualizados = 0
    statements_marcos = []
    for n in resultado["milestone_nodes"]:
        data_prevista = n.dtfim or n.dtini
        if not data_prevista:
            avisos.append(f"marco '{n.nome}' sem data — não importado.")
            continue
        pct = n.pct or 0
        nome_marco = n.nome[:250]
        campos = {
            "etapa_id": etapa_id_by_key.get(n.etapa_key), "nome": nome_marco,
            "descricao": f"Importado do cronograma (EDT {n.edt}, Id original {n.origem_id}).",
            "data_prevista": data_prevista.isoformat(),
            "data_real": data_prevista.isoformat() if pct >= 100 else None,
            "origem_importacao_id": n.origem_id,
        }
        existente = marcos_por_origem.get(n.origem_id) or marcos_por_nome_data.get((nome_marco, data_prevista.isoformat()))
        if existente:
            statements_marcos.append(_sql_update("marcos", existente["id"], campos))
            marcos_atualizados += 1
        else:
            statements_marcos.append(_sql_insert("marcos", {"id": str(uuid_lib.uuid4()), "projeto_id": projeto_id, **campos}))
            marcos_criados += 1

    _executar_em_lotes(statements_marcos, "marcos")

    _remover_temp(token)

    print(f"[cronograma_import] confirmar(): concluído em {time.monotonic() - inicio:.1f}s — "
          f"{criadas} atividade(s) criada(s), {atualizadas} atualizada(s), {len(erros)} erro(s)", flush=True)

    return {
        "atividades_criadas": criadas, "atividades_atualizadas": atualizadas,
        "marcos_criados": marcos_criados, "marcos_atualizados": marcos_atualizados,
        "dependencias_criadas": dep_criadas, "dependencias_existentes": dep_existentes,
        "horas_aproximadas": horas_aproximadas,
        "erros": erros, "avisos": avisos,
    }
