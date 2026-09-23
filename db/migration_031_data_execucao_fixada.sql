-- 57ª/58ª rodada: Data de Execução Fixada — trava Início/Fim previsto de uma
-- atividade contra recálculo automático por dependência (o Replanejamento e a
-- cascata de dependências da Edição em Lote nunca mais tocam essas datas
-- quando a trava está ligada); só edição manual direta continua podendo
-- mudá-las. Ver app/cronograma_anomalias.py: quando uma predecessora muda de
-- um jeito que "puxaria" a atividade fixada para outra data, isso não é
-- aplicado sozinho — vira um alerta de anomalia (Dashboard + Relatório
-- Executivo), pra decisão manual.
--
-- Pedido do usuário verbatim: "no cronograma crie um campo para fixar a data
-- de execução. Quando essa atividade estiver com essa 'marca' ela pode ser
-- alterada apenas manualmente. Caso uma atividade dessa for afetada pela
-- dependência um alerta no relatório deve ser apresentado como uma anomalia."

ALTER TABLE atividades ADD COLUMN data_execucao_fixada boolean NOT NULL DEFAULT false;

-- Mesmo padrão de idx_atividades_master/idx_atividades_entregavel: índice
-- parcial, só cobre as (poucas) atividades fixadas — é sempre um filtro
-- "= true" que faz esse índice valer a pena.
CREATE INDEX idx_atividades_data_fixada ON atividades(projeto_id) WHERE data_execucao_fixada = true;

COMMENT ON COLUMN atividades.data_execucao_fixada IS 'Trava dtini_prev/dtfim_prev desta atividade contra recálculo automático por dependência (cronograma_replanejamento.py e a cascata de cronograma_edicao_lote.py) -- só edição manual direta (ver ATIVIDADE_FIELDS/update_atividade em main.py) pode mudar essas datas quando esta trava está ligada. Ver app/cronograma_anomalias.py para a detecção de conflito quando uma predecessora muda e a atividade fixada fica em desacordo com o que a dependência exige (mostrado como anomalia no Dashboard e no Relatório Executivo).';
