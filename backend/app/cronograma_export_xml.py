"""
Exportação do Cronograma em XML MSPDI (Microsoft Project Data Interchange)
para importação no ProjectLibre — botão "Exportar XML (ProjectLibre)" na
tela de Cronograma, ao lado dos demais exportadores (Excel, Versões).

Pedido do usuário verbatim (69ª rodada): "crie em Cronograma um botão para
geração de xml para o project libre, não esqueça de incluir as dependências
e o % de conclusão."

Diferente do exportador Excel (cronograma_export.py), que serve para
manutenção em planilha e reimportação pelo próprio sistema, este XML serve
para ABRIR o cronograma em outra ferramenta (ProjectLibre, e por extensão
qualquer versão do MS Project que leia o mesmo formato) — não há caminho de
volta: nada deste sistema lê esse arquivo de novo.

Sempre exporta o projeto INTEIRO, sem os filtros de Etapa/Frente/Período que
o exportador Excel tem — um recorte parcial quebraria referências de
PredecessorLink apontando para atividades que ficaram de fora do arquivo.

## Formato (MSPDI)

Pesquisado na documentação oficial da Microsoft
(learn.microsoft.com/.../project/xml-data-interchange-schema-reference):

- Cada `<Task>` é um elemento "achatado" (sem aninhamento) — a hierarquia
  WBS é expressa por `OutlineLevel` (1, 2, 3...) e pela ORDEM em que as
  tarefas aparecem no arquivo, não por elementos filhos. Por isso a consulta
  que monta `atividades` (ver rota em main.py) usa a mesma ordenação natural
  por Código já usada no resto do sistema desde a 68ª rodada
  (`codigo_wbs_chave_ordenacao`) — como o esquema de numeração da 67ª rodada
  garante que o código de uma subatividade sempre começa com o código do
  pai (ex.: pai "1.2", filhos "1.2.1"/"1.2.2"), essa ordenação já entrega as
  tarefas na sequência DFS que o MSPDI espera (o código do pai, sendo
  prefixo/mais curto, sempre ordena antes dos filhos e antes do próximo
  irmão).
- `PredecessorLink/Type`: 0=Fim-Fim (FF), 1=Fim-Início (FS), 2=Início-Fim
  (SF), 3=Início-Início (SS).
- `LinkLag`: inteiro em DÉCIMOS DE MINUTO (1h de folga = 600).
- `Duration`/`Work`: tipo `xsd:duration`, forma léxica "PT{h}H{m}M0S".
- `Milestone`/`Summary`/`Critical`: booleanos MSPDI, gravados como "0"/"1"
  (forma léxica válida, mesma usada por arquivos gerados pelo MS Project).
- Uma atividade com filhas reais (`atividade_pai_id` de outra atividade
  apontando pra ela) vira `Summary="1"`. O ProjectLibre recalcula os totais
  de resumo sozinho a partir da hierarquia de OutlineLevel, então a
  duração/datas gravadas aqui pra ela são só um valor inicial de referência.
- `Milestone="1"` é reservado para os registros da tabela `marcos` (conceito
  próprio deste sistema, sem código/hierarquia/dependência) — nunca para
  `atividades`, mesmo as de duração curta, pra não inventar uma
  classificação que o cadastro não tem.
- Marcos viram tarefas soltas no fim do arquivo, `OutlineLevel=1`, sem
  predecessoras (o cadastro de Marco neste sistema não tem esse conceito).
- Escopo desta exportação: tarefas + hierarquia (WBS/OutlineLevel) +
  dependências + % concluído + datas/duração — o pedido do usuário não
  menciona recursos, e associar Resources/Assignments corretamente exigiria
  casar `recursos`/`atividade_recurso` com um segundo bloco do XML; fica de
  fora por ora para manter o escopo do pedido.

## Dois botões, dois namespaces (72ª/73ª rodada) — e a correção da 75ª

O mesmo XML MSPDI serve tanto pro ProjectLibre quanto pro Microsoft Project
de verdade — é o mesmo formato de intercâmbio — e o NAMESPACE raiz do
`<Project>` continua parametrizável (`namespace=` de `gerar_xml_bytes`), um
valor por botão, mas os dois valores hoje são iguais (ver abaixo) — o
parâmetro fica por precaução/documentação, não porque haja diferença real
hoje:

- `NAMESPACE_PROJECTLIBRE = "http://schemas.microsoft.com/project"` (SEM
  versão) — confirmado funcionando pelo usuário com o ProjectLibre (botão
  da 69ª rodada).
- `NAMESPACE_MS_PROJECT`: na 73ª rodada este valor tinha sido fixado como
  `".../project/2007"` (o `targetNamespace` do XSD oficial publicado pela
  Microsoft) por não haver, naquele momento, como testar a importação num
  MS Project de verdade. Na 75ª rodada o usuário testou o botão "Exportar
  XML (Microsoft Project)" no Project 2010 dele e reportou que o arquivo
  importava com Início/Duração/Término em branco e as tarefas marcadas
  como Inativas — testado com Project 2010 real via controle remoto do
  computador do usuário (ver seção seguinte). Investigando, o próprio
  Project 2010, ao SALVAR um arquivo de teste como "Formato XML (*.xml)",
  grava `xmlns="http://schemas.microsoft.com/project"` — o mesmo valor SEM
  versão do botão ProjectLibre, não o versionado "/2007" do XSD de
  referência da Microsoft Learn. Ou seja: o XSD oficial publicado é a
  documentação do FORMATO, mas o namespace que o Project de verdade usa nos
  arquivos que ele mesmo gera é o antigo, sem versão. `NAMESPACE_MS_PROJECT`
  foi corrigido para o mesmo valor de `NAMESPACE_PROJECTLIBRE`.

## O bug real (75ª rodada): não era o namespace, eram os campos da tarefa

O namespace por si só NÃO era a causa do Início/Duração/Término em branco
(testado: o mesmo problema acontecia com os dois namespaces, antes da
correção abaixo). A causa real, descoberta comparando byte a byte um XML
gerado por este exportador com um XML de verdade salvo pelo MS Project
2010 (usando o "Salvar como > Formato XML" do próprio Project, com uma
tarefa simples, como cobaia):

- Uma `<Task>` sem os elementos `<Active>` e `<Manual>` é importada pelo
  Project como INATIVA (`Active` ausente ⇒ tratada como inativa, daí o
  texto riscado na grade) e como Agendada Manualmente (`Manual` ausente ⇒
  cai no padrão "Agendada Manualmente" do Project, que ignora os valores de
  `<Start>`/`<Finish>`/`<Duration>` — uma tarefa Agendada Manualmente só usa
  `<ManualStart>`/`<ManualFinish>`/`<ManualDuration>`).
- O Project real sempre grava `<Active>1</Active>` e `<Manual>0/1</Manual>`
  em toda tarefa, e quando `Manual=0` (Agendamento Automático — o que este
  sistema sempre exporta, já que não tem o conceito de "tarefa manual")
  grava OS DOIS conjuntos de campos: `<Start>/<Finish>/<Duration>` E
  `<ManualStart>/<ManualFinish>/<ManualDuration>` com os MESMOS valores.
- A ORDEM dos elementos dentro de `<Task>` também importa — o leitor do
  Project parece ser sensível à sequência declarada no schema (não é uma
  leitura tolerante por nome, na posição em que o elemento aparecer). A
  ordem usada agora replica a de um arquivo real do Project 2010, reduzida
  aos campos que este sistema de fato precisa gravar (removendo dezenas de
  campos só de SAÍDA/cálculo do próprio Project — EarlyStart, LateStart,
  TotalSlack, BCWS/BCWP, custos, etc. — que o Project recalcula sozinho ao
  abrir uma tarefa Agendada Automaticamente, e que portanto não precisam
  vir do exportador).
- Também foi adicionado `<NewTasksAreManual>0</NewTasksAreManual>` no nível
  do `<Project>` (mesmo valor que o Project real grava quando o padrão de
  novas tarefas do arquivo é Agendamento Automático) — reforça, a nível de
  arquivo, que as tarefas importadas não devem cair no modo manual.

Tudo isso foi confirmado testando de verdade: controlando remotamente o
computador do usuário (que tem Project 2010 instalado) para abrir os
arquivos XML gerados neste ambiente, iterando até a importação ficar
idêntica ao comportamento de um arquivo nativo do Project (datas, duração,
% concluído e dependências corretos, sem "Inativa", sem "Agendada
Manualmente").

## Dois acabamentos finais (ainda na 75ª rodada, após o bug acima)

Com o bug principal corrigido, dois problemas menores apareceram testando em
escala real (16 atividades, com hierarquia/dependências/marco) que não
existiam no teste mínimo (3 tarefas):

1. **Duração aparecendo como "0 dias" pra tarefas de 1 dia**: `<Start>` e
   `<Finish>` eram formatados com a MESMA hora default (08:00) sempre que a
   atividade cobria um único dia — ou seja, uma tarefa de 8h saía com
   Start=dia X 08:00 e Finish=dia X 08:00 (mesmo timestamp!), duração
   aparente zero, mesmo com `<Duration>PT8H0M0S</Duration>` presente. O
   arquivo real do Project (ground truth) grava Start no início do
   expediente e Finish no FIM do expediente do calendário, mesmo quando é o
   mesmo dia (ex.: 09:00 e 18:00). Corrigido calculando `fim_expediente` (a
   partir de `horas_dia_util`) ANTES do laço de tarefas e usando-o como hora
   de `<Finish>`/`<ManualFinish>`. Também corrigido `<DefaultFinishTime>` a
   nível de `<Project>`, que estava fixo em "17:00:00" mas precisa bater com
   o fim de expediente real do calendário "Padrão" (senão o Project
   considera o "dia de trabalho nominal" maior que o calendário de verdade,
   afetando o cálculo de duração de tarefas sem predecessora).

2. **Erro de importação "elemento N com UID = X possui dados inválidos"**:
   apareceu só no teste em escala real, numa tarefa de RESUMO (grupo) que
   tinha uma predecessora com dependência Término-a-Término (FF) e
   defasagem. Isolado por eliminação, testando no Project 2010 de verdade
   com arquivos mínimos construídos à mão: dependências SS isoladas, SF
   isoladas, SS+SF na mesma tarefa, FS apontando pra tarefa de resumo — todas
   importaram sem erro; só reproduziu com a combinação exata de
   Término-a-Término (Type=0) + defasagem (lag≠0) + SUCESSOR sendo uma
   tarefa de resumo (`Summary=1`). É uma limitação real do importador do
   Project 2010 (não um problema de formatação corrigível), então a solução
   foi simplesmente NÃO exportar predecessoras cujo sucessor seja uma tarefa
   de resumo — evita a classe inteira do problema (não só o caso exato
   testado) sem perda prática, já que o Project recalcula as datas de uma
   tarefa de resumo a partir dos filhos dela de qualquer forma (rollup
   automático), não da dependência direta.

Confirmado com os 16 registros reais (`atividades` + hierarquia +
dependências + marco) do projeto de teste, nos dois botões
(ProjectLibre e Microsoft Project): importação sem nenhum diálogo de erro,
apenas o aviso esperado/benigno sobre a data do marco de teste ("Kickoff
concluído" anterior ao início do projeto — é o dado de teste, não o
exportador).
"""
from datetime import date, datetime

from xml.sax.saxutils import escape as _esc

from . import cpm

NAMESPACE_PROJECTLIBRE = "http://schemas.microsoft.com/project"
# 75ª rodada: corrigido de ".../project/2007" (o XSD de referência da
# Microsoft Learn) para o valor SEM versão — é o que o Project 2010 de
# verdade grava nos arquivos que ele mesmo salva como XML (testado). Ver
# docstring do módulo, seção "Dois botões, dois namespaces".
NAMESPACE_MS_PROJECT = "http://schemas.microsoft.com/project"

TIPO_PARA_LINKTYPE = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}
PRIORIDADE_PARA_MSPROJECT = {"Baixa": 200, "Média": 500, "Alta": 800, "Urgente": 900}


def _texto(v):
    return _esc(str(v)) if v not in (None, "") else ""


def _como_data(v):
    if v is None or v == "":
        return None
    if isinstance(v, (date, datetime)):
        return v if isinstance(v, date) and not isinstance(v, datetime) else v.date()
    return datetime.fromisoformat(str(v).split("T")[0]).date()


def _fmt_datetime(v, hora="08:00:00"):
    """MSPDI usa xsd:dateTime completo (com hora) mesmo para campos que no
    nosso banco são só `date` — 08:00 é um horário neutro dentro da janela
    de trabalho padrão, não representa nenhum evento real."""
    d = _como_data(v)
    return f"{d.isoformat()}T{hora}" if d else None


def _horas_para_duracao_xsd(horas):
    try:
        h = float(horas)
    except (TypeError, ValueError):
        h = 0.0
    if h < 0:
        h = 0.0
    horas_inteiras = int(h)
    minutos = round((h - horas_inteiras) * 60)
    if minutos == 60:
        horas_inteiras += 1
        minutos = 0
    return f"PT{horas_inteiras}H{minutos}M0S"


def _duracao_atividade(a, horas_dia_util):
    """Ordem de preferência: prazo_horas (esforço previsto, o campo que a
    tela usa) > diferença de dias úteis entre dtini_prev/dtfim_prev > 1 dia
    útil (mesmo fallback do CPM do sistema, cpm._dur_dias, para nunca
    exportar uma tarefa com duração zero sem ela ser de fato um marco)."""
    prazo = a.get("prazo_horas")
    if prazo not in (None, "", 0):
        return _horas_para_duracao_xsd(float(prazo))
    ini, fim = _como_data(a.get("dtini_prev")), _como_data(a.get("dtfim_prev"))
    if ini and fim:
        dias = cpm.dias_uteis_entre(ini, fim) or 1
        return _horas_para_duracao_xsd(dias * horas_dia_util)
    return _horas_para_duracao_xsd(horas_dia_util)  # 1 dia útil


def _lag_decimos_de_minuto(lag_horas):
    try:
        h = float(lag_horas) if lag_horas not in (None, "") else 0.0
    except (TypeError, ValueError):
        h = 0.0
    return int(round(h * 60 * 10))


def _outline_level(codigo_wbs):
    if not codigo_wbs:
        return 1
    return max(1, len(str(codigo_wbs).split(".")))


def gerar_xml_bytes(projeto, atividades, marcos, deps_por_atividade, namespace=NAMESPACE_PROJECTLIBRE):
    """`atividades`: lista de dicts no formato de ATIVIDADE_SELECT (main.py),
    já ORDENADA por codigo_wbs_chave_ordenacao — a ordem de entrada aqui é a
    ordem final das tarefas no XML (ver docstring do módulo, seção MSPDI).
    `marcos`: lista de dicts da tabela `marcos`.
    `deps_por_atividade`: {atividade_id: [{predecessora_id, tipo, lag_horas}, ...]}.
    `namespace`: NAMESPACE_PROJECTLIBRE (padrão) ou NAMESPACE_MS_PROJECT —
    ver "Dois botões, dois namespaces" no docstring do módulo. Todo o resto
    do arquivo (tarefas, dependências, marcos, calendário) é idêntico
    independente do alvo.
    """
    horas_dia_util = float(projeto.get("horas_dia_util") or 8.0)
    hoje = date.today().isoformat()
    data_inicio = (
        projeto.get("data_inicio")
        or (atividades[0].get("dtini_prev") if atividades else None)
        or hoje
    )
    data_inicio_iso = _como_data(data_inicio).isoformat() if _como_data(data_inicio) else hoje

    ids_com_filhos = {a["atividade_pai_id"] for a in atividades if a.get("atividade_pai_id")}
    uid_por_atividade = {a["id"]: i + 1 for i, a in enumerate(atividades)}
    uid_inicial_marcos = len(atividades) + 1

    # 75ª rodada — achado adicional (grade mostrando "0 dias" pra algumas
    # tarefas mesmo já com a hora de Finish corrigida — especificamente as
    # que NÃO tinham predecessora, ex.: primeira tarefa de cada frente):
    # <DefaultFinishTime> a nível de projeto estava fixo em "17:00:00",
    # mas o calendário "Padrão" (abaixo) usa como fim de expediente
    # `fim_expediente`, calculado a partir de horas_dia_util — com o padrão
    # de 8h/dia isso dá 16:00, não 17:00. Um <Task> cujo Finish cai em
    # 16:00 (fim de expediente real) mas o projeto "acha" que o expediente
    # só termina às 17:00 (DefaultFinishTime) faz o Project calcular menos
    # que 1 dia completo de duração pra ela. Calculado aqui, ANTES do
    # cabeçalho do projeto, pra <DefaultFinishTime> usar o mesmo valor que
    # o calendário realmente declara (elimina a inconsistência).
    fim_h = 8 + int(horas_dia_util)
    fim_m = round((horas_dia_util - int(horas_dia_util)) * 60)
    if fim_m == 60:
        fim_h += 1
        fim_m = 0
    fim_expediente = f"{fim_h:02d}:{fim_m:02d}:00"

    L = []
    L.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    L.append(f'<Project xmlns="{namespace}">')
    L.append("  <SaveVersion>1</SaveVersion>")
    L.append(f"  <Name>{_texto(projeto.get('nome'))}</Name>")
    L.append(f"  <Title>{_texto(projeto.get('nome'))}</Title>")
    L.append(f"  <CreationDate>{_fmt_datetime(hoje)}</CreationDate>")
    L.append(f"  <StartDate>{_fmt_datetime(data_inicio_iso)}</StartDate>")
    if projeto.get("data_fim_prevista"):
        L.append(f"  <FinishDate>{_fmt_datetime(projeto['data_fim_prevista'])}</FinishDate>")
    L.append("  <CurrencyDigits>2</CurrencyDigits>")
    L.append("  <CurrencySymbol>R$</CurrencySymbol>")
    L.append("  <CalendarUID>1</CalendarUID>")
    L.append("  <DefaultStartTime>08:00:00</DefaultStartTime>")
    L.append(f"  <DefaultFinishTime>{fim_expediente}</DefaultFinishTime>")
    L.append(f"  <MinutesPerDay>{int(round(horas_dia_util * 60))}</MinutesPerDay>")
    L.append(f"  <MinutesPerWeek>{int(round(horas_dia_util * 60 * 5))}</MinutesPerWeek>")
    L.append("  <DaysPerMonth>20</DaysPerMonth>")
    L.append("  <DefaultTaskType>0</DefaultTaskType>")
    # 75ª rodada — reforça, a nível de arquivo, que tarefas importadas não
    # devem cair em "Agendada Manualmente" (ver docstring do módulo).
    L.append("  <NewTasksAreManual>0</NewTasksAreManual>")
    L.append("  <DurationFormat>7</DurationFormat>")
    L.append("  <WeekStartDay>0</WeekStartDay>")
    L.append("  <FiscalYearStart>0</FiscalYearStart>")
    L.append(f"  <StatusDate>{_fmt_datetime(hoje)}</StatusDate>")
    L.append(f"  <CurrentDate>{_fmt_datetime(hoje)}</CurrentDate>")

    # ---- Calendário único "Padrão": seg-sex, 08:00 até 08:00 + jornada do projeto ----
    # (`fim_expediente` já calculado acima, antes do cabeçalho do projeto)
    L.append("  <Calendars>")
    L.append("    <Calendar>")
    L.append("      <UID>1</UID>")
    L.append("      <Name>Padrão</Name>")
    L.append("      <IsBaseCalendar>1</IsBaseCalendar>")
    L.append("      <WeekDays>")
    for dia in range(1, 8):  # DayType: 1=domingo ... 7=sábado
        util = dia not in (1, 7)
        L.append("        <WeekDay>")
        L.append(f"          <DayType>{dia}</DayType>")
        L.append(f"          <DayWorking>{1 if util else 0}</DayWorking>")
        if util:
            L.append("          <WorkingTimes>")
            L.append("            <WorkingTime>")
            L.append("              <FromTime>08:00:00</FromTime>")
            L.append(f"              <ToTime>{fim_expediente}</ToTime>")
            L.append("            </WorkingTime>")
            L.append("          </WorkingTimes>")
        L.append("        </WeekDay>")
    L.append("      </WeekDays>")
    L.append("    </Calendar>")
    L.append("  </Calendars>")

    # ---- Tarefas (atividades) ----
    # 75ª rodada: ordem e conjunto de campos de <Task> testados de verdade
    # contra o MS Project 2010 (ver docstring do módulo) — Active/Manual
    # presentes (senão a tarefa importa Inativa + Agendada Manualmente, com
    # Início/Duração/Término em branco), Start/Finish DUPLICADOS em
    # ManualStart/ManualFinish/ManualDuration (mesmo valor — é o que o
    # Project real grava pra uma tarefa Agendada Automaticamente), e a
    # ordem dos elementos seguindo a de um arquivo nativo do Project,
    # reduzida aos campos que este exportador precisa preencher (os campos
    # só de SAÍDA do Project — EarlyStart, TotalSlack, custos etc. — foram
    # deixados de fora; o Project recalcula isso sozinho ao abrir).
    L.append("  <Tasks>")
    for a in atividades:
        uid = uid_por_atividade[a["id"]]
        codigo = a.get("codigo_wbs") or ""
        eh_resumo = a["id"] in ids_com_filhos
        duracao = _duracao_atividade(a, horas_dia_util)
        pct = int(a.get("percentual_concluido") or 0)
        # Start/Finish sempre presentes (com fallback pro início do projeto)
        # pra nunca gravar ManualStart/ManualFinish sem o par Start/Finish
        # correspondente.
        #
        # 75ª rodada — achado adicional (grade do Project mostrando "0 dias"
        # de duração após importar, mesmo com <Duration> correto): Start e
        # Finish são datas (sem hora) no nosso banco, e a versão anterior
        # formatava os DOIS com a hora default de _fmt_datetime (08:00) — daí
        # uma tarefa de 1 dia útil (dtini_prev == dtfim_prev) saía com Start
        # e Finish EXATAMENTE IGUAIS (mesma data, mesma hora), duração
        # aparente zero. O arquivo real do Project (ground truth, ver
        # docstring do módulo — tarefa de 1 dia: Start 2026-10-05T09:00:00 /
        # Finish 2026-10-05T18:00:00, MESMO DIA, horas diferentes: início e
        # fim do expediente) confirma que Finish deve usar a hora de FIM do
        # expediente (`fim_expediente`, calculado mais acima a partir de
        # horas_dia_util), não a de início. Start continua no default 08:00
        # (= início do expediente / DefaultStartTime do calendário).
        inicio_iso = _fmt_datetime(a["dtini_prev"]) if a.get("dtini_prev") else _fmt_datetime(data_inicio_iso)
        data_fim_ativ = a.get("dtfim_prev") or a.get("dtini_prev") or data_inicio_iso
        fim_iso = _fmt_datetime(data_fim_ativ, hora=fim_expediente)

        L.append("    <Task>")
        L.append(f"      <UID>{uid}</UID>")
        L.append(f"      <ID>{uid}</ID>")
        L.append(f"      <Name>{_texto(a.get('nome'))}</Name>")
        L.append("      <Active>1</Active>")
        L.append("      <Manual>0</Manual>")
        L.append("      <Type>0</Type>")
        L.append("      <IsNull>0</IsNull>")
        if codigo:
            L.append(f"      <WBS>{_texto(codigo)}</WBS>")
            L.append(f"      <OutlineNumber>{_texto(codigo)}</OutlineNumber>")
        L.append(f"      <OutlineLevel>{_outline_level(codigo)}</OutlineLevel>")
        L.append(f"      <Priority>{PRIORIDADE_PARA_MSPROJECT.get(a.get('prioridade'), 500)}</Priority>")
        L.append(f"      <Start>{inicio_iso}</Start>")
        L.append(f"      <Finish>{fim_iso}</Finish>")
        L.append(f"      <Duration>{duracao}</Duration>")
        L.append(f"      <ManualStart>{inicio_iso}</ManualStart>")
        L.append(f"      <ManualFinish>{fim_iso}</ManualFinish>")
        L.append(f"      <ManualDuration>{duracao}</ManualDuration>")
        L.append("      <DurationFormat>7</DurationFormat>")
        L.append(f"      <Work>{duracao}</Work>")
        L.append("      <EffortDriven>0</EffortDriven>")
        L.append("      <Recurring>0</Recurring>")
        L.append("      <OverAllocated>0</OverAllocated>")
        L.append("      <Estimated>0</Estimated>")
        L.append("      <Milestone>0</Milestone>")
        L.append(f"      <Summary>{1 if eh_resumo else 0}</Summary>")
        L.append(f"      <Critical>{1 if a.get('cpm_critica') else 0}</Critical>")
        # "Completo por cento" é o rótulo que o PRÓPRIO ProjectLibre usa (localização
        # PT-BR dele) pra coluna do campo padrão PercentComplete do MSPDI — não é
        # nome escolhido por este exportador nem dá pra mudar por aqui (é tradução
        # da interface do ProjectLibre, não do XML).
        L.append(f"      <PercentComplete>{pct}</PercentComplete>")
        L.append(f"      <PercentWorkComplete>{pct}</PercentWorkComplete>")
        L.append("      <ConstraintType>0</ConstraintType>")
        L.append("      <CalendarUID>-1</CalendarUID>")
        if a.get("observacoes"):
            L.append(f"      <Notes>{_texto(a['observacoes'])}</Notes>")

        # 75ª rodada — <PredecessorLink> sempre com os mesmos 5 campos
        # (PredecessorUID, Type, CrossProject, LinkLag, LagFormat), igual ao
        # arquivo real do Project (ground truth), mesmo quando lag=0 — boa
        # prática (consistência de campos entre elementos irmãos), mas essa
        # NÃO era a causa do erro de importação "elemento N com UID = X
        # possui dados inválidos" visto ao testar em escala real (ver
        # próximo comentário).
        #
        # 75ª rodada — causa REAL do erro acima, isolada por eliminação
        # testando no Project 2010 de verdade (namespace, tipo de vínculo
        # SS isolado, SF isolado, SS+SF juntos, vínculo apontando para
        # tarefa de resumo com FS/lag=0 — todos OK; só reproduziu com a
        # combinação exata abaixo): o Project 2010 REJEITA um
        # <PredecessorLink> do tipo Término-a-Término (FF, Type=0) COM
        # defasagem (lag != 0) quando o SUCESSOR do vínculo é uma tarefa de
        # RESUMO (Summary=1) — a mesma dependência, mesmo tipo, mesmo lag,
        # apontando para uma tarefa comum (não-resumo) importa perfeitamente.
        # É uma limitação real do importador nativo do Project (não é algo
        # que dê pra "formatar direito" — reproduzido em arquivo mínimo,
        # gerado à mão, sem nenhum outro campo em comum com o exportador).
        # Como o Project de qualquer forma recalcula as datas de uma tarefa
        # de resumo a partir dos filhos dela (rollup automático — não é a
        # dependência direta que manda), a saída mais segura é simplesmente
        # NÃO exportar predecessoras cujo SUCESSOR seja uma tarefa de resumo
        # — evita a classe inteira do problema (não só o caso FF+lag
        # confirmado) sem perder informação que o Project fosse de fato usar.
        for d in deps_por_atividade.get(a["id"]) or []:
            if eh_resumo:
                # a tarefa `a` (sucessora do vínculo) é resumo — pula (ver
                # comentário acima). As datas dela continuam corretas via
                # rollup automático dos filhos, que têm suas próprias
                # dependências exportadas normalmente.
                continue
            pred_uid = uid_por_atividade.get(d.get("predecessora_id"))
            if not pred_uid:
                # predecessora fora do projeto ou sem correspondência — não deveria
                # acontecer (dependências são sempre intra-projeto), mas nunca trava
                # a exportação por causa de um dado inconsistente.
                continue
            tipo = d.get("tipo") or "FS"
            lag = _lag_decimos_de_minuto(d.get("lag_horas"))
            L.append("      <PredecessorLink>")
            L.append(f"        <PredecessorUID>{pred_uid}</PredecessorUID>")
            L.append(f"        <Type>{TIPO_PARA_LINKTYPE.get(tipo, 1)}</Type>")
            L.append("        <CrossProject>0</CrossProject>")
            L.append(f"        <LinkLag>{lag}</LinkLag>")
            L.append("        <LagFormat>7</LagFormat>")
            L.append("      </PredecessorLink>")

        L.append("      <IsPublished>1</IsPublished>")
        L.append("    </Task>")

    # ---- Marcos (tabela `marcos` — conceito à parte, sem código/dependência) ----
    for i, m in enumerate(marcos):
        uid = uid_inicial_marcos + i
        data_marco_iso = _fmt_datetime(m["data_prevista"]) if m.get("data_prevista") else _fmt_datetime(data_inicio_iso)
        L.append("    <Task>")
        L.append(f"      <UID>{uid}</UID>")
        L.append(f"      <ID>{uid}</ID>")
        L.append(f"      <Name>{_texto(m.get('nome'))}</Name>")
        L.append("      <Active>1</Active>")
        L.append("      <Manual>0</Manual>")
        L.append("      <Type>0</Type>")
        L.append("      <IsNull>0</IsNull>")
        L.append("      <OutlineLevel>1</OutlineLevel>")
        L.append("      <Priority>500</Priority>")
        L.append(f"      <Start>{data_marco_iso}</Start>")
        L.append(f"      <Finish>{data_marco_iso}</Finish>")
        L.append("      <Duration>PT0H0M0S</Duration>")
        L.append(f"      <ManualStart>{data_marco_iso}</ManualStart>")
        L.append(f"      <ManualFinish>{data_marco_iso}</ManualFinish>")
        L.append("      <ManualDuration>PT0H0M0S</ManualDuration>")
        L.append("      <DurationFormat>7</DurationFormat>")
        L.append("      <Work>PT0H0M0S</Work>")
        L.append("      <EffortDriven>0</EffortDriven>")
        L.append("      <Recurring>0</Recurring>")
        L.append("      <OverAllocated>0</OverAllocated>")
        L.append("      <Estimated>0</Estimated>")
        L.append("      <Milestone>1</Milestone>")
        L.append("      <Summary>0</Summary>")
        L.append("      <Critical>0</Critical>")
        L.append(f"      <PercentComplete>{100 if m.get('data_real') else 0}</PercentComplete>")
        L.append(f"      <PercentWorkComplete>{100 if m.get('data_real') else 0}</PercentWorkComplete>")
        L.append("      <ConstraintType>0</ConstraintType>")
        L.append("      <CalendarUID>-1</CalendarUID>")
        if m.get("descricao"):
            L.append(f"      <Notes>{_texto(m['descricao'])}</Notes>")
        L.append("      <IsPublished>1</IsPublished>")
        L.append("    </Task>")

    L.append("  </Tasks>")
    L.append("</Project>")

    return "\n".join(L).encode("utf-8")
