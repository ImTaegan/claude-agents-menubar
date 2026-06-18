"""Concurrent POSTs must not race the registry dict — every POST returns 204."""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DAEMON = Path(__file__).resolve().parents[1] / "claude_watch_daemon.py"
THREADS = 8
EVENTS_PER_THREAD = 25


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _post_one(port, session_id, thread_idx, results, lock):
    body = json.dumps({
        "session_id": session_id, "event": "running",
        "cwd": f"/projects/proj{thread_idx}",
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/event", data=body, method="POST")
    try:
        status = urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = -1
    with lock:
        results.append(status)


def test_concurrent_posts_all_return_204():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(DAEMON), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(1.2)
        results, lock, threads = [], threading.Lock(), []
        for t_idx in range(THREADS):
            def run_thread(thread_idx=t_idx):
                for e_idx in range(EVENTS_PER_THREAD):
                    _post_one(port, f"t{thread_idx}-e{e_idx}", thread_idx, results, lock)
            threads.append(threading.Thread(target=run_thread))
        for thr in threads:
            thr.start()
        for thr in threads:
            thr.join(timeout=30)
    finally:
        proc.terminate()
        proc.communicate(timeout=5)

    assert len(results) == THREADS * EVENTS_PER_THREAD
    non_204 = [r for r in results if r != 204]
    assert not non_204, f"{len(non_204)} POSTs did not return 204: {set(non_204)!r}"
