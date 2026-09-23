"""
Busca automática da planilha de Comparação Folha (Ergon x ficha financeira do
legado) direto do Google Drive (55ª rodada) — o botão "Atualizar Comparação"
da tela chama isso.

Mesma implementação compartilhada de app/drive_rubricas.py — ver
app/google_drive.py. Usa a MESMA conta de serviço do Google Cloud (mesma
GOOGLE_SERVICE_ACCOUNT_JSON) já configurada pra Rubricas — só precisa
compartilhar esta OUTRA pasta do Drive com o mesmo e-mail de conta de
serviço, e configurar GOOGLE_DRIVE_COMPARACAO_FOLDER_ID apontando pra ela.
Não é preciso criar uma segunda conta de serviço.

Diferente de Rubricas: este arquivo é MUITO maior (~130MB, ~300 mil linhas)
— ver app/comparacao_folha_import.py pra como isso é lido e gravado sem
carregar tudo de uma vez na memória.
"""
from . import google_drive

DriveComparacaoFolhaError = google_drive.GoogleDriveError


def baixar_planilha_mais_recente():
    """Retorna (conteudo_bytes, nome_arquivo, modificado_em_iso) do arquivo
    mais recentemente modificado na pasta configurada em
    GOOGLE_DRIVE_COMPARACAO_FOLDER_ID. Levanta DriveComparacaoFolhaError com
    uma mensagem pronta pra mostrar ao usuário se falhar em qualquer etapa."""
    return google_drive.baixar_arquivo_mais_recente(
        folder_id_env="GOOGLE_DRIVE_COMPARACAO_FOLDER_ID", contexto="Comparação Folha"
    )
