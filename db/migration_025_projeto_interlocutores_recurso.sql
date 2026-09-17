-- Migração 025 (36ª rodada): vincula os 5 campos de interlocutores do
-- projeto (Fiscal, Gestor, Gerente de projeto do cliente/da Consultoria,
-- Líder de projeto da Consultoria) a um recurso cadastrado, sem perder a
-- possibilidade de nome digitado livremente — mesmo padrão já usado em
-- faturas.responsavel_recebimento_recurso_id.
--
-- As colunas de texto já existentes (fiscal_projeto, gestor_projeto,
-- gerente_projeto_cliente, gerente_projeto_consultoria,
-- lider_projeto_consultoria) NÃO são alteradas nem apagadas — continuam
-- guardando o nome. A partir de agora ele é um "retrato" congelado: quando
-- há um *_recurso_id vinculado, o backend sobrescreve o nome com o cadastro
-- do recurso a cada salvamento (ver preparar_projeto_interlocutores em
-- main.py); sem *_recurso_id (pessoa ainda sem cadastro), o nome continua
-- sendo só texto digitado livremente, exatamente como já era antes desta
-- migração. Por isso ela é 100% aditiva e não perde nenhum valor já
-- cadastrado nos projetos existentes.
ALTER TABLE projetos
  ADD COLUMN fiscal_projeto_recurso_id             uuid REFERENCES recursos(id) ON DELETE SET NULL,
  ADD COLUMN gestor_projeto_recurso_id              uuid REFERENCES recursos(id) ON DELETE SET NULL,
  ADD COLUMN gerente_projeto_cliente_recurso_id     uuid REFERENCES recursos(id) ON DELETE SET NULL,
  ADD COLUMN gerente_projeto_consultoria_recurso_id uuid REFERENCES recursos(id) ON DELETE SET NULL,
  ADD COLUMN lider_projeto_consultoria_recurso_id   uuid REFERENCES recursos(id) ON DELETE SET NULL;

COMMENT ON COLUMN projetos.fiscal_projeto_recurso_id IS 'Recurso cadastrado vinculado ao Fiscal do projeto, se houver (ver fiscal_projeto, que guarda o nome congelado). NULL quando o nome foi só digitado, sem cadastro.';
COMMENT ON COLUMN projetos.gestor_projeto_recurso_id IS 'Idem, para Gestor do projeto (gestor_projeto).';
COMMENT ON COLUMN projetos.gerente_projeto_cliente_recurso_id IS 'Idem, para Gerente de projeto do cliente (gerente_projeto_cliente).';
COMMENT ON COLUMN projetos.gerente_projeto_consultoria_recurso_id IS 'Idem, para Gerente de projeto da Consultoria (gerente_projeto_consultoria) — precisa ser um recurso Consultoria (checado no backend).';
COMMENT ON COLUMN projetos.lider_projeto_consultoria_recurso_id IS 'Idem, para Líder de projeto da Consultoria (lider_projeto_consultoria) — precisa ser um recurso Consultoria (checado no backend).';
