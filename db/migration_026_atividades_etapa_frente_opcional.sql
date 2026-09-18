-- Migração 026 (37ª rodada): Etapa e Frente de trabalho deixam de ser
-- obrigatórias em `atividades`.
--
-- Contexto: a importação de cronograma (cronograma_import.py) agrupava as
-- atividades em Etapa/Frente a partir da própria estrutura do WBS da
-- planilha (nível 1 = Etapa, nível 2 com filhos = Frente) e, quando não
-- havia uma Etapa/Frente já cadastrada com nome parecido, CRIAVA uma nova
-- automaticamente a partir do texto daquele ramo do WBS — mesmo que esse
-- texto não tivesse nada a ver com a taxonomia de Etapas/Frentes que o
-- cliente já usa em Configurações. Isso gerava grupos artificiais.
--
-- A partir desta rodada (ver cronograma_import.py), o casamento é feito
-- pelo NOME da própria atividade/ramo contra as Etapas/Frentes já
-- cadastradas; quando não há um casamento confiável nem o usuário escolhe
-- explicitamente "criar nova" na tela de importação, a atividade é
-- importada mesmo assim, só que SEM Etapa/Frente — para o usuário
-- configurar manualmente depois, olhando o que já existe em Configurações.
-- Isso só é possível com as duas colunas aceitando NULL.
--
-- Aditiva/segura: só remove a restrição NOT NULL, não apaga nem altera
-- nenhum valor já gravado.
ALTER TABLE atividades ALTER COLUMN etapa_id DROP NOT NULL;
ALTER TABLE atividades ALTER COLUMN frente_trabalho_id DROP NOT NULL;

COMMENT ON COLUMN atividades.etapa_id IS 'Etapa do WBS. Pode ficar em branco (migração 026) quando a importação de cronograma não identifica com confiança uma Etapa já cadastrada a partir do nome da atividade — nesse caso, configurar manualmente na tela do Cronograma.';
COMMENT ON COLUMN atividades.frente_trabalho_id IS 'Frente de trabalho. Pode ficar em branco (migração 026) pelo mesmo motivo de etapa_id.';
