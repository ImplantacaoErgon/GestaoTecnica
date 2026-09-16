-- ============================================================================
-- Migração 020b — views de apoio a relatório (29ª rodada)
-- PASSO 2 DE 2. Só rode este script DEPOIS que migration_020a_enum_status_
-- conclusao.sql já tiver rodado com sucesso (numa execução separada, já
-- commitada) — ver o comentário no topo daquele arquivo para o motivo.
-- ============================================================================
--
-- Views precisam parar de reconhecer só o literal 'Concluída' como
-- "atividade concluída", senão uma atividade concluída com atraso/esforço
-- maior passaria a contar como "não concluída" nelas (regressão, já que hoje
-- ela conta certo como 'Concluída'). Aproveitado para também reconhecer o
-- caso "atividade Não iniciada cujo início previsto já passou" como atraso —
-- antes só o Fim Previsto vencido contava; uma atividade que nem começou e
-- já deveria ter começado é um atraso tão real quanto, só que mais cedo no
-- cronograma.
--
-- DROP + CREATE em vez de CREATE OR REPLACE: as duas views usam `a.*` (todas
-- as colunas de atividades), e o Postgres não deixa "CREATE OR REPLACE"
-- mudar a posição/nome de colunas já existentes numa view — só permite
-- acrescentar colunas no final. Como a tabela atividades já ganhou colunas
-- novas desde que essas views foram criadas pela última vez em produção
-- (ex.: eh_entregavel), o `a.*` hoje expande em mais colunas do que quando a
-- view foi criada, empurrando as colunas seguintes (etapa_numero, etapa_nome
-- etc.) para posições diferentes — daí o erro `42P16: cannot change name of
-- view column`. Reproduzi isso isoladamente (tabela + view descartáveis,
-- ALTER TABLE ADD COLUMN, depois CREATE OR REPLACE VIEW) antes de aplicar a
-- correção. DROP + CREATE não tem essa restrição de posição — mesmo padrão
-- já usado nas migrações 004 e 012 para esta mesma view. Nada depende dessas
-- duas views (nenhuma outra view/função as referencia, só o backend via
-- `SELECT * FROM vw_...`), e não há GRANT específico sobre elas no
-- schema.sql, então não há CASCADE nem privilégio a perder ao recriar.
DROP VIEW IF EXISTS vw_atividades_atrasadas;
CREATE VIEW vw_atividades_atrasadas AS
SELECT a.*, e.numero AS etapa_numero, e.nome AS etapa_nome, f.nome AS frente_nome,
       (SELECT string_agg(r.nome, ', ' ORDER BY r.nome)
        FROM atividade_recurso ar JOIN recursos r ON r.id = ar.recurso_id
        WHERE ar.atividade_id = a.id) AS responsaveis_nomes,
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

DROP VIEW IF EXISTS vw_resumo_frente;
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

-- Nada a corrigir em dados existentes nesta migração — toda atividade já
-- gravada como 'Concluída' continua válida (é exatamente a variante "no
-- prazo e no esforço previsto"/"sem informação suficiente pra saber outra
-- coisa"); a aplicação só passa a reclassificar automaticamente a partir da
-- próxima vez que cada atividade for salva pela tela.
