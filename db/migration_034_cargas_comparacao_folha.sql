-- 83ª rodada: carga da Comparação Folha (via "Selecionar arquivo no Drive")
-- deixa de ser síncrona. Até então, POST /comparacao-folha/importar/picker
-- fazia uma única requisição HTTP que baixava do Google Drive E importava
-- o arquivo inteiro (até ~300 mil linhas) sem devolver resposta até
-- terminar — o usuário reportou (verbatim) que precisava "ficar na janela
-- onde a carga está sendo feita" e perguntou se não dava pra virar um
-- processo em segundo plano (batch).
--
-- Esta tabela guarda o andamento de cada carga iniciada: o endpoint grava
-- uma linha "em_andamento" e devolve na hora (o navegador não precisa mais
-- ficar esperando — a importação de verdade roda numa thread separada no
-- servidor), e o front-end consulta GET /comparacao-folha/carga-atual pra
-- mostrar o status, inclusive depois de fechar e reabrir a aba.

CREATE TABLE cargas_comparacao_folha (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'em_andamento' CHECK (status IN ('em_andamento', 'concluido', 'erro')),
  nome_arquivo text,
  mesano date,
  total_linhas integer,
  avisos jsonb,
  mensagem_erro text,
  iniciado_por uuid REFERENCES usuarios(id),
  iniciado_por_nome text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  concluido_em timestamptz
);

CREATE INDEX idx_cargas_comparacao_folha_projeto ON cargas_comparacao_folha(projeto_id, criado_em DESC);

-- No máximo uma carga "em_andamento" por projeto de cada vez — evita duas
-- importações concorrentes pisando uma na outra (o DELETE + COPY de
-- comparacao_folha_import.importar_comparacao_folha não foi desenhado pra
-- rodar em paralelo consigo mesmo na mesma competência). O backend traduz
-- a violação desse índice num erro amigável ("já existe uma carga em
-- andamento") em vez de deixar a exceção crua do Postgres subir.
CREATE UNIQUE INDEX idx_cargas_comparacao_folha_ativa ON cargas_comparacao_folha(projeto_id) WHERE status = 'em_andamento';

COMMENT ON TABLE cargas_comparacao_folha IS 'Acompanhamento assíncrono da carga de Comparação Folha via Google Drive Picker (83ª rodada) -- uma linha por tentativa de importação, criada como em_andamento antes da thread de processamento começar, atualizada para concluido ou erro ao final. Ver POST /comparacao-folha/importar/picker e GET /comparacao-folha/carga-atual em app/main.py.';
