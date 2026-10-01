"""
Camada única de chamada à IA (texto livre) — compartilhada por
app/relatorio_executivo.py e app/requisitos_ia.py (105ª/106ª rodada).

Pedido do usuário (verbatim, depois de ver o recurso ficar indisponível
duas vezes por falta de crédito na conta da Anthropic — 99ª/100ª rodada):
"para essa análise de IA tem como usar a Anthropic e/ou OpenAI se uma não
puder? Caso não tenha créditos a Anthropic usar a OpenAI?"

Como funciona:
  - Se só ANTHROPIC_API_KEY estiver configurada: usa só a Anthropic (igual
    era antes) — se faltar crédito/der erro, falha com a mensagem de
    sempre, sem fallback (não tem pra onde cair).
  - Se só OPENAI_API_KEY estiver configurada: usa só a OpenAI, direto.
  - Se as DUAS estiverem configuradas: tenta a Anthropic primeiro (é a
    conta/modelo já em uso neste projeto); se a chamada falhar por
    QUALQUER motivo (créditos, chave inválida, limite de uso, erro de API,
    etc — não só o caso de créditos citado pelo usuário, mas qualquer
    falha, pra não deixar o recurso fora do ar por um motivo diferente de
    créditos que também tenha fallback disponível), cai automaticamente
    para a OpenAI, registra um aviso no log (pra dar visibilidade de que
    o fallback foi usado) e segue normalmente. Se a OpenAI TAMBÉM falhar,
    o erro final menciona as duas tentativas.

Qual modelo respondeu de fato fica registrado no mesmo campo que já
existia antes (`modelo_ia`, em requisitos_ia_perguntas e
relatorios_executivos) — um nome de modelo da OpenAI nesse campo já deixa
claro pro consultor, na própria tela, que a resposta veio do fallback,
sem precisar de coluna nova nem mudança de schema.

Nenhuma automatização "esperta" pra decidir qual IA é melhor para qual
pergunta — é estritamente "Anthropic primeiro, OpenAI só se a Anthropic
não puder responder", porque é isso que o usuário pediu e o que já está
calibrado (prompt, preço, histórico) é para o par atual.
"""
import os


class IAProviderError(Exception):
    pass


ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

# Sem valor padrão de propósito: ao contrário do modelo da Anthropic (que já
# era usado neste projeto antes desta rodada, com um default conhecido), não
# faz sentido chutar qual modelo da OpenAI o usuário quer/tem acesso — se
# OPENAI_API_KEY estiver configurada, OPENAI_MODEL também precisa estar (ver
# _chamar_openai abaixo, que falha com uma mensagem clara se faltar).
OPENAI_MODEL = os.environ.get("OPENAI_MODEL")

# Preços por milhão de tokens (USD) — só para ESTIMATIVA de custo na tela
# "Perguntar à IA" (relatorio_executivo.py não mostra custo). Tabela da
# Anthropic consultada em platform.claude.com/docs/en/about-claude/pricing
# em 30/09/2026. A tabela da OpenAI foi deixada vazia de propósito (ver
# _PRECO_PADRAO abaixo) — o preço por modelo da OpenAI muda com frequência e
# não quero gravar aqui um valor específico sem confirmar na página oficial
# (platform.openai.com/docs/pricing) na data em que isso for configurado; se
# quiser a estimativa exata para o modelo da OpenAI escolhido, adicione uma
# entrada em PRECOS_USD_POR_MILHAO_TOKENS com o nome exato de OPENAI_MODEL.
PRECOS_USD_POR_MILHAO_TOKENS = {
    "claude-sonnet-4-5-20250929": {"entrada": 3.00, "saida": 15.00},
    "claude-haiku-4-5": {"entrada": 1.00, "saida": 5.00},
}
# Fallback se o modelo usado (Anthropic fora da tabela acima, ou qualquer
# modelo da OpenAI) não tiver preço cadastrado — mostra uma estimativa
# conservadora (nível Sonnet) em vez de quebrar a tela ou mentir um valor.
_PRECO_PADRAO = {"entrada": 3.00, "saida": 15.00}


def estimar_custo_usd(modelo, tokens_entrada, tokens_saida):
    preco = PRECOS_USD_POR_MILHAO_TOKENS.get(modelo, _PRECO_PADRAO)
    custo = tokens_entrada * preco["entrada"] / 1_000_000 + tokens_saida * preco["saida"] / 1_000_000
    return round(custo, 4)


def _chamar_anthropic(prompt, max_tokens):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        import anthropic
    except ImportError:
        raise IAProviderError(
            "Pacote 'anthropic' não instalado na imagem do backend — rode "
            "'docker compose up -d --build' após atualizar requirements.txt."
        )
    client = anthropic.Anthropic(api_key=api_key)
    try:
        resposta = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise IAProviderError("Chave da API da Anthropic inválida ou expirada (ANTHROPIC_API_KEY).")
    except anthropic.RateLimitError:
        raise IAProviderError("Limite de uso da API da Anthropic atingido.")
    except anthropic.APIError as e:
        # Caso visto em produção (99ª/100ª rodada): a API devolve um 400 cru
        # tipo {'type':'error','error':{'type':'invalid_request_error',
        # 'message':'Your credit balance is too low...'}} — sem tratar,
        # isso aparecia na tela pro consultor como um dump de JSON em
        # inglês. Detectado pela mensagem (não tem um tipo de exceção
        # próprio no SDK para este caso).
        if "credit balance is too low" in str(e):
            raise IAProviderError("Os créditos da conta da Anthropic acabaram (console.anthropic.com → Plans & Billing).")
        raise IAProviderError(f"Erro ao chamar a API da Anthropic: {e}")

    texto = "".join(bloco.text for bloco in resposta.content if getattr(bloco, "type", None) == "text")
    if not texto.strip():
        raise IAProviderError("A IA (Anthropic) retornou uma resposta vazia.")
    usage = getattr(resposta, "usage", None)
    tokens_entrada = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_saida = int(getattr(usage, "output_tokens", 0) or 0)
    truncado = getattr(resposta, "stop_reason", None) == "max_tokens"
    return texto, tokens_entrada, tokens_saida, ANTHROPIC_MODEL, truncado


def _chamar_openai(prompt, max_tokens):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not OPENAI_MODEL:
        raise IAProviderError(
            "OPENAI_API_KEY está configurada mas OPENAI_MODEL não — defina OPENAI_MODEL "
            "(ex: um modelo da família GPT atual da sua conta) na mesma variável de "
            "ambiente do backend para poder usar a OpenAI como alternativa à Anthropic."
        )
    try:
        import openai
        from openai import OpenAI
    except ImportError:
        raise IAProviderError(
            "Pacote 'openai' não instalado na imagem do backend — rode "
            "'docker compose up -d --build' após atualizar requirements.txt."
        )
    client = OpenAI(api_key=api_key)
    try:
        resposta = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_completion_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    except openai.AuthenticationError:
        raise IAProviderError("Chave da API da OpenAI inválida ou expirada (OPENAI_API_KEY).")
    except openai.RateLimitError as e:
        # Mesmo espírito do tratamento de "credit balance is too low" da
        # Anthropic acima: a OpenAI devolve 429 tanto para limite de uso
        # (tentar de novo depois) quanto para cota/crédito esgotado (código
        # 'insufficient_quota', tentar de novo não resolve nada) — tratados
        # como RateLimitError pelo SDK, sem exceção própria pra cota
        # esgotada, por isso a checagem pelo texto/código do erro.
        corpo = str(e).lower()
        if "insufficient_quota" in corpo or "exceeded your current quota" in corpo:
            raise IAProviderError("Os créditos/cota da conta da OpenAI acabaram (platform.openai.com → Billing).")
        raise IAProviderError("Limite de uso da API da OpenAI atingido.")
    except openai.APIError as e:
        raise IAProviderError(f"Erro ao chamar a API da OpenAI: {e}")

    texto = (resposta.choices[0].message.content or "") if resposta.choices else ""
    if not texto.strip():
        raise IAProviderError("A IA (OpenAI) retornou uma resposta vazia.")
    usage = getattr(resposta, "usage", None)
    tokens_entrada = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_saida = int(getattr(usage, "completion_tokens", 0) or 0)
    truncado = bool(resposta.choices) and getattr(resposta.choices[0], "finish_reason", None) == "length"
    return texto, tokens_entrada, tokens_saida, OPENAI_MODEL, truncado


def chamar_ia(prompt, max_tokens=2000):
    """Retorna (texto, tokens_entrada, tokens_saida, modelo_usado, truncado).
    `truncado` é True se a resposta foi cortada por bater no teto de
    max_tokens (stop_reason/finish_reason de limite) — quem chamar decide
    se quer avisar sobre isso (ver relatorio_executivo.py). Tenta a
    Anthropic primeiro (se configurada); cai para a OpenAI (se configurada)
    em qualquer falha da Anthropic. Levanta IAProviderError com uma
    mensagem explicando o que falhou (e o que fazer) se nenhuma das duas
    conseguir responder."""
    tem_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    tem_openai = bool(os.environ.get("OPENAI_API_KEY"))

    if not tem_anthropic and not tem_openai:
        raise IAProviderError(
            "Nenhuma IA configurada no backend — defina ANTHROPIC_API_KEY (console.anthropic.com) "
            "e/ou OPENAI_API_KEY (platform.openai.com) nas variáveis de ambiente."
        )

    erro_anthropic = None
    if tem_anthropic:
        try:
            return _chamar_anthropic(prompt, max_tokens)
        except IAProviderError as e:
            erro_anthropic = e
            if not tem_openai:
                raise
            print(f"[ia_provider] Anthropic falhou ({e}) — tentando OpenAI como alternativa.", flush=True)

    try:
        return _chamar_openai(prompt, max_tokens)
    except IAProviderError as e_openai:
        if erro_anthropic is not None:
            raise IAProviderError(
                f"Anthropic falhou ({erro_anthropic}) e a alternativa (OpenAI) também falhou ({e_openai})."
            )
        raise
