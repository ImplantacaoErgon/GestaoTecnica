"""
Importação de ciclos de migração a partir de um CSV gerado pelo script
Oracle local (163ª rodada) -- ver extrair_ciclos_migracao_oracle.py
(entregue ao usuário via SendUserFile, fora deste repositório) e a
investigação registrada no doc do projeto
"oracle-conexao-direta-comparacao-folha.md": o Render (onde este sistema
roda) não alcança a rede do cliente, então o caminho pra "o sistema rodar a
query no Oracle" (pedido original do usuário) é o mesmo já adotado pra
Comparação de Folha -- o usuário roda a query a partir da própria máquina
(onde já tem acesso ao Oracle) e sobe aqui o CSV resultante.

Formato esperado (cabeçalho fixo, mesma convenção de delimitador/encoding
de comparacao_folha_import.py -- ';' como separador, tolera UTF-8 com/sem
BOM e latin-1):

    SIGLA;DATA_EXECUCAO;EXTRAIDOS;CARREGADOS
    MIGRA_PESSOAS;13/10/26;70913;155129
    MIG_FREQ;;0;0            <- sem execução nesse período: ignorada

O casamento SIGLA -> item de migração usa itens_migracao.sigla_oracle
(migração 044) -- cadastrado manualmente uma vez por item, já que o texto
da SIGLA (ex: "MIGRA_PESSOAS") não bate com "Tabela do legado" (ex: "01 -
LEGADO.LEG_PESSOAS") -- confirmado com o usuário via AskUserQuestion nesta
rodada.
"""
import csv
import io
from datetime import datetime

from . import db

COLUNAS_ESPERADAS = ("SIGLA", "DATA_EXECUCAO", "EXTRAIDOS", "CARREGADOS")


class CiclosMigracaoImportError(Exception):
    pass


def _detectar_encoding(conteudo_bytes, tamanho_amostra=65536):
    """Mesma lógica de comparacao_folha_import._detectar_encoding -- exports
    de sistema legado brasileiro às vezes vêm em latin-1, não utf-8."""
    amostra = conteudo_bytes[:tamanho_amostra]
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            amostra.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _parse_data(texto):
    texto = (texto or "").strip()
    if not texto:
        return None
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date().isoformat()
        except ValueError:
            continue
    raise CiclosMigracaoImportError(f'Data "{texto}" em formato não reconhecido (esperado dd/mm/aa).')


def _parse_numero(texto):
    texto = (texto or "").strip()
    if not texto:
        return 0
    # tolera separador de milhar pt-BR (1.234) e decimal (improvável aqui,
    # mas não custa aceitar) -- o export real do cliente manda inteiro puro.
    texto_norm = texto.replace(".", "").replace(",", ".") if "," in texto else texto
    try:
        return int(float(texto_norm))
    except ValueError:
        raise CiclosMigracaoImportError(f'Número "{texto}" inválido.')


def parse_ciclos_csv(conteudo_bytes, nome_arquivo=""):
    """Retorna lista de dicts {sigla, data_execucao, extraidos, carregados,
    linha_csv} -- uma entrada por linha do CSV com DATA_EXECUCAO preenchida.
    Linhas sem execução ainda nesse período (ex: "MIG_EV_HIST_FUNCAO_ACERTO
    ;;0;0") são puladas -- não há ciclo nenhum pra registrar a partir
    delas."""
    if not conteudo_bytes:
        raise CiclosMigracaoImportError("Arquivo vazio.")
    encoding = _detectar_encoding(conteudo_bytes)
    amostra_texto = conteudo_bytes[:65536].decode(encoding, errors="replace")
    primeira_linha = amostra_texto.split("\n", 1)[0] if amostra_texto else ""
    delimitador = ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","
    texto = conteudo_bytes.decode(encoding, errors="replace")
    linhas = list(csv.reader(io.StringIO(texto), delimiter=delimitador))
    if not linhas:
        raise CiclosMigracaoImportError("Arquivo sem linhas.")
    cabecalho = [(c or "").strip().upper() for c in linhas[0]]
    if cabecalho[:4] != list(COLUNAS_ESPERADAS):
        raise CiclosMigracaoImportError(
            f'Cabeçalho não reconhecido (esperado "{";".join(COLUNAS_ESPERADAS)}", '
            f'recebido "{";".join(cabecalho)}") -- "{nome_arquivo}" não parece ser o CSV '
            "gerado pelo script de ciclos de migração via Oracle."
        )
    itens = []
    for n_linha, linha in enumerate(linhas[1:], start=2):
        if not linha or not any((c or "").strip() for c in linha):
            continue  # linha em branco (comum no fim do arquivo)
        linha = (list(linha) + ["", "", "", ""])[:4]
        sigla_txt, data_txt, extraidos_txt, carregados_txt = linha
        sigla = (sigla_txt or "").strip()
        if not sigla:
            continue
        data_execucao = _parse_data(data_txt)
        if not data_execucao:
            continue  # SIGLA sem execução registrada neste período -- nada a importar
        itens.append({
            "sigla": sigla,
            "data_execucao": data_execucao,
            "extraidos": _parse_numero(extraidos_txt),
            "carregados": _parse_numero(carregados_txt),
            "linha_csv": n_linha,
        })
    return itens


def montar_preview(projeto_id, itens_csv):
    """Casa cada linha já parseada do CSV com o item de migração (por
    sigla_oracle) e decide a ação (criar/atualizar/sem_mudanca/ignorado)
    SEM gravar nada -- usada tanto pelo endpoint de prévia quanto, com os
    mesmos dados já revisados na tela, reaproveitada na confirmação
    (aplicar_importacao recalcula a ação em cima do estado atual do banco,
    nunca confia cegamente no que veio daqui, pra não arriscar condição de
    corrida entre a prévia e o clique em confirmar)."""
    linhas_item = db.fetch_all(
        "SELECT id, nome_tabela_legado, sigla_oracle FROM itens_migracao "
        f"WHERE projeto_id = {db.q(projeto_id)} AND sigla_oracle IS NOT NULL"
    )
    mapa_itens = {}
    for it in linhas_item:
        sig = (it.get("sigla_oracle") or "").strip()
        if sig:
            mapa_itens[sig] = it

    resultado = []
    nao_mapeadas = []
    for linha in itens_csv:
        item = mapa_itens.get(linha["sigla"])
        if not item:
            if linha["sigla"] not in nao_mapeadas:
                nao_mapeadas.append(linha["sigla"])
            resultado.append({**linha, "item_id": None, "nome_tabela_legado": None,
                               "acao": "nao_mapeado", "numero_ciclo": None})
            continue
        ciclo_existente = db.fetch_one(
            "SELECT id, numero_ciclo, qtd_registros_extraidos, qtd_registros_carregados, "
            "qtd_rejeicoes_detalhada FROM ciclos_migracao WHERE item_migracao_id = "
            f"{db.q(item['id'])} AND data_execucao = {db.q(linha['data_execucao'])}"
        )
        if ciclo_existente:
            mudou = (
                int(ciclo_existente.get("qtd_registros_extraidos") or 0) != linha["extraidos"]
                or int(ciclo_existente.get("qtd_registros_carregados") or 0) != linha["carregados"]
            )
            if ciclo_existente.get("qtd_rejeicoes_detalhada"):
                acao = "ignorado_rejeicoes_detalhadas"
            elif mudou:
                acao = "atualizar"
            else:
                acao = "sem_mudanca"
            numero_ciclo = ciclo_existente["numero_ciclo"]
        else:
            acao = "criar"
            numero_ciclo = None
        resultado.append({
            **linha,
            "item_id": item["id"],
            "nome_tabela_legado": item["nome_tabela_legado"],
            "acao": acao,
            "numero_ciclo": numero_ciclo,
        })
    return resultado, nao_mapeadas


def aplicar_importacao(projeto_id, itens_preview):
    """Grava de fato -- espera a mesma lista que montar_preview devolveu
    (linhas sem item_id, ex: não mapeadas, são ignoradas aqui). Idempotente:
    rodar duas vezes com os mesmos dados não duplica nem muda nada na
    segunda vez -- a ação é recalculada em cima do estado ATUAL do banco
    (mesma regra de montar_preview), não confia no campo "acao" que veio da
    prévia. Mesma proteção de create_ciclo/update_ciclo (app/main.py) pra
    ciclo com qtd_rejeicoes_detalhada=true (rejeições já vieram de uma carga
    detalhada, ver migracao_rejeicoes.py) -- esses ciclos nunca são
    sobrescritos por esta importação."""
    criados = atualizados = sem_mudanca = ignorados = 0
    for linha in itens_preview:
        item_id = linha.get("item_id")
        if not item_id or not linha.get("data_execucao"):
            continue
        extraidos = int(linha.get("extraidos") or 0)
        carregados = int(linha.get("carregados") or 0)
        rejeicoes = max(0, extraidos - carregados)
        ciclo_existente = db.fetch_one(
            "SELECT id, qtd_registros_extraidos, qtd_registros_carregados, qtd_rejeicoes_detalhada "
            f"FROM ciclos_migracao WHERE item_migracao_id = {db.q(item_id)} "
            f"AND data_execucao = {db.q(linha['data_execucao'])}"
        )
        if ciclo_existente:
            if ciclo_existente.get("qtd_rejeicoes_detalhada"):
                ignorados += 1
                continue
            mudou = (
                int(ciclo_existente.get("qtd_registros_extraidos") or 0) != extraidos
                or int(ciclo_existente.get("qtd_registros_carregados") or 0) != carregados
            )
            if not mudou:
                sem_mudanca += 1
                continue
            db.execute(
                "UPDATE ciclos_migracao SET qtd_registros_extraidos = "
                f"{db.q(extraidos)}, qtd_registros_carregados = {db.q(carregados)}, "
                f"qtd_rejeicoes = {db.q(rejeicoes)} WHERE id = {db.q(ciclo_existente['id'])}"
            )
            atualizados += 1
        else:
            proximo = db.fetch_one(
                "SELECT COALESCE(MAX(numero_ciclo), 0) + 1 AS n FROM ciclos_migracao "
                f"WHERE item_migracao_id = {db.q(item_id)}"
            )
            numero_ciclo = proximo["n"] if proximo else 1
            db.execute(
                "INSERT INTO ciclos_migracao (item_migracao_id, numero_ciclo, data_execucao, "
                "qtd_registros_extraidos, qtd_registros_carregados, qtd_rejeicoes) VALUES ("
                f"{db.q(item_id)}, {db.q(numero_ciclo)}, {db.q(linha['data_execucao'])}, "
                f"{db.q(extraidos)}, {db.q(carregados)}, {db.q(rejeicoes)})"
            )
            criados += 1
    return {
        "criados": criados, "atualizados": atualizados,
        "sem_mudanca": sem_mudanca, "ignorados_rejeicoes_detalhadas": ignorados,
    }
