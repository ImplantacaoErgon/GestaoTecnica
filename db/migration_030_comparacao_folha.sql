-- 55ª rodada: Comparação Folha — resultado já calculado da comparação entre o
-- valor de cada rubrica no Ergon e na ficha financeira do sistema legado, um
-- por servidor/vínculo/rubrica, importado da segunda planilha do cliente
-- (aba única, ~300 mil linhas por arquivo — um arquivo por competência
-- fechada). Ver app/comparacao_folha_import.py.
--
-- Diferente de rubricas: aqui a comparação já vem PRONTA da planilha (a
-- classificação por linha — SITUACAO/TIPO_COMPARACAO — e a diferença já
-- calculada), então esta tabela é só um espelho consultável dos dados: sem
-- workflow de edição manual por linha (confirmado com o usuário — a tela é só
-- consulta/filtro/exportação). Por isso as ~65 colunas da planilha viram
-- colunas diretas (sem parte em jsonb) e a maioria fica como TEXT mesmo
-- quando parece um código numérico (ex: matrícula pode ter zero à esquerda e
-- espaço, como "01 0000014192") — só datas e os 4 campos de valor/percentual
-- viram tipo de verdade, pra poder somar/ordenar/filtrar por período.
--
-- Reimportação (confirmado com o usuário): cada arquivo = uma competência
-- (MESANO) fechada, e reimportar o mesmo mês SUBSTITUI as linhas antigas
-- daquele mês (delete-then-insert), não acumula rodadas — ver
-- comparacao_folha_import.substituir_comparacao_mes(). Por isso o índice
-- importante aqui é (projeto_id, mesano), não uma chave única por linha (não
-- existe uma — a granularidade de idempotência é o mês inteiro, não a linha).

CREATE TABLE comparacao_folha (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id             uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,

  -- classificação da comparação (o "resultado" da linha, já vem pronto da planilha)
  tipo_comparacao        text,
  situacao               text,

  -- identificação do servidor/vínculo
  matricula              text,
  prontuario             text,
  empresa_consist        text,
  categoria_consist      text,
  emp_codigo             text,
  nome                   text,
  cpf                    text,
  numfunc                text,
  numvinc                text,
  numpens                text,
  dtexerc                date,
  dtaposent              date,
  tipoapos               text,
  dtvac                  date,
  modalidade             text,
  tipopens               text,
  percentual             numeric,
  tipovinc               text,
  regimejur              text,
  categoria              text,
  numvincant             text,
  numvincpos             text,

  -- lotação / cargo / função
  orgao                  text,
  setorfunc              text,
  cargo                  text,
  nome_cargo             text,
  referencia             text,
  tabvenccargo           text,
  funcao                 text,
  nome_funcao            text,
  referencia_desig       text,
  tabvencfuncao          text,
  grupo                  text,

  -- competência / folha
  mesano                 date,
  folha                  text,
  convenio               text,

  -- lado legado (CONSIST / ficha financeira)
  verba_consist          text,
  nomeabrev_consist      text,
  valor_consist          numeric,
  conta_consist          text,

  -- lado Ergon
  rubrica_ergon          text,
  nome_abrev             text,
  rubrica_nome_ergon     text,
  valor_ergon            numeric,
  conta_ergon            text,

  -- comparação
  dif_consist_ergon      numeric,
  vantagem               text,
  obs_evento             text,
  obs_atributo           text,
  tiporubr               text,
  class_rubrica          text,
  data_convergencia      date,
  fato_origem            text,
  complemento            text,
  cod_calc               text,
  cod_lotacao_1          text,
  tipoarq                text,
  jornada                text,
  afastamento            text,
  dtini_afast            date,
  dtfim_afast            date,
  obs                    text,
  data_hora_convergencia timestamptz,

  criado_em              timestamptz NOT NULL DEFAULT now()
);

-- (projeto_id, mesano): usado tanto pro DELETE de substituição na
-- reimportação quanto pro filtro por competência na tela — é o par mais
-- consultado, de longe.
CREATE INDEX idx_comparacao_folha_projeto_mesano ON comparacao_folha(projeto_id, mesano);
CREATE INDEX idx_comparacao_folha_situacao ON comparacao_folha(projeto_id, situacao);
CREATE INDEX idx_comparacao_folha_matricula ON comparacao_folha(projeto_id, matricula);
CREATE INDEX idx_comparacao_folha_rubrica_ergon ON comparacao_folha(projeto_id, rubrica_ergon);

COMMENT ON TABLE comparacao_folha IS 'Comparação (já calculada na planilha do cliente) entre o valor de cada rubrica no Ergon e na ficha financeira do sistema legado, uma linha por servidor/vínculo/rubrica/competência. Importado via app/comparacao_folha_import.py (busca automática no Google Drive — ~300 mil linhas por arquivo/mês). Reimportar o mesmo mês substitui as linhas antigas daquele mês (ver comparacao_folha_import.substituir_comparacao_mes) — não é upsert por linha, não existe chave única de linha.';
