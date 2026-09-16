"""
Autenticação e usuários do sistema.

Decisões da 11ª rodada (ver gestao-tecnica-plano.md e README, seção "Login
e cadastro de usuários"):

- Autocadastro aberto: qualquer pessoa preenche o formulário de cadastro
  (todos os campos obrigatórios) e já sai com login liberado — a senha é a
  que a própria pessoa digitou no cadastro, não uma gerada pelo sistema.
- "Esqueci a senha" gera uma senha aleatória nova e ENVIA POR E-MAIL (não é
  um link de redefinição) — pedido explícito do cliente.
- Sem perfil de administrador nesta rodada: todo usuário autenticado tem o
  mesmo nível de acesso a todas as telas do sistema.
- Hash de senha via werkzeug.security (já vem com o Flask — nenhuma
  dependência nova no requirements.txt, o que evita o mesmo problema de
  instalação de pacote que a integração com IA teve).
- Envio de e-mail via smtplib (biblioteca padrão do Python) — funciona com
  qualquer provedor (Gmail com senha de app, Office 365, Amazon SES,
  SendGrid via SMTP relay etc.), configurado só por variáveis de ambiente.
  Sem SMTP configurado, login/cadastro continuam funcionando normalmente —
  só "Esqueci a senha" fica indisponível, com uma mensagem de erro clara
  (mesmo padrão adotado para a ANTHROPIC_API_KEY na 10ª rodada).
"""
import os
import re
import secrets
import smtplib
import string
from email.mime.text import MIMEText

from werkzeug.security import check_password_hash, generate_password_hash

from . import db

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SENHA_MIN_LEN = 6

USUARIO_CADASTRO_CAMPOS_OBRIGATORIOS = [
    "email", "nome", "telefone", "empresa", "cargo", "senha",
]
# Campos que a tela de edição (Configurações > Usuários) pode alterar.
# Propositalmente SEM "email" e sem qualquer campo de senha — trocar de
# e-mail (login) ou senha passa pelas rotas dedicadas, nunca por aqui.
USUARIO_EDIT_FIELDS = [
    "nome", "telefone", "empresa", "cargo",
    "recebe_relatorio_executivo", "recebe_notificacao_whatsapp", "ativo",
]
# Colunas seguras para devolver ao front — nunca inclui senha_hash.
USUARIO_COLUNAS_PUBLICAS = (
    "id, email, nome, telefone, empresa, cargo, "
    "recebe_relatorio_executivo, recebe_notificacao_whatsapp, ativo, "
    "criado_em, ultimo_login_em"
)


class AuthError(Exception):
    """Erro esperado (validação, credenciais, SMTP não configurado etc.) —
    sempre devolvido como 400/401 pela rota, nunca um 500 genérico."""
    pass


def validar_email(email):
    email = (email or "").strip()
    if not email or not EMAIL_REGEX.match(email):
        raise AuthError("Informe um e-mail válido.")
    return email.lower()


def validar_senha(senha):
    if not senha or len(senha) < SENHA_MIN_LEN:
        raise AuthError(f"A senha precisa ter pelo menos {SENHA_MIN_LEN} caracteres.")
    return senha


def hash_senha(senha):
    return generate_password_hash(senha)


def verificar_senha(senha, senha_hash):
    if not senha_hash:
        return False
    return check_password_hash(senha_hash, senha)


def gerar_senha_temporaria(tamanho=10):
    """Senha aleatória e legível (sem caracteres ambíguos tipo 0/O, 1/l/I),
    usada por 'Esqueci a senha' — o texto puro só existe em memória o tempo
    de ser enviado por e-mail; o que fica salvo no banco é só o hash."""
    alfabeto = "".join(c for c in (string.ascii_letters + string.digits) if c not in "0O1lI")
    return "".join(secrets.choice(alfabeto) for _ in range(tamanho))


def buscar_usuario_por_email(email):
    email = (email or "").strip().lower()
    if not email:
        return None
    return db.fetch_one(
        f"SELECT * FROM usuarios WHERE lower(email) = {db.q(email)}"
    )


def buscar_usuario_publico(usuario_id):
    return db.fetch_one(
        f"SELECT {USUARIO_COLUNAS_PUBLICAS} FROM usuarios WHERE id = {db.q(usuario_id)}"
    )


def registrar_usuario(data):
    """Cadastro/'primeiro acesso': valida todos os campos obrigatórios,
    garante e-mail único e grava a senha já com hash. Levanta AuthError com
    mensagem amigável em caso de problema. Retorna a linha pública (sem
    senha_hash) do usuário criado."""
    faltando = [
        campo for campo in USUARIO_CADASTRO_CAMPOS_OBRIGATORIOS
        if not str(data.get(campo) or "").strip()
    ]
    if faltando:
        raise AuthError(
            "Todos os campos são obrigatórios. Faltando: " + ", ".join(faltando)
        )
    email = validar_email(data["email"])
    senha = validar_senha(data["senha"])
    if buscar_usuario_por_email(email):
        raise AuthError("Já existe um usuário cadastrado com este e-mail.")

    senha_hash = hash_senha(senha)
    sql = (
        "INSERT INTO usuarios (email, nome, telefone, empresa, cargo, senha_hash, "
        "recebe_relatorio_executivo, recebe_notificacao_whatsapp) VALUES ("
        f"{db.q(email)}, {db.q(data['nome'].strip())}, {db.q(data['telefone'].strip())}, "
        f"{db.q(data['empresa'].strip())}, {db.q(data['cargo'].strip())}, {db.q(senha_hash)}, "
        f"{db.q(bool(data.get('recebe_relatorio_executivo')))}, "
        f"{db.q(bool(data.get('recebe_notificacao_whatsapp')))}) "
        f"RETURNING {USUARIO_COLUNAS_PUBLICAS}"
    )
    return db.execute_returning_one(sql)


def autenticar(email, senha):
    """Retorna a linha pública do usuário se e-mail/senha conferem e o
    usuário está ativo; levanta AuthError com mensagem genérica caso
    contrário (nunca revela se o problema foi e-mail inexistente ou senha
    errada — evita enumeração de e-mails cadastrados)."""
    usuario = buscar_usuario_por_email(email)
    if not usuario or not verificar_senha(senha or "", usuario.get("senha_hash")):
        raise AuthError("E-mail ou senha inválidos.")
    if not usuario.get("ativo", True):
        raise AuthError("Este usuário está desativado. Fale com quem administra o sistema.")
    db.execute(f"UPDATE usuarios SET ultimo_login_em = now() WHERE id = {db.q(usuario['id'])}")
    usuario.pop("senha_hash", None)
    return usuario


def trocar_senha(usuario_id, senha_atual, senha_nova):
    usuario = db.fetch_one(f"SELECT * FROM usuarios WHERE id = {db.q(usuario_id)}")
    if not usuario:
        raise AuthError("Usuário não encontrado.")
    if not verificar_senha(senha_atual or "", usuario.get("senha_hash")):
        raise AuthError("Senha atual incorreta.")
    senha_nova = validar_senha(senha_nova)
    db.execute(
        f"UPDATE usuarios SET senha_hash = {db.q(hash_senha(senha_nova))} WHERE id = {db.q(usuario_id)}"
    )


def esqueci_a_senha(email):
    """Gera uma senha nova, salva o hash e ENVIA A SENHA EM TEXTO PURO por
    e-mail (pedido explícito do cliente — não é um link de redefinição).
    Por segurança contra enumeração de e-mails, o chamador (rota) deve
    devolver a mesma mensagem de sucesso tenha o e-mail sido encontrado ou
    não; esta função só levanta AuthError para problemas de configuração
    do SMTP (que aí sim precisam aparecer para quem administra o sistema
    perceber e corrigir o .env)."""
    usuario = buscar_usuario_por_email(email)
    if not usuario:
        return  # e-mail não cadastrado — silencioso de propósito (ver docstring)
    senha_nova = gerar_senha_temporaria()
    corpo = (
        f"Olá, {usuario['nome']}!\n\n"
        "Uma nova senha foi gerada para o seu acesso à Gestão de Projetos:\n\n"
        f"    {senha_nova}\n\n"
        "Use-a para entrar e, se quiser, troque por uma de sua preferência em "
        "\"Meu perfil > Alterar senha\" depois de logado.\n\n"
        "Se você não pediu essa troca, avise quem administra o sistema.\n"
    )
    enviar_email(usuario["email"], "Gestão de Projetos — nova senha de acesso", corpo)
    # só grava o novo hash DEPOIS do envio dar certo — se o SMTP falhar
    # (enviar_email levanta AuthError), a senha antiga continua valendo.
    db.execute(
        f"UPDATE usuarios SET senha_hash = {db.q(hash_senha(senha_nova))} WHERE id = {db.q(usuario['id'])}"
    )


def smtp_configurado():
    return bool(os.environ.get("SMTP_HOST", "").strip())


def enviar_email(destinatario, assunto, corpo_texto):
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        raise AuthError(
            "Envio de e-mail não configurado neste ambiente (variáveis SMTP_* ausentes no .env). "
            "Veja o README, seção \"Login e cadastro de usuários\", para configurar."
        )
    port = int(os.environ.get("SMTP_PORT", "587").strip() or "587")
    usuario_smtp = os.environ.get("SMTP_USER", "").strip()
    senha_smtp = os.environ.get("SMTP_PASSWORD", "").strip()
    remetente = os.environ.get("SMTP_FROM", "").strip() or usuario_smtp
    usar_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() not in ("false", "0", "não", "nao")

    msg = MIMEText(corpo_texto, "plain", "utf-8")
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = destinatario

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if usar_tls:
                smtp.starttls()
            if usuario_smtp:
                smtp.login(usuario_smtp, senha_smtp)
            smtp.sendmail(remetente, [destinatario], msg.as_string())
    except AuthError:
        raise
    except smtplib.SMTPAuthenticationError as e:
        raise AuthError(f"Falha de autenticação no servidor de e-mail (SMTP_USER/SMTP_PASSWORD): {e}")
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        raise AuthError(f"Falha ao enviar e-mail: {e}")
