"""A small local HTTP server for the resume tests: one file, Range requests, a speed limit and scripted
connection drops - what a flaky network does to a real download."""
import http.server
import re
import threading
import time
from pathlib import Path


class RangeServer:
    def __init__(self, path, rate: int = 3_000_000):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        self.rate = rate                     # bytes per second
        self.requests: list[list] = []       # [range start, bytes sent, finished] per download request
        self.drop_first = 0                  # the first N connections are cut after `drop_at` of the file
        self.drop_at = 0.3
        self._count = 0
        self._lock = threading.Lock()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                server._serve(self)

            def do_HEAD(self):
                server._serve(self, head=True)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/{self.path.name}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()
            self._count = 0
            self.drop_first = 0

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def sent(self) -> int:
        return sum(r[1] for r in self.requests)

    def _serve(self, h, head: bool = False) -> None:
        if not h.path.endswith(self.path.name):
            h.send_error(404)
            return
        match = re.match(r"bytes=(\d+)-", h.headers.get("Range", ""))
        start = int(match.group(1)) if match else 0
        if start >= self.size:
            h.send_response(416)
            h.send_header("Content-Range", f"bytes */{self.size}")
            h.send_header("Content-Length", "0")
            h.end_headers()
            return
        with self._lock:
            self._count += 1
            nth = self._count
        h.send_response(206 if match else 200)
        h.send_header("Content-Type", "video/mp4")
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(self.size - start))
        if match:
            h.send_header("Content-Range", f"bytes {start}-{self.size - 1}/{self.size}")
        h.end_headers()
        if head:
            return
        record = [start, 0, False]
        with self._lock:
            self.requests.append(record)
        limit = int(self.size * self.drop_at) if nth <= self.drop_first else None
        chunk = 65536
        try:
            with open(self.path, "rb") as fh:
                fh.seek(start)
                while True:
                    if limit is not None and record[1] >= limit:
                        h.connection.shutdown(2)              # the network drops
                        break
                    data = fh.read(chunk)
                    if not data:
                        record[2] = True
                        break
                    h.wfile.write(data)
                    record[1] += len(data)
                    time.sleep(chunk / self.rate)
        except OSError:
            pass                                              # the client went away (pause, cancel, quit)
