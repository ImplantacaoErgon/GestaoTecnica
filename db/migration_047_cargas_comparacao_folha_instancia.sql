-- 169ª rodada -- bug real reportado pelo usuário: clicou "Cancelar" numa
-- carga de Comparação Folha em andamento, a mensagem confirmou que ia
-- cancelar, mas a carga continuou mostrando "em andamento" pra sempre.
--
-- Causa raiz (investigada direto no Render via MCP, ver conversa sobre
-- hospedagem): o serviço está no plano Free (512 MB) e vem sofrendo OOM
-- kills com frequência -- cada OOM (ou um redeploy, ou um restart manual)
-- troca o CONTAINER inteiro. Quando isso acontece NO MEIO de uma carga, a
-- thread Python que estava rodando ela simplesmente desaparece -- ninguém
-- sobra pra marcar a linha como 'erro'/'cancelado' em
-- cargas_comparacao_folha, e ela fica "em_andamento" pra sempre, com um
-- `pid` que não existe mais em NENHUM processo vivo. O botão Cancelar
-- (POST /comparacao-folha/carga/<id>/cancelar) tentava os.kill(pid,
-- SIGTERM) nesse PID órfão, recebia ProcessLookupError (silenciosamente
-- ignorado, pensado originalmente só pro caso raro "a carga terminou
-- sozinha entre eu ler o PID e tentar matar") e devolvia {"ok": true} sem
-- ter corrigido nada de verdade -- dando a falsa impressão de que o
-- cancelamento funcionou.
--
-- `instancia_id` grava RENDER_INSTANCE_ID (variável de ambiente que o
-- Render injeta, diferente a cada boot de container -- confirmado nos
-- logs: um OOM ou um novo deploy sempre troca esse valor) no mesmo
-- momento em que `pid` é gravado (ver
-- app/main.py:_ao_iniciar_processo_carga_comparacao_folha). Com isso dá
-- pra saber, com certeza, se o PID gravado pertence a um boot do
-- container que já não existe mais -- e nesse caso nem precisa tentar
-- os.kill (que, além de inútil, corre o risco remoto mas real de matar
-- um processo ATUAL só por coincidência de reaproveitar o mesmo número
-- de PID de um boot anterior) -- a carga é marcada como erro na hora, por
-- quem quer que descubra o problema primeiro: o próprio botão Cancelar, o
-- polling de GET /comparacao-folha/carga-atual (a cada 3s, então o banner
-- se autocorrige sozinho mesmo sem ninguém clicar em nada) ou a tentativa
-- de iniciar uma carga nova (ver _criar_carga_comparacao_folha -- antes
-- disto, uma carga órfã bloqueava QUALQUER carga nova pra sempre, por
-- causa do índice único idx_cargas_comparacao_folha_ativa).
--
-- NULL em ambientes sem RENDER_INSTANCE_ID (ex: docker-compose local) --
-- nesse caso a detecção de órfã fica desligada (mantém o comportamento
-- antigo, só baseado no PID), nunca classifica errado por falta do dado.

ALTER TABLE cargas_comparacao_folha
  ADD COLUMN instancia_id text;

COMMENT ON COLUMN cargas_comparacao_folha.instancia_id IS '169ª rodada -- RENDER_INSTANCE_ID do container que abriu o processo psql desta carga (gravado junto com `pid`, ver _ao_iniciar_processo_carga_comparacao_folha). Usado por _carga_esta_orfa() pra saber com certeza se a carga pertence a um boot do container que já não existe mais (OOM, redeploy, restart) -- nesse caso ela nunca vai se auto-corrigir sozinha e precisa ser marcada como erro na mão. NULL em ambientes sem essa variável (ex: local) -- a detecção de órfã fica desligada, nunca classifica errado.';
