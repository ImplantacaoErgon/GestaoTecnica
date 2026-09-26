"""
Renumeração automática do Código WBS do cronograma (67ª rodada).

Pedido do usuário verbatim: "A numeração das atividades está bagunçada,
analise e sugira uma numeração mais inteligente por grupos de atividades" —
e, na sequência, que isso virasse uma função reutilizável do sistema (não
só uma proposta escrita), pra poder ser rodada de novo sempre que a
numeração voltar a desandar.

## Diagnóstico (planilha real exportada do sistema em produção, 584
atividades, projeto PCR)

O Código WBS gravado hoje em `atividades.codigo_wbs` vem, na maioria dos
casos, direto do EDT que o ProjectLibre gerava na importação original
(`cronograma_import.py`: `codigo_wbs = n.edt`) — um número hierárquico que
era coerente DENTRO da árvore do arquivo de origem, mas que não tem
nenhuma relação com a Etapa/Frente de trabalho atual da atividade: o
levantamento mostrou, por exemplo, o primeiro segmento "3" aparecendo em
SETE frentes diferentes (Levantamento de Processos, Folha de Pagamento,
Comparação de Folha, Rotinas Pré/Pós-Folha, Portal do Gestor, eSocial,
Operacionalização do Sistema Ergon) — cada uma com atividades espalhadas
por dezenas de códigos "3.x.y" sem nenhuma ordem visível entre si. Isso
acontece porque Etapa/Frente são campos da APLICAÇÃO (editáveis, inclusive
em massa, na tela "Editar em massa") e evoluem independentes do texto do
Código WBS, que ninguém recalcula depois de uma reclassificação.

Não existe, hoje, nenhuma tela que exponha `atividade_pai_id` (hierarquia
de subatividades) para edição em massa, e a planilha exportada da produção
não tem nenhuma atividade "contêiner"/resumo — só folhas de trabalho reais,
uma pra cada linha, já a exemplo do agrupamento "por Etapa/Frente" que o
próprio Gantt usa desde a 65ª rodada (barras macro pretas por Etapa/Frente).

## Esquema de renumeração escolhido

`codigo_wbs = "{Etapa.numero}.{índice da Frente}.{sequencial}"` para
atividades de primeiro nível (sem `atividade_pai_id`), e
`"{código do pai}.{sequencial do filho}"` para subatividades reais — a
árvore de `atividade_pai_id`, quando existir, é respeitada (o filho fica
aninhado sob o código do pai, mesmo que sua própria Etapa/Frente seja
diferente da do pai — o pai é quem manda na posição, Etapa/Frente continuam
servindo só pra filtro/classificação).

- **Segmento 1 = `etapas.numero`** — já é a numeração natural que o gestor
  atribui à Etapa (Etapa 1, Etapa 2...), usado direto.
- **Segmento 2 = posição da Frente** dentro do catálogo do projeto
  (`frentes_trabalho`, ordenado por `ordem`, depois `nome` como
  desempate) — estável mesmo que a Frente não tenha nenhuma atividade
  ainda.
- **Sequencial** — ordem relativa preservada o quanto der: as atividades
  de um mesmo grupo (mesma Etapa+Frente, ou mesmo pai) são ordenadas pelo
  Código WBS atual (comparação numérica segmento a segmento, não
  alfabética — "10" vem depois de "2"), com `dtini_prev` e nome como
  desempate — a única coisa que muda é o PREFIXO do código, a ordem
  interna de cada grupo normalmente já fazia sentido e é preservada.
- **Atividades sem Etapa e/ou sem Frente definidas** (campo nulo — a
  importação deixa em branco quando não tem certeza, ver migração 026)
  ficam de fora da renumeração e aparecem à parte no preview, como aviso —
  a função não inventa um grupo "sem classificação": classificar vem
  primeiro (dá pra fazer isso na tela "Editar em massa"), renumerar depois.

O Código WBS é só um campo de exibição/organização, mantido pela aplicação
(`schema.sql`: "numeração hierárquica, mantida pela aplicação") — não é
chave de nada: dependências entre atividades são por `atividade_id`
(`atividade_dependencia`), e a coluna "Depende de" da exportação só
formata o código do predecessor no momento da exportação
(`cronograma_export.py`). Renumerar não quebra nenhuma referência.

Mesmo padrão preview/confirmar de `cronograma_replanejamento.py` e
`cronograma_edicao_lote.py`: `calcular()` simula sem gravar nada,
`aplicar()` roda `calcular()` de novo e grava tudo numa transação só.
"""
import re

from . import db


def _chave_natural(codigo):
    """Transforma um Código WBS ("3.10.2") numa chave de ordenação que
    compara cada segmento como NÚMERO quando possível (10 > 2), não como
    texto ("10" < "2" alfabeticamente) — mesma armadilha de ordenação já
    resolvida noutras telas do sistema. Códigos vazios/None ordenam por
    último."""
    if not codigo:
        return (1, [])
    partes = re.split(r"[.\-]", str(codigo).strip())
    chave = [(0, int(p)) if p.isdigit() else (1, p) for p in partes if p != ""]
    return (0, chave)


def _chave_ordenacao(atividade):
    return (
        _chave_natural(atividade.get("codigo_wbs")),
        atividade.get("dtini_prev") or "9999-99-99",
        (atividade.get("nome") or "").upper(),
        atividade["id"],
    )


def calcular(projeto_id):
    """Simula a renumeração completa do cronograma do projeto, sem gravar
    nada. Retorna:
    - `itens`: uma linha por atividade RENUMERADA (tem Etapa e Frente),
      com `codigo_atual`/`codigo_novo`/`mudou`, já ordenada pelo código
      novo.
    - `ignoradas`: atividades sem Etapa e/ou sem Frente — não entram na
      renumeração.
    """
    projeto = db.fetch_one(f"SELECT nome FROM projetos WHERE id = {db.q(projeto_id)}")
    if not projeto:
        raise ValueError("Projeto não encontrado")

    etapas = db.fetch_all(
        f"SELECT id, numero, nome FROM etapas WHERE projeto_id = {db.q(projeto_id)} ORDER BY numero"
    )
    if not etapas:
        raise ValueError("Este projeto ainda não tem nenhuma Etapa cadastrada — cadastre as Etapas antes de renumerar.")
    numero_por_etapa = {e["id"]: e["numero"] for e in etapas}

    frentes = db.fetch_all(
        f"SELECT id, nome, ordem FROM frentes_trabalho WHERE projeto_id = {db.q(projeto_id)} "
        "ORDER BY ordem, nome"
    )
    if not frentes:
        raise ValueError("Este projeto ainda não tem nenhuma Frente de trabalho cadastrada — cadastre as Frentes antes de renumerar.")
    indice_por_frente = {f["id"]: i + 1 for i, f in enumerate(frentes)}

    atividades = db.fetch_all(
        "SELECT id, codigo_wbs, nome, etapa_id, frente_trabalho_id, atividade_pai_id, dtini_prev "
        f"FROM atividades WHERE projeto_id = {db.q(projeto_id)}"
    )
    by_id = {a["id"]: a for a in atividades}

    ignoradas = [
        a for a in atividades
        if not a.get("etapa_id") or not a.get("frente_trabalho_id")
    ]
    ignoradas_ids = {a["id"] for a in ignoradas}

    filhos_de = {}
    for a in atividades:
        pai = a.get("atividade_pai_id")
        if pai and pai in by_id and a["id"] not in ignoradas_ids:
            filhos_de.setdefault(pai, []).append(a)

    raizes_por_grupo = {}
    for a in atividades:
        if a["id"] in ignoradas_ids:
            continue
        pai = a.get("atividade_pai_id")
        if pai and pai in by_id and pai not in ignoradas_ids:
            continue  # não é raiz — é filha de outra atividade, tratada via filhos_de
        chave_grupo = (a["etapa_id"], a["frente_trabalho_id"])
        raizes_por_grupo.setdefault(chave_grupo, []).append(a)

    novo_codigo = {}

    def numerar_filhos(pai_id, prefixo):
        filhos = sorted(filhos_de.get(pai_id, []), key=_chave_ordenacao)
        for i, filho in enumerate(filhos, start=1):
            codigo = f"{prefixo}.{i}"
            novo_codigo[filho["id"]] = codigo
            numerar_filhos(filho["id"], codigo)

    for etapa in etapas:
        for frente in frentes:
            grupo = raizes_por_grupo.get((etapa["id"], frente["id"])) or []
            grupo_ordenado = sorted(grupo, key=_chave_ordenacao)
            for i, raiz in enumerate(grupo_ordenado, start=1):
                codigo = f"{numero_por_etapa[etapa['id']]}.{indice_por_frente[frente['id']]}.{i}"
                novo_codigo[raiz["id"]] = codigo
                numerar_filhos(raiz["id"], codigo)

    etapa_nome = {e["id"]: e["nome"] for e in etapas}
    frente_nome = {f["id"]: f["nome"] for f in frentes}

    itens = []
    for a in atividades:
        if a["id"] not in novo_codigo:
            continue
        codigo_novo = novo_codigo[a["id"]]
        itens.append({
            "id": a["id"],
            "nome": a["nome"],
            "codigo_atual": a.get("codigo_wbs"),
            "codigo_novo": codigo_novo,
            "mudou": (a.get("codigo_wbs") or "") != codigo_novo,
            "etapa_nome": etapa_nome.get(a["etapa_id"]),
            "frente_nome": frente_nome.get(a["frente_trabalho_id"]),
            "eh_subatividade": bool(a.get("atividade_pai_id") and a["atividade_pai_id"] not in ignoradas_ids
                                     and a["atividade_pai_id"] in by_id),
        })
    itens.sort(key=lambda i: _chave_natural(i["codigo_novo"]))

    ignoradas_out = sorted((
        {
            "id": a["id"],
            "nome": a["nome"],
            "codigo_atual": a.get("codigo_wbs"),
            "sem_etapa": not a.get("etapa_id"),
            "sem_frente": not a.get("frente_trabalho_id"),
        }
        for a in ignoradas
    ), key=lambda i: (i["nome"] or "").upper())

    return {"itens": itens, "ignoradas": ignoradas_out}


def aplicar(projeto_id):
    """Roda `calcular()` de novo e grava as mudanças de `codigo_wbs` numa
    única transação (mesmo padrão de `cronograma_edicao_lote.aplicar`)."""
    resultado = calcular(projeto_id)
    mudadas = [i for i in resultado["itens"] if i["mudou"]]
    if not mudadas:
        return {"atividades_renumeradas": 0, "ids_alterados": [],
                "total_ignoradas": len(resultado["ignoradas"])}

    stmts = ["BEGIN;"]
    for i in mudadas:
        stmts.append(
            f"UPDATE atividades SET codigo_wbs={db.q(i['codigo_novo'])} WHERE id={db.q(i['id'])};"
        )
    stmts.append("COMMIT;")
    db.execute("\n".join(stmts))

    return {
        "atividades_renumeradas": len(mudadas),
        "ids_alterados": [i["id"] for i in mudadas],
        "total_ignoradas": len(resultado["ignoradas"]),
    }
