-- 113ª rodada: atualização automática das Rubricas (pedido do usuário) --
-- o mesmo fluxo do botão "Atualizar Rubricas" (busca a planilha mais
-- recente no Google Drive e importa -- ver app/drive_rubricas.py e as rotas
-- /api/rubricas/importar/drive/preview + /confirmar em main.py), rodando
-- sozinho de hora em hora, de segunda a sexta, das 8h às 18h, no horário de
-- Brasília -- ver app/rubricas_auto_update.py. O botão manual continua
-- existindo e funcionando exatamente como antes, sem nenhuma mudança.
--
-- Por que uma tabela de controle, e não só "rodar direto": o processo web
-- roda com `gunicorn -w 4` (ver Dockerfile) -- 4 processos independentes, e
-- cada um deles liga sua própria thread de agendamento (mais simples e sem
-- dependência nova do que coordenar uma única thread entre processos
-- diferentes). Sem trava nenhuma, os 4 workers disparariam a MESMA
-- atualização ao mesmo tempo, toda hora -- 4 downloads do Drive e 4 upserts
-- concorrentes a cada disparo. A trava usa a mesma técnica já usada em
-- cargas_comparacao_folha (migração 034): um índice único que só permite
-- UMA linha por "janela" de disparo -- o worker cujo INSERT tiver sucesso é
-- quem de fato executa a atualização; os outros recebem erro de chave
-- duplicada do Postgres e simplesmente não fazem nada (não é um erro de
-- verdade, é a trava funcionando como esperado).
--
-- `janela` é o texto "AAAA-MM-DD HH" no horário de Brasília (ex:
-- "2026-10-03 09"), não um timestamp exato, de propósito -- é o que
-- identifica "a atualização das 9h de hoje", não importa qual worker/
-- réplica chegue primeiro nem o segundo exato em que cada um checou o
-- relógio.
--
-- A tabela também serve de histórico/auditoria: a tela de Rubricas mostra a
-- data/hora e o resultado da última atualização automática (GET
-- /api/rubricas/auto-update/status), pro usuário confirmar que o processo
-- está rodando sem precisar olhar log de servidor.

CREATE TABLE rubricas_auto_update_execucoes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  janela          text NOT NULL,         -- "AAAA-MM-DD HH" em horário de Brasília -- ver comentário acima
  status          text NOT NULL DEFAULT 'em_andamento' CHECK (status IN ('em_andamento', 'concluido', 'erro')),
  nome_arquivo    text,
  inseridos       integer,
  atualizados     integer,
  mensagem_erro   text,
  iniciado_em     timestamptz NOT NULL DEFAULT now(),
  concluido_em    timestamptz
);

-- A trava de concorrência entre os workers do gunicorn (ver comentário
-- acima) -- uma linha só por projeto+janela, não importa o status.
CREATE UNIQUE INDEX idx_rubricas_auto_update_janela ON rubricas_auto_update_execucoes(projeto_id, janela);
CREATE INDEX idx_rubricas_auto_update_recentes ON rubricas_auto_update_execucoes(projeto_id, iniciado_em DESC);

COMMENT ON TABLE rubricas_auto_update_execucoes IS 'Histórico + trava de concorrência (índice único por projeto+janela) da atualização automática de Rubricas via Google Drive, de hora em hora (seg-sex, 8h-18h, horário de Brasília) -- ver app/rubricas_auto_update.py. Uma linha por disparo de janela (não por worker do gunicorn que tentou disparar).';
