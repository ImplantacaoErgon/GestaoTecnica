-- 53ª rodada: catálogo de Rubricas (regras de negócio da folha de pagamento),
-- a partir da planilha de levantamento "Regras de negócio" usada pelo cliente
-- (aba "Levantamento Rubricas") — mesmo padrão de requisitos_tr/itens_migracao
-- (uma linha por item do levantamento, com status + workflow de datas), agora
-- para o levantamento e a homologação de cada rubrica da folha.
--
-- Por que a planilha original tem código ERGON e "Verba" do legado REPETIDOS
-- em várias linhas: a mesma regra de negócio pode ser levantada e homologada
-- separadamente em EMPRESAS diferentes (o cliente tem várias empresas/
-- autarquias na mesma implantação — E01, E03, E04, E06, E07, E08, E13, E16,
-- E18, E23, E24), cada uma com seu próprio código interno e seu próprio ciclo
-- de homologação. Por isso NENHUM campo de código (nem código ERGON, nem
-- verba do legado) é usado como chave única aqui — cada linha da planilha diz
-- respeito a "esta rubrica, nesta(s) empresa(s)", e pode conviver com outra
-- linha do mesmo código ERGON para outra empresa. A chave de idempotência de
-- reimportação é a posição da linha na planilha (linha_planilha).
--
-- incidencias e empresas viram JSONB (não uma coluna por incidência/empresa)
-- de propósito: são ~17 campos de incidência e ~11 empresas com valores em
-- texto livre (ex: "SIM - TETO MUNICIPAL", não booleano puro) — colunas fixas
-- deixariam a tabela com mais de 70 colunas e qualquer nova incidência exigiria
-- outra migração. Ficam pesquisáveis via operadores jsonb do Postgres
-- (->>'ir', @>, etc) quando precisar filtrar por uma incidência específica.

CREATE TYPE status_rubrica_enum AS ENUM (
  'Em levantamento', 'Enviada à Techne', 'Liberada para testes',
  'Em homologação', 'Homologada', 'Excluída'
);

CREATE TABLE rubricas (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id            uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  atividade_id          uuid REFERENCES atividades(id),
  linha_planilha        int,               -- linha da planilha de origem na importação (chave de reimportação, não de negócio)

  -- identificação / mapeamento legado -> Ergon
  analista              text,              -- quem levantou (coluna "ANÁLISE")
  verba_legado          text,              -- "Verba 07" do sistema legado
  descricao_legado      text,              -- "DESCRIÇÃO E07"
  codigo_ergon          text,              -- pode repetir entre linhas de empresas diferentes — ver comentário da tabela
  nome_abreviado        text,              -- até 30 caracteres no Ergon
  nome_extenso          text,              -- até 60 caracteres no Ergon
  tipo                  text,              -- P (provento) / D (desconto) / P-D

  -- regra de cálculo
  grupo_calculo         text,
  ordem_grupo_calculo   int,
  legislacao            text,
  valor_formula         text,              -- "R$ / %" — valor ou fórmula (texto livre, igual à planilha)
  quant_autorizado      text,
  fato_origem           text,
  validacao_compatibilidade   text,
  validacao_incompatibilidade text,
  regime_vinculo_direito      text,        -- regime jurídico / tipo de vínculo que tem direito
  categoria_cargo_direito     text,        -- categoria/subcategoria ou cargo(s) que tem direito
  secretaria            text,
  outras_condicoes      text,
  incompatibilidade     text,
  periodicidade         text,
  totalizacao           text,
  regra_negocio         text,              -- texto livre com a regra de negócio completa

  -- por empresa: {"E01": {"aplica": true, "codigo": "1 - SALARIO / TIPO: P"}, "E16": {...}, ...}
  empresas              jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- incidências: {"ir": "SIM", "rgps": "SIM", "decimo_terceiro": "VALOR DE DEZEMBRO", "incide_excedente": "SIM - TETO MUNICIPAL", ...}
  incidencias           jsonb NOT NULL DEFAULT '{}'::jsonb,

  -- workflow de levantamento -> Techne -> testes -> homologação (datado, como na planilha)
  status                status_rubrica_enum NOT NULL DEFAULT 'Em levantamento',
  situacao_planilha     text,              -- valor bruto da coluna "EP" da planilha (IMPLEMENTADA/VALIDADA/EM REVISÃO/SIM/NÃO) — mantido de referência, não normatizado
  data_levantamento     date,
  revisado_por          text,
  questionamentos_juridico text,
  data_envio_techne     date,
  consultor_techne      text,
  data_liberacao_testes date,
  observacoes_liberacao text,
  condicoes_minimas_testes text,
  questionamentos_consultor text,
  responsavel_homologacao   text,
  data_inicio_homologacao   date,
  data_homologacao      date,
  observacoes_homologacao   text,
  observacoes_finais    text,
  -- Algumas células de data na planilha original trazem mais de uma data (histórico de
  -- revisão digitado à mão, ex: "22/01/2026 - Revisão\n(28/11/2025)") ou erro de
  -- digitação que não dá pra converter em data (ex: "30/03/0206", "27/112025"). Nesses
  -- casos o importador (app/rubricas_import.py) tenta extrair a data mais plausível pro
  -- campo de data de verdade, mas NUNCA descarta o texto original — ele cai aqui, pra
  -- revisão humana na tela.
  notas_importacao      text,

  criado_em             timestamptz NOT NULL DEFAULT now(),
  atualizado_em         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_rubricas_projeto ON rubricas(projeto_id);
CREATE INDEX idx_rubricas_status ON rubricas(status);
CREATE INDEX idx_rubricas_codigo_ergon ON rubricas(projeto_id, codigo_ergon);
CREATE UNIQUE INDEX rubricas_linha_planilha_key ON rubricas(projeto_id, linha_planilha) WHERE linha_planilha IS NOT NULL;
CREATE TRIGGER trg_rubricas_atualizado_em
  BEFORE UPDATE ON rubricas
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
COMMENT ON TABLE rubricas IS 'Levantamento e homologação das rubricas (regras de negócio) da folha de pagamento — uma linha por rubrica levantada, podendo repetir código ERGON/verba do legado quando a mesma regra é levantada/homologada em empresas diferentes (ver campo empresas). Importado a partir da planilha "Levantamento Rubricas" do cliente (app/rubricas_import.py); linha_planilha é a chave usada para reimportar sem duplicar.';
