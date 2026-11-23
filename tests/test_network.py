import http.client
from pathlib import Path
import socket
import ssl
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import network_probe


class KeylessNetworkTests(unittest.TestCase):
    def test_401_proves_api_reachable_without_sending_credentials(self):
        transport = MagicMock(return_value=(401, 'application/json', b'{}', {}))
        result = network_probe('api', 'http://proxy:7890', transport=transport)
        self.assertTrue(result['ok'])
        self.assertTrue(result['reachable'])
        self.assertEqual(result['status'], 401)
        args = transport.call_args.args
        self.assertEqual(args[:2], ('api.themoviedb.org', '/3/configuration'))
        self.assertEqual(args[3], {})
        self.assertNotIn('api_key', args[1])
        self.assertEqual(transport.call_args.kwargs['timeout'], 6)

    def test_images_require_an_actual_image_response(self):
        good = lambda *a, **k: (200, 'image/jpeg', b'image', {})
        bad = lambda *a, **k: (200, 'text/html', b'error page', {})
        self.assertTrue(network_probe('image', '', transport=good)['ok'])
        result = network_probe('image', '', transport=bad)
        self.assertFalse(result['ok'])
        self.assertTrue(result['reachable'])

    def test_proxy_port_probe_and_direct_mode(self):
        connector = MagicMock()
        self.assertTrue(network_probe('proxy', 'http://proxy:7890', connector=connector)['ok'])
        connector.assert_called_once_with(('proxy', 7890), timeout=4)
        connector.reset_mock()
        result = network_probe('proxy', '', connector=connector)
        self.assertTrue(result['skipped'])
        connector.assert_not_called()

    def test_network_failures_are_specific_and_do_not_echo_secrets(self):
        cases = [
            (socket.gaierror('secret'), 'dns'),
            (TimeoutError('secret'), 'timeout'),
            (ConnectionRefusedError('secret'), 'refused'),
            (ssl.SSLCertVerificationError('secret'), 'certificate'),
            (ssl.SSLError('secret'), 'tls'),
            (OSError('Tunnel connection failed: 407 secret'), 'proxy_tunnel'),
            (http.client.BadStatusLine('secret'), 'protocol'),
        ]
        for error, code in cases:
            with self.subTest(code=code):
                result = network_probe('api', 'http://proxy:7890', transport=MagicMock(side_effect=error))
                self.assertFalse(result['ok'])
                self.assertEqual(result['error'], code)
                self.assertNotIn('secret', str(result))

    def test_invalid_target_cannot_contact_an_arbitrary_host(self):
        with self.assertRaises(ValueError):
            network_probe('https://evil.example/', '')
