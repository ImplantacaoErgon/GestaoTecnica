"""
Camada de acesso ao banco.

Fala com o Postgres via subprocess, chamando o cliente `psql` (requer o
pacote `postgresql-client` na imagem/host — já incluso no Dockerfile) e
trocando dados em JSON (`row_to_json` / `json_agg`). Essa escolha evita
depender de um driver Python compilado (psycopg2/psycopg3), o que
simplifica o deploy — só precisa de Python + `psql` no PATH.

Se no futuro quiser trocar por um driver nativo (psycopg), a interface
pública (fetch_all, fetch_one, execute_returning_one, execute) foi
desenhada para não vazar detalhe de implementação — nenhuma rota em
main.py precisaria mudar, só esta função `_run` e as duas de wrap/parse.
"""
import json
import os
import subprocess
import threading

PGHOST = os.environ.get("PGHOST", "localhost")
PGPORT = os.environ.get("PGPORT", "5432")
PGUSER = os.environ.get("PGUSER", "postgres")
PGPASSWORD = os.environ.get("PGPASSWORD", "app")
PGDATABASE = os.environ.get("PGDATABASE", "app_db")


class DbError(Exception):
    pass


def q(value):
    """Quota um valor Python como literal SQL seguro (dollar-quoting para texto)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    tag = "$q$"
    if tag in s:
        return "'" + s.replace("'", "''") + "'"
    return f"{tag}{s}{tag}"


def _run(sql: str, timeout: int = 60) -> str:
    env = dict(os.environ)
    env["PGPASSWORD"] = PGPASSWORD
    try:
        result = subprocess.run(
            ["psql", "-h", PGHOST, "-p", str(PGPORT), "-U", PGUSER, "-d", PGDATABASE,
             "-tAX", "--no-psqlrc", "-v", "ON_ERROR_STOP=1"],
            input=sql, capture_output=True, text=True, env=env, timeout=timeout,
        )
    except FileNotFoundError as e:
        raise DbError(f"psql não encontrado: {e}")
    if result.returncode != 0:
        raise DbError(result.stderr.strip() or "erro desconhecido ao executar SQL")
    return result.stdout


def fetch_all(sql_body: str) -> list:
    """sql_body: um SELECT completo (sem ; final). Retorna lista de dicts."""
    wrapped = f"SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json) FROM ({sql_body}) t;"
    out = _run(wrapped).strip()
    return json.loads(out) if out else []


def fetch_one(sql_body: str):
    rows = fetch_all(sql_body)
    return rows[0] if rows else None


def execute_returning_one(sql_body: str, prelude: str = ""):
    """sql_body: um INSERT/UPDATE/DELETE ... RETURNING * (sem ; final).
    `prelude` (opcional, ex: "SET LOCAL app.usuario_atual = ...;") roda
    antes, na MESMA transação — usado para atribuir o autor de uma
    mudança de status antes do UPDATE que dispara o trigger de
    histórico. Envolvido em BEGIN/COMMIT explícito de propósito: sob um
    pooler em modo transação (ex: Supabase Transaction Pooler / PgBouncer),
    statements soltos podem ser roteados para conexões físicas diferentes
    entre si — só um bloco de transação explícito garante que o SET LOCAL
    valha para o UPDATE seguinte."""
    if prelude:
        wrapped = f"BEGIN;\n{prelude}\nWITH x AS ({sql_body}) SELECT row_to_json(x) FROM x;\nCOMMIT;"
    else:
        wrapped = f"WITH x AS ({sql_body}) SELECT row_to_json(x) FROM x;"
    out = _run(wrapped).strip()
    # com BEGIN/COMMIT, psql também imprime "BEGIN"/"SET"/"COMMIT" como status de
    # cada comando — filtra e pega a única linha que é de fato JSON.
    result = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
    return result


def execute(sql_body: str, timeout: int = 60) -> None:
    _run(sql_body + ";", timeout=timeout)


def execute_stream(sql_prefix: str, linhas, sql_suffix: str, timeout: int = 600) -> None:
    """Como execute(), mas pra scripts GRANDES (55ª rodada — carga da
    Comparação Folha, ~300 mil linhas por importação): em vez de montar o
    script inteiro como uma string só na memória e mandar tudo de uma vez
    (o que pra 300 mil linhas x 65 colunas passa de 100MB), escreve
    `sql_prefix`, depois cada item de `linhas` (um iterável/gerador — cada
    item já deve terminar com "\\n", tipicamente uma linha de dados de um
    `COPY ... FROM STDIN`), depois `sql_suffix`, incrementalmente no stdin
    do psql. `linhas` nunca precisa virar uma lista/string única na memória
    do processo Python — só o valor de cada linha por vez.

    stdout/stderr são drenados numa thread separada ENQUANTO ainda se
    escreve no stdin — necessário pra scripts grandes: se o psql produzir
    saída (ex: avisos, ou o "COPY N" de cada comando) enquanto o buffer do
    pipe de stdin ainda não foi todo consumido, escrever tudo de uma vez
    sem drenar a saída em paralelo pode travar os dois lados esperando um
    pelo outro (deadlock clássico de pipe cheio)."""
    env = dict(os.environ)
    env["PGPASSWORD"] = PGPASSWORD
    try:
        proc = subprocess.Popen(
            ["psql", "-h", PGHOST, "-p", str(PGPORT), "-U", PGUSER, "-d", PGDATABASE,
             "-tAX", "--no-psqlrc", "-v", "ON_ERROR_STOP=1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
    except FileNotFoundError as e:
        raise DbError(f"psql não encontrado: {e}")

    saida = {}

    def _drenar(nome, fh):
        saida[nome] = fh.read()

    t_out = threading.Thread(target=_drenar, args=("stdout", proc.stdout))
    t_err = threading.Thread(target=_drenar, args=("stderr", proc.stderr))
    t_out.start()
    t_err.start()

    erro_escrita = None
    try:
        proc.stdin.write(sql_prefix)
        for linha in linhas:
            proc.stdin.write(linha)
        proc.stdin.write(sql_suffix)
    except (BrokenPipeError, OSError) as e:
        # psql pode ter morrido no meio (ex: erro de SQL com ON_ERROR_STOP=1)
        # antes de terminarmos de escrever — guarda o erro real de stderr,
        # não a quebra do pipe em si, que por si só não explica nada ao usuário.
        erro_escrita = e
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise DbError(f"Tempo esgotado ({timeout}s) executando o script no banco.")
    t_out.join(timeout=5)
    t_err.join(timeout=5)

    if returncode != 0:
        detalhe = (saida.get("stderr") or "").strip()
        if not detalhe and erro_escrita:
            detalhe = str(erro_escrita)
        raise DbError(detalhe or "erro desconhecido ao executar script em streaming")
