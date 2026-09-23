"""
Busca automática de um arquivo direto do Google Drive — implementação
compartilhada usada tanto pelo botão "Atualizar Rubricas" (54ª rodada,
app/drive_rubricas.py) quanto pelo botão "Atualizar Comparação Folha" (55ª
rodada, app/drive_comparacao_folha.py). Cada um chama
baixar_arquivo_mais_recente() com seu próprio nome de variável de ambiente
pra pasta — dá pra usar a MESMA conta de serviço do Google Cloud pras duas
coisas, só compartilhando as duas pastas do Drive com o e-mail dela (não
precisa criar uma conta de serviço por planilha).

Autenticação via CONTA DE SERVIÇO do Google Cloud — não é usuário/senha do
Google. Uma conta de serviço é uma identidade só para automação: você cria
uma no Google Cloud Console, baixa uma chave (arquivo JSON) e compartilha a
PASTA do Drive com o e-mail dela (algo como
"algumacoisa@algumprojeto.iam.gserviceaccount.com"), do mesmo jeito que se
compartilha uma pasta com uma pessoa. Sem senha nenhuma envolvida, e o
acesso pode ser revogado a qualquer momento removendo o compartilhamento ou
apagando a conta de serviço. Ver .env.example (GOOGLE_SERVICE_ACCOUNT_JSON e
as variáveis de pasta de cada integração) para o passo a passo completo.

Sem GOOGLE_SERVICE_ACCOUNT_JSON configurada, o resto do sistema funciona
normalmente — só o botão que depende disso (e nada mais) devolve um erro
explicando o que falta, seguindo o mesmo padrão já usado pro Relatório
Executivo/IA (ANTHROPIC_API_KEY) e pro "Esqueci a senha" (SMTP_*).

Por padrão, lê o arquivo MAIS RECENTEMENTE MODIFICADO dentro da pasta
configurada — não um nome fixo — porque o cliente sobe revisões da planilha
com sufixos diferentes a cada vez (ex: "..._revisadas_5.xlsx", depois "_6",
"_7"...). Desde a 55ª rodada (Adendo 3), quando o chamador passa um
`validar` (ver baixar_arquivo_mais_recente), essa regra vira "o mais recente
QUE VALIDAR como o tipo certo de planilha" — tentando os próximos mais
antigos antes de desistir — porque, em produção, a pasta compartilhada
acabou tendo outros arquivos não relacionados que também batiam o tipo MIME
reconhecido, e um deles era mais recente que a planilha de verdade.
"""
import io
import json
import os
import re

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Erro real visto em produção (55ª rodada): a variável de ambiente da pasta
# recebeu "folders/<id>" (ou o link inteiro da pasta) em vez de só o `<id>`
# puro que o campo espera — o Google então devolve "File not found" pro
# fileId (a própria string "folders/<id>" tratada como se fosse um id),
# confundindo com "pasta não compartilhada". Casa tanto "folders/<id>" quanto
# o link completo ("https://drive.google.com/drive/folders/<id>?usp=sharing")
# — em ambos os casos [a-zA-Z0-9_-]+ para exatamente no "?" ou "/" seguinte,
# isolando só o id.
_FOLDER_URL_ID_RE = re.compile(r"folders/([a-zA-Z0-9_-]+)")

# Tipos MIME que tratamos como "é uma planilha que dá pra importar": .xlsx
# ou .xlsm de verdade (baixados direto, sem conversão — o formato de arquivo
# é o mesmo, .xlsm só tem macro a mais, que os parsers ignoram), ou uma
# Planilha Google nativa (baixada via export, convertendo pra .xlsx na hora
# — os parsers só entendem .xlsx/.xlsm). NÃO inclui o formato antigo .xls
# (BIFF) — openpyxl não lê esse formato, e ele tem limite de 65.536 linhas,
# incompatível com os volumes deste sistema (Comparação Folha chega a ~300
# mil linhas por arquivo).
_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"
_MIME_GOOGLE_SHEETS = "application/vnd.google-apps.spreadsheet"
_MIMES_RECONHECIDOS = {_MIME_XLSX, _MIME_XLSM, _MIME_GOOGLE_SHEETS}

# CSV — NÃO faz parte de _MIMES_RECONHECIDOS por padrão (só faz sentido pra
# Comparação Folha: visto em produção, na 55ª rodada, que o arquivo real da
# pasta de Comparação Folha é um .csv exportado direto do sistema legado,
# não um .xlsx — a planilha de Rubricas, em contraste, é editada à mão em
# Excel e não teria por que virar CSV). Quem quiser aceitar CSV também passa
# `mimes_extra=MIMES_CSV` pra baixar_arquivo_mais_recente (ver
# drive_comparacao_folha.py). Inclui duas variantes de MIME além da
# "correta" (text/csv) por tolerância — o Drive/navegador às vezes classifica
# um .csv como texto simples dependendo de como foi subido.
_MIME_CSV = "text/csv"
MIMES_CSV = {_MIME_CSV, "text/plain", "application/csv"}

_DESCRICAO_MIME = {
    _MIME_XLSX: ".xlsx",
    _MIME_XLSM: ".xlsm",
    _MIME_GOOGLE_SHEETS: "Planilha Google",
    _MIME_CSV: ".csv",
}


def _descricao_formatos(mimes_aceitos):
    """Texto tipo ".xlsx, .xlsm, Planilha Google ou .csv" pras mensagens de
    erro, montado a partir de quais MIMEs estão em `mimes_aceitos` — só
    lista os "principais" de _DESCRICAO_MIME (ignora as variantes de
    tolerância como text/plain, pra não poluir a mensagem)."""
    nomes = [nome for mime, nome in _DESCRICAO_MIME.items() if mime in mimes_aceitos]
    if not nomes:
        return "planilha"
    if len(nomes) == 1:
        return nomes[0]
    return ", ".join(nomes[:-1]) + " ou " + nomes[-1]


class GoogleDriveError(Exception):
    """Erro de configuração ou de comunicação com o Google Drive — a
    mensagem já vem pronta pra aparecer direto pro usuário na tela."""
    pass


def _service_account_info():
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        raise GoogleDriveError(
            "Busca automática do Google Drive não configurada — falta a variável de "
            "ambiente GOOGLE_SERVICE_ACCOUNT_JSON (a chave da conta de serviço do "
            "Google Cloud, em JSON). Veja .env.example."
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise GoogleDriveError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON está configurada, mas não é um JSON válido: {e}"
        )


def _folder_id(folder_id_env):
    raw = (os.environ.get(folder_id_env) or "").strip()
    if not raw:
        raise GoogleDriveError(
            f"Busca automática do Google Drive não configurada — falta a variável de "
            f"ambiente {folder_id_env} (o id da pasta do Drive onde fica o arquivo). "
            "Veja .env.example."
        )
    # Aceita tanto o id puro (o esperado, ex: "1mdojIv1WRvpAr1AmdOoGsba3I1VK3Vam")
    # quanto, por tolerância a erro de copiar e colar, "folders/<id>" ou o
    # link inteiro da pasta — extrai só o id nesses dois últimos casos em vez
    # de mandar a string toda pro Google (que devolve "File not found" nesse
    # caso, uma mensagem que parece "pasta não compartilhada" mas não é).
    m = _FOLDER_URL_ID_RE.search(raw)
    return m.group(1) if m else raw.strip("/ ")


def _google_libs():
    """Import adiado dos três símbolos do google-api-python-client/google-auth
    usados aqui, todos num lugar só — de propósito: quem não usa nenhuma
    dessas integrações não precisa ter esses pacotes instalados pra o resto
    do sistema funcionar (mesmo espírito do ANTHROPIC_API_KEY ausente não
    travar o resto do relatório executivo). Qualquer chamador (inclusive
    _baixar_conteudo, que também precisa de MediaIoBaseDownload) passa por
    aqui, então o ModuleNotFoundError nunca escapa cru — sempre vira
    GoogleDriveError com a mensagem de instalação."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as e:
        raise GoogleDriveError(
            f"Dependências do Google Drive não instaladas no servidor ({e}). Veja "
            "backend/requirements.txt (google-api-python-client, google-auth)."
        )
    return service_account, build, MediaIoBaseDownload


def _drive_service():
    service_account, build, _ = _google_libs()
    info = _service_account_info()
    try:
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise GoogleDriveError(f"Falha ao autenticar com a conta de serviço do Google: {e}")


def _candidatos_arquivos(service, folder_id_env, contexto, mimes_aceitos):
    """Lista os arquivos "que parecem planilha" (MIME em `mimes_aceitos`) da
    pasta, do mais pro menos recentemente modificado. Retorna a LISTA
    inteira (não só o primeiro) — ver baixar_arquivo_mais_recente, que agora
    pode precisar tentar mais de um candidato (55ª rodada: a pasta
    compartilhada tinha um documento não relacionado, mais recentemente
    modificado que a planilha de verdade, sendo escolhido por engano pela
    regra antiga de "sempre pega só o mais recente")."""
    folder_id = _folder_id(folder_id_env)
    query = f"'{folder_id}' in parents and trashed = false"
    try:
        resp = service.files().list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=20,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    except Exception as e:
        raise GoogleDriveError(
            f"Falha ao listar a pasta do Google Drive de {contexto} — confira se o id da "
            f"pasta em {folder_id_env} é só o id (sem \"folders/\" nem o link inteiro — "
            "o sistema já tenta extrair o id sozinho, mas confira mesmo assim) e se a "
            f"pasta foi compartilhada com o e-mail da conta de serviço. Detalhe: {e}"
        )
    todos = resp.get("files", [])
    candidatos = [f for f in todos if f.get("mimeType") in mimes_aceitos]
    if not candidatos:
        # Diagnóstico direto na mensagem de erro (55ª rodada — visto em produção:
        # a pasta foi encontrada e compartilhada certinho, mas nada bateu com os
        # tipos reconhecidos, e sem ver o que TEM na pasta não dá pra saber se
        # ela está vazia, se o arquivo é de outro formato, ou se foi colocado
        # noutro lugar) — lista os arquivos de verdade encontrados (nome + tipo).
        if not todos:
            raise GoogleDriveError(
                f"A pasta do Drive de {contexto} está vazia (nenhum arquivo visível pra conta "
                "de serviço) — confira se o arquivo foi mesmo colocado nessa pasta (não numa "
                "subpasta) e se a pasta em si foi compartilhada com o e-mail da conta de serviço."
            )
        listagem = "; ".join(f'"{f.get("name")}" ({f.get("mimeType")})' for f in todos[:10])
        raise GoogleDriveError(
            f"Nenhum arquivo reconhecido como planilha ({_descricao_formatos(mimes_aceitos)}) "
            f"foi encontrado na pasta do Drive de {contexto}. Arquivos encontrados na pasta: "
            f"{listagem}{' (e outros)' if len(todos) > 10 else ''} — confira se o arquivo "
            "certo está nessa pasta e nesse formato."
        )
    return candidatos


def _baixar_conteudo(service, arquivo):
    """Baixa o conteúdo bruto (bytes) de UM arquivo já identificado (id +
    mimeType já conhecidos) — separado de baixar_arquivo_mais_recente pra
    poder ser chamado uma vez por candidato, quando há validação (ver
    abaixo)."""
    _, _, MediaIoBaseDownload = _google_libs()
    if arquivo["mimeType"] == _MIME_GOOGLE_SHEETS:
        request = service.files().export_media(fileId=arquivo["id"], mimeType=_MIME_XLSX)
    else:
        request = service.files().get_media(fileId=arquivo["id"])
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf.read()


def baixar_arquivo_mais_recente(folder_id_env, contexto, validar=None, max_tentativas=5, mimes_extra=None):
    """Retorna (conteudo_bytes, nome_arquivo, modificado_em_iso) de um
    arquivo da pasta apontada pela variável de ambiente `folder_id_env`.
    `contexto` é só pra mensagem de erro (ex: "Rubricas", "Comparação
    Folha"). Levanta GoogleDriveError, com uma mensagem pronta pra mostrar
    ao usuário, se falhar em qualquer etapa (configuração, autenticação,
    listagem ou download).

    `mimes_extra`: conjunto de MIME types aceitos ALÉM dos de planilha
    "de verdade" (.xlsx/.xlsm/Planilha Google, ver _MIMES_RECONHECIDOS) —
    hoje usado só por Comparação Folha, que pode receber o arquivo como
    .csv exportado direto do sistema legado (ver google_drive.MIMES_CSV e
    drive_comparacao_folha.py).

    Sem `validar`: usa sempre o arquivo mais recentemente modificado da
    pasta, sem checar o conteúdo — comportamento original (54ª/55ª rodada).

    Com `validar` (uma função que recebe conteudo_bytes e LEVANTA UMA
    EXCEÇÃO se o arquivo não parecer ser do tipo esperado — ex:
    rubricas_import.parse_rubricas_document, que levanta RubricasImportError
    se o cabeçalho não bater): tenta, em ordem do mais recente pro mais
    antigo, até `max_tentativas` candidatos da pasta — baixando e validando
    um de cada vez — e usa o primeiro que passar na validação, pulando os
    que não passarem. Existe pra cobrir o caso visto em produção (55ª
    rodada) de uma pasta compartilhada que tem, além da planilha de
    verdade, outros arquivos não relacionados (ex: um outro documento da
    consultoria) que por acaso são reconhecidos como planilha (mesmo MIME)
    e que podem ter sido modificados mais recentemente — sem validação de
    conteúdo, esse outro arquivo seria escolhido por engano, como aconteceu
    com "Especificação da migração de Atributos.xlsx" sendo importado no
    lugar de "Levantamento Rubricas". Se NENHUM candidato passar, levanta
    GoogleDriveError detalhando, arquivo por arquivo, por que cada um foi
    rejeitado."""
    service = _drive_service()
    mimes_aceitos = set(_MIMES_RECONHECIDOS) | set(mimes_extra or ())
    candidatos = _candidatos_arquivos(service, folder_id_env, contexto, mimes_aceitos)

    if validar is None:
        arquivo = candidatos[0]
        try:
            conteudo = _baixar_conteudo(service, arquivo)
        except Exception as e:
            raise GoogleDriveError(f"Falha ao baixar '{arquivo.get('name')}' do Google Drive: {e}")
        return conteudo, arquivo.get("name") or "planilha.xlsx", arquivo.get("modifiedTime")

    tentativas = candidatos[:max_tentativas]
    erros = []
    for arquivo in tentativas:
        nome = arquivo.get("name") or "?"
        try:
            conteudo = _baixar_conteudo(service, arquivo)
        except Exception as e:
            erros.append(f'"{nome}": falha ao baixar ({e})')
            continue
        try:
            validar(conteudo)
        except Exception as e:
            erros.append(f'"{nome}": {e}')
            continue
        return conteudo, nome, arquivo.get("modifiedTime")

    restantes = len(candidatos) - len(tentativas)
    detalhe = " | ".join(erros)
    raise GoogleDriveError(
        f"Nenhum dos {len(tentativas)} arquivo(s) mais recentes da pasta do Drive de "
        f"{contexto} passou na validação de conteúdo esperada"
        f"{f' ({restantes} arquivo(s) mais antigos na pasta nem chegaram a ser tentados)' if restantes > 0 else ''}. "
        f"Confira se o arquivo certo está nessa pasta (e se não há outros arquivos não "
        f"relacionados nela que possam estar confundindo a busca automática). "
        f"Detalhe por arquivo tentado: {detalhe}"
    )
