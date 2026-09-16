import io
from pathlib import Path
from unittest.mock import patch
import pytest
from streamlit.testing.v1 import AppTest

import config
from setup_sample import sample_bytes

APP = Path(__file__).resolve().parents[1] / "legacy_app.py"


def button(app, label):
    return next(b for b in app.button if b.label == label)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "ui.sqlite3")
    monkeypatch.setattr(config, "IS_DEMO", False)
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GMAIL_USER", "")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")
    return AppTest.from_file(APP, default_timeout=20)


def prepare_batch(app):
    with patch("streamlit.file_uploader", return_value=io.BytesIO(sample_bytes())):
        app.run()
        button(app, "Find overdue invoices").click().run()
    assert not app.exception
    assert app.session_state.step == "review"
    return app


def test_browser_starts_without_credentials(app):
    app.run()
    assert not app.exception
    assert app.session_state.step == "upload"


def test_bad_import_has_error_and_no_success_message(app):
    with patch("streamlit.file_uploader", return_value=io.BytesIO(b"corrupt workbook")):
        app.run()
        button(app, "Find overdue invoices").click().run()
    assert not app.exception
    assert any("Import stopped" in message.value for message in app.error)
    assert not any("No eligible" in message.value for message in app.info)
    assert app.session_state.step == "upload"


def test_browser_requires_explicit_approval(app):
    prepare_batch(app)
    assert len(app.checkbox) == 4
    assert all(not checkbox.value for checkbox in app.checkbox)
    button(app, "Approve selected and preview").click().run()
    assert not app.exception
    assert app.session_state.step == "review"
    assert any("Select at least one" in message.value for message in app.warning)


def test_edit_preview_history_and_reset_keep_settings(app):
    prepare_batch(app)
    app.session_state.business_name = "Preserved Business"
    app.session_state.gmail_user = "preserved@example.com"
    app.text_input[0].set_value("Edited and reviewed subject")
    app.text_area[0].set_value("Edited and reviewed body")
    app.checkbox[0].check()
    button(app, "Approve selected and preview").click().run()
    assert not app.exception
    assert app.session_state.step == "done"
    assert app.session_state.results[0]["Status"] == "previewed"
    with app.session_state.store.connect() as db:
        row = db.execute("SELECT * FROM reminders").fetchone()
        assert row["subject"] == "Edited and reviewed subject"
        assert row["body"] == "Edited and reviewed body"
        assert row["mode"] == "dry_run"
    button(app, "Start another batch").click().run()
    assert not app.exception
    assert app.session_state.gmail_user == "preserved@example.com"
    assert app.session_state.business_name == "Preserved Business"
    assert app.session_state.step == "upload"


def test_settings_save_for_session_works_without_gmail(app):
    app.run()
    button(app, "Business and email settings").click().run()
    assert not app.exception
    app.text_input[0].set_value("New Business")
    app.checkbox[0].uncheck()
    button(app, "Save settings").click().run()
    assert not app.exception
    assert app.session_state.business_name == "New Business"
    assert any("browser session" in message.value for message in app.success)


def test_demo_forces_preview_and_isolates_sessions(app, monkeypatch):
    monkeypatch.setattr(config, "IS_DEMO", True)
    prepare_batch(app)
    app.session_state.dry_run = False
    app.checkbox[0].check()
    button(app, "Approve selected and preview").click().run()
    assert not app.exception
    assert app.session_state.results[0]["Status"] == "previewed"
    second = AppTest.from_file(APP, default_timeout=20).run()
    assert not second.exception
    assert second.session_state.business_id != app.session_state.business_id
    assert second.session_state.store.path != app.session_state.store.path
    with second.session_state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
