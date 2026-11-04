-- 170ª rodada — a planilha "Levantamento Rubricas" do cliente ganhou a coluna
-- "Motivo de Envio à Techne" (logo depois de "Data de envio à TECHNE").
-- Pedido do usuário (verbatim): "Muitas rubricas são enviadas para
-- programação, mas sem o motivo não podemos controlar as mudanças de regras.
-- Apesar que apenas um campo sem temporalidade não nos permite validar o
-- histórico."
--
-- Duas partes:
--   1) rubricas.motivo_envio_techne — o valor ATUAL, igual à planilha.
--   2) rubricas_envios_techne_historico — a "temporalidade" que a planilha não
--      tem: um trigger grava uma linha a cada vez que o motivo OU a data de
--      envio à Techne de uma rubrica muda (por importação da planilha, por
--      atualização automática ou por edição na tela), com os valores anteriores,
--      quem mudou e quando. Não depende de nenhuma mudança de código pra
--      funcionar — qualquer caminho que escreva em `rubricas` é coberto. Esta
--      migração já grava uma "linha de base" (origem 'linha de base (migração
--      048)') pra cada rubrica que já tem data de envio à Techne, de modo que
--      a primeira mudança de motivo depois dela apareça como mudança a partir
--      do valor que existia, e não como se fosse o primeiro registro.
--      Reimportar a mesma planilha sem mudança NÃO gera linhas (o trigger só
--      dispara quando o valor realmente muda).
--
-- Quem mudou (registrado_por) e por qual caminho (origem) vêm de
-- current_setting('app.usuario_atual') / ('app.origem_mudanca'), definidos pelo
-- backend com SET LOCAL na mesma transação do INSERT/UPDATE (mesmo mecanismo
-- do histórico de status de atividades); ficam NULL se o backend não informar.

ALTER TABLE rubricas ADD COLUMN IF NOT EXISTS motivo_envio_techne text;
COMMENT ON COLUMN rubricas.motivo_envio_techne IS '170ª rodada — coluna "Motivo de Envio à Techne" da planilha de Levantamento de Rubricas (ex: "Programação"). Valor atual; o histórico de mudanças fica em rubricas_envios_techne_historico.';

CREATE TABLE IF NOT EXISTS rubricas_envios_techne_historico (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rubrica_id           uuid NOT NULL REFERENCES rubricas(id) ON DELETE CASCADE,
  projeto_id           uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  data_envio_techne    date,           -- valor NOVO (depois da mudança)
  motivo_envio_techne  text,           -- valor NOVO
  data_envio_anterior  date,           -- valor ANTES (NULL na primeira gravação)
  motivo_anterior      text,
  registrado_em        timestamptz NOT NULL DEFAULT now(),
  registrado_por       text,
  origem               text            -- ex: 'importação da planilha', 'atualização automática', 'edição na tela'
);
CREATE INDEX IF NOT EXISTS idx_rubricas_envios_techne_hist_rubrica
  ON rubricas_envios_techne_historico (rubrica_id, registrado_em DESC);
CREATE INDEX IF NOT EXISTS idx_rubricas_envios_techne_hist_projeto
  ON rubricas_envios_techne_historico (projeto_id, registrado_em DESC);
COMMENT ON TABLE rubricas_envios_techne_historico IS '170ª rodada — histórico automático (trigger trg_rubricas_historico_envio_techne) das mudanças de motivo/data de envio à Techne de cada rubrica. Nunca escrita pela aplicação diretamente.';

CREATE OR REPLACE FUNCTION registrar_historico_envio_techne() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.motivo_envio_techne IS NULL AND NEW.data_envio_techne IS NULL THEN
      RETURN NEW;
    END IF;
    INSERT INTO rubricas_envios_techne_historico
      (rubrica_id, projeto_id, data_envio_techne, motivo_envio_techne,
       registrado_por, origem)
    VALUES (NEW.id, NEW.projeto_id, NEW.data_envio_techne, NEW.motivo_envio_techne,
            current_setting('app.usuario_atual', true), current_setting('app.origem_mudanca', true));
  ELSIF NEW.motivo_envio_techne IS DISTINCT FROM OLD.motivo_envio_techne
     OR NEW.data_envio_techne IS DISTINCT FROM OLD.data_envio_techne THEN
    INSERT INTO rubricas_envios_techne_historico
      (rubrica_id, projeto_id, data_envio_techne, motivo_envio_techne,
       data_envio_anterior, motivo_anterior, registrado_por, origem)
    VALUES (NEW.id, NEW.projeto_id, NEW.data_envio_techne, NEW.motivo_envio_techne,
            OLD.data_envio_techne, OLD.motivo_envio_techne,
            current_setting('app.usuario_atual', true), current_setting('app.origem_mudanca', true));
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_rubricas_historico_envio_techne ON rubricas;
CREATE TRIGGER trg_rubricas_historico_envio_techne
  AFTER INSERT OR UPDATE ON rubricas
  FOR EACH ROW EXECUTE FUNCTION registrar_historico_envio_techne();

-- Linha de base: situação atual de cada rubrica que já foi enviada à Techne.
INSERT INTO rubricas_envios_techne_historico
  (rubrica_id, projeto_id, data_envio_techne, motivo_envio_techne, origem)
SELECT r.id, r.projeto_id, r.data_envio_techne, r.motivo_envio_techne, 'linha de base (migração 048)'
FROM rubricas r
WHERE (r.data_envio_techne IS NOT NULL OR r.motivo_envio_techne IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM rubricas_envios_techne_historico h WHERE h.rubrica_id = r.id);
