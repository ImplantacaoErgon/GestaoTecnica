-- 143ª rodada: botão "Cancelar" numa carga de Comparação Folha em
-- andamento (upload manual ou Google Picker). Precisa de um status novo
-- pra não confundir com 'erro' (que sugere algo deu errado) — cancelar é
-- uma ação intencional do usuário, e os dados que já existiam no mês
-- continuam intactos (o DELETE+COPY nunca chegou no COMMIT, ver
-- app/db.py:execute_stream e app/main.py:_marcar_carga_comparacao_folha_cancelada).

ALTER TABLE cargas_comparacao_folha DROP CONSTRAINT cargas_comparacao_folha_status_check;

ALTER TABLE cargas_comparacao_folha
  ADD CONSTRAINT cargas_comparacao_folha_status_check
  CHECK (status IN ('em_andamento', 'concluido', 'erro', 'cancelado'));

COMMENT ON TABLE cargas_comparacao_folha IS 'Acompanhamento assíncrono da carga de Comparação Folha via Google Drive Picker (83ª rodada) ou upload manual (90ª rodada) -- uma linha por tentativa de importação, criada como em_andamento antes da thread de processamento começar, atualizada para concluido, erro ou cancelado (143ª rodada -- botão "Cancelar", ver POST /comparacao-folha/carga/<id>/cancelar) ao final. Ver POST /comparacao-folha/importar/picker|upload e GET /comparacao-folha/carga-atual em app/main.py.';
