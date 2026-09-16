-- ============================================================================
-- Migração 013 — controle de horas por recurso
--
-- Contexto: "Alterar a palavra Responsáveis por Recursos em todo o sistema.
-- Na tabela de Recursos implementar um campo que determina se o referido
-- recurso deve ter controle de horas por atividade. Apenas os recursos que
-- tem essa marca é que podem registrar as informações em Minhas Atividades
-- e por conseguinte serem apresentados como filtro do relatório 'Minhas
-- Atividades'."
--
-- Adiciona `recursos.controla_horas` (boolean, default true — não afeta
-- ninguém que já usa "Minhas atividades" hoje; o administrador desmarca
-- manualmente, em Configurações > Recursos, quem não deve apontar horas).
-- `backend/app/minhas_atividades.recursos_do_usuario()` passa a filtrar por
-- esta coluna, e o combo de filtro do Relatório de Horas dos Recursos (ex
-- "Relatório de Minhas Atividades") no frontend só lista recursos com a
-- marca ativa. Não afeta quem pode ser adicionado como participante de uma
-- atividade no Cronograma (`atividade_recurso`) — isso continua livre para
-- qualquer recurso, marcado ou não.
--
-- Puramente aditiva e segura: não remove nem renomeia nenhuma coluna
-- existente, e o valor padrão (true) preserva o comportamento atual para
-- todos os recursos já cadastrados.
-- ============================================================================
BEGIN;

ALTER TABLE recursos ADD COLUMN IF NOT EXISTS controla_horas boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN recursos.controla_horas IS
  'Se true, este recurso pode registrar apontamento de horas em "Minhas '
  'atividades" e aparece como opção de filtro no Relatório de Horas dos '
  'Recursos. Não afeta se ele pode ser adicionado como participante de uma '
  'atividade no Cronograma (isso continua livre para qualquer recurso).';

COMMIT;
