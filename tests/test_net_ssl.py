"""HTTPS 证书上下文回退 (src/net_ssl.py) 的离线测试。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_net_ssl.py
"""
import os
import ssl
import sys
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import net_ssl  # noqa: E402


class NetSslTest(unittest.TestCase):
    def setUp(self):
        net_ssl.ssl_contexts.cache_clear()

    def tearDown(self):
        net_ssl.ssl_contexts.cache_clear()

    def test_ssl_contexts_includes_truststore_and_certifi(self):
        contexts = net_ssl.ssl_contexts()
        self.assertGreaterEqual(len(contexts), 2)
        self.assertEqual(type(contexts[0]).__module__, "truststore._api")
        self.assertIsInstance(contexts[1], ssl.SSLContext)

    def test_open_url_retries_next_context_on_cert_verify_error(self):
        ctx1, ctx2 = object(), object()
        calls = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(context)
            if context is ctx1:
                raise URLError(ssl.SSLCertVerificationError(1, "unable to get issuer certificate"))
            return "ok"

        with mock.patch.object(net_ssl, "ssl_contexts", return_value=(ctx1, ctx2)), \
             mock.patch.object(net_ssl, "urlopen", side_effect=fake_urlopen):
            result = net_ssl.open_url(Request("https://example.com"), timeout=5)

        self.assertEqual(result, "ok")
        self.assertEqual(calls, [ctx1, ctx2])

    def test_open_url_does_not_retry_on_http_error(self):
        ctx1, ctx2 = object(), object()
        calls = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(context)
            raise HTTPError("https://example.com", 404, "Not Found", {}, None)

        with mock.patch.object(net_ssl, "ssl_contexts", return_value=(ctx1, ctx2)), \
             mock.patch.object(net_ssl, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(HTTPError):
                net_ssl.open_url(Request("https://example.com"), timeout=5)

        self.assertEqual(calls, [ctx1])

    def test_open_url_does_not_retry_on_non_cert_url_error(self):
        ctx1, ctx2 = object(), object()
        calls = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(context)
            raise URLError(TimeoutError("timed out"))

        with mock.patch.object(net_ssl, "ssl_contexts", return_value=(ctx1, ctx2)), \
             mock.patch.object(net_ssl, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(URLError):
                net_ssl.open_url(Request("https://example.com"), timeout=5)

        self.assertEqual(calls, [ctx1])

    def test_open_url_reraises_when_all_contexts_fail(self):
        ctx1, ctx2 = object(), object()
        calls = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(context)
            raise URLError(ssl.SSLCertVerificationError(1, "certificate expired"))

        with mock.patch.object(net_ssl, "ssl_contexts", return_value=(ctx1, ctx2)), \
             mock.patch.object(net_ssl, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(URLError):
                net_ssl.open_url(Request("https://example.com"), timeout=5)

        self.assertEqual(calls, [ctx1, ctx2])


if __name__ == "__main__":
    unittest.main()
