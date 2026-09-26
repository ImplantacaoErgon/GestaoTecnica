-- Migração 027 (41ª rodada): cadastro formal de Pendências.
--
-- Contexto: riscos (evento futuro possível) e marcos (data contratual) não
-- cobrem uma terceira situação, pedida explicitamente pelo usuário —
-- "pendência": um problema JÁ em curso, que precisa ser resolvido, com
-- responsável dos dois lados (consultoria e cliente) e prazo cobrando
-- solução. Pode ou não estar ligada a uma atividade do cronograma e/ou a
-- um risco já cadastrado.
--
-- Já existia um mecanismo mais simples de "pendência" embutido no relato de
-- andamento de uma atividade (atividade_relato.eh_pendencia e os campos
-- pendencia_*) — sempre vinculado a uma atividade, um único responsável,
-- sem ligação com frente/risco, sem "ações necessárias"/"impactos". A
-- pedido do usuário, esta migração UNIFICA os dois mecanismos no novo
-- cadastro `pendencias` — não havia dado relevante em atividade_relato
-- pendente de preservação, então os campos antigos são simplesmente
-- removidos (ver DROP COLUMN abaixo). O relato de andamento volta a ser só
-- o texto livre que já era antes de existir eh_pendencia; sinalizar uma
-- pendência a partir de uma atividade agora é feito abrindo o cadastro
-- novo (pré-preenchido com a atividade) direto pela aba "Pendências" da
-- própria atividade.

CREATE TYPE status_pendencia_enum AS ENUM ('Aberta','Em andamento','Resolvida','Cancelada');

-- ============================================================================
-- PENDÊNCIAS
-- ============================================================================
CREATE TABLE pendencias (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id                  uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  codigo                      text NOT NULL,        -- gerado pelo backend, formato "PND-001" (sequencial por projeto)
  titulo                      text NOT NULL,         -- identificação curta da pendência
  frente_trabalho_id          uuid NOT NULL REFERENCES frentes_trabalho(id),
  atividade_id                uuid REFERENCES atividades(id) ON DELETE SET NULL,  -- opcional
  risco_id                    uuid REFERENCES riscos(id) ON DELETE SET NULL,      -- opcional
  descricao                   text NOT NULL,
  impactos                    text,
  responsavel_consultoria_id  uuid NOT NULL REFERENCES recursos(id),  -- precisa ser recurso tipo_vinculo = 'Consultoria' (validado no backend)
  responsavel_cliente_id      uuid NOT NULL REFERENCES recursos(id),  -- precisa ser recurso tipo_vinculo <> 'Consultoria' (validado no backend)
  data_identificacao          date NOT NULL DEFAULT CURRENT_DATE,
  data_limite                 date NOT NULL,   -- prazo máximo tolerável ("data limite de solução")
  data_prevista                date,           -- estimativa realista/atual de solução (pode mudar ao longo do acompanhamento)
  data_real                   date,           -- data em que foi efetivamente resolvida
  status                      status_pendencia_enum NOT NULL DEFAULT 'Aberta',
  prioridade                  prioridade_enum NOT NULL DEFAULT 'Média',
  categoria                   text,            -- livre: Técnica, Contratual, Financeira, Dados, Operacional…
  acoes_necessarias           text,
  observacoes                 text,
  criado_em                   timestamptz NOT NULL DEFAULT now(),
  atualizado_em                timestamptz NOT NULL DEFAULT now(),
  UNIQUE (projeto_id, codigo)
);
CREATE INDEX idx_pendencias_projeto ON pendencias(projeto_id);
CREATE INDEX idx_pendencias_atividade ON pendencias(atividade_id) WHERE atividade_id IS NOT NULL;
CREATE INDEX idx_pendencias_risco ON pendencias(risco_id) WHERE risco_id IS NOT NULL;
CREATE INDEX idx_pendencias_abertas ON pendencias(projeto_id, data_limite) WHERE status NOT IN ('Resolvida','Cancelada');

CREATE TRIGGER trg_pendencias_atualizado_em BEFORE UPDATE ON pendencias
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

COMMENT ON TABLE pendencias IS 'Registro formal de pendências (RAID log) — problema em curso, com responsável de cada lado (consultoria/cliente) e prazo. Pode linkar opcionalmente com uma atividade e/ou um risco. Sem rota de exclusão (mesmo padrão de riscos/marcos/requisitos) — usar status ''Cancelada'' em vez de apagar.';

-- ============================================================================
-- Remove o mecanismo antigo de "pendência" embutido no relato de atividade
-- (unificado no cadastro `pendencias` acima, a pedido do usuário — sem dado
-- relevante para preservar).
-- ============================================================================
DROP INDEX IF EXISTS idx_relato_pendencia_aberta;
ALTER TABLE atividade_relato
  DROP COLUMN IF EXISTS eh_pendencia,
  DROP COLUMN IF EXISTS pendencia_responsavel_id,
  DROP COLUMN IF EXISTS pendencia_prazo_possivel,
  DROP COLUMN IF EXISTS pendencia_data_limite,
  DROP COLUMN IF EXISTS pendencia_resolvida,
  DROP COLUMN IF EXISTS pendencia_resolvida_em;
