-- 141ª rodada — pedido do usuário (verbatim): "Na aba comparação de folha
-- incluir como campos de filtros Matrícula, Número Funcional, Setor
-- Funcional". Matrícula e Nº Funcional (numfunc) já são colunas existentes
-- de comparacao_folha (matrícula já tem índice desde a migration_030); o
-- filtro novo de Setor Funcional (setorfunc) é igualdade exata (combo
-- populado só com valores que já existem nos dados, mesmo critério de
-- Empresa/TipoVINC — ver comparacao_folha.resumo()/_where_filtros()), então
-- se beneficia de um índice normal, igual aos já existentes em
-- situacao/rubrica_ergon. A tabela chega a ~250-300 mil linhas por
-- competência, então um filtro novo usado com frequência sem índice vira
-- table scan.
CREATE INDEX IF NOT EXISTS idx_comparacao_folha_setorfunc
  ON comparacao_folha(projeto_id, setorfunc);
