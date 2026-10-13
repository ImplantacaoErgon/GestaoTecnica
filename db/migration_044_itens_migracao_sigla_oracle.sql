-- 163ª rodada: pedido do usuário (verbatim) -- "Tenho esse sql que fiz, ele
-- gera uma planilha [...] Como podemos ter essa query sendo executada pelo
-- nosso sistema, acessando ao banco de dados [...] A ideia é ter um botão
-- na aba Migração de Dados / Itens e Ciclos. Ao acionar essa query novos
-- ciclos seriam criados".
--
-- O Render (onde o sistema roda) não alcança a rede do cliente para rodar
-- a query Oracle direto -- mesma conclusão já registrada na investigação
-- de Comparação de Folha (ver doc do projeto
-- "oracle-conexao-direta-comparacao-folha.md"): o cliente não libera
-- firewall/Tailscale/IP fixo, e o host do Oracle só resolve dentro da rede
-- do cliente ou da VPN pessoal do usuário. O caminho adotado (confirmado
-- com o usuário via AskUserQuestion nesta rodada) repete o mesmo padrão: um
-- script local roda a query a partir da máquina do usuário (que já tem
-- acesso) e gera um CSV; um novo botão na aba Migração de Dados / Itens e
-- Ciclos importa esse CSV e cria/atualiza os ciclos -- ver
-- app/ciclos_migracao_import.py.
--
-- A SIGLA que vem do Oracle (ex: "MIGRA_PESSOAS") não bate com o texto já
-- cadastrado em "Tabela do legado" (ex: "01 - LEGADO.LEG_PESSOAS") -- o
-- usuário confirmou (mesmo AskUserQuestion) que prefere um campo novo e
-- dedicado pra esse mapeamento, preenchido manualmente uma vez por item, em
-- vez de renomear os itens já cadastrados. Nullable de propósito: todo item
-- de migração já cadastrado antes desta rodada fica sem SIGLA até alguém
-- preencher -- a importação simplesmente não encontra casamento pra ele
-- (reportado como "não mapeada" na prévia) até lá.
ALTER TABLE itens_migracao ADD COLUMN sigla_oracle text;

COMMENT ON COLUMN itens_migracao.sigla_oracle IS '163ª rodada: SIGLA do relatório de migração do Oracle do cliente (coluna mig_assunto_relat.sigla) que corresponde a este item -- usado para casar as linhas do CSV importado em /api/itens-migracao/importar-ciclos com o item certo. Preenchido manualmente (texto não bate com nome_tabela_legado). NULL = item ainda não mapeado, nenhuma linha do CSV casa com ele.';

-- Único por projeto (não globalmente) -- a mesma SIGLA Oracle não deveria
-- apontar pra dois itens do MESMO projeto (ambíguo, qual ciclo receberia a
-- linha do CSV?), mas projetos diferentes são implantações independentes e
-- nada impede a mesma SIGLA aparecer em cada um. Parcial (WHERE NOT NULL)
-- porque múltiplos itens sem SIGLA ainda (NULL) são o estado normal
-- enquanto o usuário não terminou de mapear tudo.
CREATE UNIQUE INDEX idx_itens_migracao_sigla_oracle ON itens_migracao(projeto_id, sigla_oracle) WHERE sigla_oracle IS NOT NULL;
