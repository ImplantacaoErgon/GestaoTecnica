-- 144ª rodada: corrige o botão "Cancelar" (143ª rodada, migration_040) de
-- uma carga de Comparação Folha em andamento. A 143ª rodada guardava o
-- processo psql num dict em MEMÓRIA do processo Python que iniciou a
-- carga -- e isso quebrou em produção porque o Render roda 4 processos
-- gunicorn separados (-w 4, ver backend/Dockerfile): o clique em
-- "Cancelar" podia cair num worker diferente do que estava rodando a
-- carga, que não tinha nada na própria memória pra cancelar, e sempre
-- devolvia "não está em andamento" mesmo com a carga genuinamente ativa.
--
-- Agora o estado fica no banco (que os 4 workers compartilham, diferente
-- da memória de cada um): `pid` guarda o PID do processo `psql` daquela
-- carga assim que ele abre, e `cancelamento_solicitado` é marcado pelo
-- endpoint de cancelar (de QUALQUER worker) -- quem efetivamente mata o
-- processo manda um SIGTERM direto nesse PID (os workers são
-- processos-irmãos no mesmo container, então o PID é alcançável de
-- qualquer um deles).

ALTER TABLE cargas_comparacao_folha
  ADD COLUMN pid integer,
  ADD COLUMN cancelamento_solicitado boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN cargas_comparacao_folha.pid IS '144ª rodada -- PID do processo psql (db.execute_stream) que está gravando esta carga no banco, gravado assim que ele abre. Usado por POST /comparacao-folha/carga/<id>/cancelar pra mandar SIGTERM diretamente, de qualquer worker gunicorn (ver comentário grande em app/main.py:_ao_iniciar_processo_carga_comparacao_folha).';
COMMENT ON COLUMN cargas_comparacao_folha.cancelamento_solicitado IS '144ª rodada -- true quando o usuário clicou "Cancelar" nesta carga. Consultado por db.execute_stream (parâmetro foi_cancelado) pra decidir, quando o processo psql termina com erro, se foi um cancelamento de propósito (status final vira cancelado) ou uma falha de verdade (status final vira erro).';
