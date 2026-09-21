import base64
import csv
import io
import json
import os
import uuid
from datetime import datetime, date, timedelta

from flask import Flask, request, jsonify, send_from_directory, send_file, abort, session

from . import db, cpm, tr_parser, cronograma_import, cronograma_versoes, cronograma_replanejamento, cronograma_edicao_lote, relatorio_executivo, relatorio_pdf, auth, minhas_atividades, relatorio_atividades, manuais, auditoria

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
FRONTEND_DIR = os.environ.get(
    "FRONTEND_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend"),
)

# Chave usada para assinar o cookie de sessão (login). Precisa ser FIXA e
# igual em todos os workers do gunicorn (-w 4) — se cada worker sorteasse
# a própria chave, a sessão de um usuário só funcionaria "às vezes"
# (dependendo de qual worker atendesse a requisição seguinte). Por isso o
# fallback abaixo é um valor fixo (não aleatório) só para não quebrar o
# sistema quando SECRET_KEY não estiver configurada — mas é um valor
# PÚBLICO (está neste código-fonte), então NUNCA deve ser usado em
# produção. Configure SECRET_KEY no .env antes de ir para produção (veja
# .env.example e o README, seção "Login e cadastro de usuários").
_SECRET_KEY_FALLBACK_INSEGURO = "app-troque-esta-chave-no-.env-antes-de-usar-em-producao"
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or _SECRET_KEY_FALLBACK_INSEGURO
if SECRET_KEY == _SECRET_KEY_FALLBACK_INSEGURO:
    print(
        "[SECURITY WARNING] SECRET_KEY não configurada — usando uma chave padrão INSEGURA "
        "(pública neste código-fonte) só para o sistema funcionar. Configure SECRET_KEY no "
        "arquivo .env antes de usar em produção. Veja .env.example.",
        flush=True,
    )

# Rotas de autenticação que continuam acessíveis sem sessão ativa (login,
# cadastro, "esqueci a senha") — todo o resto embaixo de /api/ exige login
# (ver exigir_login() abaixo). O front-end estático (/, /<path>) nunca é
# bloqueado aqui: é só HTML/JS/CSS, sem dado nenhum do projeto — ele mesmo
# decide mostrar a tela de login ou o sistema, com base em /api/auth/me.
AUTH_ROTAS_PUBLICAS = {
    "/api/auth/login", "/api/auth/registrar", "/api/auth/esqueci-senha",
}

PROJETO_FIELDS = [
    "sigla", "nome", "cliente", "descricao", "fiscal_projeto", "gestor_projeto",
    "gerente_projeto_cliente", "gerente_projeto_consultoria", "lider_projeto_consultoria",
    "fiscal_projeto_recurso_id", "gestor_projeto_recurso_id", "gerente_projeto_cliente_recurso_id",
    "gerente_projeto_consultoria_recurso_id", "lider_projeto_consultoria_recurso_id",
    "data_abertura", "data_inicio", "data_inicio_real", "data_fim_prevista",
    "prazo_total_meses", "valor_global_contrato", "horas_dia_util",
]

# Os 5 interlocutores do projeto (36ª rodada, migração 025): cada par
# (campo de texto, campo de id) pode apontar pra um recurso cadastrado, do
# lado esperado (cliente/terceirizado ou Consultoria) — mesmo padrão já
# usado em faturas.responsavel_recebimento_recurso_id, ver preparar_fatura.
PROJETO_INTERLOCUTORES = [
    # (campo_texto, campo_recurso_id, lado_esperado, rótulo)
    ("fiscal_projeto", "fiscal_projeto_recurso_id", "cliente", "Fiscal do projeto"),
    ("gestor_projeto", "gestor_projeto_recurso_id", "cliente", "Gestor do projeto"),
    ("gerente_projeto_cliente", "gerente_projeto_cliente_recurso_id", "cliente", "Gerente de projeto do cliente"),
    ("gerente_projeto_consultoria", "gerente_projeto_consultoria_recurso_id", "consultoria", "Gerente de projeto da Consultoria"),
    ("lider_projeto_consultoria", "lider_projeto_consultoria_recurso_id", "consultoria", "Líder de projeto da Consultoria"),
]

# Parâmetros do site (Configurações > Parâmetros): logo + nome da empresa,
# exibidos no topo do sistema. O tema da página (claro/escuro) NÃO é um
# campo aqui — é preferência pessoal salva no navegador de cada usuário
# (localStorage), não um parâmetro do site. Ver migration_010 e a seção
# "17. PARÂMETROS DO SITE" do schema.sql.
PARAMETROS_FIELDS = ["nome_empresa"]
LOGO_EXTENSOES_PERMITIDAS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
LOGO_TAMANHO_MAXIMO_BYTES = 3 * 1024 * 1024  # 3 MB
LOGO_MIME_POR_EXTENSAO = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".webp": "image/webp", ".gif": "image/gif",
}
# Colunas "leves" de parametros_site — tudo, EXCETO logo_dados (o base64 do
# logo, que pode ter alguns MB). Usadas em toda leitura/escrita que não seja
# especificamente para servir a imagem do logo (GET .../logo), para não
# duplicar esses MB em todo carregamento de página (Configurações e o
# carregarParametros() disparado no boot do front-end, ver frontend/index.html).
PARAMETROS_COLUNAS_LEVES = "id, nome_empresa, logo_arquivo, logo_mime, atualizado_em"
CLASSIFICACAO_TR_VALIDAS = {
    "Essencial/Imediato", "Obrigatório", "Desejável",
    "Customizado Curto", "Customizado Médio", "Customizado Longo",
}
ATENDIMENTO_TR_VALIDOS = {"A analisar", "Nativo", "Parcial", "Customizado", "Não atende"}
STATUS_REQUISITO_VALIDOS = {
    "Não iniciado", "Em levantamento", "Especificado", "Em desenvolvimento",
    "Em homologação", "Entregue", "Aprovado", "Rejeitado",
}
PRIORIDADE_VALIDAS = {"Urgente", "Alta", "Média", "Baixa"}
COBRANCA_VALIDAS = {"Sim", "Não", "N/A"}
TIPO_REQUISITO_VALIDOS = {"Funcional", "Não Funcional"}
TIPO_VINCULO_VALIDOS = {"Consultoria", "Cliente", "Terceirizado"}

ATIVIDADE_FIELDS = [
    "projeto_id", "etapa_id", "frente_trabalho_id", "tipo_atividade_elementar_id", "atividade_pai_id",
    "requisito_tr_id",
    "codigo_wbs", "origem_importacao_id", "nome", "descricao", "objetivo",
    "prazo_horas", "horas_realizadas", "dtini_prev", "dtfim_prev",
    "dtini_real", "dtfim_real", "percentual_concluido", "status", "prioridade", "observacoes",
    "eh_atividade_master", "eh_entregavel",
]
RELATO_FIELDS = ["autor_id", "autor_nome", "texto"]  # eh_pendencia/pendencia_* removidos na 41ª rodada (migração 027) — ver PENDENCIA_FIELDS abaixo
# Status que exigem ao menos um relato de andamento registrado (ver create/update_atividade).
# "Não iniciada" e "Concluída" ficam de fora — todo o resto (inclusive "Cancelada", que também
# merece um relato explicando o motivo) precisa de relato.
STATUS_EXIGE_RELATO = {"Em andamento", "Bloqueada", "Cancelada"}
# As 4 variantes de "atividade concluída" (29ª rodada/migração 020) — descrevem o mesmo
# evento (100% + Fim Real preenchido), diferindo só na classificação automática de
# prazo x esforço calculada por classificar_conclusao(). Qualquer lugar do sistema que
# precisar saber "esta atividade está concluída?" deve checar contra este conjunto, não
# contra o literal "Concluída" sozinho.
STATUS_FAMILIA_CONCLUIDA = {
    "Concluída", "Concluída com atraso", "Concluída com esforço maior", "Concluída com atraso e esforço maior",
}
# Status "terminais"/de exceção que a classificação automática nunca sobrescreve sozinha —
# uma atividade Bloqueada ou Cancelada com 100%+Fim Real preenchidos continua sendo um
# estado contraditório que precisa de correção manual (ver validar_consistencia_conclusao).
STATUS_NAO_AUTOMATIZAR = {"Bloqueada", "Cancelada"}
REQUISITO_FIELDS = [
    "projeto_id", "codigo", "titulo", "descricao", "tipo_requisito", "modulo_origem",
    "frente_trabalho_id", "classificacao", "atendimento",
    "status", "responsavel_id", "prioridade", "cobranca", "data_levantamento", "observacoes",
    "resposta_oficial",
]
# Metadados editáveis de um manual (o arquivo em si tem rota própria de upload —
# ver /api/manuais/<id>/arquivo — porque troca o PDF inteiro, não um campo isolado).
MANUAL_FIELDS = ["nome", "versao", "ativo"]
MANUAL_EXTENSOES_PERMITIDAS = {".pdf"}
# Colunas usadas no upsert em massa (CSV e importação de documento) — sem projeto_id,
# que é sempre passado à parte.
REQUISITO_UPSERT_COLUMNS = [
    "codigo", "titulo", "descricao", "tipo_requisito", "modulo_origem", "frente_trabalho_id",
    "classificacao", "atendimento", "status", "prioridade", "cobranca",
    "data_levantamento", "observacoes",
]
RECURSO_FIELDS = ["nome", "tipo_vinculo", "empresa", "cargo", "email", "telefone", "controla_horas", "ativo"]
ETAPA_FIELDS = ["projeto_id", "numero", "nome", "descricao", "data_inicio_prev", "data_fim_prev"]
FRENTE_FIELDS = ["projeto_id", "nome", "descricao", "cor_hex", "ordem", "ativo"]
# `util` não faz parte dos campos editáveis por aqui (Configurações > Calendário
# de Feriados) de propósito: toda linha criada por essa tela é sempre um feriado
# (exceção não-útil) — o valor default da coluna (FALSE) já cobre isso. Um
# `util=TRUE` cadastrado manualmente no banco não teria efeito nenhum no CPM/
# "Minhas atividades" hoje (sábado/domingo já são excluídos antes de olhar essa
# tabela — ver cpm.py/_business_day_offset e minhas_atividades._dias_uteis_periodo),
# então não vale expor esse valor como opção só pra confundir o usuário.
CALENDARIO_FIELDS = ["projeto_id", "data", "descricao"]
TIPO_ATIVIDADE_FIELDS = ["nome", "descricao", "ordem", "ativo"]
MARCO_FIELDS = ["projeto_id", "etapa_id", "nome", "descricao", "data_prevista", "data_real", "origem_importacao_id"]
RISCO_FIELDS = ["projeto_id", "descricao", "categoria", "probabilidade", "impacto", "mitigacao",
                "responsavel_id", "status", "identificado_em"]
# 41ª rodada (migração 027): cadastro formal de Pendências — ver preparar_pendencia() para as
# regras de validação (responsável de cada lado, vínculos opcionais com atividade/risco) e
# _proximo_codigo_pendencia() para a geração do código sequencial "PND-001".
PENDENCIA_FIELDS = [
    "projeto_id", "codigo", "titulo", "frente_trabalho_id", "atividade_id", "risco_id",
    "descricao", "impactos", "responsavel_consultoria_id", "responsavel_cliente_id",
    "data_identificacao", "data_limite", "data_prevista", "data_real",
    "status", "prioridade", "categoria", "acoes_necessarias", "observacoes",
]
CICLO_FIELDS = ["atividade_id", "numero_ciclo", "data_execucao", "qtd_registros_extraidos",
                "qtd_registros_carregados", "qtd_rejeicoes", "observacoes"]
# valor_liquido NÃO entra aqui — é coluna GENERATED (valor_total - impostos), o
# próprio Postgres calcula, nunca é inserida/atualizada diretamente. nome/cargo
# do responsável pelo recebimento entram aqui porque também podem ser digitados
# livremente (sem recurso vinculado) — ver preparar_fatura(), que sobrescreve
# os dois automaticamente quando responsavel_recebimento_recurso_id é informado.
FATURA_FIELDS = [
    "projeto_id", "atividade_id", "numero_fatura",
    "data_emissao", "data_envio", "data_pagamento_previsao", "data_pagamento", "descricao",
    "responsavel_entrega_id",
    "responsavel_recebimento_recurso_id", "responsavel_recebimento_nome", "responsavel_recebimento_cargo",
    "valor_total", "impostos",
]

# LEFT JOIN em etapas/frentes_trabalho (migração 026/37ª rodada) — etapa_id e
# frente_trabalho_id agora podem ser NULL (importação de cronograma deixa em
# branco quando não identifica um cadastro já existente pelo nome da
# atividade). Um INNER JOIN aqui faria essas atividades somem da listagem
# inteira sem nenhum aviso — com LEFT JOIN elas aparecem normalmente, só com
# etapa_numero/etapa_nome/frente_nome/frente_cor = NULL, para o front-end
# mostrar "sem etapa"/"sem frente" e o usuário completar depois.
ATIVIDADE_SELECT = """
SELECT a.*, e.numero AS etapa_numero, e.nome AS etapa_nome,
       f.nome AS frente_nome, f.cor_hex AS frente_cor,
       t.nome AS tipo_nome,
       rq.codigo AS requisito_tr_codigo, rq.titulo AS requisito_tr_titulo,
       -- Atrasada: fim previsto já venceu (sem concluir/cancelar), OU a atividade
       -- nem começou e o início previsto já passou (atraso mais cedo no
       -- cronograma, que antes da 29ª rodada não aparecia até o fim também vencer).
       (a.status NOT IN ('Concluída','Concluída com atraso','Concluída com esforço maior',
                          'Concluída com atraso e esforço maior','Cancelada')
        AND (
          (a.dtfim_prev IS NOT NULL AND a.dtfim_prev < CURRENT_DATE)
          OR (a.status = 'Não iniciada' AND a.dtini_prev IS NOT NULL AND a.dtini_prev < CURRENT_DATE)
        )) AS atrasada,
       -- Alerta antecipado de esforço: atividade ainda em andamento (não fechou em
       -- 100%) cujas horas já realizadas já ultrapassaram as horas previstas — sinal
       -- diferente de "atrasada" (que é sobre data), útil pro gerente perceber o
       -- estouro de esforço antes mesmo da atividade terminar (ver 28ª rodada).
       (a.status = 'Em andamento' AND a.horas_realizadas IS NOT NULL AND a.prazo_horas IS NOT NULL
        AND a.horas_realizadas > a.prazo_horas) AS esforco_estourado,
       -- Lista completa de recursos/participantes (N, não mais 2 campos fixos —
       -- ver tabela atividade_recurso e migração 012), já pronta pro front-end sem
       -- round-trip extra: cada item {id, nome, tipo_vinculo}.
       COALESCE((
         SELECT json_agg(json_build_object('id', r.id, 'nome', r.nome, 'tipo_vinculo', r.tipo_vinculo,
                                            'horas_alocadas', ar.horas_alocadas)
                          ORDER BY r.tipo_vinculo, r.nome)
         FROM atividade_recurso ar JOIN recursos r ON r.id = ar.recurso_id
         WHERE ar.atividade_id = a.id
       ), '[]') AS responsaveis
FROM atividades a
LEFT JOIN etapas e ON e.id = a.etapa_id
LEFT JOIN frentes_trabalho f ON f.id = a.frente_trabalho_id
LEFT JOIN tipos_atividade_elementar t ON t.id = a.tipo_atividade_elementar_id
LEFT JOIN requisitos_tr rq ON rq.id = a.requisito_tr_id
"""

FATURA_SELECT = """
SELECT f.*,
       p.nome AS projeto_nome, p.sigla AS projeto_sigla,
       a.nome AS atividade_nome, a.codigo_wbs AS atividade_codigo_wbs,
       re.nome AS responsavel_entrega_nome,
       (f.data_pagamento IS NOT NULL) AS paga,
       (f.data_pagamento IS NULL AND f.data_pagamento_previsao IS NOT NULL
        AND f.data_pagamento_previsao < CURRENT_DATE) AS atrasada
FROM faturas f
JOIN projetos p ON p.id = f.projeto_id
JOIN atividades a ON a.id = f.atividade_id
JOIN recursos re ON re.id = f.responsavel_entrega_id
"""


def upsert_requisitos(projeto_id, linhas):
    """linhas: lista de dicts com chaves de REQUISITO_UPSERT_COLUMNS (ausentes -> NULL).
    Faz upsert por (projeto_id, codigo) — reimportar atualiza em vez de duplicar.
    Deduplica códigos repetidos dentro do próprio lote (mantém a última ocorrência,
    senão o ON CONFLICT do Postgres quebra com "cannot affect row a second time").
    Retorna (inseridos, atualizados)."""
    por_codigo = {}
    for l in linhas:
        if l.get("codigo"):
            por_codigo[l["codigo"]] = l
    linhas = list(por_codigo.values())
    if not linhas:
        return 0, 0

    existentes = {
        r["codigo"] for r in db.fetch_all(
            f"SELECT codigo FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)}"
        )
    }
    inseridos = sum(1 for l in linhas if l["codigo"] not in existentes)
    atualizados = len(linhas) - inseridos

    values_sql = ", ".join(
        "(" + db.q(projeto_id) + ", " + ", ".join(db.q(l.get(col)) for col in REQUISITO_UPSERT_COLUMNS) + ")"
        for l in linhas
    )
    set_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in REQUISITO_UPSERT_COLUMNS if col != "codigo")
    sql = (
        f"INSERT INTO requisitos_tr (projeto_id, {', '.join(REQUISITO_UPSERT_COLUMNS)}) VALUES "
        + values_sql
        + f" ON CONFLICT (projeto_id, codigo) DO UPDATE SET {set_clause}"
    )
    db.execute(sql, timeout=120)
    return inseridos, atualizados


def preparar_fatura(data, projeto_id):
    """Valida e normaliza (in place) os dados de uma fatura antes de
    inserir/atualizar — usada por create_fatura/update_fatura. Levanta
    ValueError com uma mensagem amigável quando a regra de negócio não é
    atendida (o chamador converte em 400).

    Regras (26ª/27ª rodadas, pedidas explicitamente pelo usuário):
    - a atividade vinculada precisa pertencer ao mesmo projeto da fatura e
      estar marcada como entregável (atividades.eh_entregavel = true);
    - o responsável pela entrega precisa ser um recurso Consultoria;
    - o responsável pelo recebimento pode ser um recurso cadastrado (nesse
      caso nome/cargo são copiados automaticamente do cadastro, sempre —
      qualquer nome/cargo enviado pelo front-end é ignorado) ou, na
      ausência de um recurso vinculado, um nome digitado livremente (usado
      pra gente do cliente sem cadastro no sistema); um dos dois é sempre
      obrigatório.
    """
    if data.get("atividade_id"):
        ativ = db.fetch_one(
            f"SELECT projeto_id, eh_entregavel FROM atividades WHERE id = {db.q(data['atividade_id'])}"
        )
        if not ativ:
            raise ValueError("Atividade não encontrada.")
        if ativ["projeto_id"] != projeto_id:
            raise ValueError("A atividade informada não pertence a este projeto.")
        if not ativ["eh_entregavel"]:
            raise ValueError('Só é possível vincular a fatura a uma atividade marcada como "Entregável".')

    if data.get("responsavel_entrega_id"):
        rec = db.fetch_one(
            f"SELECT tipo_vinculo FROM recursos WHERE id = {db.q(data['responsavel_entrega_id'])}"
        )
        if not rec:
            raise ValueError("Responsável pela entrega não encontrado.")
        if rec["tipo_vinculo"] != "Consultoria":
            raise ValueError("O responsável pela entrega precisa ser um recurso Consultoria.")

    recurso_receb_id = data.get("responsavel_recebimento_recurso_id")
    if recurso_receb_id:
        rec = db.fetch_one(f"SELECT nome, cargo FROM recursos WHERE id = {db.q(recurso_receb_id)}")
        if not rec:
            raise ValueError("Responsável pelo recebimento (recurso) não encontrado.")
        data["responsavel_recebimento_nome"] = rec["nome"]
        data["responsavel_recebimento_cargo"] = rec["cargo"] or ""
    else:
        data["responsavel_recebimento_recurso_id"] = None
        if not (data.get("responsavel_recebimento_nome") or "").strip():
            raise ValueError(
                "Informe o responsável pelo recebimento: um recurso cadastrado ou o nome de uma pessoa do cliente."
            )


def _proximo_codigo_pendencia(projeto_id):
    """Código sequencial por projeto, formato "PND-001" — não há rota de
    exclusão de pendência (mesmo padrão de riscos/marcos/requisitos, usa-se
    status 'Cancelada' em vez de apagar), então contar linhas existentes é
    seguro (nunca reaproveita um número já usado)."""
    n = db.fetch_one(f"SELECT COUNT(*) AS n FROM pendencias WHERE projeto_id = {db.q(projeto_id)}")["n"]
    return f"PND-{int(n) + 1:03d}"


def preparar_pendencia(data, projeto_id):
    """Valida e normaliza (in place) os dados de uma pendência antes de
    inserir/atualizar — usada por create_pendencia/update_pendencia. Levanta
    ValueError com mensagem amigável quando a regra não é atendida (o
    chamador converte em 400). Mesmo padrão de preparar_fatura/
    preparar_projeto_interlocutores acima.

    Regras (41ª rodada, pedidas explicitamente pelo usuário):
    - frente de trabalho é obrigatória e precisa pertencer a este projeto;
    - atividade e risco são OPCIONAIS, mas quando informados precisam
      pertencer a este mesmo projeto;
    - responsável pela consultoria precisa ser um recurso tipo_vinculo
      'Consultoria'; responsável pelo cliente precisa ser um recurso
      tipo_vinculo diferente de 'Consultoria' (Cliente/Terceirizado) — os
      dois são sempre obrigatórios, sem opção de nome livre (o usuário foi
      explícito: "tem que ser recurso do projeto").
    """
    if "frente_trabalho_id" in data:
        frente_id = data.get("frente_trabalho_id")
        if not frente_id:
            raise ValueError("Informe a frente de trabalho.")
        frente = db.fetch_one(f"SELECT projeto_id FROM frentes_trabalho WHERE id = {db.q(frente_id)}")
        if not frente:
            raise ValueError("Frente de trabalho não encontrada.")
        if frente["projeto_id"] != projeto_id:
            raise ValueError("A frente de trabalho informada não pertence a este projeto.")

    if data.get("atividade_id"):
        ativ = db.fetch_one(f"SELECT projeto_id FROM atividades WHERE id = {db.q(data['atividade_id'])}")
        if not ativ:
            raise ValueError("Atividade não encontrada.")
        if ativ["projeto_id"] != projeto_id:
            raise ValueError("A atividade informada não pertence a este projeto.")

    if data.get("risco_id"):
        risco = db.fetch_one(f"SELECT projeto_id FROM riscos WHERE id = {db.q(data['risco_id'])}")
        if not risco:
            raise ValueError("Risco não encontrado.")
        if risco["projeto_id"] != projeto_id:
            raise ValueError("O risco informado não pertence a este projeto.")

    if "responsavel_consultoria_id" in data:
        rec_id = data.get("responsavel_consultoria_id")
        if not rec_id:
            raise ValueError("Informe o responsável da consultoria.")
        rec = db.fetch_one(f"SELECT tipo_vinculo FROM recursos WHERE id = {db.q(rec_id)}")
        if not rec:
            raise ValueError("Responsável da consultoria não encontrado.")
        if rec["tipo_vinculo"] != "Consultoria":
            raise ValueError("O responsável da consultoria precisa ser um recurso Consultoria.")

    if "responsavel_cliente_id" in data:
        rec_id = data.get("responsavel_cliente_id")
        if not rec_id:
            raise ValueError("Informe o responsável do cliente.")
        rec = db.fetch_one(f"SELECT tipo_vinculo FROM recursos WHERE id = {db.q(rec_id)}")
        if not rec:
            raise ValueError("Responsável do cliente não encontrado.")
        if rec["tipo_vinculo"] == "Consultoria":
            raise ValueError("O responsável do cliente precisa ser um recurso do cliente ou terceirizado (não Consultoria).")


def preparar_projeto_interlocutores(data):
    """Valida e normaliza (in place) os 5 campos de interlocutores do
    projeto que podem apontar pra um recurso cadastrado (36ª rodada,
    migração 025 — mesmo padrão de preparar_fatura acima): quando um
    *_recurso_id é informado, o nome (campo de texto) é sempre
    sobrescrito com o nome cadastrado no recurso — qualquer texto enviado
    pelo front-end nesse caso é ignorado — e o recurso precisa ser do lado
    certo (Cliente/Terceirizado para Fiscal/Gestor/Gerente do cliente,
    Consultoria para Gerente/Líder da Consultoria). Sem *_recurso_id, o
    campo de texto continua livre (nome de alguém ainda sem cadastro),
    exatamente como já funcionava antes desta rodada — nenhum destes 5
    campos é obrigatório."""
    for campo_nome, campo_id, lado, rotulo in PROJETO_INTERLOCUTORES:
        if campo_id not in data:
            continue
        recurso_id = data.get(campo_id)
        if recurso_id:
            rec = db.fetch_one(f"SELECT nome, tipo_vinculo FROM recursos WHERE id = {db.q(recurso_id)}")
            if not rec:
                raise ValueError(f"{rotulo}: recurso não encontrado.")
            eh_consultoria = rec["tipo_vinculo"] == "Consultoria"
            if lado == "consultoria" and not eh_consultoria:
                raise ValueError(f"{rotulo}: precisa ser um recurso da Consultoria.")
            if lado == "cliente" and eh_consultoria:
                raise ValueError(f"{rotulo}: precisa ser um recurso do cliente ou terceirizado (não Consultoria).")
            data[campo_nome] = rec["nome"]
        else:
            data[campo_id] = None


def insert_row(table, data, allowed):
    cols, vals = [], []
    for f in allowed:
        if f in data and data[f] not in (None, ""):
            cols.append(f)
            vals.append(db.q(data[f]))
    if not cols:
        raise ValueError("Nenhum campo válido informado.")
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)}) RETURNING *"
    row = db.execute_returning_one(sql)
    # Gancho de auditoria (32ª rodada) — ver auditoria.py. Ponto único: cobre
    # toda criação que passa por aqui, sem precisar tocar em cada rota.
    auditoria.registrar_criacao(table, row)
    return row


def patch_row(table, row_id, data, allowed, prelude=""):
    sets = [f"{f} = {db.q(data[f])}" for f in allowed if f in data]
    if not sets:
        return db.fetch_one(f"SELECT * FROM {table} WHERE id = {db.q(row_id)}")
    # Estado ANTES, pra diff do log de auditoria (ver auditoria.py) — um SELECT
    # a mais por edição, custo desprezível perto do ganho de rastreabilidade.
    antes = db.fetch_one(f"SELECT * FROM {table} WHERE id = {db.q(row_id)}")
    sql = f"UPDATE {table} SET {', '.join(sets)} WHERE id = {db.q(row_id)} RETURNING *"
    row = db.execute_returning_one(sql, prelude=prelude)
    auditoria.registrar_edicao(table, antes, row)
    return row


def delete_row(table, row_id):
    """DELETE genérico. Erros de FK (registro em uso por outra tabela) e de
    unicidade são traduzidos em mensagens amigáveis pelo errorhandler global
    (ver handle_db_error), então aqui só dispara o DELETE mesmo."""
    antes = db.fetch_one(f"SELECT * FROM {table} WHERE id = {db.q(row_id)}")
    db.execute(f"DELETE FROM {table} WHERE id = {db.q(row_id)}")
    auditoria.registrar_exclusao(table, antes)
    return "", 204


def _valor_mesclado(atual, data, campo):
    """Mesmo critério de patch_row: só um campo PRESENTE em `data` sobrescreve o que já
    está em `atual` (a linha gravada no banco, ou None numa criação) — usado por toda
    função de validação/classificação abaixo pra sempre calcular em cima do estado
    RESULTANTE da gravação, nunca só do payload recebido isolado."""
    return data[campo] if campo in data else (atual or {}).get(campo)


def classificar_conclusao(atual, data):
    """Calcula sozinha qual das 4 variantes de 'Concluída' vale, a partir de prazo
    (Fim Real x Fim Previsto) e esforço (Horas Realizadas x Horas Previstas), e
    SOBRESCREVE data['status'] com o resultado — automação pedida pelo usuário na 28ª/
    29ª rodada ("o % de completude de uma atividade pode ser automatizado"), estendida
    aqui para a classificação completa.

    Só age quando o estado RESULTANTE da gravação (mesclando `data` com `atual`) já diz
    "isto está pronto" — 100% concluído E Fim Real preenchido — e o Status resultante não
    é um dos dois status de exceção (Bloqueada/Cancelada, ver STATUS_NAO_AUTOMATIZAR),
    que a automação nunca sobrescreve sozinha (uma atividade cancelada com 100%/Fim Real
    continua sendo um estado contraditório — ver validar_consistencia_conclusao). Fora
    disso, não faz nada: não é papel desta função decidir SE a atividade terminou, só
    QUAL rótulo de conclusão usar quando ela já terminou.

    Sem prazo previsto (dtfim_prev) ou sem prazo de horas (prazo_horas)/horas
    realizadas cadastrados, a falta de dado nunca é tratada como "estourou" — o
    benefício da dúvida fica com a atividade (ex: uma atividade sem prazo_horas
    cadastrado não pode logicamente ter "esforço maior que o previsto")."""
    percentual = _valor_mesclado(atual, data, "percentual_concluido")
    percentual = int(percentual) if percentual not in (None, "") else 0
    dtfim_real = _valor_mesclado(atual, data, "dtfim_real")
    status_resultante = _valor_mesclado(atual, data, "status")

    if percentual != 100 or not dtfim_real:
        return
    if status_resultante in STATUS_NAO_AUTOMATIZAR:
        return

    dtfim_prev = _valor_mesclado(atual, data, "dtfim_prev")
    prazo_horas = _valor_mesclado(atual, data, "prazo_horas")
    horas_realizadas = _valor_mesclado(atual, data, "horas_realizadas")

    atrasada = bool(dtfim_prev and dtfim_real > dtfim_prev)
    esforco_maior = bool(
        prazo_horas not in (None, "") and horas_realizadas not in (None, "")
        and float(horas_realizadas) > float(prazo_horas)
    )

    if atrasada and esforco_maior:
        data["status"] = "Concluída com atraso e esforço maior"
    elif atrasada:
        data["status"] = "Concluída com atraso"
    elif esforco_maior:
        data["status"] = "Concluída com esforço maior"
    else:
        data["status"] = "Concluída"


def validar_consistencia_conclusao(atual, data):
    """Impede gravar uma atividade num estado contraditório entre Status (numa das 4
    variantes de Concluída), percentual_concluido=100 e Fim Real preenchido — os três
    precisam andar sempre juntos. Sem essa checagem dá pra marcar 100% deixando o
    Status em "Não iniciada" (foi exatamente o que aconteceu num teste do usuário na
    28ª rodada), o que quebra a projeção de prazo do card "Atividades master" do
    Dashboard/Relatório Executivo — ela só reconhece uma atividade como concluída pelo
    campo Status, então ficava reprojetando data (e mostrando atraso) pra uma atividade
    que já estava pronta.

    Chamada DEPOIS de classificar_conclusao() nas rotas — a essa altura, qualquer
    atividade que devesse ter o Status corrigido automaticamente já foi corrigida; o
    que sobra pra esta função pegar são os casos que a automação deliberadamente não
    mexe: um Status de conclusão escolhido manualmente sem os dados baterem (ex:
    "Concluída com atraso" com percentual ainda em 80%), ou Bloqueada/Cancelada
    coexistindo com 100%+Fim Real (que precisam de correção manual e explícita).

    `atual` é a linha já gravada no banco (None numa criação); `data` é o payload
    recebido — só os campos presentes em `data` sobrescrevem o valor de `atual` pra
    fins desta checagem, exatamente como patch_row faz na gravação de verdade."""
    status = _valor_mesclado(atual, data, "status")
    percentual = _valor_mesclado(atual, data, "percentual_concluido")
    percentual = int(percentual) if percentual not in (None, "") else 0
    dtfim_real = _valor_mesclado(atual, data, "dtfim_real")
    completo = percentual == 100 and bool(dtfim_real)

    if status in STATUS_FAMILIA_CONCLUIDA and not completo:
        faltando = []
        if percentual != 100:
            faltando.append("o % concluído em 100")
        if not dtfim_real:
            faltando.append("a Data de Fim Real")
        raise ValueError(f'Para marcar a atividade como "{status}", preencha também {" e ".join(faltando)}.')

    if percentual == 100 and not dtfim_real and status not in STATUS_FAMILIA_CONCLUIDA:
        # classificar_conclusao() só reclassifica quando JÁ tem Fim Real — 100% sem Fim
        # Real (em qualquer outro Status) continua sendo um estado incompleto: falta
        # dizer QUANDO a atividade terminou. Mantém o mesmo aviso da 28ª rodada.
        raise ValueError(
            "Uma atividade com 100% concluído precisa também ter a Data de Fim Real preenchida "
            "(o Status de conclusão certo é calculado automaticamente a partir daí)."
        )

    if completo and status in STATUS_NAO_AUTOMATIZAR:
        raise ValueError(
            f'Uma atividade com 100% concluído e Data de Fim Real preenchida não pode ficar '
            f'com o Status "{status}". Escolha um status de conclusão, ou corrija o percentual/'
            f'a Data de Fim Real se a atividade não estiver pronta de verdade.'
        )


def aplicar_percentual_inicial(atual, data):
    """Sugestão automática de % concluído ao registrar que a atividade começou de
    verdade: quando a Data de Início Real está sendo preenchida agora pela primeira
    vez (estava vazia em `atual`) e o usuário não digitou, neste mesmo salvamento, um
    percentual diferente do que já estava gravado (ou 0, numa atividade nova), o
    percentual sobe sozinho pra 20% — só um ponto de partida, continua editável a
    qualquer momento depois. Nunca sobrescreve um valor que o usuário tenha digitado
    deliberadamente junto com essa mesma data. Só dispara na transição "sem início
    real" -> "com início real"; preencher/alterar a data de novo depois não reaplica."""
    dtini_real_novo = data.get("dtini_real")
    dtini_real_antigo = (atual or {}).get("dtini_real")
    if not dtini_real_novo or dtini_real_antigo:
        return

    percentual_antigo = int((atual or {}).get("percentual_concluido") or 0)
    percentual_novo = data.get("percentual_concluido")
    percentual_novo = int(percentual_novo) if percentual_novo not in (None, "") else percentual_antigo
    if percentual_novo == percentual_antigo and percentual_antigo == 0:
        data["percentual_concluido"] = 20


def create_app():
    app = Flask(__name__, static_folder=None)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    app.config.update(
        SECRET_KEY=SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Secure = cookie só é enviado em HTTPS. Precisa ficar DESLIGADO em
        # dev local (docker-compose serve em http://localhost, sem HTTPS —
        # com Secure=True o navegador simplesmente descartaria o cookie e o
        # login pareceria não "colar"). Em produção atrás de HTTPS (Render,
        # ou qualquer proxy com TLS), defina SESSION_COOKIE_SECURE=true nas
        # variáveis de ambiente do serviço.
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )

    @app.before_request
    def exigir_login():
        """Todo /api/* exige sessão autenticada, exceto as rotas em
        AUTH_ROTAS_PUBLICAS (login, cadastro, esqueci-senha) e /api/health.
        O front-end estático (servido lá embaixo, fora de /api/) nunca
        passa por aqui — ele carrega sempre e é o próprio JS que decide
        mostrar a tela de login ou o sistema, chamando /api/auth/me."""
        path = request.path
        if not path.startswith("/api/"):
            return None
        if path == "/api/health" or path in AUTH_ROTAS_PUBLICAS:
            return None
        if not session.get("usuario_id"):
            return jsonify({"erro": "Sessão expirada ou não autenticada. Faça login novamente."}), 401
        return None

    # -------------------------------------------------------------- health
    @app.get("/api/health")
    def health():
        try:
            db.fetch_one("SELECT 1 AS ok")
            return jsonify({"status": "ok"})
        except db.DbError as e:
            return jsonify({"status": "error", "detail": str(e)}), 500

    # --------------------------------------------------- autenticação / usuários
    @app.post("/api/auth/registrar")
    def auth_registrar():
        """Autocadastro aberto — 'primeiro acesso'. Qualquer pessoa preenche
        todos os campos (obrigatórios) e já sai logada, com a senha que ela
        mesma digitou (não é gerada pelo sistema)."""
        data = request.get_json(force=True)
        try:
            usuario = auth.registrar_usuario(data)
        except auth.AuthError as e:
            return jsonify({"erro": str(e)}), 400
        session.clear()
        session.permanent = True
        session["usuario_id"] = usuario["id"]
        auditoria.registrar_criacao("usuarios", usuario)
        auditoria.registrar_login(usuario["id"], usuario.get("nome"), usuario.get("email"))
        return jsonify(usuario), 201

    @app.post("/api/auth/login")
    def auth_login():
        data = request.get_json(force=True)
        try:
            usuario = auth.autenticar(data.get("email"), data.get("senha"))
        except auth.AuthError as e:
            auditoria.registrar_login_falho(data.get("email"))
            return jsonify({"erro": str(e)}), 401
        session.clear()
        session.permanent = True
        session["usuario_id"] = usuario["id"]
        auditoria.registrar_login(usuario["id"], usuario.get("nome"), usuario.get("email"))
        return jsonify(usuario)

    @app.post("/api/auth/logout")
    def auth_logout():
        usuario_id = session.get("usuario_id")
        if usuario_id:
            usuario = auth.buscar_usuario_publico(usuario_id)
            if usuario:
                auditoria.registrar_logout(usuario_id, usuario.get("nome"), usuario.get("email"))
        session.clear()
        return "", 204

    @app.get("/api/auth/me")
    def auth_me():
        usuario_id = session.get("usuario_id")
        if not usuario_id:
            return jsonify({"erro": "Não autenticado."}), 401
        usuario = auth.buscar_usuario_publico(usuario_id)
        if not usuario or not usuario.get("ativo", True):
            session.clear()
            return jsonify({"erro": "Não autenticado."}), 401
        return jsonify(usuario)

    @app.post("/api/auth/esqueci-senha")
    def auth_esqueci_senha():
        email = (request.get_json(force=True) or {}).get("email")
        if not (email or "").strip():
            return jsonify({"erro": "Informe o e-mail cadastrado."}), 400
        try:
            auth.esqueci_a_senha(email)
        except auth.AuthError as e:
            # Only reaches here for a real problem sending the e-mail (SMTP não
            # configurado, credenciais erradas etc.) — precisa aparecer pra quem
            # administra perceber e corrigir. Se o e-mail simplesmente não
            # existir no cadastro, esqueci_a_senha() não levanta nada (ver
            # docstring em auth.py) e cai na mensagem genérica de sucesso abaixo,
            # de propósito, pra não revelar quais e-mails estão cadastrados.
            return jsonify({"erro": str(e)}), 400
        return jsonify({
            "ok": True,
            "mensagem": "Se este e-mail estiver cadastrado, uma nova senha foi enviada para ele.",
        })

    @app.post("/api/auth/trocar-senha")
    def auth_trocar_senha():
        data = request.get_json(force=True)
        try:
            auth.trocar_senha(session["usuario_id"], data.get("senha_atual"), data.get("senha_nova"))
        except auth.AuthError as e:
            return jsonify({"erro": str(e)}), 400
        return jsonify({"ok": True})

    @app.get("/api/usuarios")
    def list_usuarios():
        return jsonify(db.fetch_all(f"SELECT {auth.USUARIO_COLUNAS_PUBLICAS} FROM usuarios ORDER BY nome"))

    @app.get("/api/usuarios/<id>")
    def get_usuario(id):
        row = auth.buscar_usuario_publico(id)
        if not row:
            abort(404)
        return jsonify(row)

    @app.put("/api/usuarios/<id>")
    def update_usuario(id):
        """Edição pela tela Configurações > Usuários. Propositalmente não
        aceita e-mail nem senha aqui (ver auth.USUARIO_EDIT_FIELDS) — trocar
        de e-mail não é suportado nesta rodada, e senha só muda por
        'Alterar senha' (logado) ou 'Esqueci a senha' (por e-mail)."""
        row = patch_row("usuarios", id, request.get_json(force=True), auth.USUARIO_EDIT_FIELDS)
        if not row:
            abort(404)
        row.pop("senha_hash", None)
        return jsonify(row)

    # ------------------------------------------------------------ projetos
    @app.get("/api/projetos")
    def list_projetos():
        return jsonify(db.fetch_all("SELECT * FROM projetos ORDER BY criado_em"))

    @app.post("/api/projetos")
    def create_projeto():
        data = request.get_json(force=True)
        if not (data.get("nome") and data.get("cliente")):
            return jsonify({"erro": "Nome do projeto e Instituição são obrigatórios."}), 400
        try:
            preparar_projeto_interlocutores(data)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        return jsonify(insert_row("projetos", data, PROJETO_FIELDS)), 201

    @app.put("/api/projetos/<id>")
    def update_projeto(id):
        data = request.get_json(force=True)
        try:
            preparar_projeto_interlocutores(data)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        row = patch_row("projetos", id, data, PROJETO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    # ------------------------------------------------- parâmetros do site
    @app.get("/api/parametros")
    def get_parametros():
        """Singleton (mesma convenção de /api/projetos): lê a primeira
        linha e cria uma linha padrão automaticamente se ainda não existir
        nenhuma — assim o front-end nunca precisa lidar com "ainda não
        tem parâmetros cadastrados"."""
        row = db.fetch_one(f"SELECT {PARAMETROS_COLUNAS_LEVES} FROM parametros_site ORDER BY atualizado_em LIMIT 1")
        if not row:
            row = db.execute_returning_one(
                f"INSERT INTO parametros_site DEFAULT VALUES RETURNING {PARAMETROS_COLUNAS_LEVES}"
            )
        return jsonify(row)

    @app.put("/api/parametros/<id>")
    def update_parametros(id):
        data = request.get_json(force=True)
        sets = [f"{f} = {db.q(data[f])}" for f in PARAMETROS_FIELDS if f in data]
        sets.append("atualizado_em = now()")
        sql = (
            f"UPDATE parametros_site SET {', '.join(sets)} WHERE id = {db.q(id)} "
            f"RETURNING {PARAMETROS_COLUNAS_LEVES}"
        )
        row = db.execute_returning_one(sql)
        if not row:
            abort(404)
        return jsonify(row)

    @app.post("/api/parametros/<id>/logo")
    def upload_logo_parametros(id):
        """O conteúdo do logo é gravado em base64 direto no banco (coluna
        logo_dados), não em disco. Disco local não é confiável nos deploys
        deste sistema (ex: Render sem disco persistente) — é apagado a cada
        redeploy e reiniciado, então um logo salvo lá "some" pouco depois de
        enviado, mesmo continuando a existir a referência no banco. Ver
        comentário da coluna em db/schema.sql (seção 17)."""
        existe = db.fetch_one(f"SELECT id FROM parametros_site WHERE id = {db.q(id)}")
        if not existe:
            abort(404)
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"erro": "Selecione um arquivo de imagem."}), 400
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in LOGO_EXTENSOES_PERMITIDAS:
            return jsonify({"erro": "Formato não suportado. Use PNG, JPG, SVG, WEBP ou GIF."}), 400
        conteudo = file.read()
        if len(conteudo) > LOGO_TAMANHO_MAXIMO_BYTES:
            return jsonify({"erro": "Arquivo muito grande (máximo 3 MB)."}), 400
        stored_name = f"logo_{uuid.uuid4()}{ext}"
        mime = LOGO_MIME_POR_EXTENSAO.get(ext, file.mimetype or "application/octet-stream")
        dados_b64 = base64.b64encode(conteudo).decode("ascii")
        sql = (
            f"UPDATE parametros_site SET logo_arquivo = {db.q(stored_name)}, "
            f"logo_dados = {db.q(dados_b64)}, logo_mime = {db.q(mime)}, atualizado_em = now() "
            f"WHERE id = {db.q(id)} RETURNING {PARAMETROS_COLUNAS_LEVES}"
        )
        novo = db.execute_returning_one(sql)
        return jsonify(novo)

    @app.get("/api/parametros/<id>/logo")
    def get_logo_parametros(id):
        """Serve o logo a partir do base64 gravado no banco (logo_dados) —
        ver comentário em upload_logo_parametros sobre por que não fica em
        disco. O nome do arquivo nunca vem da URL (só o id dos parâmetros
        e, opcionalmente, um "?f=" cosmético só para cache-busting no
        front-end — o valor de "f" nunca é lido aqui)."""
        row = db.fetch_one(f"SELECT logo_dados, logo_mime FROM parametros_site WHERE id = {db.q(id)}")
        if not row or not row.get("logo_dados"):
            abort(404)
        conteudo = base64.b64decode(row["logo_dados"])
        return send_file(io.BytesIO(conteudo), mimetype=row.get("logo_mime") or "application/octet-stream")

    # -------------------------------------------------------------- etapas
    @app.get("/api/etapas")
    def list_etapas():
        pid = request.args.get("projeto_id")
        sql = "SELECT * FROM etapas"
        if pid:
            sql += f" WHERE projeto_id = {db.q(pid)}"
        sql += " ORDER BY numero"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/etapas")
    def create_etapa():
        data = request.get_json(force=True)
        if not (data.get("numero") and data.get("nome")):
            return jsonify({"erro": "Número e nome da etapa são obrigatórios."}), 400
        return jsonify(insert_row("etapas", data, ETAPA_FIELDS)), 201

    @app.put("/api/etapas/<id>")
    def update_etapa(id):
        row = patch_row("etapas", id, request.get_json(force=True), ETAPA_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/etapas/<id>")
    def delete_etapa(id):
        return delete_row("etapas", id)

    # -------------------------------------------------------------- frentes
    @app.get("/api/frentes")
    def list_frentes():
        pid = request.args.get("projeto_id")
        sql = "SELECT * FROM frentes_trabalho"
        if pid:
            sql += f" WHERE projeto_id = {db.q(pid)}"
        sql += " ORDER BY ordem"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/frentes")
    def create_frente():
        data = request.get_json(force=True)
        if not data.get("nome"):
            return jsonify({"erro": "Nome da frente de trabalho é obrigatório."}), 400
        return jsonify(insert_row("frentes_trabalho", data, FRENTE_FIELDS)), 201

    @app.put("/api/frentes/<id>")
    def update_frente(id):
        row = patch_row("frentes_trabalho", id, request.get_json(force=True), FRENTE_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/frentes/<id>")
    def delete_frente(id):
        return delete_row("frentes_trabalho", id)

    # -------------------------------------------------------- tipos atividade
    @app.get("/api/tipos-atividade")
    def list_tipos_atividade():
        return jsonify(db.fetch_all("SELECT * FROM tipos_atividade_elementar ORDER BY ordem"))

    @app.post("/api/tipos-atividade")
    def create_tipo_atividade():
        data = request.get_json(force=True)
        if not data.get("nome"):
            return jsonify({"erro": "Nome do tipo de atividade elementar é obrigatório."}), 400
        return jsonify(insert_row("tipos_atividade_elementar", data, TIPO_ATIVIDADE_FIELDS)), 201

    @app.put("/api/tipos-atividade/<id>")
    def update_tipo_atividade(id):
        row = patch_row("tipos_atividade_elementar", id, request.get_json(force=True), TIPO_ATIVIDADE_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/tipos-atividade/<id>")
    def delete_tipo_atividade(id):
        return delete_row("tipos_atividade_elementar", id)

    # ------------------------------------------------------------- recursos
    @app.get("/api/recursos")
    def list_recursos():
        return jsonify(db.fetch_all("SELECT * FROM recursos ORDER BY nome"))

    @app.post("/api/recursos")
    def create_recurso():
        data = request.get_json(force=True)
        if not data.get("nome"):
            return jsonify({"erro": "Nome do recurso é obrigatório."}), 400
        if data.get("tipo_vinculo") and data["tipo_vinculo"] not in TIPO_VINCULO_VALIDOS:
            return jsonify({"erro": "Vínculo inválido — use Consultoria, Cliente ou Terceirizado."}), 400
        return jsonify(insert_row("recursos", data, RECURSO_FIELDS)), 201

    @app.put("/api/recursos/<id>")
    def update_recurso(id):
        data = request.get_json(force=True)
        if data.get("tipo_vinculo") and data["tipo_vinculo"] not in TIPO_VINCULO_VALIDOS:
            return jsonify({"erro": "Vínculo inválido — use Consultoria, Cliente ou Terceirizado."}), 400
        row = patch_row("recursos", id, data, RECURSO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/recursos/<id>")
    def delete_recurso(id):
        return delete_row("recursos", id)

    # ----------------------------------------------------------- atividades
    @app.get("/api/atividades")
    def list_atividades():
        args = request.args
        where = []
        if args.get("projeto_id"):
            where.append(f"a.projeto_id = {db.q(args['projeto_id'])}")
        if args.get("etapa_id"):
            where.append(f"a.etapa_id = {db.q(args['etapa_id'])}")
        if args.get("frente_trabalho_id"):
            where.append(f"a.frente_trabalho_id = {db.q(args['frente_trabalho_id'])}")
        if args.get("status"):
            where.append(f"a.status = {db.q(args['status'])}")
        if args.get("responsavel_id"):
            where.append(
                f"EXISTS(SELECT 1 FROM atividade_recurso ar WHERE ar.atividade_id = a.id "
                f"AND ar.recurso_id = {db.q(args['responsavel_id'])})"
            )
        if args.get("atividade_pai_id"):
            where.append(f"a.atividade_pai_id = {db.q(args['atividade_pai_id'])}")
        if args.get("search"):
            termo = args["search"].replace("'", "''")
            where.append(
                f"(a.nome ILIKE '%{termo}%' OR a.descricao ILIKE '%{termo}%' OR a.codigo_wbs ILIKE '%{termo}%')"
            )
        if args.get("periodo_inicio") and args.get("periodo_fim"):
            where.append(
                f"a.dtini_prev <= {db.q(args['periodo_fim'])} AND a.dtfim_prev >= {db.q(args['periodo_inicio'])}"
            )
        if args.get("atrasadas") == "true":
            where.append(
                "a.status NOT IN ('Concluída','Concluída com atraso','Concluída com esforço maior',"
                "'Concluída com atraso e esforço maior','Cancelada') AND ("
                "(a.dtfim_prev IS NOT NULL AND a.dtfim_prev < CURRENT_DATE) OR "
                "(a.status = 'Não iniciada' AND a.dtini_prev IS NOT NULL AND a.dtini_prev < CURRENT_DATE))"
            )
        if args.get("criticas") == "true":
            where.append("a.cpm_critica = TRUE")
        sql = ATIVIDADE_SELECT
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.dtini_prev NULLS LAST, a.codigo_wbs"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/atividades")
    def create_atividade():
        data = request.get_json(force=True)
        # Herança nativa do item do TR: se a atividade tem pai e não veio com
        # requisito_tr_id explícito, copia do pai (permanece editável depois).
        if not data.get("requisito_tr_id") and data.get("atividade_pai_id"):
            pai = db.fetch_one(
                f"SELECT requisito_tr_id FROM atividades WHERE id = {db.q(data['atividade_pai_id'])}"
            )
            if pai and pai.get("requisito_tr_id"):
                data["requisito_tr_id"] = pai["requisito_tr_id"]
        # Classifica ANTES da checagem de relato/consistência: se os dados já dizem que a
        # atividade está pronta (100% + Fim Real), o Status certo (Concluída/Concluída com
        # atraso/.../com atraso e esforço maior) é calculado sozinho aqui, então as duas
        # checagens abaixo já enxergam o Status final, não o que veio bruto no payload.
        classificar_conclusao(None, data)
        status = data.get("status")
        if status in STATUS_EXIGE_RELATO:
            return jsonify({
                "erro": f'Não é possível criar a atividade já como "{status}". '
                        'Crie como "Não iniciada" e, na sequência, registre um relato de andamento ao mudar o status.'
            }), 400
        try:
            validar_consistencia_conclusao(None, data)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        aplicar_percentual_inicial(None, data)
        return jsonify(insert_row("atividades", data, ATIVIDADE_FIELDS)), 201

    @app.get("/api/atividades/<id>")
    def get_atividade(id):
        row = db.fetch_one(ATIVIDADE_SELECT + f" WHERE a.id = {db.q(id)}")
        if not row:
            abort(404)
        return jsonify(row)

    @app.put("/api/atividades/<id>")
    def update_atividade(id):
        data = request.get_json(force=True)
        atual = db.fetch_one(
            f"SELECT status, percentual_concluido, dtini_real, dtfim_real, dtfim_prev, "
            f"prazo_horas, horas_realizadas "
            f"FROM atividades WHERE id = {db.q(id)}"
        )
        if not atual:
            abort(404)
        # Mesma ordem do create: classifica primeiro (pode sobrescrever data["status"]
        # com a variante certa de Concluída), só depois checa relato/consistência.
        classificar_conclusao(atual, data)
        novo_status = data.get("status")
        if novo_status in STATUS_EXIGE_RELATO:
            tem_relato = db.fetch_one(
                f"SELECT 1 AS x FROM atividade_relato WHERE atividade_id = {db.q(id)} LIMIT 1"
            )
            if not tem_relato:
                return jsonify({
                    "erro": f'Status "{novo_status}" exige ao menos um relato de andamento registrado. '
                            'Adicione um relato na aba Relatos e tente salvar novamente.'
                }), 400
        try:
            validar_consistencia_conclusao(atual, data)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        aplicar_percentual_inicial(atual, data)
        usuario = request.headers.get("X-Usuario", "")
        prelude = f"SET LOCAL app.usuario_atual = {db.q(usuario)};" if usuario else ""
        row = patch_row("atividades", id, data, ATIVIDADE_FIELDS, prelude=prelude)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/atividades/<id>")
    def delete_atividade(id):
        return delete_row("atividades", id)

    @app.get("/api/atividades/<id>/historico")
    def atividade_historico(id):
        return jsonify(db.fetch_all(
            f"SELECT * FROM historico_status_atividade WHERE atividade_id = {db.q(id)} ORDER BY alterado_em"
        ))

    # ---------------------------------------------------------------- relatos
    # 41ª rodada: relato deixou de carregar o sinalizador de "pendência"
    # (eh_pendencia/pendencia_*, migração 027) — voltou a ser só o texto de
    # andamento que já era antes disso. Sinalizar uma pendência a partir de
    # uma atividade agora é feito pelo cadastro formal `pendencias` (ver
    # rotas mais abaixo), aberto já com a atividade pré-preenchida pela
    # própria aba "Pendências" do modal de atividade.
    @app.get("/api/atividades/<id>/relatos")
    def list_relatos(id):
        sql = (
            "SELECT r.*, au.nome AS autor_cadastro_nome "
            "FROM atividade_relato r "
            "LEFT JOIN recursos au ON au.id = r.autor_id "
            f"WHERE r.atividade_id = {db.q(id)} ORDER BY r.criado_em DESC"
        )
        return jsonify(db.fetch_all(sql))

    @app.post("/api/atividades/<id>/relatos")
    def create_relato(id):
        data = request.get_json(force=True)
        if not (data.get("texto") or "").strip():
            return jsonify({"erro": "Informe o texto do relato."}), 400
        data = dict(data)
        data["atividade_id"] = id
        row = insert_row("atividade_relato", data, ["atividade_id"] + RELATO_FIELDS)
        return jsonify(row), 201

    # ------------------------------------------------------------ dependências
    @app.get("/api/atividades/<id>/dependencias")
    def list_dependencias(id):
        sql = (
            "SELECT ad.*, p.nome AS predecessora_nome, p.status AS predecessora_status "
            "FROM atividade_dependencia ad JOIN atividades p ON p.id = ad.predecessora_id "
            f"WHERE ad.atividade_id = {db.q(id)}"
        )
        return jsonify(db.fetch_all(sql))

    @app.get("/api/projetos/<projeto_id>/dependencias")
    def list_dependencias_projeto(projeto_id):
        """Todas as dependências do projeto de uma vez (39ª rodada) — usada
        pela tela "Editar em massa" pra mostrar, em cada linha da planilha,
        quantas predecessoras/sucessoras a atividade tem, sem precisar de
        uma chamada por atividade (list_dependencias acima já existe, mas é
        por atividade, cara demais pra montar a grade inteira)."""
        sql = (
            "SELECT ad.atividade_id, ad.predecessora_id, ad.tipo, ad.lag_horas, "
            "p.codigo_wbs AS predecessora_codigo_wbs, p.nome AS predecessora_nome, "
            "s.codigo_wbs AS sucessora_codigo_wbs, s.nome AS sucessora_nome "
            "FROM atividade_dependencia ad "
            "JOIN atividades p ON p.id = ad.predecessora_id "
            "JOIN atividades s ON s.id = ad.atividade_id "
            f"WHERE p.projeto_id = {db.q(projeto_id)} AND s.projeto_id = {db.q(projeto_id)}"
        )
        return jsonify(db.fetch_all(sql))

    @app.post("/api/atividades/<id>/dependencias")
    def create_dependencia(id):
        data = request.get_json(force=True)
        data["atividade_id"] = id
        row = insert_row("atividade_dependencia", data, ["atividade_id", "predecessora_id", "tipo", "lag_horas"])
        return jsonify(row), 201

    @app.delete("/api/dependencias/<dep_id>")
    def delete_dependencia(dep_id):
        db.execute(f"DELETE FROM atividade_dependencia WHERE id = {db.q(dep_id)}")
        return "", 204

    # -------------------------------------------------------- recursos ativ.
    @app.get("/api/atividades/<id>/recursos")
    def list_atividade_recursos(id):
        sql = (
            "SELECT ar.*, r.nome AS recurso_nome, r.empresa, r.cargo, r.tipo_vinculo "
            f"FROM atividade_recurso ar JOIN recursos r ON r.id = ar.recurso_id WHERE ar.atividade_id = {db.q(id)}"
        )
        return jsonify(db.fetch_all(sql))

    @app.post("/api/atividades/<id>/recursos")
    def add_atividade_recurso(id):
        data = request.get_json(force=True)
        sql = (
            "INSERT INTO atividade_recurso (atividade_id, recurso_id, papel_na_atividade, horas_alocadas) "
            f"VALUES ({db.q(id)}, {db.q(data.get('recurso_id'))}, {db.q(data.get('papel_na_atividade'))}, "
            f"{db.q(data.get('horas_alocadas'))}) RETURNING *"
        )
        return jsonify(db.execute_returning_one(sql)), 201

    @app.patch("/api/atividades/<id>/recursos/<recurso_id>")
    def update_atividade_recurso(id, recurso_id):
        # Edita horas_alocadas/papel_na_atividade de um participante já
        # existente (18ª rodada de features "extra") — só os campos presentes
        # no corpo são tocados; mandar horas_alocadas: null limpa o valor
        # (volta a assumir as horas previstas da atividade, ver
        # minhas_atividades.garantir_dias). Update genérico (insert_row/
        # patch_row) não serve aqui porque a chave é composta
        # (atividade_id, recurso_id), não um único "id".
        data = request.get_json(force=True)
        sets = []
        if "horas_alocadas" in data:
            sets.append(f"horas_alocadas = {db.q(data.get('horas_alocadas'))}")
        if "papel_na_atividade" in data:
            sets.append(f"papel_na_atividade = {db.q(data.get('papel_na_atividade'))}")
        if not sets:
            return jsonify({"erro": "Nenhum campo válido informado."}), 400
        sql = (
            f"UPDATE atividade_recurso SET {', '.join(sets)} "
            f"WHERE atividade_id = {db.q(id)} AND recurso_id = {db.q(recurso_id)} RETURNING *"
        )
        row = db.execute_returning_one(sql)
        if not row:
            return jsonify({"erro": "Vínculo não encontrado."}), 404
        return jsonify(row)

    @app.delete("/api/atividades/<id>/recursos/<recurso_id>")
    def remove_atividade_recurso(id, recurso_id):
        db.execute(
            f"DELETE FROM atividade_recurso WHERE atividade_id = {db.q(id)} AND recurso_id = {db.q(recurso_id)}"
        )
        return "", 204

    # -------------------------------------------------- minhas atividades
    # Grade semanal do consultor logado: dias úteis da semana x atividades em
    # que o profissional vinculado a ele (por e-mail, ver
    # backend/app/minhas_atividades.py) é um dos participantes (atividade_recurso).
    @app.get("/api/minhas-atividades")
    def minhas_atividades_grade():
        # 16ª rodada: não recebe mais projeto_id — a grade sempre traz as
        # atividades de TODOS os projetos em que o usuário está delegado
        # (ver docstring de minhas_atividades.montar_grade).
        usuario = auth.buscar_usuario_publico(session["usuario_id"])
        semana_str = request.args.get("semana")
        try:
            data_ref = date.fromisoformat(semana_str) if semana_str else date.today()
        except ValueError:
            return jsonify({"erro": "Data de referência da semana inválida."}), 400
        return jsonify(minhas_atividades.montar_grade(usuario, data_ref))

    @app.put("/api/minhas-atividades/dia/<id>")
    def minhas_atividades_ajustar(id):
        usuario = auth.buscar_usuario_publico(session["usuario_id"])
        data = request.get_json(force=True)
        try:
            row = minhas_atividades.ajustar_dia(id, usuario, data.get("horas_previstas"))
        except minhas_atividades.MinhasAtividadesError as e:
            return jsonify({"erro": str(e)}), 400
        if not row:
            abort(404)
        return jsonify(row)

    @app.post("/api/minhas-atividades/dia/<id>/confirmar")
    def minhas_atividades_confirmar(id):
        usuario = auth.buscar_usuario_publico(session["usuario_id"])
        data = request.get_json(silent=True) or {}
        try:
            row = minhas_atividades.confirmar_dia(id, usuario, data.get("horas"))
        except minhas_atividades.MinhasAtividadesError as e:
            return jsonify({"erro": str(e)}), 400
        if not row:
            abort(404)
        return jsonify(row)

    @app.post("/api/minhas-atividades/dia/<id>/desconfirmar")
    def minhas_atividades_desconfirmar(id):
        usuario = auth.buscar_usuario_publico(session["usuario_id"])
        try:
            row = minhas_atividades.desconfirmar_dia(id, usuario)
        except minhas_atividades.MinhasAtividadesError as e:
            return jsonify({"erro": str(e)}), 400
        if not row:
            abort(404)
        return jsonify(row)

    # ------------------------------------------------------------- requisitos
    @app.get("/api/requisitos")
    def list_requisitos():
        args = request.args
        where = []
        if args.get("projeto_id"):
            where.append(f"r.projeto_id = {db.q(args['projeto_id'])}")
        if args.get("frente_trabalho_id"):
            where.append(f"r.frente_trabalho_id = {db.q(args['frente_trabalho_id'])}")
        if args.get("classificacao"):
            where.append(f"r.classificacao = {db.q(args['classificacao'])}")
        if args.get("atendimento"):
            where.append(f"r.atendimento = {db.q(args['atendimento'])}")
        if args.get("status"):
            where.append(f"r.status = {db.q(args['status'])}")
        if args.get("search"):
            termo = args["search"].replace("'", "''")
            where.append(f"(r.codigo ILIKE '%{termo}%' OR r.titulo ILIKE '%{termo}%' OR r.descricao ILIKE '%{termo}%')")
        sql = (
            "SELECT r.*, f.nome AS frente_nome, res.nome AS responsavel_nome, "
            "COALESCE((SELECT json_agg(json_build_object("
            "'id', rrm.id, 'manual_id', rrm.manual_id, 'manual_nome', rrm.manual_nome, "
            "'pagina', rrm.pagina, 'trecho', rrm.trecho, 'relevancia', rrm.relevancia, 'origem', rrm.origem"
            ") ORDER BY rrm.relevancia DESC NULLS LAST, rrm.criado_em) "
            "FROM requisito_referencia_manual rrm WHERE rrm.requisito_id = r.id), '[]') AS referencias_manuais "
            "FROM requisitos_tr r LEFT JOIN frentes_trabalho f ON f.id = r.frente_trabalho_id "
            "LEFT JOIN recursos res ON res.id = r.responsavel_id"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.codigo"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/requisitos")
    def create_requisito():
        return jsonify(insert_row("requisitos_tr", request.get_json(force=True), REQUISITO_FIELDS)), 201

    @app.put("/api/requisitos/<id>")
    def update_requisito(id):
        row = patch_row("requisitos_tr", id, request.get_json(force=True), REQUISITO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/requisitos/<id>")
    def delete_requisito(id):
        return delete_row("requisitos_tr", id)

    @app.get("/api/requisitos/resumo-remocao")
    def requisitos_resumo_remocao():
        """Prévia (contagens) pro modal de 'Apagar todos os requisitos' — mesmo
        espírito do /cronograma/resumo-remocao: mostra o tamanho do estrago
        antes do usuário confirmar."""
        projeto_id = request.args.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        row = db.fetch_one(f"""
            SELECT
              (SELECT count(*) FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)}) AS requisitos,
              (SELECT count(*) FROM atividade_requisito ar
                 JOIN requisitos_tr r ON r.id = ar.requisito_id
                 WHERE r.projeto_id = {db.q(projeto_id)}) AS vinculos_atividade,
              (SELECT count(*) FROM requisito_referencia_manual rrm
                 JOIN requisitos_tr r ON r.id = rrm.requisito_id
                 WHERE r.projeto_id = {db.q(projeto_id)}) AS referencias_manual,
              (SELECT count(DISTINCT a.id) FROM atividades a
                 JOIN requisitos_tr r ON r.id = a.requisito_tr_id
                 WHERE r.projeto_id = {db.q(projeto_id)}) AS atividades_perdem_vinculo
        """)
        return jsonify(row or {"requisitos": 0, "vinculos_atividade": 0, "referencias_manual": 0, "atividades_perdem_vinculo": 0})

    @app.post("/api/requisitos/remover-todos")
    def requisitos_remover_todos():
        """Apaga TODOS os requisitos do TR do projeto (e, em cascata, os vínculos
        N:N com atividades e as referências de manuais encontradas em 'Analisar
        Requisitos'). Atividades que apontavam pra um desses requisitos
        (atividades.requisito_tr_id) NÃO são apagadas — só perdem esse vínculo
        direto (fica NULL), porque o campo é opcional e a FK não tem ON DELETE
        definido. Ação irreversível: por isso exige, em `confirmacao`, o nome
        exato do projeto — conferido aqui no servidor antes de apagar
        qualquer coisa (mesmo padrão de /cronograma/remover-tudo)."""
        data = request.get_json(force=True)
        projeto_id = data.get("projeto_id")
        confirmacao = (data.get("confirmacao") or "").strip()
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        if not confirmacao or confirmacao != projeto["nome"]:
            return jsonify({"erro": "Confirmação não confere com o nome do projeto. Nada foi apagado."}), 400
        contagem = db.fetch_one(
            f"SELECT count(*) AS requisitos FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)}"
        ) or {"requisitos": 0}
        db.execute(
            f"UPDATE atividades SET requisito_tr_id = NULL WHERE requisito_tr_id IN "
            f"(SELECT id FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)})"
        )
        db.execute(f"DELETE FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)}")
        # Ação irreversível e destrutiva — registra como evento sensível dedicado
        # (não passa por delete_row: é uma exclusão em massa, não de 1 registro).
        auditoria.registrar_evento_manual(
            "exclusao",
            f'Apagou todos os requisitos do TR do projeto "{projeto["nome"]}" '
            f'({contagem["requisitos"]} requisito(s))',
            entidade="requisitos_tr", entidade_id=str(projeto_id), entidade_rotulo=projeto["nome"],
            projeto_id=projeto_id, sensivel=True,
            detalhes={"requisitos_removidos": contagem["requisitos"]},
        )
        return jsonify({"ok": True})

    @app.post("/api/requisitos/importar-csv")
    def importar_requisitos_csv():
        """Importa/atualiza requisitos do TR em massa a partir de uma planilha CSV.
        Colunas aceitas (cabeçalho, minúsculas, ; ou , como separador):
        codigo*, titulo*, descricao, frente, classificacao, atendimento, status,
        prioridade, cobranca, data_levantamento, observacoes
        (* obrigatórias). "frente" é o NOME da frente de trabalho já cadastrada
        no projeto — se não bater com nenhuma, o requisito é importado sem frente.
        Requisitos com código já existente no projeto são atualizados (upsert)."""
        projeto_id = request.form.get("projeto_id")
        file = request.files.get("file")
        if not (projeto_id and file):
            return jsonify({"erro": "projeto_id e file são obrigatórios"}), 400

        raw_bytes = file.read()
        try:
            raw = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw = raw_bytes.decode("latin-1")
        if not raw.strip():
            return jsonify({"erro": "Arquivo CSV vazio."}), 400

        primeira_linha = raw.splitlines()[0]
        delimiter = ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","
        reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
        if not reader.fieldnames:
            return jsonify({"erro": "Não foi possível ler o cabeçalho do CSV."}), 400
        reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames]
        if "codigo" not in reader.fieldnames or "titulo" not in reader.fieldnames:
            return jsonify({"erro": "O CSV precisa ter, no mínimo, as colunas 'codigo' e 'titulo'."}), 400

        frentes = {
            f["nome"].strip().lower(): f["id"]
            for f in db.fetch_all(f"SELECT id, nome FROM frentes_trabalho WHERE projeto_id = {db.q(projeto_id)}")
        }
        existentes = {
            r["codigo"] for r in db.fetch_all(f"SELECT codigo FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)}")
        }

        def campo_validado(valor, permitidos, default):
            valor = (valor or "").strip()
            return valor if valor in permitidos else default

        linhas_por_codigo = {}  # dict, não lista — um código repetido no arquivo sobrescreve o anterior
        erros = []
        for i, row in enumerate(reader, start=2):  # linha 1 = cabeçalho
            codigo = (row.get("codigo") or "").strip()
            titulo = (row.get("titulo") or "").strip()
            if not codigo or not titulo:
                erros.append(f"Linha {i}: código e título são obrigatórios — linha ignorada.")
                continue
            if codigo in linhas_por_codigo:
                erros.append(f"Linha {i}: código '{codigo}' repetido no arquivo — só a última ocorrência foi usada.")
            frente_nome = (row.get("frente") or "").strip().lower()
            frente_id = frentes.get(frente_nome)
            if frente_nome and not frente_id:
                erros.append(f"Linha {i}: frente '{row.get('frente')}' não encontrada — requisito importado sem frente.")
            linhas_por_codigo[codigo] = {
                "codigo": codigo,
                "titulo": titulo,
                "descricao": (row.get("descricao") or "").strip() or None,
                "tipo_requisito": "Funcional",
                "modulo_origem": None,
                "frente_trabalho_id": frente_id,
                "classificacao": campo_validado(row.get("classificacao"), CLASSIFICACAO_TR_VALIDAS, "Obrigatório"),
                "atendimento": campo_validado(row.get("atendimento"), ATENDIMENTO_TR_VALIDOS, "A analisar"),
                "status": campo_validado(row.get("status"), STATUS_REQUISITO_VALIDOS, "Não iniciado"),
                "prioridade": campo_validado(row.get("prioridade"), PRIORIDADE_VALIDAS, "Média"),
                "cobranca": campo_validado(row.get("cobranca"), COBRANCA_VALIDAS, "N/A"),
                "data_levantamento": (row.get("data_levantamento") or "").strip() or None,
                "observacoes": (row.get("observacoes") or "").strip() or None,
            }
        linhas_validas = list(linhas_por_codigo.values())

        if not linhas_validas:
            return jsonify({"inseridos": 0, "atualizados": 0,
                             "erros": erros or ["Nenhuma linha válida encontrada no arquivo."]}), 400

        inseridos, atualizados = upsert_requisitos(projeto_id, linhas_validas)
        return jsonify({"inseridos": inseridos, "atualizados": atualizados, "erros": erros})

    # ---------------------------------------------------- importação a partir do TR
    @app.post("/api/requisitos/importar-documento/preview")
    def importar_documento_preview():
        """Recebe o arquivo do Termo de Referência (HTML, DOCX, PDF ou TXT) e
        devolve uma PRÉVIA dos requisitos que seriam importados — não grava
        nada no banco ainda. Ver app/tr_parser.py para o algoritmo de
        extração (baseado em regras, sem IA em tempo de execução)."""
        projeto_id = request.form.get("projeto_id")
        file = request.files.get("file")
        if not (projeto_id and file):
            return jsonify({"erro": "projeto_id e file são obrigatórios"}), 400
        try:
            resultado = tr_parser.parse_tr_document(file.read(), file.filename or "")
        except tr_parser.TrParseError as e:
            return jsonify({"erro": str(e)}), 400
        except Exception as e:
            return jsonify({"erro": f"Falha ao ler o documento: {e}"}), 400

        frentes = {
            f["nome"].strip().lower(): f["id"]
            for f in db.fetch_all(f"SELECT id, nome FROM frentes_trabalho WHERE projeto_id = {db.q(projeto_id)}")
        }
        existentes = {
            r["codigo"] for r in db.fetch_all(
                f"SELECT codigo FROM requisitos_tr WHERE projeto_id = {db.q(projeto_id)}"
            )
        }
        for it in resultado["itens"]:
            it["ja_existe"] = it["codigo"] in existentes
            it["frente_sugerida_id"] = None
            it["frente_sugerida_nome"] = None
            modulo_norm = (it.get("modulo_origem") or "").lower()
            for nome, fid in frentes.items():
                if nome and nome in modulo_norm:
                    it["frente_sugerida_id"] = fid
                    it["frente_sugerida_nome"] = nome
                    break
        resumo = {
            "total": len(resultado["itens"]),
            "funcionais": sum(1 for it in resultado["itens"] if it["tipo_requisito"] == "Funcional"),
            "nao_funcionais": sum(1 for it in resultado["itens"] if it["tipo_requisito"] == "Não Funcional"),
            "ja_existentes": sum(1 for it in resultado["itens"] if it["ja_existe"]),
            "baixa_confianca": resultado.get("baixa_confianca", False),
        }
        return jsonify({"itens": resultado["itens"], "avisos": resultado["avisos"], "resumo": resumo})

    @app.post("/api/requisitos/importar-documento/confirmar")
    def importar_documento_confirmar():
        """Recebe a lista de itens que o usuário revisou/manteve marcados na
        prévia (mesmo formato devolvido por .../preview) e grava de fato,
        via upsert por código — reimportar/reconfirmar atualiza em vez de
        duplicar."""
        data = request.get_json(force=True)
        projeto_id = data.get("projeto_id")
        itens = data.get("itens") or []
        if not projeto_id or not itens:
            return jsonify({"erro": "projeto_id e itens são obrigatórios"}), 400

        linhas = []
        for it in itens:
            codigo = (it.get("codigo") or "").strip()
            titulo = (it.get("titulo") or "").strip()
            if not codigo or not titulo:
                continue
            tipo = it.get("tipo_requisito")
            linhas.append({
                "codigo": codigo,
                "titulo": titulo,
                "descricao": it.get("descricao") or None,
                "tipo_requisito": tipo if tipo in TIPO_REQUISITO_VALIDOS else "Funcional",
                "modulo_origem": it.get("modulo_origem") or None,
                "frente_trabalho_id": it.get("frente_trabalho_id") or it.get("frente_sugerida_id") or None,
                "classificacao": "Obrigatório",
                "atendimento": "A analisar",
                "status": "Não iniciado",
                "prioridade": "Média",
                "cobranca": "N/A",
                "data_levantamento": None,
                "observacoes": None,
            })
        if not linhas:
            return jsonify({"erro": "Nenhum item válido para importar."}), 400

        inseridos, atualizados = upsert_requisitos(projeto_id, linhas)
        return jsonify({"inseridos": inseridos, "atualizados": atualizados})

    @app.get("/api/atividades/<id>/requisitos")
    def list_atividade_requisitos(id):
        sql = (
            "SELECT r.* FROM atividade_requisito ar JOIN requisitos_tr r ON r.id = ar.requisito_id "
            f"WHERE ar.atividade_id = {db.q(id)}"
        )
        return jsonify(db.fetch_all(sql))

    @app.post("/api/atividades/<id>/requisitos")
    def link_atividade_requisito(id):
        rid = request.get_json(force=True).get("requisito_id")
        db.execute(
            f"INSERT INTO atividade_requisito (atividade_id, requisito_id) VALUES ({db.q(id)}, {db.q(rid)}) "
            "ON CONFLICT DO NOTHING"
        )
        return "", 201

    @app.delete("/api/atividades/<id>/requisitos/<rid>")
    def unlink_atividade_requisito(id, rid):
        db.execute(
            f"DELETE FROM atividade_requisito WHERE atividade_id = {db.q(id)} AND requisito_id = {db.q(rid)}"
        )
        return "", 204

    # ------------------------------------------- manuais do sistema (globais)
    @app.get("/api/manuais")
    def list_manuais():
        sql = f"SELECT {manuais.MANUAL_COLUNAS_LEVES} FROM manuais ORDER BY nome"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/manuais")
    def upload_manual():
        """Cadastra um manual novo: recebe o PDF (multipart), extrai o texto
        por página (ver manuais.extrair_paginas_pdf) e já grava tudo — o
        manual só fica pesquisável a partir daqui, não precisa de um passo
        separado de "processar"."""
        nome = (request.form.get("nome") or "").strip()
        versao = (request.form.get("versao") or "").strip() or None
        file = request.files.get("file")
        if not nome or not file or not file.filename:
            return jsonify({"erro": "Nome e arquivo PDF são obrigatórios."}), 400
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in MANUAL_EXTENSOES_PERMITIDAS:
            return jsonify({"erro": "Formato não suportado. Envie um arquivo PDF."}), 400
        conteudo = file.read()
        if len(conteudo) > manuais.TAMANHO_MAXIMO_BYTES:
            return jsonify({"erro": "Arquivo muito grande (máximo 40 MB)."}), 400
        try:
            paginas = manuais.extrair_paginas_pdf(conteudo)
        except manuais.ManualError as e:
            return jsonify({"erro": str(e)}), 400
        dados_b64 = base64.b64encode(conteudo).decode("ascii")
        manual = db.execute_returning_one(
            "INSERT INTO manuais (nome, versao, arquivo_nome, arquivo_mime, arquivo_dados, tamanho_bytes, paginas_total) "
            f"VALUES ({db.q(nome)}, {db.q(versao)}, {db.q(file.filename)}, 'application/pdf', "
            f"{db.q(dados_b64)}, {db.q(len(conteudo))}, {db.q(len(paginas))}) "
            f"RETURNING {manuais.MANUAL_COLUNAS_LEVES}"
        )
        if paginas:
            values_sql = ", ".join(
                "(" + db.q(manual["id"]) + ", " + db.q(p["numero_pagina"]) + ", " + db.q(p["texto"]) + ")"
                for p in paginas
            )
            db.execute(
                f"INSERT INTO manual_paginas (manual_id, numero_pagina, texto) VALUES {values_sql}",
                timeout=120,
            )
        return jsonify(manual), 201

    @app.put("/api/manuais/<id>")
    def update_manual(id):
        row = patch_row("manuais", id, request.get_json(force=True), MANUAL_FIELDS)
        if not row:
            abort(404)
        row = {k: v for k, v in row.items() if k != "arquivo_dados"}
        return jsonify(row)

    @app.delete("/api/manuais/<id>")
    def delete_manual(id):
        return delete_row("manuais", id)

    @app.get("/api/manuais/<id>/arquivo")
    def get_manual_arquivo(id):
        """Serve o PDF do manual a partir do base64 gravado no banco (mesmo
        princípio do logo — sem depender de disco local do container)."""
        row = db.fetch_one(f"SELECT arquivo_nome, arquivo_mime, arquivo_dados FROM manuais WHERE id = {db.q(id)}")
        if not row or not row.get("arquivo_dados"):
            abort(404)
        conteudo = base64.b64decode(row["arquivo_dados"])
        return send_file(io.BytesIO(conteudo), mimetype=row.get("arquivo_mime") or "application/pdf",
                          download_name=row.get("arquivo_nome") or "manual.pdf")

    # -------------------------------------- analisar requisitos x manuais
    @app.post("/api/requisitos/<id>/analisar")
    def analisar_requisito(id):
        """Roda a busca por palavra-chave (ver app/manuais.py — sem IA) do
        texto do requisito contra as páginas dos manuais ativos, e GRAVA os
        candidatos encontrados como novas referências (substitui as
        referências de origem='busca' anteriores deste requisito — as
        adicionadas à mão, origem='manual', não são mexidas)."""
        req = db.fetch_one(f"SELECT titulo, descricao FROM requisitos_tr WHERE id = {db.q(id)}")
        if not req:
            abort(404)
        texto = f"{req.get('titulo') or ''}\n{req.get('descricao') or ''}"
        referencias = manuais.buscar_referencias(texto)
        db.execute(f"DELETE FROM requisito_referencia_manual WHERE requisito_id = {db.q(id)} AND origem = 'busca'")
        if referencias:
            values_sql = ", ".join(
                "(" + ", ".join([
                    db.q(id), db.q(r["manual_id"]), db.q(r["manual_nome"]),
                    db.q(r["pagina"]), db.q(r["trecho"]), db.q(r["relevancia"]), "'busca'",
                ]) + ")"
                for r in referencias
            )
            db.execute(
                "INSERT INTO requisito_referencia_manual "
                "(requisito_id, manual_id, manual_nome, pagina, trecho, relevancia, origem) "
                f"VALUES {values_sql}"
            )
        db.execute(f"UPDATE requisitos_tr SET analisado_em = now() WHERE id = {db.q(id)}")
        referencias_salvas = db.fetch_all(
            "SELECT id, manual_id, manual_nome, pagina, trecho, relevancia, origem "
            f"FROM requisito_referencia_manual WHERE requisito_id = {db.q(id)} "
            "ORDER BY relevancia DESC NULLS LAST, criado_em"
        )
        return jsonify({"referencias": referencias_salvas, "encontradas": len(referencias)})

    @app.post("/api/requisitos/analisar-lote")
    def analisar_requisitos_lote():
        """Mesma análise de analisar_requisito(), mas para vários requisitos
        de uma vez (botão "Analisar todos" da tela Analisar Requisitos).
        Roda um por um (não é pesado — é só SQL local, sem chamada externa),
        e devolve um resumo por requisito pro front-end mostrar o progresso."""
        body = request.get_json(force=True) or {}
        ids = [i for i in (body.get("requisito_ids") or []) if i]
        if not ids:
            return jsonify({"erro": "Informe ao menos um requisito."}), 400
        resultado = []
        for rid in ids:
            req = db.fetch_one(f"SELECT titulo, descricao FROM requisitos_tr WHERE id = {db.q(rid)}")
            if not req:
                continue
            texto = f"{req.get('titulo') or ''}\n{req.get('descricao') or ''}"
            referencias = manuais.buscar_referencias(texto)
            db.execute(f"DELETE FROM requisito_referencia_manual WHERE requisito_id = {db.q(rid)} AND origem = 'busca'")
            if referencias:
                values_sql = ", ".join(
                    "(" + ", ".join([
                        db.q(rid), db.q(r["manual_id"]), db.q(r["manual_nome"]),
                        db.q(r["pagina"]), db.q(r["trecho"]), db.q(r["relevancia"]), "'busca'",
                    ]) + ")"
                    for r in referencias
                )
                db.execute(
                    "INSERT INTO requisito_referencia_manual "
                    "(requisito_id, manual_id, manual_nome, pagina, trecho, relevancia, origem) "
                    f"VALUES {values_sql}"
                )
            db.execute(f"UPDATE requisitos_tr SET analisado_em = now() WHERE id = {db.q(rid)}")
            resultado.append({"requisito_id": rid, "encontradas": len(referencias)})
        return jsonify({"resultado": resultado})

    @app.post("/api/requisitos/<id>/referencias")
    def add_referencia_manual(id):
        """Adiciona uma referência à mão (origem='manual') — usado quando o
        consultor sabe onde está a resposta, mas a busca automática não achou
        (ou achou algo irrelevante)."""
        data = request.get_json(force=True) or {}
        manual_id = data.get("manual_id")
        pagina = data.get("pagina")
        if not (manual_id and pagina):
            return jsonify({"erro": "Manual e página são obrigatórios."}), 400
        manual = db.fetch_one(f"SELECT nome FROM manuais WHERE id = {db.q(manual_id)}")
        if not manual:
            return jsonify({"erro": "Manual não encontrado."}), 404
        row = db.execute_returning_one(
            "INSERT INTO requisito_referencia_manual (requisito_id, manual_id, manual_nome, pagina, trecho, origem) "
            f"VALUES ({db.q(id)}, {db.q(manual_id)}, {db.q(manual['nome'])}, {db.q(pagina)}, "
            f"{db.q(data.get('trecho'))}, 'manual') RETURNING *"
        )
        return jsonify(row), 201

    @app.delete("/api/requisitos/<id>/referencias/<ref_id>")
    def delete_referencia_manual(id, ref_id):
        db.execute(
            f"DELETE FROM requisito_referencia_manual WHERE id = {db.q(ref_id)} AND requisito_id = {db.q(id)}"
        )
        return "", 204

    # ----------------------------------------------------------------- faturas
    # (27ª rodada) — faturamento dos entregáveis do cronograma. Filtro
    # projeto_id/atividade_id são os únicos no backend; filtro por texto e por
    # status de pagamento (pago/pendente/atrasada, já vem calculado como
    # paga/atrasada no SELECT) é feito no front-end, mesmo padrão já usado em
    # Cronograma/Analisar Requisitos.
    @app.get("/api/faturas")
    def list_faturas():
        args = request.args
        where = []
        if args.get("projeto_id"):
            where.append(f"f.projeto_id = {db.q(args['projeto_id'])}")
        if args.get("atividade_id"):
            where.append(f"f.atividade_id = {db.q(args['atividade_id'])}")
        sql = FATURA_SELECT
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY f.data_emissao DESC NULLS LAST, f.criado_em DESC"
        return jsonify(db.fetch_all(sql))

    @app.get("/api/faturas/<id>")
    def get_fatura(id):
        row = db.fetch_one(FATURA_SELECT + f" WHERE f.id = {db.q(id)}")
        if not row:
            abort(404)
        return jsonify(row)

    @app.post("/api/faturas")
    def create_fatura():
        data = request.get_json(force=True)
        obrigatorios = ["projeto_id", "atividade_id", "numero_fatura", "responsavel_entrega_id"]
        faltando = [c for c in obrigatorios if not data.get(c)]
        if faltando:
            return jsonify({"erro": "Informe projeto, número da fatura, atividade e responsável pela entrega."}), 400
        try:
            preparar_fatura(data, data["projeto_id"])
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        return jsonify(insert_row("faturas", data, FATURA_FIELDS)), 201

    @app.put("/api/faturas/<id>")
    def update_fatura(id):
        atual = db.fetch_one(f"SELECT projeto_id FROM faturas WHERE id = {db.q(id)}")
        if not atual:
            abort(404)
        data = request.get_json(force=True)
        try:
            preparar_fatura(data, atual["projeto_id"])
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        row = patch_row("faturas", id, data, FATURA_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/faturas/<id>")
    def delete_fatura(id):
        return delete_row("faturas", id)

    # ----------------------------------------------------------- ciclos migração
    @app.get("/api/ciclos-migracao")
    def list_ciclos():
        aid = request.args.get("atividade_id")
        sql = "SELECT * FROM ciclos_migracao"
        if aid:
            sql += f" WHERE atividade_id = {db.q(aid)}"
        sql += " ORDER BY numero_ciclo"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/ciclos-migracao")
    def create_ciclo():
        return jsonify(insert_row("ciclos_migracao", request.get_json(force=True), CICLO_FIELDS)), 201

    # ------------------------------------------------------------------ marcos
    @app.get("/api/marcos")
    def list_marcos():
        pid = request.args.get("projeto_id")
        sql = "SELECT * FROM marcos"
        if pid:
            sql += f" WHERE projeto_id = {db.q(pid)}"
        sql += " ORDER BY data_prevista"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/marcos")
    def create_marco():
        return jsonify(insert_row("marcos", request.get_json(force=True), MARCO_FIELDS)), 201

    @app.put("/api/marcos/<id>")
    def update_marco(id):
        row = patch_row("marcos", id, request.get_json(force=True), MARCO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    # -------------------------------------------------- importação de cronograma
    @app.post("/api/cronograma/importar/preview")
    def cronograma_importar_preview():
        """Recebe uma planilha (.xlsx exportado do MS Project, ou .csv com as
        mesmas colunas) e devolve uma PRÉVIA — contagens de atividades/marcos
        novos x a atualizar, avisos, e a lista de grupos de Etapa/Frente
        encontrados no WBS para o usuário conferir/ajustar antes de gravar.
        Não grava nada no banco ainda. Ver app/cronograma_import.py."""
        projeto_id = request.form.get("projeto_id")
        file = request.files.get("file")
        if not (projeto_id and file):
            return jsonify({"erro": "projeto_id e file são obrigatórios"}), 400
        try:
            resultado = cronograma_import.preview(projeto_id, file.read(), file.filename or "")
        except cronograma_import.ImportacaoError as e:
            return jsonify({"erro": str(e)}), 400
        except Exception as e:
            print(f"[cronograma_import] ERRO INESPERADO em /preview: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao ler a planilha: {e}"}), 400
        return jsonify(resultado)

    @app.post("/api/cronograma/importar/confirmar")
    def cronograma_importar_confirmar():
        """Recebe o token da prévia acima + o mapeamento de Etapa/Frente que o
        usuário revisou (cada grupo do WBS aponta pra uma Etapa/Frente já
        existente, ou pede pra criar uma nova com o nome informado) e grava
        de fato — sempre atualizando o projeto atual (nunca cria um projeto
        novo). Reimportar casa cada linha da planilha com a atividade/marco
        já existente (por Id de origem ou, na falta dele, pelo código WBS) e
        atualiza em vez de duplicar; campos preenchidos manualmente
        (recurso, tipo, status Bloqueada/Cancelada, observações, item do
        TR) não são sobrescritos."""
        data = request.get_json(force=True)
        token = data.get("token")
        projeto_id = data.get("projeto_id")
        if not (token and projeto_id):
            return jsonify({"erro": "token e projeto_id são obrigatórios"}), 400
        try:
            resultado = cronograma_import.confirmar(
                token, projeto_id, data.get("mapeamento_etapas") or [], data.get("mapeamento_frentes") or []
            )
        except cronograma_import.ImportacaoError as e:
            return jsonify({"erro": str(e)}), 400
        except db.DbError:
            # deixa cair no handler global (@app.errorhandler(db.DbError) mais abaixo),
            # que já traduz violação de FK/unicidade em mensagem amigável — aqui só
            # não queremos que um erro de banco no meio da importação vire uma
            # exceção genérica sem chegar nesse handler.
            raise
        except Exception as e:
            # Qualquer outro erro inesperado durante a importação (ex: um valor
            # fora do padrão numa linha da planilha que nenhum parser previu):
            # loga a mensagem completa em `docker compose logs backend` (o "log
            # da carga" pedido depois do incidente do openpyxl/timeout) e devolve
            # o motivo real pro navegador, em vez de deixar o Flask devolver um
            # 500 genérico sem corpo JSON (que aparecia como "Erro de Requisição").
            print(f"[cronograma_import] ERRO INESPERADO em /confirmar: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha inesperada durante a importação: {e}"}), 500
        return jsonify(resultado)

    @app.get("/api/cronograma/resumo-remocao")
    def cronograma_resumo_remocao():
        """Contagens do que seria apagado por /remover-tudo, para o front
        mostrar antes de pedir a confirmação — nada é apagado aqui."""
        projeto_id = request.args.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        row = db.fetch_one(f"""
            SELECT
              (SELECT count(*) FROM atividades WHERE projeto_id = {db.q(projeto_id)}) AS atividades,
              (SELECT count(*) FROM marcos WHERE projeto_id = {db.q(projeto_id)}) AS marcos,
              (SELECT count(*) FROM atividade_dependencia d
                 JOIN atividades a ON a.id = d.atividade_id
                 WHERE a.projeto_id = {db.q(projeto_id)}) AS dependencias,
              (SELECT count(*) FROM atividade_relato r
                 JOIN atividades a ON a.id = r.atividade_id
                 WHERE a.projeto_id = {db.q(projeto_id)}) AS relatos
        """)
        return jsonify(row or {"atividades": 0, "marcos": 0, "dependencias": 0, "relatos": 0})

    @app.post("/api/cronograma/remover-tudo")
    def cronograma_remover_tudo():
        """Apaga TODAS as atividades e marcos do projeto (e, em cascata, tudo
        que pendura neles: dependências, relatos, histórico de status,
        vínculos com requisitos do TR, ciclos de migração, anexos). NÃO
        apaga Etapas, Frentes, Requisitos do TR nem as configurações do
        projeto — só o cronograma em si. Ação irreversível: por isso exige
        que o cliente reenvie, em `confirmacao`, o nome exato do projeto
        (o mesmo mostrado no topo da tela), conferido aqui no servidor
        contra o nome real — digitar errado ou mandar o campo vazio
        recusa o pedido antes de tocar no banco."""
        data = request.get_json(force=True)
        projeto_id = data.get("projeto_id")
        confirmacao = (data.get("confirmacao") or "").strip()
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        if not confirmacao or confirmacao != projeto["nome"]:
            return jsonify({"erro": "Confirmação não confere com o nome do projeto. Nada foi apagado."}), 400
        contagem = db.fetch_one(
            f"SELECT (SELECT COUNT(*) FROM atividades WHERE projeto_id = {db.q(projeto_id)}) AS atividades, "
            f"(SELECT COUNT(*) FROM marcos WHERE projeto_id = {db.q(projeto_id)}) AS marcos"
        ) or {"atividades": 0, "marcos": 0}
        db.execute(f"DELETE FROM atividades WHERE projeto_id = {db.q(projeto_id)}")
        db.execute(f"DELETE FROM marcos WHERE projeto_id = {db.q(projeto_id)}")
        # Ação irreversível e destrutiva — registra como evento sensível dedicado
        # (não passa por delete_row: é uma exclusão em massa, não de 1 registro).
        auditoria.registrar_evento_manual(
            "exclusao",
            f'Apagou todo o cronograma do projeto "{projeto["nome"]}" '
            f'({contagem["atividades"]} atividade(s), {contagem["marcos"]} marco(s))',
            entidade="cronograma", entidade_id=str(projeto_id), entidade_rotulo=projeto["nome"],
            projeto_id=projeto_id, sensivel=True,
            detalhes={"atividades_removidas": contagem["atividades"], "marcos_removidos": contagem["marcos"]},
        )
        return jsonify({"ok": True})

    # --------------------------------------------------- versões do cronograma
    # Histórico sob demanda (34ª rodada): NENHUMA rota de atividade/marco acima
    # toca em `cronograma_versoes` — edições normais (mudar data, criar,
    # excluir) sempre só sobrepõem os dados ao vivo, nunca geram versão
    # sozinhas. Uma versão só nasce quando o usuário clica no botão dedicado,
    # que chama o POST abaixo. Ver backend/app/cronograma_versoes.py.
    @app.get("/api/cronograma/versoes")
    def cronograma_listar_versoes():
        projeto_id = request.args.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        return jsonify(cronograma_versoes.listar_versoes(projeto_id))

    @app.get("/api/cronograma/versoes/<id>")
    def cronograma_obter_versao(id):
        versao = cronograma_versoes.obter_versao(id)
        if not versao:
            abort(404)
        return jsonify(versao)

    @app.delete("/api/cronograma/versoes/<id>")
    def cronograma_excluir_versao(id):
        """Apaga uma versão do histórico (a "foto" gerada anteriormente pelo
        botão "Gerar versão agora"). Não mexe em nada de atividades/marcos
        ao vivo — eles são independentes da versão desde que ela foi
        criada; isto só remove o registro de `cronograma_versoes`."""
        versao = cronograma_versoes.excluir_versao(id)
        if not versao:
            abort(404)
        projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(versao['projeto_id'])}")
        projeto_nome = projeto["nome"] if projeto else None
        auditoria.registrar_evento_manual(
            "exclusao",
            f'Excluiu a versão {versao["numero_versao"]} do cronograma do projeto "{projeto_nome}"'
            + (f' — "{versao["rotulo"]}"' if versao["rotulo"] else "")
            + f' ({versao["total_atividades"]} atividade(s), {versao["total_marcos"]} marco(s))',
            entidade="cronograma_versoes", entidade_id=str(versao["id"]), entidade_rotulo=projeto_nome,
            projeto_id=versao["projeto_id"], sensivel=True,
            detalhes={"numero_versao": versao["numero_versao"], "rotulo": versao["rotulo"]},
        )
        return jsonify({"ok": True})

    @app.post("/api/cronograma/versoes")
    def cronograma_gerar_versao():
        """Gera uma nova versão (foto completa e independente) do cronograma
        do projeto no instante atual — ação explícita, disparada só pelo
        botão "Gerar versão" da tela de Cronograma. Não apaga nem altera
        nada em `atividades`/`marcos`; é puramente um INSERT numa tabela à
        parte."""
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        rotulo = (data.get("rotulo") or "").strip() or None
        usuario = request.headers.get("X-Usuario", "")
        versao = cronograma_versoes.criar_versao(projeto_id, rotulo, usuario, ATIVIDADE_SELECT)
        auditoria.registrar_evento_manual(
            "criacao",
            f'Gerou a versão {versao["numero_versao"]} do cronograma do projeto "{projeto["nome"]}"'
            + (f' — "{rotulo}"' if rotulo else "")
            + f' ({versao["total_atividades"]} atividade(s), {versao["total_marcos"]} marco(s))',
            entidade="cronograma_versoes", entidade_id=str(versao["id"]), entidade_rotulo=projeto["nome"],
            projeto_id=projeto_id,
            detalhes={"numero_versao": versao["numero_versao"], "rotulo": rotulo},
        )
        return jsonify(versao), 201

    # ------------------------------------------------- replanejamento do cronograma
    # Recalcula as datas PREVISTAS das atividades incompletas a partir de uma
    # data-âncora, respeitando dependências e o quanto já foi executado — ver
    # backend/app/cronograma_replanejamento.py para a regra completa. Como
    # qualquer outra sobreposição de dado ao vivo, isso não gera versão
    # sozinho: quem quiser preservar o estado atual usa POST /cronograma/versoes
    # antes de confirmar (o front-end oferece isso no mesmo modal).
    @app.post("/api/cronograma/replanejar/preview")
    def cronograma_replanejar_preview():
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        data_ancora = data.get("data_ancora")
        if not (projeto_id and data_ancora):
            return jsonify({"erro": "projeto_id e data_ancora são obrigatórios"}), 400
        try:
            resultado = cronograma_replanejamento.calcular(projeto_id, data_ancora, STATUS_FAMILIA_CONCLUIDA)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        itens = resultado["itens"]
        alteradas = [i for i in itens if i["mudou"]]
        return jsonify({
            "data_ancora_normalizada": resultado["data_ancora_normalizada"],
            "total_atividades": len(itens),
            "total_atividades_alteradas": len(alteradas),
            "total_marcos_alterados": len(resultado["marcos"]),
            "itens": alteradas,
            "marcos": resultado["marcos"],
        })

    @app.post("/api/cronograma/replanejar/confirmar")
    def cronograma_replanejar_confirmar():
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        data_ancora = data.get("data_ancora")
        if not (projeto_id and data_ancora):
            return jsonify({"erro": "projeto_id e data_ancora são obrigatórios"}), 400
        projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        try:
            resultado = cronograma_replanejamento.aplicar(projeto_id, data_ancora, STATUS_FAMILIA_CONCLUIDA)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        auditoria.registrar_evento_manual(
            "edicao",
            f'Replanejou o cronograma do projeto "{projeto["nome"]}" a partir de '
            f'{resultado["data_ancora_normalizada"]} '
            f'({resultado["atividades_alteradas"]} atividade(s), {resultado["marcos_alterados"]} marco(s) alterados)',
            entidade="cronograma", entidade_id=str(projeto_id), entidade_rotulo=projeto["nome"],
            projeto_id=projeto_id, sensivel=True,
            detalhes={
                "data_ancora": resultado["data_ancora_normalizada"],
                "atividades_alteradas": resultado["atividades_alteradas"],
                "marcos_alterados": resultado["marcos_alterados"],
            },
        )
        return jsonify(resultado)

    # ------------------------------------------------- edição em lote do cronograma
    # Tela "Editar em massa" (39ª rodada, formato planilha): edita várias
    # atividades de uma vez e propaga o efeito só pelas sucessoras (diretas
    # e indiretas) do que foi realmente editado — ver
    # backend/app/cronograma_edicao_lote.py para a regra completa. Mesmo
    # padrão preview/confirmar do replanejamento acima, e também não gera
    # versão sozinha (o front-end oferece gerar uma antes, no mesmo modal).
    @app.post("/api/cronograma/edicao-lote/preview")
    def cronograma_edicao_lote_preview():
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        edicoes = data.get("edicoes") or []
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        if not edicoes:
            return jsonify({"erro": "Nenhuma alteração informada."}), 400
        try:
            resultado = cronograma_edicao_lote.calcular(
                projeto_id, edicoes, STATUS_FAMILIA_CONCLUIDA, PRIORIDADE_VALIDAS,
                classificar_conclusao, validar_consistencia_conclusao,
            )
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        itens = resultado["itens"]
        return jsonify({
            "total_editadas": sum(1 for i in itens if i["editado"]),
            "total_cascata": sum(1 for i in itens if not i["editado"]),
            "itens": itens,
        })

    @app.post("/api/cronograma/edicao-lote/confirmar")
    def cronograma_edicao_lote_confirmar():
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        edicoes = data.get("edicoes") or []
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        if not edicoes:
            return jsonify({"erro": "Nenhuma alteração informada."}), 400
        projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        # autor_nome (texto livre, mesma coluna que o relato manual usa quando o
        # autor não está cadastrado em `recursos` — usuário de sistema e recurso
        # são cadastros diferentes, sem vínculo entre si) identifica quem confirmou
        # a edição em massa, nos relatos de progresso gerados automaticamente por
        # esta tela (% concluído/datas reais — ver cronograma_edicao_lote.py).
        usuario_atual = auth.buscar_usuario_publico(session.get("usuario_id"))
        try:
            resultado = cronograma_edicao_lote.aplicar(
                projeto_id, edicoes, STATUS_FAMILIA_CONCLUIDA, PRIORIDADE_VALIDAS,
                classificar_conclusao, validar_consistencia_conclusao,
                autor_nome=(usuario_atual or {}).get("nome"),
            )
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        auditoria.registrar_evento_manual(
            "edicao",
            f'Editou em massa {resultado["atividades_editadas"]} atividade(s) do cronograma do projeto '
            f'"{projeto["nome"]}"'
            + (f', com efeito em cascata em mais {resultado["atividades_cascata"]} atividade(s) dependente(s)'
               if resultado["atividades_cascata"] else '')
            + (f', com {resultado["relatos_criados"]} relato(s) de progresso registrado(s)'
               if resultado.get("relatos_criados") else '') + '.',
            entidade="cronograma", entidade_id=str(projeto_id), entidade_rotulo=projeto["nome"],
            projeto_id=projeto_id, sensivel=True,
            detalhes={
                "atividades_editadas": resultado["atividades_editadas"],
                "atividades_cascata": resultado["atividades_cascata"],
                "ids_alterados": resultado["ids_alterados"],
                "relatos_criados": resultado.get("relatos_criados", 0),
            },
        )
        return jsonify(resultado)

    # ------------------------------------------------------------------ riscos
    @app.get("/api/riscos")
    def list_riscos():
        pid = request.args.get("projeto_id")
        sql = (
            "SELECT ri.*, re.nome AS responsavel_nome FROM riscos ri "
            "LEFT JOIN recursos re ON re.id = ri.responsavel_id"
        )
        if pid:
            sql += f" WHERE ri.projeto_id = {db.q(pid)}"
        sql += " ORDER BY ri.identificado_em DESC"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/riscos")
    def create_risco():
        return jsonify(insert_row("riscos", request.get_json(force=True), RISCO_FIELDS)), 201

    @app.put("/api/riscos/<id>")
    def update_risco(id):
        row = patch_row("riscos", id, request.get_json(force=True), RISCO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    # --------------------------------------------------------------- pendências
    # 41ª rodada — ver preparar_pendencia() acima para as regras de validação.
    # Sem rota de exclusão (mesmo padrão de riscos/marcos/requisitos) — usar
    # status "Cancelada" em vez de apagar.
    @app.get("/api/pendencias")
    def list_pendencias():
        pid = request.args.get("projeto_id")
        sql = (
            "SELECT p.*, "
            "f.nome AS frente_nome, "
            "a.nome AS atividade_nome, a.codigo_wbs AS atividade_codigo_wbs, "
            "ri.descricao AS risco_descricao, "
            "rc.nome AS responsavel_consultoria_nome, rcli.nome AS responsavel_cliente_nome, "
            "(p.status NOT IN ('Resolvida','Cancelada') AND p.data_limite < CURRENT_DATE) AS atrasada "
            "FROM pendencias p "
            "JOIN frentes_trabalho f ON f.id = p.frente_trabalho_id "
            "LEFT JOIN atividades a ON a.id = p.atividade_id "
            "LEFT JOIN riscos ri ON ri.id = p.risco_id "
            "JOIN recursos rc ON rc.id = p.responsavel_consultoria_id "
            "JOIN recursos rcli ON rcli.id = p.responsavel_cliente_id"
        )
        if pid:
            sql += f" WHERE p.projeto_id = {db.q(pid)}"
        sql += " ORDER BY p.data_limite ASC, p.criado_em DESC"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/pendencias")
    def create_pendencia():
        data = request.get_json(force=True)
        obrigatorios = ["projeto_id", "titulo", "frente_trabalho_id", "descricao",
                        "responsavel_consultoria_id", "responsavel_cliente_id", "data_limite"]
        faltando = [c for c in obrigatorios if not data.get(c)]
        if faltando:
            return jsonify({"erro": "Informe identificação, frente, descrição, responsáveis (consultoria e cliente) e data limite."}), 400
        try:
            preparar_pendencia(data, data["projeto_id"])
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        data = dict(data)
        data["codigo"] = _proximo_codigo_pendencia(data["projeto_id"])
        return jsonify(insert_row("pendencias", data, PENDENCIA_FIELDS)), 201

    @app.put("/api/pendencias/<id>")
    def update_pendencia(id):
        atual = db.fetch_one(f"SELECT projeto_id FROM pendencias WHERE id = {db.q(id)}")
        if not atual:
            abort(404)
        data = request.get_json(force=True)
        try:
            preparar_pendencia(data, atual["projeto_id"])
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        data = dict(data)
        data.pop("codigo", None)  # código nunca muda depois de gerado
        row = patch_row("pendencias", id, data, PENDENCIA_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    # ------------------------------------------------------------------ anexos
    @app.post("/api/anexos")
    def upload_anexo():
        entidade_tipo = request.form.get("entidade_tipo")
        entidade_id = request.form.get("entidade_id")
        enviado_por = request.form.get("enviado_por", "")
        file = request.files.get("file")
        if not (entidade_tipo and entidade_id and file):
            return jsonify({"erro": "entidade_tipo, entidade_id e file são obrigatórios"}), 400
        ext = os.path.splitext(file.filename)[1]
        stored_name = f"{uuid.uuid4()}{ext}"
        path = os.path.join(UPLOAD_DIR, stored_name)
        file.save(path)
        sql = (
            "INSERT INTO anexos (entidade_tipo, entidade_id, nome_original, caminho_arquivo, tipo_mime, "
            "tamanho_bytes, enviado_por) VALUES ("
            f"{db.q(entidade_tipo)}, {db.q(entidade_id)}, {db.q(file.filename)}, {db.q(stored_name)}, "
            f"{db.q(file.mimetype)}, {os.path.getsize(path)}, {db.q(enviado_por)}) RETURNING *"
        )
        return jsonify(db.execute_returning_one(sql)), 201

    @app.get("/api/anexos")
    def list_anexos():
        et, eid = request.args.get("entidade_tipo"), request.args.get("entidade_id")
        sql = "SELECT * FROM anexos"
        if et and eid:
            sql += f" WHERE entidade_tipo = {db.q(et)} AND entidade_id = {db.q(eid)}"
        sql += " ORDER BY enviado_em DESC"
        return jsonify(db.fetch_all(sql))

    @app.get("/api/anexos/<id>/download")
    def download_anexo(id):
        row = db.fetch_one(f"SELECT * FROM anexos WHERE id = {db.q(id)}")
        if not row:
            abort(404)
        return send_file(os.path.join(UPLOAD_DIR, row["caminho_arquivo"]), download_name=row["nome_original"], as_attachment=True)

    @app.delete("/api/anexos/<id>")
    def delete_anexo(id):
        row = db.fetch_one(f"SELECT * FROM anexos WHERE id = {db.q(id)}")
        if row:
            try:
                os.remove(os.path.join(UPLOAD_DIR, row["caminho_arquivo"]))
            except OSError:
                pass
        db.execute(f"DELETE FROM anexos WHERE id = {db.q(id)}")
        return "", 204

    # -------------------------------------------------------------- relatórios
    @app.get("/api/relatorios/periodo")
    def relatorio_periodo():
        pid, ini, fim = request.args.get("projeto_id"), request.args.get("inicio"), request.args.get("fim")
        if not (pid and ini and fim):
            return jsonify({"erro": "projeto_id, inicio e fim são obrigatórios"}), 400
        sql = (
            ATIVIDADE_SELECT
            + f" WHERE a.projeto_id = {db.q(pid)} AND a.dtini_prev <= {db.q(fim)} AND a.dtfim_prev >= {db.q(ini)}"
            + " ORDER BY a.dtini_prev"
        )
        return jsonify(db.fetch_all(sql))

    @app.get("/api/relatorios/atrasadas")
    def relatorio_atrasadas():
        pid = request.args.get("projeto_id")
        sql = "SELECT * FROM vw_atividades_atrasadas"
        if pid:
            sql += f" WHERE projeto_id = {db.q(pid)}"
        sql += " ORDER BY dias_atraso DESC"
        return jsonify(db.fetch_all(sql))

    @app.get("/api/relatorios/resumo-frentes")
    def relatorio_resumo_frentes():
        pid = request.args.get("projeto_id")
        sql = "SELECT * FROM vw_resumo_frente"
        if pid:
            sql += f" WHERE projeto_id = {db.q(pid)}"
        sql += " ORDER BY frente_nome"
        return jsonify(db.fetch_all(sql))

    @app.get("/api/relatorios/atividades-master")
    def relatorio_atividades_master():
        """Previsão de conclusão das atividades marcadas como ★ master (ver
        atividades.eh_atividade_master), pro card do Dashboard — reaproveita
        relatorio_executivo.coletar_dados_projeto()/_projetar_datas() (a mesma
        reprojeção "conforme o andamento" usada no Relatório Executivo), sem
        chamar IA nenhuma: é só a parte numérica/determinística daquele
        cálculo, então pode ser recarregada toda vez que o Dashboard abre, de
        graça. "fim_previsto_no_plano" é a data do cronograma (dtfim_prev,
        nunca muda sozinha); "fim_projetado" é a reprojeção a partir do que
        já aconteceu de verdade (conclusões, % executado, bloqueios,
        propagando atraso pelas dependências) — pode ser bem diferente da
        primeira quando o projeto está atrasado."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        try:
            dados = relatorio_executivo.coletar_dados_projeto(pid)
        except relatorio_executivo.RelatorioExecutivoError as e:
            return jsonify({"erro": str(e)}), 404
        return jsonify({"gerado_em": dados["gerado_em"], "atividades_master": dados["atividades_master"]})

    # ------------------------------------------------- relatório executivo (IA)
    @app.get("/api/relatorios/executivo")
    def relatorio_executivo_listar():
        """Histórico (sem o conteúdo completo, só o essencial pra montar uma lista
        de 'relatórios anteriores' no Dashboard)."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        sql = (
            f"SELECT id, gerado_em, modelo_ia, gerado_por FROM relatorios_executivos "
            f"WHERE projeto_id = {db.q(pid)} ORDER BY gerado_em DESC LIMIT 20"
        )
        return jsonify(db.fetch_all(sql))

    @app.get("/api/relatorios/executivo/<id>")
    def relatorio_executivo_obter(id):
        row = db.fetch_one(f"SELECT * FROM relatorios_executivos WHERE id = {db.q(id)}")
        if not row:
            abort(404)
        return jsonify(row)

    @app.post("/api/relatorios/executivo/gerar")
    def relatorio_executivo_gerar():
        data = request.get_json(force=True)
        projeto_id = data.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        gerado_por = data.get("gerado_por")
        print(f"[relatorio_executivo] iniciando geração para projeto {projeto_id}...", flush=True)
        try:
            row = relatorio_executivo.gerar_relatorio(projeto_id, gerado_por=gerado_por)
        except relatorio_executivo.RelatorioExecutivoError as e:
            print(f"[relatorio_executivo] erro esperado: {e}", flush=True)
            return jsonify({"erro": str(e)}), 400
        except db.DbError:
            raise
        except Exception as e:
            print(f"[relatorio_executivo] ERRO INESPERADO: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha inesperada ao gerar o relatório: {e}"}), 500
        print(f"[relatorio_executivo] relatório {row['id']} gerado com sucesso.", flush=True)
        return jsonify(row), 201

    @app.get("/api/relatorios/executivo/<id>/pdf")
    def relatorio_executivo_pdf(id):
        row = db.fetch_one(f"SELECT * FROM relatorios_executivos WHERE id = {db.q(id)}")
        if not row:
            abort(404)
        if isinstance(row.get("dados_enviados"), str):
            row["dados_enviados"] = json.loads(row["dados_enviados"])
        try:
            pdf_bytes = relatorio_pdf.gerar_pdf_bytes(row)
        except Exception as e:
            print(f"[relatorio_executivo] erro ao gerar PDF do relatório {id}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao gerar o PDF: {e}"}), 500
        nome_arquivo = f"relatorio-executivo-{row['gerado_em'][:10] if isinstance(row['gerado_em'], str) else id}.pdf"
        return send_file(
            io.BytesIO(pdf_bytes), mimetype="application/pdf",
            as_attachment=True, download_name=nome_arquivo,
        )

    # ------------------------------------ relatório de minhas atividades
    # Apontamento de horas (previsto x realizado) lançado em "Minhas
    # atividades", filtrável por consultor e período — gerado na hora
    # (sem persistir nada, ao contrário do Relatório Executivo (IA)), em
    # PDF ou planilha Excel. Ver app/relatorio_atividades.py.
    def _validar_periodo_relatorio_atividades():
        data_ini, data_fim = request.args.get("data_ini"), request.args.get("data_fim")
        if not data_ini or not data_fim:
            return None, None, (jsonify({"erro": "Informe o período (data início e fim, ou mês/ano)."}), 400)
        if data_fim < data_ini:
            return None, None, (jsonify({"erro": "A data fim não pode ser anterior à data início."}), 400)
        return data_ini, data_fim, None

    @app.get("/api/relatorios/minhas-atividades/pdf")
    def relatorio_atividades_pdf():
        data_ini, data_fim, erro = _validar_periodo_relatorio_atividades()
        if erro:
            return erro
        recurso_id = request.args.get("recurso_id") or None
        try:
            dados = relatorio_atividades.coletar_dados(recurso_id, data_ini, data_fim)
            pdf_bytes = relatorio_atividades.gerar_pdf_bytes(dados)
        except Exception as e:
            print(f"[relatorio_atividades] erro ao gerar PDF: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao gerar o PDF: {e}"}), 500
        nome_arquivo = f"relatorio-atividades-{data_ini}-a-{data_fim}.pdf"
        return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                          as_attachment=True, download_name=nome_arquivo)

    @app.get("/api/relatorios/minhas-atividades/planilha")
    def relatorio_atividades_planilha():
        data_ini, data_fim, erro = _validar_periodo_relatorio_atividades()
        if erro:
            return erro
        recurso_id = request.args.get("recurso_id") or None
        try:
            dados = relatorio_atividades.coletar_dados(recurso_id, data_ini, data_fim)
            xlsx_bytes = relatorio_atividades.gerar_planilha_bytes(dados)
        except Exception as e:
            print(f"[relatorio_atividades] erro ao gerar planilha: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao gerar a planilha: {e}"}), 500
        nome_arquivo = f"relatorio-atividades-{data_ini}-a-{data_fim}.xlsx"
        return send_file(
            io.BytesIO(xlsx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=nome_arquivo,
        )

    # ------------------------------------------------- calendário de feriados
    # Configurações > Calendário de Feriados: exceções de dia útil por projeto
    # (tabela calendario_util, já existia desde as primeiras rodadas — usada pelo
    # CPM e por "Minhas atividades" para pular feriados/recessos ao distribuir
    # dias úteis — mas até agora não tinha nenhuma tela nem rota própria).
    @app.get("/api/calendario")
    def list_calendario():
        pid = request.args.get("projeto_id")
        sql = "SELECT * FROM calendario_util"
        if pid:
            sql += f" WHERE projeto_id = {db.q(pid)}"
        sql += " ORDER BY data"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/calendario")
    def create_calendario():
        data = request.get_json(force=True)
        if not (data.get("projeto_id") and data.get("data")):
            return jsonify({"erro": "Projeto e data são obrigatórios."}), 400
        return jsonify(insert_row("calendario_util", data, CALENDARIO_FIELDS)), 201

    @app.put("/api/calendario/<id>")
    def update_calendario(id):
        row = patch_row("calendario_util", id, request.get_json(force=True), CALENDARIO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/calendario/<id>")
    def delete_calendario(id):
        return delete_row("calendario_util", id)

    @app.post("/api/calendario/duplicar")
    def duplicar_calendario():
        """Copia os feriados de um projeto "modelo" para um ou mais projetos
        destino — pensado pro caso de vários projetos em cidades/estados
        diferentes, que têm o feriado nacional em comum mas precisam de
        feriados estaduais/municipais próprios além dele. Segue a mesma
        filosofia já usada na reimportação de cronograma: só ADICIONA datas que
        o destino ainda não tem, nunca sobrescreve nem remove um feriado que o
        destino já tinha cadastrado por conta própria — datas repetidas
        (mesmo projeto_id + data já existente) são só contadas e ignoradas."""
        body = request.get_json(force=True) or {}
        origem_id = body.get("projeto_origem_id")
        destino_ids = [d for d in (body.get("projetos_destino_ids") or []) if d and d != origem_id]
        if not origem_id or not destino_ids:
            return jsonify({"erro": "Informe o projeto de origem e ao menos um projeto de destino."}), 400

        origem_feriados = db.fetch_all(
            f"SELECT data, util, descricao FROM calendario_util WHERE projeto_id = {db.q(origem_id)}"
        )
        if not origem_feriados:
            return jsonify({"erro": "O projeto de origem não tem nenhum feriado cadastrado."}), 400

        resultado = []
        for destino_id in destino_ids:
            existentes = {
                r["data"] for r in db.fetch_all(
                    f"SELECT data FROM calendario_util WHERE projeto_id = {db.q(destino_id)}"
                )
            }
            novos = [f for f in origem_feriados if f["data"] not in existentes]
            if novos:
                values_sql = ", ".join(
                    "(" + db.q(destino_id) + ", " + db.q(f["data"]) + ", " + db.q(f["util"]) + ", " + db.q(f.get("descricao")) + ")"
                    for f in novos
                )
                db.execute(
                    f"INSERT INTO calendario_util (projeto_id, data, util, descricao) VALUES {values_sql} "
                    "ON CONFLICT (projeto_id, data) DO NOTHING"
                )
            projeto = db.fetch_one(f"SELECT sigla, nome FROM projetos WHERE id = {db.q(destino_id)}")
            resultado.append({
                "projeto_id": destino_id,
                "projeto_nome": (projeto or {}).get("sigla") or (projeto or {}).get("nome") or "—",
                "copiados": len(novos),
                "ja_existentes": len(origem_feriados) - len(novos),
            })
        return jsonify({"resultado": resultado})

    # -------------------------------------------------------------------- cpm
    @app.post("/api/cpm/recalcular")
    def cpm_recalcular():
        pid = (request.get_json(silent=True) or {}).get("projeto_id") or request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        try:
            resultado = cpm.recalcular(pid)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        return jsonify(resultado)

    @app.get("/api/cpm/caminho-critico")
    def cpm_caminho_critico():
        pid = request.args.get("projeto_id")
        sql = "SELECT * FROM vw_caminho_critico"
        if pid:
            sql += f" WHERE projeto_id = {db.q(pid)}"
        return jsonify(db.fetch_all(sql))

    # -------------------------------------------------------------- documentação
    # Configurações > Documentação: dois documentos vivos do próprio sistema
    # (Documento Executivo e Documento Técnico), guardados no banco (tabela
    # `documentacao`, ver db/migration_014_documentacao.sql) em vez de fixos
    # no frontend, para poderem ser atualizados sem precisar de um novo
    # deploy. O PDF é gerado no navegador (impressão), sem rota de backend
    # dedicada. Sem hierarquia de perfil no sistema (ver seção 4 do Documento
    # Técnico), o PUT abaixo fica acessível a qualquer usuário autenticado,
    # igual a todo o resto das rotas de Configurações.
    DOCUMENTACAO_TIPOS = {"executivo", "tecnico"}

    @app.get("/api/documentacao")
    def listar_documentacao():
        """Lista só os metadados (sem o conteúdo, que pode ser grande) —
        usada pela tela de listagem em Configurações > Documentação."""
        return jsonify(db.fetch_all(
            "SELECT tipo, titulo, versao, atualizado_em FROM documentacao ORDER BY tipo"
        ))

    @app.get("/api/documentacao/<tipo>")
    def obter_documentacao(tipo):
        if tipo not in DOCUMENTACAO_TIPOS:
            abort(404)
        row = db.fetch_one(f"SELECT * FROM documentacao WHERE tipo = {db.q(tipo)}")
        if not row:
            abort(404)
        return jsonify(row)

    @app.put("/api/documentacao/<tipo>")
    def atualizar_documentacao(tipo):
        if tipo not in DOCUMENTACAO_TIPOS:
            abort(404)
        data = request.get_json(force=True)
        sets = []
        if "titulo" in data:
            sets.append(f"titulo = {db.q(data['titulo'])}")
        if "versao" in data:
            sets.append(f"versao = {db.q(data['versao'])}")
        if "conteudo_md" in data:
            sets.append(f"conteudo_md = {db.q(data['conteudo_md'])}")
        if not sets:
            return jsonify({"erro": "Nada para atualizar."}), 400
        sets.append("atualizado_em = now()")
        sql = f"UPDATE documentacao SET {', '.join(sets)} WHERE tipo = {db.q(tipo)} RETURNING *"
        row = db.execute_returning_one(sql)
        if not row:
            abort(404)
        return jsonify(row)

    # -------------------------------------------------------------- log de auditoria
    # Configurações > Log de Auditoria (32ª rodada). Sem hierarquia de perfil no
    # sistema (mesma observação da seção de Documentação acima), visível a
    # qualquer usuário autenticado. Por padrão a listagem/resumo respeitam o
    # projeto ativo (projeto_id do filtro) + eventos globais (projeto_id NULL:
    # recurso, tipo de atividade, usuário, manual, login/logout) — ver
    # auditoria._where_filtros. Passar sem projeto_id enxerga tudo.
    def _filtros_log_auditoria():
        return {
            "projeto_id": request.args.get("projeto_id") or None,
            "tipo_evento": request.args.get("tipo_evento") or None,
            "entidade": request.args.get("entidade") or None,
            "usuario_id": request.args.get("usuario_id") or None,
            "somente_sensiveis": request.args.get("somente_sensiveis") == "1",
            "busca": request.args.get("busca") or None,
            "inicio": request.args.get("inicio") or None,
            "fim": request.args.get("fim") or None,
        }

    @app.get("/api/log-auditoria")
    def listar_log_auditoria():
        filtros = _filtros_log_auditoria()
        pagina = int(request.args.get("pagina") or 1)
        tamanho_pagina = min(int(request.args.get("tamanho_pagina") or 50), 200)
        return jsonify({
            "itens": auditoria.listar(pagina=pagina, tamanho_pagina=tamanho_pagina, **filtros),
            "total": auditoria.contar(**filtros),
            "pagina": pagina,
            "tamanho_pagina": tamanho_pagina,
        })

    @app.get("/api/log-auditoria/resumo")
    def resumo_log_auditoria():
        projeto_id = request.args.get("projeto_id") or None
        dias = int(request.args.get("dias") or 30)
        return jsonify(auditoria.resumo(projeto_id=projeto_id, dias=dias))

    @app.get("/api/log-auditoria/entidade/<entidade>/<entidade_id>")
    def historico_entidade_log_auditoria(entidade, entidade_id):
        return jsonify(auditoria.historico_entidade(entidade, entidade_id))

    @app.get("/api/log-auditoria/export")
    def exportar_log_auditoria():
        filtros = _filtros_log_auditoria()
        cabecalho, linhas = auditoria.exportar_csv(**filtros)
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(cabecalho)
        writer.writerows(linhas)
        resp = app.response_class(buf.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = "attachment; filename=log_auditoria.csv"
        return resp

    # -------------------------------------------------------------- error handling
    @app.errorhandler(db.DbError)
    def handle_db_error(e):
        msg = str(e)
        baixo = msg.lower()
        if "violates foreign key constraint" in baixo:
            return jsonify({
                "erro": "Não é possível excluir: existem outros registros vinculados a este item "
                        "(ex: atividades usando esta etapa/frente/tipo/recurso). "
                        "Marque como inativo em vez de excluir, se possível.",
            }), 409
        if "duplicate key value violates unique constraint" in baixo:
            return jsonify({"erro": "Já existe um registro com esses mesmos dados (código/nome duplicado)."}), 409
        return jsonify({"erro": msg}), 500

    # ------------------------------------------------------------ front-end estático
    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/<path:path>")
    def static_files(path):
        full = os.path.join(FRONTEND_DIR, path)
        if os.path.isfile(full):
            return send_from_directory(FRONTEND_DIR, path)
        return send_from_directory(FRONTEND_DIR, "index.html")

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
