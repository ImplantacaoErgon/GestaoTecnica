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

Desde a 55ª rodada (Adendo 3), também passa um `validar` pra
baixar_arquivo_mais_recente(): visto em produção que a pasta compartilhada
no Drive tinha, além da planilha de Rubricas de verdade, um OUTRO arquivo
("Especificação da migração de Atributos.xlsx") que por acaso também tem
uma aba chamada "Levantamento Rubricas" — e que estava mais recentemente
modificado, sendo escolhido por engano pela regra antiga de "sempre pega o
mais recente". Com o validador, se o mais recente não tiver o cabeçalho
certo, o sistema tenta o próximo mais recente antes de desistir.
"""
from . import google_drive, rubricas_import

DriveRubricasError = google_drive.GoogleDriveError


def _validar_conteudo(conteudo_bytes):
    """Levanta RubricasImportError se `conteudo_bytes` não parecer a
    planilha "Levantamento Rubricas" (aba/cabeçalho errado) — reaproveita o
    parser de verdade; o resultado do parse é descartado aqui, só a
    validação importa (quem usa o parse de fato é o endpoint de prévia em
    main.py, chamado de novo depois que este módulo já escolheu o arquivo
    certo)."""
    rubricas_import.parse_rubricas_document(conteudo_bytes)


def baixar_planilha_mais_recente():
    """Retorna (conteudo_bytes, nome_arquivo, modificado_em_iso) da
    planilha de Rubricas encontrada na pasta configurada em
    GOOGLE_DRIVE_RUBRICAS_FOLDER_ID — tentando, em ordem de modificação
    mais recente primeiro, até achar um arquivo cujo conteúdo realmente
    valide como Rubricas (ver _validar_conteudo e o comentário no topo do
    arquivo). Levanta DriveRubricasError com uma mensagem pronta pra
    mostrar ao usuário se falhar em qualquer etapa."""
    return google_drive.baixar_arquivo_mais_recente(
        folder_id_env="GOOGLE_DRIVE_RUBRICAS_FOLDER_ID",
        contexto="Rubricas",
        validar=_validar_conteudo,
    )
