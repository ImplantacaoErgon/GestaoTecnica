-- 68ª rodada — "o cronograma sempre deve ser apresentado ordenado pelo
-- campo código". Até aqui, todo ORDER BY codigo_wbs no sistema comparava o
-- campo como TEXTO puro — o que ordena errado assim que um segmento passa
-- de um dígito: "1.2.10" vem ANTES de "1.2.2" numa comparação de texto
-- ("1"<"2" no terceiro caractere), quando o certo é "1.2.2" antes de
-- "1.2.10". Isso ficou mais visível agora que a função "Renumerar" (67ª
-- rodada, cronograma_renumeracao.py) pode gerar sequenciais de dois
-- dígitos dentro do mesmo grupo Etapa/Frente.
--
-- Esta função quebra o código em segmentos (separados por "." ou "-") e
-- devolve uma chave de TEXTO com cada segmento numérico preenchido com
-- zeros à esquerda (8 dígitos) — a comparação de texto normal do Postgres
-- já ordena certo a partir dessa chave, sem precisar de CAST pra inteiro
-- (que quebraria a consulta inteira caso algum código tivesse uma parte
-- não numérica — nunca deveria acontecer com o esquema novo, mas pode
-- existir em projetos antigos ainda não renumerados).
CREATE OR REPLACE FUNCTION codigo_wbs_chave_ordenacao(codigo text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE WHEN codigo IS NULL OR codigo = '' THEN NULL ELSE
    (SELECT string_agg(
       CASE WHEN seg ~ '^[0-9]+$' THEN lpad(seg, 8, '0') ELSE seg END,
       '.' ORDER BY ord
     )
     FROM unnest(regexp_split_to_array(codigo, '[.\-]')) WITH ORDINALITY AS t(seg, ord))
  END;
$$;
COMMENT ON FUNCTION codigo_wbs_chave_ordenacao(text) IS
  'Chave de ordenação NATURAL (numérica por segmento) do Código WBS — usar em ORDER BY codigo_wbs_chave_ordenacao(codigo_wbs) no lugar de ORDER BY codigo_wbs puro, que ordena como texto.';
