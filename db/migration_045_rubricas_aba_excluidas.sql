-- 167ª rodada: contagem da aba "Excluídas" da planilha de Rubricas (não gera
-- registros em `rubricas` -- é uma aba só com rubricas que o cliente já tirou
-- de cogitação, com as mesmas colunas da aba principal "Levantamento
-- Rubricas"). Pedido do usuário (verbatim): "a ideia é apenas contar os
-- registros da aba de excluídas e atualizar o totalizador que tem na página
-- somados às linhas tachadas da aba principal. O Total de rubricas deve ser
-- sempre considerado apenas da aba principal."
--
-- Guardado no PROJETO (não em `rubricas`) porque é um valor único por
-- projeto, recalculado do zero a cada reimportação da planilha (manual ou
-- automática via Drive) -- ver rubricas_import.contar_aba_excluidas e
-- app/main.py:atualizar_contagem_aba_excluidas_rubricas.
ALTER TABLE projetos ADD COLUMN rubricas_aba_excluidas_qtd integer;
ALTER TABLE projetos ADD COLUMN rubricas_aba_excluidas_atualizado_em timestamp;

COMMENT ON COLUMN projetos.rubricas_aba_excluidas_qtd IS 'Quantidade de linhas com dado na aba "Excluídas" da planilha de Rubricas, na última reimportação em que essa aba existiu no arquivo (167ª rodada) -- somada ao total de Excluídas mostrado na tela, ver resumo_rubricas/renderRubricas/_vexRenderFolha. NULL = nunca contabilizado.';
COMMENT ON COLUMN projetos.rubricas_aba_excluidas_atualizado_em IS 'Quando rubricas_aba_excluidas_qtd foi atualizado por último.';
