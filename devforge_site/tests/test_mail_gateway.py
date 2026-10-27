"""Testes locais sem Flask e sem chamar Gmail ou Apps Script real."""
import json
import unittest
from unittest.mock import patch

import mail_gateway as gw

URL = "https://script.google.com/macros/s/ID/exec"
DEST = "https://script.googleusercontent.com/macros/echo?user_content_key=abc"
DATA = b'{"token":"secret"}'


class GatewayTests(unittest.TestCase):
    def test_redirect_then_json_ok(self):
        actions = [(302, {"Location": DEST}, b""), (200, {}, b'{"ok":true}')]
        with patch.object(gw, "_read_once", side_effect=actions) as fn:
            gw.deliver_appscript(URL, DATA)
            self.assertEqual(fn.call_count, 2)
            self.assertEqual(fn.call_args_list[0].args[0].get_method(), "POST")
            self.assertEqual(fn.call_args_list[1].args[0].get_method(), "GET")

    def test_get_retry_not_post(self):
        actions = [(302, {"Location": DEST}, b""), (404, {}, b""),
                   (200, {}, b'{"ok":true}')]
        with patch.object(gw, "_read_once", side_effect=actions) as fn, patch.object(gw.time, "sleep"):
            gw.deliver_appscript(URL, DATA)
            self.assertEqual([c.args[0].get_method() for c in fn.call_args_list], ["POST", "GET", "GET"])

    def test_wrong_token_reports_error(self):
        with patch.object(gw, "_read_once", return_value=(200, {}, b'{"ok":false,"error":"nao_autorizado"}')):
            with self.assertRaisesRegex(gw.MailGatewayError, "nao_autorizado"):
                gw.deliver_appscript(URL, DATA)

    def test_redirect_to_unknown_host_blocked(self):
        with patch.object(gw, "_read_once", return_value=(302, {"Location": "https://example.org/x"}, b"")):
            with self.assertRaisesRegex(gw.MailGatewayError, "redirecionamento_inesperado"):
                gw.deliver_appscript(URL, DATA)

    def test_html_response_not_accepted(self):
        with patch.object(gw, "_read_once", return_value=(200, {}, b"<html>error</html>")):
            with self.assertRaisesRegex(gw.MailGatewayError, "resposta_nao_json"):
                gw.deliver_appscript(URL, DATA)

    def test_http_error_reported(self):
        with patch.object(gw, "_read_once", return_value=(403, {}, b"Forbidden")):
            with self.assertRaisesRegex(gw.MailGatewayError, "http_403"):
                gw.deliver_appscript(URL, DATA)


if __name__ == "__main__":
    unittest.main()
