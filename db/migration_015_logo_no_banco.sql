-- ============================================================================
-- Migração 015 — Logo gravado no banco, não mais em disco
-- ============================================================================
-- Corrige um bug relatado: o logo (Configurações > Parâmetros) aparecia
-- certinho logo após o upload, mas "sumia" (ícone de imagem quebrada) depois
-- de um tempo, sempre que o backend reiniciava.
--
-- Causa: o arquivo do logo era salvo em backend/uploads/, um diretório em
-- disco LOCAL do container (ver upload_logo_parametros() em
-- backend/app/main.py, versão anterior). Isso funciona em desenvolvimento
-- local (docker-compose, com um volume "uploads" persistente — ver
-- docker-compose.yml), mas o sistema roda em produção no Render como Web
-- Service com Docker (sem docker-compose, sem esse volume — ver seção
-- "Publicando em produção (Render)" do README): o disco daquele container é
-- apagado a cada redeploy e a cada vez que o Render reinicia a instância, e
-- reconstruído do zero a partir da imagem Docker (que nem inclui
-- backend/uploads/ — ver .dockerignore). Resultado: o logo enviado
-- desaparece assim que isso acontece, mesmo com o nome do arquivo continuando
-- gravado em parametros_site.logo_arquivo.
--
-- Solução: gravar o conteúdo da imagem em base64 direto no banco (mesmo
-- princípio já usado para o conteúdo da Documentação — migração 014), que
-- não tem esse problema de persistência. Sem isso, é puramente aditivo:
-- não apaga nem exige nada de quem já tinha um logo cadastrado (que,
-- estruturalmente, já estava perdido depois do próximo redeploy) — só é
-- necessário reenviar o logo uma vez, depois desta migração + do deploy do
-- código novo, para que ele passe a ficar de fato salvo.
--
-- Seguro rodar a qualquer momento: só adiciona colunas novas (nenhuma
-- existente é alterada ou removida).

BEGIN;

ALTER TABLE parametros_site
  ADD COLUMN IF NOT EXISTS logo_dados text,
  ADD COLUMN IF NOT EXISTS logo_mime  text;

COMMENT ON COLUMN parametros_site.logo_arquivo IS
  'Nome do arquivo original enviado (só para saber a extensão e para o '
  '"?f=" de cache-busting da URL do logo no front-end) — o CONTEÚDO da '
  'imagem não fica em disco, ver comentário em logo_dados. NULL enquanto '
  'nenhum logo foi enviado.';
COMMENT ON COLUMN parametros_site.logo_dados IS
  'Conteúdo binário da imagem do logo, em base64, gravado direto no banco '
  '(migração 015 — antes ficava salvo em backend/uploads/, um diretório em '
  'disco local do container; em deploys como o Render, sem disco '
  'persistente, esse diretório é apagado a cada redeploy/reinício, então o '
  'logo "sumia" pouco depois de enviado). Servido por '
  'GET /api/parametros/<id>/logo. NULL enquanto nenhum logo foi enviado.';
COMMENT ON COLUMN parametros_site.logo_mime IS
  'Content-Type da imagem do logo (ex: image/png), usado ao servir '
  'GET /api/parametros/<id>/logo. NULL enquanto nenhum logo foi enviado.';

COMMIT;
