-- 169ª rodada (continuação) -- pedido do usuário, sobre o banner "Carga em
-- andamento" de Comparação Folha: "nessa carga tem como ter um contador de
-- completude? que seja linhas carregadas / linhas totais, ou % de carga?"
--
-- Até aqui, cargas_comparacao_folha só sabia "em_andamento" ou terminado --
-- nenhuma visibilidade de quanto já tinha sido processado NO MEIO da
-- importação. Duas colunas novas, as duas nullable e só preenchidas durante
-- uma carga em andamento (ficam para trás, intactas, em cargas antigas já
-- concluídas antes desta rodada):
--
--   - linhas_processadas: quantas linhas da planilha/CSV já foram lidas e
--     escritas no COPY até agora -- atualizada periodicamente (não a cada
--     linha, custaria caro demais -- ver comentário de intervalo_progresso
--     em comparacao_folha_import.py) pela própria thread de importação.
--   - total_linhas_estimado: uma ESTIMATIVA do total de linhas do arquivo,
--     calculada antes de começar a processar de verdade. Deliberadamente
--     chamada de "estimado", não "total" -- ver comentário grande em
--     comparacao_folha_import._estimar_total_linhas() pro motivo de não dar
--     pra saber o número exato sem uma segunda passada pelo arquivo inteiro
--     (contradiria o design "single-pass" documentado no topo daquele
--     arquivo, pensado pra não repetir o custo de ler um arquivo de
--     ~130MB/~300 mil linhas duas vezes).
--
-- Mesmo padrão de pid/cancelamento_solicitado (144ª rodada, migration_041):
-- fica no banco (não em memória do processo Python) porque os 4 workers
-- gunicorn (ver Dockerfile, -w 4) não compartilham memória entre si -- o
-- GET /comparacao-folha/carga-atual que o front-end faz polling pode cair
-- em qualquer um deles, e só o banco é visto por todos.

ALTER TABLE cargas_comparacao_folha
  ADD COLUMN linhas_processadas integer,
  ADD COLUMN total_linhas_estimado integer;

COMMENT ON COLUMN cargas_comparacao_folha.linhas_processadas IS '169ª rodada -- quantas linhas já foram lidas/escritas no COPY até agora, atualizada periodicamente pela própria thread de importação (ver comparacao_folha_import.importar_comparacao_folha, parâmetro ao_progredir, e app/main.py:_ao_progredir_carga_comparacao_folha). NULL antes da primeira atualização de progresso.';
COMMENT ON COLUMN cargas_comparacao_folha.total_linhas_estimado IS '169ª rodada -- estimativa (não exata -- ver comparacao_folha_import._estimar_total_linhas) do total de linhas do arquivo sendo importado, calculada uma vez no início da carga, antes do processamento linha a linha. NULL quando a estimativa não pôde ser calculada (ex: planilha .xlsx sem a tag <dimension> preenchida) -- nesse caso o front-end mostra só a contagem corrida, sem % nem "de quanto".';
