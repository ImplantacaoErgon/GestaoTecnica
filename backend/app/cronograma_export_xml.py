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

## Risco conhecido, não verificável neste ambiente

O namespace raiz usado abaixo é o SEM VERSÃO
`http://schemas.microsoft.com/project` — é o que arquivos realmente gerados
por "Salvar como XML" do MS Project usam na prática, e o mais comumente
aceito por leitores desse formato. O XSD oficial hoje publicado pela
Microsoft declara `targetNamespace` COM versão
("http://schemas.microsoft.com/project/2007"). Não foi possível confirmar
com certeza qual delas o importador MSPDI do ProjectLibre (via MPXJ) exige,
nem rodar um teste de importação real (sem acesso de rede a Maven/PyPI
neste ambiente para trazer o MPXJ como verificador). Se o ProjectLibre
recusar ou ignorar o arquivo, o primeiro ajuste a tentar é trocar a
constante NAMESPACE abaixo para a versão com "/2007".
"""
from datetime import date, datetime

from xml.sax.saxutils import escape as _esc

from . import cpm

NAMESPACE = "http://schemas.microsoft.com/project"
# Ver "Risco conhecido" acima. Alternativa a tentar se o ProjectLibre recusar o arquivo:
# NAMESPACE = "http://schemas.microsoft.com/project/2007"

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


def gerar_xml_bytes(projeto, atividades, marcos, deps_por_atividade):
    """`atividades`: lista de dicts no formato de ATIVIDADE_SELECT (main.py),
    já ORDENADA por codigo_wbs_chave_ordenacao — a ordem de entrada aqui é a
    ordem final das tarefas no XML (ver docstring do módulo, seção MSPDI).
    `marcos`: lista de dicts da tabela `marcos`.
    `deps_por_atividade`: {atividade_id: [{predecessora_id, tipo, lag_horas}, ...]}.
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

    L = []
    L.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    L.append(f'<Project xmlns="{NAMESPACE}">')
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
    L.append("  <DefaultFinishTime>17:00:00</DefaultFinishTime>")
    L.append(f"  <MinutesPerDay>{int(round(horas_dia_util * 60))}</MinutesPerDay>")
    L.append(f"  <MinutesPerWeek>{int(round(horas_dia_util * 60 * 5))}</MinutesPerWeek>")
    L.append("  <DaysPerMonth>20</DaysPerMonth>")
    L.append("  <DefaultTaskType>0</DefaultTaskType>")
    L.append("  <DurationFormat>7</DurationFormat>")
    L.append("  <WeekStartDay>0</WeekStartDay>")
    L.append("  <FiscalYearStart>0</FiscalYearStart>")
    L.append(f"  <StatusDate>{_fmt_datetime(hoje)}</StatusDate>")
    L.append(f"  <CurrentDate>{_fmt_datetime(hoje)}</CurrentDate>")

    # ---- Calendário único "Padrão": seg-sex, 08:00 até 08:00 + jornada do projeto ----
    fim_h = 8 + int(horas_dia_util)
    fim_m = round((horas_dia_util - int(horas_dia_util)) * 60)
    if fim_m == 60:
        fim_h += 1
        fim_m = 0
    fim_expediente = f"{fim_h:02d}:{fim_m:02d}:00"
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
    L.append("  <Tasks>")
    for a in atividades:
        uid = uid_por_atividade[a["id"]]
        codigo = a.get("codigo_wbs") or ""
        eh_resumo = a["id"] in ids_com_filhos
        duracao = _duracao_atividade(a, horas_dia_util)
        pct = int(a.get("percentual_concluido") or 0)

        L.append("    <Task>")
        L.append(f"      <UID>{uid}</UID>")
        L.append(f"      <ID>{uid}</ID>")
        L.append(f"      <Name>{_texto(a.get('nome'))}</Name>")
        if codigo:
            L.append(f"      <WBS>{_texto(codigo)}</WBS>")
            L.append(f"      <OutlineNumber>{_texto(codigo)}</OutlineNumber>")
        L.append(f"      <OutlineLevel>{_outline_level(codigo)}</OutlineLevel>")
        if a.get("dtini_prev"):
            L.append(f"      <Start>{_fmt_datetime(a['dtini_prev'])}</Start>")
        if a.get("dtfim_prev"):
            L.append(f"      <Finish>{_fmt_datetime(a['dtfim_prev'])}</Finish>")
        L.append(f"      <Duration>{duracao}</Duration>")
        L.append("      <DurationFormat>7</DurationFormat>")
        L.append(f"      <Work>{duracao}</Work>")
        L.append(f"      <PercentComplete>{pct}</PercentComplete>")
        L.append(f"      <PercentWorkComplete>{pct}</PercentWorkComplete>")
        L.append("      <Milestone>0</Milestone>")
        L.append(f"      <Summary>{1 if eh_resumo else 0}</Summary>")
        L.append(f"      <Critical>{1 if a.get('cpm_critica') else 0}</Critical>")
        L.append(f"      <Priority>{PRIORIDADE_PARA_MSPROJECT.get(a.get('prioridade'), 500)}</Priority>")
        if a.get("observacoes"):
            L.append(f"      <Notes>{_texto(a['observacoes'])}</Notes>")

        for d in deps_por_atividade.get(a["id"]) or []:
            pred_uid = uid_por_atividade.get(d.get("predecessora_id"))
            if not pred_uid:
                # predecessora fora do projeto ou sem correspondência — não deveria
                # acontecer (dependências são sempre intra-projeto), mas nunca trava
                # a exportação por causa de um dado inconsistente.
                continue
            tipo = d.get("tipo") or "FS"
            L.append("      <PredecessorLink>")
            L.append(f"        <PredecessorUID>{pred_uid}</PredecessorUID>")
            L.append(f"        <Type>{TIPO_PARA_LINKTYPE.get(tipo, 1)}</Type>")
            lag = _lag_decimos_de_minuto(d.get("lag_horas"))
            if lag:
                L.append(f"        <LinkLag>{lag}</LinkLag>")
                L.append("        <LagFormat>7</LagFormat>")
            L.append("      </PredecessorLink>")

        L.append("    </Task>")

    # ---- Marcos (tabela `marcos` — conceito à parte, sem código/dependência) ----
    for i, m in enumerate(marcos):
        uid = uid_inicial_marcos + i
        L.append("    <Task>")
        L.append(f"      <UID>{uid}</UID>")
        L.append(f"      <ID>{uid}</ID>")
        L.append(f"      <Name>{_texto(m.get('nome'))}</Name>")
        L.append("      <OutlineLevel>1</OutlineLevel>")
        if m.get("data_prevista"):
            L.append(f"      <Start>{_fmt_datetime(m['data_prevista'])}</Start>")
            L.append(f"      <Finish>{_fmt_datetime(m['data_prevista'])}</Finish>")
        L.append("      <Duration>PT0H0M0S</Duration>")
        L.append("      <DurationFormat>7</DurationFormat>")
        L.append(f"      <PercentComplete>{100 if m.get('data_real') else 0}</PercentComplete>")
        L.append("      <Milestone>1</Milestone>")
        L.append("      <Summary>0</Summary>")
        if m.get("descricao"):
            L.append(f"      <Notes>{_texto(m['descricao'])}</Notes>")
        L.append("    </Task>")

    L.append("  </Tasks>")
    L.append("</Project>")

    return "\n".join(L).encode("utf-8")
