-- 156ª rodada: nova coluna em rubricas_auto_update_execucoes pra registrar
-- quantas rubricas foram marcadas como "Excluída" na própria atualização
-- automática (seg-sex, 8h-18h, horário de Brasília — ver
-- app/rubricas_auto_update.py) — mesma lógica que o botão manual "Importar
-- planilha"/"Atualizar Rubricas" passou a ter nesta rodada: pedido do
-- usuário (verbatim) "quando for atualizar a tabela com base na planilha,
-- caso uma linha da planilha tenha sumido considere que essa linha foi
-- excluída, crie esse status e marque como Excluída".
--
-- Nullable de propósito (sem DEFAULT 0): execuções antigas (antes desta
-- rodada) não têm esse dado — NULL deixa claro "não contabilizado" em vez
-- de fingir que foi zero.
ALTER TABLE rubricas_auto_update_execucoes ADD COLUMN excluidas integer;

COMMENT ON COLUMN rubricas_auto_update_execucoes.excluidas IS 'Quantas rubricas foram marcadas como status Excluída nesta execução por terem sumido da planilha de origem (156ª rodada) -- ver app/main.py:marcar_rubricas_ausentes_como_excluidas. NULL em execuções anteriores a esta rodada, não contabilizadas.';
