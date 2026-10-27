"""Envio HTTPS ao Apps Script com diagnóstico e redirecionamento controlado.

A solicitação POST não é repetida automaticamente: na perda de confirmação,
o Google pode já ter enviado o e-mail, e repetir criaria duplicatas.
"""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MailGatewayError(Exception):
    """Erro sem dados confidenciais, seguro para exibir no painel admin."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect)
_REDIRECT_STATUS = (301, 302, 303, 307, 308)
_TRANSIENT_GET_STATUS = (404, 429, 500, 502, 503, 504)


def _read_once(req: Request, timeout: int):
    try:
        with _OPENER.open(req, timeout=timeout) as res:
            return res.status, res.headers, res.read(16_385)
    except HTTPError as err:
        try:
            return err.code, err.headers, err.read(16_385)
        finally:
            err.close()


def deliver_appscript(endpoint: str, data: bytes) -> None:
    """Dispara POST uma vez, acompanha redirecionamentos com GET e valida JSON.

    Lança MailGatewayError com código de diagnóstico e sem vazar token.
    """
    request = Request(endpoint, data=data, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "DevForge-Studio/2.1",
    })
    try:
        status, headers, body = _read_once(request, timeout=30)
        current = endpoint
        for _ in range(5):
            if status not in _REDIRECT_STATUS:
                break
            location = headers.get("Location", "")
            target = urljoin(current, location)
            parsed = urlsplit(target)
            if parsed.scheme != "https" or parsed.hostname != "script.googleusercontent.com":
                raise MailGatewayError("redirecionamento_inesperado")
            current = target
            # Este GET recupera a confirmação do ContentService. Em falhas
            # transitórias, repetir somente GET não reenvia o e-mail.
            for attempt in range(3):
                status, headers, body = _read_once(Request(current, method="GET", headers={
                    "User-Agent": "DevForge-Studio/2.1",
                }), timeout=15)
                if status not in _TRANSIENT_GET_STATUS or attempt == 2:
                    break
                time.sleep(1 + attempt)
        else:
            raise MailGatewayError("redirecionamentos_excessivos")
    except (URLError, TimeoutError, OSError) as exc:
        raise MailGatewayError(f"rede_{type(exc).__name__}") from exc

    if status != 200:
        raise MailGatewayError(f"http_{status}")
    if len(body) > 16_384:
        raise MailGatewayError("resposta_muito_grande")
    try:
        answer = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise MailGatewayError("resposta_nao_json") from exc
    if not isinstance(answer, dict):
        raise MailGatewayError("resposta_json_invalida")
    if answer.get("ok") is not True:
        reason = str(answer.get("error", "rejeitado"))
        # Só retornar códigos curtos do Apps Script, nunca texto arbitrário.
        if not reason.isascii() or not reason.replace("_", "").isalnum() or len(reason) > 50:
            reason = "rejeitado"
        raise MailGatewayError(reason)
