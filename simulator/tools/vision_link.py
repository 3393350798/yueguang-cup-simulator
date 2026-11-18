"""Optional loopback camera and raw UART transport."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import parse_qs, urlsplit


class VisionLink:
    def __init__(self, port):
        self.lock = Lock()
        self.frame = None
        self.frame_time = 0
        self.active = False
        self.inbox = bytearray()
        self.outbox = bytearray()
        self.cursor = 0
        link = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def reply(self, status, data=b'', mime='text/plain', **headers):
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                for key,value in headers.items():
                    self.send_header(key, str(value))
                self.end_headers()
                self.wfile.write(data)

            def local_host(self):
                return self.headers.get('Host', '').partition(':')[0].lower() in ('127.0.0.1','localhost')

            def do_GET(self):
                if not self.local_host():
                    return self.reply(403)
                url = urlsplit(self.path)
                if url.path == '/frame.png':
                    with link.lock:
                        data, timestamp = link.frame, link.frame_time
                    if data is None:
                        return self.reply(503, b'Camera is starting')
                    return self.reply(200, data, 'image/png', **{'X-Sim-Time':timestamp})
                if url.path == '/uart':
                    try:
                        after = int(parse_qs(url.query).get('after', ['0'])[0])
                    except ValueError:
                        return self.reply(400, b'after must be a byte cursor')
                    with link.lock:
                        end = link.cursor
                        start = end - len(link.outbox)
                        data = bytes(link.outbox[max(0, after-start):])
                    return self.reply(200, data, 'application/octet-stream',
                                      **{'X-Next-Cursor':end, 'X-Buffer-Start':start})
                return self.reply(404)

            def do_POST(self):
                if not self.local_host():
                    return self.reply(403)
                if self.path != '/uart':
                    return self.reply(404)
                if self.headers.get_content_type() != 'application/octet-stream' or self.headers.get('Origin'):
                    return self.reply(415, b'Use a local binary client')
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    size = 0
                if not 0 < size <= 4096:
                    return self.reply(400, b'Expected 1 to 4096 bytes')
                data = self.rfile.read(size)
                if len(data) != size:
                    return self.reply(400, b'Incomplete body')
                with link.lock:
                    if not link.active:
                        result = 409
                    elif len(link.inbox) + size > 4096:
                        result = 429
                    else:
                        link.inbox.extend(data)
                        result = 204
                return self.reply(result)

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def camera(self, frame, timestamp):
        with self.lock:
            self.frame, self.frame_time = frame, timestamp

    def receive(self, active):
        with self.lock:
            self.active = active
            data = bytes(self.inbox)
            self.inbox.clear()
        return data if active else b''

    def transmit(self, data):
        with self.lock:
            self.outbox.extend(data)
            self.cursor += len(data)
            if len(self.outbox) > 65536:
                del self.outbox[:-65536]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
