"""
Atualização automática das Rubricas (113ª rodada) -- pedido do usuário
(verbatim): "crie um processo de atualização das rubricas automático de
hora em hora, de segunda a sexta das 8h às 18h no horário de Brasília.
Mantenha o botão de atualização manual."

Roda exatamente o mesmo fluxo do botão "🔄 Atualizar Rubricas" (54ª rodada
-- ver app/drive_rubricas.py e as rotas /api/rubricas/importar/drive/preview
+ /confirmar em main.py): busca a planilha "Levantamento Rubricas" mais
recente na pasta do Google Drive configurada e grava via upsert -- sozinho,
sem precisar que alguém abra o sistema e clique no botão. O botão manual
continua existindo e funcionando exatamente como antes, sem nenhuma
mudança -- este módulo só adiciona um disparo automático que usa o mesmo
caminho de código por baixo.

Por que chama as funções Python direto (drive_rubricas + rubricas_import +
upsert_rubricas), em vez de bater nos endpoints HTTP de sempre: TODA rota
/api/* exige sessão autenticada (ver @app.before_request exigir_login em
main.py) -- um processo de fundo não tem usuário logado nem cookie de
sessão, então teria que fingir um login só pra isso. Rodando dentro do
próprio processo, pula o HTTP inteiro e chama a mesma lógica de negócio de
verdade (a mesma função que o botão aciona por trás, só que sem passar por
Flask).

Mesmo comportamento do botão manual ao confirmar a prévia sem desmarcar
nenhuma linha (a prévia já vem com tudo marcado por padrão -- ver
_exibirPreviewRubricas no front-end) -- ou seja, a atualização automática
aplica TODAS as linhas da planilha mais recente do Drive, sem revisão
humana linha a linha. Isso é seguro porque upsert_rubricas (main.py) é um
UPSERT por linha_planilha -- rodar de novo com a mesma planilha não
duplica nada, só atualiza o que mudou -- e essa planilha já é mantida
manualmente pela equipe do cliente num ritmo próprio (diferente de, por
exemplo, uma importação de cronograma, que o usuário revisa linha a linha
de propósito).

Concorrência entre os workers do gunicorn (`-w 4`, ver Dockerfile): cada um
dos 4 processos liga sua própria thread de agendamento (mais simples do que
coordenar uma única thread entre processos diferentes, e não exige
dependência nova). A trava que evita 4 disparos simultâneos por hora é o
índice único (projeto_id, janela) de rubricas_auto_update_execucoes (ver
migração 037) -- só o worker cujo INSERT tiver sucesso executa de verdade;
os outros recebem erro de chave duplicada e não fazem nada.
"""
import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, drive_rubricas, rubricas_import

logger = logging.getLogger(__name__)

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

# Janela de disparo -- pedido do usuário (verbatim): "de hora em hora, de
# segunda a sexta das 8h às 18h no horário de Brasília". Interpretado como
# as duas pontas incluídas (dispara às 8h, 9h, ..., 18h -- 11 disparos por
# dia útil), sempre no horário de Brasília (zoneinfo, não um offset fixo --
# resolve corretamente mesmo se o Brasil voltar a adotar horário de verão).
DIAS_SEMANA_ATIVOS = range(0, 5)  # segunda=0 ... sexta=4 (sábado/domingo fora)
HORA_INICIO = 8
HORA_FIM = 18

# Checagem a cada 30s -- não precisa ser mais fino que isso (o disparo em si
# é por HORA cheia); só precisa ser frequente o bastante pra não perder o
# minuto 0 de cada hora.
INTERVALO_VERIFICACAO_SEGUNDOS = 30


def _projeto_id_configurado():
    return (os.environ.get("RUBRICAS_AUTO_UPDATE_PROJETO_ID") or "").strip() or None


def _deve_disparar_agora(agora):
    return (
        agora.weekday() in DIAS_SEMANA_ATIVOS
        and HORA_INICIO <= agora.hour <= HORA_FIM
        and agora.minute == 0
    )


def _executar_atualizacao(projeto_id, janela):
    """Faz de fato a atualização -- mesmo caminho do botão manual: baixa a
    planilha mais recente do Drive, faz o parse (mesmo parser/validador do
    endpoint de prévia) e grava via upsert, com TODAS as linhas (nenhuma
    desmarcada -- ver docstring do módulo). Qualquer erro (Drive fora do ar,
    credencial faltando, planilha no formato errado) fica registrado na
    própria linha de execução, pra aparecer na tela -- nunca sobe e derruba
    a thread de agendamento."""
    from .main import upsert_rubricas  # import tardio -- evita ciclo (main.py importa este módulo no topo)

    try:
        conteudo, nome_arquivo, _modificado_em = drive_rubricas.baixar_planilha_mais_recente()
        resultado = rubricas_import.parse_rubricas_document(conteudo, nome_arquivo or "")
        itens = [it for it in (resultado.get("itens") or []) if it.get("linha_planilha")]
        inseridos, atualizados = upsert_rubricas(projeto_id, itens) if itens else (0, 0)
        db.execute(
            "UPDATE rubricas_auto_update_execucoes SET status = 'concluido', "
            f"nome_arquivo = {db.q(nome_arquivo)}, inseridos = {db.q(inseridos)}, "
            f"atualizados = {db.q(atualizados)}, concluido_em = now() "
            f"WHERE projeto_id = {db.q(projeto_id)} AND janela = {db.q(janela)}"
        )
        logger.info(
            "Atualização automática de Rubricas concluída (janela %s): %s nova(s), %s atualizada(s).",
            janela, inseridos, atualizados,
        )
    except Exception as e:
        try:
            db.execute(
                "UPDATE rubricas_auto_update_execucoes SET status = 'erro', "
                f"mensagem_erro = {db.q(str(e))}, concluido_em = now() "
                f"WHERE projeto_id = {db.q(projeto_id)} AND janela = {db.q(janela)}"
            )
        except db.DbError:
            pass  # nem o próprio registro de erro deve derrubar a thread
        logger.exception("Falha na atualização automática de Rubricas (janela %s).", janela)


def _tentar_disparar(projeto_id, agora):
    """INSERT contra o índice único (projeto_id, janela) -- é a trava entre
    os N workers do gunicorn (ver docstring do módulo e migração 037): só
    quem conseguir inserir de verdade executa a atualização; os demais caem
    no `except` (chave duplicada) e não fazem nada."""
    janela = agora.strftime("%Y-%m-%d %H")
    try:
        db.execute(
            "INSERT INTO rubricas_auto_update_execucoes (projeto_id, janela) VALUES "
            f"({db.q(projeto_id)}, {db.q(janela)})"
        )
    except db.DbError as e:
        if "duplicate key" in str(e).lower() or "idx_rubricas_auto_update_janela" in str(e):
            return  # outro worker já pegou esta janela -- nada a fazer aqui
        logger.exception(
            "Falha ao registrar disparo da atualização automática de Rubricas (janela %s).", janela
        )
        return
    _executar_atualizacao(projeto_id, janela)


def _loop_agendador(projeto_id):
    logger.info(
        "Agendador de atualização automática de Rubricas iniciado (projeto %s, seg-sex 8h-18h, "
        "horário de Brasília).", projeto_id,
    )
    while True:
        try:
            agora = datetime.now(FUSO_BRASILIA)
            if _deve_disparar_agora(agora):
                _tentar_disparar(projeto_id, agora)
        except Exception:
            logger.exception("Erro inesperado no loop do agendador de atualização automática de Rubricas.")
        time.sleep(INTERVALO_VERIFICACAO_SEGUNDOS)


def iniciar_agendador():
    """Chamado uma vez por processo (create_app(), em main.py) -- liga a
    thread de agendamento só se RUBRICAS_AUTO_UPDATE_PROJETO_ID estiver
    configurada (o projeto ao qual a atualização automática se aplica; a
    pasta do Drive em si já é global -- GOOGLE_DRIVE_RUBRICAS_FOLDER_ID, ver
    drive_rubricas.py). Sem essa variável, o recurso fica silenciosamente
    desligado -- mesmo padrão já usado pelas outras integrações opcionais
    deste sistema (IA, Drive, SMTP): falta de configuração nunca derruba o
    resto do sistema, e o botão manual continua funcionando normalmente."""
    projeto_id = _projeto_id_configurado()
    if not projeto_id:
        logger.info(
            "RUBRICAS_AUTO_UPDATE_PROJETO_ID não configurada -- atualização automática de "
            "Rubricas desligada (o botão manual continua funcionando normalmente)."
        )
        return
    thread = threading.Thread(target=_loop_agendador, args=(projeto_id,), daemon=True)
    thread.start()


def ultima_execucao(projeto_id):
    """Última execução automática (qualquer status) deste projeto -- usado
    pelo GET /api/rubricas/auto-update/status, que alimenta o aviso
    "Última atualização automática" na tela de Rubricas."""
    return db.fetch_one(
        "SELECT * FROM rubricas_auto_update_execucoes WHERE projeto_id = "
        f"{db.q(projeto_id)} ORDER BY iniciado_em DESC LIMIT 1"
    )
