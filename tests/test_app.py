import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import State, ImageCache, create_server, route, validate_config, upstream


class ConfigTests(unittest.TestCase):
    def test_transport_tunnels_to_fixed_host_and_filters_private_headers(self):
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{}'
        response.getheader.side_effect = lambda name, default=None: 'application/json' if name == 'Content-Type' else default
        with patch('app.http.client.HTTPSConnection', return_value=conn):
            upstream('api.themoviedb.org', '/3/configuration?api_key=x', 'http://proxy:7890',
                     {'Authorization': 'Bearer test', 'Cookie': 'private', 'Proxy-Authorization': 'private'})
        conn.set_tunnel.assert_called_once_with('api.themoviedb.org', 443)
        headers = conn.request.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer test')
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('Proxy-Authorization', headers)
        conn.close.assert_called_once()

    def test_addresses(self):
        c = validate_config(dict(proxy_url=' http://192.168.1.2:7890/ ', public_url='https://tmdb.example.com/'))
        self.assertEqual(c['proxy_url'], 'http://192.168.1.2:7890')
        self.assertEqual(c['public_url'], 'https://tmdb.example.com')
        for value in ['http://user:pass@host:7890', 'https://host:7890', 'http://host:0', 'http://host:65536', 'http://host/path', 'http://host?x=1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config({'proxy_url': value})

    def test_routes_cannot_be_arbitrary_urls(self):
        self.assertEqual(route('/3/movie/123?api_key=test')[0], 'api.themoviedb.org')
        self.assertTrue(route('/t/p/w500/a.jpg')[2])
        self.assertFalse(route('/3/account/123')[2])
        for path in ['https://evil.example/3/movie', '//evil.example/3/movie', '/t/p/w500/../../secret.jpg', '/t/p/w500/a.jpg?url=https://evil.example', '/3/../x', '/3/%2e%2e/x']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                route(path)

    def test_cache_evicts_and_is_persistent(self):
        with tempfile.TemporaryDirectory() as d:
            cache = ImageCache(d)
            cache.put('a', 'image/png', b'a' * 10, 15)
            cache.put('b', 'image/png', b'b' * 10, 15)
            self.assertIsNone(cache.get('a'))
            self.assertEqual(ImageCache(d).get('b'), ('image/png', b'b' * 10))
            cache.clear()
            self.assertEqual(cache.info()['bytes'], 0)

    def test_access_key_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            first = State(d, 'test-password-123')
            second = State(d, 'test-password-123')
            self.assertEqual(first.snapshot(), second.snapshot())


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []

        def fake(host, path, proxy, headers):
            self.calls.append((host, path, proxy, dict(headers)))
            if host == 'image.tmdb.org':
                return 200, 'image/jpeg', b'fake-image', {}
            return 200, 'application/json', b'{"success":true}', {}

        self.state = State(self.temp.name, 'test-password-123', fake)
        self.server = create_server('127.0.0.1', 0, self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None, method=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        h = {'Cookie': self.cookie}
        if data is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        c.request(method or ('POST' if data is not None else 'GET'), path, json.dumps(data) if data is not None else None, h)
        response = c.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), body)
        c.close()
        return result

    def login(self):
        status, h, _ = self.request('/admin/login', {'password': 'test-password-123'})
        self.assertEqual(status, 200)
        self.cookie = h['Set-Cookie'].split(';')[0]

    def relay(self, suffix):
        return '/r/' + self.state.snapshot()['access_key'] + suffix

    def test_admin_requires_login_and_logout_revokes_session(self):
        self.assertEqual(self.request('/admin/state')[0], 401)
        self.assertEqual(self.request('/admin/login', {'password': 'wrong'})[0], 401)
        self.login()
        self.assertEqual(self.request('/admin/state')[0], 200)
        self.assertEqual(self.request('/admin/logout', {})[0], 200)
        self.assertEqual(self.request('/admin/state')[0], 401)

    def test_bad_token_and_bad_routes_never_contact_upstream(self):
        self.assertEqual(self.request('/r/wrong/3/configuration')[0], 403)
        self.assertEqual(self.request(self.relay('/t/p/w500/../x.jpg'))[0], 404)
        self.assertEqual(self.calls, [])

    def test_settings_affect_proxy_and_preserve_query_auth(self):
        self.login()
        self.assertEqual(self.request('/admin/config', dict(proxy_url='http://192.168.1.2:7890', public_url='https://relay.example', cache_mb=128))[0], 200)
        self.assertEqual(self.request(self.relay('/3/movie/123?api_key=secret'), headers={'Authorization': 'Bearer test', 'Cookie': 'private=never'})[0], 200)
        host, path, proxy, headers = self.calls[-1]
        self.assertEqual((host, path, proxy), ('api.themoviedb.org', '/3/movie/123?api_key=secret', 'http://192.168.1.2:7890'))
        # Handler transports only approved headers through the real transport (covered separately).
        state = json.loads(self.request('/admin/state')[2])
        self.assertNotIn('secret', json.dumps(state['events']))

    def test_images_cached_but_api_is_not(self):
        path = self.relay('/t/p/w500/a.jpg')
        for expected in ('MISS', 'HIT'):
            headers = self.request(path)[1]
            self.assertEqual(headers['X-Relay-Cache'], expected)
            self.assertEqual(headers['Cache-Control'], 'private, max-age=86400')
        self.assertEqual(len(self.calls), 1)
        for _ in range(2):
            self.assertEqual(self.request(self.relay('/3/account/1'))[1]['Cache-Control'], 'no-store')
        self.assertEqual(len(self.calls), 3)

    def test_rotate_invalidates_old_addresses(self):
        self.login()
        old = self.relay('/3/configuration')
        self.assertEqual(self.request('/admin/rotate', {})[0], 200)
        self.assertEqual(self.request(old)[0], 403)
        self.assertEqual(self.request(self.relay('/3/configuration'))[0], 200)

    def test_cross_origin_and_invalid_config_leave_state_unchanged(self):
        self.login()
        before = self.state.snapshot()
        self.assertEqual(self.request('/admin/config', {}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/admin/config', {'proxy_url': 'file:///etc/passwd'})[0], 400)
        self.assertEqual(self.state.snapshot(), before)

    def test_diagnostics_use_unsaved_proxy_but_do_not_save_key(self):
        self.login()
        status, _, body = self.request('/admin/test', dict(proxy_url='http://proxy:7890', tmdb_key='ephemeral-key'))
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)['results']), 2)
        self.assertEqual(self.calls[0][2], 'http://proxy:7890')
        self.assertEqual(self.state.snapshot()['proxy_url'], '')
        self.assertNotIn('ephemeral-key', self.state.file.read_text())

    def test_keyless_network_route_requires_login_and_ignores_other_drafts(self):
        payload = dict(target='api', proxy_url='http://proxy:7890', tmdb_key='must-not-send', public_url='unfinished address')
        self.assertEqual(self.request('/admin/network-test', payload)[0], 401)
        self.login()
        with patch('app.upstream', return_value=(401, 'application/json', b'{}', {})) as transport:
            status, _, body = self.request('/admin/network-test', payload)
            result = json.loads(body)['result']
            self.assertEqual(status, 200)
            self.assertTrue(result['ok'])
            self.assertNotIn('must-not-send', str(transport.call_args))
            self.assertEqual(self.state.snapshot()['proxy_url'], '')
        self.assertEqual(self.request('/admin/network-test', dict(target='arbitrary', proxy_url=''))[0], 400)


if __name__ == '__main__':
    unittest.main()
