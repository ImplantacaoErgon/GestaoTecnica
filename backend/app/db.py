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
