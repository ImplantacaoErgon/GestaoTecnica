-- 105ª rodada — conserta uma lentidão séria (timeout de 60s em produção)
-- no Dashboard de Comparação Folha, causada pela correção da 100ª/101ª
-- rodada (ver comentário completo em app/comparacao_folha.py, função
-- _codigo_igual_sql).
--
-- Resumo: comparar "57" com "00057" (mesmo código, zero à esquerda
-- diferente) exige normalizar os dois lados antes de comparar. A versão
-- anterior fazia isso com uma expressão ad-hoc (regex + CAST ou regex +
-- LTRIM) direto no SQL de cada consulta — o Postgres NÃO consegue usar
-- nenhum índice pra resolver esse tipo de expressão, então em consultas
-- com EXISTS/NOT EXISTS correlacionado (reavaliado linha a linha) contra a
-- tabela comparacao_folha (~250-300 mil linhas por competência em
-- produção), isso virava uma varredura completa da tabela menor para CADA
-- uma das ~300 mil linhas — na prática, trava a consulta (timeout).
--
-- A correção: colocar a normalização numa função SQL própria, marcada
-- IMMUTABLE (o resultado só depende do argumento, nunca muda) — isso
-- permite criar um ÍNDICE FUNCIONAL sobre ela. Com os dois lados da
-- comparação usando a MESMA função, e havendo índice funcional dos dois
-- lados, o Postgres volta a poder usar índice pra resolver o cruzamento.
--
-- Testado localmente com dados em escala de produção (250 mil linhas em
-- comparacao_folha, até 3400 em rubricas): a consulta que travava (sem
-- terminar depois de 90s) passou a rodar em ~3-4s depois desta migração.

CREATE OR REPLACE FUNCTION normaliza_codigo_rubrica(codigo text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN codigo ~ '^[0-9]+$' THEN COALESCE(NULLIF(LTRIM(codigo, '0'), ''), '0')
    ELSE codigo
  END
$$;

COMMENT ON FUNCTION normaliza_codigo_rubrica(text) IS
  'Normaliza um código de rubrica/verba para comparação tolerante a zero à '
  'esquerda: se for puramente numérico, remove os zeros à esquerda (mantendo '
  'pelo menos um dígito); senão, mantém o valor original. IMMUTABLE de '
  'propósito — permite índice funcional (ver migration_036). Usada em '
  'app/comparacao_folha.py (_codigo_igual_sql) para cruzar '
  'rubricas.codigo_ergon/verba_legado com '
  'comparacao_folha.rubrica_ergon/verba_consist, que guardam o mesmo código '
  'em formatos de zero-padding diferentes.';

-- Índices funcionais — criar DEPOIS da função (CREATE INDEX precisa que a
-- função já exista). Sem CONCURRENTLY de propósito: mais simples de rodar
-- num único psql -f, e o tempo de criação em ~250-300 mil linhas é de
-- poucos segundos — aceitável como operação pontual. Se preferir evitar
-- qualquer lock de escrita durante a criação, pode rodar os quatro
-- CREATE INDEX abaixo manualmente com CONCURRENTLY, um de cada vez (fora de
-- bloco de transação).
CREATE INDEX IF NOT EXISTS idx_rubricas_codigo_ergon_norm
  ON rubricas (projeto_id, normaliza_codigo_rubrica(codigo_ergon));
CREATE INDEX IF NOT EXISTS idx_rubricas_verba_legado_norm
  ON rubricas (projeto_id, normaliza_codigo_rubrica(verba_legado));
CREATE INDEX IF NOT EXISTS idx_comparacao_folha_rubrica_ergon_norm
  ON comparacao_folha (projeto_id, normaliza_codigo_rubrica(rubrica_ergon));
CREATE INDEX IF NOT EXISTS idx_comparacao_folha_verba_consist_norm
  ON comparacao_folha (projeto_id, normaliza_codigo_rubrica(verba_consist));
