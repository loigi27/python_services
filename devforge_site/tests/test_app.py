"""Execute com: python -m pytest -q (após instalar requirements.txt e pytest)."""
import io
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="devforge_init_test_"))
os.environ.setdefault("SECRET_KEY", "test-strong-key-for-automated-tests")

import pytest
from werkzeug.security import generate_password_hash

import app as site


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite3"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(site, "DB_PATH", db_path)
    monkeypatch.setattr(site, "UPLOAD_DIR", uploads)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(site, "send_notification", lambda *args, **kwargs: True)
    site.initialize_database()
    site.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    with site.app.test_client() as test_client:
        yield test_client


def csrf(client):
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def register(client, name, email):
    client.get("/register")
    return client.post("/register", data={
        "csrf_token": csrf(client), "name": name, "email": email,
        "password": "senha-detalhada123",
    }, follow_redirects=True)


def login(client, email):
    client.get("/login")
    return client.post("/login", data={
        "csrf_token": csrf(client), "email": email,
        "password": "senha-detalhada123",
    }, follow_redirects=True)


def logout(client):
    return client.post("/logout", data={"csrf_token": csrf(client)})


def make_project(client):
    return client.post("/request/new", data={
        "csrf_token": csrf(client),
        "title": "Aplicativo para catálogo de livros",
        "project_type": "windows",
        "description": "Quero um software para gerenciar meus livros e mostrar uma lista organizada.",
        "attachments": (io.BytesIO(b"arquivo de referencia"), "referencia.txt"),
    }, content_type="multipart/form-data", follow_redirects=False)


def create_admin():
    with site.app.app_context():
        db = site.get_db()
        db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')", (
            "Dev Admin", "admin@example.com", generate_password_hash("senha-detalhada123")
        ))
        db.commit()


def test_full_flow_permissions_upload_chat_delivery(client):
    assert client.get("/health").json["ok"]
    assert register(client, "Cliente Teste", "cliente@example.com").status_code == 200
    result = make_project(client)
    assert result.status_code == 302
    assert result.location.endswith("/projects/1")
    assert b"cat\xc3\xa1logo" in client.get("/projects/1").data
    response = client.get("/api/projects/1/messages")
    assert response.status_code == 200
    assert response.json["messages"][0]["files"][0]["name"] == "referencia.txt"
    file_url = response.json["messages"][0]["files"][0]["url"]
    assert client.get(file_url).data == b"arquivo de referencia"

    assert client.post("/projects/1/messages", data={
        "csrf_token": csrf(client), "body": "Pode incluir pesquisa por autor?"
    }).status_code == 302

    logout(client)
    assert register(client, "Outro Cliente", "outro@example.com").status_code == 200
    assert client.get("/projects/1").status_code == 403
    assert client.get("/api/projects/1/messages").status_code == 403
    assert client.get(file_url).status_code == 403
    logout(client)

    create_admin()
    assert login(client, "admin@example.com").status_code == 200
    assert client.get("/admin").status_code == 200
    assert client.post("/projects/1/status", data={
        "csrf_token": csrf(client), "status": "desenvolvimento"
    }).status_code == 302
    delivery = client.post("/projects/1/deliver", data={
        "csrf_token": csrf(client), "note": "Versao pronta. Teste e envie ajustes.",
        "attachments": (io.BytesIO(b"arquivos de entrega"), "programa.zip"),
    }, content_type="multipart/form-data", follow_redirects=False)
    assert delivery.status_code == 302
    assert b"Entregue" in client.get("/projects/1").data
    msgs = client.get("/api/projects/1/messages").json["messages"]
    assert any(m["kind"] == "delivery" for m in msgs)
    zip_url = next(f["url"] for m in msgs for f in m["files"] if f["name"] == "programa.zip")
    assert client.get(zip_url).data == b"arquivos de entrega"
    logout(client)

    assert login(client, "cliente@example.com").status_code == 200
    assert client.get("/admin").status_code == 403
    assert client.get(zip_url).data == b"arquivos de entrega"


def test_csrf_and_blocked_upload(client):
    register(client, "Ana Teste", "ana@example.com")
    assert client.post("/request/new", data={"title": "Teste"}).status_code == 400
    response = client.post("/request/new", data={
        "csrf_token": csrf(client), "title": "Projeto de teste de arquivo",
        "project_type": "site", "description": "Quero montar um site de teste com referencias anexadas.",
        "attachments": (io.BytesIO(b"conteudo"), "payload.sh"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400
    with site.app.app_context():
        assert site.get_db().execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_messages_notify_admin_and_reply_to_client(client, monkeypatch):
    mail = []
    monkeypatch.setattr(site, "send_notification", lambda recipient, title, body: mail.append((recipient, title, body)) or True)
    register(client, "Pablo Teste", "pablo@example.com")
    make_project(client)
    assert mail[0][0] == "admin@example.com"
    assert "gerenciar meus livros" in mail[0][2]
    client.post("/projects/1/messages", data={
        "csrf_token": csrf(client), "body": "Preciso modificar a cor azul do layout."
    })
    assert mail[-1][0] == "admin@example.com"
    assert "cor azul" in mail[-1][2]
    logout(client)
    create_admin()
    login(client, "admin@example.com")
    client.post("/projects/1/messages", data={
        "csrf_token": csrf(client), "body": "Pode deixar, vou alterar."
    })
    assert mail[-1][0] == "pablo@example.com"
    assert "vou alterar" in mail[-1][2]
