-- 116ª rodada: "Dentro de Migração de dados crie uma funcionalidade para
-- carga dos registros rejeitados da migração a partir de planilhas csv ou
-- xlsx" (pedido do usuário, verbatim) — carga, análise e histórico das
-- planilhas de rejeição que o Ergon devolve em cada ciclo de migração.
-- Primeiro recorte: Eventos de Cargos, com duas fases por ciclo:
--
--   Fase 2 (De-Para)     -- arquivo "LISTA_P_REJ_DP_*"  -- rejeições do
--                           de-para (cargo/função/referência/jornada) antes
--                           da carga final.
--   Fase 3 (Carga final) -- arquivo "LISTA_P_REJ_ERG_*" -- rejeições de
--                           verdade na carga no Ergon, já com código de erro
--                           ERG-NNNNN.
--
-- O usuário confirmou (AskUserQuestion desta rodada): uma página só, com
-- filtro de Fase; vincular a carga a um ciclo de ciclos_migracao (o mesmo
-- conceito já usado em Migração de Dados — não criamos um "ciclo" paralelo
-- aqui) e PREENCHER "Rejeições" sozinho a partir do total importado; os
-- textos de "ação sugerida" eu monto (editável depois); reimportar o
-- mesmo (item, ciclo, fase) LIMPA e carrega de novo (não duplica).
--
-- "Guardar histórico dessas cargas" (pedido verbatim) é atendido por
-- migracao_rejeicoes_cargas nunca ser esvaziada como um todo — cada upload
-- bem-sucedido fica registrado ali (mesmo padrão de
-- cargas_comparacao_folha/rubricas_auto_update_execucoes, migrações
-- 034/037); o "limpa e carrega de novo" (pedido verbatim) é só por
-- combinação (item_migracao_id, ciclo_migracao_id, fase): o backend
-- (app/migracao_rejeicoes_import.py) apaga a carga antiga DESSA combinação
-- (cascata apaga as linhas de detalhe) antes de inserir a nova — o
-- histórico de OUTRAS combinações (outro ciclo, outra fase) continua
-- intacto.

-- Cabeçalho de cada upload -- uma linha por tentativa bem-sucedida de
-- importação. UNIQUE(item_migracao_id, ciclo_migracao_id, fase) não é pra
-- impedir reimportar (o backend apaga a linha antiga antes de inserir a
-- nova, ver comentário acima) -- é uma garantia a mais contra duplicidade
-- se algum dia o delete-then-insert falhar pela metade.
CREATE TABLE migracao_rejeicoes_cargas (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id          uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  item_migracao_id    uuid NOT NULL REFERENCES itens_migracao(id) ON DELETE CASCADE,
  ciclo_migracao_id   uuid NOT NULL REFERENCES ciclos_migracao(id) ON DELETE CASCADE,
  fase                text NOT NULL CHECK (fase IN ('fase2_depara', 'fase3_carga_final')),
  nome_arquivo        text,
  total_linhas        integer NOT NULL DEFAULT 0,
  total_classificadas integer NOT NULL DEFAULT 0,
  iniciado_por        uuid REFERENCES usuarios(id),
  iniciado_por_nome   text,
  criado_em           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (item_migracao_id, ciclo_migracao_id, fase)
);
CREATE INDEX idx_migracao_rejeicoes_cargas_projeto ON migracao_rejeicoes_cargas(projeto_id, criado_em DESC);
COMMENT ON TABLE migracao_rejeicoes_cargas IS '116ª rodada: histórico de cargas da planilha de rejeitados da migração (Fase 2/De-Para ou Fase 3/Carga final), uma linha por upload bem-sucedido -- ver app/migracao_rejeicoes_import.py.';

-- Uma linha de detalhe por registro rejeitado. `dados` guarda a linha
-- original inteira (todas as colunas da planilha, nome->valor) em JSONB --
-- mesma lógica já usada em rubricas.empresas/incidencias (migração 029):
-- os dois formatos (DP com 29 colunas, ERG com 36) têm conjuntos de colunas
-- bem diferentes entre si e é só pra exibição no detalhe (nenhuma coluna
-- aqui precisa ser filtrada/agregada em SQL), então não vale a pena criar
-- ~40 colunas fixas que mudam a cada tabela legado nova que este recurso
-- vier a cobrir.
CREATE TABLE migracao_rejeicoes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  carga_id        uuid NOT NULL REFERENCES migracao_rejeicoes_cargas(id) ON DELETE CASCADE,
  linha_planilha  integer,
  tipo_erro_chave text NOT NULL,
  msg_erro        text,
  identificador   text,
  dados           jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_em       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_migracao_rejeicoes_carga ON migracao_rejeicoes(carga_id);
CREATE INDEX idx_migracao_rejeicoes_tipo ON migracao_rejeicoes(carga_id, tipo_erro_chave);
COMMENT ON TABLE migracao_rejeicoes IS '116ª rodada: uma linha por registro rejeitado de uma carga (migracao_rejeicoes_cargas), já classificado em tipo_erro_chave -- ver app/migracao_rejeicoes_import.py.';

-- Catálogo de "ação sugerida" por tipo de erro -- o usuário pediu pra
-- promover sugestões de ação pra cada tipo de rejeição; os textos abaixo
-- (seed) foram montados a partir da análise de verdade das duas planilhas
-- reais anexadas nesta rodada (12 códigos ERG-NNNNN distintos na Fase 3,
-- 7 padrões de mensagem na Fase 2 -- nenhum "_OUTRO" sobrou nas 2053+6354
-- linhas reais analisadas). Editável depois pela tela (pedido verbatim:
-- "Eu já monto o texto, editável depois") -- `acao_sugerida` é o único
-- campo que a tela deixa editar; `descricao_erro` é só contexto fixo.
-- `_OUTRO` (um por fase) é o fallback pra qualquer MSG_ERRO que não bater
-- com nenhum padrão conhecido -- novas variações aparecem com o tempo
-- (novos códigos ERG, nova mensagem do De-Para), e a tela não pode travar
-- a importação só porque uma mensagem é desconhecida.
CREATE TABLE migracao_rejeicoes_acoes_sugeridas (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fase            text NOT NULL CHECK (fase IN ('fase2_depara', 'fase3_carga_final')),
  tipo_erro_chave text NOT NULL,
  descricao_erro  text,
  acao_sugerida   text,
  atualizado_em   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (fase, tipo_erro_chave)
);
COMMENT ON TABLE migracao_rejeicoes_acoes_sugeridas IS '116ª rodada: catálogo editável de "ação sugerida" por tipo de erro de rejeição -- acao_sugerida é editável pela tela, o resto é fixo. Ver app/migracao_rejeicoes.py.';

INSERT INTO migracao_rejeicoes_acoes_sugeridas (fase, tipo_erro_chave, descricao_erro, acao_sugerida) VALUES
('fase3_carga_final', 'ERG-03923', 'Jornada não cadastrada para o cargo no período informado.',
 'Cadastrar a jornada indicada na mensagem de erro para o cargo apontado, cobrindo o período de vigência do evento, e reprocessar.'),
('fase3_carga_final', 'ERG-00012', 'Setor informado é inválido (não cadastrado ou fora de vigência) no período da carga.',
 'Verificar o cadastro do setor no Ergon para a empresa e período indicados (criar o setor ou corrigir a vigência) e reprocessar.'),
('fase3_carga_final', 'ERG-00101', 'Referência salarial fora dos limites permitidos pelo cargo a partir da data de provimento.',
 'Revisar a tabela de referências do cargo no Ergon (limites mínimo/máximo) ou corrigir a referência de origem no legado, e reprocessar.'),
('fase3_carga_final', 'ERG-03904', 'Período do evento é concomitante com outro evento incompatível do mesmo funcionário.',
 'Analisar os eventos concomitantes do funcionário/matrícula indicados e ajustar as datas de início/fim de um dos dois antes de reprocessar.'),
('fase3_carga_final', 'ERG-00135', 'Mudança de cargo sem o evento/relacionamento que a descreve (ex: tipo de evento de provimento).',
 'Incluir no legado o evento que descreve a mudança de cargo (nomeação, promoção, etc.) antes do evento rejeitado, e reprocessar.'),
('fase3_carga_final', 'ERG-03921', 'Tipo de evento não é permitido para funcionário falecido ou desligado.',
 'Confirmar a situação do vínculo (falecido/desligado) no legado; se estiver correta, remover ou corrigir o evento de origem, pois ele não se aplica.'),
('fase3_carga_final', 'ERG-00110', 'Cargo exige escolaridade mínima que o funcionário não possui cadastrada.',
 'Cadastrar/atualizar a escolaridade do funcionário no Ergon (ou no legado, se a carga de escolaridade também vier de lá) e reprocessar.'),
('fase3_carga_final', 'ERG-00111', 'Espécie do evento não é válida para a categoria/subcategoria do funcionário.',
 'Revisar a parametrização de Espécies de Evento x Categoria/Subcategoria no Ergon, ou corrigir a espécie informada no legado, e reprocessar.'),
('fase3_carga_final', 'ERG-10198', 'Evento de designação em período concomitante a uma cessão.',
 'Ajustar as datas do evento de designação ou da cessão do funcionário para eliminar a concomitância, e reprocessar.'),
('fase3_carga_final', 'ERG-00120', 'Evento de natureza provimento antes da data de vacância do cargo.',
 'Corrigir a data de início do evento de provimento para depois da data de vacância indicada, e reprocessar.'),
('fase3_carga_final', 'ERG-01209', 'Referência salarial fora dos limites permitidos pelo cargo (regra de referência).',
 'Revisar a tabela de referências do cargo no Ergon ou a referência informada no legado, e reprocessar.'),
('fase3_carga_final', 'ERG-03909', 'Tipo de evento incompatível com algum atributo válido do funcionário no período.',
 'Revisar os atributos cadastrados para o funcionário/tipo de evento no período indicado e corrigir a incompatibilidade, e reprocessar.'),
('fase3_carga_final', '_OUTRO', 'Código de erro ERG não reconhecido pelo catálogo (mensagem nova ou código ainda não visto).',
 'Analisar a mensagem completa do erro (código ERG-NNNNN) e, se for um padrão recorrente, cadastrar uma ação sugerida específica para ele.'),

('fase2_depara', 'DP_VINCULO_NAO_CARREGADO', 'O vínculo dessa matrícula ainda não foi carregado no Ergon -- o de-para depende do vínculo já existir.',
 'Confirmar se a carga de vínculos/cadastro dessa matrícula já rodou; se não, aguardar/reexecutar essa carga antes de repetir o de-para.'),
('fase2_depara', 'DP_CARGO_NAO_LOCALIZADO', 'DE_PARA_CARGO não localizado e o código do cargo do legado não tem cargo alvo válido/parametrizado no Ergon.',
 'Cadastrar o de-para de cargo para a combinação empresa/cargo/referência indicada (planilha de De-Para de Cargo) e reprocessar.'),
('fase2_depara', 'DP_FUNCAO_NAO_LOCALIZADA', 'Função não localizada no DE-PARA-FUNCAO -- comum em cargos em comissão.',
 'Analisar a Planilha de Cargos em Comissão e cadastrar o de-para da função SIGEP indicada para a empresa, e reprocessar.'),
('fase2_depara', 'DP_REFERENCIA_NAO_LOCALIZADA', 'Referência do legado não encontrada na regra de De-Para.',
 'Cadastrar a referência do legado indicada na regra de De-Para de Referência para a empresa apontada, e reprocessar.'),
('fase2_depara', 'DP_TIPO_EVENTO_NAO_IDENTIFICADO', 'Tipo de evento não identificado para o cargo/empresa indicados.',
 'Verificar a parametrização de Tipos de Evento para a empresa e cargo indicados no Ergon e completar o cadastro, e reprocessar.'),
('fase2_depara', 'DP_TIPO_CARGO_NAO_IDENTIFICADO', 'Tipo de cargo não identificado para o cargo do Ergon indicado.',
 'Completar a classificação de Tipo de Cargo para o código do Ergon indicado e reprocessar.'),
('fase2_depara', 'DP_JORNADA_NAO_LOCALIZADA', 'Jornada do legado não encontrada na regra de De-Para.',
 'Cadastrar o de-para da jornada do legado indicada (Tipos de Frequências/Jornada) e reprocessar.'),
('fase2_depara', '_OUTRO', 'Mensagem de rejeição da Fase 2 não reconhecida pelo catálogo (mensagem nova, ainda não vista).',
 'Analisar a mensagem completa do erro e, se for um padrão recorrente, cadastrar uma ação sugerida específica para ele.');

-- Resolve o conflito com a regra existente de create_ciclo/update_ciclo
-- (Rejeitados = Extraídos - Carregados, sempre recalculado -- ver
-- app/main.py) -- quando este recurso importa o detalhe de rejeições de
-- uma planilha real pra um ciclo, qtd_rejeicoes passa a ser PREENCHIDO
-- SOZINHO a partir desse detalhe (pedido verbatim), e esse valor não pode
-- ser silenciosamente sobrescrito a próxima vez que o usuário editar
-- extraídos/carregados desse ciclo pela tela manual já existente
-- ("Ciclos de execução"). Este flag marca que qtd_rejeicoes deste ciclo
-- tem origem na importação detalhada (migracao_rejeicoes), não na conta
-- extraídos-carregados -- ver create_ciclo/update_ciclo em app/main.py.
ALTER TABLE ciclos_migracao ADD COLUMN qtd_rejeicoes_detalhada boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN ciclos_migracao.qtd_rejeicoes_detalhada IS '116ª rodada: true quando qtd_rejeicoes deste ciclo foi preenchido a partir da carga detalhada de rejeições (migracao_rejeicoes_cargas), não da conta extraídos-carregados -- nesse caso create_ciclo/update_ciclo não recalculam/sobrescrevem qtd_rejeicoes.';
