"""
Análise de consistência entre o "de_para de Verbas" (Consist/legado -> Ergon) e
a tabela Rubricas do sistema — 169ª rodada.

Pedido do usuário (verbatim): "analise as duas planilha de_para_verbas e
Regras de negócio PCR...., essa planilha é a tabela Rubricas [...] e gere:
Aba com as verbas legado sem rubricas ergon / Aba com as Rubricas Ergon sem
Verba Legado / Aba com duplicidades / Aba com os registros corretos (match)
[...] crie um botão na Aba Rubricas que faça essa análise, mas que solicite o
arquivo base de de_para de Verbas do Consist (Legado) com o Ergon, a outra
tabela o sistema pode buscar da tabela Rubricas".

Duas fontes:

  A) de_para (arquivo enviado pelo usuário, CSV ou XLSX): uma linha por
     (EMPRESA_ORIGEM, VERBA_ORIGEM) -> RUBRICA_ERGON, mais nome/grupo.
  B) tabela `rubricas` (já no banco): cada linha é uma rubrica Ergon
     (codigo_ergon) com, por empresa, a(s) verba(s) legado em
     empresas[Exx].codigo (texto do tipo "205 - 13 SALARIO / TIPO: P", às
     vezes várias verbas na mesma célula, uma por linha) e a coluna "Verba 07"
     (verba_legado), que é a verba da empresa E07.

A chave de comparação é o TRIO (empresa, verba legado, rubrica Ergon) —
verba e rubrica sem zero à esquerda ("00065" == "65", mesma regra de
normaliza_codigo_rubrica, migration_036), empresa no formato "E07".

Todo trio de A ou B cai em EXATAMENTE UMA das categorias abaixo (nada some):

  match                  — o trio existe nas duas fontes.
  sem_rubrica_ergon      — a verba legado não tem rubrica Ergon utilizável
                           (RUBRICA_ERGON em branco no de_para; rubrica do
                           de_para que não existe na tabela Rubricas; verba
                           informada numa linha da tabela sem Código Ergon).
  divergencias           — a verba/rubrica existe nas duas fontes, mas o
                           vínculo é diferente (ou só uma fonte conhece).
  rubricas_sem_verba     — lado inverso: rubrica Ergon sem nenhuma verba
                           legado (linha da tabela sem verba; linha do de_para
                           sem VERBA_ORIGEM; empresa que a rubrica atende sem
                           verba informada para ela).
  duplicidades           — sobreposição, calculada À PARTE das demais
                           (um mesmo registro pode estar em "match" e também em
                           "duplicidades", p. ex. verba repetida).

Nada aqui grava no banco. Tudo roda em memória: o de_para tem poucos milhares
de linhas e a tabela Rubricas poucas centenas, bem abaixo de qualquer risco
pro limite de memória do plano atual.
"""
import csv
import io
import re
from collections import OrderedDict, defaultdict
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


class DeparaErro(Exception):
    """Arquivo de_para ilegível ou fora do layout esperado — a mensagem é
    mostrada direto ao usuário."""


COLUNAS_OBRIGATORIAS = ("EMPRESA_ORIGEM", "VERBA_ORIGEM", "RUBRICA_ERGON")
LIMITE_BYTES = 25 * 1024 * 1024
LIMITE_AVISOS = 60

_RE_SO_DIGITOS = re.compile(r"^\d+$")
_RE_EMPRESA = re.compile(r"^[Ee]?\s*(\d{1,3})$")
# "205 - 13 SALARIO / TIPO: P", "56\tCOMPLEMENT. CARGA HORARIA", "0002 - VENCIMENTO"
_RE_VERBA_LINHA = re.compile(r"^\s*(\d+)\s*(?:[-–—:]|\t|\s|$)\s*(.*)$")
# "141/228/301", "141, 228 e 301"
_RE_LISTA_VERBAS = re.compile(r"^\s*\d+(?:\s*(?:[/,;]|\be\b)\s*\d+)*\s*$", re.IGNORECASE)


def _norm_num(valor):
    """"00065" -> "65"; "0" continua "0"; texto não numérico passa só com
    trim. Mesma regra de comparacao_folha._normaliza_codigo_rubrica_py."""
    if valor is None:
        return None
    s = str(valor).strip()
    if not s:
        return None
    if _RE_SO_DIGITOS.match(s):
        return s.lstrip("0") or "0"
    return s


def _norm_empresa(valor):
    if valor is None:
        return None
    s = str(valor).strip()
    if not s:
        return None
    m = _RE_EMPRESA.match(s)
    if m:
        return f"E{int(m.group(1)):02d}"
    return s.upper()


def _limpa(valor):
    if valor is None:
        return None
    s = ILLEGAL_CHARACTERS_RE.sub("", str(valor)).replace("\xa0", " ").strip()
    return s or None


# --------------------------------------------------------------------------
# Leitura do de_para (arquivo enviado)
# --------------------------------------------------------------------------
def _decodificar(conteudo):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return conteudo.decode(enc)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _detectar_delimitador(cabecalho):
    candidatos = {d: cabecalho.count(d) for d in (";", ",", "\t")}
    melhor = max(candidatos, key=candidatos.get)
    return melhor if candidatos[melhor] > 0 else ";"


def _linhas_do_arquivo(conteudo, nome_arquivo):
    """Devolve (cabecalho_normalizado, [(numero_linha, [celulas...]), ...]).
    CSV: numero_linha é a linha FÍSICA do arquivo onde o registro termina
    (a que o usuário vê abrindo no editor); XLSX: número da linha do Excel."""
    if len(conteudo) > LIMITE_BYTES:
        raise DeparaErro("O arquivo de_para é grande demais (limite de 25 MB).")
    if conteudo[:2] == b"PK":  # xlsx é um zip
        try:
            wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        except Exception as e:
            raise DeparaErro(f"Não foi possível ler o arquivo como planilha Excel: {e}")
        for ws in wb.worksheets:
            linhas = list(ws.iter_rows(values_only=True))
            for idx, celulas in enumerate(linhas[:10]):
                cab = [str(c).strip().upper() if c is not None else "" for c in celulas]
                if all(col in cab for col in COLUNAS_OBRIGATORIAS):
                    corpo = [
                        (i + 1, ["" if c is None else str(c) for c in l])
                        for i, l in enumerate(linhas[idx + 1:], start=idx + 1)
                    ]
                    return cab, corpo
        raise DeparaErro(
            "Não encontrei, em nenhuma aba do arquivo, o cabeçalho esperado do de_para "
            f"({', '.join(COLUNAS_OBRIGATORIAS)}). Confirme se é o arquivo certo."
        )
    texto = _decodificar(conteudo)
    primeira = texto.split("\n", 1)[0]
    leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=_detectar_delimitador(primeira))
    corpo = []
    cab = None
    for celulas in leitor:
        if cab is None:
            if not any(c.strip() for c in celulas):
                continue
            cab = [c.strip().upper() for c in celulas]
            continue
        corpo.append((leitor.line_num, celulas))
    if cab is None:
        raise DeparaErro("O arquivo de_para está vazio.")
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in cab]
    if faltando:
        raise DeparaErro(
            f"O cabeçalho do arquivo de_para não tem a(s) coluna(s): {', '.join(faltando)}. "
            "Confirme se é o arquivo certo (esperado: o de_para de Verbas do Consist com o Ergon)."
        )
    return cab, corpo


def ler_depara(conteudo, nome_arquivo=""):
    """Lê o arquivo de_para e devolve {"registros": [...], "avisos": [...],
    "linhas_lidas": N, "linhas_ignoradas": N}. Cada registro:
    {linha, empresa, verba, rubrica, nome, grupo, fato}. Registros com
    verba ou rubrica em branco são MANTIDOS (verba/rubrica None) — são
    justamente o que as abas "sem rubrica" / "sem verba" precisam listar."""
    cab, corpo = _linhas_do_arquivo(conteudo, nome_arquivo)
    idx = {nome: cab.index(nome) for nome in cab if nome}

    def celula(celulas, nome):
        i = idx.get(nome)
        if i is None or i >= len(celulas):
            return None
        return celulas[i]

    registros, avisos, ignoradas, lidas = [], [], 0, 0
    for numero, celulas in corpo:
        if not any(str(c).strip() for c in celulas):
            continue
        lidas += 1
        empresa_bruta = (celula(celulas, "EMPRESA_ORIGEM") or "").strip()
        # Fragmento de texto: acontece quando um campo livre (ex: OBS) tem
        # quebra de linha sem aspas no CSV — a continuação vira uma "linha"
        # que começa com texto, não com o código da empresa. Os campos-chave
        # (primeiras colunas) da linha original já foram lidos normalmente.
        if not _RE_EMPRESA.match(empresa_bruta):
            ignoradas += 1
            if len(avisos) < LIMITE_AVISOS:
                avisos.append(
                    f"Linha {numero} do de_para ignorada: não começa com código de empresa "
                    f"(\"{empresa_bruta[:50]}...\") — provavelmente continuação de um campo de texto "
                    "com quebra de linha."
                )
            continue
        registros.append({
            "linha": numero,
            "empresa": _norm_empresa(empresa_bruta),
            "verba": _norm_num(celula(celulas, "VERBA_ORIGEM")),
            "rubrica": _norm_num(celula(celulas, "RUBRICA_ERGON")),
            "nome": _limpa(celula(celulas, "NOMEABREV")) or _limpa(celula(celulas, "NOMEEXTENSO")),
            "grupo": _limpa(celula(celulas, "GRUPO")),
            "fato": _limpa(celula(celulas, "FATO_ORIGEM")),
        })
    if not registros:
        raise DeparaErro("Nenhuma linha de dados válida foi encontrada no arquivo de_para.")
    return {
        "registros": registros, "avisos": avisos,
        "linhas_lidas": lidas, "linhas_ignoradas": ignoradas,
    }


# --------------------------------------------------------------------------
# Leitura da tabela Rubricas (já carregada do banco pelo chamador)
# --------------------------------------------------------------------------
def _verbas_da_celula(texto):
    """Extrai [(verba_normalizada, nome)] de uma célula "COD Exx" — uma verba
    por linha ("205 - 13 SALARIO / TIPO: P"). Linhas que não começam com
    número (texto solto, continuação de nome) são ignoradas."""
    achadas = []
    for linha in re.split(r"[\r\n]+", str(texto or "")):
        m = _RE_VERBA_LINHA.match(linha.replace("\xa0", " "))
        if not m:
            continue
        nome = re.sub(r"\s*/\s*TIPO\s*:?\s*\S*\s*$", "", m.group(2)).strip(" -–—\t")
        achadas.append((_norm_num(m.group(1)), nome or None))
    return achadas


def extrair_vinculos_rubricas(rubricas):
    """`rubricas`: linhas de `rubricas` (dicts do banco). Devolve
    {"vinculos": [...], "linhas": {id: info}, "avisos": [...]}.

    Um "vínculo" é (empresa, verba) de UMA linha da tabela — a rubrica
    daquela linha pode ser None (linha sem Código Ergon). `linhas` guarda, por
    linha da tabela, o que as abas "sem verba" precisam (nome, status,
    empresas em que se aplica, empresas com verba)."""
    vinculos, linhas, avisos = [], OrderedDict(), []
    for r in rubricas:
        rid = r.get("id") or f"linha-{r.get('linha_planilha')}"
        rubrica = _norm_num(r.get("codigo_ergon"))
        empresas = r.get("empresas") or {}
        if isinstance(empresas, str):
            empresas = {}
        nome_rubrica = _limpa(r.get("nome_abreviado")) or _limpa(r.get("nome_extenso"))
        verbas_por_empresa = defaultdict(OrderedDict)  # empresa -> {verba: nome}

        for sigla, dados in empresas.items():
            texto = (dados or {}).get("codigo")
            if not texto:
                continue
            achadas = _verbas_da_celula(texto)
            if not achadas and len(avisos) < LIMITE_AVISOS:
                avisos.append(
                    f"Tabela Rubricas, linha {r.get('linha_planilha')} (Ergon {rubrica or '—'}): "
                    f"COD {sigla} sem número de verba reconhecível (\"{str(texto).strip()[:60]}\")."
                )
            for verba, nome in achadas:
                verbas_por_empresa[_norm_empresa(sigla)].setdefault(verba, nome)

        # Coluna "Verba 07": verba(s) da empresa E07. Só vale se for um número
        # (ou lista de números — "141/228/301"); texto livre é ignorado.
        verba07 = _limpa(r.get("verba_legado"))
        if verba07:
            if _RE_LISTA_VERBAS.match(verba07):
                for v in re.findall(r"\d+", verba07):
                    verbas_por_empresa["E07"].setdefault(_norm_num(v), _limpa(r.get("descricao_legado")))
            elif len(avisos) < LIMITE_AVISOS:
                avisos.append(
                    f"Tabela Rubricas, linha {r.get('linha_planilha')} (Ergon {rubrica or '—'}): "
                    f"\"Verba 07\" não numérica (\"{verba07[:50]}\") — ignorada na comparação."
                )

        aplica = sorted(
            _norm_empresa(s) for s, d in empresas.items() if (d or {}).get("aplica")
        )
        linhas[rid] = {
            "id": rid, "linha": r.get("linha_planilha"), "rubrica": rubrica,
            "nome": nome_rubrica, "status": r.get("status"),
            "aplica": aplica, "verbas_por_empresa": verbas_por_empresa,
            "tem_verba": any(verbas_por_empresa.values()),
        }
        for empresa, verbas in verbas_por_empresa.items():
            for verba, nome in verbas.items():
                vinculos.append({
                    "linha_id": rid, "linha": r.get("linha_planilha"),
                    "empresa": empresa, "verba": verba, "rubrica": rubrica,
                    "nome_verba": nome, "nome_rubrica": nome_rubrica,
                    "status": r.get("status"),
                })
    return {"vinculos": vinculos, "linhas": linhas, "avisos": avisos}


# --------------------------------------------------------------------------
# Cruzamento
# --------------------------------------------------------------------------
def _juntar(valores, limite=12):
    vistos = []
    for v in valores:
        if v is not None and str(v) != "" and v not in vistos:
            vistos.append(v)
    if not vistos:
        return None
    if len(vistos) == 1:
        return vistos[0]
    texto = ", ".join(str(v) for v in vistos[:limite])
    if len(vistos) > limite:
        texto += f" … (+{len(vistos) - limite})"
    return texto


def _ordem_chave(*partes):
    """Ordena empresa (E07), verba e rubrica numericamente quando possível."""
    def k(p):
        s = "" if p is None else str(p)
        m = re.match(r"^E?(\d+)$", s)
        return (0, int(m.group(1)), "") if m else (1, 0, s)
    return tuple(k(p) for p in partes)


def analisar(depara, rubricas_db):
    """`depara`: retorno de ler_depara(). `rubricas_db`: linhas de `rubricas`.
    Devolve {"abas": {...listas de dicts...}, "contagens": {...}, "avisos": [...]}."""
    A = depara["registros"]
    extr = extrair_vinculos_rubricas(rubricas_db)
    B = extr["vinculos"]
    linhas_tabela = extr["linhas"]
    avisos = list(depara.get("avisos") or []) + list(extr["avisos"])

    nome_rubrica_por_codigo = {}
    status_por_codigo = defaultdict(set)
    for info in linhas_tabela.values():
        if info["rubrica"]:
            nome_rubrica_por_codigo.setdefault(info["rubrica"], info["nome"])
            if info["status"]:
                status_por_codigo[info["rubrica"]].add(info["status"])
    codigos_tabela = set(nome_rubrica_por_codigo)

    def status_de(rubrica):
        return " / ".join(sorted(status_por_codigo.get(rubrica, []))) or None

    # índices -----------------------------------------------------------
    A_trio = defaultdict(list)       # (emp, verba, rub) -> registros
    A_chave = defaultdict(list)      # (emp, verba) -> registros
    A_rub = defaultdict(list)        # rub -> registros
    for a in A:
        if a["verba"] is not None:
            A_chave[(a["empresa"], a["verba"])].append(a)
        if a["rubrica"] is not None:
            A_rub[a["rubrica"]].append(a)
            if a["verba"] is not None:
                A_trio[(a["empresa"], a["verba"], a["rubrica"])].append(a)

    B_trio = defaultdict(list)
    B_chave = defaultdict(list)
    B_rub = defaultdict(list)
    for b in B:
        B_chave[(b["empresa"], b["verba"])].append(b)
        if b["rubrica"] is not None:
            B_rub[b["rubrica"]].append(b)
            B_trio[(b["empresa"], b["verba"], b["rubrica"])].append(b)

    empresas_tabela = {e for l in linhas_tabela.values() for e in (l["verbas_por_empresa"] or {})} | {
        e for l in linhas_tabela.values() for e in l["aplica"]
    }

    def rubricas_de_A(chave):
        return sorted({a["rubrica"] for a in A_chave.get(chave, []) if a["rubrica"]}, key=lambda x: _ordem_chave(x))

    def rubricas_de_B(chave):
        return sorted({b["rubrica"] for b in B_chave.get(chave, []) if b["rubrica"]}, key=lambda x: _ordem_chave(x))

    # 1) match ----------------------------------------------------------
    match = []
    for trio in sorted(set(A_trio) & set(B_trio), key=lambda t: _ordem_chave(*t)):
        emp, verba, rub = trio
        a_regs, b_regs = A_trio[trio], B_trio[trio]
        match.append({
            "Empresa": emp, "Verba legado": verba,
            "Nome da verba (de_para)": _juntar([a["nome"] for a in a_regs], 1),
            "Rubrica Ergon": rub,
            "Nome da rubrica (Rubricas)": nome_rubrica_por_codigo.get(rub),
            "Status da rubrica": status_de(rub),
            "Grupo (de_para)": _juntar([a["grupo"] for a in a_regs], 1),
            "Linha(s) no de_para": _juntar([a["linha"] for a in a_regs]),
            "Linha(s) na tabela Rubricas": _juntar([b["linha"] for b in b_regs]),
        })

    # 2) verbas legado sem rubrica Ergon -----------------------------------
    # Caso mais comum (visto nos dados reais do cliente — consignações, rubricas
    # 7xxx): a tabela Rubricas tem a verba numa linha SEM Código Ergon e o
    # de_para já aponta a rubrica, que nem existe como linha da tabela. São as
    # duas pontas do MESMO problema — vira UMA linha só (Origem "de_para +
    # Tabela Rubricas"), não duas, pra não inflar a contagem.
    sem_rubrica = []
    fundidas = set()  # (empresa, verba) já tratadas pela fusão acima
    for b in B:
        if b["rubrica"] is not None:
            continue
        chave = (b["empresa"], b["verba"])
        sugeridas = rubricas_de_A(chave)
        inexistentes = [r for r in sugeridas if r not in codigos_tabela]
        if inexistentes:
            fundidas.add(chave)
            a_regs = [a for a in A_chave[chave] if a["rubrica"] in inexistentes]
            sem_rubrica.append(_lin_sem_rubrica(
                "de_para + Tabela Rubricas", b["empresa"], b["verba"],
                b["nome_verba"] or (a_regs[0]["nome"] if a_regs else None),
                "A tabela Rubricas informa a verba numa linha sem Código Ergon; o de_para indica a rubrica "
                f"{', '.join(inexistentes)}, que ainda não existe como linha da tabela",
                _juntar([a["linha"] for a in a_regs]), b["linha"], ", ".join(inexistentes),
                a_regs[0].get("grupo") if a_regs else None,
            ))
        else:
            sem_rubrica.append(_lin_sem_rubrica(
                "Tabela Rubricas", b["empresa"], b["verba"], b["nome_verba"],
                "Tabela Rubricas: verba informada em linha sem Código Ergon"
                + ("; o de_para indica a rubrica " + ", ".join(sugeridas) if sugeridas else " (e a verba não consta no de_para)"),
                None, b["linha"], ", ".join(sugeridas) or None, None,
            ))
    for a in A:
        if a["verba"] is None:
            continue
        chave = (a["empresa"], a["verba"])
        if a["rubrica"] is None:
            outra = [x for x in A_chave[chave] if x["rubrica"]]
            motivo = "de_para: RUBRICA_ERGON em branco"
            if outra:
                motivo += " (a mesma verba tem outra linha preenchida no de_para)"
            sem_rubrica.append(_lin_sem_rubrica(
                "de_para", a["empresa"], a["verba"], a["nome"], motivo,
                a["linha"], None, _juntar(rubricas_de_B(chave)), a.get("grupo"),
            ))
        elif a["rubrica"] not in codigos_tabela and chave not in fundidas:
            sem_rubrica.append(_lin_sem_rubrica(
                "de_para", a["empresa"], a["verba"], a["nome"],
                f"de_para aponta para a rubrica Ergon {a['rubrica']}, que não existe na tabela Rubricas",
                a["linha"], None, a["rubrica"], a.get("grupo"),
            ))
    sem_rubrica.sort(key=lambda r: (_ordem_chave(r["Empresa"], r["Verba legado"]), r["Origem"]))

    # 3) rubricas Ergon sem verba legado -----------------------------------
    sem_verba = []
    for info in linhas_tabela.values():
        if not info["rubrica"]:
            continue
        verbas_a = _juntar(
            [f"{a['empresa']}/{a['verba']}" for a in sorted(A_rub.get(info["rubrica"], []), key=lambda x: _ordem_chave(x["empresa"], x["verba"]))
             if a["verba"] is not None], 15)
        if not info["tem_verba"]:
            motivo = (
                "Linha da tabela Rubricas sem verba legado; o de_para informa verba(s) para esta rubrica"
                if verbas_a else "Sem verba legado na tabela Rubricas e a rubrica não consta no de_para"
            )
            sem_verba.append(_lin_sem_verba(
                "Tabela Rubricas", info["rubrica"], info["nome"], info["status"],
                ", ".join(info["aplica"]) or None, motivo, info["linha"], None, verbas_a,
            ))
        else:
            for emp in info["aplica"]:
                if not info["verbas_por_empresa"].get(emp):
                    sem_verba.append(_lin_sem_verba(
                        "Tabela Rubricas", info["rubrica"], info["nome"], info["status"],
                        emp, f"Rubrica atende a empresa {emp}, mas sem verba legado informada para ela",
                        info["linha"], None,
                        _juntar([f"{a['empresa']}/{a['verba']}" for a in A_rub.get(info["rubrica"], [])
                                 if a["empresa"] == emp and a["verba"] is not None]),
                    ))
    for a in A:
        if a["verba"] is None and a["rubrica"] is not None:
            sem_verba.append(_lin_sem_verba(
                "de_para", a["rubrica"], a["nome"] or nome_rubrica_por_codigo.get(a["rubrica"]),
                status_de(a["rubrica"]), a["empresa"], "de_para: VERBA_ORIGEM em branco",
                None, a["linha"], None,
            ))
    sem_verba.sort(key=lambda r: (_ordem_chave(r["Rubrica Ergon"]), r["Origem"]))

    # 4) divergências ---------------------------------------------------------
    divergencias = []
    for trio in sorted(set(A_trio) - set(B_trio), key=lambda t: _ordem_chave(*t)):
        emp, verba, rub = trio
        if rub not in codigos_tabela:
            continue  # já em "sem rubrica" (rubrica inexistente na tabela)
        chave = (emp, verba)
        a_regs = A_trio[trio]
        if emp not in empresas_tabela:
            situacao = f"Empresa {emp} não existe na tabela Rubricas (não há como comparar)"
        elif chave in B_chave:
            situacao = "A verba existe nas duas fontes, mas com rubrica Ergon diferente"
        else:
            situacao = "Vínculo só no de_para (a verba não consta na tabela Rubricas para esta empresa)"
        divergencias.append({
            "Origem": "de_para", "Empresa": emp, "Verba legado": verba,
            "Nome da verba": a_regs[0]["nome"], "Rubrica Ergon": rub,
            "Nome da rubrica": nome_rubrica_por_codigo.get(rub),
            "Rubrica(s) na OUTRA fonte (tabela Rubricas)": _juntar(rubricas_de_B(chave)) or None,
            "Situação": situacao,
            "Linha(s) de_para": _juntar([a["linha"] for a in a_regs]),
            "Linha(s) tabela Rubricas": None,
        })
    for trio in sorted(set(B_trio) - set(A_trio), key=lambda t: _ordem_chave(*t)):
        emp, verba, rub = trio
        chave = (emp, verba)
        b_regs = B_trio[trio]
        situacao = (
            "A verba existe nas duas fontes, mas com rubrica Ergon diferente"
            if chave in A_chave and any(a["rubrica"] for a in A_chave[chave])
            else "Vínculo só na tabela Rubricas (a verba não consta no de_para para esta empresa)"
        )
        divergencias.append({
            "Origem": "Tabela Rubricas", "Empresa": emp, "Verba legado": verba,
            "Nome da verba": b_regs[0]["nome_verba"], "Rubrica Ergon": rub,
            "Nome da rubrica": nome_rubrica_por_codigo.get(rub),
            "Rubrica(s) na OUTRA fonte (tabela Rubricas)": _juntar(rubricas_de_A(chave)) or None,
            "Situação": situacao,
            "Linha(s) de_para": None,
            "Linha(s) tabela Rubricas": _juntar([b["linha"] for b in b_regs]),
        })
    # Cabeçalho da coluna "outra fonte" precisa refletir a origem de cada
    # linha — em vez de um título fixo enganoso, usa um título neutro.
    for d in divergencias:
        d["Rubrica(s) na outra fonte"] = d.pop("Rubrica(s) na OUTRA fonte (tabela Rubricas)")
    divergencias.sort(key=lambda r: (_ordem_chave(r["Empresa"], r["Verba legado"], r["Rubrica Ergon"]), r["Origem"]))

    # 5) duplicidades -------------------------------------------------------------
    duplicidades = []
    grupo_n = 0

    def add_grupo(tipo, origem, chave_txt, ocorrencias, observacao=None):
        nonlocal grupo_n
        grupo_n += 1
        total = len(ocorrencias)
        for i, oc in enumerate(ocorrencias, start=1):
            duplicidades.append({
                "Grupo": grupo_n, "Tipo de duplicidade": tipo, "Origem": origem,
                "Chave": chave_txt, "Ocorrência": f"{i}/{total}",
                "Empresa": oc.get("empresa"), "Verba legado": oc.get("verba"),
                "Rubrica Ergon": oc.get("rubrica"), "Nome": oc.get("nome"),
                "Status": oc.get("status"), "Linha": oc.get("linha"),
                "Observação": observacao,
            })

    for trio in sorted((t for t, v in A_trio.items() if len(v) > 1), key=lambda t: _ordem_chave(*t)):
        add_grupo(
            "de_para: linha repetida (mesma Empresa + Verba + Rubrica)", "de_para",
            f"{trio[0]} / verba {trio[1]} / rubrica {trio[2]}",
            [{"empresa": a["empresa"], "verba": a["verba"], "rubrica": a["rubrica"], "nome": a["nome"], "linha": a["linha"]}
             for a in A_trio[trio]],
        )
    for chave in sorted(A_chave, key=lambda t: _ordem_chave(*t)):
        rubs = rubricas_de_A(chave)
        if len(rubs) > 1:
            add_grupo(
                "de_para: mesma Empresa + Verba apontando para rubricas Ergon diferentes", "de_para",
                f"{chave[0]} / verba {chave[1]} -> rubricas {', '.join(rubs)}",
                [{"empresa": a["empresa"], "verba": a["verba"], "rubrica": a["rubrica"], "nome": a["nome"], "linha": a["linha"]}
                 for a in A_chave[chave] if a["rubrica"]],
                "Confirme se a verba foi dividida de propósito entre estas rubricas.",
            )
    # tabela Rubricas: mesmo Código Ergon em mais de uma linha
    linhas_por_rubrica = defaultdict(list)
    for info in linhas_tabela.values():
        if info["rubrica"]:
            linhas_por_rubrica[info["rubrica"]].append(info)
    for rub in sorted((r for r, v in linhas_por_rubrica.items() if len(v) > 1), key=lambda x: _ordem_chave(x)):
        infos = linhas_por_rubrica[rub]
        cont_emp = defaultdict(int)
        for i in infos:
            for e in set(i["aplica"]) | set(i["verbas_por_empresa"]):
                cont_emp[e] += 1
        sobrepostas = sorted(e for e, n in cont_emp.items() if n > 1)
        obs = (
            f"Possível duplicidade real — empresa(s) em mais de uma linha: {', '.join(sobrepostas)}"
            if sobrepostas else "Mesma rubrica em empresas distintas (esperado — ver migration_029)"
        )
        add_grupo(
            "Tabela Rubricas: mesmo Código Ergon em mais de uma linha", "Tabela Rubricas",
            f"rubrica {rub}",
            [{"empresa": ", ".join(sorted(set(i["aplica"]) | set(i["verbas_por_empresa"]))) or None,
              "verba": _juntar([v for vs in i["verbas_por_empresa"].values() for v in vs], 8),
              "rubrica": rub, "nome": i["nome"], "status": i["status"], "linha": i["linha"]}
             for i in infos],
            obs,
        )
    # tabela Rubricas: mesma Empresa + Verba em mais de uma linha
    for chave in sorted(B_chave, key=lambda t: _ordem_chave(*t)):
        linhas_ids = []
        for b in B_chave[chave]:
            if b["linha_id"] not in linhas_ids:
                linhas_ids.append(b["linha_id"])
        if len(linhas_ids) > 1:
            add_grupo(
                "Tabela Rubricas: mesma Empresa + Verba em mais de uma linha", "Tabela Rubricas",
                f"{chave[0]} / verba {chave[1]}",
                [{"empresa": chave[0], "verba": chave[1], "rubrica": linhas_tabela[lid]["rubrica"],
                  "nome": linhas_tabela[lid]["nome"], "status": linhas_tabela[lid]["status"],
                  "linha": linhas_tabela[lid]["linha"]} for lid in linhas_ids],
                (
                    "Mesma verba e MESMA rubrica repetidas em mais de uma linha — possível duplicidade real."
                    if len({linhas_tabela[lid]["rubrica"] for lid in linhas_ids}) == 1
                    else "A verba aparece ligada a rubricas diferentes (divisão por atributo/evento?) — confirme se é intencional."
                ),
            )

    contagens = {
        "depara_linhas_lidas": depara.get("linhas_lidas", len(A)),
        "depara_linhas_ignoradas": depara.get("linhas_ignoradas", 0),
        "depara_registros": len(A),
        "rubricas_linhas": len(linhas_tabela),
        "rubricas_vinculos": len(B),
        "match": len(match),
        "sem_rubrica_ergon": len(sem_rubrica),
        "rubricas_sem_verba": len(sem_verba),
        "divergencias": len(divergencias),
        "duplicidades": len(duplicidades),
        "duplicidades_grupos": grupo_n,
    }
    return {
        "abas": {
            "match": match, "sem_rubrica_ergon": sem_rubrica, "rubricas_sem_verba": sem_verba,
            "duplicidades": duplicidades, "divergencias": divergencias,
        },
        "contagens": contagens, "avisos": avisos,
    }


def _lin_sem_rubrica(origem, empresa, verba, nome, motivo, linha_depara, linha_tabela, sugestao, grupo):
    return {
        "Origem": origem, "Empresa": empresa, "Verba legado": verba, "Nome da verba": nome,
        "Motivo": motivo,
        "Rubrica(s) Ergon na outra fonte / informada": sugestao or None,
        "Grupo (de_para)": grupo,
        "Linha de_para": linha_depara, "Linha tabela Rubricas": linha_tabela,
    }


def _lin_sem_verba(origem, rubrica, nome, status, empresas, motivo, linha_tabela, linha_depara, verbas_depara):
    return {
        "Origem": origem, "Rubrica Ergon": rubrica, "Nome da rubrica": nome, "Status da rubrica": status,
        "Empresa(s)": empresas, "Motivo": motivo,
        "Verba(s) informadas no de_para para esta rubrica": verbas_depara or None,
        "Linha tabela Rubricas": linha_tabela, "Linha de_para": linha_depara,
    }


# --------------------------------------------------------------------------
# Planilha de saída
# --------------------------------------------------------------------------
COR_CABECALHO_HEX = "1A3D5C"
COR_TITULO_RESUMO_HEX = "E8EDF2"

_ABAS = (
    ("1 - Verbas sem Rubrica Ergon", "sem_rubrica_ergon",
     ["Origem", "Empresa", "Verba legado", "Nome da verba", "Motivo",
      "Rubrica(s) Ergon na outra fonte / informada", "Grupo (de_para)", "Linha de_para", "Linha tabela Rubricas"],
     [16, 9, 12, 38, 62, 26, 22, 12, 14]),
    ("2 - Rubricas sem Verba Legado", "rubricas_sem_verba",
     ["Origem", "Rubrica Ergon", "Nome da rubrica", "Status da rubrica", "Empresa(s)", "Motivo",
      "Verba(s) informadas no de_para para esta rubrica", "Linha tabela Rubricas", "Linha de_para"],
     [16, 13, 38, 20, 22, 66, 40, 14, 12]),
    ("3 - Duplicidades", "duplicidades",
     ["Grupo", "Tipo de duplicidade", "Origem", "Chave", "Ocorrência", "Empresa", "Verba legado",
      "Rubrica Ergon", "Nome", "Status", "Linha", "Observação"],
     [8, 58, 16, 46, 11, 18, 18, 13, 36, 20, 8, 60]),
    ("4 - Registros corretos (match)", "match",
     ["Empresa", "Verba legado", "Nome da verba (de_para)", "Rubrica Ergon", "Nome da rubrica (Rubricas)",
      "Status da rubrica", "Grupo (de_para)", "Linha(s) no de_para", "Linha(s) na tabela Rubricas"],
     [9, 12, 38, 13, 38, 20, 24, 16, 18]),
    ("5 - Divergências", "divergencias",
     ["Origem", "Empresa", "Verba legado", "Nome da verba", "Rubrica Ergon", "Nome da rubrica",
      "Rubrica(s) na outra fonte", "Situação", "Linha(s) de_para", "Linha(s) tabela Rubricas"],
     [16, 9, 12, 36, 13, 36, 24, 70, 14, 18]),
)


def _estiliza_cabecalho(ws, n_colunas):
    for col in range(1, n_colunas + 1):
        cel = ws.cell(row=1, column=col)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill(start_color=COR_CABECALHO_HEX, end_color=COR_CABECALHO_HEX, fill_type="solid")
        cel.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"


def gerar_planilha_bytes(resultado, meta=None):
    """`meta`: {"projeto": str, "arquivo_depara": str, "gerado_em": datetime}
    — só pra aba Resumo."""
    meta = meta or {}
    c = resultado["contagens"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    gerado = meta.get("gerado_em") or datetime.now()
    linhas = [
        ("Análise de_para de Verbas × tabela Rubricas", None),
        (None, None),
        ("Projeto", meta.get("projeto") or "—"),
        ("Arquivo de_para analisado", meta.get("arquivo_depara") or "—"),
        ("Gerado em", gerado.strftime("%d/%m/%Y %H:%M")),
        (None, None),
        ("FONTES", None),
        ("de_para — linhas de dados lidas", c["depara_linhas_lidas"]),
        ("de_para — linhas ignoradas (fragmentos de texto)", c["depara_linhas_ignoradas"]),
        ("de_para — registros considerados", c["depara_registros"]),
        ("Tabela Rubricas — linhas (rubricas)", c["rubricas_linhas"]),
        ("Tabela Rubricas — vínculos Empresa + Verba extraídos", c["rubricas_vinculos"]),
        (None, None),
        ("RESULTADO (cada aba é uma lista de pendências, exceto a 4)", None),
        ("1 - Verbas legado sem Rubrica Ergon", c["sem_rubrica_ergon"]),
        ("2 - Rubricas Ergon sem Verba Legado", c["rubricas_sem_verba"]),
        ("3 - Duplicidades (registros / grupos)", f"{c['duplicidades']} / {c['duplicidades_grupos']}"),
        ("4 - Registros corretos (match)", c["match"]),
        ("5 - Divergências (vínculos diferentes entre as fontes)", c["divergencias"]),
        (None, None),
        ("COMO O CRUZAMENTO FOI FEITO", None),
        ("Chave", "Empresa + Verba legado + Rubrica Ergon. Zeros à esquerda são ignorados (00065 = 65)."),
        ("Tabela Rubricas", "As verbas de cada empresa vêm das colunas \"COD Exx\" (várias verbas na mesma célula, uma por linha) e da coluna \"Verba 07\" (empresa E07)."),
        ("Match", "O trio existe nas duas fontes."),
        ("Divergência", "A verba/rubrica existe nas duas fontes, mas o vínculo é diferente — ou só uma das fontes conhece."),
        ("Duplicidades", "São calculadas à parte: um registro pode estar em \"match\" e também aparecer como duplicado."),
    ]
    for rotulo, valor in linhas:
        ws.append([rotulo, valor])
    ws["A1"].font = Font(bold=True, size=14)
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        a = row[0]
        if a.value in ("FONTES", "RESULTADO (cada aba é uma lista de pendências, exceto a 4)", "COMO O CRUZAMENTO FOI FEITO"):
            for cel in row[:2]:
                cel.font = Font(bold=True)
                cel.fill = PatternFill(start_color=COR_TITULO_RESUMO_HEX, end_color=COR_TITULO_RESUMO_HEX, fill_type="solid")
        row[1].alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
    ws.column_dimensions["A"].width = 58
    ws.column_dimensions["B"].width = 100

    for titulo, chave, colunas, larguras in _ABAS:
        aba = wb.create_sheet(titulo[:31])
        aba.append(colunas)
        for item in resultado["abas"][chave]:
            aba.append([item.get(col) for col in colunas])
        _estiliza_cabecalho(aba, len(colunas))
        for i, largura in enumerate(larguras, start=1):
            aba.column_dimensions[get_column_letter(i)].width = largura
        if aba.max_row > 1:
            aba.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{aba.max_row}"

    avisos = resultado.get("avisos") or []
    if avisos:
        aba = wb.create_sheet("Avisos")
        aba.append(["Avisos da leitura dos arquivos"])
        for av in avisos:
            aba.append([av])
        _estiliza_cabecalho(aba, 1)
        aba.column_dimensions["A"].width = 160

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
