"""
Log de auditoria (32ª rodada) — "quem entrou, o que fez".

Duas famílias de evento:

  1. Acesso: login, login_falho, logout — gravados explicitamente pelas
     rotas de autenticação em main.py (não passam por dado nenhum).

  2. Dado: criacao, edicao, exclusao — gravados a partir de UM ÚNICO PONTO
     cada, dentro de insert_row/patch_row/delete_row (main.py), que são as
     3 funções genéricas por onde passa a esmagadora maioria das ~30 rotas
     de criar/editar/excluir do sistema. Isso cobre de uma vez ~16 entidades
     (atividades, requisitos do TR, faturas, riscos, marcos, recursos,
     etapas, frentes de trabalho, tipos de atividade elementar, usuários,
     manuais, calendário de feriados, projetos, ciclos de migração, relatos
     de atividade, dependências) sem precisar instrumentar rota por rota.

Princípio inegociável: uma falha ao GRAVAR o log NUNCA pode quebrar a
operação real do usuário. Toda escrita aqui é best-effort — qualquer
exceção é engolida (com traceback no console do servidor), nunca
repassada pra quem chamou.
"""
import json
import traceback

from flask import request, session

from . import db

# ---------------------------------------------------------------------------
# Rótulos amigáveis — usados tanto na descrição pronta quanto no diff exibido.
# ---------------------------------------------------------------------------

ENTIDADE_ROTULOS_MINUSCULO = {
    # Só as exceções onde .lower() do rótulo normal fica estranho (sigla no
    # meio) — as demais caem no fallback (rotulo.lower()) em _rotulo_minusculo.
    "requisitos_tr": "requisito do TR",
}

ENTIDADE_ROTULOS = {
    "projetos": "Projeto",
    "usuarios": "Usuário",
    "recursos": "Recurso",
    "etapas": "Etapa",
    "frentes_trabalho": "Frente de trabalho",
    "tipos_atividade_elementar": "Tipo de atividade elementar",
    "atividades": "Atividade",
    "atividade_relato": "Relato de atividade",
    "atividade_dependencia": "Dependência entre atividades",
    "requisitos_tr": "Requisito do TR",
    "manuais": "Manual do sistema",
    "faturas": "Fatura",
    "ciclos_migracao": "Ciclo de migração",
    "marcos": "Marco",
    "riscos": "Risco",
    "calendario_util": "Calendário de feriados",
    "cronograma": "Cronograma",
}

# Campo -> rótulo amigável, por tabela. Só precisa cobrir os campos que
# valem a pena mostrar no diff — um campo sem entrada aqui ainda aparece no
# diff, só que com o próprio nome da coluna como rótulo (ver _rotulo_campo).
CAMPO_ROTULOS = {
    "projetos": {
        "sigla": "Sigla", "nome": "Nome", "cliente": "Cliente", "descricao": "Descrição",
        "fiscal_projeto": "Fiscal do projeto", "gestor_projeto": "Gestor do projeto",
        "gerente_projeto_cliente": "Gerente do projeto (cliente)",
        "gerente_projeto_consultoria": "Gerente do projeto (Consultoria)",
        "lider_projeto_consultoria": "Líder do projeto (Consultoria)",
        "data_abertura": "Data de abertura", "data_inicio": "Data de início",
        "data_inicio_real": "Data de início real", "data_fim_prevista": "Data fim prevista",
        "prazo_total_meses": "Prazo total (meses)", "valor_global_contrato": "Valor global do contrato",
        "horas_dia_util": "Horas por dia útil",
    },
    "usuarios": {
        "nome": "Nome", "cargo": "Cargo", "ativo": "Ativo", "perfil": "Perfil",
    },
    "recursos": {
        "nome": "Nome", "tipo_vinculo": "Tipo de vínculo", "empresa": "Empresa", "cargo": "Cargo",
        "email": "E-mail", "telefone": "Telefone", "controla_horas": "Controla horas", "ativo": "Ativo",
    },
    "etapas": {
        "numero": "Número", "nome": "Nome", "descricao": "Descrição",
        "data_inicio_prev": "Início previsto", "data_fim_prev": "Fim previsto",
    },
    "frentes_trabalho": {
        "nome": "Nome", "descricao": "Descrição", "cor_hex": "Cor", "ordem": "Ordem", "ativo": "Ativo",
    },
    "tipos_atividade_elementar": {
        "nome": "Nome", "descricao": "Descrição", "ordem": "Ordem", "ativo": "Ativo",
    },
    "atividades": {
        "etapa_id": "Etapa", "frente_trabalho_id": "Frente de trabalho",
        "tipo_atividade_elementar_id": "Tipo de atividade elementar", "atividade_pai_id": "Atividade pai",
        "requisito_tr_id": "Requisito do TR vinculado", "codigo_wbs": "Código WBS",
        "nome": "Nome", "descricao": "Descrição", "prazo_horas": "Prazo (horas)",
        "horas_realizadas": "Horas realizadas", "dtini_prev": "Início previsto",
        "dtfim_prev": "Fim previsto", "dtini_real": "Início real", "dtfim_real": "Fim real",
        "percentual_concluido": "Percentual concluído", "status": "Status", "prioridade": "Prioridade",
        "observacoes": "Observações", "eh_atividade_master": "É atividade-mestre",
        "eh_entregavel": "É entregável",
    },
    "atividade_relato": {
        "texto": "Relato", "eh_pendencia": "É pendência",
        "pendencia_responsavel_id": "Responsável pela pendência",
        "pendencia_prazo_possivel": "Prazo possível da pendência",
        "pendencia_data_limite": "Data limite da pendência",
    },
    "requisitos_tr": {
        "codigo": "Código", "titulo": "Título", "descricao": "Descrição", "tipo_requisito": "Tipo",
        "modulo_origem": "Módulo de origem", "frente_trabalho_id": "Frente de trabalho",
        "classificacao": "Classificação", "atendimento": "Atendimento", "status": "Status",
        "responsavel_id": "Responsável", "prioridade": "Prioridade", "cobranca": "Cobrança",
        "data_levantamento": "Data de levantamento", "observacoes": "Observações",
        "resposta_oficial": "Resposta oficial",
    },
    "manuais": {"nome": "Nome", "versao": "Versão", "ativo": "Ativo"},
    "faturas": {
        "numero_fatura": "Número da fatura", "data_emissao": "Data de emissão",
        "data_envio": "Data de envio", "data_pagamento_previsao": "Previsão de pagamento",
        "data_pagamento": "Data de pagamento", "descricao": "Descrição",
        "responsavel_entrega_id": "Responsável pela entrega",
        "responsavel_recebimento_recurso_id": "Responsável pelo recebimento",
        "responsavel_recebimento_nome": "Nome de quem recebeu",
        "responsavel_recebimento_cargo": "Cargo de quem recebeu",
        "valor_total": "Valor total", "impostos": "Impostos",
    },
    "marcos": {
        "etapa_id": "Etapa", "nome": "Nome", "descricao": "Descrição",
        "data_prevista": "Data prevista", "data_real": "Data real",
    },
    "riscos": {
        "descricao": "Descrição", "categoria": "Categoria", "probabilidade": "Probabilidade",
        "impacto": "Impacto", "mitigacao": "Mitigação", "responsavel_id": "Responsável",
        "status": "Status", "identificado_em": "Identificado em",
    },
    "calendario_util": {"data": "Data", "descricao": "Descrição"},
    "ciclos_migracao": {
        "numero_ciclo": "Número do ciclo", "data_execucao": "Data de execução",
        "qtd_registros_extraidos": "Registros extraídos", "qtd_registros_carregados": "Registros carregados",
        "qtd_rejeicoes": "Rejeições", "observacoes": "Observações",
    },
}

# Campo usado como "nome amigável" do registro (entidade_rotulo), por tabela.
_CAMPO_ROTULO_REGISTRO = {
    "projetos": "nome", "usuarios": "nome", "recursos": "nome", "etapas": "nome",
    "frentes_trabalho": "nome", "tipos_atividade_elementar": "nome", "atividades": "nome",
    "requisitos_tr": "titulo", "manuais": "nome", "marcos": "nome", "calendario_util": "descricao",
    "ciclos_migracao": "numero_ciclo",
}

# Campos que nunca devem aparecer no diff (irrelevantes ou ruidosos demais).
_CAMPOS_IGNORAR_DIFF = {"atualizado_em", "criado_em", "senha_hash"}


def _rotulo_minusculo(tabela):
    return ENTIDADE_ROTULOS_MINUSCULO.get(tabela) or ENTIDADE_ROTULOS.get(tabela, tabela).lower()


def _rotulo_campo(tabela, campo):
    return (CAMPO_ROTULOS.get(tabela) or {}).get(campo, campo)


def _fmt_valor(v):
    if v is None or v == "":
        return "(vazio)"
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    return str(v)


def _rotulo_registro(tabela, row):
    if not row:
        return None
    campo = _CAMPO_ROTULO_REGISTRO.get(tabela)
    if campo and row.get(campo):
        return str(row[campo])
    if row.get("codigo_wbs"):
        return str(row["codigo_wbs"])
    return None


def _diff_campos(tabela, antes, depois):
    """Compara `antes` (dict ou None, numa criação) com `depois` (dict) e
    retorna (detalhes, descricao) — detalhes é {campo: {rotulo, de, para}},
    descricao já é a frase pronta juntando todos os campos alterados."""
    antes = antes or {}
    mudou = {}
    for campo, valor_novo in depois.items():
        if campo in _CAMPOS_IGNORAR_DIFF or campo == "id":
            continue
        valor_antigo = antes.get(campo)
        if valor_antigo == valor_novo:
            continue
        mudou[campo] = {
            "rotulo": _rotulo_campo(tabela, campo),
            "de": _fmt_valor(valor_antigo),
            "para": _fmt_valor(valor_novo),
        }
    if not mudou:
        return {}, ""
    partes = [f'{v["rotulo"]}: "{v["de"]}" → "{v["para"]}"' for v in mudou.values()]
    return mudou, "; ".join(partes)


def _usuario_atual():
    """(id, nome, email) do usuário logado nesta requisição, ou (None, None,
    None) fora de contexto de requisição (ex.: script batch)."""
    try:
        usuario_id = session.get("usuario_id")
    except RuntimeError:
        return None, None, None
    if not usuario_id:
        return None, None, None
    try:
        row = db.fetch_one(f"SELECT nome, email FROM usuarios WHERE id = {db.q(usuario_id)}")
    except db.DbError:
        row = None
    if not row:
        return usuario_id, None, None
    return usuario_id, row.get("nome"), row.get("email")


def _ip_atual():
    try:
        return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.remote_addr or None)
    except RuntimeError:
        return None


def _projeto_atual(tabela, depois, antes):
    """projeto_id do evento, quando a tabela tiver a coluna — senão None
    (evento global: recurso, tipo de atividade, usuário, manual...)."""
    row = depois or antes or {}
    return row.get("projeto_id")


# Regras de "mudança sensível" — sinalizam eventos que merecem destaque na
# tela de auditoria (filtro "só sensíveis"), mesmo sem serem exclusões.
def _eh_sensivel(tipo_evento, tabela, antes, depois, detalhes):
    if tipo_evento == "exclusao":
        return True
    if tipo_evento == "login_falho":
        return True
    if tipo_evento != "edicao" or not detalhes:
        return False
    if tabela == "atividades":
        # Prazo empurrado (data fim prevista adiada) ou atividade cancelada/bloqueada.
        if "dtfim_prev" in detalhes:
            de, para = (antes or {}).get("dtfim_prev"), (depois or {}).get("dtfim_prev")
            if de and para and str(para) > str(de):
                return True
        if "status" in detalhes and detalhes["status"]["para"] in ("Cancelada", "Bloqueada"):
            return True
    if tabela == "requisitos_tr" and "atendimento" in detalhes:
        return True
    if tabela == "riscos" and ("probabilidade" in detalhes or "impacto" in detalhes):
        de_p = (antes or {}).get("probabilidade")
        de_i = (antes or {}).get("impacto")
        if detalhes.get("probabilidade", {}).get("para") == "Alto" and de_p != "Alto":
            return True
        if detalhes.get("impacto", {}).get("para") == "Alto" and de_i != "Alto":
            return True
    if tabela == "faturas" and "valor_total" in detalhes:
        return True
    if tabela == "usuarios" and "ativo" in detalhes and detalhes["ativo"]["para"] == "Não":
        return True
    return False


def _inserir_log(**campos):
    """Grava uma linha em log_auditoria. Nunca propaga exceção — uma falha
    aqui não pode derrubar a operação real do usuário."""
    try:
        usuario_id, usuario_nome, usuario_email = _usuario_atual()
        cols = {
            "usuario_id": usuario_id,
            "usuario_nome": usuario_nome,
            "usuario_email": usuario_email,
            "ip": _ip_atual(),
        }
        cols.update(campos)
        colunas = [c for c, v in cols.items() if v is not None]
        valores = [db.q(cols[c]) if c != "detalhes" else db.q(json.dumps(cols[c], default=str)) + "::jsonb"
                   for c in colunas]
        # sensivel/detalhes podem ser omitidos (default da tabela cobre); tratados
        # como os demais acima. jsonb precisa do cast explícito quando via texto.
        sql = f"INSERT INTO log_auditoria ({', '.join(colunas)}) VALUES ({', '.join(valores)})"
        db.execute(sql)
    except Exception:
        print("[auditoria] falha ao gravar log (ignorada):", flush=True)
        traceback.print_exc()


# ---------------------------------------------------------------------------
# API pública — chamada pelos 3 chokepoints de dado (main.py) e pelas rotas
# de autenticação.
# ---------------------------------------------------------------------------

def registrar_criacao(tabela, row):
    if not row:
        return
    rotulo_min = _rotulo_minusculo(tabela)
    nome_registro = _rotulo_registro(tabela, row)
    descricao = f"Criou {rotulo_min}" + (f' "{nome_registro}"' if nome_registro else "")
    _inserir_log(
        tipo_evento="criacao", entidade=tabela, entidade_id=str(row.get("id") or ""),
        entidade_rotulo=nome_registro, descricao=descricao,
        projeto_id=_projeto_atual(tabela, row, None), sensivel=False,
    )


def registrar_edicao(tabela, antes, depois):
    if not depois:
        return
    detalhes, descricao_campos = _diff_campos(tabela, antes, depois)
    if not detalhes:
        return  # nada mudou de fato (ex.: PUT reenviando os mesmos valores)
    rotulo_min = _rotulo_minusculo(tabela)
    nome_registro = _rotulo_registro(tabela, depois) or _rotulo_registro(tabela, antes)
    descricao = f'Editou {rotulo_min}' + (f' "{nome_registro}"' if nome_registro else "") + f": {descricao_campos}"
    sensivel = _eh_sensivel("edicao", tabela, antes, depois, detalhes)
    _inserir_log(
        tipo_evento="edicao", entidade=tabela, entidade_id=str(depois.get("id") or ""),
        entidade_rotulo=nome_registro, descricao=descricao, detalhes=detalhes,
        projeto_id=_projeto_atual(tabela, depois, antes), sensivel=sensivel,
    )


def registrar_exclusao(tabela, row):
    if not row:
        return
    rotulo_min = _rotulo_minusculo(tabela)
    nome_registro = _rotulo_registro(tabela, row)
    descricao = f"Excluiu {rotulo_min}" + (f' "{nome_registro}"' if nome_registro else "")
    _inserir_log(
        tipo_evento="exclusao", entidade=tabela, entidade_id=str(row.get("id") or ""),
        entidade_rotulo=nome_registro, descricao=descricao,
        projeto_id=_projeto_atual(tabela, None, row), sensivel=True,
    )


def registrar_evento_manual(tipo_evento, descricao, *, entidade=None, entidade_id=None,
                             entidade_rotulo=None, detalhes=None, projeto_id=None, sensivel=False):
    """Para eventos que não vêm de insert_row/patch_row/delete_row nem de
    login/logout — hoje só a limpeza em massa do cronograma (remover-tudo)."""
    _inserir_log(
        tipo_evento=tipo_evento, entidade=entidade, entidade_id=entidade_id,
        entidade_rotulo=entidade_rotulo, descricao=descricao, detalhes=detalhes,
        projeto_id=projeto_id, sensivel=sensivel,
    )


def registrar_login(usuario_id, nome, email):
    _inserir_log(
        tipo_evento="login", descricao=f'Login de "{nome}"',
        usuario_id=usuario_id, usuario_nome=nome, usuario_email=email, sensivel=False,
    )


def registrar_login_falho(email):
    _inserir_log(
        tipo_evento="login_falho", descricao=f'Tentativa de login com credenciais inválidas ("{email}")',
        usuario_email=email, sensivel=True,
    )


def registrar_logout(usuario_id, nome, email):
    _inserir_log(
        tipo_evento="logout", descricao=f'Logout de "{nome}"',
        usuario_id=usuario_id, usuario_nome=nome, usuario_email=email, sensivel=False,
    )


# ---------------------------------------------------------------------------
# Leitura — tela de auditoria e histórico por registro.
# ---------------------------------------------------------------------------

def _where_filtros(projeto_id=None, tipo_evento=None, entidade=None, usuario_id=None,
                    somente_sensiveis=False, busca=None, inicio=None, fim=None):
    cond = []
    if projeto_id:
        cond.append(f"(projeto_id = {db.q(projeto_id)} OR projeto_id IS NULL)")
    if tipo_evento:
        cond.append(f"tipo_evento = {db.q(tipo_evento)}")
    if entidade:
        cond.append(f"entidade = {db.q(entidade)}")
    if usuario_id:
        cond.append(f"usuario_id = {db.q(usuario_id)}")
    if somente_sensiveis:
        cond.append("sensivel = TRUE")
    if busca:
        termo = db.q(f"%{busca}%")
        cond.append(
            f"(descricao ILIKE {termo} OR entidade_rotulo ILIKE {termo} "
            f"OR usuario_nome ILIKE {termo} OR usuario_email ILIKE {termo})"
        )
    if inicio:
        cond.append(f"criado_em >= {db.q(inicio)}")
    if fim:
        cond.append(f"criado_em < ({db.q(fim)}::date + INTERVAL '1 day')")
    return " WHERE " + " AND ".join(cond) if cond else ""


def listar(pagina=1, tamanho_pagina=50, **filtros):
    where = _where_filtros(**filtros)
    offset = max(pagina - 1, 0) * tamanho_pagina
    return db.fetch_all(
        f"SELECT * FROM log_auditoria{where} ORDER BY criado_em DESC, id DESC "
        f"LIMIT {int(tamanho_pagina)} OFFSET {int(offset)}"
    )


def contar(**filtros):
    where = _where_filtros(**filtros)
    row = db.fetch_one(f"SELECT COUNT(*) AS total FROM log_auditoria{where}")
    return (row or {}).get("total", 0)


def resumo(projeto_id=None, dias=30):
    """KPIs pra tela de auditoria: total de eventos no período, exclusões,
    eventos sensíveis, tentativas de login malsucedidas, usuários distintos."""
    where = _where_filtros(projeto_id=projeto_id)
    conector = " AND " if where else " WHERE "
    where_periodo = where + f"{conector}criado_em >= now() - INTERVAL '{int(dias)} days'"
    row = db.fetch_one(f"""
        SELECT
          COUNT(*) AS total_eventos,
          COUNT(*) FILTER (WHERE tipo_evento = 'exclusao') AS total_exclusoes,
          COUNT(*) FILTER (WHERE sensivel) AS total_sensiveis,
          COUNT(*) FILTER (WHERE tipo_evento = 'login_falho') AS total_logins_falhos,
          COUNT(DISTINCT usuario_id) AS usuarios_ativos
        FROM log_auditoria{where_periodo}
    """)
    return row or {
        "total_eventos": 0, "total_exclusoes": 0, "total_sensiveis": 0,
        "total_logins_falhos": 0, "usuarios_ativos": 0,
    }


def historico_entidade(entidade, entidade_id, limite=200):
    return db.fetch_all(
        f"SELECT * FROM log_auditoria WHERE entidade = {db.q(entidade)} "
        f"AND entidade_id = {db.q(str(entidade_id))} ORDER BY criado_em DESC LIMIT {int(limite)}"
    )


def exportar_csv(**filtros):
    """Retorna (cabecalho, linhas) já formatados pra virar CSV — sem
    limite de paginação (respeita só os filtros), pensado pra download."""
    where = _where_filtros(**filtros)
    linhas = db.fetch_all(f"SELECT * FROM log_auditoria{where} ORDER BY criado_em DESC, id DESC")
    cabecalho = ["Data/Hora", "Usuário", "E-mail", "Evento", "Entidade", "Registro", "Descrição", "Sensível"]
    corpo = [[
        l.get("criado_em"), l.get("usuario_nome") or "", l.get("usuario_email") or "",
        l.get("tipo_evento"), ENTIDADE_ROTULOS.get(l.get("entidade"), l.get("entidade") or ""),
        l.get("entidade_rotulo") or "", l.get("descricao"), "Sim" if l.get("sensivel") else "Não",
    ] for l in linhas]
    return cabecalho, corpo
