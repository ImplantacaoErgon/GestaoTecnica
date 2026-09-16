# Gestão de Projetos — Acompanhamento da Implantação

Banco de dados Postgres + backend (Flask) + frontend web para acompanhar
tecnicamente a implantação do sistema: cronograma por etapa/frente de
trabalho, requisitos do Termo de Referência, caminho crítico (CPM), riscos,
marcos, ciclos de migração e anexos.

Este projeto substitui o protótipo anterior (feito como site publicado pelo
Claude, com banco embutido de uso interno) por uma aplicação própria, com um
Postgres completo, para ser hospedada onde você quiser e acessada por toda a
equipe — Consultoria e cliente.

## Arquitetura

```
db/         schema.sql (estrutura completa) + seed.sql (dados de exemplo)
backend/    API Flask (Python) — fala com o Postgres via psql (ver app/db.py)
frontend/   site estático (HTML/CSS/JS puro, sem build step) servido pelo backend
docker-compose.yml   sobe banco + backend com um comando
```

Não há dependência de driver Python de Postgres (psycopg): o backend chama o
cliente `psql` via subprocess para executar SQL e receber o resultado em
JSON. Isso foi uma escolha, não um acaso — elimina a necessidade de compilar
extensões nativas e simplifica o deploy a "Python + psql instalados". Ver o
comentário no topo de `backend/app/db.py` se quiser trocar por um driver
nativo no futuro (a interface pública não precisaria mudar).

## Subindo com Docker (recomendado)

```bash
cp .env.example .env    # ajuste a senha do banco
docker compose up -d --build
```

O site fica em `http://localhost:8000`. Na primeira subida o Postgres roda
`db/schema.sql` automaticamente (banco vazio, sem dados de exemplo). Para
carregar os dados de exemplo (útil para conhecer a ferramenta):

```bash
docker compose exec -T db psql -U postgres -d app_db < db/seed.sql
```

## Usando Supabase como banco

1. Crie o projeto no Supabase (anote a senha do banco que você definir).
2. Abra o **SQL Editor** do projeto e rode o conteúdo de `db/schema.sql`
   inteiro (cria tabelas, tipos, funções, triggers, views — tudo). Não rode
   `db/seed.sql` lá a menos que queira os dados de exemplo também na nuvem.
3. No painel do projeto, clique em **Connect** (topo da página) e copie os
   dados da aba **Transaction pooler** (porta 6543) — é a opção recomendada
   para este backend, e funciona em qualquer host que só tenha IPv4 (a
   conexão direta do Supabase é IPv6-only por padrão).
4. Copie `.env.example` para `.env` e preencha `PGHOST`, `PGUSER` (no
   formato `postgres.<project-ref>`), `PGPASSWORD` e `PGDATABASE=postgres`
   conforme os comentários do próprio arquivo.
5. Suba só o backend, sem banco local:
   ```bash
   docker compose -f docker-compose.supabase.yml up -d --build
   ```
   (ou, sem Docker, exporte as mesmas variáveis do `.env` e rode `python run.py` — `run.py` já carrega o `.env` automaticamente via `python-dotenv`.)

Nota: o Supabase mostra um aviso no painel dizendo que as tabelas estão
"públicas" e sem Row Level Security (RLS). Isso é sobre a API REST
automática do Supabase (PostgREST) — como este backend conecta direto no
Postgres com o usuário `postgres` (que não é afetado por RLS), o aviso
pode ser ignorado, a menos que você também queira expor essas tabelas pela
API REST do Supabase para outra aplicação.

## Atualizando um banco que você já criou

Se você já rodou `db/schema.sql` uma vez (ex: no Supabase) e depois este projeto
ganhou campos/tabelas novas, **não rode `schema.sql` de novo** (ele daria erro
de "já existe"). Em vez disso, rode só o(s) arquivo(s) de migração pendentes,
na ordem do número, no SQL Editor do Supabase:

- `db/migration_002_cadastro_projeto.sql` — adiciona os campos do cadastro
  completo do projeto (sigla, fiscal, gestor, gerentes, datas, prazo) e libera
  anexar arquivos diretamente ao projeto.
- `db/migration_003_tipo_requisito_e_importacao_tr.sql` — adiciona o tipo do
  requisito (Funcional/Não Funcional) e o módulo de origem no TR, usados pela
  importação automática de requisitos a partir do documento do Termo de
  Referência (ver seção **Importação automática de requisitos a partir do
  documento do TR** abaixo).
- `db/migration_004_configuracoes_e_responsaveis.sql` — adiciona a prioridade
  "Urgente", separa Responsável Consultoria de Responsável cliente no cronograma,
  e prepara Responsáveis/Frentes/Tipos de Atividade para terem CRUD completo
  na nova tela de **Configurações** (ver seção dedicada abaixo).
- `db/migration_005_horas_realizadas_e_alertas.sql` — adiciona o campo de
  horas realizadas na atividade, usado para comparar com as horas previstas
  (ver seção **Horas previstas x realizadas** abaixo).
- `db/migration_006_relatos_e_requisito_tr_atividade.sql` — adiciona o vínculo
  opcional da atividade com um item do TR e a tabela de relatos de andamento
  (ver seção **Relatos de andamento e vínculo com o TR** abaixo).
- `db/migration_007_importacao_cronograma.sql` — adiciona `origem_importacao_id`
  em atividades e marcos, usado para reimportar uma planilha de cronograma
  atualizando o que já existe em vez de duplicar (ver seção **Importar
  cronograma (Excel/CSV) pela interface** abaixo).
- `db/migration_008_atividade_master_e_relatorio_executivo.sql` — adiciona a
  marcação de "atividade master" e a tabela de histórico do Relatório
  Executivo (IA) do Dashboard (ver seção **Relatório Executivo (IA)** abaixo).
- `db/migration_009_usuarios.sql` — cria a tabela de usuários com login (ver
  seção **Login e cadastro de usuários** abaixo). **Depois de rodar esta
  migração, configure `SECRET_KEY` no `.env`** (veja `.env.example`) — sem
  isso o sistema usa uma chave de sessão padrão insegura só para não quebrar.
- `db/migration_010_parametros_site.sql` — cria a tabela de parâmetros do
  site (logo e nome da empresa exibidos no topo do sistema — ver seção
  **Parâmetros do site** abaixo). Não precisa de nenhuma configuração
  extra no `.env`.
- `db/migration_011_horas_diarias_atividade.sql` — cria a tabela de horas
  diárias usada pela página **Minhas atividades** (ver seção dedicada
  abaixo). Não precisa de nenhuma configuração extra no `.env`.

Depois de rodar a migração, se estiver usando Docker, recrie o container do
backend pra ele pegar o código novo:
```bash
docker compose -f docker-compose.supabase.yml up -d --build --force-recreate
```

## Rodando sem Docker (desenvolvimento local)

Requer Postgres 14+ e `psql` no PATH.

```bash
createdb app_db
psql -d app_db -f db/schema.sql
psql -d app_db -f db/seed.sql        # opcional, dados de exemplo

cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export PGHOST=localhost PGUSER=postgres PGPASSWORD=... PGDATABASE=app_db
python run.py    # http://localhost:8000
```

## Login e cadastro de usuários

O acesso ao site inteiro exige login — não é possível navegar por nenhuma
tela nem chamar a API sem uma sessão autenticada. Isso é feito com um
cookie de sessão assinado pelo Flask (`SECRET_KEY`), sem depender de nenhum
serviço externo de autenticação.

### Decisões desta rodada

- **Autocadastro aberto** — qualquer pessoa que acessa o site pode se
  cadastrar sozinha ("Primeiro acesso / Criar cadastro" na tela de login),
  preenchendo todos os campos obrigatórios: e-mail, nome, telefone, empresa,
  cargo, senha, se quer receber os Relatórios Executivos e se quer receber
  notificações por WhatsApp. Ao concluir, o acesso já é liberado na hora —
  não existe aprovação nem confirmação por e-mail.
- **Sem perfil de administrador nesta rodada** — todo usuário autenticado
  tem o mesmo nível de acesso a todas as telas do sistema, inclusive à tela
  de Configurações > Usuários (onde qualquer um pode editar o cadastro de
  qualquer outro usuário, ou desativar o acesso de alguém marcando
  "Ativo" como desmarcado). Se no futuro for necessário restringir isso a
  só alguns usuários, dá pra evoluir adicionando um campo de perfil
  (ex: `eh_administrador`) e checando esse campo nas rotas sensíveis.
- **"Esqueci a senha" gera e ENVIA uma senha nova por e-mail** — não é um
  link de redefinição. Foi um pedido explícito: ao clicar, o sistema cria
  uma senha aleatória, salva o hash dela e manda o texto puro por e-mail;
  depois de entrar, dá pra trocar por uma senha de preferência em
  "Alterar minha senha" (clicando no próprio nome, no canto superior
  direito, já logado).
- **Tabela `usuarios` é independente de `recursos`** — `recursos` continua
  sendo só as pessoas responsáveis por atividades no cronograma (usado nos
  campos "Responsável Consultoria"/"Responsável cliente"); `usuarios` é quem tem
  login no sistema. Um usuário de login não precisa ser um recurso do
  cronograma, e vice-versa — são cadastros e telas separados de propósito.
- **Telefone e "recebe notificação WhatsApp" são só cadastro por enquanto**
  — nenhum envio de WhatsApp acontece ainda; é a base de dados para uma
  integração futura.

### Configuração necessária

**`SECRET_KEY`** (obrigatória antes de produção) — assina o cookie de
sessão. Sem ela configurada, o sistema usa uma chave padrão insegura (que
fica visível no próprio código-fonte) só para não quebrar — funciona para
testar, mas **qualquer pessoa com acesso ao código consegue forjar sessões**
nesse estado. Gere um valor com:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```
e cole em `SECRET_KEY` no `.env`.

**Envio de e-mail (`SMTP_*`)** — opcional, usado só por "Esqueci a senha".
Sem isso configurado, login e cadastro continuam funcionando normalmente;
só o botão "Esqueci a senha" devolve uma mensagem de erro explicando o que
falta configurar. Funciona com qualquer provedor SMTP (Gmail com senha de
app, Office 365, Amazon SES, SendGrid via SMTP relay etc.) — veja os
exemplos comentados em `.env.example`. Nenhuma biblioteca nova foi
adicionada ao `requirements.txt` para isso: hash de senha usa
`werkzeug.security` (já vem com o Flask) e envio de e-mail usa `smtplib`
(biblioteca padrão do Python) — não há dependência de pacote externo nem
de chave de API paga para o login funcionar.

### Rodando localmente / testando

Depois de rodar `db/migration_009_usuarios.sql` (ou `schema.sql` do zero)
e configurar `SECRET_KEY`, o próprio site mostra a tela de login/cadastro
na primeira visita — não precisa de nenhum usuário "seed". A primeira
pessoa a se cadastrar já pode usar o sistema inteiro normalmente.

## Parâmetros do site

Aba **Configurações > Parâmetros**, com dois blocos independentes:

- **Identidade visual** (logo + nome da empresa) — fica guardada na tabela
  `parametros_site` (um único registro, no mesmo estilo de `projetos`) e
  vale **para todos os usuários**: qualquer pessoa autenticada que mude o
  logo ou o nome da empresa muda o que todo mundo vê no topo do sistema
  (mesmo nível único de acesso das demais telas de Configurações — não há
  perfil de administrador nesta rodada, ver **Login e cadastro de
  usuários** acima). O logo aceita PNG, JPG, SVG, WEBP ou GIF, até 3 MB, e
  fica salvo em `backend/uploads/` (mesmo diretório dos anexos) — o
  arquivo antigo é apagado automaticamente ao enviar um novo. Se nenhum
  logo/nome estiver cadastrado, o espaço correspondente no topo simplesmente
  não aparece (não deixa um buraco vazio).
- **Aparência (tema da página)** — **decisão explícita**: ao contrário do
  logo/nome, o tema (Automático / Claro / Escuro) é uma **preferência
  pessoal de cada usuário**, salva só no navegador dela
  (`localStorage`, chave `appTema`) — não fica no banco de dados e não
  afeta o que os outros usuários veem. "Automático" (padrão) segue a
  preferência de claro/escuro do próprio sistema operacional/navegador,
  exatamente como o sistema já se comportava antes desta rodada; "Claro"
  e "Escuro" forçam o tema escolhido independentemente do que o sistema
  operacional preferir. A escolha é restaurada assim que a página carrega
  (antes mesmo do CSS principal), pra não piscar no tema errado por uma
  fração de segundo.

## Publicando em produção (Render)

O sistema está preparado para rodar no [Render](https://render.com) como um
**Web Service com Docker**, usando o Supabase como banco (ver "Usando
Supabase como banco" acima) — sem precisar de um segundo serviço só para o
Postgres.

### Por que o build do Docker mudou nesta rodada

O `backend/Dockerfile` sempre existiu, mas ele dependia de dois recursos que
só o `docker-compose` oferece e que o Render **não** tem: o volume
`./frontend:/frontend:ro` (que "empresta" a pasta `frontend/` para dentro do
container) e a porta fixa `8000`. Sem eles, a imagem builda mas o site fica
sem front-end e o Render não consegue detectar em qual porta o servidor está
escutando. Por isso:

- O contexto de build passou a ser a **raiz do repositório** (não mais só
  `backend/`) e o Dockerfile agora copia `frontend/` para dentro da própria
  imagem (`COPY frontend /frontend`) — funciona com ou sem o volume de
  desenvolvimento. Os dois `docker-compose*.yml` já foram atualizados para
  esse novo contexto (`context: .` / `dockerfile: backend/Dockerfile`); se
  você tiver algum script ou comando próprio rodando
  `docker build ./backend`, troque para
  `docker build -f backend/Dockerfile .` (a partir da raiz do projeto).
- O `CMD` do container agora escuta em `0.0.0.0:${PORT:-8000}` em vez de
  `8000` fixo — plataformas como o Render escolhem a porta pela variável de
  ambiente `PORT` (normalmente `10000`); localmente, sem essa variável
  definida, continua caindo em `8000` como antes.

Nenhuma dessas mudanças afeta quem já usa `docker compose up` localmente —
o comportamento é o mesmo de antes.

### Criando o serviço no Render

A ferramenta de automação usada para configurar isso só cria serviços Web
sem Docker — como este sistema depende de Docker (para instalar o
`postgresql-client` usado por `app/db.py`), a criação inicial do serviço
precisa ser feita pelo painel do Render:

1. Acesse https://dashboard.render.com/web/new e conecte o repositório do
   GitHub com o código deste projeto.
2. **Runtime**: Docker. **Dockerfile Path**: `backend/Dockerfile`. **Docker
   Build Context Directory**: deixe como a raiz do repositório (padrão).
3. Em **Environment Variables**, configure (mesmos nomes do `.env`, ver
   `.env.example`):
   - `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`,
     `PGSSLMODE=require` — dados de conexão do Supabase (aba "Transaction
     pooler" do botão **Connect** no painel do Supabase).
   - `SECRET_KEY` — gere com
     `python3 -c "import secrets; print(secrets.token_hex(32))"` e nunca
     reaproveite o valor de desenvolvimento.
   - `SESSION_COOKIE_SECURE=true` — o Render já serve tudo em HTTPS, então
     isso deve ficar ligado em produção (ver comentário no `.env.example`).
   - Opcionais: `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` (Relatório Executivo)
     e `SMTP_*` (e-mail de "Esqueci a senha") — sem eles, o resto do sistema
     funciona normalmente, só essas duas funcionalidades ficam indisponíveis.
4. Antes do primeiro acesso, rode `db/schema.sql` no SQL Editor do Supabase
   (se ainda não tiver feito isso).
5. Depois de criado, o serviço passa a poder ser monitorado/configurado
   também por chat (variáveis de ambiente, redeploys, logs), sem precisar
   voltar ao painel.

## Modelo de dados

Ver `db/schema.sql` — cada tabela tem `COMMENT ON TABLE` explicando seu
papel. Resumo:

| Tabela | O que guarda |
|---|---|
| `projetos` | Um projeto de implantação (permite reuso em outros clientes da Consultoria) |
| `etapas` | Etapa 1/2/3 da metodologia — CRUD em Configurações |
| `frentes_trabalho` | Parametrização, Migração de Dados, Folha de Pagamento, Treinamentos... — CRUD em Configurações, com campo `ativo` |
| `tipos_atividade_elementar` | Vocabulário controlado do tipo de trabalho (Levantamento, Extração, Carga, Especificação...) — CRUD em Configurações, com campo `ativo` |
| `recursos` | Responsáveis (pessoas) — nome, `tipo_vinculo` (Consultoria/Cliente/Terceirizado), empresa, cargo, e-mail, telefone, ativo — CRUD em Configurações |
| `atividades` | O cronograma em si — WBS hierárquica, `responsavel_consultoria_id`/`responsavel_cliente_id`, datas previstas/reais, `prazo_horas`/`horas_realizadas` (previsto x realizado), % concluído, resultado do CPM, `requisito_tr_id` (vínculo opcional e único com um item do TR, herdado nativamente por subatividades) |
| `atividade_relato` | Log temporal (append-only) de relatos de andamento por atividade; obrigatório ter ao menos um quando o status não é "Não iniciada"/"Concluída"; pode marcar pendência com responsável, prazo possível e data limite |
| `atividade_dependencia` | Grafo de precedência entre atividades (FS/SS/FF/SF + lag), usado no caminho crítico |
| `atividade_recurso` | Alocação de pessoas em atividades (N:N) |
| `ciclos_migracao` | Uma linha por execução semanal de migração de um tema (extração/carga/rejeições) |
| `requisitos_tr` | Cada item do Termo de Referência (classificação, tipo de atendimento, status) |
| `atividade_requisito` | Rastreabilidade requisito ↔ atividades que o atendem (N:N) |
| `marcos` | Marcos/milestones do projeto |
| `riscos` | Mapa de riscos |
| `calendario_util` | Feriados/exceções usadas para converter o CPM em datas de calendário |
| `anexos` | Metadados de arquivos (xls, csv, txt, pdf...) vinculados a qualquer entidade — o conteúdo fica em `backend/uploads/` |
| `historico_status_atividade` | Auditoria automática (trigger) de toda mudança de status de uma atividade |
| `usuarios` | Login no sistema (e-mail, senha com hash, telefone, empresa, cargo) — independente de `recursos`, ver seção **Login e cadastro de usuários** |
| `parametros_site` | Logo e nome da empresa exibidos no topo do sistema (tabela "singleton") — ver seção **Parâmetros do site** |
| `atividade_horas_dia` | Quebra diária das horas previstas/confirmadas de uma atividade — alimenta a página **Minhas atividades** |

Três views prontas para relatório: `vw_atividades_atrasadas`,
`vw_caminho_critico`, `vw_resumo_frente`.

## Caminho crítico (CPM)

`POST /api/cpm/recalcular` (botão "Recalcular caminho crítico" na aba
correspondente) percorre o grafo de dependências, calcula early/late
start/finish e folga de cada atividade (`backend/app/cpm.py`, usa
`networkx`), e grava o resultado nas colunas `cpm_*` de `atividades`.
Suporta dependências Fim-Início (FS) integralmente; SS/FF/SF são calculadas
por aproximação — reveja manualmente redes com muitas dependências desses
tipos.

As datas do CPM são **teóricas** (início do projeto + encadeamento das
durações) — mostram o prazo mínimo possível dado o que foi cadastrado. As
datas do dia a dia (que aparecem no cronograma e nos filtros de
semana/mês/trimestre) continuam sendo `dtini_prev`/`dtfim_prev`, que você
ajusta manualmente considerando disponibilidade de equipe, férias etc.

## O que a interface cobre hoje

- **Login e cadastro de usuários** — o acesso ao site exige login (ver seção **Login e cadastro de usuários** abaixo). Tela de "Primeiro acesso" para autocadastro, "Esqueci a senha" (gera e envia uma nova senha por e-mail), "Alterar minha senha" (logado) e uma tela em **Configurações > Usuários** para editar dados/desativar acesso de qualquer usuário cadastrado.
- **Cadastro do projeto** — na primeira vez que o site abre sem nenhum projeto no banco, ele pede o cadastro completo (sigla, nome, instituição, fiscal, gestor, gerentes de projeto do cliente e da Consultoria, líder da Consultoria, datas de abertura/início previsto/início real, prazo total em meses, jornada padrão) antes de liberar o resto da navegação. Depois de criado, os dados ficam acessíveis a qualquer momento pelo botão **⚙ Dados do projeto** no topo da tela, inclusive para anexar arquivos ao projeto (ex: o próprio Termo de Referência, contrato).
- **Dashboard** — KPIs gerais, progresso por frente, próximos marcos, atividades atrasadas, alerta de atividades Urgente/Alta sem responsável ou sem prazo, relatório de horas por responsável (ver seções **Horas previstas x realizadas** e **Alertas de prioridade sem dono/prazo** abaixo), botão **"↻ Atualizar"** para recarregar os números sem precisar dar F5 na página inteira, e o botão **"🧠 Gerar Relatório Executivo (IA)"** (ver seção **Relatório Executivo (IA)** abaixo).
- **Cronograma** — Gantt + tabela, filtros por período (semana/mês/trimestre ou datas), etapa, frente, status, **Responsável Consultoria** e **Responsável cliente** (dois campos separados — ver seção **Configurações** abaixo); cadastro de atividades com dependências, requisitos vinculados, anexos, **horas previstas/realizadas**, e a marcação **★ Atividade master** (usada pelo Relatório Executivo (IA) do Dashboard — ver seção dedicada). No formulário de atividade, Etapa e Frente de Trabalho aparecem primeiro, antes dos demais campos, já que definem onde a atividade se encaixa na estrutura do projeto.
- **Caminho crítico** — lista calculada via CPM, com botão de recálculo.
- **Requisitos do TR** — a mesma gestão do protótipo anterior, agora em Postgres, com filtros (inclusive por tipo Funcional/Não Funcional) e vínculo a atividades. Além do **cadastro manual** e da **importação por planilha CSV** (botão "Importar CSV" — há um botão "Baixar modelo CSV" ao lado com as colunas esperadas), dá para **importar direto do documento do Termo de Referência** (botão "Importar do TR (documento)") — ver seção dedicada abaixo. Reimportar (CSV ou documento) com um código já existente **atualiza** o requisito em vez de duplicar.
- **Riscos & Marcos** — leitura rápida (edição via API por enquanto — ver backlog abaixo).
- **Configurações** — cadastros base do projeto, com CRUD completo pela própria interface (antes só dava pra criar via SQL direto): Responsáveis, Etapas, Frentes de Trabalho, Tipos de Atividade Elementar, **Usuários** (edição/desativação — a criação em si é pelo autocadastro, ver **Login e cadastro de usuários**) e **Parâmetros** (logo, nome da empresa e tema da página — ver seção dedicada abaixo). Ver seção dedicada abaixo.
- **Minhas atividades** — planilha semanal para o consultor logado ajustar/confirmar as horas previstas de cada dia das atividades delegadas a ele (ver seção dedicada abaixo).

### Formato do CSV de requisitos

Colunas aceitas no cabeçalho (`;` ou `,` como separador, acentos e maiúsculas/minúsculas tanto faz no cabeçalho):

```
codigo*, titulo*, descricao, frente, classificacao, atendimento, status, prioridade, cobranca, data_levantamento, observacoes
```

Só `codigo` e `titulo` são obrigatórios. `frente` deve ser o **nome** de uma frente de trabalho já cadastrada no projeto — se não bater com nenhuma, o requisito é importado sem frente (aparece um aviso, mas a importação continua). Valores fora do vocabulário fechado de `classificacao`/`atendimento`/`status`/`prioridade`/`cobranca` caem no padrão em vez de travar a importação inteira.

## Configurações

Aba nova (**⚙ Configurações** no menu do topo) com CRUD completo — criar,
editar e excluir pela própria interface, sem precisar mexer no banco — para
os quatro cadastros base do projeto:

- **Responsáveis** — nome, **vínculo** (Consultoria / Cliente / Terceirizado — um
  vocabulário fechado, usado para filtrar quem aparece como "Responsável
  Consultoria" x "Responsável cliente" no cronograma), **empresa** (texto livre —
  o nome real da empresa/instituição, por exemplo "Consultoria", o nome do órgão,
  ou de uma terceirizada — não é mais só "Consultoria"/"Cliente"), **cargo**,
  e-mail, telefone e um marcador **ativo/inativo**.
- **Etapas** — número, nome, descrição, datas previstas de início/fim.
- **Frentes de Trabalho** — nome, descrição, cor (usada no Gantt), ordem de
  exibição e um marcador **ativo/inativo**.
- **Tipos de Atividade Elementar** — nome, descrição, ordem de exibição e um
  marcador **ativo/inativo**.

**Por que "ativo/inativo" em vez de só excluir:** tentar excluir um
Responsável/Etapa/Frente/Tipo que já está em uso em alguma atividade dá um
erro amigável explicando que existem registros vinculados, em vez de deixar
excluir e quebrar referências (ou de mostrar um erro cru de banco de dados).
Nesses casos, o caminho recomendado é desmarcar "Ativo" — o item continua
existindo (então o histórico não se perde), mas some das listas de "criar
nova atividade" enquanto continua aparecendo, marcado como inativo, em
qualquer atividade antiga que já apontava pra ele. Exclusão de verdade (o
botão "Excluir" no cadastro) só funciona pra itens que nunca foram usados em
nenhuma atividade.

### Responsável Consultoria e Responsável cliente no cronograma

Cada atividade agora tem dois campos de responsável, não um só:
**Responsável Consultoria** (quem da consultoria executa/acompanha) e
**Responsável cliente** (a contraparte do órgão que valida/participa) — os
dois usam o mesmo cadastro de Responsáveis, então dá pra escolher qualquer
pessoa cadastrada nos dois campos (o sistema não trava a escolha pelo
"vínculo" da pessoa, só usa o rótulo pra te ajudar a identificar quem é
quem). No formulário de atividade, **Etapa** e **Frente de Trabalho**
aparecem como os dois primeiros campos, antes do nome/descrição da
atividade — a ideia é sempre situar a atividade na estrutura do projeto
antes de detalhar o que ela é.

### Prioridade "Urgente"

A prioridade de atividades e requisitos agora tem 4 níveis:
**Urgente, Alta, Média, Baixa** (antes eram só 3, sem "Urgente").

## Horas previstas x realizadas

Cada atividade já tinha um campo de horas previstas (`prazo_horas` — a
estimativa de esforço feita no planejamento). Agora ela também tem
**horas realizadas** (`horas_realizadas`), preenchido conforme a execução
avança — como a realidade pode ficar acima ou abaixo do planejado, os dois
campos ficam lado a lado no formulário da atividade ("Horas previstas" /
"Horas realizadas") e a tabela do Cronograma ganhou uma coluna
**"Horas (prev./real.)"** mostrando os dois valores juntos, em vermelho
quando o realizado já passou do previsto e em verde quando está dentro ou
abaixo. Nenhum dos dois campos é obrigatório — uma atividade sem horas
lançadas simplesmente aparece com "—" ou só o valor previsto.

No Dashboard, o card **"Horas por responsável (previsto x realizado)"**
soma essas horas por pessoa (olhando tanto o campo Responsável Consultoria
quanto Responsável cliente de cada atividade), mostrando quantas atividades
a pessoa tem, o total de horas previstas, o total já realizado, e a
diferença. Um detalhe importante: a **diferença só é calculada sobre as
atividades que já têm horas realizadas lançadas** — comparar o realizado
com o previsto de tudo (incluindo atividades que nem começaram) faria
parecer que todo mundo está "muito abaixo do previsto" só porque o trabalho
ainda não foi executado, o que não é a mesma coisa que ter gastado menos
horas que o planejado. Passe o mouse sobre o valor da diferença para ver
sobre quantas atividades ele foi calculado.

## Relatos de andamento e vínculo com o TR

Esta seção resolve um problema real da implantação: o cronograma sozinho não
mostra o que está de fato acontecendo numa atividade (discussões travadas,
recursos não alocados, pendências do cliente...), e não deixa claro qual
item do Termo de Referência cada atividade está atendendo. É a fase 1 de uma
evolução maior — os relatos aqui registrados são o insumo que, numa fase
futura, uma IA vai analisar para gerar relatórios executivos com grau de
importância e recomendações (a IA **analisa e sinaliza para os gestores
decidirem — ela não coordena nem decide sozinha** o projeto).

**Item do TR na atividade** — cada atividade agora tem um campo opcional
"Item do TR" (aba Detalhes) apontando para um item cadastrado em
Requisitos do TR. Isso é diferente da aba "Requisitos vinculados" (que já
existia): aquela é uma relação N:N pensada para rastreabilidade completa
(um requisito pode ter várias atividades, e uma atividade pode atender
vários requisitos); este campo novo é um vínculo único e rápido para o uso
do dia a dia — "essa atividade é sobre qual item do TR, no geral?". Quando
uma atividade tem uma subatividade (`atividade_pai_id`), a subatividade
**herda automaticamente** o item do TR do pai no momento da criação (e
continua editável depois, independente do pai). A tabela do Cronograma
mostra o código do item logo abaixo do nome da atividade quando há vínculo.

**Relatos de andamento** — nova aba "Relatos" no formulário da atividade.
Qualquer pessoa (consultor da consultoria ou do cliente) pode registrar um relato
de texto livre a qualquer momento; os relatos ficam num **log temporal —
não podem ser editados nem apagados depois de criados**, só um novo relato
pode ser adicionado por cima. Regra de negócio: **toda atividade cujo
status seja "Em andamento", "Bloqueada" ou "Cancelada" precisa ter pelo
menos um relato registrado** para poder salvar essa mudança de status (não
se aplica a "Não iniciada" nem "Concluída"). Se você tentar salvar sem
relato, o sistema recusa com uma mensagem clara e leva você direto pra aba
Relatos.

**Pendências** — ao criar um relato, é possível marcar "É uma pendência"
quando o relato descreve algo que está impedindo o andamento da atividade.
Nesse caso o sistema exige três informações: **responsável** (quem precisa
agir para resolver), **prazo possível** (uma estimativa realista) e **data
limite** (o prazo máximo tolerável). Pendências abertas aparecem destacadas
na lista de relatos e podem ser marcadas como resolvidas com um clique
("Marcar como resolvida") — isso não apaga o relato, só marca a resolução e
quando ela ocorreu.

O que **não** foi implementado nesta fase (propositalmente, por decisão
conjunta): nenhum painel de anomalias automático e nenhuma geração de
relatório executivo por IA ainda — isso fica para as próximas fases, depois
que a base de relatos tiver volume suficiente para ser analisada.

## Alertas de prioridade sem dono/prazo

O Dashboard mostra um card de atenção (some automaticamente quando não há
nada a reportar) listando atividades de prioridade **Urgente** ou **Alta**,
ainda não concluídas/canceladas, que estão **sem Responsável Consultoria** ou
**sem data fim prevista**. A ideia é pegar esse tipo de atividade antes que
ela vire um problema — diferente do card de "Atividades atrasadas", que só
acusa depois que a data já passou (e nem acusa nada se a atividade não tem
data cadastrada). Clicar em uma linha do alerta abre a atividade
diretamente no Cronograma para completar o cadastro.

## Importação automática de requisitos a partir do documento do TR

Em vez de digitar (ou preparar um CSV com) cada item do Termo de Referência
na mão, o botão **"Importar do TR (documento)"** em Requisitos do TR lê o
próprio documento do TR (o anexo com o catálogo de funcionalidades e os
requisitos técnicos/não funcionais) e propõe a lista de requisitos prontos
para conferir e importar.

**Formatos aceitos:** HTML/HTM, TXT, DOCX e PDF (`backend/app/tr_parser.py`).
Não há chamada a nenhuma IA em tempo de execução, nem é preciso internet ou
chave de API — a leitura do arquivo usa bibliotecas comuns (`python-docx`,
`pypdf`, `html.parser` da própria linguagem) e a extração dos requisitos é
100% baseada em regras (regex sobre a estrutura do texto), pensada para o
padrão mais comum de TR de licitação pública de TI no Brasil:

1. Um **catálogo de funcionalidades** organizado em módulos numerados em
   maiúsculas (ex: "1. ABRANGÊNCIA E CARACTERÍSTICAS GERAIS...", "4. GESTÃO
   DA BASE FINANCEIRA E DA FOLHA DE PAGAMENTO..."), com itens numerados
   hierarquicamente dentro de cada módulo (2.2.1, 4.16.2.1...) — viram
   requisitos com código `RF-<numeração>` e `tipo_requisito = Funcional`.
2. Um **anexo de requisitos técnicos/não funcionais**, à parte (ex: "ANEXO
   I — REQUISITOS TÉCNICOS DA SOLUÇÃO"), organizado por categoria (uso,
   armazenamento, conformidade, desempenho, integração, disponibilidade...)
   — vira requisitos com código `RNF-<numeração>` e
   `tipo_requisito = Não Funcional`.

Cada requisito importado guarda também o **módulo de origem** (o caminho da
seção do TR de onde ele veio, ex: "4. GESTÃO DA BASE FINANCEIRA E DA FOLHA
DE PAGAMENTO > 4.29 Progressão funcional"), visível no campo "Módulo de
origem (do TR)" ao editar o requisito — útil pra rastreabilidade na hora de
alguém questionar de onde saiu aquele item.

**Fluxo de duas etapas (nada é gravado sem revisão):**

1. **Analisar documento** — envia o arquivo, o backend devolve a lista de
   itens encontrados (com tipo, módulo de origem, um trecho do texto e se o
   código já existe no projeto) sem gravar nada ainda. Se o documento não
   tiver a cara de um TR de requisitos (por exemplo, se por engano foi
   enviado o Edital em vez do Anexo/Termo de Referência com as
   especificações técnicas), aparece um aviso de baixa confiança pedindo
   pra conferir com atenção antes de prosseguir.
2. **Confirmar importação** — na tela de pré-visualização dá pra buscar,
   filtrar por tipo, e marcar/desmarcar itens individualmente (ou
   "Selecionar todos"); só os itens marcados são de fato inseridos/
   atualizados. Itens cujo código já existe no projeto vêm desmarcados por
   padrão (pra não sobrescrever à toa algo que já foi editado manualmente).

Todo item importado entra com status **"Não iniciado"** e atendimento **"A
analisar"** — nada é classificado automaticamente como atendido nesta fase.

Como a extração é heurística (não é uma IA lendo e interpretando o texto),
ela é boa em separar "isto é um item de requisito" de "isto é um título de
seção", mas não substitui a revisão humana antes de confirmar — principalmente
em TRs que fogem do padrão descrito acima (por exemplo, sem um anexo dedicado
de requisitos técnicos, ou com uma numeração de seções muito diferente). Nesses
casos os avisos da etapa de análise apontam o que não foi reconhecido.

## Importar cronograma (Excel/CSV) pela interface

Botão **"Importar Cronograma (Excel/CSV)"** na aba Cronograma — pensado para
o fluxo de reelaborar o cronograma no MS Project (replanejamento, ajuste de
datas, novas tarefas) e trazer a versão atualizada para dentro da Gestão de Projetos
sem precisar rodar nenhum script. Aceita a exportação em `.xlsx` do MS
Project ou um `.csv` com as mesmas colunas (`EDT, Id, Nome da tarefa,
Início, Término, Duração, % concluída, Predecessoras, Nomes dos
recursos` — a ordem das colunas não importa, o sistema casa pelo nome do
cabeçalho).

**A importação sempre atualiza o projeto atual — nunca cria um projeto
novo.** Cada linha da planilha é casada com a atividade/marco já existente
(pelo Id da tarefa no MS Project e, se ainda não tiver isso guardado, pelo
código WBS) e é **atualizada** em vez de duplicada; uma linha nova na
planilha vira uma atividade nova. Campos que só fazem sentido como edição
manual dentro do sistema — responsável, tipo de atividade elementar,
observações, item do TR vinculado, e um status "Bloqueada"/"Cancelada" já
definido manualmente — **nunca são sobrescritos** por uma reimportação; se
a planilha sugere avançar o status de uma atividade que está manualmente
Bloqueada/Cancelada no sistema, a importação preserva o status manual e
avisa em vez de sobrescrever.

Fluxo de duas etapas (mesmo padrão da importação de requisitos do TR):

1. **Analisar planilha** — envia o arquivo, o sistema devolve um resumo
   (quantas atividades/marcos são novos x já existem para atualizar,
   dependências, durações aproximadas) e a lista de grupos de Etapa/Frente
   que o WBS da planilha sugere, cada um já com uma sugestão de Etapa/Frente
   já cadastrada (por nome parecido) ou, se nenhuma bater, a opção de criar
   uma nova com o nome vindo da própria planilha (editável). Nada é gravado
   nesta etapa.
2. **Confirmar importação** — usa o mapeamento de Etapa/Frente que você
   conferiu/ajustou na tela para gravar de fato: cria as Etapas/Frentes que
   você marcou como novas, casa o resto com as que você selecionou, e então
   cria/atualiza as atividades, dependências e marcos.

**Por que conferir Etapa/Frente a cada importação, em vez de decidir isso
uma vez só:** a estrutura do WBS pode mudar de uma exportação pra outra
(uma fase renomeada, uma frente desmembrada em duas) — a tela de
conferência existe justamente pra pegar esses casos antes de gravar, em vez
de uma regra fixa que classificaria errado sem avisar.

Mesmas regras de conversão já validadas no script original (ver seção
abaixo): duração em dias/semanas corridos vira horas por aproximação
(×8h/dia); atividade "resumo" do WBS não recebe horas previstas; uma tarefa
de duração 0h vira Marco em vez de Atividade; toda atividade que a planilha
mostra como "em andamento" ganha um relato automático antes de ter o status
alterado.

Pré-requisito: o banco precisa já ter a `db/migration_007_importacao_cronograma.sql`
aplicada (além da `006`) — sem ela, a importação ainda funciona para
projetos criados do zero por este caminho, mas não reconhece atividades já
existentes vindas de importações anteriores como as mesmas ao reimportar.

**Dependência de sistema:** a leitura de `.xlsx` usa a biblioteca Python
`openpyxl` (`backend/requirements.txt`). Se você atualizou só os arquivos e
não reconstruiu a imagem Docker, rode `docker compose up -d --build` (ou
`docker compose -f docker-compose.supabase.yml up -d --build`, se o banco
for Supabase) — só copiar os arquivos por cima não instala a dependência
nova, e o backend fica reiniciando em loop até isso ser feito.

**Se a importação der "Erro de Requisição" ou carregar só uma parte das
atividades:** a causa mais provável é lentidão/instabilidade da conexão com
o banco (típico contra um Postgres remoto como o Supabase, se a rede
estiver ruim no momento). A importação grava em lotes e imprime o progresso
passo a passo — rode `docker compose logs backend --tail=100` (ou
`--follow` durante a importação) logo depois do erro: cada linha começando
com `[cronograma_import]` mostra em qual etapa (etapas/frentes/recursos,
atividades, dependências, marcos) e em qual lote a importação parou, e
qualquer erro de banco aparece com a mensagem completa do Postgres. Como a
importação é reentrante (reimportar sempre atualiza em vez de duplicar),
o caminho normal depois de um erro no meio é simplesmente refazer a
importação do mesmo arquivo — o que já tiver sido gravado antes do erro é
reconhecido e atualizado, não duplicado.

### Remover cronograma inteiro

Botão **"Remover cronograma inteiro"**, ao lado do botão de importar, na
mesma aba Cronograma. Apaga **todas** as atividades e marcos do projeto
atual — e, em cascata, tudo que pendura neles (dependências entre
atividades, relatos de andamento, histórico de status, vínculos com
requisitos do TR, ciclos de migração, anexos de atividade). Etapas, Frentes
de Trabalho, Requisitos do TR e as configurações do projeto **não** são
afetados — só o cronograma em si é limpo, para você reimportar do zero uma
versão nova (por exemplo, depois de uma reestruturação grande do WBS no MS
Project que tornaria o casamento com o que já existe mais confuso do que
útil).

É uma ação **irreversível e sem confirmação simples de "OK"**: a tela
mostra antes quantos registros de cada tipo serão apagados, e só libera o
botão de remoção depois que você digita, num campo de texto, o nome exato
do projeto (o mesmo mostrado no topo da tela) — conferido de novo no
servidor antes de apagar qualquer coisa, então nem uma automação nem um
clique duplo acidental conseguem disparar a remoção sem esse texto exato.
Ainda assim, **não existe desfazer**: use com cautela, e considere exportar
o cronograma atual (relatório em CSV, ou um backup do banco) antes de usar
este botão se houver qualquer dúvida.

## Relatório Executivo (IA)

Botão **"🧠 Gerar Relatório Executivo (IA)"** no topo do Dashboard. Gera, sob
demanda, um diagnóstico do projeto em linguagem de gestão (não técnica) —
progresso por frente, atrasos e bloqueios mais críticos, riscos abertos, e
uma **previsão de conclusão** da(s) atividade(s) marcada(s) como entregável
mestre do projeto (ex: "Folha definitiva"). O texto é escrito por um modelo
de IA (Anthropic Claude); os **números são calculados pelo backend**, não
pela IA — ver "Como a previsão é calculada" abaixo.

### Configuração (obrigatória para o botão funcionar)

1. Crie uma chave de API em [console.anthropic.com](https://console.anthropic.com)
   (aba "API Keys").
2. Cole a chave na variável `ANTHROPIC_API_KEY` do arquivo `.env` (veja
   `.env.example`). Sem isso, o botão continua aparecendo, mas devolve uma
   mensagem explicando o que falta configurar — o resto do sistema funciona
   normalmente.
3. Depois de editar o `.env`, suba de novo com `--build` para a imagem
   pegar a dependência nova (`anthropic`, adicionada ao `requirements.txt`):
   ```bash
   docker compose -f docker-compose.supabase.yml up -d --build
   ```
4. Cada relatório gerado consome créditos da conta Anthropic (cobrança por
   uso — veja o preço atual em [anthropic.com/pricing](https://www.anthropic.com/pricing);
   um relatório típico custa poucos centavos de dólar). O modelo usado é
   configurável pela variável `ANTHROPIC_MODEL` (padrão:
   `claude-sonnet-4-5-20250929`) — troque aqui se a Anthropic lançar um
   modelo mais novo depois desta implantação.

### O que sai do seu ambiente

Ao clicar em "Gerar relatório", os seguintes dados do **projeto de
implantação** (não da folha de pagamento real dos servidores do órgão) são
enviados à API da Anthropic para gerar o texto: nomes e códigos de
atividades, prazos, status, percentuais concluídos, nomes de responsáveis
Consultoria/cliente, descrições de riscos abertos. Isso é necessariamente
externo ao seu ambiente (Docker/Supabase) — só configure a chave da API se
esse envio já estiver autorizado pelo cliente. Cada relatório gerado fica
salvo (texto + retrato exato dos dados enviados) na tabela
`relatorios_executivos`, para auditoria: é possível abrir o histórico
("Relatórios anteriores" no topo do modal) e ver exatamente que números
embasaram cada diagnóstico já apresentado.

### Marcando a(s) atividade(s) master

No formulário de qualquer atividade, marque o campo **"★ Atividade master"**
para indicar que ela é um entregável mestre do projeto (o exemplo típico
aqui é a atividade de processamento da Folha Definitiva). Pode marcar mais
de uma. Atividades marcadas aparecem com ★ na lista do Cronograma. Sem
nenhuma atividade marcada, o relatório ainda é gerado, mas avisa que não há
como calcular uma previsão de conclusão até que pelo menos uma seja
marcada.

### Como a previsão é calculada

**Isto é calculado por código Python determinístico — a IA não inventa nem
recalcula essas datas, só interpreta e explica o resultado.** O motivo: um
modelo de linguagem não é confiável para aritmética de datas em cima de uma
tabela com centenas de linhas, e para um relatório apresentado a um órgão
público isso precisa ser auditável e sempre igual para os mesmos dados.

O algoritmo (em `backend/app/relatorio_executivo.py`, função
`_projetar_datas`) é uma extrapolação simples do ritmo de execução
observado, propagada pela cadeia de dependências:

- **Atividade concluída**: usa a data real de término — é fato, não
  previsão (nível de confiança "alta").
- **Atividade em andamento com % lançado**: calcula o ritmo até agora
  (% concluído ÷ dias úteis decorridos desde o início real) e projeta
  quando chegaria a 100% nesse ritmo (confiança "média" — é uma
  extrapolação linear, não considera se o ritmo vai acelerar ou piorar).
- **Atividade não iniciada**: sua data de início projetada é a mais tardia
  entre a data prevista no plano, "hoje" (se o início previsto já passou e
  ela nem começou) e a data de término projetada das suas predecessoras
  (respeitando o tipo de dependência FS/SS/FF/SF, do mesmo jeito que o
  motor de Caminho Crítico em `app/cpm.py`) — isso é o que faz um atraso
  em qualquer atividade **se propagar** até a atividade master, mesmo que
  ela própria ainda não tenha nenhum atraso direto.
- **Atividade bloqueada**: mesma lógica acima, mas marcada com confiança
  "baixa" — é um piso mínimo, não uma previsão real, porque não há como
  saber quando um bloqueio será resolvido.

O relatório também lista, para cada atividade master, se ela depende
(direta ou indiretamente) de alguma atividade **Bloqueada** — esse é o
sinal de risco mais concreto que existe num cronograma, porque só se
resolve com uma decisão humana, não com mais prazo.

Essa reprojeção é independente da tela **Caminho Crítico**: aquela é
calculada só a partir da duração planejada (nunca reage a atrasos já
ocorridos); esta reage ao que já aconteceu de verdade no cronograma. As
duas responderem coisas diferentes é intencional.

### Formato de saída

O relatório aparece na tela (dentro do próprio modal) e também pode ser
baixado em PDF (botão "⬇ Baixar PDF"), já formatado com cabeçalho, título
do projeto e um aviso no rodapé lembrando que é conteúdo gerado por IA e
que deve passar por revisão do gerente de projeto antes de qualquer
apresentação oficial ao cliente.

## Importação de um cronograma real do MS Project (Excel) — script de linha de comando

Este caminho por linha de comando continua útil para a **primeira carga**
de um cronograma muito grande antes de o projeto existir no sistema, ou
para rodar fora do navegador (ex: direto contra produção, de forma
não-interativa com `--yes`). Para reimportar uma versão atualizada de um
cronograma já carregado, prefira o botão **"Importar Cronograma"** da
interface acima — ele faz a mesma coisa mas atualiza o projeto existente em
vez de criar um novo a cada vez, e deixa você conferir a classificação de
Etapa/Frente antes de gravar.

`scripts/importar_cronograma_pcr.py` lê a exportação em Excel (`.xlsx`) de um
cronograma do MS Project e cria, via chamadas HTTP na própria API do Sistema
PM (não escreve direto no banco), o projeto inteiro: etapas, frentes de
trabalho, responsáveis, tipos de atividade elementar, atividades (com
hierarquia e dependências) e marcos.

**Por que via API e não SQL direto:** importar pela API garante que as
mesmas regras de negócio do sistema (por exemplo, a exigência de relato ao
marcar uma atividade como "Em andamento") são realmente testadas com dados
reais, e não contornadas.

Este script foi escrito e validado especificamente contra a estrutura do
cronograma real da implantação do PCR (`ModeloCronograma.xlsx`, exportação
do MS Project com EDT/WBS de 6 níveis). Ele assume um formato específico de
planilha (colunas `EDT, Id, Nome da tarefa, Início, Término, Duração, %
concluída, ..., Predecessoras, Nomes dos recursos, ...`) e decisões tomadas
para *este* cronograma:

- As **Etapas** do sistema são recriadas a partir do nível 1 do WBS
  (Planejamento/Iniciação/Execução/Encerramento), substituindo as 3 Etapas
  fictícias que viriam do `seed.sql`.
- Atividades com duração em **dias/semanas corridos** (em vez de horas) são
  convertidas multiplicando por 8h/dia — uma aproximação (duração de
  calendário não é a mesma coisa que esforço), aceita explicitamente para
  esta carga.
- Atividades "resumo" do WBS (nós que só agrupam subatividades) **não**
  recebem `prazo_horas` — a Duração do MS Project nesses nós é o prazo de
  calendário do ramo inteiro, não o esforço da atividade em si; usar esse
  valor infla os totais de horas por responsável.
- Uma tarefa com duração 0h vira um **Marco** (`/api/marcos`) em vez de uma
  atividade comum.
- Ao inferir o status "Em andamento"/"Bloqueada"/"Cancelada" a partir do %
  concluído, o script cria automaticamente um relato inicial antes de gravar
  o status, respeitando a regra de negócio descrita em **Relatos de
  andamento** acima.

Se o seu cronograma tiver uma estrutura de WBS diferente (outros níveis,
outras frentes), ajuste os dicionários no topo do arquivo antes de rodar —
principalmente `ETAPA_NOME` (nomes do nível 1) e `FRENTE_POR_EDT2` (qual
frente de trabalho corresponde a cada ramo do nível 2 do WBS).

**Pré-requisitos:**

```bash
pip install openpyxl requests
```

- O backend da Gestão de Projetos precisa estar rodando e acessível por HTTP.
- O banco precisa já ter `db/migration_006_relatos_e_requisito_tr_atividade.sql`
  aplicada — o script confere isso sozinho antes de importar (ver abaixo) e
  para com uma mensagem clara se faltar.

**Uso:**

```bash
python3 scripts/importar_cronograma_pcr.py --xlsx ModeloCronograma.xlsx
```

Por padrão aponta para `http://localhost:8000/api`; para importar direto
contra um backend em produção (ex: Supabase por trás de um domínio próprio),
use `--api-url`:

```bash
python3 scripts/importar_cronograma_pcr.py \
  --xlsx ModeloCronograma.xlsx \
  --api-url https://seu-dominio.com/api
```

Outras opções: `--sheet` (nome da aba, padrão `"Plan1"`), `--projeto-nome` /
`--projeto-cliente` (padrão "Implantação do Sistema" / "PCR"), `--yes`
(pula as confirmações interativas — útil em automação, mas use com cuidado),
`--relatorio` (caminho do JSON de resumo gerado ao final, padrão
`import_result.json`).

**Proteções de segurança embutidas:**

- **Checagem de migração antes de escrever:** o script faz uma chamada de
  teste à API antes de criar qualquer coisa; se a resposta indicar que
  `requisito_tr_id`/`atividade_relato` não existem (o erro exato que aparece
  se você tentar abrir o Cronograma num banco sem a migration_006), ele para
  com instruções em vez de falhar no meio da importação.
- **Detecção de projeto duplicado:** antes de criar o projeto, o script
  procura um projeto já existente com o mesmo nome + cliente. Se encontrar,
  avisa que rodar de novo cria um projeto duplicado (não atualiza o
  existente) e exige digitar `CONFIRMAR` para prosseguir mesmo assim (a
  menos que `--yes` tenha sido passado).
- Ao final, um relatório JSON é salvo (`import_result.json` por padrão) com
  contagens de atividades/dependências/marcos criados e a lista de avisos
  (ex: referências de predecessora não encontradas na planilha).

Validado localmente com o cronograma real do PCR: 300 atividades, 218
dependências e 1 marco criados a partir de 306 linhas da planilha, sem
erros, com a hierarquia, os responsáveis e os relatos automáticos
conferidos manualmente na interface depois da carga.

## Minhas atividades

Aba nova (**Minhas atividades** no menu do topo) — uma planilha semanal em
que cada consultor logado vê as atividades delegadas a ele e confirma ou
ajusta as horas trabalhadas dia a dia, em vez de só um total lançado de vez
em quando pelo gestor.

**Como o sistema sabe quais atividades são "suas":** o vínculo entre o seu
usuário de login e o seu cadastro de Responsável (`recursos`) é feito **por
e-mail** — se o e-mail com que você loga bate (sem diferenciar
maiúsculas/minúsculas) com o e-mail cadastrado em algum Responsável, todas
as atividades em que esse Responsável aparece como **Responsável Consultoria OU
Responsável cliente** aparecem aqui. Não existe um campo novo de "usuário x
responsável" pra cadastrar manualmente — é automático a partir do e-mail. Se
o seu e-mail de login não bate com nenhum Responsável, a tela avisa e
orienta a cadastrar (ou corrigir) o seu e-mail em **Configurações >
Responsáveis**.

**A grade mostra a semana atual** (segunda a sexta), com botões para navegar
pra semana anterior/seguinte e um atalho "Semana atual" para voltar. Só
aparecem atividades que têm **data de início/fim previstas e horas
previstas cadastradas** (sem isso não há como montar a grade) e cujo período
cruza com a semana em exibição.

**De onde vêm as horas previstas de cada dia:** na primeira vez que a grade
de uma atividade é aberta, o sistema divide o total de horas previstas da
atividade (`prazo_horas`) igualmente pelos dias úteis entre o início e o fim
previstos dela (pulando fins de semana e as exceções cadastradas no
calendário de dias úteis do projeto). A partir daí, essa quebra diária vira
a fonte de verdade daquela atividade — **editar o total geral ou as datas da
atividade depois não regera a grade automaticamente** (é uma limitação
conhecida: se o planejamento mudar muito depois que a grade já foi gerada,
pode ser necessário ajustar manualmente dia a dia).

**Em cada dia, você pode:**
- **Ajustar o número de horas previstas** — edite o campo e saia dele (ou
  aperte Enter); se aquele dia já estava confirmado, a confirmação é
  desfeita automaticamente (o número mudou, então a confirmação anterior não
  vale mais para ele).
- **Apenas confirmar a execução** — clique em "Confirmar" sem mudar nada: o
  valor exibido é gravado como horas realizadas daquele dia, o campo fica
  travado (fundo verde) e o botão vira "✓ Confirmado". Clicar de novo desfaz
  a confirmação e libera o campo pra editar.

Dias fora do período previsto da atividade aparecem com um traço, sem
campo — não tem o que lançar ali. Um feriado/exceção cadastrada no
calendário útil do projeto aparece com uma marcação "feriado" ao lado do
campo (o número ali é só o que a divisão automática calculou; nada impede
de zerá-lo manualmente se ninguém trabalhou naquele dia).

**Decisão de design importante:** nenhuma ação nesta tela toca o campo
`atividades.horas_realizadas` (o total agregado da atividade, já existente
desde a seção **Horas previstas x realizadas** e editável manualmente no
modal da atividade) — de propósito, pra não haver risco de uma confirmação
diária sobrescrever silenciosamente um valor que um gestor lançou por
outro caminho. As duas fontes (o total manual da atividade e a grade diária
de "Minhas atividades") convivem de forma independente por enquanto — ver
backlog abaixo para uma possível integração futura.

## Backlog sugerido para próximas sessões

- Formulários de criar/editar Riscos e Marcos na interface (hoje só leitura; a API já suporta POST/PUT).
- ✅ **Autenticação/login** — implementado nesta rodada (ver seção **Login e
  cadastro de usuários** acima). O que ficou de fora de propósito, por não
  ter sido pedido ainda: perfis/permissões diferenciadas (hoje todo usuário
  autenticado tem o mesmo acesso a tudo, inclusive à tela de Configurações >
  Usuários); confirmação de e-mail no cadastro (o acesso é liberado na hora,
  sem verificar se o e-mail informado existe de verdade); envio efetivo de
  notificações por WhatsApp (o cadastro já guarda telefone e a preferência,
  mas nenhum envio acontece ainda); e um log de auditoria de quem
  editou/desativou o cadastro de quem (hoje só fica o `ultimo_login_em`).
- Tela dedicada para lançar ciclos de migração semanais (`ciclos_migracao`) e ver a evolução da taxa de rejeição.
- Módulo de Rubricas da Folha de Pagamento (o levantamento com ~30 campos por rubrica, discutido antes de partirmos para o banco Postgres).
- Exportação de relatórios operacionais (ex: lista de atividades filtrada, resumo por frente) em PDF/Excel a partir das views prontas — hoje só o Relatório Executivo (IA) tem exportação em PDF (ver seção dedicada acima); os demais relatórios do Dashboard ainda são só leitura na tela.
- ✅ **"Minhas atividades" — grade semanal de horas por consultor** — ver
  seção dedicada acima. Ficou de fora de propósito: integrar a soma das
  horas confirmadas dia a dia com o total agregado
  `atividades.horas_realizadas` (hoje as duas coisas são independentes, ver
  decisão de design na seção acima); regenerar a grade diária quando o total
  geral ou as datas da atividade mudam depois que ela já foi gerada uma vez
  (hoje fica desatualizada em silêncio, sem aviso); mostrar o mês inteiro em
  vez de só a semana atual; e uma tela para o gestor ver a grade de **todos**
  os consultores de uma vez (hoje cada um só vê a própria).

**Sugestões de gerenciamento de projeto — o que já foi implementado e o que
ficou de fora** (as duas primeiras foram pedidas explicitamente numa rodada
seguinte e já estão na interface; as demais seguem como sugestão, pra não
inflar demais o escopo sem uma conversa antes):
- ✅ **Horas previstas x realizadas por atividade**, com relatório agregado
  por responsável no Dashboard — ver seção **Horas previstas x realizadas**
  acima. Ficou de fora dessa implementação a quebra por período/semana (hoje
  é só o total acumulado da atividade inteira) — se fizer falta pra
  identificar sobrealocação numa semana específica, dá pra evoluir depois
  usando `atividade_recurso.horas_alocadas` junto com lançamentos por data.
- ✅ **Alerta de atividade "Urgente"/"Alta" sem Responsável Consultoria ou sem
  data prevista** — card dedicado no Dashboard, ver seção **Alertas de
  prioridade sem dono/prazo** acima.
- ✅ **Relatos de andamento (log temporal) + vínculo opcional com item do
  TR, com herança nativa em subatividades** — ver seção **Relatos de
  andamento e vínculo com o TR** acima. Esta é a fase 1 de um plano maior
  discutido para lidar com o problema de fundo do cronograma virar "peça de
  ficção" (atividades que se alongam por discussões sem fim, recursos não
  alocados, situações anômalas difíceis de gerenciar). As próximas fases,
  ainda não implementadas de propósito (dependem da fase 1 estar em uso e
  acumular relatos reais primeiro):
  - **Fase 2 — radar de anomalias**: painel no Dashboard com regras
    determinísticas (não-IA) cruzando relatos, pendências vencidas/perto do
    limite e o histórico de status, pra destacar atividades "anômalas" sem
    precisar caçar isso manualmente atividade por atividade.
  - ✅ **Fase 3 — relatório executivo assistido por IA** — chegou antes das
    fases 1/2 (pedido explícito numa rodada seguinte), na forma do botão
    **"🧠 Gerar Relatório Executivo (IA)"** do Dashboard — ver seção
    **Relatório Executivo (IA)** acima. É gerado sob demanda (não periódico
    automático), e sempre como texto para um gestor humano revisar antes de
    apresentar ao cliente — nunca como decisão automática ou coordenação
    autônoma do projeto (mesma decisão de design já tomada aqui antes,
    reafirmada nessa rodada, dado o contexto de contrato público). Os
    NÚMEROS do relatório (previsão de prazo, atraso projetado) são
    calculados por código determinístico, não pela IA — só a redação do
    texto é gerada por IA. Ficou de fora dessa implementação: usar o
    acumulado de relatos de andamento (fase 1) como insumo do diagnóstico —
    hoje o relatório olha status/percentual/datas, mas não lê o texto dos
    relatos; e o radar de anomalias (fase 2) continua não implementado, então
    o relatório não aponta pendências vencidas especificamente por esse
    caminho (aparecem indiretamente via atividades atrasadas/bloqueadas).
- Validar que as datas de uma atividade caem dentro do período da sua Etapa
  (hoje não há essa checagem — dá pra cadastrar uma atividade com data fora
  da janela da etapa sem aviso nenhum).
- Aprovação/trilha de auditoria quando um Responsável ou Frente é marcado
  como inativo (hoje qualquer usuário pode fazer isso livremente).
- Vínculo formal entre Responsável e as Frentes de Trabalho em que atua (hoje
  qualquer responsável pode ser escolhido em qualquer atividade, mesmo sem
  relação com a frente — o rótulo com vínculo/cargo ajuda a identificar, mas
  não impede o cadastro incorreto).

## Onde ficaram os dados de exemplo

`db/seed.sql` popula 1 projeto, 3 etapas, 12 frentes, 22 atividades (com
dependências formando um caminho crítico de verdade), 10 requisitos, 3
riscos, 4 marcos, alguns vínculos de atividade com item do TR e alguns
relatos de andamento (incluindo uma pendência de exemplo) — todos com nomes
plausíveis para o contexto do sistema, mas fictícios. Apague-os (`DELETE FROM projetos;` — os `ON DELETE CASCADE`
cuidam do resto) antes de começar a cadastrar os dados reais, ou simplesmente
não rode `seed.sql` num banco de produção.
