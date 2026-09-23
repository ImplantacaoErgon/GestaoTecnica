"""
Busca automática da planilha "Levantamento Rubricas" direto do Google Drive
(54ª rodada) — alternativa ao upload manual: o botão "Atualizar Rubricas" da
tela chama isso em vez de pedir um arquivo.

A implementação de verdade (autenticação com conta de serviço, listagem da
pasta, download) é compartilhada com o botão "Atualizar Comparação Folha" —
ver app/google_drive.py. Este módulo é só uma casca fina com o nome da
variável de ambiente da pasta de Rubricas (GOOGLE_DRIVE_RUBRICAS_FOLDER_ID) e
o nome do erro (DriveRubricasError) que o resto do código (main.py, testes)
já espera — mantido separado por clareza de import (`drive_rubricas.
baixar_planilha_mais_recente()`) e pra não precisar tocar em main.py de novo.
"""
from . import google_drive

DriveRubricasError = google_drive.GoogleDriveError


def baixar_planilha_mais_recente():
    """Retorna (conteudo_bytes, nome_arquivo, modificado_em_iso) do arquivo
    mais recentemente modificado na pasta configurada em
    GOOGLE_DRIVE_RUBRICAS_FOLDER_ID. Levanta DriveRubricasError com uma
    mensagem pronta pra mostrar ao usuário se falhar em qualquer etapa."""
    return google_drive.baixar_arquivo_mais_recente(
        folder_id_env="GOOGLE_DRIVE_RUBRICAS_FOLDER_ID", contexto="Rubricas"
    )
