-- 99ª rodada: "Pergunta livre à IA" na tela Analisar Requisitos — pedido do
-- usuário (verbatim): "pensei em ter um campo para prompt... quero uma
-- análise sobre determinados requisitos ou quero uma análise sobre
-- determinada semântica do TR para determinar por exemplo se um assunto
-- específico faz parte do escopo". Primeira versão combinada (acordada com
-- o usuário): pergunta objetiva sobre os requisitos que já estão na lista
-- (respeitando os filtros/seleção atuais da tela) — não sobre o TR inteiro
-- ainda (isso fica para uma evolução futura, com um passo de pré-filtro
-- próprio, ver comentário em app/requisitos_ia.py).
--
-- Mesmo espírito da busca por palavra-chave já existente (app/manuais.py) e
-- do Relatório Executivo (app/relatorio_executivo.py, mesma
-- ANTHROPIC_API_KEY/ANTHROPIC_MODEL, reaproveitados sem configuração nova):
-- a resposta da IA é sempre um CANDIDATO pra revisão do consultor, nunca uma
-- decisão automática de escopo — TR é documento contratual.
--
-- Esta tabela é o histórico (auditoria) de cada pergunta feita: guarda os
-- IDS dos requisitos considerados (retrato do momento — não é FK, pra
-- sobreviver à exclusão de um requisito depois), a pergunta, a resposta, o
-- modelo usado e os tokens de entrada/saída (direto da API, sempre exatos)
-- — a partir dos tokens dá pra estimar o custo em USD (tabela de preço fixa
-- no código, não é a fatura real da Anthropic, ver
-- requisitos_ia.PRECOS_USD_POR_MILHAO_TOKENS).
CREATE TABLE requisitos_ia_perguntas (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id         uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  requisito_ids      jsonb NOT NULL DEFAULT '[]'::jsonb,
  qtd_requisitos     integer NOT NULL DEFAULT 0,
  pergunta           text NOT NULL,
  resposta           text NOT NULL,
  modelo_ia          text NOT NULL,
  tokens_entrada     integer NOT NULL DEFAULT 0,
  tokens_saida       integer NOT NULL DEFAULT 0,
  custo_usd_estimado numeric(10,4),
  perguntado_por     uuid REFERENCES usuarios(id),
  perguntado_por_nome text,
  criado_em          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_requisitos_ia_perguntas_projeto ON requisitos_ia_perguntas(projeto_id, criado_em DESC);

COMMENT ON TABLE requisitos_ia_perguntas IS 'Histórico de perguntas livres (IA) feitas na tela Analisar Requisitos sobre um conjunto de requisitos do TR filtrado/selecionado pelo consultor -- ver app/requisitos_ia.py e POST /api/requisitos/perguntar-ia. custo_usd_estimado é uma ESTIMATIVA (tabela de preço fixa no código, a partir dos tokens reais retornados pela API), não a fatura real da Anthropic.';
