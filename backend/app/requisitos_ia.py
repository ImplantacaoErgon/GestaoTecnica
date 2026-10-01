"""
Pergunta livre (IA) sobre requisitos do TR — tela Analisar Requisitos (99ª rodada).

Pedido do usuário (verbatim): "pensei em ter um campo para prompt. Exemplo,
quero uma análise sobre determinados requisitos ou quero uma análise sobre
determinada semântica do TR para determinar por exemplo se um assunto
específico faz parte do escopo". Discutido com o usuário antes de implementar
(ver documentação de gestão técnica): a ideia foi dividida em duas etapas —
esta é a PRIMEIRA ("pergunta mais objetiva, filtra itens"): o consultor
filtra/seleciona um conjunto de requisitos na tela (mesmos filtros/seleção já
usados por "Analisar selecionados"/"Analisar todos os filtrados") e faz uma
pergunta em português livre só sobre ESSE conjunto. A segunda etapa ("análise
sobre determinada semântica do TR", isto é, perguntar sobre o TR INTEIRO sem
pré-filtrar manualmente) fica para uma evolução futura, que vai precisar de
um passo de pré-seleção automática (reaproveitando a busca por palavra-chave
de app/manuais.py, ou evoluindo para busca semântica) antes de chamar a IA —
mandar os ~1000 requisitos de um projeto real em toda pergunta não escala em
custo/tempo.

Diferente da busca por palavra-chave já existente (app/manuais.py) — que só
casa texto contra as páginas dos manuais, sem entender a pergunta — aqui o
texto dos requisitos SELECIONADOS + a pergunta em linguagem natural vão para
a API da Anthropic, que responde com base neles. Mesmo espírito da busca por
palavra-chave e do Relatório Executivo (app/relatorio_executivo.py): a
resposta é sempre um CANDIDATO para revisão do consultor, nunca uma decisão
automática de escopo — TR é documento contratual, uma resposta errada tratada
como definitiva pode virar discussão contratual de verdade com o cliente. Por
isso o prompt abaixo pede explicitamente à IA para citar o código de cada
requisito em que baseou a resposta, e para deixar claro que a palavra final é
do consultor quando a pergunta envolver escopo.

Reaproveita a mesma configuração do Relatório Executivo
(ANTHROPIC_API_KEY/ANTHROPIC_MODEL, ver .env.example) — nenhuma variável de
ambiente nova pra Anthropic. Uma única chave/conta da Anthropic para o
backend inteiro (não por projeto/cliente) — mesmo modelo já usado em
relatorio_executivo.py.

105ª/106ª rodada — pedido do usuário (depois de ver o recurso ficar
indisponível por falta de crédito na Anthropic, 99ª/100ª rodada): "tem como
usar a Anthropic e/ou OpenAI se uma não puder? Caso não tenha créditos a
Anthropic usar a OpenAI?" A chamada de IA em si (com o fallback automático
pra OpenAI quando configurado) saiu deste arquivo e virou
app/ia_provider.py, compartilhado com relatorio_executivo.py — ver aquele
módulo para os detalhes de como o fallback funciona e quais variáveis de
ambiente ele usa (ANTHROPIC_API_KEY/ANTHROPIC_MODEL, OPENAI_API_KEY/
OPENAI_MODEL).

Cada pergunta é gravada em requisitos_ia_perguntas (requisitos considerados,
pergunta, resposta, tokens de entrada/saída e custo estimado, quem
perguntou) — dá histórico/auditoria na própria tela e evita pagar de novo por
uma pergunta já respondida antes (ver listar_historico()). O campo
`modelo_ia` grava o modelo que respondeu DE FATO (Anthropic ou, se caiu no
fallback, OpenAI) — é esse campo que, na tela, mostra pro consultor que a
resposta veio do fallback, sem precisar de coluna nova.
"""
import json

from . import db, ia_provider

# Teto de requisitos por pergunta — proteção de custo/contexto (não é sobre o
# limite técnico do modelo, que aguenta muito mais texto que isso; é para
# evitar que "todos os filtrados" sem nenhum filtro aplicado, num projeto com
# ~1000 requisitos, vire sem querer uma pergunta cara e lenta). O front-end
# mostra esse número e pede pra refinar o filtro quando ultrapassa.
LIMITE_REQUISITOS_POR_PERGUNTA = 200


class RequisitosIAError(Exception):
    pass


def _montar_prompt(requisitos, pergunta):
    blocos = []
    for r in requisitos:
        partes = [f"[{r['codigo']}] {r['titulo']}"]
        if r.get("descricao"):
            partes.append(r["descricao"])
        partes.append(
            f"(Frente: {r.get('frente_nome') or '—'} · Classificação: {r.get('classificacao') or '—'} · "
            f"Atendimento: {r.get('atendimento') or '—'} · Status: {r.get('status') or '—'})"
        )
        blocos.append("\n".join(partes))
    lista_requisitos = "\n\n".join(blocos)

    return f"""Você é um consultor sênior de implantação de sistemas, analisando requisitos de um Termo de Referência (TR) de licitação pública para o sistema Ergon (Gestão de Pessoas e Folha de Pagamento de um órgão público brasileiro).

REGRA MAIS IMPORTANTE: responda usando SOMENTE o texto dos requisitos listados abaixo. Não invente requisito, cláusula, prazo ou informação que não esteja aqui. Se a pergunta não puder ser respondida com o que está listado (por exemplo, se o assunto perguntado não aparecer em nenhum dos requisitos abaixo), diga isso explicitamente em vez de adivinhar — isso também é uma resposta útil. Sempre que possível, cite o CÓDIGO de cada requisito (ex: RF-1.1) em que você baseou cada afirmação, para o consultor conseguir conferir rapidamente.

REQUISITOS CONSIDERADOS NESTA PERGUNTA ({len(requisitos)} ao todo — só estes, não o TR inteiro):
{lista_requisitos}

PERGUNTA DO CONSULTOR:
{pergunta}

Responda em português do Brasil, de forma objetiva e direta. Esta resposta é um candidato para revisão do consultor responsável, nunca uma decisão automática — se a pergunta envolver determinar se algo está dentro do escopo contratual do TR, deixe isso explícito na resposta e recomende confirmação humana antes de qualquer posicionamento formal com o cliente."""


def perguntar(projeto_id, requisito_ids, pergunta, perguntado_por_id=None, perguntado_por_nome=None):
    """Roda uma pergunta livre em linguagem natural sobre um conjunto de
    requisitos do TR (já filtrado/selecionado no front-end), grava a
    pergunta + resposta + custo estimado em requisitos_ia_perguntas e
    retorna a linha criada (histórico)."""
    pergunta = (pergunta or "").strip()
    if not pergunta:
        raise RequisitosIAError("Digite uma pergunta.")
    if len(pergunta) > 2000:
        raise RequisitosIAError("Pergunta muito longa (máximo 2000 caracteres).")

    ids = [i for i in (requisito_ids or []) if i]
    if not ids:
        raise RequisitosIAError("Selecione ao menos um requisito (ou ajuste os filtros) antes de perguntar.")
    if len(ids) > LIMITE_REQUISITOS_POR_PERGUNTA:
        raise RequisitosIAError(
            f"{len(ids)} requisitos selecionados — o limite por pergunta é "
            f"{LIMITE_REQUISITOS_POR_PERGUNTA} (proteção de custo/contexto). Refine os filtros e tente de novo."
        )

    placeholders = ", ".join(db.q(i) for i in ids)
    requisitos = db.fetch_all(f"""
        SELECT r.codigo, r.titulo, r.descricao, r.classificacao, r.atendimento, r.status,
               f.nome AS frente_nome
        FROM requisitos_tr r LEFT JOIN frentes_trabalho f ON f.id = r.frente_trabalho_id
        WHERE r.projeto_id = {db.q(projeto_id)} AND r.id IN ({placeholders})
        ORDER BY r.codigo
    """)
    if not requisitos:
        raise RequisitosIAError("Nenhum dos requisitos informados pertence a este projeto.")

    prompt = _montar_prompt(requisitos, pergunta)
    try:
        resposta_texto, tokens_entrada, tokens_saida, modelo_usado, _truncado = ia_provider.chamar_ia(prompt)
    except ia_provider.IAProviderError as e:
        raise RequisitosIAError(str(e))
    custo_usd = ia_provider.estimar_custo_usd(modelo_usado, tokens_entrada, tokens_saida)

    row = db.execute_returning_one(
        "INSERT INTO requisitos_ia_perguntas "
        "(projeto_id, requisito_ids, qtd_requisitos, pergunta, resposta, modelo_ia, "
        "tokens_entrada, tokens_saida, custo_usd_estimado, perguntado_por, perguntado_por_nome) "
        f"VALUES ({db.q(projeto_id)}, {db.q(json.dumps(ids))}::jsonb, {db.q(len(requisitos))}, "
        f"{db.q(pergunta)}, {db.q(resposta_texto)}, {db.q(modelo_usado)}, "
        f"{db.q(tokens_entrada)}, {db.q(tokens_saida)}, {db.q(custo_usd)}, "
        f"{db.q(perguntado_por_id)}, {db.q(perguntado_por_nome)}) "
        "RETURNING *"
    )
    if isinstance(row.get("requisito_ids"), str):
        row["requisito_ids"] = json.loads(row["requisito_ids"])
    return row


def listar_historico(projeto_id, limite=30):
    """Histórico de perguntas já feitas neste projeto, mais recentes primeiro
    — reaproveitável sem pagar de novo (o consultor pode reabrir uma
    pergunta antiga em vez de refazer a mesma)."""
    rows = db.fetch_all(f"""
        SELECT id, requisito_ids, qtd_requisitos, pergunta, resposta, modelo_ia,
               tokens_entrada, tokens_saida, custo_usd_estimado, perguntado_por_nome, criado_em
        FROM requisitos_ia_perguntas
        WHERE projeto_id = {db.q(projeto_id)}
        ORDER BY criado_em DESC
        LIMIT {int(limite)}
    """)
    for r in rows:
        if isinstance(r.get("requisito_ids"), str):
            r["requisito_ids"] = json.loads(r["requisito_ids"])
    return rows
