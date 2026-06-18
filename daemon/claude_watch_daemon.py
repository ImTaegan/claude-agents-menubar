#!/usr/bin/env python3
"""Local daemon for the Claude Agents menu bar app.

Runs a localhost HTTP server that collects Claude Code hook events (POST /event)
and status-line usage reports (POST /usage), aggregates them, and serves the
current state at GET /status — which the menu bar app polls. Pure stdlib.
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aggregator import SessionRegistry
from events import handle_event_body, handle_usage_body
from usage_scan import scan_today_output_tokens


class AppState:
    def __init__(self):
        self.registry = SessionRegistry()
        self.lock = threading.Lock()
        self.today_tokens = None  # output tokens used today (background scan)


def make_handler(state, idle_timeout):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/status":
                self.send_response(404)
                self.end_headers()
                return
            now = time.time()
            try:
                with state.lock:
                    state.registry.gc(now, idle_timeout)
                    payload = state.registry.status(now)
                payload["today_output_tokens"] = state.today_tokens
                body = json.dumps(payload).encode()
            except Exception:
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass  # client hung up after headers

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            now = time.time()
            try:
                with state.lock:
                    state.registry.gc(now, idle_timeout)
                    if self.path == "/usage":
                        handle_usage_body(state.registry, raw, now)
                    else:
                        handle_event_body(state.registry, raw, now)
            except Exception:
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(204)
            self.end_headers()

    return Handler


def start_http(state, port, idle_timeout):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, idle_timeout))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[daemon] listening on 127.0.0.1:{port}", file=sys.stderr)
    return server


def start_usage_scan(state, interval=300):
    projects = os.path.expanduser("~/.claude/projects")

    def loop():
        while True:
            try:
                state.today_tokens = scan_today_output_tokens(projects, time.time())
            except Exception:
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()


def main():
    p = argparse.ArgumentParser(description="Claude Agents local daemon")
    p.add_argument("--port", type=int, default=7459)
    p.add_argument("--idle-timeout", type=float, default=900.0)
    args = p.parse_args()

    state = AppState()
    start_http(state, args.port, args.idle_timeout)
    start_usage_scan(state)
    threading.Event().wait()  # serve until killed


if __name__ == "__main__":
    main()
