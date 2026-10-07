"""
Salvamentos ("fotos") do Dashboard Comparação Folha — 146ª rodada.

Pedido do usuário (verbatim): "crie um botão para salvar o dashboard. Com
a carga da comparação da folha as informações anteriores do dashboard se
perdem. Salvar esses dados vai permitir fazer comparações de uma mesma
competência. Mais fácil salvar essas informações do que uma imagem
completa da Comparação que são milhares de linhas."

Contexto: reimportar uma competência SUBSTITUI as linhas antigas daquele
mês (DELETE+COPY, ver comparacao_folha_import.py) — então os indicadores
do Dashboard (comparacao_folha.dashboard()) daquele estado anterior somem
assim que uma nova carga entra. Este módulo guarda uma foto JSONB do
retorno de dashboard() sob demanda (nunca sozinho), pra permitir comparar
a MESMA competência em momentos diferentes (ex.: antes/depois de corrigir
uma regra de cálculo, ou entre duas cargas sucessivas do legado).

Mesmo espírito de cronograma_versoes.py (histórico sob demanda, numeração
sequencial, nunca disparado automaticamente por nenhuma rota de carga) —
só que escopado por COMPETÊNCIA (projeto_id + mesano), não só por projeto:
faz sentido comparar "versão 1 vs versão 2" de setembro/2026, não misturar
numeração com a de outras competências.
"""
import json

from . import db


def criar_snapshot(projeto_id, mesano, rotulo, usuario, dashboard_payload):
    """Grava uma foto do retorno de comparacao_folha.dashboard() já
    calculado por quem chamou (main.py) — não recalcula aqui, pra garantir
    que o que fica salvo é exatamente o que o usuário estava olhando na
    tela no momento de clicar "Salvar". `numero_versao` é sequencial por
    (projeto_id, mesano), calculado dentro do próprio INSERT (MAX + 1)."""
    dados_json = json.dumps(dashboard_payload, default=str, ensure_ascii=False)
    geral = (dashboard_payload or {}).get("geral") or {}
    row = db.execute_returning_one(f"""
        INSERT INTO comparacao_folha_dashboard_snapshots
            (projeto_id, mesano, numero_versao, rotulo, dados, total_linhas, criado_por)
        VALUES (
            {db.q(projeto_id)},
            {db.q(mesano + '-01')}::date,
            COALESCE((SELECT MAX(numero_versao) FROM comparacao_folha_dashboard_snapshots
                      WHERE projeto_id = {db.q(projeto_id)}
                        AND date_trunc('month', mesano) = date_trunc('month', {db.q(mesano + '-01')}::date)), 0) + 1,
            {db.q(rotulo or None)},
            {db.q(dados_json)}::jsonb,
            {db.q(geral.get("total") or 0)},
            {db.q(usuario or None)}
        )
        RETURNING id, projeto_id, numero_versao, rotulo, total_linhas, criado_em, criado_por
    """)
    # O INSERT acima não devolve os campos "geral" prontos pro front-end
    # atualizar a lista na hora sem precisar de uma segunda chamada — anexa
    # aqui (não vem do banco, vem do mesmo payload já em mãos).
    if row:
        row["geral"] = geral
    return row


def listar_snapshots(projeto_id, mesano):
    """Lista leve (sem o jsonb pesado de `dados`) para a tela mostrar o
    histórico de salvamentos desta competência. `geral` vem extraído de
    `dados->'geral'` — os 5 indicadores do topo do Dashboard — suficiente
    pra comparar de relance sem precisar abrir cada snapshot."""
    return db.fetch_all(f"""
        SELECT id, projeto_id, numero_versao, rotulo, total_linhas, criado_em, criado_por,
               dados->'geral' AS geral
        FROM comparacao_folha_dashboard_snapshots
        WHERE projeto_id = {db.q(projeto_id)}
          AND date_trunc('month', mesano) = date_trunc('month', {db.q(mesano + '-01')}::date)
        ORDER BY numero_versao DESC
    """)


def excluir_snapshot(snapshot_id):
    """Apaga um salvamento do histórico. Devolve os dados básicos do
    registro apagado (pro Log de Auditoria) ou None se o id não existia."""
    return db.execute_returning_one(f"""
        DELETE FROM comparacao_folha_dashboard_snapshots
        WHERE id = {db.q(snapshot_id)}
        RETURNING id, projeto_id, numero_versao, rotulo, total_linhas
    """)
