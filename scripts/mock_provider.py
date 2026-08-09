#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.headers.get("Authorization") != "Bearer e2e-test-key":
            self.send_response(401); self.end_headers(); return
        serialized = json.dumps(body, ensure_ascii=False)
        text = "OK" if "只回复 OK" in serialized else "**梯度下降**是一种优化方法。公式：$x_{t+1}=x_t-\\eta\\nabla f(x_t)$。\n\n```python\nloss.backward()\n```\n\n<img src=x onerror=alert('unsafe')>"
        if text != "OK":
            time.sleep(0.35)
        if self.path.endswith("/chat/completions"):
            payload = {"choices": [{"message": {"role": "assistant", "content": text}}]}
        elif self.path.endswith("/responses"):
            payload = {"output": [{"content": [{"type": "output_text", "text": text}]}]}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.environ.get("CONCEPT_BRANCH_MOCK_PROVIDER_PORT", "9432"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
