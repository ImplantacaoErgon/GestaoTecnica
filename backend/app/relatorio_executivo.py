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
import os
from datetime import date, timedelta

import networkx as nx

from . import db
from .cpm import _dur_dias, _business_day_offset, dias_uteis_entre

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
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
    duração planejada restante) ou "baixa" (atividade bloqueada — data é um piso, não
    uma previsão confiável, porque não sabemos quando o bloqueio será resolvido)."""
    atividades = db.fetch_all(f"""
        SELECT id, status, percentual_concluido, prazo_horas, dtini_prev, dtfim_prev, dtini_real, dtfim_real
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
previsão real).

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

## Riscos abertos
Resuma os riscos abertos, priorizando por probabilidade/impacto.

## Recomendações
3 a 5 recomendações objetivas e acionáveis para as próximas 1-2 semanas, priorizadas
pelo que mais reduz o risco ao prazo da(s) atividade(s) master.

Não adicione seções além dessas. Não use tabelas em markdown (o texto vai também para
PDF e tabelas markdown não renderizam bem lá) — use listas com "-" quando precisar
enumerar itens.
"""


def montar_prompt(dados):
    return PROMPT_TEMPLATE.format(dados_json=json.dumps(dados, ensure_ascii=False, indent=2, default=str))


def chamar_ia(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RelatorioExecutivoError(
            "ANTHROPIC_API_KEY não configurada no backend. Gere uma chave em "
            "console.anthropic.com e coloque-a no arquivo .env (veja o README, seção "
            "\"Relatório Executivo (IA)\")."
        )
    try:
        import anthropic
    except ImportError:
        raise RelatorioExecutivoError(
            "Pacote 'anthropic' não instalado na imagem do backend — rode "
            "'docker compose up -d --build' após atualizar requirements.txt."
        )
    client = anthropic.Anthropic(api_key=api_key)
    try:
        resposta = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise RelatorioExecutivoError("Chave da API da Anthropic inválida ou expirada (ANTHROPIC_API_KEY).")
    except anthropic.RateLimitError:
        raise RelatorioExecutivoError("Limite de uso da API da Anthropic atingido — tente novamente em alguns minutos.")
    except anthropic.APIError as e:
        raise RelatorioExecutivoError(f"Erro ao chamar a API da Anthropic: {e}")
    texto = "".join(bloco.text for bloco in resposta.content if getattr(bloco, "type", None) == "text")
    if not texto.strip():
        raise RelatorioExecutivoError("A IA retornou uma resposta vazia — tente gerar novamente.")
    return texto


def gerar_relatorio(projeto_id, gerado_por=None):
    """Coleta os dados, chama a IA, grava em relatorios_executivos e retorna a linha criada."""
    dados = coletar_dados_projeto(projeto_id)
    prompt = montar_prompt(dados)
    conteudo_md = chamar_ia(prompt)
    row = db.execute_returning_one(
        "INSERT INTO relatorios_executivos (projeto_id, modelo_ia, conteudo_md, dados_enviados, gerado_por) "
        f"VALUES ({db.q(projeto_id)}, {db.q(ANTHROPIC_MODEL)}, {db.q(conteudo_md)}, "
        f"{db.q(json.dumps(dados, ensure_ascii=False, default=str))}::jsonb, {db.q(gerado_por)}) RETURNING *"
    )
    return row
