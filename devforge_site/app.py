"""DevForge Studio: portal de encomendas, chat e entregas.

Local: SQLite e anexos privados. Render Free: PostgreSQL externo e Supabase Storage.
Nunca exponha chaves, variáveis de ambiente nem arquivos privados.
"""
from __future__ import annotations

import hmac
import io
import json
import os
import re
import secrets
import smtplib
import sqlite3
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import wraps
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    send_file, send_from_directory, session, url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "instance"))).resolve()
UPLOAD_DIR = DATA_DIR / "private_uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "devforge.sqlite3"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
STORAGE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
STORAGE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "devforge-private").strip()
CLOUD_STORAGE = bool(STORAGE_URL and STORAGE_KEY)
if os.getenv("RENDER", "").strip().lower() == "true" and not DATABASE_URL:
    raise RuntimeError("Render Free: configure DATABASE_URL e Supabase Storage; SQLite local seria apagado.")
if DATABASE_URL and not CLOUD_STORAGE:
    raise RuntimeError("Com DATABASE_URL configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY: arquivos privados precisam ser persistentes.")
if CLOUD_STORAGE and not DATABASE_URL:
    raise RuntimeError("Com Supabase Storage configure DATABASE_URL: pedidos e chat precisam de banco persistente.")

APP_NAME = os.getenv("SITE_NAME", "DevForge Studio")
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:5000").rstrip("/")
MAX_FILE_MB = 20
MAX_FILES = 5
ALLOWED_EXTENSIONS = {
    ".zip", ".7z", ".rar", ".pdf", ".txt", ".md", ".csv", ".json",
    ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".webp",
    ".gif", ".svg", ".html", ".css", ".js", ".ts", ".py", ".java",
    ".cpp", ".c", ".cs", ".sql", ".xml", ".apk", ".exe", ".msi",
    ".whl", ".tar", ".gz", ".mp4", ".mov", ".mp3", ".wav",
    ".fig", ".blend", ".stl", ".obj", ".glb", ".gltf", ".psd",
}
STATUSES = {
    "novo": "Novo pedido", "analise": "Em análise", "desenvolvimento": "Em desenvolvimento",
    "revisao": "Aguardando revisão", "entregue": "Entregue", "concluido": "Concluído",
    "cancelado": "Cancelado",
}
TYPES = {
    "site": "Site / sistema web", "windows": "Aplicativo Windows",
    "android": "Aplicativo Android", "ios": "Aplicativo iOS",
    "bot": "Bot / automação", "jogo": "Jogo", "outro": "Outro software",
}

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "change-this-secret-before-deploying"),
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
)
if app.config["SESSION_COOKIE_SECURE"] and (
    len(str(app.config["SECRET_KEY"])) < 32
    or str(app.config["SECRET_KEY"]).startswith(("change-this-", "SUBSTITUA_"))
):
    raise RuntimeError("Configure uma SECRET_KEY aleatória e forte antes de publicar em HTTPS.")

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)


class PostgresCursor:
    """Cursor semelhante ao SQLite, com fetch e lastrowid usados no projeto."""
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class PostgresConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        import re as _re
        pg_sql = _re.sub(r"datetime\('now'\)",
                         "to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')",
                         sql, flags=_re.I)
        pg_sql = pg_sql.replace("?", "%s")
        inserting = bool(_re.match(r"\s*INSERT\s+INTO\s+", pg_sql, flags=_re.I))
        if inserting:
            pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
        cur = self._conn.execute(pg_sql, params)
        new_id = cur.fetchone()["id"] if inserting else None
        return PostgresCursor(cur, new_id)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect_database():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        # Na Supabase, copie preferencialmente a URL do Session Pooler (IPv4).
        return PostgresConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=12))
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect_database()
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


SQLITE_SCHEMA = """
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'client' CHECK(role IN ('client', 'admin')),
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        title TEXT NOT NULL, project_type TEXT NOT NULL, description TEXT NOT NULL,
        budget TEXT NOT NULL DEFAULT '', desired_date TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'novo',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')), delivered_at TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id),
        body TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'chat',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
        uploaded_by INTEGER NOT NULL REFERENCES users(id),
        original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL, is_delivery INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS ix_projects_user ON projects(user_id);
    CREATE INDEX IF NOT EXISTS ix_messages_project_id ON messages(project_id,id);
    CREATE INDEX IF NOT EXISTS ix_files_message ON files(message_id);
"""

POSTGRES_SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'client' CHECK(role IN ('client', 'admin')),
        created_at TEXT NOT NULL DEFAULT (to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))
    );
    CREATE TABLE IF NOT EXISTS projects (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id),
        title TEXT NOT NULL, project_type TEXT NOT NULL, description TEXT NOT NULL,
        budget TEXT NOT NULL DEFAULT '', desired_date TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'novo',
        created_at TEXT NOT NULL DEFAULT (to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')),
        updated_at TEXT NOT NULL DEFAULT (to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')),
        delivered_at TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id),
        body TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'chat',
        created_at TEXT NOT NULL DEFAULT (to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))
    );
    CREATE TABLE IF NOT EXISTS files (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        message_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,
        uploaded_by BIGINT NOT NULL REFERENCES users(id),
        original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
        size_bytes BIGINT NOT NULL, is_delivery INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS'))
    );
    CREATE INDEX IF NOT EXISTS ix_projects_user ON projects(user_id);
    CREATE INDEX IF NOT EXISTS ix_messages_project_id ON messages(project_id,id);
    CREATE INDEX IF NOT EXISTS ix_files_message ON files(message_id);
    -- Sem políticas públicas: bloqueia leitura de clientes pelo REST da Supabase.
    -- O backend Flask acessa por conexão SQL como proprietário das tabelas.
    ALTER TABLE users ENABLE ROW LEVEL SECURITY;
    ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
    ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
    ALTER TABLE files ENABLE ROW LEVEL SECURITY;
"""


def initialize_database():
    if DATABASE_URL:
        conn = connect_database()
        try:
            for statement in POSTGRES_SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SQLITE_SCHEMA)
            conn.commit()
        finally:
            conn.close()


initialize_database()


def br_datetime(raw):
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(str(raw)).replace(tzinfo=timezone.utc)
        try:
            dt = dt.astimezone(ZoneInfo(os.getenv("APP_TZ", "America/Sao_Paulo")))
        except ZoneInfoNotFoundError:
            pass
        return dt.strftime("%d/%m/%Y às %H:%M")
    except (ValueError, TypeError):
        return str(raw)


@app.template_filter("br_date")
def br_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return "—"


@app.context_processor
def global_template_context():
    return {
        "site_name": APP_NAME,
        "csrf_token": session.get("csrf_token", ""),
        "current_user": getattr(g, "user", None),
        "status_labels": STATUSES,
        "type_labels": TYPES,
        "format_datetime": br_datetime,
        "max_file_mb": MAX_FILE_MB,
        "max_files": MAX_FILES,
    }


@app.before_request
def load_identity_and_check_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = get_db().execute(
            "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if g.user is None:
            session.pop("user_id", None)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        received = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(session.get("csrf_token", ""), received):
            abort(400, description="Formulário expirado. Atualize a página e tente novamente.")


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if getattr(g, "user", None) and request.path != "/health":
        response.headers["Cache-Control"] = "private, no-store"
    return response


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if g.user is None:
            flash("Entre na sua conta para continuar.", "info")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def get_project_authorized(project_id):
    project = get_db().execute("""
        SELECT p.*, u.name AS client_name, u.email AS client_email
        FROM projects p JOIN users u ON u.id = p.user_id WHERE p.id = ?
    """, (project_id,)).fetchone()
    if not project:
        abort(404)
    if g.user["role"] != "admin" and project["user_id"] != g.user["id"]:
        abort(403)
    return project


def provided_uploads():
    return [f for f in request.files.getlist("attachments") if f and f.filename]


def validate_uploads(files):
    if len(files) > MAX_FILES:
        raise ValueError(f"Envie até {MAX_FILES} arquivos por vez.")
    results = []
    for f in files:
        name = secure_filename(f.filename)
        if not name or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Tipo de arquivo não permitido: {f.filename}")
        pos = f.stream.tell()
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(pos)
        if size > MAX_FILE_MB * 1024 * 1024:
            raise ValueError(f"O arquivo {name} excede {MAX_FILE_MB} MB.")
        if size <= 0:
            raise ValueError(f"O arquivo {name} está vazio.")
        results.append((f, name, size))
    return results


def object_url(stored):
    return (f"{STORAGE_URL}/storage/v1/object/"
            f"{quote(STORAGE_BUCKET, safe='')}/{quote(stored, safe='')}")


def storage_headers(content_type=None):
    result = {"apikey": STORAGE_KEY}
    # Chaves novas sb_secret_ não são JWT e NÃO devem ser usadas como Bearer.
    # A chave service_role legada é um JWT e pode ser enviada com ambos headers.
    if not STORAGE_KEY.startswith("sb_secret_"):
        result["Authorization"] = f"Bearer {STORAGE_KEY}"
    if content_type:
        result["Content-Type"] = content_type
    return result


def save_object(upload, stored):
    upload.stream.seek(0)
    if CLOUD_STORAGE:
        raw = upload.stream.read()
        req = Request(object_url(stored), data=raw, method="POST",
                      headers=storage_headers("application/octet-stream"))
        with urlopen(req, timeout=40) as response:
            if response.status not in (200, 201):
                raise RuntimeError("Falha ao armazenar arquivo na nuvem.")
    else:
        upload.save(UPLOAD_DIR / stored)


def delete_object(stored):
    if CLOUD_STORAGE:
        url = f"{STORAGE_URL}/storage/v1/object/{quote(STORAGE_BUCKET, safe='')}"
        req = Request(url, data=json.dumps({"prefixes": [stored]}).encode("utf-8"),
                      method="DELETE", headers=storage_headers("application/json"))
        try:
            with urlopen(req, timeout=15):
                pass
        except (HTTPError, URLError, TimeoutError) as error:
            app.logger.warning("Não foi possível limpar anexo de upload abortado: %s", type(error).__name__)
    else:
        (UPLOAD_DIR / stored).unlink(missing_ok=True)


def load_object(stored):
    req = Request(object_url(stored), headers=storage_headers(), method="GET")
    with urlopen(req, timeout=40) as response:
        return response.read()


def save_uploads(db, validated, project_id, message_id, delivery=False):
    saved = []
    try:
        for upload, name, size in validated:
            stored = f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
            save_object(upload, stored)
            saved.append(stored)
            db.execute("""
                INSERT INTO files (project_id,message_id,uploaded_by,original_name,stored_name,size_bytes,is_delivery)
                VALUES (?,?,?,?,?,?,?)
            """, (project_id, message_id, g.user["id"], name, stored, size, int(delivery)))
        return saved
    except Exception:
        for stored in saved:
            delete_object(stored)
        raise


def valid_email(value):
    return bool(re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value or ""))


def send_notification(recipient, subject, body):
    """Envia por Apps Script HTTPS, Resend ou SMTP; erros não apagam dados."""
    provider = os.getenv("MAIL_PROVIDER", "smtp").strip().lower()
    subject = " ".join(str(subject).splitlines())[:240]
    if not valid_email(recipient):
        app.logger.warning("E-mail não enviado: destinatário inválido.")
        return False

    if provider == "appscript":
        endpoint = os.getenv("GOOGLE_MAIL_URL", "").strip()
        token = os.getenv("DEVFORGE_MAIL_TOKEN", "").strip()
        if not (endpoint.startswith("https://script.google.com/macros/s/") and endpoint.endswith("/exec") and len(token) >= 32):
            app.logger.error("Apps Script não configurado. Defina GOOGLE_MAIL_URL (/exec) e DEVFORGE_MAIL_TOKEN (32+ caracteres).")
            return False
        admin_emails = [x.strip().lower() for x in os.getenv("ADMIN_EMAILS", "loigi2704@gmail.com,luigi.benicio@gmail.com").split(",") if x.strip()]
        kind = "admin" if recipient.lower() in admin_emails else "client"
        data = json.dumps({"token": token, "to": recipient, "kind": kind,
                           "subject": subject, "body": str(body)[:25000]}, ensure_ascii=False).encode("utf-8")
        req = Request(endpoint, data=data, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "DevForge-Studio/2.0",
        })
        try:
            with urlopen(req, timeout=35) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok") is True:
                return True
            app.logger.error("Apps Script recusou envio: %s", payload.get("error", "sem detalhe"))
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            app.logger.error("Apps Script sem confirmação de envio: %s", type(error).__name__)
        return False

    if provider == "resend":
        key = os.getenv("RESEND_API_KEY", "").strip()
        sender = os.getenv("RESEND_FROM", "").strip()
        if not key or not sender:
            app.logger.warning("Resend não configurado: defina RESEND_API_KEY e RESEND_FROM.")
            return False
        payload = json.dumps({
            "from": sender, "to": [recipient], "subject": subject, "text": body,
        }).encode("utf-8")
        req = Request("https://api.resend.com/emails", data=payload, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "User-Agent": "DevForge-Studio/1.0",
        })
        try:
            with urlopen(req, timeout=12) as resp:
                return 200 <= resp.status < 300
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            app.logger.error("Falha na API de e-mail Resend: %s", error)
            return False

    if provider != "smtp":
        app.logger.warning("MAIL_PROVIDER inválido: %s", provider)
        return False
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", username).strip()
    if not all((host, sender)):
        app.logger.warning("E-mail não enviado: configure SMTP_HOST e SMTP_FROM.")
        return False
    port = int(os.getenv("SMTP_PORT", "465"))
    use_ssl = os.getenv("SMTP_SSL", "1") == "1"
    msg = EmailMessage()
    try:
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content(body)
        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port, timeout=12)
        else:
            smtp = smtplib.SMTP(host, port, timeout=12)
        with smtp:
            if not use_ssl:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException, ValueError) as error:
        app.logger.error("Falha no envio SMTP: %s", error)
        return False


def admin_recipients():
    configured = os.getenv("ADMIN_EMAILS", "").strip()
    if configured:
        addresses = [x.strip().lower() for x in configured.replace(";", ",").split(",")]
    else:
        addresses = [os.getenv("ADMIN_EMAIL", "").strip().lower()]
    return list(dict.fromkeys(x for x in addresses if valid_email(x)))


def notify_admin(subject, body):
    recipients = admin_recipients()
    if not recipients:
        app.logger.warning("Configure ADMIN_EMAILS para receber solicitações.")
        return False
    results = [send_notification(email, subject, body) for email in recipients]
    return all(results)


def project_url(project_id):
    return f"{SITE_URL}{url_for('project_detail', project_id=project_id)}"


def bootstrap_admin_if_requested():
    """Cria o primeiro admin automaticamente em hospedagens sem terminal interativo.

    Só usa as variáveis de ambiente quando AINDA NÃO EXISTE um administrador.
    Após iniciar pela primeira vez, remova BOOTSTRAP_ADMIN_PASSWORD do host.
    """
    name = os.getenv("BOOTSTRAP_ADMIN_NAME", "Administrador").strip()
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not (email and password):
        return
    if not valid_email(email) or len(password) < 12 or len(name) < 2:
        app.logger.error("Bootstrap admin ignorado: e-mail/nome inválido ou senha menor que 12 caracteres.")
        return
    with app.app_context():
        db = get_db()
        existing = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin'").fetchone()["c"]
        if existing:
            return
        user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        hashed = generate_password_hash(password)
        if user:
            db.execute("UPDATE users SET name=?,password_hash=?,role='admin' WHERE id=?",
                       (name, hashed, user["id"]))
        else:
            db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')",
                       (name, email, hashed))
        db.commit()
        app.logger.info("Primeira conta de administrador criada com as variáveis de ambiente.")


bootstrap_admin_if_requested()


@app.get("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not (2 <= len(name) <= 90 and 5 <= len(email) <= 254 and valid_email(email)):
            flash("Confira seu nome e endereço de e-mail.", "error")
        elif not 8 <= len(password) <= 128:
            flash("A senha deve ter pelo menos 8 caracteres.", "error")
        else:
            try:
                db = get_db()
                cur = db.execute(
                    "INSERT INTO users(name,email,password_hash) VALUES(?,?,?)",
                    (name, email, generate_password_hash(password)),
                )
                db.commit()
                session.clear()
                session["user_id"] = cur.lastrowid
                session["csrf_token"] = secrets.token_urlsafe(32)
                flash("Conta criada! Agora conte sobre seu projeto.", "success")
                return redirect(url_for("new_request"))
            except Exception as error:
                # SQLite e PostgreSQL informam unicidade de modos diferentes.
                if isinstance(error, sqlite3.IntegrityError) or (DATABASE_URL and getattr(error, "sqlstate", "") == "23505"):
                    get_db().rollback()
                    flash("Este e-mail já possui cadastro. Faça login.", "error")
                else:
                    raise
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per 15 minutes", methods=["POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash(f"Bem-vindo(a), {user['name']}!", "success")
            destination = request.form.get("next", "")
            if destination.startswith("/") and not destination.startswith("//") and "\\" not in destination:
                return redirect(destination)
            return redirect(url_for("dashboard"))
        flash("E-mail ou senha inválidos.", "error")
    return render_template("auth.html", mode="login", next_path=request.args.get("next", ""))


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("Você saiu da sua conta.", "info")
    return redirect(url_for("home"))


@app.get("/dashboard")
@login_required
def dashboard():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    projects = get_db().execute("""
        SELECT p.*, (SELECT COUNT(*) FROM messages m WHERE m.project_id=p.id AND m.kind='chat') AS message_count
        FROM projects p WHERE p.user_id=? ORDER BY p.updated_at DESC, p.id DESC
    """, (g.user["id"],)).fetchall()
    return render_template("dashboard.html", projects=projects)


@app.route("/request/new", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def new_request():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        project_type = request.form.get("project_type", "")
        description = request.form.get("description", "").strip()
        budget = request.form.get("budget", "").strip()[:80]
        desired_date = request.form.get("desired_date", "").strip()
        if not (4 <= len(title) <= 120) or project_type not in TYPES or not (20 <= len(description) <= 12000):
            flash("Preencha o título (4+ caracteres), tipo e descrição (20+ caracteres).", "error")
            return render_template("new_request.html"), 400
        if desired_date:
            try:
                datetime.strptime(desired_date, "%Y-%m-%d")
            except ValueError:
                flash("Prazo desejado inválido.", "error")
                return render_template("new_request.html"), 400
        try:
            validated = validate_uploads(provided_uploads())
        except ValueError as error:
            flash(str(error), "error")
            return render_template("new_request.html"), 400
        db = get_db()
        saved = []
        try:
            cur = db.execute("""
                INSERT INTO projects(user_id,title,project_type,description,budget,desired_date)
                VALUES(?,?,?,?,?,?)
            """, (g.user["id"], title, project_type, description, budget, desired_date))
            project_id = cur.lastrowid
            if validated:
                msg_id = db.execute("""
                    INSERT INTO messages(project_id,user_id,body,kind) VALUES(?,?,?,?)
                """, (project_id, g.user["id"], "Arquivos anexados ao pedido inicial.", "upload")).lastrowid
                saved = save_uploads(db, validated, project_id, msg_id)
            db.commit()
        except Exception:
            db.rollback()
            for filename in saved:
                delete_object(filename)
            raise
        file_names = ", ".join(name for _, name, _ in validated) or "Nenhum"
        notified = notify_admin(f"[NOVO PEDIDO #{project_id}] {title}", (
            f"Novo pedido de software!\n\nCliente: {g.user['name']}\n"
            f"E-mail: {g.user['email']}\nTipo: {TYPES[project_type]}\n"
            f"Título: {title}\nOrçamento informado: {budget or 'Não informado'}\n"
            f"Prazo desejado: {desired_date or 'Não informado'}\n\n"
            f"Descrição:\n{description}\n\nArquivos: {file_names}\n\n"
            f"Acessar: {project_url(project_id)}"
        ))
        if not notified:
            flash("Pedido salvo, mas o aviso por e-mail falhou. O administrador pode vê-lo no painel.", "warning")
        flash("Pedido enviado! Acompanhe o andamento e converse pelo chat.", "success")
        return redirect(url_for("project_detail", project_id=project_id))
    return render_template("new_request.html")


@app.get("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = get_project_authorized(project_id)
    attached = get_db().execute(
        "SELECT * FROM files WHERE project_id=? ORDER BY id DESC", (project_id,)
    ).fetchall()
    return render_template("project.html", project=project, attached=attached)


@app.get("/api/projects/<int:project_id>/messages")
@login_required
@limiter.limit("120 per minute")
def messages_api(project_id):
    get_project_authorized(project_id)
    try:
        after = max(0, int(request.args.get("after", "0")))
    except ValueError:
        abort(400)
    rows = get_db().execute("""
        SELECT m.*, u.name AS author_name, u.role AS author_role
        FROM messages m JOIN users u ON u.id=m.user_id
        WHERE m.project_id=? AND m.id>? ORDER BY m.id LIMIT 150
    """, (project_id, after)).fetchall()
    db = get_db()
    results = []
    for item in rows:
        files = db.execute(
            "SELECT id, original_name, size_bytes, is_delivery FROM files WHERE message_id=? ORDER BY id",
            (item["id"],),
        ).fetchall()
        results.append({
            "id": item["id"], "author": item["author_name"],
            "is_mine": item["user_id"] == g.user["id"],
            "is_admin": item["author_role"] == "admin", "body": item["body"],
            "kind": item["kind"], "created_at": br_datetime(item["created_at"]),
            "files": [{
                "name": f["original_name"], "size": round(f["size_bytes"] / 1024),
                "is_delivery": bool(f["is_delivery"]),
                "url": url_for("download_file", file_id=f["id"]),
            } for f in files],
        })
    return jsonify({"messages": results, "has_more": len(rows) == 150})


@app.post("/projects/<int:project_id>/messages")
@login_required
@limiter.limit("40 per hour")
def send_message(project_id):
    project = get_project_authorized(project_id)
    body = request.form.get("body", "").strip()
    if len(body) > 5000:
        flash("Mensagem muito longa (máximo de 5.000 caracteres).", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    try:
        validated = validate_uploads(provided_uploads())
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("project_detail", project_id=project_id))
    if not body and not validated:
        flash("Escreva uma mensagem ou selecione um arquivo.", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    db = get_db()
    saved = []
    try:
        msg_id = db.execute(
            "INSERT INTO messages(project_id,user_id,body) VALUES(?,?,?)",
            (project_id, g.user["id"], body),
        ).lastrowid
        if validated:
            saved = save_uploads(db, validated, project_id, msg_id)
        db.execute("UPDATE projects SET updated_at=datetime('now') WHERE id=?", (project_id,))
        db.commit()
    except Exception:
        db.rollback()
        for filename in saved:
            delete_object(filename)
        raise
    file_summary = ", ".join(name for _, name, _ in validated)
    detail = body or "(Somente anexos)"
    note = (f"Mensagem do pedido #{project_id}: {project['title']}\n\n"
            f"Enviada por: {g.user['name']} ({g.user['email']})\n\n"
            f"{detail}\n\nAnexos: {file_summary or 'Nenhum'}\n\n"
            f"Acessar o chat: {project_url(project_id)}")
    if g.user["role"] == "admin":
        notified = send_notification(project["client_email"], f"[RESPOSTA #{project_id}] {project['title']}", note)
    else:
        notified = notify_admin(f"[CHAT #{project_id}] {project['title']}", note)
    if not notified:
        flash("Mensagem registrada no chat, mas o aviso por e-mail falhou.", "warning")
    flash("Mensagem enviada e registrada no pedido.", "success")
    return redirect(url_for("project_detail", project_id=project_id) + "#chat")


@app.post("/projects/<int:project_id>/status")
@admin_required
def change_status(project_id):
    project = get_project_authorized(project_id)
    status = request.form.get("status", "")
    if status not in STATUSES:
        abort(400)
    db = get_db()
    db.execute("UPDATE projects SET status=?, updated_at=datetime('now') WHERE id=?", (status, project_id))
    db.commit()
    send_notification(project["client_email"], f"[STATUS #{project_id}] {STATUSES[status]}", (
        f"Olá, {project['client_name']}!\n\nO status do seu pedido "
        f"'{project['title']}' mudou para: {STATUSES[status]}.\n\n"
        f"Acompanhe em: {project_url(project_id)}"
    ))
    flash("Status atualizado.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.post("/projects/<int:project_id>/deliver")
@admin_required
@limiter.limit("20 per hour")
def deliver(project_id):
    project = get_project_authorized(project_id)
    note = request.form.get("note", "").strip()
    if len(note) > 5000:
        flash("Texto da entrega muito longo.", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    try:
        validated = validate_uploads(provided_uploads())
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("project_detail", project_id=project_id))
    if not validated:
        flash("Adicione ao menos um arquivo para enviar a entrega.", "error")
        return redirect(url_for("project_detail", project_id=project_id))
    db = get_db()
    saved = []
    try:
        msg_id = db.execute("""
            INSERT INTO messages(project_id,user_id,body,kind) VALUES(?,?,?,?)
        """, (project_id, g.user["id"], note or "Seu software está pronto! Arquivos da entrega abaixo.", "delivery")).lastrowid
        saved = save_uploads(db, validated, project_id, msg_id, delivery=True)
        db.execute("""
            UPDATE projects SET status='entregue', delivered_at=datetime('now'),
            updated_at=datetime('now') WHERE id=?
        """, (project_id,))
        db.commit()
    except Exception:
        db.rollback()
        for filename in saved:
            delete_object(filename)
        raise
    files = ", ".join(name for _, name, _ in validated)
    send_notification(project["client_email"], f"[ENTREGA #{project_id}] Seu projeto está pronto", (
        f"Olá, {project['client_name']}!\n\nSeu pedido '{project['title']}' foi entregue.\n\n"
        f"Mensagem: {note or 'Seu projeto está pronto.'}\n"
        f"Arquivos: {files}\n\nEntre para baixar: {project_url(project_id)}\n\n"
        "Para pedir ajustes, responda pelo chat do projeto."
    ))
    flash("Entrega enviada e disponível para o cliente!", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.get("/files/<int:file_id>/download")
@login_required
def download_file(file_id):
    file = get_db().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not file:
        abort(404)
    get_project_authorized(file["project_id"])
    if CLOUD_STORAGE:
        try:
            content = load_object(file["stored_name"])
        except (HTTPError, URLError, TimeoutError) as error:
            app.logger.error("Falha ao baixar anexo privado da nuvem: %s", type(error).__name__)
            abort(503, description="Armazenamento temporariamente indisponível.")
        return send_file(io.BytesIO(content), as_attachment=True,
                         download_name=file["original_name"], mimetype="application/octet-stream")
    return send_from_directory(
        UPLOAD_DIR, file["stored_name"], as_attachment=True,
        download_name=file["original_name"], mimetype="application/octet-stream",
    )


@app.get("/admin")
@admin_required
def admin_dashboard():
    q = request.args.get("q", "").strip()[:100]
    status = request.args.get("status", "").strip()
    if status and status not in STATUSES:
        abort(400)
    conds = []
    params = []
    if q:
        conds.append("(p.title LIKE ? OR u.name LIKE ? OR u.email LIKE ? OR CAST(p.id AS TEXT) LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if status:
        conds.append("p.status=?")
        params.append(status)
    where = " WHERE " + " AND ".join(conds) if conds else ""
    db = get_db()
    projects = db.execute("""
        SELECT p.*, u.name AS client_name, u.email AS client_email,
        (SELECT COUNT(*) FROM messages m WHERE m.project_id=p.id AND m.kind='chat') AS message_count
        FROM projects p JOIN users u ON u.id=p.user_id
    """ + where + " ORDER BY p.updated_at DESC,p.id DESC LIMIT 250", params).fetchall()
    stats_rows = db.execute("SELECT status, COUNT(*) AS total FROM projects GROUP BY status").fetchall()
    stats = {s: 0 for s in STATUSES}
    stats.update({row["status"]: row["total"] for row in stats_rows})
    total_users = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='client'").fetchone()["c"]
    return render_template("admin.html", projects=projects, stats=stats, total_users=total_users, q=q, selected_status=status)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": APP_NAME})


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(429)
def display_error(error):
    msgs = {
        400: "A solicitação não pôde ser processada. Confira o formulário.",
        403: "Você não tem permissão para acessar esta página.",
        404: "Não encontramos a página ou o arquivo.",
        413: "O envio excedeu o limite total de 50 MB. Envie menos arquivos.",
        429: "Muitas tentativas. Aguarde antes de tentar novamente.",
    }
    code = getattr(error, "code", 500)
    return render_template("error.html", error_code=code, error_message=msgs.get(code)), code


def create_admin_interactive():
    print("\n=== Criar ou redefinir administrador ===")
    name = input("Nome do administrador: ").strip()
    email = input("E-mail do administrador: ").strip().lower()
    password = getpass("Senha (mínimo 8 caracteres): ")
    confirmation = getpass("Repita a senha: ")
    if len(name) < 2 or "@" not in email or len(password) < 8 or password != confirmation:
        print("Dados inválidos ou senhas diferentes. Nenhuma alteração realizada.")
        sys.exit(1)
    with app.app_context():
        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        hashed = generate_password_hash(password)
        if existing:
            db.execute("UPDATE users SET name=?,password_hash=?,role='admin' WHERE id=?", (name, hashed, existing["id"]))
        else:
            db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')", (name, email, hashed))
        db.commit()
    print("Administrador preparado! Agora abra /login e entre com esse e-mail e senha.")
    if os.getenv("ADMIN_EMAIL", "").strip().lower() != email:
        print("DICA: coloque ADMIN_EMAIL=" + email + " no arquivo .env para receber pedidos.")


def test_email_cli():
    recipients = admin_recipients()
    if not recipients:
        print("Preencha ADMIN_EMAILS no .env antes do teste.")
        sys.exit(1)
    worked = notify_admin("Teste de e-mail DevForge", "O Apps Script do DevForge está funcionando!")
    print("E-mail de teste enviado a: " + ", ".join(recipients) if worked else "Falha no envio. Revise o .env e os logs.")
    if not worked:
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "init-admin":
        create_admin_interactive()
    elif len(sys.argv) >= 2 and sys.argv[1] == "test-email":
        test_email_cli()
    elif len(sys.argv) >= 2:
        print("Comandos: python app.py | python app.py init-admin | python app.py test-email")
        sys.exit(2)
    else:
        app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "0") == "1")
