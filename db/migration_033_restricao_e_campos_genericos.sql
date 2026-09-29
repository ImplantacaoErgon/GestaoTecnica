-- 74ª rodada — Tipo de Restrição (padrão MS Project/ProjectLibre) e 15
-- campos genéricos (5 numéricos, 5 booleanos, 5 de texto) na atividade.
--
-- Pedido do usuário verbatim: "NA atividade em nosso cronograma crie um
-- Tipo de Restrição com o padrão da imagem. Crie 5 campos Numéricos, 5
-- campos booleanos e 5 campos de texto para poder usar de forma genérica.
-- Esses campos podem ser usados diretamente no cronograma" — a imagem
-- mostrava o combo de "Tipo de restrição" do MS Project/ProjectLibre (As
-- Soon As Possible / As Late As Possible / Must Start On / Must Finish On
-- / Start No Earlier Than / Start No Later Than / Finish No Earlier Than /
-- Finish No Later Than, aqui em PT-BR) com "Data da restrição" ao lado,
-- mostrando "ND" (não definida) quando o tipo é um dos dois que não usam
-- data (O Mais Breve Possível / O mais tarde possível).
--
-- `tipo_restricao` guarda o RÓTULO em português diretamente (mesmo padrão
-- já usado por `status`/`prioridade` neste sistema — texto livre validado
-- na aplicação, sem CHECK constraint no banco; ver TIPOS_RESTRICAO_VALIDOS
-- em app/main.py e app/cronograma_edicao_lote.py). `data_restricao` fica
-- NULL para os dois tipos "...possível" (a regra de sempre zerar a data
-- quando o tipo muda pra um desses é aplicada na aplicação, não aqui).
--
-- Os 15 campos genéricos (numero_1..5, booleano_1..5, texto_1..5) não têm
-- significado nenhum pré-definido — servem pra necessidades que só
-- aparecem depois de o projeto estar rodando (ex: um campo de controle
-- específico do cliente), sem precisar de outra migração pra cada uma.
-- Mesmo espírito dos campos "Number1..20"/"Flag1..20"/"Text1..30" do
-- Microsoft Project.

ALTER TABLE atividades
  ADD COLUMN tipo_restricao text NOT NULL DEFAULT 'O Mais Breve Possível',
  ADD COLUMN data_restricao date,
  ADD COLUMN numero_1 numeric,
  ADD COLUMN numero_2 numeric,
  ADD COLUMN numero_3 numeric,
  ADD COLUMN numero_4 numeric,
  ADD COLUMN numero_5 numeric,
  ADD COLUMN booleano_1 boolean NOT NULL DEFAULT false,
  ADD COLUMN booleano_2 boolean NOT NULL DEFAULT false,
  ADD COLUMN booleano_3 boolean NOT NULL DEFAULT false,
  ADD COLUMN booleano_4 boolean NOT NULL DEFAULT false,
  ADD COLUMN booleano_5 boolean NOT NULL DEFAULT false,
  ADD COLUMN texto_1 text,
  ADD COLUMN texto_2 text,
  ADD COLUMN texto_3 text,
  ADD COLUMN texto_4 text,
  ADD COLUMN texto_5 text;

COMMENT ON COLUMN atividades.tipo_restricao IS 'Tipo de restrição de agendamento (padrão MS Project/ProjectLibre) -- um de: O Mais Breve Possível, O mais tarde possível, Deve iniciar em, Deve terminar em, Não iniciar antes de, Não iniciar depois de, Não terminar antes de, Não terminar depois de. Ver TIPOS_RESTRICAO_VALIDOS/TIPOS_RESTRICAO_SEM_DATA em app/main.py.';
COMMENT ON COLUMN atividades.data_restricao IS 'Data associada ao tipo_restricao -- sempre NULL ("ND" na tela) quando tipo_restricao é "O Mais Breve Possível" ou "O mais tarde possível".';
COMMENT ON COLUMN atividades.numero_1 IS 'Campo numérico genérico 1/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.numero_2 IS 'Campo numérico genérico 2/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.numero_3 IS 'Campo numérico genérico 3/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.numero_4 IS 'Campo numérico genérico 4/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.numero_5 IS 'Campo numérico genérico 5/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.booleano_1 IS 'Campo booleano genérico 1/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.booleano_2 IS 'Campo booleano genérico 2/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.booleano_3 IS 'Campo booleano genérico 3/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.booleano_4 IS 'Campo booleano genérico 4/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.booleano_5 IS 'Campo booleano genérico 5/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.texto_1 IS 'Campo de texto genérico 1/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.texto_2 IS 'Campo de texto genérico 2/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.texto_3 IS 'Campo de texto genérico 3/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.texto_4 IS 'Campo de texto genérico 4/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
COMMENT ON COLUMN atividades.texto_5 IS 'Campo de texto genérico 5/5 -- sem significado pré-definido, uso livre por projeto (74ª rodada).';
