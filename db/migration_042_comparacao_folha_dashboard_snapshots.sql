-- 146ª rodada: botão "Salvar" no Dashboard Comparação Folha. Pedido do
-- usuário (verbatim): "crie um botão para salvar o dashboard. Com a carga
-- da comparação da folha as informações anteriores do dashboard se
-- perdem. Salvar esses dados vai permitir fazer comparações de uma mesma
-- competência."
--
-- Reimportar uma competência substitui as linhas antigas (DELETE+COPY) --
-- os indicadores do Dashboard daquele estado anterior se perdem assim que
-- uma carga nova entra. Esta tabela guarda uma foto JSONB do retorno de
-- comparacao_folha.dashboard(), só sob ação explícita do usuário (nunca
-- automaticamente) -- mesmo espírito de cronograma_versoes (migration_024),
-- mas escopada por COMPETÊNCIA (projeto_id + mesano), não só por projeto:
-- numeração sequencial por (projeto_id, mesano), não por projeto sozinho.

CREATE TABLE comparacao_folha_dashboard_snapshots (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id     uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  mesano         date NOT NULL,
  numero_versao  integer NOT NULL,
  rotulo         text,
  dados          jsonb NOT NULL,
  total_linhas   integer NOT NULL DEFAULT 0,
  criado_em      timestamptz NOT NULL DEFAULT now(),
  criado_por     text,
  UNIQUE (projeto_id, mesano, numero_versao)
);
CREATE INDEX idx_cf_dashboard_snapshots_projeto_mes ON comparacao_folha_dashboard_snapshots (projeto_id, mesano, numero_versao DESC);
COMMENT ON TABLE comparacao_folha_dashboard_snapshots IS 'Fotos dos indicadores do Dashboard Comparação Folha (comparacao_folha.dashboard()), geradas só sob ação explícita do usuário (botão "Salvar"), escopadas por competência -- permitem comparar a mesma competência ao longo do tempo, já que reimportar um mês substitui os dados ao vivo. Ver backend/app/comparacao_folha_dashboard_snapshots.py.';
