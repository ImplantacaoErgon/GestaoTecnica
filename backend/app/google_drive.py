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

Sempre lê o arquivo MAIS RECENTEMENTE MODIFICADO dentro da pasta configurada
— não um nome fixo — porque o cliente sobe revisões da planilha com sufixos
diferentes a cada vez (ex: "..._revisadas_5.xlsx", depois "_6", "_7"...).
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
    baixar_arquivo_mais_recente, que também precisa de MediaIoBaseDownload)
    passa por aqui, então o ModuleNotFoundError nunca escapa cru — sempre
    vira GoogleDriveError com a mensagem de instalação."""
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


def _arquivo_mais_recente(service, folder_id_env, contexto):
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
    candidatos = [f for f in todos if f.get("mimeType") in _MIMES_RECONHECIDOS]
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
            f"Nenhum arquivo reconhecido como planilha (.xlsx, .xlsm ou Planilha Google) foi "
            f"encontrado na pasta do Drive de {contexto}. Arquivos encontrados na pasta: "
            f"{listagem}{' (e outros)' if len(todos) > 10 else ''} — confira se o arquivo "
            "certo está nessa pasta e nesse formato."
        )
    return candidatos[0]


def baixar_arquivo_mais_recente(folder_id_env, contexto):
    """Retorna (conteudo_bytes, nome_arquivo, modificado_em_iso) do arquivo
    mais recentemente modificado na pasta apontada pela variável de ambiente
    `folder_id_env`. `contexto` é só pra mensagem de erro (ex: "Rubricas",
    "Comparação Folha"). Levanta GoogleDriveError, com uma mensagem pronta
    pra mostrar ao usuário, se falhar em qualquer etapa (configuração,
    autenticação, listagem ou download)."""
    _, _, MediaIoBaseDownload = _google_libs()
    service = _drive_service()
    arquivo = _arquivo_mais_recente(service, folder_id_env, contexto)
    try:
        if arquivo["mimeType"] == _MIME_GOOGLE_SHEETS:
            request = service.files().export_media(fileId=arquivo["id"], mimeType=_MIME_XLSX)
        else:
            request = service.files().get_media(fileId=arquivo["id"])
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except Exception as e:
        raise GoogleDriveError(
            f"Falha ao baixar '{arquivo.get('name')}' do Google Drive: {e}"
        )
    buf.seek(0)
    return buf.read(), arquivo.get("name") or "planilha.xlsx", arquivo.get("modifiedTime")
