-- ============================================================================
-- Migração 019 — corrige atividades com Status/percentual/Fim Real inconsistentes
-- ============================================================================
-- Não muda nenhuma estrutura de tabela — é uma correção de DADOS.
--
-- Contexto (28ª rodada): até esta rodada, Status, percentual_concluido e as
-- datas reais eram campos totalmente independentes, sem nenhuma checagem
-- cruzada. Era possível (e aconteceu, num teste real do usuário) marcar uma
-- atividade com 100% concluído e a Data de Fim Real preenchida, mas deixar o
-- Status em "Não iniciada" — o card "Atividades master" do Dashboard, que só
-- reconhece uma atividade como concluída pelo Status, ficava então
-- reprojetando uma data de término (e mostrando "atraso") pra uma atividade
-- que já estava pronta.
--
-- A partir desta rodada, o backend passa a impedir GRAVAR uma atividade
-- assim (ver validar_consistencia_conclusao() em main.py) — mas isso só vale
-- pra daqui pra frente. Esta migração corrige o que já estiver inconsistente
-- no banco hoje, nas duas direções óbvias e seguras (não inventa nenhuma
-- data que não exista):
--
--   1) 100% concluído + Fim Real preenchido, mas Status ainda não é
--      Concluída/Cancelada -> corrige o Status para Concluída.
--   2) Status já é Concluída, mas percentual_concluido não é 100
--      -> corrige o percentual para 100.
--
-- Uma atividade com Status = Concluída mas SEM Fim Real preenchido não é
-- corrigida aqui de propósito (não há data pra inventar) — a partir de
-- agora, a próxima tentativa de salvar essa atividade vai pedir que o Fim
-- Real seja preenchido, então ela fica sinalizada naturalmente pelo próprio
-- uso do sistema, sem precisar de um script de auditoria à parte.

BEGIN;

UPDATE atividades
SET status = 'Concluída'
WHERE percentual_concluido = 100
  AND dtfim_real IS NOT NULL
  AND status NOT IN ('Concluída', 'Cancelada');

UPDATE atividades
SET percentual_concluido = 100
WHERE status = 'Concluída'
  AND percentual_concluido <> 100;

COMMIT;
