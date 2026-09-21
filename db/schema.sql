-- ============================================================================
-- Gestão de Projetos — banco de dados de gestão técnica da implantação
-- PostgreSQL 14+
--
-- Organização geral:
--   projetos > etapas > frentes_trabalho > atividades (WBS hierárquica)
--   atividades <-> atividade_dependencia   (grafo para caminho crítico / CPM)
--   atividades <-> recursos                (recursos, N:N)
--   atividades <-> requisitos_tr           (rastreabilidade com o Termo de Referência)
--   atividades -> ciclos_migracao          (execuções semanais da migração de dados)
--   atividades / requisitos_tr / riscos -> anexos (arquivos: xls, csv, txt, pdf, ...)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ----------------------------------------------------------------------------
-- Tipos enumerados (vocabulário fechado e estável)
-- ----------------------------------------------------------------------------
CREATE TYPE prioridade_enum AS ENUM ('Urgente','Alta','Média','Baixa');

-- As quatro variantes de conclusão ('Concluída' e as três seguintes) descrevem
-- o MESMO evento (atividade pronta: 100% + Fim Real preenchido) — a diferença
-- é só a classificação automática de prazo x esforço, calculada e gravada
-- pela aplicação na hora de salvar (main.py/classificar_conclusao, 29ª
-- rodada/migração 020). 'Concluída' sozinha = terminou dentro do prazo E
-- dentro do esforço previsto (ou não há dados suficientes pra dizer outra
-- coisa — ex: atividade sem prazo_horas cadastrado).
CREATE TYPE status_atividade_enum AS ENUM (
  'Não iniciada','Em andamento','Bloqueada',
  'Concluída','Concluída com atraso','Concluída com esforço maior','Concluída com atraso e esforço maior',
  'Cancelada'
);

CREATE TYPE tipo_dependencia_enum AS ENUM ('FS','SS','FF','SF');
-- FS = Fim-Início (padrão), SS = Início-Início, FF = Fim-Fim, SF = Início-Fim

CREATE TYPE classificacao_tr_enum AS ENUM (
  'Essencial/Imediato','Obrigatório','Desejável',
  'Customizado Curto','Customizado Médio','Customizado Longo'
);

CREATE TYPE atendimento_tr_enum AS ENUM ('A analisar','Nativo','Parcial','Customizado','Não atende');

CREATE TYPE status_requisito_enum AS ENUM (
  'Não iniciado','Em levantamento','Especificado','Em desenvolvimento',
  'Em homologação','Entregue','Aprovado','Rejeitado'
);

CREATE TYPE cobranca_enum AS ENUM ('Sim','Não','N/A');

CREATE TYPE tipo_requisito_enum AS ENUM ('Funcional','Não Funcional');

CREATE TYPE tipo_vinculo_enum AS ENUM ('Consultoria','Cliente','Terceirizado');

CREATE TYPE nivel_risco_enum AS ENUM ('Baixo','Médio','Alto');

CREATE TYPE status_risco_enum AS ENUM ('Aberto','Mitigado','Encerrado');

CREATE TYPE entidade_anexo_enum AS ENUM ('atividade','requisito','risco','marco','projeto');

-- ----------------------------------------------------------------------------
-- Função utilitária: atualizar coluna atualizado_em automaticamente
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_atualizado_em() RETURNS trigger AS $$
BEGIN
  NEW.atualizado_em = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 1. PROJETOS
-- ============================================================================
CREATE TABLE projetos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sigla           text,                          -- sigla curta do projeto, ex: "SISTEMA-2026"
  nome            text NOT NULL,
  cliente         text NOT NULL,                 -- Instituição/órgão cliente
  descricao       text,
  -- Os 5 campos abaixo guardam sempre o NOME do interlocutor (retrato
  -- congelado) — cada um tem um *_recurso_id irmão (migração 025, FK pra
  -- recursos, fechada mais abaixo assim que a tabela recursos existe:
  -- mesmo motivo de atividades.requisito_tr_id) que, quando preenchido,
  -- é a fonte de verdade: o backend sobrescreve o nome com o cadastro do
  -- recurso a cada salvamento (preparar_projeto_interlocutores, em
  -- main.py). Sem *_recurso_id (pessoa ainda sem cadastro de recurso), o
  -- nome continua sendo só texto digitado livremente, exatamente como
  -- sempre funcionou antes da migração 025.
  fiscal_projeto            text,                -- fiscal do contrato, nomeado pelo cliente
  fiscal_projeto_recurso_id             uuid,     -- recurso do lado do cliente/terceirizado (ou NULL = nome livre)
  gestor_projeto            text,                -- gestor do projeto pelo cliente
  gestor_projeto_recurso_id             uuid,
  gerente_projeto_cliente   text,
  gerente_projeto_cliente_recurso_id    uuid,
  gerente_projeto_consultoria    text,
  gerente_projeto_consultoria_recurso_id uuid,    -- recurso precisa ser da Consultoria (checado no backend)
  lider_projeto_consultoria      text,
  lider_projeto_consultoria_recurso_id   uuid,    -- idem, recurso da Consultoria
  data_abertura       date,                      -- data de abertura/assinatura do projeto
  data_inicio         date,                      -- início previsto
  data_inicio_real    date,
  data_fim_prevista   date,
  prazo_total_meses   numeric(5,1),               -- prazo contratual total, em meses
  valor_global_contrato numeric(14,2),             -- valor global do contrato (migração 018) — referência para
                                       -- acompanhamento de faturamento; não é somado/derivado automaticamente
                                       -- do valor das faturas emitidas, é digitado direto no cadastro do projeto
  horas_dia_util  numeric(4,1) NOT NULL DEFAULT 8.0,   -- jornada padrão p/ conversão horas->dias no CPM
  criado_em       timestamptz NOT NULL DEFAULT now(),
  atualizado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_projetos_atualizado_em BEFORE UPDATE ON projetos
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
COMMENT ON TABLE projetos IS 'Um projeto de implantação (ex: Sistema num órgão específico). Permite reuso do modelo em outros clientes da Consultoria.';

-- ============================================================================
-- 2. ETAPAS  (Etapa 1, 2, 3 do documento de metodologia)
-- ============================================================================
CREATE TABLE etapas (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  numero          int NOT NULL,                 -- 1, 2, 3...
  nome            text NOT NULL,                -- "Folha de Pagamento", "Módulos Isolados"...
  descricao       text,
  data_inicio_prev date,
  data_fim_prev   date,
  criado_em       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (projeto_id, numero)
);
COMMENT ON TABLE etapas IS 'Macro-fases do projeto, conforme a metodologia (Etapa 1 = folha completa, Etapa 2 = módulos isolados, etc).';

-- ============================================================================
-- 3. FRENTES DE TRABALHO
-- ============================================================================
CREATE TABLE frentes_trabalho (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  nome            text NOT NULL,      -- Parametrização, Migração de Dados, Folha de Pagamento, Treinamento...
  descricao       text,
  cor_hex         text,               -- para exibição consistente no front-end
  ordem           int NOT NULL DEFAULT 0,
  ativo           boolean NOT NULL DEFAULT true,  -- permite "aposentar" uma frente sem quebrar atividades já lançadas nela
  criado_em       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (projeto_id, nome)
);
COMMENT ON TABLE frentes_trabalho IS 'Catálogo de frentes: Parametrização, Migração de Dados, Folha de Pagamento, Customizações, Contagem de Tempo, Integrações, eSocial, Portal do Servidor, Workflow, BI, Treinamentos, Gestão.';

-- ============================================================================
-- 4. TIPOS DE ATIVIDADE ELEMENTAR  (campo tabelado pedido pelo usuário)
-- ============================================================================
CREATE TABLE tipos_atividade_elementar (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome            text NOT NULL UNIQUE,   -- Levantamento, Parametrização, Extração, Carga, Análise de Rejeição,
                                           -- Especificação de Customização, Programação, Homologação, Entrega,
                                           -- Treinamento, Teste, Comparação, Reunião...
  descricao       text,
  ordem           int NOT NULL DEFAULT 0,
  ativo           boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE tipos_atividade_elementar IS 'Vocabulário controlado do "tipo" de trabalho de cada atividade, reutilizável entre frentes — permite agregar, por exemplo, "todas as atividades de Levantamento", independente da frente.';

-- ============================================================================
-- 5. RECURSOS (pessoas: consultores da consultoria, analistas/gestores do cliente)
-- ============================================================================
CREATE TABLE recursos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome            text NOT NULL,
  tipo_vinculo    tipo_vinculo_enum NOT NULL DEFAULT 'Consultoria',  -- Consultoria / Cliente / Terceirizado — usado para filtrar recursos
  empresa         text,               -- nome da empresa/instituição a que pertence (ex: "Consultoria", "TSE", "Consultoria XYZ")
  cargo           text,               -- "Consultor de Folha", "Líder Técnico", "Analista de TI", "Gestor de RH"...
  email           text,
  telefone        text,
  controla_horas  boolean NOT NULL DEFAULT true,  -- migração 013: só recursos com esta marca registram apontamento em "Minhas atividades" / aparecem no Relatório de Horas dos Recursos
  ativo           boolean NOT NULL DEFAULT true,
  criado_em       timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE recursos IS 'Recursos (consultores da consultoria, terceirizados, analistas/gestores do cliente). tipo_vinculo classifica o lado; empresa guarda o nome real da empresa/instituição; controla_horas define se o recurso pode apontar horas em "Minhas atividades".';

-- Agora que recursos existe, fecha as FKs dos 5 interlocutores do projeto
-- (declaradas lá em cima — migração 025 — mesmo motivo de
-- atividades.requisito_tr_id). ON DELETE SET NULL: excluir o recurso não
-- apaga o projeto nem o nome congelado, só desvincula o cadastro.
ALTER TABLE projetos ADD CONSTRAINT fk_projetos_fiscal_recurso FOREIGN KEY (fiscal_projeto_recurso_id) REFERENCES recursos(id) ON DELETE SET NULL;
ALTER TABLE projetos ADD CONSTRAINT fk_projetos_gestor_recurso FOREIGN KEY (gestor_projeto_recurso_id) REFERENCES recursos(id) ON DELETE SET NULL;
ALTER TABLE projetos ADD CONSTRAINT fk_projetos_gerente_cliente_recurso FOREIGN KEY (gerente_projeto_cliente_recurso_id) REFERENCES recursos(id) ON DELETE SET NULL;
ALTER TABLE projetos ADD CONSTRAINT fk_projetos_gerente_consultoria_recurso FOREIGN KEY (gerente_projeto_consultoria_recurso_id) REFERENCES recursos(id) ON DELETE SET NULL;
ALTER TABLE projetos ADD CONSTRAINT fk_projetos_lider_consultoria_recurso FOREIGN KEY (lider_projeto_consultoria_recurso_id) REFERENCES recursos(id) ON DELETE SET NULL;

-- ============================================================================
-- 6. ATIVIDADES  (WBS hierárquica — o coração do cronograma)
-- ============================================================================
CREATE TABLE atividades (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id        uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  etapa_id          uuid REFERENCES etapas(id),          -- opcional desde a migração 026: a importação de
                                       -- cronograma deixa em branco quando não identifica com confiança uma Etapa
                                       -- já cadastrada pelo nome da atividade, em vez de forçar/inventar uma
  frente_trabalho_id uuid REFERENCES frentes_trabalho(id), -- idem, para Frente de trabalho (migração 026)
  tipo_atividade_elementar_id uuid REFERENCES tipos_atividade_elementar(id),
  atividade_pai_id  uuid REFERENCES atividades(id) ON DELETE CASCADE,  -- hierarquia WBS (subatividades)
  requisito_tr_id   uuid,             -- item do Termo de Referência ao qual esta atividade se relaciona (opcional).
                                       -- FK criada mais abaixo (requisitos_tr só é definida depois). Subatividades
                                       -- herdam este valor do pai automaticamente na criação (ver backend).

  codigo_wbs        text,             -- ex: "1.3.2" — numeração hierárquica, mantida pela aplicação
  origem_importacao_id text,          -- Id da tarefa na planilha de origem (MS Project), usado para casar a
                                       -- mesma atividade entre reimportações do cronograma (atualiza em vez de
                                       -- duplicar) — ver backend/app/cronograma_import.py. NULL se cadastrada manualmente.
  nome              text NOT NULL,    -- "Levantamento Grupo de Vencimento", "Extração de Pessoas"...
  descricao         text,
  objetivo          text,             -- o que essa atividade entrega/produz (ajuda o time a não perder de vista
                                       -- o resultado esperado) — distinto de `descricao`, que é livre
  -- Quem executa/participa da atividade (Consultoria, cliente ou terceirizado, quantos forem
  -- necessários) fica em `atividade_recurso` (N:N — ver seção 8), não mais em campos fixos
  -- aqui. Migração 012 removeu os antigos responsavel_consultoria_id/responsavel_cliente_id
  -- (um único recurso de cada lado) por essa tabela de relação.

  prazo_horas       numeric(8,2),     -- horas previstas (estimativa de esforço planejada)
  horas_realizadas  numeric(8,2),     -- horas efetivamente gastas na execução — pode ficar acima ou abaixo do previsto
  dtini_prev        date,
  dtfim_prev        date,
  dtini_real        date,
  dtfim_real        date,

  -- Consistência entre percentual_concluido=100, status numa das 4 variantes de
  -- Concluída e dtfim_real preenchido é exigida pela APLICAÇÃO
  -- (main.py/validar_consistencia_conclusao, migração 019/28ª rodada), não por
  -- CHECK aqui no banco — a importação de cronograma (cronograma_import.py)
  -- continua podendo gravar direto por SQL sem passar por essa validação, então
  -- uma atividade importada ainda pode ficar temporariamente inconsistente até
  -- ser editada pela tela (ou corrigida pela migration_019, que já limpa o que já
  -- existir hoje). A partir da 29ª rodada (migração 020) a APLICAÇÃO também
  -- calcula sozinha QUAL das 4 variantes de Concluída vale, a partir de
  -- dtfim_prev x dtfim_real (prazo) e prazo_horas x horas_realizadas (esforço) —
  -- ver main.py/classificar_conclusao.
  percentual_concluido smallint NOT NULL DEFAULT 0 CHECK (percentual_concluido BETWEEN 0 AND 100),
  status            status_atividade_enum NOT NULL DEFAULT 'Não iniciada',
  prioridade        prioridade_enum NOT NULL DEFAULT 'Média',

  -- resultado do último cálculo de caminho crítico (CPM), em dias úteis a partir do início do projeto
  cpm_es_dias       numeric(8,2),     -- early start
  cpm_ef_dias       numeric(8,2),     -- early finish
  cpm_ls_dias       numeric(8,2),     -- late start
  cpm_lf_dias       numeric(8,2),     -- late finish
  cpm_folga_dias    numeric(8,2),     -- folga total (slack)
  cpm_critica       boolean NOT NULL DEFAULT false,
  cpm_calculado_em  timestamptz,
  cpm_es_data       date,             -- datas de calendário equivalentes a es/ef/ls/lf (calculadas a partir do calendário útil)
  cpm_ef_data       date,
  cpm_ls_data       date,
  cpm_lf_data       date,

  observacoes       text,
  eh_atividade_master boolean NOT NULL DEFAULT false,  -- marca esta atividade como um dos entregáveis mestres do
                                       -- projeto (ex: "Folha definitiva") — usado pelo Relatório Executivo (IA)
                                       -- para calcular previsão de conclusão e priorizar o diagnóstico
  eh_entregavel     boolean NOT NULL DEFAULT false,  -- marca a atividade como um entregável atrelado a faturamento
                                       -- (ex: "Aceite da Migração", "Aceite da customização Bloco 1"). Independente
                                       -- de eh_atividade_master: um entregável faturável não é necessariamente um
                                       -- marco mestre do projeto (migração 017). Valores de faturamento ficam para
                                       -- uma tela própria futura — aqui só marca "isto é um entregável".
  criado_em         timestamptz NOT NULL DEFAULT now(),
  atualizado_em     timestamptz NOT NULL DEFAULT now(),

  CHECK (dtfim_prev IS NULL OR dtini_prev IS NULL OR dtfim_prev >= dtini_prev),
  CHECK (dtfim_real IS NULL OR dtini_real IS NULL OR dtfim_real >= dtini_real)
);
CREATE INDEX idx_atividades_projeto ON atividades(projeto_id);
CREATE INDEX idx_atividades_etapa ON atividades(etapa_id);
CREATE INDEX idx_atividades_frente ON atividades(frente_trabalho_id);
CREATE INDEX idx_atividades_pai ON atividades(atividade_pai_id);
CREATE INDEX idx_atividades_status ON atividades(status);
CREATE INDEX idx_atividades_dtfim_prev ON atividades(dtfim_prev);
CREATE INDEX idx_atividades_dtini_prev ON atividades(dtini_prev);
CREATE INDEX idx_atividades_origem_importacao ON atividades(projeto_id, origem_importacao_id);
CREATE INDEX idx_atividades_master ON atividades(projeto_id) WHERE eh_atividade_master = true;
CREATE INDEX idx_atividades_entregavel ON atividades(projeto_id) WHERE eh_entregavel = true;
CREATE TRIGGER trg_atividades_atualizado_em BEFORE UPDATE ON atividades
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
COMMENT ON TABLE atividades IS 'Atividade elementar do cronograma. atividade_pai_id permite desdobrar uma atividade em subatividades quando necessário (WBS).';
COMMENT ON COLUMN atividades.cpm_folga_dias IS 'Folga total calculada pelo método do caminho crítico; 0 = atividade crítica.';
COMMENT ON COLUMN atividades.requisito_tr_id IS 'Item do Termo de Referência ao qual esta atividade se relaciona (opcional). Subatividades herdam do pai na criação.';

-- Histórico de mudança de status (auditoria automática via trigger)
CREATE TABLE historico_status_atividade (
  id              bigserial PRIMARY KEY,
  atividade_id    uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,
  status_anterior status_atividade_enum,
  status_novo     status_atividade_enum NOT NULL,
  alterado_em     timestamptz NOT NULL DEFAULT now(),
  alterado_por    text
);
CREATE INDEX idx_historico_atividade ON historico_status_atividade(atividade_id);

CREATE OR REPLACE FUNCTION registrar_historico_status_atividade() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO historico_status_atividade(atividade_id, status_anterior, status_novo, alterado_por)
    VALUES (NEW.id, CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.status END, NEW.status,
            current_setting('app.usuario_atual', true));
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_atividades_historico
  AFTER INSERT OR UPDATE ON atividades
  FOR EACH ROW EXECUTE FUNCTION registrar_historico_status_atividade();

-- Relatos de andamento (consultores/cliente) — log temporal, append-only (sem UPDATE/DELETE pela aplicação).
-- Toda atividade com status diferente de "Não iniciada"/"Concluída" precisa ter ao menos um relato
-- (regra aplicada no backend). Até a migração 026 este relato também podia ser marcado como
-- "pendência" (eh_pendencia + campos pendencia_*); a migração 027 removeu esses campos — sinalizar
-- uma pendência a partir de uma atividade agora é feito pelo cadastro formal `pendencias` (ver
-- abaixo), pré-preenchido com a atividade, aberto pela própria aba "Pendências" do modal.
CREATE TABLE atividade_relato (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  atividade_id              uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,
  autor_id                  uuid REFERENCES recursos(id),      -- autor cadastrado como recurso (consultor da consultoria ou do cliente)
  autor_nome                text,                              -- alternativa livre, caso o autor não esteja cadastrado em recursos
  texto                     text NOT NULL,
  criado_em                 timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_relato_atividade ON atividade_relato(atividade_id, criado_em DESC);
COMMENT ON TABLE atividade_relato IS 'Registro temporal (append-only) de relatos de andamento — insumo futuro para análise de IA e relatórios executivos (a IA analisa e sugere, não decide/coordena).';

-- ============================================================================
-- 7. DEPENDÊNCIAS ENTRE ATIVIDADES  (grafo usado no cálculo do caminho crítico)
-- ============================================================================
CREATE TABLE atividade_dependencia (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  atividade_id      uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,  -- sucessora
  predecessora_id   uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,
  tipo              tipo_dependencia_enum NOT NULL DEFAULT 'FS',
  lag_horas         numeric(8,2) NOT NULL DEFAULT 0,   -- tempo de espera adicional (pode ser negativo p/ antecipação)
  CHECK (atividade_id <> predecessora_id),
  UNIQUE (atividade_id, predecessora_id)
);
CREATE INDEX idx_dep_atividade ON atividade_dependencia(atividade_id);
CREATE INDEX idx_dep_predecessora ON atividade_dependencia(predecessora_id);
COMMENT ON TABLE atividade_dependencia IS 'Arestas do grafo de precedência. tipo/lag seguem a convenção clássica de CPM (FS, SS, FF, SF + lag).';

-- ============================================================================
-- 8. ALOCAÇÃO DE RECURSOS EM ATIVIDADES (N:N — quantos recursos forem necessários)
-- ============================================================================
-- Desde a migração 012, esta é a ÚNICA fonte de "quem participa/executa" uma
-- atividade — os antigos campos fixos atividades.responsavel_consultoria_id /
-- responsavel_cliente_id (um só de cada lado) foram removidos. Uma atividade
-- pode ter quantos participantes forem necessários, de qualquer tipo de
-- vínculo (ex: uma reunião gerencial com 4 pessoas da Consultoria e 5 do cliente).
-- Editável na aba "Recursos" do modal da atividade (ver frontend/index.html
-- e as rotas /api/atividades/<id>/recursos em backend/app/main.py). É também
-- esta lista que decide quem vê a atividade em "Minhas atividades" — cada
-- participante aponta e confirma as PRÓPRIAS horas lá, de forma independente
-- dos demais (ver seção 18 abaixo) — sem que isso altere o previsto/realizado
-- da atividade aqui no Cronograma (`atividades.prazo_horas`/`horas_realizadas`
-- continuam sendo um número único, editado à parte, igual para a atividade
-- inteira independente de quantos participaram ou de quantas horas cada um
-- apontou individualmente).
CREATE TABLE atividade_recurso (
  atividade_id    uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,
  recurso_id      uuid NOT NULL REFERENCES recursos(id) ON DELETE CASCADE,
  papel_na_atividade text,           -- "Executor", "Revisor", "Aprovador"
  horas_alocadas  numeric(8,2),
  PRIMARY KEY (atividade_id, recurso_id)
);

-- ============================================================================
-- 9. CICLOS DE MIGRAÇÃO  (execuções semanais — extração/carga/rejeições)
-- ============================================================================
CREATE TABLE ciclos_migracao (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  atividade_id          uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,
  numero_ciclo          int NOT NULL,
  data_execucao         date NOT NULL,
  qtd_registros_extraidos int,
  qtd_registros_carregados int,
  qtd_rejeicoes         int,
  percentual_rejeicao   numeric(5,2) GENERATED ALWAYS AS (
    CASE WHEN qtd_registros_extraidos > 0
         THEN round(100.0 * qtd_rejeicoes / qtd_registros_extraidos, 2)
         ELSE NULL END
  ) STORED,
  observacoes           text,
  criado_em             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (atividade_id, numero_ciclo)
);
COMMENT ON TABLE ciclos_migracao IS 'Uma linha por rodada semanal de migração de um tema (a atividade "pai" representa o tema, ex: Migração de Pessoas).';

-- ============================================================================
-- 10. REQUISITOS DO TERMO DE REFERÊNCIA
-- ============================================================================
CREATE TABLE requisitos_tr (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  codigo          text NOT NULL,             -- número do item no edital, ex: "TR-014"
  titulo          text NOT NULL,
  descricao       text,                      -- texto literal do edital
  tipo_requisito  tipo_requisito_enum NOT NULL DEFAULT 'Funcional',
  modulo_origem   text,                      -- seção/módulo do TR de onde o item veio (rastreabilidade)
  frente_trabalho_id uuid REFERENCES frentes_trabalho(id),
  classificacao   classificacao_tr_enum NOT NULL DEFAULT 'Obrigatório',
  atendimento     atendimento_tr_enum NOT NULL DEFAULT 'A analisar',
  status          status_requisito_enum NOT NULL DEFAULT 'Não iniciado',
  responsavel_id  uuid REFERENCES recursos(id),
  prioridade      prioridade_enum NOT NULL DEFAULT 'Média',
  cobranca        cobranca_enum NOT NULL DEFAULT 'N/A',
  data_levantamento date,
  observacoes     text,
  resposta_oficial text,             -- resposta do consultor no processo de Análise TR x Manuais (complementa as referências encontradas)
  analisado_em    timestamptz,       -- última vez que a busca por referência nos manuais foi executada pra este requisito
  criado_em       timestamptz NOT NULL DEFAULT now(),
  atualizado_em   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (projeto_id, codigo)
);
CREATE INDEX idx_requisitos_projeto ON requisitos_tr(projeto_id);
CREATE INDEX idx_requisitos_status ON requisitos_tr(status);
CREATE TRIGGER trg_requisitos_atualizado_em BEFORE UPDATE ON requisitos_tr
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

-- Agora que requisitos_tr existe, fecha a FK de atividades.requisito_tr_id (declarada lá em cima).
ALTER TABLE atividades ADD CONSTRAINT fk_atividades_requisito_tr FOREIGN KEY (requisito_tr_id) REFERENCES requisitos_tr(id);
CREATE INDEX idx_atividades_requisito_tr ON atividades(requisito_tr_id);

-- Rastreabilidade requisito <-> atividades que o atendem (N:N)
CREATE TABLE atividade_requisito (
  atividade_id    uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,
  requisito_id    uuid NOT NULL REFERENCES requisitos_tr(id) ON DELETE CASCADE,
  PRIMARY KEY (atividade_id, requisito_id)
);
COMMENT ON TABLE atividade_requisito IS 'Um requisito pode gerar várias atividades (levantamento, parametrização, migração, programação, teste) e uma atividade pode atender vários requisitos.';

-- ============================================================================
-- 11. MARCOS (milestones)
-- ============================================================================
CREATE TABLE marcos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  etapa_id        uuid REFERENCES etapas(id),
  nome            text NOT NULL,
  descricao       text,
  data_prevista   date NOT NULL,
  data_real       date,
  origem_importacao_id text,   -- mesmo propósito de atividades.origem_importacao_id, para marcos extraídos
                                -- de tarefas de duração zero na importação de cronograma (Excel/CSV)
  criado_em       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_marcos_origem_importacao ON marcos(projeto_id, origem_importacao_id);

-- ============================================================================
-- 12. RISCOS
-- ============================================================================
CREATE TABLE riscos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  descricao       text NOT NULL,
  categoria       text,
  probabilidade   nivel_risco_enum NOT NULL DEFAULT 'Médio',
  impacto         nivel_risco_enum NOT NULL DEFAULT 'Médio',
  mitigacao       text,
  responsavel_id  uuid REFERENCES recursos(id),
  status          status_risco_enum NOT NULL DEFAULT 'Aberto',
  identificado_em date NOT NULL DEFAULT CURRENT_DATE,
  atualizado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_riscos_atualizado_em BEFORE UPDATE ON riscos
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

-- ============================================================================
-- 12B. PENDÊNCIAS (41ª rodada, migração 027)
-- ============================================================================
-- Situação distinta de risco (evento futuro possível) e marco (data
-- contratual): um problema JÁ em curso, com responsável de cada lado
-- (consultoria e cliente) e prazo cobrando solução. Pode ou não estar
-- ligada a uma atividade e/ou a um risco já cadastrado. Sem rota de
-- exclusão (mesmo padrão de riscos/marcos/requisitos) — usar status
-- 'Cancelada' em vez de apagar.
CREATE TYPE status_pendencia_enum AS ENUM ('Aberta','Em andamento','Resolvida','Cancelada');

CREATE TABLE pendencias (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id                  uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  codigo                      text NOT NULL,        -- gerado pelo backend, formato "PND-001" (sequencial por projeto)
  titulo                      text NOT NULL,
  frente_trabalho_id          uuid NOT NULL REFERENCES frentes_trabalho(id),
  atividade_id                uuid REFERENCES atividades(id) ON DELETE SET NULL,
  risco_id                    uuid REFERENCES riscos(id) ON DELETE SET NULL,
  descricao                   text NOT NULL,
  impactos                    text,
  responsavel_consultoria_id  uuid NOT NULL REFERENCES recursos(id),  -- tipo_vinculo = 'Consultoria' (validado no backend)
  responsavel_cliente_id      uuid NOT NULL REFERENCES recursos(id),  -- tipo_vinculo <> 'Consultoria' (validado no backend)
  data_identificacao          date NOT NULL DEFAULT CURRENT_DATE,
  data_limite                 date NOT NULL,
  data_prevista                date,
  data_real                   date,
  status                      status_pendencia_enum NOT NULL DEFAULT 'Aberta',
  prioridade                  prioridade_enum NOT NULL DEFAULT 'Média',
  categoria                   text,
  acoes_necessarias           text,
  observacoes                 text,
  criado_em                   timestamptz NOT NULL DEFAULT now(),
  atualizado_em                timestamptz NOT NULL DEFAULT now(),
  UNIQUE (projeto_id, codigo)
);
CREATE INDEX idx_pendencias_projeto ON pendencias(projeto_id);
CREATE INDEX idx_pendencias_atividade ON pendencias(atividade_id) WHERE atividade_id IS NOT NULL;
CREATE INDEX idx_pendencias_risco ON pendencias(risco_id) WHERE risco_id IS NOT NULL;
CREATE INDEX idx_pendencias_abertas ON pendencias(projeto_id, data_limite) WHERE status NOT IN ('Resolvida','Cancelada');
CREATE TRIGGER trg_pendencias_atualizado_em BEFORE UPDATE ON pendencias
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
COMMENT ON TABLE pendencias IS 'Registro formal de pendências (RAID log).';

-- ============================================================================
-- 13. CALENDÁRIO ÚTIL (feriados/fins de semana do cliente — usado no CPM)
-- ============================================================================
CREATE TABLE calendario_util (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  data            date NOT NULL,
  util            boolean NOT NULL DEFAULT false,
  descricao       text,      -- "Feriado municipal", "Recesso"...
  UNIQUE (projeto_id, data)
);
COMMENT ON TABLE calendario_util IS 'Exceções ao calendário padrão (seg-sex úteis). Usada para converter dias do CPM em datas de calendário reais.';

-- ============================================================================
-- 14. ANEXOS (arquivos: xls, csv, txt, pdf, ... vinculados a qualquer entidade)
-- ============================================================================
CREATE TABLE anexos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entidade_tipo   entidade_anexo_enum NOT NULL,
  entidade_id     uuid NOT NULL,          -- id da atividade/requisito/risco/marco (FK polimórfica, validada na aplicação)
  nome_original   text NOT NULL,
  caminho_arquivo text NOT NULL,          -- caminho no armazenamento (disco/S3/MinIO)
  tipo_mime       text,
  tamanho_bytes   bigint,
  enviado_por     text,
  enviado_em      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_anexos_entidade ON anexos(entidade_tipo, entidade_id);
COMMENT ON TABLE anexos IS 'Metadados de arquivos (xls, csv, txt, pdf...) anexados a atividades, requisitos, riscos ou marcos. O conteúdo fica em disco/objeto; aqui só o caminho.';

-- ============================================================================
-- 15. RELATÓRIOS EXECUTIVOS GERADOS POR IA (Dashboard)
-- ============================================================================
-- Histórico dos relatórios executivos gerados no Dashboard — guarda o texto
-- (markdown) devolvido pela IA e também um retrato (JSON) dos dados/números
-- que foram enviados no prompt, para auditoria: qualquer diagnóstico
-- apresentado ao cliente deve poder ser rastreado até os dados de origem.
-- Ver backend/app/relatorio_executivo.py e a seção "Relatório Executivo (IA)"
-- do README.
CREATE TABLE relatorios_executivos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id      uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  gerado_em       timestamptz NOT NULL DEFAULT now(),
  modelo_ia       text NOT NULL,
  conteudo_md     text NOT NULL,
  dados_enviados  jsonb NOT NULL,
  gerado_por      text
);
CREATE INDEX idx_relatorios_executivos_projeto ON relatorios_executivos(projeto_id, gerado_em DESC);
COMMENT ON TABLE relatorios_executivos IS 'Histórico dos relatórios executivos gerados por IA no Dashboard — ver backend/app/relatorio_executivo.py.';

-- ============================================================================
-- 16. USUÁRIOS (login no sistema)
-- ============================================================================
-- Autocadastro aberto: qualquer pessoa acessa a tela de "Primeiro acesso /
-- Cadastro", preenche todos os campos (obrigatórios) e já sai com login
-- liberado (a própria senha digitada no cadastro). "Esqueci a senha" gera
-- uma nova senha aleatória e envia por e-mail (não é um link de
-- redefinição). Tabela independente de "recursos": nem todo usuário de
-- login é um recurso alocado em atividades no cronograma, e vice-versa. Todos
-- os usuários autenticados têm o mesmo nível de acesso (sem perfil
-- "administrador" nesta rodada). Ver backend/app/auth.py, as rotas
-- /api/auth/* e /api/usuarios em backend/app/main.py, e a seção "Login e
-- cadastro de usuários" do README.
CREATE TABLE usuarios (
  id                           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email                        text NOT NULL,
  nome                         text NOT NULL,
  telefone                     text NOT NULL,
  empresa                      text NOT NULL,
  cargo                        text NOT NULL,
  senha_hash                   text NOT NULL,
  recebe_relatorio_executivo   boolean NOT NULL DEFAULT true,
  recebe_notificacao_whatsapp  boolean NOT NULL DEFAULT true,
  ativo                        boolean NOT NULL DEFAULT true,
  criado_em                    timestamptz NOT NULL DEFAULT now(),
  ultimo_login_em              timestamptz
);
CREATE UNIQUE INDEX idx_usuarios_email_lower ON usuarios (lower(email));
COMMENT ON TABLE usuarios IS
  'Usuários com login no sistema (equipe Consultoria e do cliente). Autocadastro '
  'aberto — ver README, seção "Login e cadastro de usuários". Distinta de '
  '"recursos" (pessoas alocadas em atividades no cronograma): um '
  'usuário de login não precisa ser um recurso, e vice-versa.';
COMMENT ON COLUMN usuarios.senha_hash IS
  'Hash da senha (werkzeug.security.generate_password_hash) — a senha em '
  'texto puro nunca é armazenada.';
COMMENT ON COLUMN usuarios.recebe_relatorio_executivo IS
  'Reservado para uso futuro: se este usuário deve receber cópia dos '
  'Relatórios Executivos (IA) gerados no Dashboard (ex: por e-mail).';
COMMENT ON COLUMN usuarios.recebe_notificacao_whatsapp IS
  'Reservado para uso futuro: se este usuário deve receber notificações '
  'via WhatsApp no telefone cadastrado. Nenhum envio de WhatsApp é feito '
  'ainda nesta rodada — só o cadastro do telefone e da preferência.';

-- ============================================================================
-- 17. PARÂMETROS DO SITE (identidade visual: logo e nome da empresa)
-- ============================================================================
-- Tabela "singleton" (mesma convenção de `projetos`): sem constraint de
-- banco forçando 1 linha só — a aplicação sempre lê/edita a primeira
-- linha, e cria uma linha padrão automaticamente na primeira leitura se
-- ainda não existir nenhuma. Ver Configurações > Parâmetros no frontend e
-- as rotas /api/parametros em backend/app/main.py.
--
-- O tema da página (claro/escuro/automático) NÃO fica guardado aqui: é
-- uma preferência pessoal de cada usuário, salva só no navegador dela
-- (localStorage) — não afeta o que os demais usuários veem.
CREATE TABLE parametros_site (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome_empresa  text NOT NULL DEFAULT '',
  logo_arquivo  text,
  logo_dados    text,
  logo_mime     text,
  atualizado_em timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE parametros_site IS
  'Parâmetros de identidade visual do site (logo e nome da empresa), '
  'exibidos no topo do sistema. Tabela "singleton" — a aplicação sempre '
  'usa a primeira linha, criando uma linha padrão automaticamente se '
  'necessário. Qualquer usuário autenticado pode editar (mesmo nível '
  'único de acesso das demais telas de Configurações — ver tabela '
  'usuarios).';
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

-- ============================================================================
-- 18. HORAS DIÁRIAS DA ATIVIDADE  ("Minhas atividades" — grade semanal do consultor)
-- ============================================================================
-- Quebra diária das horas previstas de uma atividade, POR RESPONSÁVEL.
-- Alimenta a página "Minhas atividades": cada usuário logado vê, numa
-- planilha, os dias úteis da semana corrente cruzados com as atividades em
-- que o profissional vinculado a ele (ver nota abaixo) é um dos participantes
-- (tabela `atividade_recurso`, seção 8) — com a hora prevista de cada dia — e
-- pode ajustar o número ou simplesmente confirmar a execução.
--
-- Desde a migração 012, a granularidade é por (atividade, recurso, dia), não
-- mais só (atividade, dia): uma atividade com N participantes (ex: uma
-- reunião gerencial com 4 pessoas da Consultoria e 5 do cliente) gera uma quebra
-- diária PRÓPRIA para cada participante, e cada um aponta/confirma as
-- próprias horas de forma totalmente independente dos demais — sem que isso
-- altere `atividades.prazo_horas`/`horas_realizadas`, que continuam sendo um
-- número único da atividade, editado à parte (a reunião continua
-- prevista/realizada em, por exemplo, 2h no Cronograma, não importa quantos
-- participaram nem quantas horas cada um apontou individualmente).
--
-- As linhas desta tabela são geradas automaticamente, por participante
-- (pelo backend, função garantir_dias() em backend/app/minhas_atividades.py)
-- na primeira vez que a grade da atividade é aberta por aquele participante:
-- o total de atividades.prazo_horas é dividido igualmente pelos dias úteis
-- entre dtini_prev e dtfim_prev (respeitando as exceções cadastradas em
-- calendario_util) — cada participante recebe a MESMA distribuição prevista
-- (não dividida entre eles), e cada um confirma seu próprio andamento. A
-- partir daí a geração NÃO se repete para aquele participante (mesmo que
-- prazo_horas/datas sejam editados depois por reimportação de cronograma ou
-- edição manual) — a grade diária, uma vez criada, passa a ser a fonte de
-- verdade e não deve ser sobrescrita por ajustes já feitos pelo consultor.
-- Isso é uma limitação conhecida: se o planejamento de uma atividade muda
-- muito depois que a grade já foi gerada, pode ser necessário um ajuste
-- manual dia a dia (não existe nesta rodada uma regeneração/redistribuição
-- automática).
--
-- O vínculo "usuário logado -> profissional (recursos)" NÃO é uma coluna
-- nova — é resolvido em tempo de consulta comparando o e-mail de login
-- (usuarios.email) com o e-mail cadastrado no Recurso (recursos.email),
-- ambos case-insensitive. Um usuário cujo e-mail de login não bate com
-- nenhum Recurso cadastrado, ou que bate com um Recurso sem
-- `controla_horas` habilitado (migração 013), simplesmente não vê nenhuma
-- atividade nesta tela (a interface avisa e orienta o que fazer em cada
-- caso — ver backend/app/minhas_atividades.py).
CREATE TABLE atividade_horas_dia (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  atividade_id      uuid NOT NULL REFERENCES atividades(id) ON DELETE CASCADE,
  recurso_id        uuid NOT NULL REFERENCES recursos(id) ON DELETE CASCADE,
  data              date NOT NULL,
  horas_previstas   numeric(5,2) NOT NULL DEFAULT 0,
  horas_realizadas  numeric(5,2),        -- preenchido quando o consultor confirma (ajustado ou igual ao previsto)
  confirmado        boolean NOT NULL DEFAULT false,
  confirmado_em     timestamptz,
  confirmado_por    uuid REFERENCES usuarios(id),
  atualizado_em     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (atividade_id, recurso_id, data)
);
CREATE INDEX idx_horas_dia_atividade ON atividade_horas_dia(atividade_id, data);
CREATE INDEX idx_horas_dia_recurso ON atividade_horas_dia(recurso_id);
CREATE INDEX idx_horas_dia_data ON atividade_horas_dia(data);
CREATE TRIGGER trg_horas_dia_atualizado_em BEFORE UPDATE ON atividade_horas_dia
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
COMMENT ON TABLE atividade_horas_dia IS
  'Quebra diária das horas previstas/confirmadas de uma atividade, POR '
  'RESPONSÁVEL — alimenta a página "Minhas atividades" (grade semanal por '
  'consultor). Gerada automaticamente uma única vez por (atividade, '
  'recurso) (ver comentário acima); a partir daí é a fonte de verdade '
  'daquele participante naquela atividade, dia a dia — independente dos '
  'demais participantes da mesma atividade.';

-- Acelera o casamento usuário-logado -> profissional por e-mail (ver comentário
-- acima) — mesmo padrão do índice funcional já usado em usuarios(lower(email)).
CREATE INDEX idx_recursos_email_lower ON recursos (lower(email)) WHERE email IS NOT NULL;

-- ============================================================================
-- 19. DOCUMENTAÇÃO (Documento Executivo e Documento Técnico do próprio sistema)
-- ============================================================================
-- Conteúdo (markdown) exibido em Configurações > Documentação e exportável
-- em PDF via impressão do navegador (window.print(), sem geração de PDF no
-- backend). Guardado no banco — não fixo no frontend — para poder ser
-- atualizado sem precisar de um novo deploy (ver PUT /api/documentacao/<tipo>
-- em backend/app/main.py). Convenção: toda mudança relevante no sistema deve
-- também atualizar o conteúdo destes dois documentos. Ver migration_014.
CREATE TABLE documentacao (
  tipo            text PRIMARY KEY CHECK (tipo IN ('executivo','tecnico')),
  titulo          text NOT NULL,
  versao          text NOT NULL DEFAULT '1.0',
  conteudo_md     text NOT NULL DEFAULT '',
  atualizado_em   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE documentacao IS
  'Conteúdo (markdown) dos dois documentos vivos do sistema, exibidos em '
  'Configurações > Documentação e exportáveis em PDF via impressão do '
  'navegador. "executivo" = visão geral de negócio; "tecnico" = banco de '
  'dados, relacionamentos e arquitetura. Devem ser atualizados a cada '
  'mudança relevante do sistema.';
CREATE TRIGGER trg_documentacao_atualizado_em BEFORE UPDATE ON documentacao
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

INSERT INTO documentacao (tipo, titulo, versao, conteudo_md) VALUES
('executivo', 'Documento Executivo', '1.0', $doc$# Gestão de Projetos — Documento Executivo

## 1. Visão geral

A **Gestão de Projetos** é o sistema de gestão da própria implantação do sistema (o sistema de Gestão de Recursos Humanos e Folha de Pagamento da Consultoria) em órgãos públicos. Ele não é o sistema em si — é a ferramenta que a equipe da Consultoria e a equipe do cliente usam, juntas, para planejar, acompanhar e prestar contas de todo o projeto de implantação: cronograma, requisitos do Termo de Referência, riscos, apontamento de horas da equipe, e a comunicação executiva do andamento.

O sistema foi desenhado para ser reutilizável: cada órgão/cliente onde a Consultoria implanta o sistema vira um **projeto** independente dentro do mesmo sistema, com seu próprio cronograma, sua própria equipe alocada e seus próprios requisitos — mas todos os usuários (consultores da Consultoria, em especial) enxergam, numa única tela ("Minhas atividades"), o apontamento de horas de todos os projetos em que estão atuando ao mesmo tempo.

## 2. Conceitos-chave

Antes de descrever cada tela, vale alinhar o vocabulário usado em todo o sistema.

**Projeto** — uma implantação do sistema em um cliente específico (ex: "Implantação do Sistema — TSE"). Tem sigla, nome, instituição/cliente, datas de início e fim previstas, prazo contratual e os nomes dos principais interlocutores (fiscal do contrato, gestor do projeto, gerentes e líder técnico). Um usuário pode participar de vários projetos e trocar entre eles pelo seletor no topo da tela.

**Etapa** — as macro-fases da metodologia de implantação (ex: Etapa 1 = folha de pagamento completa, Etapa 2 = módulos isolados). Cada atividade do cronograma pertence a uma etapa.

**Frente de trabalho** — as frentes técnicas do projeto: Parametrização, Migração de Dados, Folha de Pagamento, Customizações, Contagem de Tempo, Integrações, eSocial, Portal do Servidor, Workflow, BI, Treinamentos, Gestão, entre outras. Cada atividade pertence a uma frente.

**Recurso** — qualquer pessoa envolvida no projeto: consultor da consultoria, analista/gestor do cliente, ou terceirizado. Um recurso pode participar de quantas atividades forem necessárias, e uma atividade pode ter quantos recursos forem necessários (não há mais o limite antigo de "um responsável da consultoria + um responsável do cliente"). Cada recurso tem um vínculo (Consultoria, Cliente ou Terceirizado), pode ou não ter e-mail de login associado (o que o conecta à tela "Minhas atividades") e tem uma marcação de **controle de horas por atividade** — só recursos com essa marca ativa podem apontar e confirmar horas no sistema.

**Atividade** — a unidade elementar do cronograma (a WBS — Estrutura Analítica do Projeto). Toda atividade pertence a uma Etapa e a uma Frente de trabalho, tem um tipo elementar (Levantamento, Parametrização, Extração, Carga, Programação, Homologação, Treinamento, Reunião etc.), datas previstas/reais de início e fim, horas previstas/realizadas, percentual concluído, status e prioridade. Atividades podem ter subatividades (hierarquia WBS), dependências entre si (para o cálculo de caminho crítico) e podem estar vinculadas a um ou mais requisitos do Termo de Referência.

**Marco** — um evento de data única (sem duração), como a entrega de um produto ou uma virada de fase.

**Requisito TR** — um item do Termo de Referência do edital, com seu código, descrição, classificação (Essencial, Obrigatório, Desejável, Customizado curto/médio/longo), como está sendo atendido (nativo, parcial, customizado, não atende) e o status do trabalho de atendê-lo. Requisitos e atividades se relacionam em N:N: um requisito pode gerar várias atividades (levantar, parametrizar, migrar, programar, testar) e uma atividade pode atender vários requisitos ao mesmo tempo.

**Risco** — um risco identificado do projeto, com probabilidade, impacto, mitigação planejada, recurso responsável e status (aberto, mitigado, encerrado).

**Relato / Pendência** — qualquer atividade pode receber relatos de andamento ao longo do tempo (um histórico, nunca apagado). Um relato pode ser marcado como **pendência**, que exige um recurso responsável, um prazo possível de resolução e uma data limite — as pendências abertas aparecem nos alertas do Dashboard.

## 3. Telas do sistema

### 3.1 Login e cadastro

O acesso é por e-mail e senha. O cadastro é **autocadastro aberto**: qualquer pessoa preenche nome, e-mail, telefone, empresa, cargo e senha, e já sai com acesso liberado — não existe aprovação prévia nem hierarquia de perfil nesta versão (todo usuário autenticado enxerga as mesmas telas). "Esqueci a senha" gera uma senha nova aleatória e a envia por e-mail (não é um link de redefinição).

### 3.2 Seleção de projeto

O cabeçalho do sistema sempre mostra o projeto ativo, com um seletor para trocar entre os projetos em que o usuário tem atividades ou acesso, e a opção de cadastrar um novo projeto. A última escolha fica lembrada no navegador do usuário.

### 3.3 Dashboard

A tela inicial, com uma visão executiva do projeto selecionado: contagem de atividades por status, atividades atrasadas (fim previsto já passado e não concluídas/canceladas), horas previstas por recurso, e alertas de prioridade (atividades urgentes sem recurso definido ou sem prazo, pendências abertas se aproximando da data limite). A partir daqui também se geram os dois relatórios do sistema:

- **Relatório Executivo (IA)** — um relatório em linguagem natural, gerado por inteligência artificial a partir dos dados reais do projeto (atividades, prazos, status, riscos, pendências), analisando ritmo de execução e propondo diagnóstico — nunca decide nada sozinho, só analisa e sugere. Fica com histórico de gerações anteriores e pode ser baixado em PDF.
- **Relatório de Horas dos Recursos** — o apontamento de horas (previsto × realizado) que cada recurso lançou em "Minhas atividades", em **todos os projetos** em que atua, filtrável por recurso e por período (datas específicas ou mês/ano), exportável em PDF ou planilha Excel (com uma aba de resumo por recurso/atividade e outra de detalhe dia a dia).

### 3.4 Cronograma

A WBS completa do projeto, em lista, com filtros por período, etapa, frente de trabalho, recurso e status, e um indicador visual de atividades atrasadas. É possível: criar/editar atividades e marcos manualmente; importar um cronograma inteiro de uma planilha Excel/CSV ou de um arquivo de MS Project (a reimportação é sempre aditiva — atualiza atividades já existentes pelo código de origem, nunca duplica nem apaga participantes já cadastrados manualmente); e remover o cronograma inteiro de uma vez, quando necessário recomeçar.

O cadastro de cada atividade é organizado em abas:
- **Detalhes** — etapa, frente, código WBS, prioridade, nome, descrição, tipo elementar, item do TR vinculado, status e percentual concluído.
- **Recursos** — a lista de participantes da atividade (quantos forem necessários, de qualquer vínculo), com botão de adicionar/remover. É esta lista que decide quem vê a atividade em "Minhas atividades".
- **Relatos** — o histórico de andamento da atividade, com a opção de registrar uma pendência (recurso responsável, prazo possível, data limite).
- **Dependências** — os predecessores da atividade, usados no cálculo do caminho crítico.
- **Requisitos vinculados** — quais itens do Termo de Referência esta atividade atende.
- **Anexos** — arquivos (planilhas, PDFs, documentos) relacionados à atividade.

### 3.5 Caminho Crítico

O cálculo do método do caminho crítico (CPM) sobre a rede de dependências das atividades, convertendo horas previstas em dias úteis (considerando a jornada diária configurada no projeto e o calendário de feriados/recessos cadastrado). Mostra a sequência de atividades sem folga (as que, se atrasarem, atrasam o projeto inteiro) e as datas de início/fim mais cedo e mais tarde de cada uma.

### 3.6 Requisitos TR

A lista completa de requisitos do Termo de Referência, com seus filtros (classificação, atendimento, status, frente de trabalho) e a rastreabilidade com as atividades do cronograma que os atendem. Requisitos podem ser cadastrados manualmente, importados de uma planilha CSV, ou **extraídos automaticamente** a partir do próprio documento do Termo de Referência em Word ou PDF — o sistema lê o arquivo, reconhece os itens por padrões de texto (regras, sem inteligência artificial nesta extração) e apresenta uma prévia para conferência antes de confirmar a importação.

### 3.7 Riscos & Marcos

O cadastro de riscos do projeto (probabilidade, impacto, mitigação, recurso responsável, status) e a lista de marcos (eventos de data única, previstos e realizados).

### 3.8 Minhas atividades

A tela pessoal de apontamento de horas de cada consultor: uma grade semanal com os dias úteis cruzados com todas as atividades, **de todos os projetos**, em que o recurso vinculado ao usuário logado é um dos participantes. Cada dia mostra a hora prevista (distribuída automaticamente entre os dias úteis da atividade) e permite ajustar o valor ou simplesmente confirmar a execução. Cada participante de uma atividade aponta e confirma as próprias horas, de forma totalmente independente dos demais participantes da mesma atividade — isso nunca altera o previsto/realizado da atividade no Cronograma, que continua sendo um número único, editado à parte.

Só recursos marcados com "controla horas por atividade" conseguem usar esta tela; os demais recebem uma mensagem explicando o motivo (e-mail sem recurso cadastrado, ou recurso cadastrado sem essa marca habilitada).

### 3.9 Configurações

Os cadastros base do projeto, organizados em abas:
- **Dados do Projeto** — identificação, interlocutores e prazos do projeto ativo.
- **Recursos** — o cadastro de pessoas (nome, vínculo, empresa, cargo, e-mail, telefone, controle de horas, ativo/inativo).
- **Etapas** e **Frentes de Trabalho** — o vocabulário de fases e frentes do projeto.
- **Tipos de Atividade Elementar** — o vocabulário de "tipo de trabalho" (Levantamento, Parametrização, Programação etc.), reutilizável entre frentes.
- **Usuários** — os logins do sistema (distintos dos Recursos — nem todo usuário de login é um recurso alocado em atividades, e vice-versa).
- **Parâmetros** — identidade visual do sistema (logo e nome da empresa exibidos no topo, vistos por todos os usuários).
- **Documentação** — esta e a documentação técnica do sistema, sempre disponíveis para consulta e exportação em PDF.

## 4. Regras de negócio importantes

- **Horas da atividade vs. apontamento individual**: o previsto/realizado de uma atividade no Cronograma é sempre um número único, editável manualmente a qualquer momento — nunca é somado ou recalculado a partir do apontamento diário de cada participante em "Minhas atividades". Uma reunião com 9 participantes continua prevista em, por exemplo, 2 horas no Cronograma, independentemente de cada um dos 9 apontar suas próprias 2 horas individualmente.
- **Reimportação de cronograma é sempre aditiva**: reimportar uma planilha nunca apaga participantes, relatos ou anexos já cadastrados manualmente — só atualiza os campos que vêm da própria planilha (datas, percentual concluído) e cria o que ainda não existe.
- **Controle de horas por recurso**: só recursos com essa marca habilitada podem apontar horas em "Minhas atividades" e aparecem como opção de filtro no Relatório de Horas dos Recursos — mas continuam podendo ser adicionados livremente como participantes de qualquer atividade no Cronograma, marcados ou não.
- **Acesso único**: nesta versão não existe hierarquia de perfil — todo usuário autenticado tem acesso às mesmas telas e pode editar os mesmos cadastros. Times de acesso diferenciado (ex: só leitura para o cliente) não fazem parte do sistema ainda.

## 5. Um sistema em evolução

O Gestão de Projetos está em implantação contínua: novas funcionalidades, campos e validações são adicionadas conforme o projeto avança. Este documento é mantido atualizado a cada mudança relevante — a data e a versão no topo indicam a última revisão.$doc$),
('tecnico', 'Documento Técnico', '1.0', $doc$# Gestão de Projetos — Documento Técnico

## 1. Como usar este documento

Este documento descreve a arquitetura, o banco de dados e as convenções técnicas da **Gestão de Projetos** — a ferramenta de gestão da implantação do sistema (não confundir com o sistema em si, o produto de RH/Folha da Consultoria que está sendo implantado no cliente). Ele é o ponto de partida para qualquer pessoa (ou IA) que vá dar manutenção, corrigir bugs ou adicionar funcionalidades à Gestão de Projetos, e é mantido atualizado a cada rodada de desenvolvimento.

## 2. Stack tecnológica

| Camada | Tecnologia | Observação |
|---|---|---|
| Backend | Python 3 / Flask | API REST em JSON, servida também como arquivo estático do frontend |
| Banco de dados | PostgreSQL 14+ | Acesso via subprocess `psql` (ver seção 5), não via driver Python |
| Frontend | HTML + JavaScript puro (vanilla), um único arquivo `index.html` | Sem framework (React/Vue) nem build step — SPA por troca de visibilidade de `<section class="view">` |
| Geração de PDF | ReportLab | Relatório Executivo e Relatório de Horas dos Recursos |
| Geração de planilha | openpyxl | Relatório de Horas dos Recursos e exportações |
| Leitura de Word/PDF | python-docx, pypdf | Extração automática de requisitos do Termo de Referência |
| Cálculo de caminho crítico | NetworkX | Grafo de dependências entre atividades |
| Inteligência artificial | API da Anthropic (Claude) | Só no Relatório Executivo — analisa dados reais e escreve o diagnóstico em texto |
| Autenticação | Flask session (cookie assinado) + werkzeug.security | Sem JWT, sem OAuth — sessão de servidor tradicional |
| Deploy | Render (aplicação) + Supabase (Postgres gerenciado) | Também roda localmente via Docker Compose |

## 3. Arquitetura geral

O backend é organizado em módulos dentro de `backend/app/`, cada um com uma responsabilidade:

| Módulo | Responsabilidade |
|---|---|
| `main.py` | Todas as rotas HTTP da API (~65 rotas), validação de payload, orquestração entre módulos |
| `db.py` | Camada de acesso ao banco (ver seção 5) |
| `auth.py` | Autenticação, cadastro, hash de senha, "esqueci a senha" via e-mail |
| `cpm.py` | Cálculo do caminho crítico (CPM) |
| `cronograma_import.py` | Importação de cronograma via Excel/CSV/MS Project |
| `tr_parser.py` | Extração automática de requisitos a partir de documentos do Termo de Referência (.docx/.pdf) |
| `minhas_atividades.py` | Regras da tela "Minhas atividades": geração da grade diária, autorização por recurso, ajuste/confirmação de horas |
| `relatorio_executivo.py` | Coleta de dados do projeto e chamada à IA (Anthropic) para o Relatório Executivo |
| `relatorio_pdf.py` | Montagem do PDF do Relatório Executivo |
| `relatorio_atividades.py` | Coleta de dados e montagem de PDF/planilha do Relatório de Horas dos Recursos |

O frontend é um único arquivo (`frontend/index.html`, ~3000 linhas) com HTML, CSS e JavaScript embutidos — sem framework nem etapa de build. A navegação entre telas é feita alternando a visibilidade de blocos `<section class="view" id="view-...">`; o estado da aplicação vive em variáveis JavaScript globais (`PROJETO`, `ATIVIDADES`, `RECURSOS`, `ETAPAS`, `FRENTES` etc.), recarregadas da API a cada troca de projeto. Chamadas à API passam por uma função utilitária `api(caminho, opções)` que já injeta cabeçalhos e trata erros de forma padronizada. O tema da interface (claro/escuro/automático) é uma preferência salva só no navegador de cada usuário (`localStorage`), não no banco.

## 4. Autenticação e autorização

- Login é feito por e-mail/senha; a senha é validada com `werkzeug.security.check_password_hash` contra o hash salvo em `usuarios.senha_hash` (nunca se guarda senha em texto puro).
- A sessão é um **cookie Flask assinado** com `SECRET_KEY` (variável de ambiente obrigatória em produção — há um valor padrão inseguro só para não quebrar ambientes de desenvolvimento sem configuração, com aviso no log). O cookie guarda `usuario_id`.
- `before_request` (`exigir_login()` em `main.py`) bloqueia qualquer rota `/api/...` sem sessão ativa, exceto as rotas de login/cadastro/esqueci-senha (`AUTH_ROTAS_PUBLICAS`). As rotas do frontend estático (`/`, `/<path>`) nunca são bloqueadas — o próprio JavaScript decide mostrar a tela de login ou o sistema, consultando `GET /api/auth/me`.
- **Não existe hierarquia de perfil**: todo usuário autenticado tem acesso de leitura/escrita às mesmas rotas e telas. Um controle de permissões por papel (admin/consultor/cliente-leitura) ainda não foi implementado.
- "Esqueci a senha" gera uma senha nova, salva o hash e a envia em texto puro por e-mail via `smtplib` (SMTP configurado por variáveis de ambiente `SMTP_*`) — sem SMTP configurado, a funcionalidade fica indisponível com mensagem de erro clara, o resto do sistema funciona normalmente.

## 5. Acesso ao banco de dados

`backend/app/db.py` **não usa um driver Python de Postgres** (nem psycopg2, nem psycopg3, nem um ORM). Em vez disso, chama o cliente de linha de comando `psql` via `subprocess`, passando o SQL pela entrada padrão e recebendo o resultado como texto — cada `SELECT` é embrulhado numa consulta que devolve `json_agg(row_to_json(...))`, e o backend faz `json.loads()` no texto de volta. Essa escolha evita depender de um driver compilado (o que simplifica bastante o Dockerfile/deploy), ao custo de uma latência um pouco maior por chamada e da necessidade do pacote `postgresql-client` estar instalado no ambiente que roda o backend.

A interface pública de `db.py` tem quatro funções: `fetch_all(sql)`, `fetch_one(sql)`, `execute(sql)` e `execute_returning_one(sql, prelude="")` — esta última é usada quando o SQL precisa gravar um `RETURNING *` (ex: criar/editar um registro e devolver a linha resultante ao frontend) e aceita um `prelude` para rodar um `SET LOCAL` na mesma transação (usado para registrar o autor de uma mudança de status antes do `UPDATE` que dispara o trigger de histórico). A função `db.q(valor)` faz o escaping seguro de qualquer valor Python para literal SQL (incluindo dollar-quoting de texto), usada em toda concatenação de SQL do backend.

## 6. Banco de dados — tabelas e relacionamentos

O diagrama conceitual da hierarquia principal:

```
projetos
  └─ etapas
  └─ frentes_trabalho
  └─ atividades (etapa_id, frente_trabalho_id, tipo_atividade_elementar_id, atividade_pai_id)
       ├─ atividade_recurso (N:N com recursos)
       ├─ atividade_dependencia (grafo de precedência, para o CPM)
       ├─ atividade_requisito (N:N com requisitos_tr)
       ├─ atividade_relato (histórico de andamento / pendências)
       ├─ atividade_horas_dia (apontamento diário, por recurso — "Minhas atividades")
       ├─ historico_status_atividade (auditoria automática via trigger)
       └─ ciclos_migracao (execuções semanais de migração de dados)
  └─ requisitos_tr
  └─ marcos
  └─ riscos
  └─ calendario_util (feriados/exceções, usado no CPM)
  └─ relatorios_executivos (histórico dos relatórios de IA)
anexos (FK polimórfica: atividade | requisito | risco | marco | projeto)
recursos (global, sem projeto_id — a mesma pessoa atua em vários projetos)
usuarios (global, login — independente de recursos)
parametros_site (singleton, identidade visual)
documentacao (singleton por tipo — este documento e o Documento Executivo)
```

### 6.1 Tabelas principais

**`projetos`** — uma implantação por linha. Campos-chave: `sigla`, `nome`, `cliente`, os cinco campos de interlocutores (`fiscal_projeto`, `gestor_projeto`, `gerente_projeto_cliente`, `gerente_projeto_consultoria`, `lider_projeto_consultoria`), datas (`data_abertura`, `data_inicio`, `data_inicio_real`, `data_fim_prevista`), `prazo_total_meses` e `horas_dia_util` (jornada padrão usada para converter horas em dias úteis no CPM). Desde a migração 025, cada um dos cinco interlocutores tem um `*_recurso_id` irmão (FK opcional a `recursos`, `ON DELETE SET NULL`): quando preenchido, o backend sobrescreve o nome com o cadastro do recurso a cada salvamento (fiscal/gestor/gerente do cliente esperam um recurso Cliente/Terceirizado; gerente/líder da Consultoria esperam um recurso Consultoria — checado em `preparar_projeto_interlocutores`, `main.py`); sem `*_recurso_id`, o nome continua sendo só texto digitado livremente (pessoa ainda sem cadastro de recurso).

**`etapas`** — `projeto_id` (FK, `ON DELETE CASCADE`), `numero`, `nome`. `UNIQUE (projeto_id, numero)`.

**`frentes_trabalho`** — `projeto_id` (FK cascade), `nome`, `cor_hex` (exibição), `ordem`, `ativo` (permite "aposentar" uma frente sem quebrar atividades já lançadas nela). `UNIQUE (projeto_id, nome)`.

**`tipos_atividade_elementar`** — vocabulário global (sem `projeto_id`), `nome` único, `ordem`, `ativo`.

**`recursos`** — global (sem `projeto_id`: a mesma pessoa pode atuar em vários projetos ao mesmo tempo). Campos: `nome`, `tipo_vinculo` (enum: Consultoria/Cliente/Terceirizado), `empresa`, `cargo`, `email`, `telefone`, `controla_horas` (boolean, default `true` — desde a migração 013, só recursos com esta marca podem apontar horas em "Minhas atividades"), `ativo`. Índice funcional `idx_recursos_email_lower` (`lower(email)`) acelera o casamento usuário-logado → recurso por e-mail.

**`atividades`** — a tabela central. `projeto_id` (FK obrigatória), `etapa_id`, `frente_trabalho_id` (FK opcionais desde a migração 026 — em branco quando a importação de cronograma não identifica com confiança um cadastro já existente pelo nome da atividade; a tela pede pra configurar depois), `tipo_atividade_elementar_id` (FK opcional), `atividade_pai_id` (auto-FK, `ON DELETE CASCADE` — hierarquia WBS), `requisito_tr_id` (FK opcional para `requisitos_tr`, criada por `ALTER TABLE` depois que a tabela de requisitos existe, por causa da ordem de criação), `codigo_wbs` (numeração hierárquica mantida pela aplicação), `origem_importacao_id` (id da tarefa na planilha de origem, usado para casar a mesma atividade entre reimportações), `nome`, `descricao`, `prazo_horas`, `horas_realizadas`, quatro datas (`dtini_prev`, `dtfim_prev`, `dtini_real`, `dtfim_real`), `percentual_concluido` (0–100), `status` (enum), `prioridade` (enum), sete colunas de resultado do CPM (`cpm_es_dias`, `cpm_ef_dias`, `cpm_ls_dias`, `cpm_lf_dias`, `cpm_folga_dias`, `cpm_critica`, `cpm_calculado_em`, mais as quatro datas de calendário equivalentes), `observacoes`, `eh_atividade_master` (marca os entregáveis mestres do projeto, usados pelo Relatório Executivo). Não existem mais campos fixos de responsável (`responsavel_consultoria_id`/`responsavel_cliente_id` foram removidos na migração 012) — quem participa de uma atividade vive só em `atividade_recurso`. Índices em `projeto_id`, `etapa_id`, `frente_trabalho_id`, `atividade_pai_id`, `status`, `dtfim_prev`, `dtini_prev`, `(projeto_id, origem_importacao_id)` e um índice parcial em atividades master.

**`historico_status_atividade`** — auditoria automática (nunca escrita manualmente pela aplicação): um trigger (`trg_atividades_historico`, função `registrar_historico_status_atividade()`) grava uma linha a cada `INSERT` ou a cada vez que `status` muda num `UPDATE`, guardando o status anterior, o novo e quem alterou (lido de `current_setting('app.usuario_atual', true)`).

**`atividade_relato`** — log de andamento, append-only (sem rota de `UPDATE`/`DELETE`). `autor_id` (FK opcional a `recursos`) ou `autor_nome` (texto livre, se o autor não estiver cadastrado). Quando `eh_pendencia = true`, exige (`CHECK`) `pendencia_responsavel_id`, `pendencia_prazo_possivel` e `pendencia_data_limite` preenchidos; guarda também `pendencia_resolvida`/`pendencia_resolvida_em`.

**`atividade_dependencia`** — arestas do grafo de precedência: `atividade_id` (sucessora), `predecessora_id`, `tipo` (enum FS/SS/FF/SF — convenção clássica de CPM), `lag_horas` (pode ser negativo, para antecipação). `CHECK (atividade_id <> predecessora_id)` e `UNIQUE (atividade_id, predecessora_id)` evitam auto-referência e duplicidade.

**`atividade_recurso`** — tabela de junção N:N entre `atividades` e `recursos`, chave primária composta `(atividade_id, recurso_id)`. Também guarda `papel_na_atividade` (texto livre) e `horas_alocadas` (opcional). É a **única** fonte de "quem participa" de uma atividade desde a migração 012, e também a base de quem enxerga a atividade em "Minhas atividades".

**`ciclos_migracao`** — uma linha por rodada semanal de migração de um tema (a atividade "pai" representa o tema, ex: "Migração de Pessoas"). `numero_ciclo`, `data_execucao`, `qtd_registros_extraidos`, `qtd_registros_carregados`, `qtd_rejeicoes`, e uma coluna gerada (`GENERATED ALWAYS AS ... STORED`) `percentual_rejeicao` calculada automaticamente pelo próprio Postgres. `UNIQUE (atividade_id, numero_ciclo)`.

**`requisitos_tr`** — `projeto_id`, `codigo` (único por projeto, ex: "TR-014"), `titulo`, `descricao` (texto literal do edital), `tipo_requisito` (Funcional/Não Funcional), `modulo_origem`, `frente_trabalho_id` (opcional), `classificacao` (enum: Essencial/Imediato, Obrigatório, Desejável, Customizado Curto/Médio/Longo), `atendimento` (enum: A analisar, Nativo, Parcial, Customizado, Não atende), `status` (enum de 8 estados, de "Não iniciado" a "Aprovado"/"Rejeitado"), `responsavel_id` (FK a `recursos`), `prioridade`, `cobranca` (Sim/Não/N-A), `data_levantamento`.

**`atividade_requisito`** — junção N:N entre `atividades` e `requisitos_tr` (chave composta), a rastreabilidade requisito ↔ atividades que o atendem.

**`marcos`** — `projeto_id`, `etapa_id` (opcional), `nome`, `data_prevista` (obrigatória), `data_real` (opcional), `origem_importacao_id` (mesmo propósito do de atividades, para marcos extraídos de tarefas de duração zero na importação de cronograma).

**`riscos`** — `projeto_id`, `descricao`, `categoria`, `probabilidade`/`impacto` (enum Baixo/Médio/Alto), `mitigacao`, `responsavel_id` (FK a `recursos`), `status` (Aberto/Mitigado/Encerrado), `identificado_em`.

**`calendario_util`** — exceções ao calendário padrão (segunda a sexta úteis) por projeto: uma linha por `(projeto_id, data)` marcando se aquele dia é útil ou não (feriados, recessos). Usada para converter os deslocamentos em dias úteis do CPM em datas de calendário reais, e para a distribuição diária de horas em "Minhas atividades".

**`anexos`** — metadados de arquivo (o conteúdo fica em disco, em `backend/uploads/`; aqui só o caminho). FK **polimórfica** validada na aplicação (não no banco): `entidade_tipo` (enum: atividade/requisito/risco/marco/projeto) + `entidade_id` (uuid solto, sem `REFERENCES` de banco, porque aponta para tabelas diferentes conforme o tipo).

**`relatorios_executivos`** — histórico dos relatórios gerados por IA no Dashboard: `conteudo_md` (o texto markdown devolvido pela IA), `dados_enviados` (jsonb — um retrato fiel dos dados/números que foram enviados no prompt, para auditoria: todo diagnóstico apresentado ao cliente pode ser rastreado até a origem), `modelo_ia`, `gerado_por`.

**`usuarios`** — login do sistema, **independente** de `recursos` (nem todo usuário de login é um recurso alocado em atividades, e vice-versa — o vínculo entre os dois é resolvido em tempo de consulta comparando e-mails, nunca por uma FK). `email` (único, case-insensitive via índice funcional), `nome`, `telefone`, `empresa`, `cargo`, `senha_hash`, `recebe_relatorio_executivo`/`recebe_notificacao_whatsapp` (reservados para uso futuro — nenhum envio automático existe ainda), `ativo`, `ultimo_login_em`.

**`parametros_site`** — tabela "singleton": sem constraint de banco forçando uma linha só, mas a aplicação sempre lê/edita a primeira linha e cria uma linha padrão automaticamente na primeira leitura, se ainda não existir nenhuma. Guarda `nome_empresa` e `logo_arquivo` (nome do arquivo em `backend/uploads/`).

**`atividade_horas_dia`** — a tabela por trás de "Minhas atividades": a quebra diária das horas previstas/confirmadas de uma atividade, **por recurso** (não por atividade sozinha — desde a migração 012, a granularidade é `(atividade_id, recurso_id, data)`, com `UNIQUE` composta, corrigindo um bug latente em que dois participantes lançando no mesmo dia se sobrescreviam). Gerada automaticamente (função `garantir_dias()` em `minhas_atividades.py`) na primeira vez que a grade de uma atividade é aberta por um participante específico: `prazo_horas` da atividade é dividido igualmente pelos dias úteis entre `dtini_prev` e `dtfim_prev` (respeitando `calendario_util`); a partir daí a grade não é regenerada automaticamente, mesmo que a atividade seja editada depois (limitação conhecida — mudanças grandes de planejamento após a geração exigem ajuste manual dia a dia). Colunas: `horas_previstas`, `horas_realizadas` (preenchida na confirmação), `confirmado`, `confirmado_em`, `confirmado_por` (FK a `usuarios`).

**`documentacao`** — guarda o conteúdo (markdown) deste Documento Técnico e do Documento Executivo, cada um identificado por `tipo` (`'executivo'` ou `'tecnico'`), com `titulo`, `versao` e `atualizado_em`. Exibida em Configurações → Documentação, com exportação em PDF via impressão do navegador (`window.print()`, sem geração de PDF no backend). Ver migração 014.

### 6.2 Tipos enumerados (vocabulário fechado)

`prioridade_enum` (Urgente/Alta/Média/Baixa) · `status_atividade_enum` (Não iniciada/Em andamento/Bloqueada/Concluída/Cancelada) · `tipo_dependencia_enum` (FS/SS/FF/SF) · `classificacao_tr_enum` · `atendimento_tr_enum` · `status_requisito_enum` · `cobranca_enum` (Sim/Não/N-A) · `tipo_requisito_enum` (Funcional/Não Funcional) · `tipo_vinculo_enum` (Consultoria/Cliente/Terceirizado) · `nivel_risco_enum` (Baixo/Médio/Alto) · `status_risco_enum` (Aberto/Mitigado/Encerrado) · `status_pendencia_enum` (Aberta/Em andamento/Resolvida/Cancelada) · `entidade_anexo_enum` (atividade/requisito/risco/marco/projeto).

### 6.3 Views

- **`vw_atividades_atrasadas`** — atividades com `dtfim_prev` no passado e status diferente de Concluída/Cancelada, já com `responsaveis_nomes` (agregação dos participantes via `string_agg`) e `dias_atraso` calculado.
- **`vw_caminho_critico`** — a última fotografia do caminho crítico calculado (`cpm_critica = true`), ordenada por `cpm_es_dias`.
- **`vw_resumo_frente`** — contagem de atividades por status, agrupada por frente de trabalho.

### 6.4 Triggers

- `set_atualizado_em()` — função genérica reaproveitada por várias tabelas (`projetos`, `atividades`, `requisitos_tr`, `riscos`, `atividade_horas_dia`) para atualizar `atualizado_em = now()` a cada `UPDATE`.
- `registrar_historico_status_atividade()` — grava o histórico de mudança de status de atividades (seção 6.1).

## 7. Fluxos técnicos principais

**Caminho crítico (CPM)** — `backend/app/cpm.py` monta um grafo dirigido (NetworkX) a partir de `atividade_dependencia`, converte a duração de cada atividade de horas para dias úteis (`prazo_horas / horas_dia_util`, arredondado para cima, mínimo 1 dia) e calcula início/fim mais cedo e mais tarde por atividade, marcando como crítica toda atividade com folga zero. Suporta integralmente dependências Fim-Início (FS, o caso predominante); SS/FF/SF são calculadas por aproximação da mesma fórmula. O resultado é persistido nas colunas `cpm_*` de `atividades` a cada recálculo (`POST /api/cpm/recalcular`).

**Importação de cronograma** — `backend/app/cronograma_import.py` lê planilhas Excel/CSV ou exportações de MS Project, casando cada linha com uma atividade já existente pelo `origem_importacao_id` (ou, na falta dele, pelo código WBS) — reimportar sempre atualiza em vez de duplicar, e nunca sobrescreve campos preenchidos manualmente (recurso, tipo, status Bloqueada/Cancelada, observações, item do TR). Tarefas de duração zero na planilha viram `marcos` em vez de `atividades`.

**Extração de requisitos do TR** — `backend/app/tr_parser.py` lê arquivos `.docx` (python-docx) ou `.pdf` (pypdf) e reconhece itens de requisito por padrões de texto (regras/regex, sem IA nesta etapa), devolvendo uma prévia para conferência humana antes de qualquer gravação (`/api/requisitos/importar-documento/preview` → `/confirmar`).

**Relatório Executivo (IA)** — `backend/app/relatorio_executivo.py` monta um retrato em JSON do estado atual do projeto (atividades, prazos, riscos, pendências, ritmo de execução) e envia como prompt para a API da Anthropic (variável `ANTHROPIC_API_KEY`); o texto de resposta (markdown) e o retrato de dados enviado são persistidos em `relatorios_executivos`, para que qualquer afirmação do relatório possa ser auditada até a origem. Sem a chave configurada, a rota devolve um erro explicando o que falta, sem quebrar o resto do sistema. `relatorio_pdf.py` converte o texto em PDF (ReportLab) para download.

**Relatório de Horas dos Recursos** — `backend/app/relatorio_atividades.py` é 100% determinístico (nenhuma IA): lê `atividade_horas_dia` filtrando por período e, opcionalmente, por recurso, agrega em memória por recurso → atividade → dia com subtotais, e formata o mesmo dicionário em PDF (ReportLab, paisagem A4) ou planilha (openpyxl, abas "Resumo" e "Detalhe"). Nada é persistido — os filtros mudam a cada geração.

**"Minhas atividades"** — `backend/app/minhas_atividades.py` resolve o vínculo "usuário logado → recurso" comparando e-mails (case-insensitive), sem nenhuma FK entre `usuarios` e `recursos`. `recursos_do_usuario(email, apenas_com_controle_horas=True)` filtra por `controla_horas = true` por padrão; quando a grade dá "não vinculado", a mesma função (chamada de novo sem esse filtro) permite distinguir "nenhum recurso com este e-mail" de "recurso existe, mas sem controle de horas habilitado", para uma mensagem mais precisa ao usuário. `garantir_dias()` gera a quebra diária uma única vez por par (atividade, recurso); `ajustar_dia`/`confirmar_dia`/`desconfirmar_dia` são sempre autorizados pelo `recurso_id` do usuário logado, nunca por quem originalmente criou a linha.

## 8. API — rotas por módulo

Todas as rotas ficam sob o prefixo `/api/`, respondem e recebem JSON (exceto downloads de arquivo), e — com exceção de `/api/auth/login`, `/api/auth/registrar` e `/api/auth/esqueci-senha` — exigem sessão ativa.

- **Autenticação**: `POST /auth/registrar`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/esqueci-senha`, `POST /auth/trocar-senha`.
- **Usuários**: `GET/GET·id/PUT /usuarios`.
- **Projetos**: `GET/POST/PUT /projetos`.
- **Parâmetros do site**: `GET/PUT /parametros`, `POST/GET /parametros/<id>/logo`.
- **Configurações base**: `GET/POST/PUT/DELETE` para `/etapas`, `/frentes`, `/tipos-atividade`, `/recursos`.
- **Atividades**: `GET/POST /atividades`, `GET/PUT/DELETE /atividades/<id>`, `/atividades/<id>/historico`, `/atividades/<id>/relatos` (+ `PATCH /relatos/<id>/resolver`), `/atividades/<id>/dependencias` (+ `DELETE /dependencias/<id>`), `/atividades/<id>/recursos` (+ `DELETE .../recursos/<recurso_id>`), `/atividades/<id>/requisitos` (+ `DELETE .../requisitos/<id>`).
- **Minhas atividades**: `GET /minhas-atividades`, `PUT /minhas-atividades/dia/<id>`, `POST /minhas-atividades/dia/<id>/confirmar`, `POST .../desconfirmar`.
- **Requisitos TR**: `GET/POST/PUT/DELETE /requisitos`, `POST /requisitos/importar-csv`, `POST /requisitos/importar-documento/preview` e `/confirmar`.
- **Ciclos de migração**: `GET/POST /ciclos-migracao`.
- **Marcos**: `GET/POST/PUT /marcos`.
- **Importação de cronograma**: `POST /cronograma/importar/preview` e `/confirmar`, `GET /cronograma/resumo-remocao`, `POST /cronograma/remover-tudo`.
- **Riscos**: `GET/POST/PUT /riscos`.
- **Anexos**: `POST/GET /anexos`, `GET /anexos/<id>/download`, `DELETE /anexos/<id>`.
- **Relatórios**: `GET /relatorios/periodo`, `/atrasadas`, `/resumo-frentes`; Relatório Executivo (`GET/POST /relatorios/executivo...`, `GET .../pdf`); Relatório de Horas dos Recursos (`GET /relatorios/minhas-atividades/pdf` e `/planilha`).
- **Caminho crítico**: `POST /cpm/recalcular`, `GET /cpm/caminho-critico`.
- **Documentação**: `GET /documentacao`, `GET /documentacao/<tipo>`, `PUT /documentacao/<tipo>`.
- **Frontend estático**: `GET /` e `GET /<path:path>` servem os arquivos de `frontend/`.

## 9. Convenções do projeto

- **Migrações**: `db/schema.sql` é sempre o retrato completo e atual do banco; cada `db/migration_0NN_*.sql` é um script incremental, seguro para produção (`BEGIN;`/`COMMIT;`), aplicado uma vez e mantido para o histórico — nunca editado depois de já ter sido aplicado em produção. Toda mudança de schema atualiza os dois: a migração incremental e o `schema.sql` consolidado.
- **Nomenclatura**: tabelas, colunas, funções e nomes de variável no backend são em português (refletindo o domínio do negócio); só nomes de arquivos/módulos e algumas convenções técnicas genéricas ficam em inglês.
- **Sem ORM**: todo SQL é escrito à mão nos módulos do backend, usando `db.q()` para escaping seguro — não há migrations automáticas nem um mapeamento objeto-relacional.
- **Renomeações de rótulo vs. identificador**: quando um termo do domínio muda (ex: "Responsável" → "Recurso", 19ª rodada), a convenção adotada é renomear rótulos visíveis ao usuário e comentários de código, mas preservar identificadores internos já em uso (nomes de coluna do banco, ids de elemento HTML, chaves de campo JSON) — trocar um identificador interno amplamente usado é um risco de quebra desproporcional ao ganho cosmético, e pode ser feito à parte, deliberadamente, se um dia for necessário.

## 10. Deploy e ambientes

- **Local/self-hosted**: Docker Compose (`docker-compose.yml` para Postgres local; `docker-compose.supabase.yml` para apontar para um Supabase remoto durante o desenvolvimento).
- **Produção**: aplicação hospedada no Render (serviço web, deploy automático a cada push na branch `main`) e banco Postgres gerenciado no Supabase.
- **Variáveis de ambiente** (ver `.env.example`): credenciais do Postgres (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`), `SECRET_KEY` (sessão), `ANTHROPIC_API_KEY` (Relatório Executivo), `SMTP_*` (envio de e-mail do "esqueci a senha").
- **Sem dependência de driver nativo de banco** — só precisa de Python, das bibliotecas em `requirements.txt` e do binário `psql` disponível no `PATH` (já incluído no Dockerfile).

## 11. Um sistema em evolução

Este documento técnico é atualizado a cada rodada de desenvolvimento que altere o banco de dados, a arquitetura ou uma convenção do projeto — a versão e a data no topo indicam a última revisão. Para o histórico detalhado, rodada a rodada, de cada decisão técnica tomada, consulte a documentação de gestão técnica do projeto (mantida à parte, fora do próprio sistema).$doc$);

-- ============================================================================
-- 20. MANUAIS DO SISTEMA
-- ============================================================================
-- Catálogo GLOBAL de manuais do Sistema (não é por projeto — os mesmos manuais
-- valem pra qualquer implantação). Cada manual é um PDF guardado em base64
-- direto no banco (mesmo padrão já usado pra logo/documentação — o Render de
-- produção não tem disco persistente entre deploys, então arquivo em disco
-- não sobrevive). O texto de cada página é extraído no upload e guardado à
-- parte em manual_paginas, pra viabilizar a busca por palavra-chave.
CREATE TABLE manuais (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome            text NOT NULL,
  versao          text,
  arquivo_nome    text NOT NULL,
  arquivo_mime    text NOT NULL DEFAULT 'application/pdf',
  arquivo_dados   text NOT NULL,             -- conteúdo do PDF em base64
  tamanho_bytes   integer NOT NULL,
  paginas_total   integer NOT NULL DEFAULT 0,
  ativo           boolean NOT NULL DEFAULT true,
  enviado_em      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE manuais IS 'Catálogo global (não por projeto) dos manuais do sistema, usados na análise de Requisitos TR x Manuais (Configurações > Manuais do Sistema).';

CREATE TABLE manual_paginas (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  manual_id       uuid NOT NULL REFERENCES manuais(id) ON DELETE CASCADE,
  numero_pagina   integer NOT NULL,
  texto           text NOT NULL DEFAULT '',
  busca           tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', texto)) STORED,
  UNIQUE (manual_id, numero_pagina)
);
CREATE INDEX idx_manual_paginas_busca ON manual_paginas USING GIN (busca);
COMMENT ON TABLE manual_paginas IS 'Texto extraído por página de cada manual (via pypdf, sem OCR), usado na busca textual (tsvector/ts_rank) da tela Analisar Requisitos.';

-- ============================================================================
-- 21. REFERÊNCIAS DE REQUISITOS TR NOS MANUAIS
-- ============================================================================
-- Resultado (revisável) da análise "Analisar Requisitos": cada linha é uma
-- referência (manual + página + trecho) associada a um requisito. N:N porque
-- um requisito pode ter várias referências e nada impede duas referências
-- apontarem pro mesmo manual em páginas diferentes. `manual_nome` fica
-- congelado no momento da referência (não só via FK) pra a citação continuar
-- fazendo sentido mesmo se o manual de origem for excluído depois — por isso
-- manual_id usa ON DELETE SET NULL, não CASCADE.
CREATE TABLE requisito_referencia_manual (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  requisito_id    uuid NOT NULL REFERENCES requisitos_tr(id) ON DELETE CASCADE,
  manual_id       uuid REFERENCES manuais(id) ON DELETE SET NULL,
  manual_nome     text NOT NULL,
  pagina          integer NOT NULL,
  trecho          text,
  relevancia      real,
  origem          text NOT NULL DEFAULT 'busca' CHECK (origem IN ('busca', 'manual')),
  criado_em       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_requisito_referencia_requisito ON requisito_referencia_manual(requisito_id);
COMMENT ON TABLE requisito_referencia_manual IS 'Referências (manual + página + trecho) encontradas ou cadastradas manualmente pra cada requisito, na tela Analisar Requisitos. origem=busca (achada pela busca por palavra-chave) ou manual (adicionada à mão pelo consultor).';

-- ============================================================================
-- 22. FATURAS  (faturamento dos entregáveis do cronograma)
-- ============================================================================
-- Uma fatura está sempre atrelada a UMA atividade do cronograma marcada como
-- entregável (atividades.eh_entregavel = true — 26ª rodada); essa checagem é
-- feita no backend, não é um CHECK de banco (exigiria trigger pra olhar outra
-- tabela). responsavel_entrega_id precisa ser um recurso da consultoria (idem,
-- checado no backend). responsavel_recebimento pode ser um recurso cadastrado
-- (normalmente do cliente) OU só um nome digitado de alguém do cliente sem
-- cadastro — por isso responsavel_recebimento_nome/cargo são sempre
-- preenchidos como um "retrato" congelado no momento do salvamento (copiados
-- automaticamente do cadastro do recurso quando há um vinculado, ou digitados
-- livremente quando não há), e responsavel_recebimento_recurso_id usa
-- ON DELETE SET NULL — a fatura preserva nome/cargo mesmo que esse recurso
-- seja excluído depois, mesmo princípio já usado em
-- requisito_referencia_manual.manual_id/manual_nome.
CREATE TABLE faturas (
  id                                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id                          uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  atividade_id                        uuid NOT NULL REFERENCES atividades(id),  -- precisa ser um entregável (checado no backend)
  numero_fatura                       text NOT NULL,             -- alfanumérico (nº da nota fiscal/fatura)
  data_emissao                        date,
  data_envio                          date,
  data_pagamento_previsao             date,
  data_pagamento                      date,
  descricao                           text,
  responsavel_entrega_id              uuid NOT NULL REFERENCES recursos(id),   -- precisa ser recurso da consultoria (checado no backend)
  responsavel_recebimento_recurso_id  uuid REFERENCES recursos(id) ON DELETE SET NULL,
  responsavel_recebimento_nome        text,   -- nome do responsável pelo recebimento — copiado automaticamente do
                                       -- recurso vinculado, ou digitado livremente quando não há recurso cadastrado
  responsavel_recebimento_cargo       text,   -- idem: copiado automaticamente do cargo do recurso, ou digitado
  valor_total                         numeric(14,2) NOT NULL DEFAULT 0,
  impostos                            numeric(14,2) NOT NULL DEFAULT 0,
  valor_liquido                       numeric(14,2) GENERATED ALWAYS AS (valor_total - impostos) STORED,
  criado_em                           timestamptz NOT NULL DEFAULT now(),
  atualizado_em                       timestamptz NOT NULL DEFAULT now(),
  CHECK (responsavel_recebimento_recurso_id IS NOT NULL OR responsavel_recebimento_nome IS NOT NULL),
  UNIQUE (projeto_id, numero_fatura)
);
CREATE INDEX idx_faturas_projeto ON faturas(projeto_id);
CREATE INDEX idx_faturas_atividade ON faturas(atividade_id);
CREATE TRIGGER trg_faturas_atualizado_em BEFORE UPDATE ON faturas
  FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
COMMENT ON TABLE faturas IS 'Faturas emitidas para os entregáveis do cronograma (atividades.eh_entregavel = true), com prazos de envio/pagamento e valores. valor_liquido é sempre valor_total - impostos, calculado pelo próprio Postgres.';

-- ============================================================================
-- LOG DE AUDITORIA (32ª rodada) — acessos e alterações de dados
-- ============================================================================
-- Alimentada centralmente por insert_row/patch_row/delete_row (main.py, que já
-- canalizam quase toda criação/edição/exclusão do sistema) e pelas rotas de
-- autenticação (login/logout/tentativa malsucedida) — ver auditoria.py. Cada
-- linha é UM salvamento (não um campo por linha): um UPDATE que mexeu em 3
-- campos gera 1 linha com os 3 no `detalhes` (jsonb), não 3 linhas.
CREATE TABLE log_auditoria (
  id              bigserial PRIMARY KEY,     -- sequencial simples (ordem = tempo), tabela só cresce (append-only)
  criado_em       timestamptz NOT NULL DEFAULT now(),
  usuario_id      uuid REFERENCES usuarios(id) ON DELETE SET NULL,
  usuario_nome    text,             -- snapshot do nome no momento do evento — sobrevive a usuário desativado/renomeado depois
  usuario_email   text,             -- idem, útil em login_falho (não há usuario_id quando a senha/e-mail está errado)
  projeto_id      uuid REFERENCES projetos(id) ON DELETE SET NULL,  -- NULL = evento global (recurso, tipo de atividade, usuário, manual, login/logout)
  tipo_evento     text NOT NULL,    -- 'login' | 'login_falho' | 'logout' | 'senha_redefinida' | 'criacao' | 'edicao' | 'exclusao'
  entidade        text,             -- nome da tabela afetada (ex.: 'atividades') — NULL em login/logout
  entidade_id     text,             -- id do registro afetado — NULL em login/logout
  entidade_rotulo text,             -- identificação amigável do registro no momento do evento (ex.: nome da atividade) — sobrevive a exclusão/renomeação depois
  descricao       text NOT NULL,    -- frase pronta pra exibir na lista, já com os campos alterados
  detalhes        jsonb,            -- diff estruturado {campo: {rotulo, de, para}} — pra quem quiser abrir e ver exatamente o que mudou
  sensivel        boolean NOT NULL DEFAULT false,  -- mudança crítica sinalizada (prazo empurrado, exclusão, etc.) — ver auditoria.py
  ip              text
);
COMMENT ON TABLE log_auditoria IS 'Trilha de auditoria do sistema: acessos (login/logout/tentativa malsucedida) e alterações de dados (criação/edição/exclusão), com diff campo a campo quando aplicável. Alimentada centralmente por insert_row/patch_row/delete_row (main.py) e pelas rotas de autenticação — ver auditoria.py.';

CREATE INDEX idx_log_auditoria_projeto ON log_auditoria (projeto_id, criado_em DESC);
CREATE INDEX idx_log_auditoria_entidade ON log_auditoria (entidade, entidade_id, criado_em DESC);
CREATE INDEX idx_log_auditoria_usuario ON log_auditoria (usuario_id, criado_em DESC);
CREATE INDEX idx_log_auditoria_criado_em ON log_auditoria (criado_em DESC);

-- ============================================================================
-- 23. VERSÕES DO CRONOGRAMA (histórico sob demanda — migração 024)
-- ============================================================================
-- Cada linha é uma "foto" completa e independente do cronograma do projeto
-- num instante escolhido pelo usuário — NUNCA gerada automaticamente por uma
-- edição normal (criar/alterar/excluir atividade ou marco): só quando o
-- usuário clica no botão "Gerar versão" na tela de Cronograma. As edições do
-- dia a dia continuam apenas sobrepondo `atividades`/`marcos`, sem tocar
-- nesta tabela.
--
-- `dados` guarda o conteúdo já RESOLVIDO (nomes de etapa/frente/recursos,
-- não só os ids) para que a versão continue fazendo sentido mesmo que uma
-- etapa/frente/recurso seja depois renomeado ou excluído — ver
-- backend/app/cronograma_versoes.py, que monta esse jsonb reaproveitando o
-- mesmo ATIVIDADE_SELECT usado no resto do sistema.
CREATE TABLE cronograma_versoes (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  projeto_id        uuid NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  numero_versao     integer NOT NULL,   -- sequencial por projeto (1, 2, 3...), não reaproveitado
  rotulo            text,               -- rótulo opcional dado pelo usuário ao gerar (ex.: "Linha de base", "Antes da reunião de 10/09")
  dados             jsonb NOT NULL,     -- {atividades: [...], marcos: [...], dependencias: [...]} — já resolvido, ver acima
  total_atividades  integer NOT NULL DEFAULT 0,   -- contagens denormalizadas, só para listar sem carregar o jsonb inteiro
  total_marcos      integer NOT NULL DEFAULT 0,
  criado_em         timestamptz NOT NULL DEFAULT now(),
  criado_por        text,               -- nome do usuário logado que gerou (snapshot — ver X-Usuario)
  UNIQUE (projeto_id, numero_versao)
);
CREATE INDEX idx_cronograma_versoes_projeto ON cronograma_versoes (projeto_id, numero_versao DESC);
COMMENT ON TABLE cronograma_versoes IS 'Fotos completas do cronograma, geradas só sob ação explícita do usuário (botão "Gerar versão"). Nunca criadas automaticamente por CRUD de atividades/marcos.';

-- ============================================================================
-- Views de apoio a relatórios
-- ============================================================================

CREATE VIEW vw_atividades_atrasadas AS
SELECT a.*, e.numero AS etapa_numero, e.nome AS etapa_nome, f.nome AS frente_nome,
       (SELECT string_agg(r.nome, ', ' ORDER BY r.nome)
        FROM atividade_recurso ar JOIN recursos r ON r.id = ar.recurso_id
        WHERE ar.atividade_id = a.id) AS responsaveis_nomes,
       -- "Não iniciada" cujo início previsto já passou também é atraso, só que
       -- mais cedo no cronograma — nesse caso conta os dias a partir do início
       -- previsto (não do fim, que ainda pode nem ter vencido) — ver 29ª rodada.
       CASE
         WHEN a.status = 'Não iniciada' AND a.dtini_prev IS NOT NULL AND a.dtini_prev < CURRENT_DATE
              AND (a.dtfim_prev IS NULL OR a.dtfim_prev >= CURRENT_DATE)
           THEN CURRENT_DATE - a.dtini_prev
         ELSE CURRENT_DATE - a.dtfim_prev
       END AS dias_atraso
FROM atividades a
JOIN etapas e ON e.id = a.etapa_id
JOIN frentes_trabalho f ON f.id = a.frente_trabalho_id
WHERE a.status NOT IN ('Concluída','Concluída com atraso','Concluída com esforço maior',
                        'Concluída com atraso e esforço maior','Cancelada')
  AND (
    (a.dtfim_prev IS NOT NULL AND a.dtfim_prev < CURRENT_DATE)
    OR (a.status = 'Não iniciada' AND a.dtini_prev IS NOT NULL AND a.dtini_prev < CURRENT_DATE)
  );
COMMENT ON VIEW vw_atividades_atrasadas IS 'Atividades não concluídas/canceladas cujo fim previsto já passou, ou que nem começaram e já deveriam ter começado (início previsto no passado).';

CREATE VIEW vw_caminho_critico AS
SELECT a.*, e.numero AS etapa_numero, e.nome AS etapa_nome, f.nome AS frente_nome,
       (SELECT string_agg(r.nome, ', ' ORDER BY r.nome)
        FROM atividade_recurso ar JOIN recursos r ON r.id = ar.recurso_id
        WHERE ar.atividade_id = a.id) AS responsaveis_nomes
FROM atividades a
JOIN etapas e ON e.id = a.etapa_id
JOIN frentes_trabalho f ON f.id = a.frente_trabalho_id
WHERE a.cpm_critica = true
ORDER BY a.cpm_es_dias;
COMMENT ON VIEW vw_caminho_critico IS 'Última foto do caminho crítico calculado (ver endpoint /api/cpm/recalcular no backend).';

CREATE VIEW vw_resumo_frente AS
SELECT f.projeto_id, f.id AS frente_trabalho_id, f.nome AS frente_nome,
       count(a.id) AS total_atividades,
       count(*) FILTER (WHERE a.status IN ('Concluída','Concluída com atraso','Concluída com esforço maior',
                                            'Concluída com atraso e esforço maior')) AS concluidas,
       count(*) FILTER (WHERE a.status = 'Em andamento') AS em_andamento,
       count(*) FILTER (WHERE a.status = 'Não iniciada') AS nao_iniciadas,
       count(*) FILTER (
         WHERE a.status NOT IN ('Concluída','Concluída com atraso','Concluída com esforço maior',
                                 'Concluída com atraso e esforço maior','Cancelada')
           AND (
             (a.dtfim_prev IS NOT NULL AND a.dtfim_prev < CURRENT_DATE)
             OR (a.status = 'Não iniciada' AND a.dtini_prev IS NOT NULL AND a.dtini_prev < CURRENT_DATE)
           )
       ) AS atrasadas
FROM frentes_trabalho f
LEFT JOIN atividades a ON a.frente_trabalho_id = f.id
GROUP BY f.projeto_id, f.id, f.nome;
