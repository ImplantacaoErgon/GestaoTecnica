import base64
import csv
import io
import json
import os
import threading
import traceback
import uuid
from datetime import datetime, date, timedelta

from flask import Flask, request, jsonify, send_from_directory, send_file, abort, session

from . import db, cpm, tr_parser, cronograma_import, cronograma_versoes, cronograma_replanejamento, cronograma_edicao_lote, cronograma_renumeracao, cronograma_anomalias, cronograma_export, cronograma_export_xml, cronograma_comparacao, relatorio_executivo, relatorio_pdf, auth, minhas_atividades, relatorio_atividades, manuais, auditoria, rubricas_import, drive_rubricas, rubricas_auto_update, comparacao_folha, comparacao_folha_import, google_drive, migracao_quadro_export, requisitos_ia, migracao_rejeicoes, migracao_rejeicoes_import

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
# 74ª rodada (migração 033) — "Tipo de Restrição", padrão MS Project/ProjectLibre
# (pedido do usuário com print da tela de restrição do MS Project/ProjectLibre em
# anexo). Os dois primeiros ("...possível") nunca têm data associada — ver
# TIPOS_RESTRICAO_SEM_DATA, usado pela cascata da Edição em Lote (cronograma_edicao_lote.py)
# pra zerar data_restricao sozinha quando o tipo muda pra um desses.
TIPOS_RESTRICAO_VALIDOS = [
    "O Mais Breve Possível", "O mais tarde possível",
    "Deve iniciar em", "Deve terminar em",
    "Não iniciar antes de", "Não iniciar depois de",
    "Não terminar antes de", "Não terminar depois de",
]
TIPOS_RESTRICAO_SEM_DATA = {"O Mais Breve Possível", "O mais tarde possível"}

ATIVIDADE_FIELDS = [
    "projeto_id", "etapa_id", "frente_trabalho_id", "tipo_atividade_elementar_id", "atividade_pai_id",
    "requisito_tr_id",
    "codigo_wbs", "origem_importacao_id", "nome", "descricao", "objetivo",
    "prazo_horas", "horas_realizadas", "dtini_prev", "dtfim_prev",
    "dtini_real", "dtfim_real", "percentual_concluido", "status", "prioridade", "observacoes",
    "eh_atividade_master", "eh_entregavel",
    # 57ª/58ª rodada (migração 031): trava dtini_prev/dtfim_prev contra recálculo automático
    # por dependência (Replanejamento e cascata da Edição em Lote) — só edição manual direta
    # (aqui mesmo) pode mudar a data de uma atividade fixada. Ver app/cronograma_anomalias.py.
    "data_execucao_fixada",
    # 74ª rodada (migração 033) — Tipo de Restrição/Data da restrição (padrão MS
    # Project/ProjectLibre) + 15 campos genéricos (uso livre, sem significado
    # pré-definido) pedidos pra poderem ser usados diretamente no Cronograma.
    "tipo_restricao", "data_restricao",
    "numero_1", "numero_2", "numero_3", "numero_4", "numero_5",
    "booleano_1", "booleano_2", "booleano_3", "booleano_4", "booleano_5",
    "texto_1", "texto_2", "texto_3", "texto_4", "texto_5",
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
# 53ª rodada — mesma lógica de REQUISITO_UPSERT_COLUMNS, agora para a importação da
# planilha de rubricas (ver app/rubricas_import.py). linha_planilha não entra na lista
# de SET do upsert (é a própria chave de conflito), mas entra no INSERT — por isso é
# tratada à parte em upsert_rubricas(), não incluída aqui.
RUBRICA_UPSERT_COLUMNS = [
    "analista", "verba_legado", "descricao_legado", "codigo_ergon", "nome_abreviado",
    "nome_extenso", "tipo", "grupo_calculo", "ordem_grupo_calculo", "legislacao",
    "valor_formula", "quant_autorizado", "fato_origem",
    "validacao_compatibilidade", "validacao_incompatibilidade",
    "regime_vinculo_direito", "categoria_cargo_direito", "secretaria",
    "outras_condicoes", "incompatibilidade", "periodicidade", "totalizacao", "regra_negocio",
    "empresas", "incidencias",
    "status", "situacao_planilha", "data_levantamento", "revisado_por",
    "questionamentos_juridico", "data_envio_techne", "consultor_techne",
    "data_liberacao_testes", "observacoes_liberacao", "condicoes_minimas_testes",
    "questionamentos_consultor", "responsavel_homologacao", "data_inicio_homologacao",
    "data_homologacao", "observacoes_homologacao", "observacoes_finais", "notas_importacao",
]
# Campos editáveis pela tela de detalhe (CRUD manual) — igual à lista de upsert, mais
# atividade_id (vínculo com a Frente de Trabalho/atividade, que a importação não seta
# sozinha) e sem linha_planilha (só a importação atribui isso).
RUBRICA_FIELDS = ["projeto_id", "atividade_id"] + RUBRICA_UPSERT_COLUMNS
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
CICLO_FIELDS = ["item_migracao_id", "atividade_id", "numero_ciclo", "data_execucao", "qtd_registros_extraidos",
                "qtd_registros_carregados", "qtd_rejeicoes", "observacoes"]
# 48ª rodada: catálogo de itens de migração (tabelas do legado a migrar pro
# Ergon) — cada item tem uma meta (qtd_registros_estimada) e, através dos
# ciclos_migracao vinculados a ele (item_migracao_id), quanto já foi
# carregado. atividade_id aqui é o vínculo opcional com o cronograma (a
# atividade de migração que contém este item), não obrigatório.
ITEM_MIGRACAO_FIELDS = [
    "projeto_id", "atividade_id", "nome_tabela_legado", "nome_tabela_destino",
    "sistema_origem", "qtd_registros_estimada", "status", "responsavel_id", "observacoes",
]
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


def upsert_rubricas(projeto_id, linhas):
    """linhas: lista de dicts com chaves de RUBRICA_UPSERT_COLUMNS + "linha_planilha"
    (ausentes -> NULL). Faz upsert por (projeto_id, linha_planilha) — reimportar a
    mesma planilha atualiza em vez de duplicar (ver comentário em
    db/migration_029_rubricas.sql sobre por que NÃO é por código ERGON/verba: a mesma
    regra pode aparecer em mais de uma linha, uma por empresa). Deduplica
    linha_planilha repetida dentro do próprio lote pelo mesmo motivo que
    upsert_requisitos deduplica código. Retorna (inseridos, atualizados)."""
    por_linha = {}
    for l in linhas:
        if l.get("linha_planilha"):
            por_linha[l["linha_planilha"]] = l
    linhas = list(por_linha.values())
    if not linhas:
        return 0, 0

    existentes = {
        r["linha_planilha"] for r in db.fetch_all(
            f"SELECT linha_planilha FROM rubricas WHERE projeto_id = {db.q(projeto_id)} "
            "AND linha_planilha IS NOT NULL"
        )
    }
    inseridos = sum(1 for l in linhas if l["linha_planilha"] not in existentes)
    atualizados = len(linhas) - inseridos

    colunas = ["linha_planilha"] + RUBRICA_UPSERT_COLUMNS
    values_sql = ", ".join(
        "(" + db.q(projeto_id) + ", " + ", ".join(_valor_sql(l.get(col)) for col in colunas) + ")"
        for l in linhas
    )
    set_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in RUBRICA_UPSERT_COLUMNS)
    sql = (
        f"INSERT INTO rubricas (projeto_id, {', '.join(colunas)}) VALUES "
        + values_sql
        + f" ON CONFLICT (projeto_id, linha_planilha) WHERE linha_planilha IS NOT NULL "
        + f"DO UPDATE SET {set_clause}"
    )
    db.execute(sql, timeout=120)
    return inseridos, atualizados


def _rubricas_preview_resultado(projeto_id, conteudo_bytes, nome_arquivo):
    """Lógica de prévia compartilhada pelos dois jeitos de importar
    rubricas (upload manual e busca automática no Google Drive — 54ª
    rodada): faz o parse da planilha e marca quais linhas já existem no
    projeto, sem gravar nada no banco. Levanta rubricas_import.RubricasImportError
    (ou outra exceção de leitura) se a planilha não for válida — quem chama
    decide o código HTTP."""
    resultado = rubricas_import.parse_rubricas_document(conteudo_bytes, nome_arquivo or "")
    existentes = {
        r["linha_planilha"] for r in db.fetch_all(
            f"SELECT linha_planilha FROM rubricas WHERE projeto_id = {db.q(projeto_id)} "
            "AND linha_planilha IS NOT NULL"
        )
    }
    for it in resultado["itens"]:
        it["ja_existe"] = it["linha_planilha"] in existentes
    resultado["resumo"]["ja_existentes"] = sum(1 for it in resultado["itens"] if it["ja_existe"])
    return resultado


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


def _valor_sql(v):
    """Igual a db.q(), mas serializa dict/list (colunas jsonb, ex: rubricas.empresas/
    incidencias — 53ª rodada) como JSON + cast ::jsonb em vez de cair no str(dict) do
    Python (que usa aspas simples e quebraria o parser de JSON do Postgres)."""
    if isinstance(v, (dict, list)):
        return db.q(json.dumps(v, ensure_ascii=False)) + "::jsonb"
    return db.q(v)


def insert_row(table, data, allowed):
    cols, vals = [], []
    for f in allowed:
        if f in data and data[f] not in (None, ""):
            cols.append(f)
            vals.append(_valor_sql(data[f]))
    if not cols:
        raise ValueError("Nenhum campo válido informado.")
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)}) RETURNING *"
    row = db.execute_returning_one(sql)
    # Gancho de auditoria (32ª rodada) — ver auditoria.py. Ponto único: cobre
    # toda criação que passa por aqui, sem precisar tocar em cada rota.
    auditoria.registrar_criacao(table, row)
    return row


def patch_row(table, row_id, data, allowed, prelude=""):
    sets = [f"{f} = {_valor_sql(data[f])}" for f in allowed if f in data]
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


def _normaliza_data(v):
    if v in (None, ""):
        return None
    return v.isoformat() if hasattr(v, "isoformat") else v


def _normalizar_restricao(data):
    """74ª rodada — mesma regra usada na cascata da Edição em Lote
    (cronograma_edicao_lote._validar_campos_simples): um tipo_restricao
    "...possível" (O Mais Breve Possível / O mais tarde possível) nunca tem
    data associada — zera data_restricao sozinho aqui pra não depender só
    do front-end (que já desabilita/limpa o campo na tela) pra manter essa
    regra consistente com quem chamar a API diretamente."""
    if data.get("tipo_restricao") in TIPOS_RESTRICAO_SEM_DATA:
        data["data_restricao"] = None


def _cascatear_edicao_individual(projeto_id, edicao, excluir_id=None, origem_rotulo=None):
    """71ª rodada — cascata automática de datas pras SUCESSORAS (diretas e
    indiretas) de uma atividade editada FORA da tela "Editar em massa": pelo
    modal individual (esforço/datas previstas) ou por uma dependência
    avulsa adicionada/removida ali mesmo. Até aqui só a Edição em Lote e o
    Replanejamento propagavam esse efeito — o modal individual, que é o
    jeito mais comum de mexer numa atividade, fazia um UPDATE cego, sem
    tocar em nada além da própria linha. Ver análise completa na 71ª
    rodada (gestao-tecnica-plano-16b-continuacao-45.md).

    Reaproveita o MESMO motor de grafo de dependências da Edição em Lote
    (`cronograma_edicao_lote.calcular`), só que com uma lista de edição de
    UM item só — `edicao` é literalmente uma linha do payload daquela tela
    (`{"id":..., "dtini_prev":..., "dtfim_prev":..., "dependencias_adicionar":...}`
    etc.), o motor já sabe calcular a cascata a partir disso sem precisar
    de nenhuma lógica nova. `excluir_id`, quando informado, tira a própria
    atividade editada do que é gravado aqui — usada pelo modal individual,
    onde a linha em si já foi salva segundos antes com o valor exato que o
    usuário digitou (inclusive esforço, que a Edição em Lote recalcularia
    sozinha a partir da data se os dois fossem gravados juntos); a
    dependência avulsa NÃO usa essa exclusão, porque ali a própria
    atividade editada pode ter sua posição deslocada pela predecessora
    nova/removida, e isso também precisa ser gravado.

    Nunca bloqueia quem chamou: qualquer impedimento (atividade concluída/
    cancelada, ainda sem Início previsto, ciclo no grafo etc.) é engolido
    em silêncio — o que disparou esta chamada já foi salvo com sucesso
    antes dela ser chamada; a cascata é sempre "melhor esforço", nunca uma
    condição para o salvamento principal ter sucedido."""
    try:
        resultado = cronograma_edicao_lote.calcular(
            projeto_id, [edicao], STATUS_FAMILIA_CONCLUIDA, PRIORIDADE_VALIDAS,
        )
    except ValueError:
        return None
    afetadas = [i for i in resultado["itens"] if i["mudou"] and i["id"] != excluir_id]
    if not afetadas:
        return None
    stmts = ["BEGIN;"]
    for i in afetadas:
        stmts.append(
            f"UPDATE atividades SET dtini_prev={db.q(i['dtini_prev_novo'])}, "
            f"dtfim_prev={db.q(i['dtfim_prev_novo'])}, prazo_horas={db.q(i['prazo_horas_novo'])} "
            f"WHERE id={db.q(i['id'])};"
        )
    stmts.append("COMMIT;")
    db.execute("\n".join(stmts))
    itens_afetados = [{"id": i["id"], "codigo_wbs": i.get("codigo_wbs"), "nome": i.get("nome")} for i in afetadas]
    # Mesmo padrão de log da Edição em Lote/Replanejamento (registrar_evento_manual) —
    # aqui best-effort feito à mão (sem passar por patch_row) porque o gatilho é este
    # UPDATE em lote das sucessoras, não a linha que o usuário editou (essa já foi
    # logada normalmente por patch_row/insert_row/delete_row, no chamador).
    try:
        nomes = ", ".join(f'{i["codigo_wbs"]} "{i["nome"]}"' for i in itens_afetados[:5])
        if len(itens_afetados) > 5:
            nomes += f" e mais {len(itens_afetados) - 5}"
        auditoria.registrar_evento_manual(
            "edicao",
            f'Cascata automática do cronograma ({origem_rotulo or "edição de atividade"}): '
            f'{len(itens_afetados)} sucessora(s) reagendada(s) — {nomes}',
            entidade="cronograma", entidade_id=str(projeto_id),
            projeto_id=projeto_id, sensivel=False,
            detalhes={"origem": origem_rotulo, "atividades_afetadas": itens_afetados},
        )
    except Exception:
        traceback.print_exc()
    return {
        "atividades_afetadas": len(afetadas),
        "itens": itens_afetados,
    }


def _valor_mesclado(atual, data, campo):
    """Mesmo critério de patch_row: só um campo PRESENTE em `data` sobrescreve o que já
    está em `atual` (a linha gravada no banco, ou None numa criação) — usado por toda
    função de validação/classificação abaixo pra sempre calcular em cima do estado
    RESULTANTE da gravação, nunca só do payload recebido isolado."""
    return data[campo] if campo in data else (atual or {}).get(campo)


def classificar_conclusao(atual, data, relato_para_concluir_sem_data=False):
    """Calcula sozinha qual das 4 variantes de 'Concluída' vale, a partir de prazo
    (Fim Real x Fim Previsto) e esforço (Horas Realizadas x Horas Previstas), e
    SOBRESCREVE data['status'] com o resultado — automação pedida pelo usuário na 28ª/
    29ª rodada ("o % de completude de uma atividade pode ser automatizado"), estendida
    aqui para a classificação completa.

    Só age quando o estado RESULTANTE da gravação (mesclando `data` com `atual`) já diz
    "isto está pronto" — 100% concluído — e o Status resultante não é um dos dois status
    de exceção (Bloqueada/Cancelada, ver STATUS_NAO_AUTOMATIZAR), que a automação nunca
    sobrescreve sozinha (uma atividade cancelada com 100%/Fim Real continua sendo um
    estado contraditório — ver validar_consistencia_conclusao). Fora disso, não faz nada:
    não é papel desta função decidir SE a atividade terminou, só QUAL rótulo de conclusão
    usar quando ela já terminou.

    Sem prazo previsto (dtfim_prev) ou sem prazo de horas (prazo_horas)/horas
    realizadas cadastrados, a falta de dado nunca é tratada como "estourou" — o
    benefício da dúvida fica com a atividade (ex: uma atividade sem prazo_horas
    cadastrado não pode logicamente ter "esforço maior que o previsto").

    `relato_para_concluir_sem_data` (125ª rodada): quando True — quem chamou já
    confirmou que existe um relato de andamento registrado explicando o motivo —
    permite classificar como concluída mesmo SEM Fim Real preenchido. Sem a data
    real não há como comparar com o Fim previsto, então cai sempre no rótulo
    genérico "Concluída" (nunca numa das variantes "com atraso"/"com esforço
    maior", que descrevem um fato sobre a execução real que aqui não está
    disponível); se o Status resultante já for uma variante específica escolhida
    manualmente, não é sobrescrito. Ver validar_consistencia_conclusao() — quem
    decide SE o relato exigido existe é sempre a rota, nunca esta função."""
    percentual = _valor_mesclado(atual, data, "percentual_concluido")
    percentual = int(percentual) if percentual not in (None, "") else 0
    dtfim_real = _valor_mesclado(atual, data, "dtfim_real")
    status_resultante = _valor_mesclado(atual, data, "status")

    if percentual != 100:
        return
    if status_resultante in STATUS_NAO_AUTOMATIZAR:
        return

    if not dtfim_real:
        if relato_para_concluir_sem_data and status_resultante not in STATUS_FAMILIA_CONCLUIDA:
            data["status"] = "Concluída"
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


def validar_consistencia_conclusao(atual, data, relato_para_concluir_sem_data=False):
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
    fins desta checagem, exatamente como patch_row faz na gravação de verdade.

    `relato_para_concluir_sem_data` (125ª rodada — pedido explícito do usuário:
    "permita que nas atualizações em massa e na própria página de Cronograma possamos
    atualizar as concluídas, mas desde que com relato"): quando True, 100% concluído
    SEM Fim Real preenchido deixa de ser bloqueado — fica só faltando o % em 100 (isso
    nunca é dispensado). Quem passa True já checou (na rota, ou em
    cronograma_edicao_lote) que existe (ou vai existir, no mesmo salvamento) um relato
    de andamento explicando por que a data real não está disponível — esta função não
    acessa o banco, só decide a consequência. Pensado pra atividades concluídas antes
    do acompanhamento atual do projeto, sem data real registrada; o caminho padrão
    (sem relato) continua travando exatamente como antes."""
    status = _valor_mesclado(atual, data, "status")
    percentual = _valor_mesclado(atual, data, "percentual_concluido")
    percentual = int(percentual) if percentual not in (None, "") else 0
    dtfim_real = _valor_mesclado(atual, data, "dtfim_real")
    completo = percentual == 100 and bool(dtfim_real)
    concluir_sem_data_real_ok = relato_para_concluir_sem_data and percentual == 100 and not dtfim_real

    if status in STATUS_FAMILIA_CONCLUIDA and not completo and not concluir_sem_data_real_ok:
        faltando = []
        if percentual != 100:
            faltando.append("o % concluído em 100")
        if not dtfim_real:
            faltando.append("a Data de Fim Real")
        raise ValueError(f'Para marcar a atividade como "{status}", preencha também {" e ".join(faltando)}.')

    if percentual == 100 and not dtfim_real and status not in STATUS_FAMILIA_CONCLUIDA and not concluir_sem_data_real_ok:
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


def validar_ordem_datas(atual, data):
    """Trava amigável pros dois pares Início/Fim (Previsto e Real) — o banco já
    impõe isso via CHECK constraint (atividades_check/atividades_check1), mas sem
    esta checagem o erro que chega na tela do modal individual é o erro cru do
    Postgres (reportado pelo usuário: reabriu uma atividade concluída, digitou
    um novo Início Real pro trabalho retomado e esqueceu de também atualizar o
    Fim Real antigo, que ficou anterior ao novo Início — a tela só mostrou
    "violates check constraint "atividades_check1""). A Edição em Lote
    (cronograma_edicao_lote._avaliar_progresso) já faz essa mesma checagem pro
    par de datas reais há mais tempo; esta função estende a mesma regra — pros
    dois pares, Previsto e Real — pro modal individual, que não tinha nenhuma.

    Calculado em cima do estado RESULTANTE (mescla `data` com `atual`), igual a
    classificar_conclusao/validar_consistencia_conclusao: se um PUT só manda um
    dos dois campos do par, o outro ainda é considerado (ex: só alterar o Início
    Real pra depois do Fim Real já gravado também é pego aqui)."""
    for campo_ini, campo_fim, rotulo_ini, rotulo_fim in (
        ("dtini_prev", "dtfim_prev", "Início Previsto", "Fim Previsto"),
        ("dtini_real", "dtfim_real", "Início Real", "Fim Real"),
    ):
        ini = _valor_mesclado(atual, data, campo_ini)
        fim = _valor_mesclado(atual, data, campo_fim)
        if ini and fim and fim < ini:
            raise ValueError(
                f'A Data de "{rotulo_fim}" ({fim}) não pode ser anterior à Data de "{rotulo_ini}" ({ini}).'
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


# ---------------------------------------------------------------------------
# 83ª rodada — carga em segundo plano da Comparação Folha (Google Picker).
#
# Até esta rodada, POST /comparacao-folha/importar/picker baixava do Drive
# E importava (até ~300 mil linhas) tudo dentro da MESMA requisição HTTP,
# sem devolver resposta até terminar — usuário reportou (verbatim) que
# precisava "ficar na janela onde a carga está sendo feita" e perguntou se
# não dava pra virar um processo em batch.
#
# Agora o endpoint só grava uma linha em cargas_comparacao_folha (status
# em_andamento) e devolve na hora; estas duas funções rodam numa THREAD
# separada, disparada pelo endpoint, e são as únicas responsáveis por
# terminar a carga (baixar + importar) e atualizar o status ao final. Fora
# de módulo/função aninhada de propósito: nada aqui depende do `app` Flask
# nem de contexto de requisição — só dos módulos já importados no topo do
# arquivo (db, google_drive, comparacao_folha_import, auditoria), que
# funcionam de qualquer thread (ver comentário de threading em db.py: cada
# chamada abre seu próprio subprocess `psql`, sem estado compartilhado).
def _processar_carga_comparacao_folha_picker(
    carga_id, projeto_id, file_id, mime_type, access_token, resource_key,
    nome_arquivo, usuario_id, usuario_nome, usuario_email,
):
    try:
        conteudo = google_drive.baixar_arquivo_selecionado(
            file_id, mime_type, access_token, nome_arquivo, resource_key=resource_key,
        )
        resultado = comparacao_folha_import.importar_comparacao_folha(projeto_id, conteudo)
    except google_drive.GoogleDriveError as e:
        _marcar_carga_comparacao_folha_erro(carga_id, str(e))
        return
    except comparacao_folha_import.ComparacaoFolhaImportError as e:
        _marcar_carga_comparacao_folha_erro(carga_id, f'{e} (arquivo selecionado: "{nome_arquivo}")')
        return
    except Exception as e:
        _marcar_carga_comparacao_folha_erro(carga_id, f'Falha ao importar a planilha "{nome_arquivo}": {e}')
        return

    try:
        db.execute(
            "UPDATE cargas_comparacao_folha SET "
            f"status = 'concluido', mesano = {db.q(resultado['mesano'])}, "
            f"total_linhas = {db.q(resultado['total_linhas'])}, "
            f"avisos = {db.q(json.dumps(resultado['avisos']))}::jsonb, concluido_em = now() "
            f"WHERE id = {db.q(carga_id)}"
        )
        auditoria.registrar_evento_manual(
            "edicao", f"Atualizou Comparação Folha — competência {resultado['mesano'][:7]} "
            f"({resultado['total_linhas']} linhas, arquivo \"{nome_arquivo}\")",
            entidade="comparacao_folha", entidade_rotulo=resultado["mesano"][:7],
            projeto_id=projeto_id, sensivel=False,
            usuario_id=usuario_id, usuario_nome=usuario_nome, usuario_email=usuario_email,
        )
    except Exception:
        # A importação em si já terminou (dados gravados); uma falha aqui é
        # só no registro de status/auditoria — não desfaz a carga, só evita
        # que o front-end fique achando que ainda está em_andamento pra
        # sempre. Loga pra investigar depois.
        print("[comparacao_folha] falha ao marcar carga como concluída:", flush=True)
        traceback.print_exc()
        try:
            db.execute(
                "UPDATE cargas_comparacao_folha SET status = 'concluido', concluido_em = now() "
                f"WHERE id = {db.q(carga_id)}"
            )
        except Exception:
            pass


def _marcar_carga_comparacao_folha_erro(carga_id, mensagem):
    try:
        db.execute(
            f"UPDATE cargas_comparacao_folha SET status = 'erro', mensagem_erro = {db.q(mensagem)}, "
            f"concluido_em = now() WHERE id = {db.q(carga_id)}"
        )
    except Exception:
        print("[comparacao_folha] falha ao marcar carga como erro:", flush=True)
        traceback.print_exc()


# 90ª rodada — mesmo motivo/padrão de _processar_carga_comparacao_folha_picker
# acima, agora pro upload manual (POST /comparacao-folha/importar/upload):
# usuário reportou (mesma reclamação de antes, agora pro botão "📤 Enviar
# arquivo do computador") que precisava ficar com a aba aberta esperando o
# arquivo (csv OU xlsx — formato detectado pelo conteúdo, ver _parece_xlsx()
# em comparacao_folha_import.py) importar inteiro, até ~300 mil linhas.
#
# Diferença pro picker: aqui não tem download — o conteúdo do arquivo
# (`conteudo`, bytes) já foi lido DENTRO da requisição, antes de disparar
# esta thread, porque request.files só existe durante a requisição HTTP (não
# dá pra ler o arquivo de dentro de uma thread separada).
def _processar_carga_comparacao_folha_upload(
    carga_id, projeto_id, conteudo, nome_arquivo, usuario_id, usuario_nome, usuario_email,
):
    try:
        resultado = comparacao_folha_import.importar_comparacao_folha(projeto_id, conteudo)
    except comparacao_folha_import.ComparacaoFolhaImportError as e:
        _marcar_carga_comparacao_folha_erro(carga_id, f'{e} (arquivo enviado: "{nome_arquivo}")')
        return
    except Exception as e:
        _marcar_carga_comparacao_folha_erro(carga_id, f'Falha ao importar a planilha "{nome_arquivo}": {e}')
        return

    try:
        db.execute(
            "UPDATE cargas_comparacao_folha SET "
            f"status = 'concluido', mesano = {db.q(resultado['mesano'])}, "
            f"total_linhas = {db.q(resultado['total_linhas'])}, "
            f"avisos = {db.q(json.dumps(resultado['avisos']))}::jsonb, concluido_em = now() "
            f"WHERE id = {db.q(carga_id)}"
        )
        auditoria.registrar_evento_manual(
            "edicao", f"Atualizou Comparação Folha (upload manual) — competência "
            f"{resultado['mesano'][:7]} ({resultado['total_linhas']} linhas, arquivo \"{nome_arquivo}\")",
            entidade="comparacao_folha", entidade_rotulo=resultado["mesano"][:7],
            projeto_id=projeto_id, sensivel=False,
            usuario_id=usuario_id, usuario_nome=usuario_nome, usuario_email=usuario_email,
        )
    except Exception:
        # Mesma lógica do picker: a importação já terminou (dados gravados),
        # falha aqui é só no registro de status/auditoria.
        print("[comparacao_folha] falha ao marcar carga (upload) como concluída:", flush=True)
        traceback.print_exc()
        try:
            db.execute(
                "UPDATE cargas_comparacao_folha SET status = 'concluido', concluido_em = now() "
                f"WHERE id = {db.q(carga_id)}"
            )
        except Exception:
            pass


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

    @app.after_request
    def nao_cachear_api(response):
        """119ª rodada — investigação de bug relatado pelo usuário: o card
        "Comparação Folha x Ergon" (rota nova da 118ª rodada,
        /api/comparacao-folha/resumo-competencias) voltou, em produção, o
        próprio index.html em vez de JSON — mesmo com a rota corretamente
        registrada e o deploy confirmado no commit certo (verificado tanto
        inspecionando app.url_map quanto via API do Render). A explicação
        mais provável: durante a janela do rolling deploy (processo gunicorn
        antigo ainda respondendo por alguns segundos, sem a rota nova, caindo
        no fallback de SPA), o navegador do usuário pode ter armazenado essa
        resposta 200 (index.html, com Cache-Control: no-cache + ETag/
        Last-Modified do werkzeug) associada à URL exata da nova API —
        e passou a reusá-la/revalidá-la de forma inconsistente em vez de
        sempre buscar a rota JSON nova. Qualquer /api/* nunca deveria ser
        cacheável pelo navegador (são sempre dados dinâmicos, autenticados
        por sessão) — isso fecha essa classe de bug para qualquer rota nova
        futura que nasça durante uma janela de deploy."""
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

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
        # 68ª rodada — "o cronograma sempre deve ser apresentado ordenado pelo
        # campo código": era por data prevista (dtini_prev). codigo_wbs_chave_ordenacao
        # (migração 032) ordena numericamente por segmento, não como texto puro
        # (senão "1.2.10" viria antes de "1.2.2").
        sql += " ORDER BY codigo_wbs_chave_ordenacao(a.codigo_wbs) NULLS LAST, a.nome"
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
            validar_ordem_datas(None, data)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        aplicar_percentual_inicial(None, data)
        _normalizar_restricao(data)
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
            f"SELECT status, percentual_concluido, dtini_real, dtfim_real, dtini_prev, dtfim_prev, "
            f"prazo_horas, horas_realizadas "
            f"FROM atividades WHERE id = {db.q(id)}"
        )
        if not atual:
            abort(404)
        # 125ª rodada — pedido explícito do usuário: permitir concluir (100%) sem a
        # Data de Fim Real preenchida na tela de edição individual, desde que exista
        # ao menos um relato registrado explicando o motivo (dados anteriores ao
        # acompanhamento atual do projeto, sem data real disponível). Calculado em
        # cima do estado RESULTANTE (mescla `data` com `atual`, igual a
        # classificar_conclusao/validar_consistencia_conclusao) ANTES de
        # classificar_conclusao() rodar, porque ela usa esse mesmo flag pra decidir
        # se assume "Concluída" sem data.
        percentual_resultante = _valor_mesclado(atual, data, "percentual_concluido")
        percentual_resultante = int(percentual_resultante) if percentual_resultante not in (None, "") else 0
        dtfim_real_resultante = _valor_mesclado(atual, data, "dtfim_real")
        tentando_concluir_sem_data_real = percentual_resultante == 100 and not dtfim_real_resultante

        # Mesma ordem do create: classifica primeiro (pode sobrescrever data["status"]
        # com a variante certa de Concluída), só depois checa relato/consistência.
        novo_status_bruto = data.get("status")  # só o que veio explícito no payload, igual ao comportamento de sempre
        tem_relato = None
        if novo_status_bruto in STATUS_EXIGE_RELATO or tentando_concluir_sem_data_real:
            tem_relato = db.fetch_one(
                f"SELECT 1 AS x FROM atividade_relato WHERE atividade_id = {db.q(id)} LIMIT 1"
            )
        if novo_status_bruto in STATUS_EXIGE_RELATO and not tem_relato:
            return jsonify({
                "erro": f'Status "{novo_status_bruto}" exige ao menos um relato de andamento registrado. '
                        'Adicione um relato na aba Relatos e tente salvar novamente.'
            }), 400
        if tentando_concluir_sem_data_real and not tem_relato:
            return jsonify({
                "erro": "Para concluir (100%) sem a Data de Fim Real preenchida, registre antes um relato "
                        "explicando o motivo (ex.: atividade concluída antes do acompanhamento atual do "
                        "projeto, sem registro da data exata). Adicione um relato na aba Relatos e tente "
                        "salvar novamente."
            }), 400
        relato_para_concluir_sem_data = tentando_concluir_sem_data_real and bool(tem_relato)

        classificar_conclusao(atual, data, relato_para_concluir_sem_data=relato_para_concluir_sem_data)
        try:
            validar_consistencia_conclusao(atual, data, relato_para_concluir_sem_data=relato_para_concluir_sem_data)
            validar_ordem_datas(atual, data)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        aplicar_percentual_inicial(atual, data)
        _normalizar_restricao(data)
        usuario = request.headers.get("X-Usuario", "")
        prelude = f"SET LOCAL app.usuario_atual = {db.q(usuario)};" if usuario else ""
        # 71ª rodada — dispara a cascata pras sucessoras quando o que muda é
        # esforço ou data prevista (os únicos campos que entram no grafo de
        # dependências) — ver _cascatear_edicao_individual acima.
        dispara_cascata = bool({"prazo_horas", "dtini_prev", "dtfim_prev"} & set(data.keys()))
        # O form do modal individual sempre manda os três campos juntos
        # (dtini_prev/dtfim_prev/prazo_horas), tenha o usuário mexido ou não —
        # diferente da Edição em Lote, que só recebe o que foi de fato
        # editado. Por isso, pra decidir se a cascata usa a DATA ou o
        # ESFORÇO como âncora de duração, comparamos com o valor QUE JÁ
        # ESTAVA GRAVADO antes deste PUT: só um Fim previsto que realmente
        # mudou nesta edição manda sobre o esforço — senão (ex.: o usuário só
        # mexeu no Esforço e o campo Fim previsto na tela ficou com o valor
        # antigo, porque o auto-cálculo do form só preenche data QUE ESTAVA
        # em branco) a duração continua vindo do esforço, como sempre foi.
        # Mesma regra de "o que foi tocado de verdade vence" que a Edição em
        # Lote já usa (ver cronograma_edicao_lote.py).
        dtfim_foi_editado = (
            "dtfim_prev" in data and _normaliza_data(data.get("dtfim_prev")) != _normaliza_data(atual.get("dtfim_prev"))
        )
        row = patch_row("atividades", id, data, ATIVIDADE_FIELDS, prelude=prelude)
        if not row:
            abort(404)
        if dispara_cascata and row.get("dtini_prev"):
            # Ecoa o Início JÁ GRAVADO (não o que veio em `data`) como o
            # "edit" que o motor espera — assim a cascata reflete fielmente o
            # estado atual da atividade não importa qual dos três campos foi
            # de fato alterado. `excluir_id` evita regravar a própria linha,
            # que já foi salva do jeito exato que o usuário digitou, um
            # passo acima.
            edicao = {"id": id, "dtini_prev": row["dtini_prev"]}
            if dtfim_foi_editado and row.get("dtfim_prev"):
                edicao["dtfim_prev"] = row["dtfim_prev"]
            cascata = _cascatear_edicao_individual(
                row["projeto_id"], edicao, excluir_id=id,
                origem_rotulo=f'edição de "{row.get("nome") or row.get("codigo_wbs") or id}"',
            )
            if cascata:
                row = dict(row)
                row["_cascata"] = cascata
        return jsonify(row)

    @app.delete("/api/atividades/<id>")
    def delete_atividade(id):
        return delete_row("atividades", id)

    @app.post("/api/atividades/<id>/duplicar")
    def duplicar_atividade(id):
        """56ª rodada — cria uma cópia completa de uma atividade: todos os campos de
        ATIVIDADE_FIELDS (dados da atividade), os recursos alocados (atividade_recurso)
        e as DEPENDÊNCIAS COMO PREDECESSORA (atividade_dependencia — só o lado em que a
        atividade original é a sucessora; a cópia não vira predecessora automática de
        quem dependia da original, isso o usuário ajusta manualmente depois se quiser).
        origem_importacao_id NUNCA é copiado (é a chave de casamento da reimportação do
        MS Project em cronograma_import.py — duas atividades com o mesmo valor
        quebrariam esse casamento). atividade_requisito (vínculos com requisitos do TR)
        também não é copiado — fora do escopo pedido ("duplique dados da atividade,
        recursos, dependências"). Grava direto via insert_row (mesmo caminho de
        auditoria/histórico do CRUD normal), sem passar pelas validações de
        relato/consistência de conclusão do POST/PUT normais — mesma lógica que
        cronograma_import.py já usa para inserção em lote."""
        original = db.fetch_one(f"SELECT * FROM atividades WHERE id = {db.q(id)}")
        if not original:
            abort(404)
        dados = {campo: original.get(campo) for campo in ATIVIDADE_FIELDS}
        dados["origem_importacao_id"] = None
        if dados.get("nome"):
            dados["nome"] = f"{dados['nome']} (cópia)"
        nova = insert_row("atividades", dados, ATIVIDADE_FIELDS)
        novo_id = nova["id"]

        db.execute(
            "INSERT INTO atividade_recurso (atividade_id, recurso_id, papel_na_atividade, horas_alocadas) "
            f"SELECT {db.q(novo_id)}, recurso_id, papel_na_atividade, horas_alocadas "
            f"FROM atividade_recurso WHERE atividade_id = {db.q(id)}"
        )
        db.execute(
            "INSERT INTO atividade_dependencia (atividade_id, predecessora_id, tipo, lag_horas) "
            f"SELECT {db.q(novo_id)}, predecessora_id, tipo, lag_horas "
            f"FROM atividade_dependencia WHERE atividade_id = {db.q(id)}"
        )
        row = db.fetch_one(ATIVIDADE_SELECT + f" WHERE a.id = {db.q(novo_id)}")
        return jsonify(row), 201

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
        # 71ª rodada — adicionar uma predecessora pode empurrar a própria
        # atividade (e a cadeia de sucessoras dela) — ver
        # _cascatear_edicao_individual acima. Diferente do modal
        # individual, aqui NÃO exclui a própria atividade do que é
        # regravado: ela pode genuinamente mudar de posição.
        resposta = dict(row)
        atividade = db.fetch_one(f"SELECT projeto_id, nome, codigo_wbs FROM atividades WHERE id = {db.q(id)}")
        if atividade:
            cascata = _cascatear_edicao_individual(
                atividade["projeto_id"], {
                    "id": id,
                    "dependencias_adicionar": [{
                        "predecessora_id": data["predecessora_id"],
                        "tipo": data.get("tipo") or "FS",
                        "lag_horas": data.get("lag_horas") or 0,
                    }],
                },
                origem_rotulo=f'nova dependência em "{atividade.get("nome") or atividade.get("codigo_wbs") or id}"',
            )
            if cascata:
                resposta["_cascata"] = cascata
        return jsonify(resposta), 201

    @app.delete("/api/dependencias/<dep_id>")
    def delete_dependencia(dep_id):
        dep = db.fetch_one(f"SELECT atividade_id, predecessora_id FROM atividade_dependencia WHERE id = {db.q(dep_id)}")
        db.execute(f"DELETE FROM atividade_dependencia WHERE id = {db.q(dep_id)}")
        # 71ª rodada — remover uma predecessora também pode reposicionar a
        # atividade e a cadeia dela (ver create_dependencia acima). Resposta
        # deixa de ser 204 vazio pra poder carregar o resumo da cascata; o
        # front-end (api()) já trata normalmente qualquer 2xx com corpo JSON.
        cascata = None
        if dep:
            atividade = db.fetch_one(f"SELECT projeto_id, nome, codigo_wbs FROM atividades WHERE id = {db.q(dep['atividade_id'])}")
            if atividade:
                cascata = _cascatear_edicao_individual(
                    atividade["projeto_id"], {
                        "id": dep["atividade_id"],
                        "dependencias_remover": [dep["predecessora_id"]],
                    },
                    origem_rotulo=f'remoção de dependência em "{atividade.get("nome") or atividade.get("codigo_wbs") or dep["atividade_id"]}"',
                )
        return jsonify({"_cascata": cascata}), 200

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

    @app.post("/api/requisitos/perguntar-ia")
    def requisitos_perguntar_ia():
        """99ª rodada: pergunta livre (IA) sobre os requisitos que estão na
        tela no momento (filtrados ou selecionados pelo consultor) — ver
        app/requisitos_ia.py. A resposta é sempre um candidato para revisão
        do consultor, nunca uma decisão automática de escopo."""
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        usuario_atual = auth.buscar_usuario_publico(session.get("usuario_id")) or {}
        try:
            row = requisitos_ia.perguntar(
                projeto_id, data.get("requisito_ids"), data.get("pergunta"),
                perguntado_por_id=usuario_atual.get("id"), perguntado_por_nome=usuario_atual.get("nome"),
            )
        except requisitos_ia.RequisitosIAError as e:
            return jsonify({"erro": str(e)}), 400
        except db.DbError:
            raise
        except Exception as e:
            print(f"[requisitos_ia] ERRO INESPERADO: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha inesperada ao perguntar: {e}"}), 500
        return jsonify(row), 201

    @app.get("/api/requisitos/perguntas-ia")
    def requisitos_perguntas_ia_listar():
        """Histórico de perguntas (IA) já feitas neste projeto — usado para
        popular a lista de histórico do painel na tela Analisar Requisitos."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        return jsonify(requisitos_ia.listar_historico(pid))

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
        iid = request.args.get("item_migracao_id")
        where = []
        if aid:
            where.append(f"atividade_id = {db.q(aid)}")
        if iid:
            where.append(f"item_migracao_id = {db.q(iid)}")
        sql = "SELECT * FROM ciclos_migracao"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY numero_ciclo"
        return jsonify(db.fetch_all(sql))

    @app.post("/api/ciclos-migracao")
    def create_ciclo():
        payload = request.get_json(force=True)
        # Rejeitados = Extraídos - Carregados, calculado automaticamente (nunca
        # digitado): recalcula aqui em vez de confiar no que a tela mandou, pra
        # garantir a mesma regra mesmo numa chamada direta à API. (116ª rodada:
        # um ciclo recém-criado por esta rota nunca tem qtd_rejeicoes_detalhada
        # = true ainda -- esse flag só é ligado por app/migracao_rejeicoes.py
        # depois de uma carga detalhada de rejeições -- então a derivação
        # abaixo sempre vale aqui, sem precisar checar o flag.)
        extraidos = payload.get("qtd_registros_extraidos")
        carregados = payload.get("qtd_registros_carregados")
        if extraidos is not None and carregados is not None:
            payload["qtd_rejeicoes"] = max(0, int(extraidos) - int(carregados))
        return jsonify(insert_row("ciclos_migracao", payload, CICLO_FIELDS)), 201

    @app.put("/api/ciclos-migracao/<id>")
    def update_ciclo(id):
        """76ª rodada: permite corrigir os dados de um ciclo já registrado
        (a tela só tinha "+ Registrar ciclo" e excluir — sem editar, um
        número digitado errado só dava pra corrigir excluindo e recriando,
        perdendo o número/posição original). Mesma regra de
        `create_ciclo` pra Rejeições = Extraídos - Carregados, recalculada
        aqui a partir do valor já salvo quando a edição não reenvia os
        dois campos (patch_row só atualiza o que vier no payload).

        116ª rodada: quando este ciclo já tem qtd_rejeicoes "preenchido
        sozinho" a partir de uma carga detalhada de rejeições (ver
        app/migracao_rejeicoes.py e migration_038 -- flag
        qtd_rejeicoes_detalhada), essa recomputação NÃO roda -- senão a
        próxima edição manual de extraídos/carregados pela tela de
        "Ciclos de execução" sobrescreveria silenciosamente o valor de
        verdade vindo da planilha. Nesse caso qtd_rejeicoes nem entra no
        payload (CICLO_FIELDS tem a coluna, mas o valor que a tela manda
        é ignorado aqui), mantendo o que já está gravado."""
        atual = db.fetch_one(f"SELECT * FROM ciclos_migracao WHERE id = {db.q(id)}")
        if not atual:
            abort(404)
        payload = request.get_json(force=True)
        if atual.get("qtd_rejeicoes_detalhada"):
            payload.pop("qtd_rejeicoes", None)
        else:
            extraidos = payload.get("qtd_registros_extraidos", atual.get("qtd_registros_extraidos"))
            carregados = payload.get("qtd_registros_carregados", atual.get("qtd_registros_carregados"))
            if extraidos is not None and carregados is not None:
                payload["qtd_rejeicoes"] = max(0, int(extraidos) - int(carregados))
        row = patch_row("ciclos_migracao", id, payload, CICLO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/ciclos-migracao/<id>")
    def delete_ciclo(id):
        return delete_row("ciclos_migracao", id)

    # ------------------------------------------------------- itens de migração
    # 48ª rodada: "Qtd de dados a migrar de cada tabela e qtd de dados
    # carregados" — cada linha é uma tabela/entidade do sistema legado a
    # migrar, com uma meta opcional (qtd_registros_estimada) e progresso
    # calculado a partir do último ciclo_migracao vinculado a ela.
    @app.get("/api/itens-migracao")
    def list_itens_migracao():
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        sql = f"""
            SELECT im.*, res.nome AS responsavel_nome,
                   a.codigo_wbs AS atividade_codigo, a.nome AS atividade_nome,
                   COALESCE((SELECT c.qtd_registros_carregados FROM ciclos_migracao c
                             WHERE c.item_migracao_id = im.id
                             ORDER BY c.numero_ciclo DESC LIMIT 1), 0) AS qtd_registros_carregados,
                   COALESCE((SELECT c.qtd_rejeicoes FROM ciclos_migracao c
                             WHERE c.item_migracao_id = im.id
                             ORDER BY c.numero_ciclo DESC LIMIT 1), 0) AS qtd_rejeicoes,
                   (SELECT c.percentual_rejeicao FROM ciclos_migracao c
                    WHERE c.item_migracao_id = im.id
                    ORDER BY c.numero_ciclo DESC LIMIT 1) AS percentual_rejeicao,
                   (SELECT count(*) FROM ciclos_migracao c WHERE c.item_migracao_id = im.id) AS qtd_ciclos,
                   (SELECT max(c.data_execucao) FROM ciclos_migracao c WHERE c.item_migracao_id = im.id) AS ultima_execucao
            FROM itens_migracao im
            LEFT JOIN recursos res ON res.id = im.responsavel_id
            LEFT JOIN atividades a ON a.id = im.atividade_id
            WHERE im.projeto_id = {db.q(pid)}
            ORDER BY im.nome_tabela_legado
        """
        return jsonify(db.fetch_all(sql))

    @app.get("/api/itens-migracao/resumo")
    def resumo_itens_migracao():
        """Totais agregados pro card de resumo da tela e pro futuro relatório
        detalhado (48ª rodada) — tudo calculado aqui no banco, determinístico,
        sem IA: quantos itens por status, soma da meta x soma do carregado."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        row = db.fetch_one(f"""
            SELECT
              count(*) AS total_itens,
              count(*) FILTER (WHERE im.status = 'Concluído') AS itens_concluidos,
              count(*) FILTER (WHERE im.status = 'Em andamento') AS itens_em_andamento,
              count(*) FILTER (WHERE im.status = 'Bloqueado') AS itens_bloqueados,
              count(*) FILTER (WHERE im.status = 'Não iniciado') AS itens_nao_iniciados,
              COALESCE(sum(im.qtd_registros_estimada), 0) AS total_estimado,
              COALESCE(sum((SELECT c.qtd_registros_carregados FROM ciclos_migracao c
                            WHERE c.item_migracao_id = im.id
                            ORDER BY c.numero_ciclo DESC LIMIT 1)), 0) AS total_carregado
            FROM itens_migracao im
            WHERE im.projeto_id = {db.q(pid)}
        """)
        return jsonify(row or {
            "total_itens": 0, "itens_concluidos": 0, "itens_em_andamento": 0,
            "itens_bloqueados": 0, "itens_nao_iniciados": 0, "total_estimado": 0, "total_carregado": 0,
        })

    def _montar_quadro_ciclo(pid, numero_ciclo_arg):
        """78ª/79ª rodada: monta o "quadro" de carga por ciclo — usado tanto
        pelo endpoint JSON (tela) quanto pelo export em Excel, pra nunca
        calcular os números de um jeito na tela e de outro na planilha.
        Devolve (numero_ciclo, ciclos_disponiveis, linhas, erro); erro vem
        preenchido (mensagem, status) só quando numero_ciclo_arg é inválido."""
        disponiveis = db.fetch_all(f"""
            SELECT DISTINCT c.numero_ciclo
            FROM ciclos_migracao c
            JOIN itens_migracao im ON im.id = c.item_migracao_id
            WHERE im.projeto_id = {db.q(pid)}
            ORDER BY c.numero_ciclo DESC
        """)
        ciclos_disponiveis = [r["numero_ciclo"] for r in disponiveis]

        if numero_ciclo_arg not in (None, ""):
            try:
                numero_ciclo = int(numero_ciclo_arg)
            except ValueError:
                return None, ciclos_disponiveis, [], ("numero_ciclo inválido", 400)
        else:
            numero_ciclo = ciclos_disponiveis[0] if ciclos_disponiveis else None

        linhas = []
        if numero_ciclo is not None:
            linhas = db.fetch_all(f"""
                SELECT im.id AS item_migracao_id,
                       im.nome_tabela_legado, im.nome_tabela_destino, im.sistema_origem,
                       c.numero_ciclo, c.data_execucao,
                       COALESCE(c.qtd_registros_extraidos, 0) AS total_a_carregar,
                       COALESCE(c.qtd_registros_carregados, 0) AS total_carregado,
                       COALESCE(c.qtd_rejeicoes, 0) AS total_rejeitados,
                       CASE WHEN COALESCE(c.qtd_registros_extraidos, 0) > 0
                            THEN round(100.0 * COALESCE(c.qtd_registros_carregados, 0) / c.qtd_registros_extraidos, 2)
                            ELSE NULL END AS percentual_carregado,
                       c.percentual_rejeicao
                FROM ciclos_migracao c
                JOIN itens_migracao im ON im.id = c.item_migracao_id
                WHERE im.projeto_id = {db.q(pid)} AND c.numero_ciclo = {db.q(numero_ciclo)}
                ORDER BY im.nome_tabela_legado
            """)

        return numero_ciclo, ciclos_disponiveis, linhas, None

    def _totais_quadro_ciclo(linhas):
        """Mesma soma exibida na linha "Total geral" da tela (ver
        renderQuadroCiclo no frontend) — os dois percentuais são
        recalculados sobre a soma, não a média dos percentuais de cada
        linha, senão distorce quando as tabelas têm tamanhos bem diferentes."""
        total_a_carregar = sum(l["total_a_carregar"] or 0 for l in linhas)
        total_carregado = sum(l["total_carregado"] or 0 for l in linhas)
        total_rejeitados = sum(l["total_rejeitados"] or 0 for l in linhas)
        return {
            "total_a_carregar": total_a_carregar,
            "total_carregado": total_carregado,
            "total_rejeitados": total_rejeitados,
            "percentual_carregado": round(100.0 * total_carregado / total_a_carregar, 2) if total_a_carregar > 0 else None,
            "percentual_rejeicao": round(100.0 * total_rejeitados / total_a_carregar, 2) if total_a_carregar > 0 else None,
        }

    @app.get("/api/itens-migracao/quadro-ciclo")
    def quadro_ciclo_migracao():
        """78ª/80ª rodada: "quadro" pedido pelo usuário em Migração de Dados —
        Destino/Sistema, Número do Ciclo, Data do ciclo, Total a carregar,
        Total carregado, Total rejeitados, % carregado, % rejeitado,
        consolidado por UM ciclo de execução por vez (o "Filtro Ciclo" da
        tela). Sem numero_ciclo no
        querystring, usa o maior já registrado em qualquer item do projeto
        ("sempre o último", como pedido) — ciclos_disponiveis vai junto na
        resposta pra popular o filtro sem precisar de uma segunda chamada."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        numero_ciclo, ciclos_disponiveis, linhas, erro = _montar_quadro_ciclo(pid, request.args.get("numero_ciclo"))
        if erro:
            return jsonify({"erro": erro[0]}), erro[1]
        return jsonify({
            "numero_ciclo": numero_ciclo,
            "ciclos_disponiveis": ciclos_disponiveis,
            "linhas": linhas,
        })

    @app.get("/api/itens-migracao/quadro-ciclo/exportar")
    def quadro_ciclo_migracao_exportar():
        """79ª rodada: "habilite a exportação para planilha excel" do quadro
        acima — mesmos dados e mesmo cálculo de _montar_quadro_ciclo/
        _totais_quadro_ciclo (nada recalculado na planilha), formatados por
        app/migracao_quadro_export.py numa aba só, com a linha de total
        geral em negrito igual à da tela."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        projeto = db.fetch_one(f"SELECT nome, sigla FROM projetos WHERE id = {db.q(pid)}")
        if not projeto:
            abort(404)
        numero_ciclo, _ciclos_disponiveis, linhas, erro = _montar_quadro_ciclo(pid, request.args.get("numero_ciclo"))
        if erro:
            return jsonify({"erro": erro[0]}), erro[1]
        totais = _totais_quadro_ciclo(linhas)
        try:
            xlsx_bytes = migracao_quadro_export.gerar_planilha_bytes(numero_ciclo, linhas, totais)
        except Exception as e:
            print(f"[migracao_quadro_export] erro ao gerar planilha: {e}", flush=True)
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao gerar a planilha: {e}"}), 500
        base = (projeto.get("sigla") or projeto.get("nome") or "projeto").strip()
        base = "".join(c if c.isalnum() or c in "-_" else "-" for c in base).strip("-") or "projeto"
        sufixo_ciclo = f"-ciclo{numero_ciclo}" if numero_ciclo is not None else ""
        nome_arquivo = f"quadro-carga-migracao-{base}{sufixo_ciclo}-{date.today().isoformat()}.xlsx"
        return send_file(
            io.BytesIO(xlsx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=nome_arquivo,
        )

    @app.post("/api/itens-migracao")
    def create_item_migracao():
        return jsonify(insert_row("itens_migracao", request.get_json(force=True), ITEM_MIGRACAO_FIELDS)), 201

    @app.put("/api/itens-migracao/<id>")
    def update_item_migracao(id):
        row = patch_row("itens_migracao", id, request.get_json(force=True), ITEM_MIGRACAO_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/itens-migracao/<id>")
    def delete_item_migracao(id):
        return delete_row("itens_migracao", id)

    # -------------------------------------------------- migração: rejeitados
    # 116ª rodada (pedido verbatim do usuário): carga, análise e histórico
    # das planilhas de REJEITADOS que o Ergon devolve em cada ciclo de
    # migração -- Fase 2 (De-Para, arquivo "LISTA_P_REJ_DP_*") e Fase 3
    # (Carga final, arquivo "LISTA_P_REJ_ERG_*"). Ver app/migracao_rejeicoes.py
    # (persistência/consulta) e app/migracao_rejeicoes_import.py (parser e
    # classificação de MSG_ERRO em tipo_erro_chave). Arquivos pequenos (até
    # alguns milhares de linhas, bem diferente das ~300 mil de Comparação
    # Folha) -- a importação roda DENTRO da própria requisição, sem fila/
    # thread em segundo plano.
    @app.get("/api/migracao-rejeicoes/cargas")
    def list_migracao_rejeicoes_cargas():
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        cargas = migracao_rejeicoes.listar_cargas(
            pid, item_migracao_id=request.args.get("item_migracao_id"), fase=request.args.get("fase"),
        )
        return jsonify(cargas)

    @app.post("/api/migracao-rejeicoes/importar")
    def importar_migracao_rejeicoes():
        """Form-data: projeto_id, item_migracao_id, numero_ciclo, fase
        ("fase2_depara"/"fase3_carga_final"), file -- e opcionalmente
        data_execucao, só usada se o ciclo informado ainda não existir
        (ver migracao_rejeicoes.encontrar_ou_criar_ciclo). Reimportar a
        MESMA combinação (item, ciclo, fase) substitui a carga anterior
        por completo (pedido verbatim) -- nunca duplica."""
        projeto_id = request.form.get("projeto_id")
        item_migracao_id = request.form.get("item_migracao_id")
        fase = request.form.get("fase")
        numero_ciclo = request.form.get("numero_ciclo")
        data_execucao = request.form.get("data_execucao") or None
        file = request.files.get("file")

        if not projeto_id or not item_migracao_id or not fase or not numero_ciclo:
            return jsonify({"erro": "Informe projeto, item de migração, número do ciclo e fase."}), 400
        if fase not in migracao_rejeicoes_import.FASES_VALIDAS:
            return jsonify({"erro": "Fase inválida."}), 400
        if not file:
            return jsonify({"erro": "Selecione o arquivo (.csv ou .xlsx)."}), 400
        try:
            numero_ciclo = int(numero_ciclo)
        except ValueError:
            return jsonify({"erro": "Número do ciclo inválido."}), 400

        nome_arquivo = file.filename
        conteudo = file.read()
        usuario_atual = auth.buscar_usuario_publico(session.get("usuario_id")) or {}

        try:
            ciclo = migracao_rejeicoes.encontrar_ou_criar_ciclo(item_migracao_id, numero_ciclo, data_execucao)
            carga = migracao_rejeicoes.importar(
                projeto_id, item_migracao_id, ciclo["id"], fase, nome_arquivo, conteudo,
                usuario_id=usuario_atual.get("id"), usuario_nome=usuario_atual.get("nome"),
            )
        except migracao_rejeicoes_import.MigracaoRejeicoesImportError as e:
            return jsonify({"erro": str(e)}), 400
        except db.DbError as e:
            return jsonify({"erro": f"Falha ao gravar no banco: {e}"}), 500
        return jsonify(carga), 201

    @app.delete("/api/migracao-rejeicoes/cargas/<id>")
    def delete_migracao_rejeicoes_carga(id):
        carga = migracao_rejeicoes.excluir_carga(id)
        if not carga:
            abort(404)
        return "", 204

    @app.get("/api/migracao-rejeicoes/resumo")
    def resumo_migracao_rejeicoes():
        carga_id = request.args.get("carga_id")
        if not carga_id:
            return jsonify({"erro": "carga_id é obrigatório"}), 400
        res = migracao_rejeicoes.resumo(carga_id)
        if not res:
            abort(404)
        return jsonify(res)

    @app.get("/api/migracao-rejeicoes/detalhe")
    def detalhe_migracao_rejeicoes():
        carga_id = request.args.get("carga_id")
        if not carga_id:
            return jsonify({"erro": "carga_id é obrigatório"}), 400
        limit = request.args.get("limit", 200)
        offset = request.args.get("offset", 0)
        linhas = migracao_rejeicoes.detalhe(
            carga_id, tipo_erro_chave=request.args.get("tipo_erro_chave"), limit=limit, offset=offset,
        )
        return jsonify(linhas)

    @app.get("/api/migracao-rejeicoes/acoes-sugeridas")
    def list_migracao_rejeicoes_acoes_sugeridas():
        return jsonify(migracao_rejeicoes.listar_acoes_sugeridas(fase=request.args.get("fase")))

    @app.post("/api/migracao-rejeicoes/acoes-sugeridas")
    def create_migracao_rejeicoes_acao_sugerida():
        """Usada pela tela quando o breakdown de uma carga mostra um
        tipo_erro_chave que ainda não tem entrada no catálogo (ex: um
        código ERG-NNNNN nunca visto antes) -- cadastra (ou completa, se
        já existir) a ação sugerida pra esse tipo."""
        data = request.get_json(force=True)
        if not data.get("fase") or not data.get("tipo_erro_chave"):
            return jsonify({"erro": "Informe fase e tipo_erro_chave."}), 400
        row = migracao_rejeicoes.criar_acao_sugerida(
            data["fase"], data["tipo_erro_chave"],
            descricao_erro=data.get("descricao_erro"), acao_sugerida=data.get("acao_sugerida"),
        )
        return jsonify(row), 201

    @app.put("/api/migracao-rejeicoes/acoes-sugeridas/<id>")
    def update_migracao_rejeicoes_acao_sugerida(id):
        data = request.get_json(force=True)
        row = migracao_rejeicoes.atualizar_acao_sugerida(
            id, acao_sugerida=data.get("acao_sugerida"), descricao_erro=data.get("descricao_erro"),
        )
        if not row:
            abort(404)
        return jsonify(row)

    # ---------------------------------------------------------------- rubricas
    # 53ª rodada: levantamento e homologação das rubricas (regras de negócio)
    # da folha de pagamento — importado da planilha "Levantamento Rubricas" do
    # cliente (ver app/rubricas_import.py). Uma linha por rubrica levantada,
    # podendo repetir código ERGON quando a mesma regra é tratada em mais de
    # uma empresa (ver db/migration_029_rubricas.sql).
    @app.get("/api/rubricas")
    def list_rubricas():
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        condicoes = [f"r.projeto_id = {db.q(pid)}"]
        status = request.args.get("status")
        if status:
            condicoes.append(f"r.status = {db.q(status)}")
        grupo = request.args.get("grupo_calculo")
        if grupo:
            condicoes.append(f"r.grupo_calculo = {db.q(grupo)}")
        busca = request.args.get("busca")
        if busca:
            termo = busca.replace("'", "''")
            condicoes.append(
                "(r.nome_abreviado ILIKE '%" + termo + "%' OR r.nome_extenso ILIKE '%" + termo + "%' "
                "OR r.codigo_ergon ILIKE '%" + termo + "%' OR r.verba_legado ILIKE '%" + termo + "%' "
                "OR r.descricao_legado ILIKE '%" + termo + "%')"
            )
        sql = f"""
            SELECT r.*, a.codigo_wbs AS atividade_codigo, a.nome AS atividade_nome
            FROM rubricas r
            LEFT JOIN atividades a ON a.id = r.atividade_id
            WHERE {' AND '.join(condicoes)}
            ORDER BY r.linha_planilha NULLS LAST, r.codigo_ergon, r.nome_abreviado
        """
        return jsonify(db.fetch_all(sql))

    @app.get("/api/rubricas/resumo")
    def resumo_rubricas():
        """Funil de status pro card de resumo — igual em espírito ao
        /api/itens-migracao/resumo."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        row = db.fetch_one(f"""
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE status = 'Em levantamento') AS em_levantamento,
              count(*) FILTER (WHERE status = 'Enviada à Techne') AS enviadas_techne,
              count(*) FILTER (WHERE status = 'Liberada para testes') AS liberadas_testes,
              count(*) FILTER (WHERE status = 'Em homologação') AS em_homologacao,
              count(*) FILTER (WHERE status = 'Homologada') AS homologadas,
              count(*) FILTER (WHERE status = 'Excluída') AS excluidas
            FROM rubricas WHERE projeto_id = {db.q(pid)}
        """)
        return jsonify(row or {
            "total": 0, "em_levantamento": 0, "enviadas_techne": 0, "liberadas_testes": 0,
            "em_homologacao": 0, "homologadas": 0, "excluidas": 0,
        })

    @app.get("/api/rubricas/<id>")
    def get_rubrica(id):
        row = db.fetch_one(f"SELECT * FROM rubricas WHERE id = {db.q(id)}")
        if not row:
            abort(404)
        return jsonify(row)

    @app.post("/api/rubricas")
    def create_rubrica():
        return jsonify(insert_row("rubricas", request.get_json(force=True), RUBRICA_FIELDS)), 201

    @app.put("/api/rubricas/<id>")
    def update_rubrica(id):
        row = patch_row("rubricas", id, request.get_json(force=True), RUBRICA_FIELDS)
        if not row:
            abort(404)
        return jsonify(row)

    @app.delete("/api/rubricas/<id>")
    def delete_rubrica(id):
        return delete_row("rubricas", id)

    @app.post("/api/rubricas/importar/preview")
    def importar_rubricas_preview():
        """Recebe a planilha "Levantamento Rubricas" (upload manual) e
        devolve uma PRÉVIA — não grava nada no banco. Ver app/rubricas_import.py."""
        projeto_id = request.form.get("projeto_id")
        file = request.files.get("file")
        if not (projeto_id and file):
            return jsonify({"erro": "projeto_id e file são obrigatórios"}), 400
        try:
            resultado = _rubricas_preview_resultado(projeto_id, file.read(), file.filename)
        except rubricas_import.RubricasImportError as e:
            return jsonify({"erro": str(e)}), 400
        except Exception as e:
            return jsonify({"erro": f"Falha ao ler a planilha: {e}"}), 400
        return jsonify(resultado)

    @app.post("/api/rubricas/importar/drive/preview")
    def importar_rubricas_drive_preview():
        """Mesma prévia de .../importar/preview, mas busca a planilha
        automaticamente — lê o arquivo mais recente da pasta do Google Drive
        configurada (GOOGLE_DRIVE_RUBRICAS_FOLDER_ID) em vez de receber
        upload manual (54ª rodada — botão "Atualizar Rubricas" da tela). Ver
        app/drive_rubricas.py. Não grava nada no banco — a confirmação usa
        o mesmo endpoint de sempre, /api/rubricas/importar/confirmar, com os
        itens desta prévia (possivelmente já revisados na tela)."""
        data = request.get_json(silent=True) or {}
        projeto_id = data.get("projeto_id") or request.args.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        try:
            conteudo, nome_arquivo, modificado_em = drive_rubricas.baixar_planilha_mais_recente()
        except drive_rubricas.DriveRubricasError as e:
            return jsonify({"erro": str(e)}), 502
        try:
            resultado = _rubricas_preview_resultado(projeto_id, conteudo, nome_arquivo)
        except rubricas_import.RubricasImportError as e:
            # Inclui qual arquivo foi baixado do Drive na mensagem — sem isso,
            # um erro de parse (aba/cabeçalho errado) não dá pra saber se foi o
            # arquivo errado que acabou sendo o "mais recentemente modificado"
            # da pasta (visto em produção, 55ª rodada).
            return jsonify({"erro": f'{e} (arquivo do Drive: "{nome_arquivo}", modificado em {modificado_em})'}), 400
        except Exception as e:
            return jsonify({"erro": f'Falha ao ler a planilha "{nome_arquivo}": {e}'}), 400
        resultado["arquivo_origem"] = {"nome": nome_arquivo, "modificado_em": modificado_em}
        return jsonify(resultado)

    @app.post("/api/rubricas/importar/confirmar")
    def importar_rubricas_confirmar():
        """Recebe a lista de itens revisados na prévia (mesmo formato de
        .../preview) e grava via upsert por linha_planilha — reimportar
        atualiza em vez de duplicar."""
        data = request.get_json(force=True)
        projeto_id = data.get("projeto_id")
        itens = data.get("itens") or []
        if not projeto_id or not itens:
            return jsonify({"erro": "projeto_id e itens são obrigatórios"}), 400
        linhas = [it for it in itens if it.get("linha_planilha")]
        if not linhas:
            return jsonify({"erro": "Nenhum item válido para importar."}), 400
        inseridos, atualizados = upsert_rubricas(projeto_id, linhas)
        return jsonify({"inseridos": inseridos, "atualizados": atualizados})

    @app.get("/api/rubricas/auto-update/status")
    def rubricas_auto_update_status():
        """113ª rodada — última execução (qualquer status) da atualização
        automática de Rubricas (seg-sex, 8h-18h, horário de Brasília — ver
        app/rubricas_auto_update.py), pra tela mostrar um aviso de quando foi
        a última vez que o processo rodou sozinho. `null` quando o recurso
        está desligado (RUBRICAS_AUTO_UPDATE_PROJETO_ID não configurada) ou
        ainda não disparou nenhuma vez para este projeto."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        return jsonify(rubricas_auto_update.ultima_execucao(pid))

    # ------------------------------------------------------------- comparação folha
    # 55ª rodada: aba "Comparação Folha" (dentro de Folha de Pagamento, junto
    # de Rubricas) — resultado já calculado (pelo cliente) da comparação
    # entre o valor de cada rubrica no Ergon e na ficha financeira do sistema
    # legado. Diferente de Rubricas: só consulta (sem CRUD manual por linha,
    # sem prévia com checkbox) — confirmado com o usuário, dado o volume
    # (~300 mil linhas por arquivo). Ver app/comparacao_folha_import.py (a
    # carga em si, via streaming/COPY) e app/comparacao_folha.py (leitura
    # paginada/filtrada usada pelas rotas abaixo).
    def _filtros_comparacao_folha():
        return {
            "mesano": request.args.get("mesano") or None,
            "situacao": request.args.get("situacao") or None,
            "tipo_comparacao": request.args.get("tipo_comparacao") or None,
            "tiporubr": request.args.get("tiporubr") or None,
            # 65ª rodada: Empresa/TipoVINC/Rubrica Ergon/Verba Consist.
            "empresa": request.args.get("empresa") or None,
            "tipovinc": request.args.get("tipovinc") or None,
            "rubrica_ergon": request.args.get("rubrica_ergon") or None,
            "verba_consist": request.args.get("verba_consist") or None,
            "busca": request.args.get("busca") or None,
            "so_divergentes": request.args.get("so_divergentes") == "1",
        }

    @app.get("/api/comparacao-folha")
    def listar_comparacao_folha():
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        filtros = _filtros_comparacao_folha()
        filtros["projeto_id"] = pid
        pagina = int(request.args.get("pagina") or 1)
        tamanho_pagina = min(int(request.args.get("tamanho_pagina") or 50), 200)
        return jsonify({
            "itens": comparacao_folha.listar(pagina=pagina, tamanho_pagina=tamanho_pagina, **filtros),
            "total": comparacao_folha.contar(**filtros),
            "pagina": pagina,
            "tamanho_pagina": tamanho_pagina,
        })

    @app.get("/api/comparacao-folha/resumo")
    def resumo_comparacao_folha():
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        # 84ª rodada: antes só repassava "mesano" — os KPIs do topo (rótulo
        # "Linhas (filtro atual)") ficavam sempre com o total do mês, iguais
        # pra qualquer Empresa/Rubrica/Situação selecionada. Agora repassa os
        # mesmos filtros da listagem (ver _filtros_comparacao_folha() acima).
        filtros = _filtros_comparacao_folha()
        return jsonify(comparacao_folha.resumo(projeto_id=pid, **filtros))

    @app.get("/api/comparacao-folha/competencias")
    def comparacao_folha_competencias():
        """93ª rodada — consulta enxuta só pra popular o <select> de
        Competência nas abas Comparação Folha/Dashboard, ANTES do usuário
        escolher alguma coisa (pedido do usuário: não carregar nada
        automaticamente até ele escolher a Competência). Ver
        comparacao_folha.competencias_disponiveis() pro motivo de não
        reaproveitar resumo()/dashboard() pra isso."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        return jsonify({"meses_disponiveis": comparacao_folha.competencias_disponiveis(pid)})

    @app.get("/api/comparacao-folha/consulta-dinamica")
    def comparacao_folha_consulta_dinamica():
        """92ª rodada — pedido do usuário (verbatim): "quero informar o tipo
        de comparação, a empresa, o tipo da rubrica, a verba consist ou
        rubrica ergon ... quero informar alguns campos e receber as
        quantidades". Só projeto_id + mesano (escopo) + os 5 campos que
        podem ser filtro OU dimensão de agrupamento (ver
        comparacao_folha.consulta_dinamica pra regra completa) — os demais
        filtros da grade (situação, tipovinc, busca, só divergentes) não
        entram aqui de propósito, ficam restritos aos 5 campos que o usuário
        pediu."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        return jsonify(comparacao_folha.consulta_dinamica(
            projeto_id=pid,
            mesano=request.args.get("mesano") or None,
            tipo_comparacao=request.args.get("tipo_comparacao") or None,
            empresa=request.args.get("empresa") or None,
            tiporubr=request.args.get("tiporubr") or None,
            rubrica_ergon=request.args.get("rubrica_ergon") or None,
            verba_consist=request.args.get("verba_consist") or None,
        ))

    @app.get("/api/comparacao-folha/resumo-competencias")
    def resumo_competencias_comparacao_folha():
        """118ª rodada — card "Folha de Pagamento" do Dashboard Executivo
        (Visão Executiva): os quadros por Situação e por Tipo de Rubrica,
        repetidos pelas competências mais recentes (no máx. `limite`, padrão
        5 — ver comparacao_folha.resumo_por_competencia). Só projeto_id;
        sem os demais filtros da grade (igual dashboard_comparacao_folha
        logo abaixo) — é um painel agregado, não uma consulta filtrável."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        limite = min(int(request.args.get("limite") or 5), 12)
        return jsonify(comparacao_folha.resumo_por_competencia(pid, limite=limite))

    @app.get("/api/comparacao-folha/dashboard")
    def dashboard_comparacao_folha():
        """65ª rodada: aba "Dashboard" dedicada à Comparação Folha — métricas
        de convergência (% sem divergência geral/por situação, contagem de
        rubricas Ergon x verbas Consist por empresa, cobertura de mapeamento
        cruzando com a parametrização de Rubricas). Só projeto_id + mesano
        (opcional) — as demais colunas da grade não fazem sentido como filtro
        de um painel agregado."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        mesano = request.args.get("mesano") or None
        return jsonify(comparacao_folha.dashboard(pid, mesano=mesano))

    @app.get("/api/comparacao-folha/export")
    def exportar_comparacao_folha():
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        filtros = _filtros_comparacao_folha()
        filtros["projeto_id"] = pid
        cabecalho, linhas, truncado = comparacao_folha.exportar_csv(**filtros)
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(cabecalho)
        writer.writerows(linhas)
        resp = app.response_class(buf.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = "attachment; filename=comparacao_folha.csv"
        if truncado:
            resp.headers["X-Export-Truncado"] = "1"
        return resp

    @app.post("/api/comparacao-folha/importar/picker")
    def importar_comparacao_folha_picker():
        """83ª rodada — deixou de baixar+importar dentro desta mesma
        requisição (o que segurava o navegador até ~15min, ver timeout do
        gunicorn no Dockerfile, pra arquivos grandes): agora só valida a
        entrada, grava uma linha em cargas_comparacao_folha (status
        em_andamento) e dispara uma THREAD separada
        (_processar_carga_comparacao_folha_picker, definida antes de
        create_app) que faz o trabalho de verdade — devolve 202 na hora,
        com o id da carga, pro usuário poder fechar a aba. Ver GET
        /comparacao-folha/carga-atual pra acompanhar o andamento depois.

        `usuario_id`/nome/email são capturados AQUI, ainda dentro da
        requisição (via `session`) — a thread não tem acesso a `session`
        (não existe fora de contexto de requisição), então precisam ser
        passados explicitamente pra auditoria.registrar_evento_manual não
        perder a autoria do evento (ver comentário grande no próprio
        registrar_evento_manual)."""
        data = request.get_json(silent=True) or {}
        projeto_id = data.get("projeto_id") or request.args.get("projeto_id")
        file_id = data.get("file_id")
        access_token = data.get("access_token")
        mime_type = data.get("mime_type")
        resource_key = data.get("resource_key")
        nome_arquivo = data.get("nome_arquivo") or file_id
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        if not file_id or not access_token:
            return jsonify({"erro": "Selecione o arquivo no Google Drive antes de importar."}), 400

        usuario_atual = auth.buscar_usuario_publico(session.get("usuario_id")) or {}

        try:
            carga = db.execute_returning_one(
                "INSERT INTO cargas_comparacao_folha (projeto_id, nome_arquivo, iniciado_por, iniciado_por_nome) "
                f"VALUES ({db.q(projeto_id)}, {db.q(nome_arquivo)}, {db.q(usuario_atual.get('id'))}, "
                f"{db.q(usuario_atual.get('nome'))}) RETURNING *"
            )
        except db.DbError as e:
            if "idx_cargas_comparacao_folha_ativa" in str(e) or "duplicate key" in str(e):
                return jsonify({
                    "erro": "Já existe uma carga de Comparação Folha em andamento para este "
                    "projeto — aguarde ela terminar (ou consulte o andamento na tela) antes "
                    "de iniciar outra."
                }), 409
            raise

        thread = threading.Thread(
            target=_processar_carga_comparacao_folha_picker,
            args=(
                carga["id"], projeto_id, file_id, mime_type, access_token, resource_key,
                nome_arquivo, usuario_atual.get("id"), usuario_atual.get("nome"), usuario_atual.get("email"),
            ),
            daemon=True,
        )
        thread.start()
        return jsonify(carga), 202

    @app.get("/api/comparacao-folha/carga-atual")
    def comparacao_folha_carga_atual():
        """83ª rodada — devolve a carga MAIS RECENTE (em_andamento,
        concluido ou erro) deste projeto, pro front-end mostrar/atualizar o
        indicador de progresso da importação pelo Google Picker — inclusive
        depois de fechar e reabrir a aba, já que a carga em si roda no
        servidor, não no navegador. `null` quando o projeto nunca teve
        nenhuma carga por esse caminho."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        carga = db.fetch_one(
            f"SELECT * FROM cargas_comparacao_folha WHERE projeto_id = {db.q(pid)} "
            "ORDER BY criado_em DESC LIMIT 1"
        )
        return jsonify(carga)

    @app.post("/api/comparacao-folha/importar/upload")
    def importar_comparacao_folha_upload():
        """Alternativa simples ao Picker acima (61ª rodada) — upload manual
        comum, igual ao usado em Rubricas/Cronograma/etc: o usuário escolhe
        o arquivo direto do computador dele, sem precisar de nenhuma
        configuração do Google (Client ID, Chave de API, tela de
        consentimento OAuth, resource key...). Único passo, sem prévia —
        mesmo motivo do /importar/picker (arquivo grande demais, até ~300
        mil linhas, pra renderizar prévia linha a linha).

        90ª rodada — passou a rodar em segundo plano, no mesmo padrão já
        usado pelo Picker (ver comentário grande em cima de
        _processar_carga_comparacao_folha_picker/_upload): antes, este
        endpoint importava o arquivo inteiro DENTRO da mesma requisição HTTP
        (csv ou xlsx, ~300 mil linhas), obrigando o usuário a ficar com a
        aba aberta esperando. Agora só valida a entrada, lê o conteúdo do
        arquivo (precisa ser aqui — request.files só existe durante a
        requisição), grava uma linha em cargas_comparacao_folha (status
        em_andamento) e dispara a importação de verdade numa THREAD
        separada — devolve 202 na hora. Front-end acompanha pelo mesmo
        banner/polling do Picker (GET /comparacao-folha/carga-atual)."""
        projeto_id = request.form.get("projeto_id")
        file = request.files.get("file")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        if not file:
            return jsonify({"erro": "Selecione o arquivo."}), 400
        nome_arquivo = file.filename
        conteudo = file.read()

        usuario_atual = auth.buscar_usuario_publico(session.get("usuario_id")) or {}

        try:
            carga = db.execute_returning_one(
                "INSERT INTO cargas_comparacao_folha (projeto_id, nome_arquivo, iniciado_por, iniciado_por_nome) "
                f"VALUES ({db.q(projeto_id)}, {db.q(nome_arquivo)}, {db.q(usuario_atual.get('id'))}, "
                f"{db.q(usuario_atual.get('nome'))}) RETURNING *"
            )
        except db.DbError as e:
            if "idx_cargas_comparacao_folha_ativa" in str(e) or "duplicate key" in str(e):
                return jsonify({
                    "erro": "Já existe uma carga de Comparação Folha em andamento para este "
                    "projeto — aguarde ela terminar (ou consulte o andamento na tela) antes "
                    "de iniciar outra."
                }), 409
            raise

        thread = threading.Thread(
            target=_processar_carga_comparacao_folha_upload,
            args=(
                carga["id"], projeto_id, conteudo, nome_arquivo,
                usuario_atual.get("id"), usuario_atual.get("nome"), usuario_atual.get("email"),
            ),
            daemon=True,
        )
        thread.start()
        return jsonify(carga), 202

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

    @app.get("/api/cronograma/exportar")
    def cronograma_exportar():
        """Exporta as atividades do projeto numa planilha Excel — botão
        "Exportar Cronograma (Excel)" ao lado de Importar/Versões do
        Cronograma. Filtros de Etapa, Frente de trabalho e período previsto
        são opcionais (mesma semântica de sobreposição de intervalo já usada
        em GET /atividades) — sem nenhum, exporta o cronograma inteiro do
        projeto. Ver app/cronograma_export.py."""
        projeto_id = request.args.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        projeto = db.fetch_one(f"SELECT nome, sigla FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        where = [f"a.projeto_id = {db.q(projeto_id)}"]
        if request.args.get("etapa_id"):
            where.append(f"a.etapa_id = {db.q(request.args['etapa_id'])}")
        if request.args.get("frente_trabalho_id"):
            where.append(f"a.frente_trabalho_id = {db.q(request.args['frente_trabalho_id'])}")
        if request.args.get("periodo_inicio") and request.args.get("periodo_fim"):
            where.append(
                f"a.dtini_prev <= {db.q(request.args['periodo_fim'])} "
                f"AND a.dtfim_prev >= {db.q(request.args['periodo_inicio'])}"
            )
        # 68ª rodada — mesma ordenação da grade do Cronograma (por Código, não
        # por data), ver comentário em list_atividades() acima.
        sql = (ATIVIDADE_SELECT + " WHERE " + " AND ".join(where)
               + " ORDER BY codigo_wbs_chave_ordenacao(a.codigo_wbs) NULLS LAST, a.nome")
        atividades = db.fetch_all(sql)
        # Dependências do projeto inteiro (45ª rodada, coluna "Depende de" da
        # planilha) — uma única consulta pra todo mundo, mesmo padrão de
        # list_dependencias_projeto logo acima, em vez de uma por atividade.
        deps_rows = db.fetch_all(
            "SELECT ad.atividade_id, p.codigo_wbs AS predecessora_codigo_wbs, ad.tipo, ad.lag_horas "
            "FROM atividade_dependencia ad "
            "JOIN atividades p ON p.id = ad.predecessora_id "
            "JOIN atividades s ON s.id = ad.atividade_id "
            f"WHERE s.projeto_id = {db.q(projeto_id)} AND p.projeto_id = {db.q(projeto_id)}"
        )
        deps_por_atividade = {}
        for r in deps_rows:
            deps_por_atividade.setdefault(r["atividade_id"], []).append(r)
        try:
            xlsx_bytes = cronograma_export.gerar_planilha_bytes(atividades, deps_por_atividade)
        except Exception as e:
            print(f"[cronograma_export] erro ao gerar planilha: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao gerar a planilha: {e}"}), 500
        # Nome de arquivo derivado da sigla/nome do projeto — sanitizado na unha (sem
        # regex extra) pra nunca deixar caractere fora do comum quebrar o header
        # Content-Disposition, por mais improvável que seja com os dados atuais.
        base = (projeto.get("sigla") or projeto.get("nome") or "projeto").strip()
        base = "".join(c if c.isalnum() or c in "-_" else "-" for c in base).strip("-") or "projeto"
        nome_arquivo = f"cronograma-{base}-{date.today().isoformat()}.xlsx"
        return send_file(
            io.BytesIO(xlsx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=nome_arquivo,
        )

    @app.get("/api/cronograma/exportar-xml")
    def cronograma_exportar_xml():
        """Exporta o cronograma do projeto INTEIRO (sem filtros — ver
        justificativa no docstring de app/cronograma_export_xml.py) num XML
        MSPDI. Botão "Exportar XML (ProjectLibre)" na tela de Cronograma,
        69ª rodada — pedido explícito do cliente: incluir dependências e %
        de conclusão. Botão irmão "Exportar XML (Microsoft Project)",
        73ª rodada: mesmo gerador, mesmo arquivo — só muda o NAMESPACE do
        `<Project>` raiz (`alvo=projectlibre|msproject`, ver "Dois botões,
        dois namespaces" no docstring de cronograma_export_xml.py) e o nome
        do arquivo baixado, pra deixar claro pra qual ferramenta cada
        download foi pensado."""
        projeto_id = request.args.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        alvo = request.args.get("alvo", "projectlibre")
        if alvo not in ("projectlibre", "msproject"):
            return jsonify({"erro": "alvo inválido — use 'projectlibre' ou 'msproject'"}), 400
        namespace = (
            cronograma_export_xml.NAMESPACE_MS_PROJECT if alvo == "msproject"
            else cronograma_export_xml.NAMESPACE_PROJECTLIBRE
        )
        projeto = db.fetch_one(f"SELECT * FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        # mesma ordenação por Código (natural, não textual) usada na grade —
        # ver docstring do módulo: é o que garante a sequência DFS correta
        # (pai antes dos filhos, filhos antes do próximo irmão) que o MSPDI
        # espera pela combinação OutlineLevel + ordem de aparição no arquivo.
        sql = (ATIVIDADE_SELECT + f" WHERE a.projeto_id = {db.q(projeto_id)} "
               "ORDER BY codigo_wbs_chave_ordenacao(a.codigo_wbs) NULLS LAST, a.nome")
        atividades = db.fetch_all(sql)
        marcos = db.fetch_all(
            f"SELECT * FROM marcos WHERE projeto_id = {db.q(projeto_id)} ORDER BY data_prevista"
        )
        deps_rows = db.fetch_all(
            "SELECT ad.atividade_id, ad.predecessora_id, ad.tipo, ad.lag_horas "
            "FROM atividade_dependencia ad "
            "JOIN atividades s ON s.id = ad.atividade_id "
            "JOIN atividades p ON p.id = ad.predecessora_id "
            f"WHERE s.projeto_id = {db.q(projeto_id)} AND p.projeto_id = {db.q(projeto_id)}"
        )
        deps_por_atividade = {}
        for r in deps_rows:
            deps_por_atividade.setdefault(r["atividade_id"], []).append(r)
        try:
            xml_bytes = cronograma_export_xml.gerar_xml_bytes(
                projeto, atividades, marcos, deps_por_atividade, namespace=namespace,
            )
        except Exception as e:
            print(f"[cronograma_export_xml] erro ao gerar XML: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao gerar o XML: {e}"}), 500
        base = (projeto.get("sigla") or projeto.get("nome") or "projeto").strip()
        base = "".join(c if c.isalnum() or c in "-_" else "-" for c in base).strip("-") or "projeto"
        sufixo_alvo = "ms-project" if alvo == "msproject" else "projectlibre"
        nome_arquivo = f"cronograma-{base}-{sufixo_alvo}-{date.today().isoformat()}.xml"
        return send_file(
            io.BytesIO(xml_bytes),
            mimetype="application/xml",
            as_attachment=True, download_name=nome_arquivo,
        )

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

    @app.get("/api/cronograma/versoes/<id>/comparar")
    def cronograma_comparar_versao(id):
        """Planilha comparando esta versão salva contra o cronograma AO VIVO
        — botão "📊 Comparar" na lista de Versões. Ver app/cronograma_comparacao.py
        pra regra completa (casamento por id, azul = atividade nova, amarelo
        = campo que mudou)."""
        versao = cronograma_versoes.obter_versao(id)
        if not versao:
            abort(404)
        projeto_id = versao["projeto_id"]
        projeto = db.fetch_one(f"SELECT nome, sigla FROM projetos WHERE id = {db.q(projeto_id)}")
        atividades_atuais = db.fetch_all(
            ATIVIDADE_SELECT + f" WHERE a.projeto_id = {db.q(projeto_id)} ORDER BY a.codigo_wbs NULLS LAST, a.nome"
        )
        # Dependências atuais, no mesmo formato usado por /cronograma/exportar.
        deps_atuais_rows = db.fetch_all(
            "SELECT ad.atividade_id, p.codigo_wbs AS predecessora_codigo_wbs, ad.tipo, ad.lag_horas "
            "FROM atividade_dependencia ad "
            "JOIN atividades p ON p.id = ad.predecessora_id "
            "JOIN atividades s ON s.id = ad.atividade_id "
            f"WHERE s.projeto_id = {db.q(projeto_id)} AND p.projeto_id = {db.q(projeto_id)}"
        )
        deps_atuais_por_atividade = {}
        for r in deps_atuais_rows:
            deps_atuais_por_atividade.setdefault(r["atividade_id"], []).append(r)
        # Dependências da versão já vêm resolvidas dentro do próprio snapshot
        # (dados.dependencias — ver cronograma_versoes.montar_snapshot).
        deps_versao_por_atividade = {}
        for r in (versao.get("dados") or {}).get("dependencias") or []:
            deps_versao_por_atividade.setdefault(r["atividade_id"], []).append(r)
        try:
            xlsx_bytes = cronograma_comparacao.gerar_planilha_bytes(
                versao, atividades_atuais, deps_versao_por_atividade, deps_atuais_por_atividade
            )
        except Exception as e:
            print(f"[cronograma_comparacao] erro ao gerar planilha: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"erro": f"Falha ao gerar a planilha de comparação: {e}"}), 500
        base = (projeto.get("sigla") or projeto.get("nome") or "projeto").strip() if projeto else "projeto"
        base = "".join(c if c.isalnum() or c in "-_" else "-" for c in base).strip("-") or "projeto"
        nome_arquivo = f"comparacao-v{versao['numero_versao']}-{base}-{date.today().isoformat()}.xlsx"
        return send_file(
            io.BytesIO(xlsx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=nome_arquivo,
        )

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

    # ------------------------------------------------ renumeração do cronograma
    # "Renumerar" (67ª rodada): recalcula o Código WBS de todas as atividades
    # do projeto por Etapa → Frente → sequencial (e sub-hierarquia real via
    # atividade_pai_id, quando existir) — ver cronograma_renumeracao.py para
    # o diagnóstico completo e o esquema de numeração escolhido. Mesmo padrão
    # preview/confirmar das outras operações em lote do cronograma acima.
    @app.post("/api/cronograma/renumerar/preview")
    def cronograma_renumerar_preview():
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        try:
            resultado = cronograma_renumeracao.calcular(projeto_id)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        itens = resultado["itens"]
        alterados = [i for i in itens if i["mudou"]]
        return jsonify({
            "total_atividades": len(itens),
            "total_alteradas": len(alterados),
            "total_ignoradas": len(resultado["ignoradas"]),
            "itens": itens,
            "ignoradas": resultado["ignoradas"],
        })

    @app.post("/api/cronograma/renumerar/confirmar")
    def cronograma_renumerar_confirmar():
        data = request.get_json(force=True) or {}
        projeto_id = data.get("projeto_id")
        if not projeto_id:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(projeto_id)}")
        if not projeto:
            abort(404)
        try:
            resultado = cronograma_renumeracao.aplicar(projeto_id)
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        auditoria.registrar_evento_manual(
            "edicao",
            f'Renumerou o Código WBS de {resultado["atividades_renumeradas"]} atividade(s) do cronograma do '
            f'projeto "{projeto["nome"]}"'
            + (f' ({resultado["total_ignoradas"]} sem Etapa/Frente definidas, não renumerada(s))'
               if resultado["total_ignoradas"] else '') + '.',
            entidade="cronograma", entidade_id=str(projeto_id), entidade_rotulo=projeto["nome"],
            projeto_id=projeto_id, sensivel=True,
            detalhes={
                "atividades_renumeradas": resultado["atividades_renumeradas"],
                "ids_alterados": resultado["ids_alterados"],
                "total_ignoradas": resultado["total_ignoradas"],
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

    @app.get("/api/relatorios/anomalias-data-fixada")
    def relatorio_anomalias_data_fixada():
        """57ª/58ª rodada — atividades com data_execucao_fixada=true cuja data prevista
        já não respeita o que uma predecessora direta exige hoje (ver
        app/cronograma_anomalias.py). Checagem "ao vivo", independente de o usuário ter
        rodado Replanejamento ou Edição em Lote — pro card ⚠📌 do Dashboard."""
        pid = request.args.get("projeto_id")
        if not pid:
            return jsonify({"erro": "projeto_id é obrigatório"}), 400
        if not db.fetch_one(f"SELECT id FROM projetos WHERE id = {db.q(pid)}"):
            return jsonify({"erro": "Projeto não encontrado"}), 404
        anomalias = cronograma_anomalias.detectar(pid, STATUS_FAMILIA_CONCLUIDA)
        return jsonify({"anomalias": anomalias, "total": len(anomalias)})

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
        if 'violates check constraint "atividades_check' in baixo:
            # Rede de segurança pro mesmo caso que validar_ordem_datas() já pega no modal
            # individual e create/update_atividade — cobre quem grava `atividades` por um
            # caminho que não passa por lá (ex: cronograma_import.py, que escreve direto
            # via SQL). Mensagem genérica de propósito: não dá pra saber aqui qual dos dois
            # pares (Previsto/Real) foi o culpado, só que um Fim ficou antes do Início.
            return jsonify({
                "erro": "Não é possível salvar: a Data de Fim (Previsto ou Real) ficaria anterior à "
                        "Data de Início correspondente. Confira as datas da atividade.",
            }), 400
        return jsonify({"erro": msg}), 500

    # ------------------------------------------------------------ front-end estático
    def _servir_index_html():
        # 119ª rodada — Cache-Control: no-store aqui de propósito (ver
        # comentário em nao_cachear_api acima): o navegador NUNCA deve reter
        # essa resposta associada à URL que a gerou. Sem isso, qualquer rota
        # /api/* nova que nasça durante a janela de um rolling deploy (o
        # processo antigo do gunicorn ainda respondendo, sem a rota nova, e
        # por isso caindo neste fallback de SPA) corre o risco de ficar presa
        # no cache do navegador, mesmo depois do deploy novo já estar no ar.
        resp = send_from_directory(FRONTEND_DIR, "index.html")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    def index():
        return _servir_index_html()

    @app.get("/<path:path>")
    def static_files(path):
        full = os.path.join(FRONTEND_DIR, path)
        if os.path.isfile(full):
            return send_from_directory(FRONTEND_DIR, path)
        return _servir_index_html()

    # 113ª rodada — liga a thread de agendamento da atualização automática de
    # Rubricas (seg-sex, 8h-18h, horário de Brasília). Sem efeito nenhum se
    # RUBRICAS_AUTO_UPDATE_PROJETO_ID não estiver configurada — ver
    # app/rubricas_auto_update.py.
    rubricas_auto_update.iniciar_agendador()

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
