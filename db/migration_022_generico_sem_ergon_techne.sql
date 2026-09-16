-- ============================================================================
-- Migração 022 — deixar o sistema genérico: remove os nomes "Ergon"/"Techne"
-- do modelo de dados (33ª rodada). Puramente estrutural (renomear enum
-- value + duas colunas de texto) — não apaga nem transforma nenhum dado.
-- ============================================================================
--
-- Contexto: o sistema estava "batizado" com os nomes do cliente real (Ergon,
-- o produto de RH/Folha que está sendo implantado) e da empresa consultora
-- (Techne) espalhados pelo modelo de dados, não só na interface. Pra virar
-- um sistema genérico de gestão de implantação (reutilizável com qualquer
-- nome de produto/consultoria), o valor 'Techne' do enum tipo_vinculo_enum
-- e as duas colunas *_techne de `projetos` precisam mudar de nome.
--
-- ALTER TYPE ... RENAME VALUE (ao contrário do ADD VALUE das migrações
-- 020a/020b) NÃO tem a restrição de "valor não comitado" — ele só troca o
-- rótulo (label) de um valor que já existe no catálogo, sem criar posição
-- nova nem exigir commit intermediário. Por isso pode rodar numa transação
-- só, junto com os ALTER TABLE RENAME COLUMN abaixo, com segurança. Rodar
-- este arquivo sozinho (sem misturar com outra migração) mesmo assim, só
-- por consistência com o padrão já adotado nas rodadas anteriores.
--
-- O rótulo muda, mas o valor interno (oid) do enum é o mesmo — então toda
-- linha de `recursos` que hoje tem tipo_vinculo = 'Techne' passa a mostrar
-- 'Consultoria' automaticamente, sem nenhum UPDATE de dado necessário.
-- O mesmo vale pro DEFAULT da coluna `recursos.tipo_vinculo` (é guardado
-- como valor do enum, não como texto — não precisa de ALTER COLUMN SET
-- DEFAULT depois disso).

ALTER TYPE tipo_vinculo_enum RENAME VALUE 'Techne' TO 'Consultoria';

ALTER TABLE projetos RENAME COLUMN gerente_projeto_techne TO gerente_projeto_consultoria;
ALTER TABLE projetos RENAME COLUMN lider_projeto_techne TO lider_projeto_consultoria;
