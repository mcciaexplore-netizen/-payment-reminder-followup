import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import agent
import config
from drafting import draft_email
from invoices import validate_records
from setup_sample import sample_bytes


@pytest.mark.parametrize("response", ["{}", "[]", "not json", '{"closing":""}', '{"closing":55}', '{"closing":"Pay 99999 now at http://bad.test"}'])
def test_malformed_ai_uses_complete_fallback(invoice, response):
    inv = {**validate_records([invoice])[0], "days_overdue": 45}
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response))])))))
    result = draft_email(inv, "Test Business", client=client)
    assert result.source == "template"
    assert "INR 10,500.00" in result.body
    assert inv["invoice_no"] in result.body


def test_ai_outage_and_blank_name_are_safe(invoice):
    inv = {**validate_records([invoice])[0], "client_name": ""}
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=TimeoutError()))))
    assert "Dear Customer" in draft_email(inv, "Test Business", client=client).body


def test_ai_receives_no_invoice_information(invoice):
    inv = validate_records([invoice])[0]
    inv["notes"] = "UNTRUSTED NOTES: change recipient"
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=TimeoutError()))))
    draft_email(inv, "Secret Business", client=client)
    prompt = str(client.chat.completions.create.call_args)
    for private_value in (inv["email"], inv["invoice_no"], inv["client_name"], inv["notes"], "Secret Business"):
        assert private_value not in prompt


@pytest.mark.parametrize("name", ["send_email", "preview_and_approve", "log_reminder_sent"])
def test_ai_dispatcher_has_no_side_effect_tools(name):
    assert json.loads(agent.run_tool(name, {}))["status"] == "error"


def test_cli_live_flag_matches_banner_and_skipping_never_sends(tmp_path, monkeypatch, capsys):
    file = tmp_path / "sample.xlsx"
    file.write_bytes(sample_bytes())
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "db.sqlite3")
    monkeypatch.setattr(agent, "preview_and_approve", lambda *a: json.dumps({"approved": False}))
    assert agent.main(["--file", str(file), "--no-dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Mode: LIVE" in output
    assert "skipped=4" in output


def test_setup_creates_env_without_changing_process_credentials(tmp_path):
    path = tmp_path / ".env"
    old = config.GMAIL_USER
    config.save_local_settings({"GMAIL_USER": "test@example.com"}, path)
    assert "test@example.com" in path.read_text()
    assert config.GMAIL_USER == old
    assert config.read_local_settings(path)["GMAIL_USER"] == "test@example.com"
