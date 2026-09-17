"""Exercise the deployment entry point in a fresh Streamlit runtime."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


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
