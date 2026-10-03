"""
Leitura e classificação das planilhas de REJEITADOS da migração (116ª
rodada, pedido verbatim do usuário) — duas fases por ciclo de migração:

  Fase 2 (De-Para)      -- arquivo "LISTA_P_REJ_DP_*"  -- rejeições do
                            de-para (cargo/função/referência/jornada)
                            antes da carga final.
  Fase 3 (Carga final)  -- arquivo "LISTA_P_REJ_ERG_*" -- rejeições de
                            verdade na carga no Ergon, com código de erro
                            ERG-NNNNN.

Primeiro recorte: Eventos de Cargos -- 36 colunas na Fase 3, 29 na Fase 2
(nomes exatos vistos nos dois arquivos reais anexados nesta rodada:
LISTA_P_REJ_ERG_EV_7670.csv e LISTA_P_REJ_DP_EV_7667.csv). O parser
reaproveita a detecção de formato/encoding de comparacao_folha_import.py
(mesmo tipo de export do sistema legado: .csv ou .xlsx, latin-1/utf-8,
";" como separador) em vez de duplicar essa lógica aqui -- ver
_abrir_primeira_aba_com_dados / _parece_xlsx nesse módulo.

Classificação de MSG_ERRO (tipo_erro_chave):
  - Fase 3: toda mensagem real analisada (2053 linhas) trazia um código
    "ERG-NNNNN" -- extraído por regex, 12 códigos distintos encontrados,
    nenhuma linha sem código.
  - Fase 2: as mensagens reais (6354 linhas) não têm código numérico, mas
    bateram 100% em um de 7 templates fixos (prefixo da mensagem) -- ver
    PREFIXOS_FASE2.
  - Qualquer mensagem que não bata com nenhum padrão conhecido (mensagem
    nova, ainda não vista) cai em "_OUTRO" -- não trava a importação, só
    fica sem ação sugerida específica até alguém cadastrar uma (ver seed
    de migracao_rejeicoes_acoes_sugeridas na migração 038).
"""
import re

from .comparacao_folha_import import _abrir_primeira_aba_com_dados


class MigracaoRejeicoesImportError(Exception):
    pass


FASE2_DEPARA = "fase2_depara"
FASE3_CARGA_FINAL = "fase3_carga_final"
FASES_VALIDAS = (FASE2_DEPARA, FASE3_CARGA_FINAL)

# Prefixo do NOME do arquivo (pedido verbatim do usuário: "Dados
# rejeitados no de-para tem o nome começando assim LISTA_P_REJ_DP" /
# "...na carga final tem o nome começando assim LISTA_P_REJ_ERG") --
# usado só pra PRÉ-SELECIONAR a fase na tela a partir do arquivo
# escolhido; quem decide de fato a fase da importação é o campo "fase"
# enviado pelo front-end (nome de arquivo não é garantia suficiente pra
# travar nisso).
PREFIXO_ARQUIVO_FASE2 = "LISTA_P_REJ_DP"
PREFIXO_ARQUIVO_FASE3 = "LISTA_P_REJ_ERG"


def sugerir_fase_pelo_nome(nome_arquivo):
    nome = (nome_arquivo or "").strip().upper()
    if nome.startswith(PREFIXO_ARQUIVO_FASE2):
        return FASE2_DEPARA
    if nome.startswith(PREFIXO_ARQUIVO_FASE3):
        return FASE3_CARGA_FINAL
    return None


TIPO_OUTRO = "_OUTRO"
_PAT_ERG = re.compile(r"ERG-(\d+)")

# Ordem importa (comparado de cima pra baixo, a primeira que bater
# vence) -- prefixos extraídos das 6354 linhas reais da planilha Fase 2
# anexada nesta rodada; cobriram 100% das linhas, sem nenhuma sobrar em
# "_OUTRO".
PREFIXOS_FASE2 = [
    ("DP_VINCULO_NAO_CARREGADO", "Erro: O vínculo dessa matrícula não foi carregado no Ergon."),
    ("DP_CARGO_NAO_LOCALIZADO", "Erro: DE_PARA_CARGO não localizado"),
    ("DP_FUNCAO_NAO_LOCALIZADA", "Erro: FUNCAO não localizada no DE-PARA-FUNCAO"),
    ("DP_REFERENCIA_NAO_LOCALIZADA", "Erro: Não achou a Referencia na Regra de DE-PARA"),
    ("DP_TIPO_EVENTO_NAO_IDENTIFICADO", "Erro: Não foi identificado o tipo de evento"),
    ("DP_TIPO_CARGO_NAO_IDENTIFICADO", "Erro: Tipo de cargo não identificado"),
    ("DP_JORNADA_NAO_LOCALIZADA", "Erro: Não achou a Jornada na Regra de DE-PARA"),
]


def classificar_fase3(msg_erro):
    m = _PAT_ERG.search(msg_erro or "")
    return f"ERG-{m.group(1)}" if m else TIPO_OUTRO


def classificar_fase2(msg_erro):
    msg = (msg_erro or "").strip()
    for chave, prefixo in PREFIXOS_FASE2:
        if msg.startswith(prefixo):
            return chave
    return TIPO_OUTRO


def classificar(fase, msg_erro):
    return classificar_fase3(msg_erro) if fase == FASE3_CARGA_FINAL else classificar_fase2(msg_erro)


# MATRICULA existe nos dois formatos (Fase 2 e Fase 3) -- usada como
# identificador legível de cada linha na tela de detalhe.
COLUNA_IDENTIFICADOR = "MATRICULA"
COLUNA_MSG_ERRO = "MSG_ERRO"


def processar_planilha(conteudo_bytes, fase):
    """Lê o arquivo (csv ou xlsx, formato detectado pelo CONTEÚDO, não
    pela extensão do nome -- ver _abrir_primeira_aba_com_dados) e devolve
    uma lista de dicts, um por linha de dados, prontos pra inserir em
    migracao_rejeicoes: {linha_planilha, tipo_erro_chave, msg_erro,
    identificador, dados}. `dados` guarda a linha ORIGINAL inteira (todas
    as colunas da planilha, nome -> valor), pro detalhe na tela sem
    precisar de uma coluna fixa por campo (ver comentário na migração
    038 sobre JSONB aqui)."""
    if fase not in FASES_VALIDAS:
        raise MigracaoRejeicoesImportError(f"Fase inválida: {fase!r}")

    try:
        ws = _abrir_primeira_aba_com_dados(conteudo_bytes)
    except Exception as e:
        raise MigracaoRejeicoesImportError(str(e))

    linhas_iter = ws.iter_rows(min_row=1, values_only=True)
    cabecalho = next(linhas_iter, None)
    if not cabecalho:
        raise MigracaoRejeicoesImportError("Arquivo sem cabeçalho.")
    colunas = [str(c).strip() if c is not None else "" for c in cabecalho]
    if COLUNA_MSG_ERRO not in colunas:
        raise MigracaoRejeicoesImportError(
            f"Coluna {COLUNA_MSG_ERRO} não encontrada no arquivo -- confirme se é a "
            "planilha de rejeitados correta (LISTA_P_REJ_DP_* ou LISTA_P_REJ_ERG_*)."
        )
    idx_msg = colunas.index(COLUNA_MSG_ERRO)
    idx_ident = colunas.index(COLUNA_IDENTIFICADOR) if COLUNA_IDENTIFICADOR in colunas else None

    resultado = []
    for n, linha in enumerate(linhas_iter, start=2):
        if linha is None or all(v is None for v in linha):
            continue
        valores = list(linha) + [None] * (len(colunas) - len(linha))
        msg_erro = valores[idx_msg]
        dados = {colunas[i]: valores[i] for i in range(len(colunas)) if colunas[i]}
        identificador = valores[idx_ident] if idx_ident is not None else None
        resultado.append({
            "linha_planilha": n,
            "tipo_erro_chave": classificar(fase, msg_erro),
            "msg_erro": (str(msg_erro).strip() if msg_erro is not None else None),
            "identificador": (str(identificador).strip() if identificador is not None else None),
            "dados": dados,
        })

    if not resultado:
        raise MigracaoRejeicoesImportError("Arquivo sem nenhuma linha de dados.")
    return resultado
