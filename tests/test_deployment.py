"""Exercise the deployment entry point in a fresh Streamlit runtime."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def test_asgi_entrypoint_serves_http_and_websocket_from_another_directory(tmp_path):
    # Streamlit has process-global runtime state, so isolate this from AppTest.
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYTHONIOENCODING": "utf-8",
        "WORKSPACE_DATABASE_PATH": str(tmp_path / "workspace.sqlite3"),
        "WORKSPACE_LIVE_ENABLED": "false",
        "WORKSPACE_DEMO_LOGIN_ENABLED": "false",
        "APP_MODE": "local",
    }
    environment.pop("SPACE_ID", None)
    check = textwrap.dedent("""\
        import os
        from pathlib import Path
        from starlette.testclient import TestClient
        from streamlit.proto.BackMsg_pb2 import BackMsg
        from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
        from asgi_app import app as application

        # Loading the server must not execute the UI or create an account database.
        assert not Path(os.environ["WORKSPACE_DATABASE_PATH"]).exists()
        with TestClient(application, base_url="http://localhost") as client:
            assert client.get("/_stcore/health").status_code == 200
            assert client.get("/health").json() == {"status": "ok"}
            page = client.get("/")
            assert page.status_code == 200
            assert "text/html" in page.headers["content-type"]
            with client.websocket_connect("/_stcore/stream", subprotocols=["streamlit"]) as socket:
                assert socket.accepted_subprotocol == "streamlit"
                run = BackMsg()
                run.rerun_script.SetInParent()
                socket.send_bytes(run.SerializeToString())
                setup_button = False
                for _ in range(200):
                    message = ForwardMsg.FromString(socket.receive_bytes())
                    if message.HasField("delta"):
                        element = message.delta.new_element
                        assert not element.HasField("exception"), str(element.exception)
                        setup_button |= element.button.label == "Create business"
                    if message.HasField("script_finished"):
                        assert message.script_finished == ForwardMsg.FINISHED_SUCCESSFULLY
                        break
                else:
                    raise AssertionError("The workspace did not finish rendering")
                assert setup_button, "The full workspace must render first-owner setup"
            assert client.get("/portal/not-a-valid-token").status_code == 400
            event = client.post("/webhooks/unknown/payments", json={})
            assert event.status_code == 400
            assert "Verify the signature" in event.json()["error"]
        """)
    completed = subprocess.run(
        [sys.executable, "-c", check], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_real_server_does_not_log_private_customer_urls(tmp_path):
    # A real Uvicorn process catches logging changes made when Streamlit imports;
    # an in-process ASGI client does not exercise those access log handlers.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, "APP_MODE": "local", "SPACE_ID": "",
        "WORKSPACE_DATABASE_PATH": str(tmp_path / "workspace.sqlite3"),
        "WORKSPACE_LIVE_ENABLED": "false", "WORKSPACE_DEMO_LOGIN_ENABLED": "false",
        "PYTHONIOENCODING": "utf-8"}
    log_path = tmp_path / "server.log"
    private_token = "private-link-must-never-appear-in-access-logs"
    with log_path.open("w", encoding="utf-8") as logs:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "asgi_app:app",
            "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            cwd=ROOT, env=environment, stdout=logs, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                assert process.poll() is None, log_path.read_text(encoding="utf-8")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except (URLError, OSError):
                    time.sleep(0.1)
            else:
                raise AssertionError("Server did not start: " + log_path.read_text(encoding="utf-8"))
            try:
                with urlopen(f"http://127.0.0.1:{port}/portal/{private_token}", timeout=10):
                    raise AssertionError("Invalid customer link was accepted")
            except HTTPError as response:
                assert response.code == 400
                response.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    assert private_token not in log_path.read_text(encoding="utf-8")
