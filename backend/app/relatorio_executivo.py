"""
Relatório Executivo (IA) — gerado a partir do Dashboard.

Idéia: em vez de pedir para a IA "olhar a planilha toda e adivinhar o que
está acontecendo" (o que a deixaria livre para inventar datas, porcentagens
ou prazos que não existem — péssimo numa entrega para um órgão público),
este módulo faz o trabalho numérico em Python — determinístico, testável,
sempre o mesmo resultado para os mesmos dados — e manda pronto para a IA só
o texto: o diagnóstico, a priorização do que destacar e a redação em
linguagem de gestão. A IA escreve o relatório; ela não calcula nada.

Duas partes:

1. `_projetar_datas()` — reprojeta a data de término de cada atividade a
   partir do que REALMENTE aconteceu até hoje (conclusões, % executado,
   bloqueios), propagando atraso pelas dependências — bem diferente do
   caminho crítico "de plano" da tela Caminho Crítico (app/cpm.py), que é
   calculado só a partir da duração planejada e nunca reage a atrasos já
   ocorridos. É essa reprojeção que sustenta a previsão da(s) atividade(s)
   "master" (ver atividades.eh_atividade_master).

   O método é uma extrapolação linear simples (ritmo observado até agora =
   % concluído dividido pelos dias úteis decorridos desde o início real,
   projetado até 100%) — é uma aproximação de gestão de projetos conhecida
   (na família de "earned schedule"), não uma ciência exata. Está descrita
   em detalhe no README, seção "Relatório Executivo (IA)", porque quem lê
   o relatório precisa saber que é uma estimativa, não um fato.

2. `gerar_relatorio()` — monta o prompt com os números já calculados,
   chama a API da Anthropic e grava o resultado (texto + retrato dos dados
   enviados, para auditoria) em `relatorios_executivos`.

Nenhum dado de folha de pagamento real (dados de servidores do órgão
público) passa por aqui — só metadados do PROJETO DE IMPLANTAÇÃO (nomes de
atividades, prazos, status, nomes de recursos da consultoria/cliente). Ainda
assim, isso sai do ambiente do cliente rumo à API da Anthropic — só chame
isso se o cliente já autorizou (ver README).
"""
import json
import math
from datetime import date, timedelta

import networkx as nx

from . import comparacao_folha, cronograma_anomalias, db, ia_provider
from .cpm import _dur_dias, _business_day_offset, dias_uteis_entre

LIMITE_ATRASADAS_NO_PROMPT = 40  # não manda a lista inteira se o projeto tiver centenas — manda uma amostra + o total

# Mesma família de status "concluído" de backend/app/main.py (duplicada aqui de propósito
# — importar de main.py criaria import circular, já que main.py importa este módulo).
# Mantenha as duas listas em sincronia se algum dia mudar (ver 29ª rodada/migração 020).
STATUS_FAMILIA_CONCLUIDA = {
    "Concluída", "Concluída com atraso", "Concluída com esforço maior", "Concluída com atraso e esforço maior",
}


class RelatorioExecutivoError(Exception):
    pass


def _parse_data(v):
    if not v:
        return None
    return date.fromisoformat(v) if isinstance(v, str) else v


def _projetar_datas(projeto_id, horas_dia_util, non_working):
    """Retorna {atividade_id: {"inicio_proj": date, "fim_proj": date, "confianca": str}}.
    "confianca": "alta" (já aconteceu — concluída), "média" (extrapolação de ritmo ou
    duração planejada restante), "baixa" (atividade bloqueada — data é um piso, não
    uma previsão confiável, porque não sabemos quando o bloqueio será resolvido) ou
    "fixada" (57ª/58ª rodada — atividade com Data de Execução Fixada que ainda não
    começou de verdade: o Fim projetado é o próprio Fim previsto no plano, nunca
    recalculado a partir do que aconteceu com as predecessoras)."""
    atividades = db.fetch_all(f"""
        SELECT id, status, percentual_concluido, prazo_horas, dtini_prev, dtfim_prev, dtini_real, dtfim_real,
               data_execucao_fixada
        FROM atividades WHERE projeto_id = {db.q(projeto_id)}
    """)
    if not atividades:
        return {}
    by_id = {a["id"]: a for a in atividades}

    deps = db.fetch_all(
        "SELECT ad.atividade_id, ad.predecessora_id, ad.tipo, ad.lag_horas "
        "FROM atividade_dependencia ad "
        f"JOIN atividades a ON a.id = ad.atividade_id AND a.projeto_id = {db.q(projeto_id)}"
    )
    g = nx.DiGraph()
    g.add_nodes_from(by_id.keys())
    for d in deps:
        if d["atividade_id"] in by_id and d["predecessora_id"] in by_id:
            lag_dias = _dur_dias(d["lag_horas"], horas_dia_util) if d.get("lag_horas") else 0
            g.add_edge(d["predecessora_id"], d["atividade_id"], tipo=d["tipo"], lag=lag_dias)

    try:
        ordem = list(nx.topological_sort(g))
    except nx.NetworkXUnfeasible:
        # ciclo de dependências — não deveria acontecer (a tela de Caminho Crítico já
        # bloquearia isso ao recalcular), mas o relatório não pode travar por causa disso
        ordem = list(by_id.keys())

    hoje = date.today()
    inicio_proj, fim_proj, confianca = {}, {}, {}

    for n in ordem:
        a = by_id[n]
        status = a["status"]
        dtini_real, dtfim_real = _parse_data(a.get("dtini_real")), _parse_data(a.get("dtfim_real"))
        dtini_prev, dtfim_prev = _parse_data(a.get("dtini_prev")), _parse_data(a.get("dtfim_prev"))
        pct = a.get("percentual_concluido") or 0
        dur_planejada = _dur_dias(a.get("prazo_horas"), horas_dia_util)

        # Trata como concluída tanto quando o Status diz isso (qualquer uma das 4
        # variantes de Concluída — ver STATUS_FAMILIA_CONCLUIDA) quanto quando os
        # dados já dizem isso sozinhos (100% + Fim Real preenchido) — a partir da 28ª
        # rodada o backend passa a impedir gravar essa combinação com um Status fora
        # da família, mas dado já existente (import de cronograma, ou gravado antes
        # dessa validação existir) pode continuar assim, e a projeção não deveria
        # quebrar/mostrar atraso falso por causa disso.
        if status in STATUS_FAMILIA_CONCLUIDA or (pct >= 100 and dtfim_real):
            fim = dtfim_real or dtfim_prev or hoje
            inicio_proj[n] = dtini_real or dtini_prev or fim
            fim_proj[n] = fim
            confianca[n] = "alta"
            continue

        if status == "Em andamento" and pct > 0 and dtini_real:
            dias_decorridos = max(1, dias_uteis_entre(dtini_real, hoje, non_working))
            ritmo = pct / dias_decorridos  # % concluído por dia útil decorrido
            if ritmo > 0:
                dias_totais_estimados = math.ceil(100 / ritmo)
                fim = _business_day_offset(dtini_real, non_working, dias_totais_estimados)
                inicio_proj[n] = dtini_real
                fim_proj[n] = max(fim, hoje)
                confianca[n] = "média"
                continue

        # Atividade com Data de Execução Fixada (57ª/58ª rodada — migração 031) que
        # ainda não começou de verdade (sem dtini_real): a mesma trava que já vale pro
        # Replanejamento e pela cascata da Edição em Lote (não recalcular
        # dtini_prev/dtfim_prev a partir de dependências) também precisa valer aqui —
        # senão a projeção "antecipa" (ou atrasa) uma atividade fixada só porque uma
        # predecessora terminou mais cedo/tarde do que o plano, o que contradiz a
        # trava. Fim projetado = o próprio Fim previsto no plano, sem olhar pro grafo.
        # Uma vez que a atividade realmente começa (dtini_real preenchido, ramo "Em
        # andamento" acima), a extrapolação de ritmo real volta a valer normalmente —
        # a trava é sobre o PLANO, não sobre esconder o andamento real já observado.
        if a.get("data_execucao_fixada") and dtini_prev and dtfim_prev:
            inicio_proj[n] = dtini_real or dtini_prev
            fim_proj[n] = dtfim_prev
            confianca[n] = "fixada"
            continue

        # Fallback: não iniciada, bloqueada, ou em andamento sem % lançado ainda.
        # Início = o mais tarde entre: fim projetado dos predecessores (considerando o
        # tipo de dependência), o início real/previsto já registrado, e hoje (se o
        # início previsto já passou e a atividade nem começou — isso por si só já é
        # um atraso que precisa aparecer na projeção).
        candidatos_inicio = []
        for pred, _, edata in g.in_edges(n, data=True):
            tipo, lag = edata["tipo"], edata["lag"]
            pred_ini, pred_fim = inicio_proj.get(pred), fim_proj.get(pred)
            if pred_ini is None or pred_fim is None:
                continue
            if tipo == "FS":
                candidatos_inicio.append(_business_day_offset(pred_fim, non_working, lag))
            elif tipo == "SS":
                candidatos_inicio.append(_business_day_offset(pred_ini, non_working, lag))
            elif tipo == "FF":
                # aproximação por dias corridos (mesmo critério já adotado para FF/SF no
                # motor de Caminho Crítico — app/cpm.py — que também trata esses dois
                # tipos como aproximação, não como cálculo exato de dias úteis)
                alvo_fim = pred_fim + timedelta(days=lag)
                candidatos_inicio.append(alvo_fim - timedelta(days=dur_planejada))
            elif tipo == "SF":
                alvo_fim = pred_ini + timedelta(days=lag)
                candidatos_inicio.append(alvo_fim - timedelta(days=dur_planejada))

        if not dtini_real and dtini_prev and dtini_prev <= hoje and status in ("Não iniciada", "Bloqueada"):
            candidatos_inicio.append(hoje)

        base = dtini_real or dtini_prev or hoje
        inicio = max(candidatos_inicio) if candidatos_inicio else base
        inicio = _business_day_offset(inicio, non_working, 0)  # rola pro próximo dia útil se cair em fim de semana

        dur_restante = dur_planejada
        if status == "Em andamento" and pct > 0:
            dur_restante = max(1, round(dur_planejada * (100 - pct) / 100))

        inicio_proj[n] = inicio
        fim_proj[n] = _business_day_offset(inicio, non_working, dur_restante)
        confianca[n] = "baixa" if status == "Bloqueada" else "média"

    return {
        n: {"inicio_proj": inicio_proj[n], "fim_proj": fim_proj[n], "confianca": confianca[n]}
        for n in inicio_proj
    }


def _bloqueios_no_caminho(atividade_id, deps_por_sucessora, by_id, visitados=None):
    """Lista (nome, codigo_wbs) de toda atividade BLOQUEADA que é predecessora, direta ou
    indireta, de `atividade_id` — para avisar quando a atividade master depende de algo
    travado, o sinal de risco mais concreto que existe num cronograma."""
    if visitados is None:
        visitados = set()
    achados = []
    for pred_id in deps_por_sucessora.get(atividade_id, []):
        if pred_id in visitados:
            continue
        visitados.add(pred_id)
        pred = by_id.get(pred_id)
        if not pred:
            continue
        if pred["status"] == "Bloqueada":
            achados.append({"codigo_wbs": pred.get("codigo_wbs"), "nome": pred["nome"]})
        achados.extend(_bloqueios_no_caminho(pred_id, deps_por_sucessora, by_id, visitados))
    return achados


def _migracao_de_dados(projeto_id):
    """Quadro comparativo de Migração de Dados: para cada item (tabela do
    legado), compara o ÚLTIMO ciclo de execução registrado com o
    imediatamente anterior, e calcula o % de evolução (positivo) ou
    involução (negativo) — tudo em Python, determinístico, igual ao resto
    deste módulo (a IA só formata a tabela, não recalcula nada — ver
    instrução no PROMPT_TEMPLATE). Um item com um único ciclo (ainda comum
    nesta fase do projeto) aparece com "ciclo_anterior": None e
    "evolucao_percentual": None — não dá pra comparar o que não existe. Cada
    ciclo (em "ultimo_ciclo"/"ciclo_anterior") também carrega "rejeitado" e
    "percentual_rejeicao" (coluna gerada pelo banco), usados na tabela para
    mostrar a taxa de rejeição do último ciclo por item."""
    linhas = db.fetch_all(f"""
        SELECT im.id AS item_id, im.nome_tabela_legado, im.nome_tabela_destino,
               im.qtd_registros_estimada, im.status,
               c.numero_ciclo, c.data_execucao, c.qtd_registros_extraidos,
               c.qtd_registros_carregados, c.qtd_rejeicoes, c.percentual_rejeicao
        FROM itens_migracao im
        JOIN ciclos_migracao c ON c.item_migracao_id = im.id
        WHERE im.projeto_id = {db.q(projeto_id)}
        ORDER BY im.nome_tabela_legado, c.numero_ciclo DESC
    """)

    # 138ª rodada — mantém até 5 ciclos por item (não só os 2 últimos): os 2
    # primeiros continuam alimentando "ultimo_ciclo"/"ciclo_anterior" (tabela
    # comparativa, sem mudança), e a lista completa (até 5) alimenta o gráfico
    # de evolução por item embutido no PDF (ver app/relatorio_graficos.py e
    # app/relatorio_pdf.py) — mesmos "últimos 5 ciclos" que a Visão Executiva
    # já mostra (131ª/136ª rodadas).
    CICLOS_PARA_GRAFICO = 5
    por_item = {}
    ordem_itens = []
    for l in linhas:
        item_id = l["item_id"]
        if item_id not in por_item:
            por_item[item_id] = {
                "tabela_legado": l["nome_tabela_legado"], "tabela_destino": l["nome_tabela_destino"],
                "meta": l.get("qtd_registros_estimada"), "status": l["status"], "ciclos": [],
            }
            ordem_itens.append(item_id)
        # já vem ORDER BY numero_ciclo DESC — os 2 primeiros de cada item são o
        # último ciclo e o imediatamente anterior
        if len(por_item[item_id]["ciclos"]) < CICLOS_PARA_GRAFICO:
            por_item[item_id]["ciclos"].append({
                "numero": l["numero_ciclo"], "data": l["data_execucao"],
                "extraido": l.get("qtd_registros_extraidos") or 0,
                "carregado": l["qtd_registros_carregados"] or 0,
                # 51ª rodada — % de rejeição do ciclo, já calculado pelo banco (coluna
                # gerada ciclos_migracao.percentual_rejeicao) — só passa adiante, não recalcula.
                "rejeitado": l["qtd_rejeicoes"] or 0,
                "percentual_rejeicao": l.get("percentual_rejeicao"),
            })

    itens = []
    total_ultimo_geral = 0
    total_ultimo_comparavel, total_anterior_comparavel, itens_com_2_ciclos = 0, 0, 0
    for item_id in ordem_itens:
        it = por_item[item_id]
        ciclos = it["ciclos"]
        ultimo = ciclos[0] if ciclos else None
        anterior = ciclos[1] if len(ciclos) > 1 else None

        evolucao_percentual = None
        evolucao_observacao = None
        if ultimo:
            total_ultimo_geral += ultimo["carregado"]
        if ultimo and anterior:
            carregado_ultimo, carregado_anterior = ultimo["carregado"], anterior["carregado"]
            if carregado_anterior > 0:
                evolucao_percentual = round(100 * (carregado_ultimo - carregado_anterior) / carregado_anterior, 2)
            elif carregado_ultimo > 0:
                evolucao_observacao = "carga iniciada neste ciclo (0 no ciclo anterior)"
            else:
                evolucao_percentual = 0.0
            total_ultimo_comparavel += carregado_ultimo
            total_anterior_comparavel += carregado_anterior
            itens_com_2_ciclos += 1
        elif ultimo:
            evolucao_observacao = "sem ciclo anterior para comparação (só 1 ciclo registrado)"

        itens.append({
            "tabela_legado": it["tabela_legado"], "tabela_destino": it["tabela_destino"],
            "meta": it["meta"], "status": it["status"],
            "ultimo_ciclo": ultimo, "ciclo_anterior": anterior,
            # 138ª rodada — ordem cronológica (mais antigo -> mais recente), só para o
            # gráfico embutido no PDF; não vai para o prompt da IA (ela só usa
            # ultimo_ciclo/ciclo_anterior, como já era).
            "ciclos_recentes": list(reversed(ciclos)),
            "evolucao_percentual": evolucao_percentual, "evolucao_observacao": evolucao_observacao,
        })

    evolucao_percentual_global = None
    if itens_com_2_ciclos and total_anterior_comparavel > 0:
        evolucao_percentual_global = round(
            100 * (total_ultimo_comparavel - total_anterior_comparavel) / total_anterior_comparavel, 2
        )

    return {
        "itens": itens,
        "resumo": {
            "total_itens": len(itens),
            "itens_com_ciclo_anterior_para_comparar": itens_com_2_ciclos,
            "total_carregado_ultimo_ciclo": total_ultimo_geral,
            "total_carregado_ciclo_anterior_itens_comparaveis": total_anterior_comparavel if itens_com_2_ciclos else None,
            "evolucao_percentual_global": evolucao_percentual_global,
        },
    }


# 138ª rodada — pedido do usuário (verbatim): "o relatório gerencial contempla
# as últimas novidades que incluímos no projeto, como a comparação da folha?"
# — não contemplava: "folha_de_pagamento" ficava fixo num placeholder de
# "ainda sem dados estruturados" escrito antes de existirem o funil de
# Rubricas e a Comparação Folha × Ergon (118ª rodada em diante). Substitui
# por dados reais, no mesmo espírito do resto do módulo (tudo pré-calculado
# em Python — a IA só formata/escreve em torno dos números prontos).
#
# Só contagens agregadas de LINHAS COMPARADAS (quantas rubricas bateram/não
# bateram entre o legado e o Ergon, por status/situação/tipo) saem daqui —
# nenhum dado de servidor/folha real do órgão (ver aviso no topo deste
# módulo) — mesma categoria de dado "de processo de implantação" que
# migração de dados já manda (contagens de registros migrados, não os
# registros em si).
def _comparacao_folha(projeto_id):
    rubricas_rows = db.fetch_all(f"""
        SELECT status, count(*) AS total FROM rubricas
        WHERE projeto_id = {db.q(projeto_id)} AND status <> 'Excluída'
        GROUP BY status
    """)
    por_status = {r["status"]: r["total"] for r in rubricas_rows}
    ordem_status = ["Em levantamento", "Enviada à Techne", "Liberada para testes", "Em homologação", "Homologada"]
    rubricas_funil = {
        "total_levantadas": sum(por_status.values()),
        "por_status": [{"status": s, "total": por_status.get(s, 0)} for s in ordem_status],
    }

    comp = comparacao_folha.resumo_por_competencia(projeto_id, limite=5)
    competencias = comp["competencias"]
    if not competencias:
        return {
            "detalhamento_disponivel": rubricas_funil["total_levantadas"] > 0,
            "rubricas_funil": rubricas_funil,
            "comparacao_disponivel": False,
            "observacao": "Nenhuma competência de Comparação Folha × Ergon importada ainda.",
        }

    # Tabela "por Situação" já pivotada (uma linha por situação, uma coluna por
    # competência) — mesmo critério de frontend/index.html:_vexTabelaPorSituacao:
    # "Não Divergente" sempre primeiro; mais de 6 outras situações viram uma
    # linha "Outras situações" agregada.
    mapa_sit, total_por_sit = {}, {}
    for l in comp["por_situacao"]:
        sit = l["situacao"]
        mapa_sit.setdefault(sit, {})[l["mesano"]] = int(l["total"] or 0)
        total_por_sit[sit] = total_por_sit.get(sit, 0) + int(l["total"] or 0)
    categorias = sorted(mapa_sit.keys(), key=lambda s: -total_por_sit[s])
    if "Não Divergente" in categorias:
        categorias.remove("Não Divergente")
        categorias.insert(0, "Não Divergente")
    LIMITE = 6
    linhas_show, resto = categorias[:LIMITE], categorias[LIMITE:]
    if resto:
        outras = {m: sum(mapa_sit[c].get(m, 0) for c in resto) for m in competencias}
        mapa_sit["Outras situações"] = outras
        linhas_show.append("Outras situações")
    total_por_mes = {m: sum(mapa_sit[c].get(m, 0) for c in linhas_show) for m in competencias}

    tabela_por_situacao = []
    for cat in linhas_show:
        linha = {"situacao": cat, "por_competencia": []}
        for m in competencias:
            v = mapa_sit[cat].get(m, 0)
            tm = total_por_mes.get(m, 0)
            linha["por_competencia"].append({
                "competencia": m, "total": v, "percentual_do_mes": round(100 * v / tm) if tm else 0,
            })
        tabela_por_situacao.append(linha)

    # Tabela "por Tipo de Rubrica" (Vantagem/Desconto) — % convergente por
    # competência (mesmo critério de _vexTabelaPorTipoRubrica).
    mapa_tipo = {}
    for l in comp["por_tiporubr"]:
        tipo = l["tiporubr"] or "(sem tipo)"
        total_linhas = int(l.get("total_linhas") or 0)
        convergente = int(l.get("total_sem_divergencia") or 0)
        mapa_tipo.setdefault(tipo, {})[l["mesano"]] = {
            "total_linhas": total_linhas,
            "percentual_convergente": round(100 * convergente / total_linhas) if total_linhas else None,
        }
    ordem_pref = ["VANTAGEM", "DESCONTO"]
    tipos = sorted(mapa_tipo.keys(), key=lambda t: (ordem_pref.index(t.upper()) if t.upper() in ordem_pref else 99, t))
    tabela_por_tipo = []
    for t in tipos:
        linha = {"tipo": t, "por_competencia": []}
        for m in competencias:
            c = mapa_tipo[t].get(m)
            linha["por_competencia"].append({
                "competencia": m,
                "total_linhas": c["total_linhas"] if c else 0,
                "percentual_convergente": c["percentual_convergente"] if c else None,
            })
        tabela_por_tipo.append(linha)

    return {
        "detalhamento_disponivel": True,
        "rubricas_funil": rubricas_funil,
        "comparacao_disponivel": True,
        "competencias": competencias,
        "comparacao_por_situacao": tabela_por_situacao,
        "comparacao_por_tipo_rubrica": tabela_por_tipo,
        # Campos com "_" na frente: só para os GRÁFICOS deste relatório (ver
        # app/relatorio_pdf.py/app/relatorio_graficos.py) — a IA deve ignorá-los
        # ao escrever o texto (ver instrução no PROMPT_TEMPLATE), já estão
        # cobertos pelas tabelas pivotadas acima.
        "_por_tiporubr_grafico": comp["por_tiporubr"],
    }


def coletar_dados_projeto(projeto_id):
    """Monta o retrato completo de dados que embasa o relatório — tudo calculado em
    Python a partir do banco, nada inventado. É esse dicionário (serializado como
    JSON) que vai tanto no prompt da IA quanto gravado em relatorios_executivos.dados_enviados
    para auditoria."""
    projeto = db.fetch_one(f"SELECT * FROM projetos WHERE id = {db.q(projeto_id)}")
    if not projeto:
        raise RelatorioExecutivoError("Projeto não encontrado.")
    horas_dia_util = float(projeto.get("horas_dia_util") or 8.0)

    atividades = db.fetch_all(f"""
        SELECT a.*, f.nome AS frente_nome,
               (SELECT string_agg(r.nome, ', ' ORDER BY r.nome)
                FROM atividade_recurso ar JOIN recursos r ON r.id = ar.recurso_id
                WHERE ar.atividade_id = a.id) AS responsaveis_nomes,
               (a.status NOT IN ('Concluída','Concluída com atraso','Concluída com esforço maior',
                                 'Concluída com atraso e esforço maior','Cancelada')
                AND (
                  (a.dtfim_prev IS NOT NULL AND a.dtfim_prev < CURRENT_DATE)
                  OR (a.status = 'Não iniciada' AND a.dtini_prev IS NOT NULL AND a.dtini_prev < CURRENT_DATE)
                )) AS atrasada,
               CASE
                 WHEN a.status = 'Não iniciada' AND a.dtini_prev IS NOT NULL AND a.dtini_prev < CURRENT_DATE
                      AND (a.dtfim_prev IS NULL OR a.dtfim_prev >= CURRENT_DATE)
                   THEN CURRENT_DATE - a.dtini_prev
                 ELSE CURRENT_DATE - a.dtfim_prev
               END AS dias_atraso
        FROM atividades a
        -- LEFT JOIN (migração 026) — frente_trabalho_id pode ser NULL agora; um INNER
        -- JOIN excluiria essas atividades do Relatório Executivo sem nenhum aviso.
        LEFT JOIN frentes_trabalho f ON f.id = a.frente_trabalho_id
        WHERE a.projeto_id = {db.q(projeto_id)}
    """)
    by_id = {a["id"]: a for a in atividades}
    total = len(atividades)

    excecoes = db.fetch_all(f"SELECT data FROM calendario_util WHERE projeto_id = {db.q(projeto_id)} AND util = FALSE")
    non_working = {_parse_data(e["data"]) for e in excecoes}

    hoje = date.today()

    # ---------------- KPIs gerais ----------------
    concluidas = sum(1 for a in atividades if a["status"] in STATUS_FAMILIA_CONCLUIDA)
    em_andamento = sum(1 for a in atividades if a["status"] == "Em andamento")
    bloqueadas = sum(1 for a in atividades if a["status"] == "Bloqueada")
    canceladas = sum(1 for a in atividades if a["status"] == "Cancelada")
    atrasadas_todas = sorted(
        [a for a in atividades if a["atrasada"]], key=lambda a: -(a["dias_atraso"] or 0)
    )
    criticas = sum(1 for a in atividades if a.get("cpm_critica"))

    kpis = {
        "total_atividades": total,
        "concluidas": concluidas,
        "percentual_concluido_geral": round(100 * concluidas / total, 1) if total else 0,
        "em_andamento": em_andamento,
        "bloqueadas": bloqueadas,
        "canceladas": canceladas,
        "atrasadas": len(atrasadas_todas),
        "no_caminho_critico_do_plano": criticas,
    }

    atrasadas_amostra = [
        {
            "codigo_wbs": a.get("codigo_wbs"), "nome": a["nome"], "frente": a["frente_nome"] or "Sem frente de trabalho",
            "status": a["status"], "fim_previsto": a.get("dtfim_prev"), "dias_atraso": a["dias_atraso"],
            "responsaveis": a.get("responsaveis_nomes"),
        }
        for a in atrasadas_todas[:LIMITE_ATRASADAS_NO_PROMPT]
    ]

    # ---------------- resumo por frente ----------------
    # atividades sem Frente de trabalho (migração 026 — a importação de cronograma
    # pode deixar em branco quando não identifica com confiança um cadastro já
    # existente) entram num grupo próprio, em vez de quebrar o sorted() abaixo
    # (comparar None com str dá erro) ou desaparecer silenciosamente do resumo.
    por_frente = {}
    for a in atividades:
        f = por_frente.setdefault(a["frente_nome"] or "Sem frente de trabalho",
                                   {"total": 0, "concluidas": 0, "em_andamento": 0, "atrasadas": 0})
        f["total"] += 1
        if a["status"] in STATUS_FAMILIA_CONCLUIDA:
            f["concluidas"] += 1
        if a["status"] == "Em andamento":
            f["em_andamento"] += 1
        if a["atrasada"]:
            f["atrasadas"] += 1
    resumo_frentes = [
        {"frente": nome, "total": v["total"], "concluidas": v["concluidas"],
         "em_andamento": v["em_andamento"], "atrasadas": v["atrasadas"],
         "percentual_concluido": round(100 * v["concluidas"] / v["total"], 1) if v["total"] else 0}
        for nome, v in sorted(por_frente.items())
    ]

    # ---------------- bloqueios ativos ----------------
    bloqueios_ativos = [
        {"codigo_wbs": a.get("codigo_wbs"), "nome": a["nome"], "frente": a["frente_nome"] or "Sem frente de trabalho"}
        for a in atividades if a["status"] == "Bloqueada"
    ]

    # ---------------- marcos ----------------
    marcos = db.fetch_all(f"SELECT * FROM marcos WHERE projeto_id = {db.q(projeto_id)}")
    marcos_atrasados = sorted(
        [m for m in marcos if not m.get("data_real") and _parse_data(m["data_prevista"]) and _parse_data(m["data_prevista"]) < hoje],
        key=lambda m: m["data_prevista"],
    )
    marcos_proximos = sorted(
        [m for m in marcos if not m.get("data_real") and _parse_data(m["data_prevista"]) and _parse_data(m["data_prevista"]) >= hoje],
        key=lambda m: m["data_prevista"],
    )[:8]

    # ---------------- riscos abertos ----------------
    riscos = db.fetch_all(
        f"SELECT descricao, categoria, probabilidade, impacto, status FROM riscos "
        f"WHERE projeto_id = {db.q(projeto_id)} AND status != 'Encerrado' ORDER BY "
        f"CASE probabilidade WHEN 'Alto' THEN 0 WHEN 'Médio' THEN 1 ELSE 2 END, "
        f"CASE impacto WHEN 'Alto' THEN 0 WHEN 'Médio' THEN 1 ELSE 2 END"
    )

    # ---------------- atividades master: projeção ----------------
    projecao = _projetar_datas(projeto_id, horas_dia_util, non_working)
    deps_raw = db.fetch_all(
        "SELECT ad.atividade_id, ad.predecessora_id FROM atividade_dependencia ad "
        f"JOIN atividades a ON a.id = ad.atividade_id AND a.projeto_id = {db.q(projeto_id)}"
    )
    deps_por_sucessora = {}
    for d in deps_raw:
        deps_por_sucessora.setdefault(d["atividade_id"], []).append(d["predecessora_id"])

    atividades_master = []
    for a in atividades:
        if not a.get("eh_atividade_master"):
            continue
        proj = projecao.get(a["id"])
        dtfim_prev = _parse_data(a.get("dtfim_prev"))
        item = {
            "codigo_wbs": a.get("codigo_wbs"), "nome": a["nome"], "frente": a["frente_nome"] or "Sem frente de trabalho",
            "status": a["status"], "percentual_concluido": a.get("percentual_concluido"),
            "responsaveis": a.get("responsaveis_nomes"),
            "fim_previsto_no_plano": a.get("dtfim_prev"),
            "no_caminho_critico_do_plano": bool(a.get("cpm_critica")),
        }
        if proj and proj["fim_proj"]:
            item["fim_projetado"] = proj["fim_proj"].isoformat()
            item["confianca_da_previsao"] = proj["confianca"]
            if dtfim_prev:
                item["atraso_projetado_dias_uteis"] = dias_uteis_entre(dtfim_prev, proj["fim_proj"], non_working)
        else:
            item["fim_projetado"] = None
            item["confianca_da_previsao"] = "indeterminada"
        bloqueios = _bloqueios_no_caminho(a["id"], deps_por_sucessora, by_id)
        if bloqueios:
            item["bloqueada_por"] = bloqueios
        atividades_master.append(item)

    return {
        "projeto": {
            "nome": projeto.get("nome"), "sigla": projeto.get("sigla"), "cliente": projeto.get("cliente"),
            "data_inicio": projeto.get("data_inicio"), "data_fim_prevista": projeto.get("data_fim_prevista"),
            "prazo_total_meses": projeto.get("prazo_total_meses"),
        },
        "gerado_em": hoje.isoformat(),
        "kpis": kpis,
        "atividades_atrasadas_amostra": atrasadas_amostra,
        "atividades_atrasadas_total": len(atrasadas_todas),
        "resumo_por_frente": resumo_frentes,
        "bloqueios_ativos": bloqueios_ativos,
        "marcos_atrasados": [{"nome": m["nome"], "data_prevista": m["data_prevista"]} for m in marcos_atrasados],
        "marcos_proximos": [{"nome": m["nome"], "data_prevista": m["data_prevista"]} for m in marcos_proximos],
        "riscos_abertos": riscos,
        "atividades_master": atividades_master,
        # 57ª/58ª rodada (migração 031) — atividades com data_execucao_fixada=true cuja
        # data prevista já não respeita o que uma predecessora direta exige hoje (ver
        # app/cronograma_anomalias.py). Reaproveita o mesmo detector do card do
        # Dashboard, por isso passa a própria STATUS_FAMILIA_CONCLUIDA deste módulo
        # (duplicada de main.py de propósito — ver comentário no topo do arquivo).
        "anomalias_data_fixada": cronograma_anomalias.detectar(projeto_id, STATUS_FAMILIA_CONCLUIDA),
        # 50ª rodada — dois temas novos, sempre ao final do relatório (ver PROMPT_TEMPLATE):
        "migracao_de_dados": _migracao_de_dados(projeto_id),
        # 138ª rodada — antes era um placeholder fixo de "ainda sem dados"; agora reflete
        # o funil de Rubricas + a Comparação Folha × Ergon reais (ver _comparacao_folha()).
        "folha_de_pagamento": _comparacao_folha(projeto_id),
    }


PROMPT_TEMPLATE = """\
Você é um consultor sênior de PMO (gestão de projetos) escrevendo um relatório executivo
para a diretoria de um órgão público e para a diretoria da empresa de consultoria, sobre um
projeto de implantação de sistema de Gestão de Pessoas e Folha de Pagamento. O público-alvo
NÃO é técnico em cronograma — é gestor público e gestor de
projeto. Escreva em português do Brasil, tom profissional e direto, sem jargão técnico
desnecessário.

REGRA MAIS IMPORTANTE: use SOMENTE os números e fatos do JSON abaixo. Não invente datas,
percentuais, nomes ou prazos que não estejam explicitamente nos dados. Se um dado não
existir (ex: nenhuma atividade master marcada), diga isso explicitamente em vez de
adivinhar. Os campos "fim_projetado" e "atraso_projetado_dias_uteis" já vêm calculados
por um algoritmo determinístico (extrapolação do ritmo de execução observado,
propagando atrasos pela cadeia de dependências) — não recalcule, apenas interprete e
explique o que esses números significam na prática. O campo "confianca_da_previsao"
indica quão confiável é essa previsão ("alta" = já concluída/fato consumado; "média" =
extrapolação de ritmo; "baixa" = atividade bloqueada, data é um piso mínimo, não uma
previsão real; "fixada" = atividade com a data de execução travada manualmente — o fim
projetado é o próprio fim previsto no plano, propositalmente sem levar em conta o que
aconteceu com as predecessoras, porque essa trava só é removida por decisão humana).

DADOS DO PROJETO (JSON):
{dados_json}

Estruture o relatório em markdown com exatamente estas seções, usando "##" para os
títulos:

## Resumo executivo
3 a 5 frases: situação geral do projeto, se está no prazo ou não, e qual é a
preocupação principal (se houver).

## Diagnóstico geral
Progresso por frente de trabalho, comparando ritmo entre frentes. Aponte quais frentes
estão puxando o atraso, se houver diferença relevante entre elas.

## Atrasos e bloqueios críticos
Liste as atividades atrasadas mais relevantes (não precisa listar todas se houver
muitas — priorize as de maior atraso e as citadas em "atividades_master" ou seus
bloqueios) e destaque qualquer atividade BLOQUEADA — bloqueio é o tipo de risco mais
urgente porque só se resolve com ação humana, não com mais tempo.

## Previsão da(s) atividade(s) master
Se "atividades_master" estiver vazio, diga claramente que nenhuma atividade foi marcada
como entregável mestre do projeto e recomende marcar pelo menos uma (ex: a atividade da
Folha Definitiva) para que este relatório possa estimar uma data de conclusão do
projeto. Se houver itens, para cada um: status atual, data prevista original x data
projetada, o tamanho do desvio em dias úteis, o nível de confiança dessa previsão, e se
depende de alguma atividade bloqueada.

## Anomalias de Data Fixada
Se "anomalias_data_fixada" estiver vazio, escreva 1 frase confirmando que nenhuma
atividade com data de execução fixada está em conflito com suas predecessoras no momento
— não precisa de mais nada nesta seção. Se houver itens, para cada um explique: qual
atividade está com a data fixada, qual predecessora dela mudou/está causando o conflito,
o tipo de dependência (FS/SS/FF/SF) envolvido, e a diferença em dias úteis entre a data
fixada e a data que a dependência exigiria — deixe claro que o sistema NÃO altera essa
data sozinho (é fixada, só edição manual muda) e que por isso precisa de uma decisão
humana: manter a data mesmo assim, ou desmarcar a trava e deixar recalcular.

## Riscos abertos
Resuma os riscos abertos, priorizando por probabilidade/impacto.

## Recomendações
3 a 5 recomendações objetivas e acionáveis para as próximas 1-2 semanas, priorizadas
pelo que mais reduz o risco ao prazo da(s) atividade(s) master.

## Migração de Dados
Apresente um quadro comparativo, tabela a tabela, com todos os itens de
"migracao_de_dados.itens": uma linha por item, comparando o carregado no ciclo anterior
com o carregado no último ciclo e o % de evolução (ou involução, se negativo). Monte essa
tabela em sintaxe de tabela markdown (GFM: uma linha de cabeçalho, uma linha separadora
"---" logo abaixo, células separadas por "|") com as colunas, nesta ordem: Tabela do
legado | Destino | Carregado (ciclo anterior) | Carregado (último ciclo) | Evolução | %
Rejeições (último ciclo). Use EXATAMENTE os valores já calculados em "ultimo_ciclo.carregado",
"ciclo_anterior.carregado", "evolucao_percentual" e "ultimo_ciclo.percentual_rejeicao" — não
recalcule nada. Quando "ciclo_anterior" for null, escreva "—" nas colunas de carregado do
ciclo anterior/evolução e, na coluna Evolução, o texto de "evolucao_observacao" (ex: "sem
ciclo anterior para comparação"). Na coluna Evolução, quando houver percentual, escreva no
formato "+12,3%" (evolução) ou "-8,5%" (involução) — troque o ponto decimal do JSON por
vírgula, e sempre com o sinal. Na coluna % Rejeições (último ciclo), escreva no formato
"X,XX%" (troque o ponto decimal do JSON por vírgula, SEM sinal — é uma taxa, não uma
evolução); quando não houver "ultimo_ciclo" ou "percentual_rejeicao" for null, escreva "—".
Depois da tabela, escreva um parágrafo curto com o total geral
(resumo.total_carregado_ultimo_ciclo, resumo.evolucao_percentual_global) e destacando por
nome qualquer tabela com involução (percentual negativo) ou com taxa de rejeição alta, se
houver — involução merece atenção da diretoria porque normalmente indica retrabalho ou erro
de carga que precisou ser desfeito.

## Folha de Pagamento
Se "folha_de_pagamento.detalhamento_disponivel" for false, escreva 1-2 frases registrando
que o tema ainda está em levantamento/parametrização (sem Rubricas levantadas ainda) — sem
inventar nenhum número, status ou prazo. Caso contrário, estruture assim:

1. Um parágrafo curto com o funil de Rubricas: total levantado
("folha_de_pagamento.rubricas_funil.total_levantadas") e a distribuição por status
("rubricas_funil.por_status" — já vem na ordem do fluxo: Em levantamento → Enviada à
Techne → Liberada para testes → Em homologação → Homologada). Destaque o % já homologado
sobre o total.

2. Se "folha_de_pagamento.comparacao_disponivel" for false, encerre a seção com 1 frase
dizendo que nenhuma competência de Comparação Folha × Ergon foi importada ainda — pule os
itens 3 e 4 abaixo. Caso contrário, continue:

3. Uma tabela markdown (GFM, mesma sintaxe da tabela de Migração de Dados) "Comparação por
Situação": uma linha por item de "folha_de_pagamento.comparacao_por_situacao" (a ordem já
vem pronta — "Não Divergente" sempre primeiro), uma coluna por competência de
"folha_de_pagamento.competencias" (cabeçalho = a competência no formato "AAAA-MM" mesmo,
não precisa formatar). Em cada célula, use o item correspondente em "por_competencia"
(mesma ordem de "competencias") no formato "total (percentual_do_mes%)" — ex: "111.039
(40%)" — usando EXATAMENTE os valores já calculados, sem recalcular, e com separador de
milhar "." (ex: "111.039", não "111039"). Primeira coluna: "Situação".

4. Logo depois, outra tabela markdown "Comparação por Tipo de Rubrica — % convergente":
uma linha por item de "folha_de_pagamento.comparacao_por_tipo_rubrica" (primeira coluna
"Tipo"), mesmas colunas de competência. Em cada célula use o "percentual_convergente"
correspondente no formato "XX%" (ou "—" quando for null — significa 0 linhas comparadas
naquela competência para aquele tipo). Depois das duas tabelas, 1-2 frases apontando a
situação mais crítica (menor % convergente, ou tipo/situação com mais divergência) se
houver alguma clara — sem alarmismo se os números já estiverem bons.

Ignore qualquer campo de "folha_de_pagamento" cujo nome comece com "_" (ex:
"_por_tiporubr_grafico") — é usado só pelos gráficos deste relatório, nunca no texto.

Não adicione seções além dessas. Não use tabelas em markdown em NENHUMA seção, EXCETO as
tabelas pedidas em "Migração de Dados" (1 tabela) e "Folha de Pagamento" (até 2 tabelas) —
nas demais seções use listas com "-" quando precisar enumerar itens (o texto também vai
para PDF, que só sabe renderizar "##", parágrafo, lista com "-" e tabela markdown; qualquer
outra coisa não renderiza bem lá).
"""


def montar_prompt(dados):
    return PROMPT_TEMPLATE.format(dados_json=json.dumps(dados, ensure_ascii=False, indent=2, default=str))


def chamar_ia(prompt):
    # 105ª/106ª rodada — a chamada em si (com fallback automático pra
    # OpenAI quando a Anthropic falha e OPENAI_API_KEY está configurada)
    # saiu daqui e virou app/ia_provider.py, compartilhado com
    # requisitos_ia.py — ver aquele módulo para detalhes.
    #
    # max_tokens=8000 (não o default de 2000 do ia_provider): 52ª rodada,
    # bug real encontrado — com 4000 o relatório vinha sendo cortado ANTES
    # de chegar nas duas últimas seções (Migração de Dados / Folha de
    # Pagamento — ver PROMPT_TEMPLATE, elas ficam no fim de propósito). Com
    # 8 seções e uma tabela por item de migração, 4000 tokens ficou pequeno
    # demais; 8000 dá folga confortável mesmo com bastante atividade
    # atrasada/itens de migração listados.
    try:
        texto, tokens_entrada, tokens_saida, modelo_usado, truncado = ia_provider.chamar_ia(prompt, max_tokens=8000)
    except ia_provider.IAProviderError as e:
        raise RelatorioExecutivoError(str(e))
    if truncado:
        # Não falha o relatório por causa disso (o texto gerado até aqui ainda pode ser
        # útil), mas registra no log pra não repetir o mesmo "quadro sumiu" sem
        # explicação — se isso aparecer de novo, é sinal de que o limite precisa subir
        # de novo (ou o prompt precisa ficar mais enxuto).
        print(
            f"[relatorio_executivo] AVISO: resposta da IA ({modelo_usado}) cortada por atingir o teto de "
            f"tokens ({tokens_saida} tokens gerados) — o relatório pode estar incompleto (seções do fim, "
            "como Migração de Dados/Folha de Pagamento, podem ter ficado de fora).",
            flush=True,
        )
    return texto, modelo_usado


def gerar_relatorio(projeto_id, gerado_por=None):
    """Coleta os dados, chama a IA, grava em relatorios_executivos e retorna a linha criada."""
    dados = coletar_dados_projeto(projeto_id)
    prompt = montar_prompt(dados)
    conteudo_md, modelo_usado = chamar_ia(prompt)
    row = db.execute_returning_one(
        "INSERT INTO relatorios_executivos (projeto_id, modelo_ia, conteudo_md, dados_enviados, gerado_por) "
        f"VALUES ({db.q(projeto_id)}, {db.q(modelo_usado)}, {db.q(conteudo_md)}, "
        f"{db.q(json.dumps(dados, ensure_ascii=False, default=str))}::jsonb, {db.q(gerado_por)}) RETURNING *"
    )
    return row
