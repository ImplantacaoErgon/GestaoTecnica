-- ============================================================================
-- Migração 024 — histórico de versões do cronograma (34ª rodada).
-- Puramente aditiva (tabela nova) — não apaga nem transforma nenhum dado
-- existente, e não mexe em nenhuma rotina de criação/edição/exclusão de
-- atividades ou marcos.
-- ============================================================================
--
-- Contexto: o cliente pediu que o cronograma tenha histórico, mas deixou bem
-- claro que as edições do dia a dia (mudar data, excluir ou criar atividade)
-- NUNCA devem gerar histórico sozinhas — elas continuam sempre sobrepondo os
-- dados "ao vivo" de `atividades`/`marcos`, exatamente como hoje. O histórico
-- só nasce quando o usuário decide, clicando num botão dedicado ("Gerar
-- versão") que tira uma foto completa do cronograma naquele instante. Por
-- isso essa é uma tabela nova e independente — nada nas tabelas existentes
-- muda, e nenhuma rota de INSERT/UPDATE/DELETE de atividades/marcos precisa
-- (nem deve) passar a gravar aqui.
--
-- `dados` guarda o conteúdo já resolvido (nomes, não só ids) para a versão
-- continuar íntegra mesmo que uma etapa/frente/recurso seja renomeado ou
-- excluído depois — ver backend/app/cronograma_versoes.py.

CREATE TABLE cronograma_versoes (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id        uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  numero_versao     integer NOT NULL,
  rotulo            text,
  dados             jsonb NOT NULL,
  total_atividades  integer NOT NULL DEFAULT 0,
  total_marcos      integer NOT NULL DEFAULT 0,
  criado_em         timestamptz NOT NULL DEFAULT now(),
  criado_por        text,
  UNIQUE (projeto_id, numero_versao)
);
CREATE INDEX idx_cronograma_versoes_projeto ON cronograma_versoes (projeto_id, numero_versao DESC);
COMMENT ON TABLE cronograma_versoes IS 'Fotos completas do cronograma, geradas só sob ação explícita do usuário (botão "Gerar versão"). Nunca criadas automaticamente por CRUD de atividades/marcos.';
