-- 48ª rodada: catálogo de itens de migração de dados (tabelas do sistema
-- legado a migrar para o Ergon), com quantidade planejada (meta) x carregada
-- por item, e ciclos de execução vinculados a cada item.
--
-- ciclos_migracao já existia (migration original, ver schema.sql linha ~370)
-- ligada a uma ATIVIDADE (o "tema" da migração, ex: "Migração de Pessoas") —
-- itens_migracao é uma camada mais fina: uma linha por TABELA do legado
-- dentro desse tema, cada uma com sua própria meta de registros. Os ciclos
-- de execução passam a se referir ao item (item_migracao_id), não mais só
-- à atividade; atividade_id em ciclos_migracao fica opcional (mantido por
-- compatibilidade, não é mais o vínculo obrigatório).

CREATE TYPE status_item_migracao_enum AS ENUM (
  'Não iniciado', 'Em andamento', 'Concluído', 'Bloqueado'
);

CREATE TABLE itens_migracao (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  atividade_id uuid REFERENCES atividades(id),
  nome_tabela_legado text NOT NULL,
  nome_tabela_destino text,
  sistema_origem text,
  qtd_registros_estimada integer,
  status status_item_migracao_enum NOT NULL DEFAULT 'Não iniciado',
  responsavel_id uuid REFERENCES recursos(id),
  observacoes text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (projeto_id, nome_tabela_legado)
);
CREATE INDEX idx_itens_migracao_projeto ON itens_migracao(projeto_id);
CREATE INDEX idx_itens_migracao_status ON itens_migracao(status);
CREATE TRIGGER trg_itens_migracao_atualizado_em
  BEFORE UPDATE ON itens_migracao
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
COMMENT ON TABLE itens_migracao IS 'Uma linha por tabela/entidade do sistema legado a migrar pro Ergon — meta de registros (qtd_registros_estimada) x carregado, apurado a partir do último ciclo em ciclos_migracao vinculado a este item.';

ALTER TABLE ciclos_migracao ADD COLUMN item_migracao_id uuid REFERENCES itens_migracao(id) ON DELETE CASCADE;
ALTER TABLE ciclos_migracao ALTER COLUMN atividade_id DROP NOT NULL;
CREATE INDEX idx_ciclos_migracao_item ON ciclos_migracao(item_migracao_id);
CREATE UNIQUE INDEX ciclos_migracao_item_numero_key ON ciclos_migracao(item_migracao_id, numero_ciclo) WHERE item_migracao_id IS NOT NULL;
