-- ============================================================================
-- Dados de exemplo — Implantação do Sistema
-- Datas de referência: "hoje" = 2026-09-03 (data em que este exemplo foi montado)
-- ============================================================================

-- 1. Projeto -------------------------------------------------------------
INSERT INTO projetos (id, nome, cliente, descricao, data_inicio, data_fim_prevista, horas_dia_util) VALUES
('11111111-0000-0000-0000-000000000001', 'Implantação do Sistema', 'Prefeitura Exemplo',
 'Implantação do sistema (RH e Folha de Pagamento) — Etapa 1: folha completa.',
 '2026-06-01', '2027-09-30', 8.0);

-- 2. Etapas ----------------------------------------------------------------
INSERT INTO etapas (id, projeto_id, numero, nome, descricao, data_inicio_prev, data_fim_prev) VALUES
('11111111-0000-0000-0000-000000000101', '11111111-0000-0000-0000-000000000001', 1, 'Folha de Pagamento (completa)',
 'Parametrização, migração, customizações, integrações e programação da folha, de forma simultânea.', '2026-06-01', '2027-06-30'),
('11111111-0000-0000-0000-000000000102', '11111111-0000-0000-0000-000000000001', 2, 'Módulos Isolados',
 'Entrada gradual de módulos adicionais após a folha em produção.', '2027-07-01', '2027-12-31'),
('11111111-0000-0000-0000-000000000103', '11111111-0000-0000-0000-000000000001', 3, 'Operação Assistida',
 'Acompanhamento pós-produção.', '2028-01-01', '2028-03-31');

-- 3. Frentes de trabalho -----------------------------------------------------
INSERT INTO frentes_trabalho (id, projeto_id, nome, cor_hex, ordem) VALUES
('11111111-0000-0000-0000-000000000201', '11111111-0000-0000-0000-000000000001', 'Parametrização', '#2f5f8a', 1),
('11111111-0000-0000-0000-000000000202', '11111111-0000-0000-0000-000000000001', 'Migração de Dados', '#7a4fa0', 2),
('11111111-0000-0000-0000-000000000203', '11111111-0000-0000-0000-000000000001', 'Folha de Pagamento', '#1f7a4d', 3),
('11111111-0000-0000-0000-000000000204', '11111111-0000-0000-0000-000000000001', 'Customizações', '#a15c0d', 4),
('11111111-0000-0000-0000-000000000205', '11111111-0000-0000-0000-000000000001', 'Contagem de Tempo', '#0d8a99', 5),
('11111111-0000-0000-0000-000000000206', '11111111-0000-0000-0000-000000000001', 'Integrações', '#ab2f2f', 6),
('11111111-0000-0000-0000-000000000207', '11111111-0000-0000-0000-000000000001', 'eSocial', '#4a6b1f', 7),
('11111111-0000-0000-0000-000000000208', '11111111-0000-0000-0000-000000000001', 'Portal do Servidor', '#8a5ca1', 8),
('11111111-0000-0000-0000-000000000209', '11111111-0000-0000-0000-000000000001', 'Workflow', '#556b8a', 9),
('11111111-0000-0000-0000-000000000210', '11111111-0000-0000-0000-000000000001', 'BI', '#8a6d3a', 10),
('11111111-0000-0000-0000-000000000211', '11111111-0000-0000-0000-000000000001', 'Treinamentos', '#3a7a8a', 11),
('11111111-0000-0000-0000-000000000212', '11111111-0000-0000-0000-000000000001', 'Gestão', '#5a5a5a', 12);

-- 4. Tipos de atividade elementar (campo tabelado) --------------------------
INSERT INTO tipos_atividade_elementar (id, nome, ordem) VALUES
('11111111-0000-0000-0000-000000000301', 'Levantamento', 1),
('11111111-0000-0000-0000-000000000302', 'Parametrização', 2),
('11111111-0000-0000-0000-000000000303', 'Extração', 3),
('11111111-0000-0000-0000-000000000304', 'Carga', 4),
('11111111-0000-0000-0000-000000000305', 'Análise de Rejeição', 5),
('11111111-0000-0000-0000-000000000306', 'Especificação de Customização', 6),
('11111111-0000-0000-0000-000000000307', 'Programação', 7),
('11111111-0000-0000-0000-000000000308', 'Homologação', 8),
('11111111-0000-0000-0000-000000000309', 'Entrega', 9),
('11111111-0000-0000-0000-000000000310', 'Treinamento', 10),
('11111111-0000-0000-0000-000000000311', 'Teste', 11),
('11111111-0000-0000-0000-000000000312', 'Comparação de Folha', 12),
('11111111-0000-0000-0000-000000000313', 'Reunião', 13);

-- 5. Recursos ----------------------------------------------------------------
INSERT INTO recursos (id, nome, tipo_vinculo, empresa, cargo, email) VALUES
('11111111-0000-0000-0000-000000000401', 'Ana Consultoria', 'Consultoria', 'Consultoria', 'Consultora de Parametrização', 'ana@consultoria.example'),
('11111111-0000-0000-0000-000000000402', 'Bruno FSW', 'Consultoria', 'Consultoria', 'Desenvolvedor Fábrica de Software', 'bruno@consultoria.example'),
('11111111-0000-0000-0000-000000000403', 'Carla Folha', 'Consultoria', 'Consultoria', 'Consultora de Folha de Pagamento', 'carla@consultoria.example'),
('11111111-0000-0000-0000-000000000404', 'Diego Fábrica', 'Consultoria', 'Consultoria', 'Desenvolvedor de Integrações', 'diego@consultoria.example'),
('11111111-0000-0000-0000-000000000405', 'Eduardo CT', 'Consultoria', 'Consultoria', 'Consultor de Contagem de Tempo', 'eduardo@consultoria.example'),
('11111111-0000-0000-0000-000000000406', 'Fernanda BI', 'Consultoria', 'Consultoria', 'Consultora de BI', 'fernanda@consultoria.example'),
('11111111-0000-0000-0000-000000000407', 'Gestora de RH (Cliente)', 'Cliente', 'Cliente', 'Gestora de Recursos Humanos', 'rh@cliente.example'),
('11111111-0000-0000-0000-000000000408', 'Analista de TI (Cliente)', 'Cliente', 'Cliente', 'Analista de Sistemas', 'ti@cliente.example');

-- 6. Atividades ----------------------------------------------------------------
-- status/datas calibrados em torno de "hoje" = 2026-09-03
INSERT INTO atividades (id, projeto_id, etapa_id, frente_trabalho_id, tipo_atividade_elementar_id, codigo_wbs, nome, descricao,
                         prazo_horas, horas_realizadas, dtini_prev, dtfim_prev, dtini_real, dtfim_real, percentual_concluido, status, prioridade) VALUES
('11111111-0000-0000-0000-000000000501','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000201','11111111-0000-0000-0000-000000000301','1.1','Levantamento de Tipos de Vínculo','Levantamento das regras de vínculo do cliente (efetivo, comissionado, temporário...).',24,22,'2026-06-01','2026-06-05','2026-06-01','2026-06-04',100,'Concluída','Alta'),
('11111111-0000-0000-0000-000000000502','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000201','11111111-0000-0000-0000-000000000302','1.2','Parametrização de Tipos de Vínculo','Cadastro dos tipos de vínculo e validações no sistema.',32,38,'2026-06-08','2026-06-19','2026-06-08','2026-06-22',100,'Concluída','Alta'),
('11111111-0000-0000-0000-000000000503','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000201','11111111-0000-0000-0000-000000000301','1.3','Levantamento Grupo de Vencimento','Levantamento das regras de vencimentos e referências.',20,19,'2026-07-06','2026-07-10','2026-07-06','2026-07-09',100,'Concluída','Alta'),
('11111111-0000-0000-0000-000000000504','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000201','11111111-0000-0000-0000-000000000302','1.4','Parametrização de Vagas Numeradas','Controle de ocupação de vagas individuais por cargo.',40,28,'2026-08-25','2026-09-05','2026-08-25',NULL,70,'Em andamento','Alta'),
('11111111-0000-0000-0000-000000000505','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000201','11111111-0000-0000-0000-000000000302','1.5','Parametrização de Atributos','Atributos dependentes das regras de cálculo da folha (gratificações, adicionais).',48,NULL,'2026-09-08','2026-09-19',NULL,NULL,0,'Não iniciada','Alta'),

('11111111-0000-0000-0000-000000000506','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000202','11111111-0000-0000-0000-000000000303','2.1','Extração de Pessoas (legado)','Extração dos dados cadastrais de pessoas do sistema legado.',16,16,'2026-06-15','2026-06-17','2026-06-15','2026-06-17',100,'Concluída','Alta'),
('11111111-0000-0000-0000-000000000507','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000202','11111111-0000-0000-0000-000000000304','2.2','Carga de Pessoas','Carga e validação de pessoas no ambiente do sistema.',20,23,'2026-06-18','2026-06-24','2026-06-18','2026-06-25',100,'Concluída','Alta'),
('11111111-0000-0000-0000-000000000508','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000202','11111111-0000-0000-0000-000000000303','2.3','Extração de Eventos de Cargos','Extração de nomeações, progressões e demais eventos funcionais do legado.',24,15,'2026-08-18','2026-08-28','2026-08-18',NULL,60,'Em andamento','Alta'),
('11111111-0000-0000-0000-000000000509','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000202','11111111-0000-0000-0000-000000000304','2.4','Carga de Eventos de Cargos','Carga das tabelas espelho de eventos de cargos.',24,NULL,'2026-09-04','2026-09-10',NULL,NULL,0,'Não iniciada','Alta'),
('11111111-0000-0000-0000-000000000510','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000202','11111111-0000-0000-0000-000000000305','2.5','Análise de Rejeição de Vínculos','Análise semanal das rejeições de carga de vínculos.',16,NULL,'2026-09-11','2026-09-16',NULL,NULL,0,'Não iniciada','Média'),

('11111111-0000-0000-0000-000000000511','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000203','11111111-0000-0000-0000-000000000301','3.1','Levantamento de Regras de Vencimentos','Levantamento detalhado das rubricas de vencimento e unificação de códigos.',60,72,'2026-07-13','2026-08-07','2026-07-13','2026-08-12',100,'Concluída','Alta'),
('11111111-0000-0000-0000-000000000512','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000203','11111111-0000-0000-0000-000000000307','3.2','Programação Grupo Vencimentos Básicos','Programação C++ das rubricas do grupo de vencimentos básicos.',160,NULL,'2026-09-17','2026-10-08',NULL,NULL,0,'Não iniciada','Alta'),
('11111111-0000-0000-0000-000000000513','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000203','11111111-0000-0000-0000-000000000311','3.3','Testes Grupo Vencimentos Básicos','Testes unitários e liberação do grupo para comparação.',40,NULL,'2026-10-09','2026-10-16',NULL,NULL,0,'Não iniciada','Alta'),
('11111111-0000-0000-0000-000000000514','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000203','11111111-0000-0000-0000-000000000312','3.4','Comparação de Folha — ciclo 1','Primeira comparação Sistema x legado do grupo de vencimentos básicos.',16,NULL,'2026-10-19','2026-10-23',NULL,NULL,0,'Não iniciada','Alta'),

('11111111-0000-0000-0000-000000000515','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000204','11111111-0000-0000-0000-000000000306','4.1','Especificação Customização Bloco 1 (Contracheque QR)','Especificação funcional do QR Code de autenticação no contracheque.',30,26,'2026-08-20','2026-09-02','2026-08-20',NULL,80,'Em andamento','Média'),
('11111111-0000-0000-0000-000000000516','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000204','11111111-0000-0000-0000-000000000307','4.2','Desenvolvimento Customização Bloco 1','Desenvolvimento e testes internos da customização do contracheque.',60,NULL,'2026-09-07','2026-09-25',NULL,NULL,0,'Não iniciada','Média'),
('11111111-0000-0000-0000-000000000517','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000206','11111111-0000-0000-0000-000000000309','4.3','Entrega da Integração com Sistema Contábil','Entrega e homologação da integração de empenho/liquidação com o sistema contábil.',80,NULL,'2026-11-02','2026-11-20',NULL,NULL,0,'Não iniciada','Alta'),

('11111111-0000-0000-0000-000000000518','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000211','11111111-0000-0000-0000-000000000310','5.1','Treinamento Inicial Gestores e TI','Treinamento inicial de conceitos do Sistema para gestores e equipe de TI.',16,15,'2026-06-01','2026-06-03','2026-06-01','2026-06-03',100,'Concluída','Alta'),
('11111111-0000-0000-0000-000000000519','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000211','11111111-0000-0000-0000-000000000310','5.2','Treinamento Final Usuários da Folha','Treinamento dos usuários finais antes do paralelo real.',24,NULL,'2026-10-26','2026-10-30',NULL,NULL,0,'Não iniciada','Média'),

('11111111-0000-0000-0000-000000000520','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000207','11111111-0000-0000-0000-000000000307','6.1','Programação Conector eSocial S-2200','Conector do evento de admissão (S-2200).',40,34,'2026-08-03','2026-08-21','2026-08-03','2026-08-19',100,'Concluída','Alta'),

('11111111-0000-0000-0000-000000000521','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000205','11111111-0000-0000-0000-000000000301','7.1','Levantamento Regras de Licença-Prêmio','Regras de contagem de tempo para concessão de licença-prêmio.',20,NULL,'2026-09-14','2026-09-18',NULL,NULL,0,'Não iniciada','Média'),

('11111111-0000-0000-0000-000000000522','11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','11111111-0000-0000-0000-000000000212','11111111-0000-0000-0000-000000000313','8.1','Reunião de Status Mensal — Setembro','Apresentação de status às partes interessadas.',3,NULL,'2026-09-29','2026-09-29',NULL,NULL,0,'Não iniciada','Média');

-- 6b. Responsáveis por atividade (N:N — ver atividade_recurso) -----------------
-- A reunião de status (8.1) é o exemplo de atividade com múltiplos
-- participantes de ambos os lados (consultoria e cliente), como no caso de uso
-- que motivou a mudança: previsto/realizado da atividade continuam sendo um
-- número único (3h), independente de cada um dos 3 participantes apontar
-- suas próprias horas em "Minhas atividades".
INSERT INTO atividade_recurso (atividade_id, recurso_id) VALUES
('11111111-0000-0000-0000-000000000501','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000502','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000503','11111111-0000-0000-0000-000000000403'),
('11111111-0000-0000-0000-000000000504','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000505','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000506','11111111-0000-0000-0000-000000000408'),
('11111111-0000-0000-0000-000000000507','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000508','11111111-0000-0000-0000-000000000408'),
('11111111-0000-0000-0000-000000000509','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000510','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000511','11111111-0000-0000-0000-000000000403'),
('11111111-0000-0000-0000-000000000512','11111111-0000-0000-0000-000000000403'),
('11111111-0000-0000-0000-000000000513','11111111-0000-0000-0000-000000000403'),
('11111111-0000-0000-0000-000000000514','11111111-0000-0000-0000-000000000403'),
('11111111-0000-0000-0000-000000000515','11111111-0000-0000-0000-000000000402'),
('11111111-0000-0000-0000-000000000516','11111111-0000-0000-0000-000000000402'),
('11111111-0000-0000-0000-000000000517','11111111-0000-0000-0000-000000000404'),
('11111111-0000-0000-0000-000000000518','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000519','11111111-0000-0000-0000-000000000403'),
('11111111-0000-0000-0000-000000000520','11111111-0000-0000-0000-000000000404'),
('11111111-0000-0000-0000-000000000521','11111111-0000-0000-0000-000000000405'),
('11111111-0000-0000-0000-000000000522','11111111-0000-0000-0000-000000000401'),
('11111111-0000-0000-0000-000000000522','11111111-0000-0000-0000-000000000407'),
('11111111-0000-0000-0000-000000000522','11111111-0000-0000-0000-000000000408');

-- 7. Dependências (grafo de precedência para o CPM) ---------------------------
INSERT INTO atividade_dependencia (atividade_id, predecessora_id, tipo) VALUES
('11111111-0000-0000-0000-000000000502','11111111-0000-0000-0000-000000000501','FS'),
('11111111-0000-0000-0000-000000000504','11111111-0000-0000-0000-000000000502','FS'),
('11111111-0000-0000-0000-000000000505','11111111-0000-0000-0000-000000000504','FS'),
('11111111-0000-0000-0000-000000000507','11111111-0000-0000-0000-000000000506','FS'),
('11111111-0000-0000-0000-000000000509','11111111-0000-0000-0000-000000000508','FS'),
('11111111-0000-0000-0000-000000000510','11111111-0000-0000-0000-000000000509','FS'),
('11111111-0000-0000-0000-000000000512','11111111-0000-0000-0000-000000000505','FS'),
('11111111-0000-0000-0000-000000000512','11111111-0000-0000-0000-000000000510','FS'),
('11111111-0000-0000-0000-000000000512','11111111-0000-0000-0000-000000000511','FS'),
('11111111-0000-0000-0000-000000000513','11111111-0000-0000-0000-000000000512','FS'),
('11111111-0000-0000-0000-000000000514','11111111-0000-0000-0000-000000000513','FS'),
('11111111-0000-0000-0000-000000000516','11111111-0000-0000-0000-000000000515','FS'),
('11111111-0000-0000-0000-000000000519','11111111-0000-0000-0000-000000000513','FS');

-- 8. Alocação de recursos extra (apoio, além do responsável principal) --------
INSERT INTO atividade_recurso (atividade_id, recurso_id, papel_na_atividade, horas_alocadas) VALUES
('11111111-0000-0000-0000-000000000512','11111111-0000-0000-0000-000000000401','Apoio em atributos', 20),
('11111111-0000-0000-0000-000000000509','11111111-0000-0000-0000-000000000408','Validação de dados', 8);

-- 9. Ciclos de migração (execuções semanais da atividade "Extração de Eventos de Cargos") ----
INSERT INTO ciclos_migracao (atividade_id, numero_ciclo, data_execucao, qtd_registros_extraidos, qtd_registros_carregados, qtd_rejeicoes, observacoes) VALUES
('11111111-0000-0000-0000-000000000508', 1, '2026-08-18', 4200, 3550, 650, 'Rejeições concentradas em cargos comissionados sem tipo de evento parametrizado.'),
('11111111-0000-0000-0000-000000000508', 2, '2026-08-25', 4230, 3980, 250, 'Ajuste na parametrização de tipos de evento reduziu rejeições.'),
('11111111-0000-0000-0000-000000000508', 3, '2026-09-01', 4250, 4190, 60, 'Restam rejeições de matrículas com vínculo ainda não migrado.');

-- 10. Requisitos do TR (migrados do protótipo anterior) ------------------------
INSERT INTO requisitos_tr (id, projeto_id, codigo, titulo, descricao, frente_trabalho_id, classificacao, atendimento, status, responsavel_id, prioridade, cobranca, data_levantamento) VALUES
('11111111-0000-0000-0000-000000000601','11111111-0000-0000-0000-000000000001','TR-014','Cadastro de Empresas e Subempresas com hierarquia orçamentária','O sistema deve permitir o cadastro de empresas e subempresas vinculadas, com estrutura orçamentária própria para cada uma.','11111111-0000-0000-0000-000000000201','Essencial/Imediato','Nativo','Aprovado','11111111-0000-0000-0000-000000000401','Alta','N/A','2026-06-10'),
('11111111-0000-0000-0000-000000000602','11111111-0000-0000-0000-000000000001','TR-027','Vínculo em empresa previdenciária distinta na aposentadoria','Registrar aposentadorias e pensões em empresa previdenciária diferente da empresa de origem do servidor.','11111111-0000-0000-0000-000000000201','Obrigatório','Parcial','Em levantamento','11111111-0000-0000-0000-000000000401','Alta','N/A','2026-07-01'),
('11111111-0000-0000-0000-000000000603','11111111-0000-0000-0000-000000000001','TR-058','Emissão de contracheque com QR Code de autenticação','Contracheque emitido pelo Portal do Servidor deve conter QR Code para validação de autenticidade.','11111111-0000-0000-0000-000000000208','Desejável','Customizado','Especificado','11111111-0000-0000-0000-000000000402','Média','Sim','2026-08-15'),
('11111111-0000-0000-0000-000000000604','11111111-0000-0000-0000-000000000001','TR-102','Cálculo de quinquênio proporcional em afastamentos','Rubrica de quinquênio proporcional nos meses com afastamento sem direito integral.','11111111-0000-0000-0000-000000000203','Essencial/Imediato','Nativo','Em homologação','11111111-0000-0000-0000-000000000403','Alta','N/A','2026-07-20'),
('11111111-0000-0000-0000-000000000605','11111111-0000-0000-0000-000000000001','TR-133','Integração com sistema de ponto eletrônico biométrico','Receber diariamente marcações do ponto biométrico e gerar ocorrências de frequência automaticamente.','11111111-0000-0000-0000-000000000206','Obrigatório','Customizado','Em desenvolvimento','11111111-0000-0000-0000-000000000404','Alta','Sim','2026-08-01'),
('11111111-0000-0000-0000-000000000606','11111111-0000-0000-0000-000000000001','TR-145','Conector eSocial — evento S-2200 (admissão)','Gerar e transmitir o evento S-2200 para admissões.','11111111-0000-0000-0000-000000000207','Essencial/Imediato','Nativo','Aprovado','11111111-0000-0000-0000-000000000401','Alta','N/A','2026-07-25'),
('11111111-0000-0000-0000-000000000607','11111111-0000-0000-0000-000000000001','TR-167','Contagem de tempo para licença-prêmio','Contabilizar tempo de efetivo exercício com regras de interrupção por afastamento.','11111111-0000-0000-0000-000000000205','Obrigatório','Parcial','Não iniciado','11111111-0000-0000-0000-000000000405','Média','N/A','2026-09-01'),
('11111111-0000-0000-0000-000000000608','11111111-0000-0000-0000-000000000001','TR-201','Workflow de solicitação de férias com aprovação em cadeia','Solicitação de férias com aprovação em até três níveis hierárquicos.','11111111-0000-0000-0000-000000000209','Desejável','Customizado','Não iniciado','11111111-0000-0000-0000-000000000402','Baixa','Sim','2026-09-01'),
('11111111-0000-0000-0000-000000000609','11111111-0000-0000-0000-000000000001','TR-233','Dashboard gerencial de absenteísmo por secretaria','Painel de BI com indicadores de absenteísmo por secretaria e período.','11111111-0000-0000-0000-000000000210','Desejável','Nativo','Rejeitado','11111111-0000-0000-0000-000000000406','Baixa','N/A','2026-08-05'),
('11111111-0000-0000-0000-000000000610','11111111-0000-0000-0000-000000000001','TR-260','Migração de histórico de dependentes do legado','Migrar histórico completo de dependentes e suas dependências (início/fim de direito).','11111111-0000-0000-0000-000000000202','Obrigatório','A analisar','Em levantamento','11111111-0000-0000-0000-000000000401','Alta','N/A','2026-08-20');

-- Rastreabilidade requisito <-> atividade
INSERT INTO atividade_requisito (atividade_id, requisito_id) VALUES
('11111111-0000-0000-0000-000000000501','11111111-0000-0000-0000-000000000602'),
('11111111-0000-0000-0000-000000000502','11111111-0000-0000-0000-000000000602'),
('11111111-0000-0000-0000-000000000515','11111111-0000-0000-0000-000000000603'),
('11111111-0000-0000-0000-000000000516','11111111-0000-0000-0000-000000000603'),
('11111111-0000-0000-0000-000000000512','11111111-0000-0000-0000-000000000604'),
('11111111-0000-0000-0000-000000000517','11111111-0000-0000-0000-000000000605'),
('11111111-0000-0000-0000-000000000520','11111111-0000-0000-0000-000000000606'),
('11111111-0000-0000-0000-000000000521','11111111-0000-0000-0000-000000000607'),
('11111111-0000-0000-0000-000000000508','11111111-0000-0000-0000-000000000610'),
('11111111-0000-0000-0000-000000000509','11111111-0000-0000-0000-000000000610');

-- 11. Marcos ------------------------------------------------------------------
INSERT INTO marcos (projeto_id, etapa_id, nome, descricao, data_prevista, data_real) VALUES
('11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','Fim da Parametrização Base','Todos os temas base de parametrização concluídos.','2026-09-19',NULL),
('11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','Início da Programação da Folha','Kernel carregado e primeiras rubricas em programação.','2026-09-17',NULL),
('11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','Primeira Comparação de Folha','Primeiro ciclo de comparação Sistema x legado.','2026-10-23',NULL),
('11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000101','Migração Definitiva','Carga final de dados antes da virada em produção.','2027-05-15',NULL);

-- 12. Riscos --------------------------------------------------------------------
INSERT INTO riscos (projeto_id, descricao, categoria, probabilidade, impacto, mitigacao, responsavel_id, status) VALUES
('11111111-0000-0000-0000-000000000001','Atraso na entrega de extrações do sistema legado pela equipe de TI do cliente.','Migração de Dados','Médio','Alto','Definir cronograma formal de extrações com o analista de TI do cliente e escalar atrasos semanalmente.','11111111-0000-0000-0000-000000000408','Aberto'),
('11111111-0000-0000-0000-000000000001','Baixa disponibilidade dos usuários-chave do cliente para parametrização.','Parametrização','Alto','Alto','Negociar agenda dedicada com a gestão do cliente para os temas mais sensíveis.','11111111-0000-0000-0000-000000000407','Aberto'),
('11111111-0000-0000-0000-000000000001','Qualidade baixa dos dados de Eventos de Cargos no legado gerando alta taxa de rejeição.','Migração de Dados','Médio','Médio','Rotina de análise de rejeição semanal com plano de ação por tipo de erro.','11111111-0000-0000-0000-000000000401','Mitigado');

-- 13. Calendário útil (feriados nacionais/exceções) -----------------------------
INSERT INTO calendario_util (projeto_id, data, util, descricao) VALUES
('11111111-0000-0000-0000-000000000001','2026-09-07', false, 'Independência do Brasil'),
('11111111-0000-0000-0000-000000000001','2026-10-12', false, 'Nossa Senhora Aparecida'),
('11111111-0000-0000-0000-000000000001','2026-11-02', false, 'Finados'),
('11111111-0000-0000-0000-000000000001','2026-11-15', false, 'Proclamação da República');

-- 14. Item do TR direto na atividade (fase 1 do plano de sinal de execução) ------
-- Diferente da rastreabilidade N:N da seção 10 (uma atividade pode atender vários
-- requisitos) — aqui é o vínculo único e rápido usado no dia a dia da atividade.
UPDATE atividades SET requisito_tr_id = '11111111-0000-0000-0000-000000000604' WHERE id IN (
  '11111111-0000-0000-0000-000000000511','11111111-0000-0000-0000-000000000512',
  '11111111-0000-0000-0000-000000000513','11111111-0000-0000-0000-000000000514');
UPDATE atividades SET requisito_tr_id = '11111111-0000-0000-0000-000000000603' WHERE id IN (
  '11111111-0000-0000-0000-000000000515','11111111-0000-0000-0000-000000000516');
UPDATE atividades SET requisito_tr_id = '11111111-0000-0000-0000-000000000606' WHERE id = '11111111-0000-0000-0000-000000000520';
UPDATE atividades SET requisito_tr_id = '11111111-0000-0000-0000-000000000607' WHERE id = '11111111-0000-0000-0000-000000000521';

-- 15. Relatos de andamento (log temporal) ----------------------------------------
INSERT INTO atividade_relato (atividade_id, autor_id, texto, eh_pendencia, criado_em) VALUES
('11111111-0000-0000-0000-000000000504','11111111-0000-0000-0000-000000000401',
 'Concluída a parametrização das vagas do quadro efetivo; faltam as vagas comissionadas, previstas para amanhã.',
 false, '2026-09-02 17:10:00-03'),
('11111111-0000-0000-0000-000000000515','11111111-0000-0000-0000-000000000402',
 'Especificação revisada com a Gestora de RH; aguardando validação final do layout do QR Code no contracheque.',
 false, '2026-09-01 11:30:00-03');

INSERT INTO atividade_relato (atividade_id, autor_id, texto, eh_pendencia,
                               pendencia_responsavel_id, pendencia_prazo_possivel, pendencia_data_limite, criado_em) VALUES
('11111111-0000-0000-0000-000000000508','11111111-0000-0000-0000-000000000401',
 'Lista de rejeições da carga enviada ao cliente em 25/08 para validação; ainda sem retorno.', true,
 '11111111-0000-0000-0000-000000000408', '2026-09-08', '2026-09-15', '2026-08-28 09:00:00-03');
