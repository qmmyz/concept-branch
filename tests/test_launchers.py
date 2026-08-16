import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def test_all_server_launchers_disable_request_access_logs():
    for relative_path in ("scripts/start_server.sh", "scripts/dev.sh", "scripts/run_e2e.sh"):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "--no-access-log" in source, f"{relative_path} must not log query strings"

    module_source = (ROOT / "backend/concept_branch/__main__.py").read_text(encoding="utf-8")
    assert "access_log=False" in module_source


def test_module_server_does_not_log_search_query(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    secret_query = "TOPSECRET_SEARCH_QUERY"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "backend"),
        "CONCEPT_BRANCH_PORT": str(port),
        "CONCEPT_BRANCH_DB": str(tmp_path / "data" / "concept-branch.sqlite3"),
        "CONCEPT_BRANCH_CONFIG_DIR": str(tmp_path / "config"),
        "CONCEPT_BRANCH_SERVE_FRONTEND": "0",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "concept_branch"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                urlopen(f"http://127.0.0.1:{port}/api/search?q={secret_query}", timeout=1)
                break
            except HTTPError as error:
                assert error.code == 401
                break
            except URLError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError("module server did not become ready")
                time.sleep(0.05)
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=10)

    assert secret_query not in output
