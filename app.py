"""映桥 TMDB Relay. Python 3.12+, no third-party runtime dependencies."""
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
VERSION = '1.0.6'


def validate_config(raw):
    proxy = str(raw.get('proxy_url', '')).strip().rstrip('/')
    public = str(raw.get('public_url', '')).strip().rstrip('/')
    for value, schemes in [(proxy, ('http',)), (public, ('http', 'https'))]:
        if not value:
            continue
        u = urlsplit(value)
        if (u.scheme not in schemes or not u.hostname or u.username is not None
                or u.password is not None or u.query or u.fragment or u.path
                or len(value) > 2048 or any(c.isspace() for c in value)):
            raise ValueError('请填写完整地址；网络代理使用 http://IP:端口，公网地址只填域名根地址')
        if u.port is not None and not 1 <= u.port <= 65535:
            raise ValueError('端口范围为 1–65535')
    size = int(raw.get('cache_mb', 256))
    if not 32 <= size <= 4096:
        raise ValueError('图片缓存容量范围为 32–4096 MB')
    return dict(proxy_url=proxy, public_url=public, cache_mb=size)


def route(path):
    """Fixed upstreams only. Never accept a destination URL supplied by a client."""
    u = urlsplit(path)
    if u.scheme or u.netloc or u.fragment or len(path) > 8192:
        raise ValueError('无效路径')
    if re.fullmatch(r'/3/[A-Za-z0-9_/-]+', u.path) and '..' not in u.path:
        return 'api.themoviedb.org', path, False
    if re.fullmatch(r'/t/p/(?:w[0-9]{1,4}|h[0-9]{1,4}|original)/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|svg)', u.path) and not u.query:
        return 'image.tmdb.org', path, True
    raise ValueError('仅支持 TMDB API 与图片路径')


def upstream(host, path, proxy='', headers=None, *, timeout=18):
    context = ssl.create_default_context()
    if proxy:
        u = urlsplit(proxy)
        conn = http.client.HTTPSConnection(u.hostname, u.port or 80, timeout=timeout, context=context)
        conn.set_tunnel(host, 443)
    else:
        conn = http.client.HTTPSConnection(host, timeout=timeout, context=context)
    try:
        clean = {'Accept': '*/*', 'User-Agent': 'YingQiao-TMDB/1.0'}
        for key in ('Authorization', 'Accept-Language'):
            value = (headers or {}).get(key)
            if value and '\r' not in value and '\n' not in value and len(value) <= 8192:
                clean[key] = value
        conn.request('GET', path, headers=clean)
        response = conn.getresponse()
        # Redirects aren't followed: this service cannot become an arbitrary outbound proxy.
        limit = 20 * 1024 * 1024 if host == 'image.tmdb.org' else 8 * 1024 * 1024
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('上游响应超过大小限制')
        content_type = response.getheader('Content-Type', 'application/octet-stream')
        extra = {k: response.getheader(k) for k in ('Retry-After',) if response.getheader(k)}
        return response.status, content_type, data, extra
    finally:
        conn.close()


def network_probe(target, proxy, *, transport=None, connector=None):
    """One small keyless probe per request, so a slow target cannot hide other results."""
    names = {'proxy': 'NAS → Clash 代理', 'api': 'TMDB API 网络', 'image': 'TMDB 图片网络'}
    if target not in names:
        raise ValueError('无效的检测项')
    result = dict(target=target, name=names[target], ok=False, reachable=False, status=0, phase='connect')
    start = time.monotonic()
    try:
        if target == 'proxy':
            if not proxy:
                result.update(ok=True, skipped=True, message='未配置代理，当前使用直连')
            else:
                u = urlsplit(proxy)
                with (connector or socket.create_connection)((u.hostname, u.port or 80), timeout=4):
                    pass
                result.update(ok=True, reachable=True, message='NAS 能连接代理端口；TMDB 链路请看下方结果')
        else:
            host, path = ('api.themoviedb.org', '/3/configuration') if target == 'api' else (
                'image.tmdb.org', '/t/p/w92/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg')
            status, content_type, data, _ = (transport or upstream)(host, path, proxy, {}, timeout=6)
            result.update(status=status, bytes=len(data), reachable=True, phase='http')
            if target == 'api' and status in (200, 401):
                result.update(ok=True, message='API 网络正常（HTTP 401 是未鉴权响应，无需填写 Key）' if status == 401 else 'API 网络正常')
            elif target == 'image' and status == 200 and content_type.startswith('image/'):
                result.update(ok=True, message='图片网络正常，已实际下载测试海报')
            elif status == 429:
                result['message'] = '网络已连通，但上游限流（HTTP 429），请稍后重试'
            else:
                result['message'] = f'已收到 HTTP {status}，但测试内容不可用；请检查代理规则或上游限制'
    except socket.gaierror:
        result.update(phase='dns', error='dns', message='域名解析失败：使用代理时检查 NAS 能否解析代理地址；直连时检查 TMDB DNS')
    except (socket.timeout, TimeoutError):
        result.update(error='timeout', message='连接或读取超时：检查 Clash 允许局域网访问、代理节点与 TMDB 分流规则')
    except ConnectionRefusedError:
        result.update(error='refused', message='连接被拒绝：检查代理 IP、端口，以及 Clash 是否监听局域网地址')
    except ssl.SSLCertVerificationError:
        result.update(phase='tls', error='certificate', message='TLS 证书验证失败：检查 NAS 时间、信任证书及代理是否拦截 HTTPS')
    except ssl.SSLError:
        result.update(phase='tls', error='tls', message='TLS 握手失败：检查代理协议、节点与 HTTPS 连接')
    except OSError as problem:
        tunnel = re.search(r'Tunnel connection failed:\s*(\d{3})', str(problem))
        if tunnel:
            code = int(tunnel[1])
            result.update(phase='tunnel', error='proxy_tunnel', status=code,
                          message='代理要求认证（HTTP 407），当前只支持无认证的 HTTP/mixed 代理' if code == 407 else
                          f'代理拒绝或无法建立 TMDB 隧道（HTTP {code}），检查代理节点、规则与访问控制')
        else:
            result.update(error='network', message='网络连接失败：检查 NAS 到代理的路由、防火墙与代理节点状态')
    except http.client.HTTPException:
        result.update(error='protocol', message='代理响应异常：请确认填写的是 HTTP/mixed 端口，而不是 SOCKS 端口或管理页面')
    except Exception:
        result.update(error='probe', message='检测未完成，请重试或检查容器日志')
    result['ms'] = round((time.monotonic() - start) * 1000)
    return result


class ImageCache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def get(self, path):
        key = hashlib.sha256(path.encode()).hexdigest()
        with self.lock:
            try:
                meta = json.loads((self.directory / (key + '.json')).read_text())
                if time.time() - meta['created'] > 7 * 86400:
                    return None
                blob = self.directory / (key + '.bin')
                data = blob.read_bytes()
                blob.touch()
                return meta['type'], data
            except (OSError, ValueError, KeyError):
                return None

    def put(self, path, content_type, data, capacity):
        if len(data) > capacity:
            return
        key = hashlib.sha256(path.encode()).hexdigest()
        with self.lock:
            blob = self.directory / (key + '.bin')
            temp = self.directory / (key + '.tmp')
            temp.write_bytes(data)
            temp.replace(blob)
            (self.directory / (key + '.json')).write_text(json.dumps({'type': content_type, 'created': time.time()}))
            entries = sorted(self.directory.glob('*.bin'), key=lambda p: p.stat().st_mtime)
            size = sum(p.stat().st_size for p in entries)
            while entries and (size > capacity or len(entries) > 2000):
                old = entries.pop(0)
                size -= old.stat().st_size
                old.unlink(missing_ok=True)
                old.with_suffix('.json').unlink(missing_ok=True)

    def info(self):
        with self.lock:
            files = list(self.directory.glob('*.bin'))
            return {'files': len(files), 'bytes': sum(p.stat().st_size for p in files)}

    def clear(self):
        with self.lock:
            for p in self.directory.iterdir():
                if p.suffix in ('.bin', '.json', '.tmp'):
                    p.unlink(missing_ok=True)


class State:
    def __init__(self, directory, password, transport=upstream):
        if len(password) < 12:
            raise ValueError('ADMIN_PASSWORD 至少需要 12 位，请先配置管理员密码')
        self.password = password
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sessions = {}
        self.logins = {}
        self.transport = transport
        self.gate = threading.BoundedSemaphore(12)
        self.started = time.time()
        self.events = deque(maxlen=40)
        self.counts = dict(requests=0, image_requests=0, cache_hits=0, errors=0, transferred=0)
        self.file = self.directory / 'config.json'
        if self.file.exists():
            self.config = json.loads(self.file.read_text(encoding='utf-8'))
            validate_config(self.config)
            if not re.fullmatch(r'[a-f0-9]{48}', self.config.get('access_key', '')):
                raise ValueError('持久化访问密钥无效')
        else:
            self.config = dict(proxy_url='', public_url='', cache_mb=256, access_key=secrets.token_hex(24))
            self.save(self.config)
        self.cache = ImageCache(self.directory / 'images')

    def save(self, config):
        with self.lock:
            temp = self.file.with_suffix('.tmp')
            with temp.open('w', encoding='utf-8') as f:
                os.chmod(temp, 0o600)
                json.dump(config, f, ensure_ascii=False)
            temp.replace(self.file)
            self.config = config.copy()

    def snapshot(self):
        with self.lock:
            return self.config.copy()

    def record(self, kind, status, elapsed, size=0, cached=False):
        with self.lock:
            self.counts['requests'] += 1
            self.counts['image_requests'] += int(kind == '图片')
            self.counts['cache_hits'] += int(cached)
            self.counts['errors'] += int(status >= 400)
            self.counts['transferred'] += size
            self.events.appendleft(dict(kind=kind, status=status, ms=round(elapsed), cached=cached, at=time.time()))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.connections = threading.BoundedSemaphore(48)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'YingQiao/' + VERSION

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    @property
    def state(self):
        return self.server.state

    def log_message(self, *_):
        pass  # Never log URLs containing TMDB API keys or the relay access key.

    def send(self, status, body, content_type='application/json; charset=utf-8', headers=None,
             *, cache_control='no-store'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache_control)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        from http.cookies import SimpleCookie, CookieError
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie['session'].value
        except (KeyError, ValueError, CookieError):
            return False
        with self.state.lock:
            return self.state.sessions.get(token, 0) > time.time()

    def body(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('请求需要 JSON 格式')
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 16384:
            raise ValueError('请求内容为空或过长')
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise ValueError('请求需要 JSON 对象')
        return result

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send(500, {'error': '服务处理失败，请重试'})

    def get(self):
        path = urlsplit(self.path).path
        if path == '/health':
            return self.send(200, {'ok': True, 'version': VERSION})
        if path in ('/', '/app.js', '/style.css'):
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
            mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'application/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8'}[name]
            return self.send(200, (ROOT / 'web' / name).read_bytes(), mime)
        if path == '/admin/state':
            if not self.authenticated():
                return self.send(401, {'error': '请先登录'})
            with self.state.lock:
                payload = dict(version=VERSION, config=self.state.snapshot(), counts=self.state.counts.copy(), events=list(self.state.events), uptime=round(time.time() - self.state.started))
            payload['cache'] = self.state.cache.info()
            return self.send(200, payload)
        if not path.startswith('/r/'):
            return self.send(404, {'error': '路径不存在'})
        parts = self.path.split('/', 3)
        if len(parts) != 4 or not hmac.compare_digest(parts[2], self.state.snapshot()['access_key']):
            return self.send(403, {'error': '加速访问密钥无效'})
        try:
            host, target, image = route('/' + parts[3])
        except ValueError:
            return self.send(404, {'error': '不支持的加速路径'})
        if not self.state.gate.acquire(blocking=False):
            return self.send(503, {'error': '请求较多，请稍后重试'}, headers={'Retry-After': '2'})
        start = time.monotonic()
        kind = '图片' if image else '资料'
        try:
            config = self.state.snapshot()
            cached = self.state.cache.get(target) if image else None
            if cached:
                status, content_type, data, extra = 200, *cached, {}
            else:
                status, content_type, data, extra = self.state.transport(host, target, config['proxy_url'], self.headers)
                if image and status == 200 and content_type.startswith('image/'):
                    self.state.cache.put(target, content_type, data, config['cache_mb'] * 1024 * 1024)
            if status not in (200, 400, 401, 403, 404, 422, 429):
                status, content_type, data, extra = 502, 'application/json', b'{"error":"TMDB upstream unavailable"}', {}
            self.state.record(kind, status, (time.monotonic() - start) * 1000, len(data), cached is not None)
            extra['X-Relay-Cache'] = 'HIT' if cached else 'MISS'
            cache_control = 'private, max-age=86400' if image and status == 200 and content_type.startswith('image/') else 'no-store'
            return self.send(status, data, content_type, extra, cache_control=cache_control)
        except Exception:
            self.state.record(kind, 502, (time.monotonic() - start) * 1000)
            return self.send(502, {'error': 'TMDB 连接失败，请在管理页测试上游网络'})
        finally:
            self.state.gate.release()

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self.send(403, {'error': '跨站请求已拒绝'})
            data = self.body()
            if self.path == '/admin/login':
                return self.login(data)
            if not self.authenticated():
                return self.send(401, {'error': '请先登录'})
            if self.path == '/admin/logout':
                from http.cookies import SimpleCookie
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                with self.state.lock:
                    self.state.sessions.pop(cookie['session'].value, None)
                return self.send(200, {'ok': True}, headers={'Set-Cookie': 'session=; Path=/admin; HttpOnly; SameSite=Strict; Max-Age=0'})
            if self.path == '/admin/config':
                config = validate_config(data)
                config['access_key'] = self.state.snapshot()['access_key']
                self.state.save(config)
                return self.send(200, {'ok': True})
            if self.path == '/admin/rotate':
                config = self.state.snapshot()
                config['access_key'] = secrets.token_hex(24)
                self.state.save(config)
                return self.send(200, {'ok': True})
            if self.path == '/admin/cache/clear':
                self.state.cache.clear()
                return self.send(200, {'ok': True})
            if self.path == '/admin/test':
                return self.diagnostics(data)
            if self.path == '/admin/network-test':
                config = validate_config({'proxy_url': data.get('proxy_url', '')})
                target = data.get('target')
                if target not in ('proxy', 'api', 'image'):
                    raise ValueError('无效的检测项')
                if not self.state.gate.acquire(blocking=False):
                    return self.send(503, {'error': '请求较多，请稍后重试'})
                try:
                    return self.send(200, {'result': network_probe(target, config['proxy_url'])})
                finally:
                    self.state.gate.release()
            return self.send(404, {'error': '路径不存在'})
        except (ValueError, TypeError, KeyError):
            return self.send(400, {'error': '配置格式无效，请检查地址、端口和缓存容量'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            return self.send(500, {'error': '操作失败，请检查数据卷权限或稍后重试'})

    def login(self, data):
        now = time.time()
        ip = self.client_address[0]
        with self.state.lock:
            self.state.logins = {k: v for k, v in self.state.logins.items() if v[1] > now - 300}
            failures, _ = self.state.logins.get(ip, (0, now))
            if failures >= 8:
                return self.send(429, {'error': '登录尝试较多，请 5 分钟后重试'})
            if not hmac.compare_digest(str(data.get('password', '')).encode(), self.state.password.encode()):
                self.state.logins[ip] = (failures + 1, now)
                return self.send(401, {'error': '管理员密码不正确'})
            self.state.logins.pop(ip, None)
            self.state.sessions = {k: v for k, v in self.state.sessions.items() if v > now}
            token = secrets.token_urlsafe(32)
            self.state.sessions[token] = now + 8 * 3600
        secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
        return self.send(200, {'ok': True}, headers={'Set-Cookie': f'session={token}; Path=/admin; HttpOnly; SameSite=Strict; Max-Age=28800{secure}'})

    def diagnostics(self, data):
        config = validate_config(data)
        key = str(data.get('tmdb_key', '')).strip()
        if len(key) > 2048 or '\n' in key or '\r' in key:
            raise ValueError('密钥格式无效')
        if not self.state.gate.acquire(blocking=False):
            return self.send(503, {'error': '请求较多，请稍后重试'})
        try:
            results = []
            for label, host, path in [('TMDB API', 'api.themoviedb.org', '/3/configuration'), ('海报图片', 'image.tmdb.org', '/t/p/w92/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg')]:
                headers = {}
                if key and label == 'TMDB API':
                    if len(key) > 50:
                        headers['Authorization'] = 'Bearer ' + key
                    else:
                        from urllib.parse import urlencode
                        path += '?' + urlencode({'api_key': key})
                start = time.monotonic()
                try:
                    status, _, payload, _ = self.state.transport(host, path, config['proxy_url'], headers)
                    reachable = status == 200 or (label == 'TMDB API' and status == 401)
                    message = '连接正常' if status == 200 else '已连通；未填写密钥' if status == 401 and not key else '已连通；密钥无效' if status == 401 else '上游返回异常状态'
                    results.append(dict(name=label, reachable=reachable, ok=status == 200, status=status, ms=round((time.monotonic() - start) * 1000), message=message, bytes=len(payload)))
                except Exception:
                    results.append(dict(name=label, reachable=False, ok=False, status=0, ms=round((time.monotonic() - start) * 1000), message='连接失败；检查 Clash 允许局域网访问、地址与规则'))
            return self.send(200, {'results': results})
        finally:
            self.state.gate.release()


def create_server(host, port, state):
    server = Server((host, port), Handler)
    server.state = state
    return server


if __name__ == '__main__':
    password = os.environ.get('ADMIN_PASSWORD', '')
    password_file = os.environ.get('ADMIN_PASSWORD_FILE')
    if password_file:
        password = Path(password_file).read_text().strip()
    state = State(os.environ.get('DATA_DIR', '/data'), password)
    server = create_server(os.environ.get('HOST', '0.0.0.0'), int(os.environ.get('PORT', '8080')), state)
    print('映桥 TMDB Relay 已启动，管理页面端口：' + str(server.server_port), flush=True)
    server.serve_forever()
