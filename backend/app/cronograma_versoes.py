"""
Histórico de versões do cronograma (34ª rodada).

Regra de ouro, pedida explicitamente pelo cliente: NADA aqui roda sozinho.
As rotas normais de criar/editar/excluir atividade ou marco (main.py) não
importam este módulo e não sabem que ele existe — elas continuam apenas
sobrepondo `atividades`/`marcos`, exatamente como sempre fizeram. Uma
"versão" só nasce quando o usuário aciona explicitamente o botão "Gerar
versão" na tela de Cronograma, que chama `criar_versao()` abaixo. É por
isso, inclusive, que logo depois de gerar uma versão ela é idêntica aos
dados ao vivo — só diverge depois, conforme o cronograma real for mudando.

`dados` (jsonb) guarda o conteúdo já RESOLVIDO — nomes de etapa/frente/
recursos/predecessoras, não só ids — para que abrir uma versão antiga
continue fazendo sentido mesmo que uma etapa, frente ou recurso tenha sido
renomeado ou excluído depois de a versão ter sido gerada.
"""
import json

from . import db


def montar_snapshot(projeto_id, atividade_select_sql):
    """Monta a "foto" do cronograma no instante atual. `atividade_select_sql`
    é o ATIVIDADE_SELECT de main.py (reaproveitado para que a versão saia
    com exatamente as mesmas colunas/derivações — atrasada, esforço
    estourado, responsáveis etc. — que o resto do sistema usa)."""
    atividades = db.fetch_all(
        atividade_select_sql
        + f" WHERE a.projeto_id = {db.q(projeto_id)} ORDER BY a.codigo_wbs NULLS LAST, a.nome"
    )
    marcos = db.fetch_all(f"""
        SELECT m.*, e.numero AS etapa_numero, e.nome AS etapa_nome
        FROM marcos m
        LEFT JOIN etapas e ON e.id = m.etapa_id
        WHERE m.projeto_id = {db.q(projeto_id)}
        ORDER BY m.data_prevista
    """)
    dependencias = db.fetch_all(f"""
        SELECT d.id, d.tipo, d.lag_horas,
               d.atividade_id, a.codigo_wbs AS atividade_codigo_wbs, a.nome AS atividade_nome,
               d.predecessora_id, p.codigo_wbs AS predecessora_codigo_wbs, p.nome AS predecessora_nome
        FROM atividade_dependencia d
        JOIN atividades a ON a.id = d.atividade_id
        JOIN atividades p ON p.id = d.predecessora_id
        WHERE a.projeto_id = {db.q(projeto_id)}
        ORDER BY a.codigo_wbs NULLS LAST
    """)
    return {"atividades": atividades, "marcos": marcos, "dependencias": dependencias}


def criar_versao(projeto_id, rotulo, usuario, atividade_select_sql):
    """Gera e grava uma nova versão (foto completa) do cronograma do
    projeto. `numero_versao` é sequencial por projeto, calculado dentro do
    próprio INSERT (MAX + 1) para ficar atômico num único statement."""
    snapshot = montar_snapshot(projeto_id, atividade_select_sql)
    # default=str cobre qualquer valor que o json padrão não serializa sozinho
    # (ex.: Decimal vindo de numeric, embora aqui os dados já chegam como
    # dict/list "puros" — json.loads do fetch_all já converteu tudo).
    dados_json = json.dumps(snapshot, default=str, ensure_ascii=False)
    row = db.execute_returning_one(f"""
        INSERT INTO cronograma_versoes
            (projeto_id, numero_versao, rotulo, dados, total_atividades, total_marcos, criado_por)
        VALUES (
            {db.q(projeto_id)},
            COALESCE((SELECT MAX(numero_versao) FROM cronograma_versoes WHERE projeto_id = {db.q(projeto_id)}), 0) + 1,
            {db.q(rotulo or None)},
            {db.q(dados_json)}::jsonb,
            {len(snapshot["atividades"])},
            {len(snapshot["marcos"])},
            {db.q(usuario or None)}
        )
        RETURNING id, projeto_id, numero_versao, rotulo, total_atividades, total_marcos, criado_em, criado_por
    """)
    return row


def listar_versoes(projeto_id):
    """Lista leve (sem o jsonb pesado de `dados`) para a tela mostrar o
    histórico de versões geradas."""
    return db.fetch_all(f"""
        SELECT id, projeto_id, numero_versao, rotulo, total_atividades, total_marcos, criado_em, criado_por
        FROM cronograma_versoes
        WHERE projeto_id = {db.q(projeto_id)}
        ORDER BY numero_versao DESC
    """)


def obter_versao(versao_id):
    """Detalhe completo de uma versão específica, `dados` incluso — para
    abrir/visualizar a foto daquele momento."""
    return db.fetch_one(f"""
        SELECT id, projeto_id, numero_versao, rotulo, dados, total_atividades, total_marcos, criado_em, criado_por
        FROM cronograma_versoes
        WHERE id = {db.q(versao_id)}
    """)
