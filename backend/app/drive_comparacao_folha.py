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

Desde a 55ª rodada (Adendo 3), também passa um `validar` pra
baixar_arquivo_mais_recente() — mesmo problema visto em produção com
Rubricas (pasta compartilhada com outros arquivos não relacionados que por
acaso batem o tipo MIME reconhecido) pode acontecer aqui também. A
validação usa comparacao_folha_import.validar_estrutura(), que só confere
o cabeçalho (barata mesmo pra um arquivo de ~130MB — não lê as ~300 mil
linhas de dados), pra não pagar o custo de rodar a importação inteira só
pra descobrir que o arquivo é o errado.

Desde a 55ª rodada (Adendo 4), também aceita `.csv` (google_drive.MIMES_CSV)
além de .xlsx/.xlsm/Planilha Google — visto em produção: o arquivo de
verdade que o sistema legado exporta pra essa pasta é um .csv
("LISTA_COMPARA FOLHAS_....csv"), não uma planilha Excel. `validar_estrutura`
e o resto do parser (`comparacao_folha_import.py`) já reconhecem CSV
automaticamente (detectam pelo conteúdo, não pela extensão do nome), então
nada muda aqui além de aceitar o MIME na busca.
"""
from . import comparacao_folha_import, google_drive

DriveComparacaoFolhaError = google_drive.GoogleDriveError


def baixar_planilha_mais_recente():
    """Retorna (conteudo_bytes, nome_arquivo, modificado_em_iso) da
    planilha de Comparação Folha encontrada na pasta configurada em
    GOOGLE_DRIVE_COMPARACAO_FOLDER_ID — tentando, em ordem de modificação
    mais recente primeiro, até achar um arquivo cujo cabeçalho realmente
    valide como Comparação Folha (ver comparacao_folha_import.
    validar_estrutura e o comentário no topo do arquivo). Aceita tanto
    planilha (.xlsx/.xlsm/Planilha Google) quanto .csv — o formato real que
    o sistema legado exporta pra essa pasta. Levanta DriveComparacaoFolhaError
    com uma mensagem pronta pra mostrar ao usuário se falhar em qualquer
    etapa."""
    return google_drive.baixar_arquivo_mais_recente(
        folder_id_env="GOOGLE_DRIVE_COMPARACAO_FOLDER_ID",
        contexto="Comparação Folha",
        validar=comparacao_folha_import.validar_estrutura,
        mimes_extra=google_drive.MIMES_CSV,
    )
