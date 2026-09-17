import io
from pathlib import Path
from unittest.mock import patch
import pytest
from streamlit.testing.v1 import AppTest

import config
from accounts import Accounts
from ledger import Ledger
from setup_sample import sample_bytes
from workspace_store import WorkspaceStore

APP=Path(__file__).resolve().parents[1]/"app.py"


def button(app,label):
    return next(b for b in app.button if b.label==label)


@pytest.fixture
def workspace_app(tmp_path,monkeypatch):
    monkeypatch.setenv("WORKSPACE_DATABASE_PATH",str(tmp_path/"workspace.sqlite3"))
    monkeypatch.setenv("WORKSPACE_DEMO_LOGIN_ENABLED","false")
    monkeypatch.setattr(config,"IS_DEMO",False)
    return AppTest.from_file(APP,default_timeout=30)


def sign_in(app,tmp_path):
    store=WorkspaceStore(tmp_path/"workspace.sqlite3")
    accounts=Accounts(store)
    token,business=accounts.bootstrap("owner@example.com","a long test password","Test Business")
    from invoices import read_invoice_file
    ledger=Ledger(store,token,business)
    ledger.import_records(read_invoice_file(io.BytesIO(sample_bytes())))
    inv=ledger.invoices()[0]
    ledger.update_collection(inv["invoice_no"],consents=["email"])
    app.session_state.ws_token=token
    app.session_state.ws_business=business
    app.run()
    return store,token,business


def test_first_owner_setup_is_usable(workspace_app):
    app=workspace_app.run()
    assert not app.exception
    assert any(t.value=="Payment follow-up" for t in app.title)
    app.text_input[0].set_value("My Business")
    app.text_input[1].set_value("owner@example.com")
    app.text_input[2].set_value("a long test password")
    button(app,"Create business").click().run()
    assert not app.exception
    assert any(t.value=="Overview" for t in app.title)
    button(app,"Sign out").click().run()
    assert not app.exception
    assert any(b.label=="Continue" for b in app.button)


@pytest.mark.parametrize("page",["dashboard","invoice_book","payments","reminder_queue","policies","reports","settings","team"])
def test_authenticated_pages_render_without_errors(workspace_app,tmp_path,page):
    sign_in(workspace_app,tmp_path)
    workspace_app.switch_page("app_pages/"+page+".py").run()
    assert not workspace_app.exception
    assert not workspace_app.error


def test_manual_preview_requires_review_and_records_edited_message(workspace_app,tmp_path):
    store,token,business=sign_in(workspace_app,tmp_path)
    app=workspace_app.switch_page("app_pages/reminder_queue.py").run()
    # The first invoice has explicit email permission from setup.
    button(app,"Prepare draft").click().run()
    assert not app.exception
    button(app,"Approve and process").click().run()
    assert any("Approve the reviewed" in w.value for w in app.warning)
    next(t for t in app.text_input if t.label=="Subject").set_value("Reviewed subject")
    app.text_area[0].set_value("Reviewed exact message")
    app.checkbox[0].check()
    button(app,"Approve and process").click().run()
    assert not app.exception
    with store.transaction() as db:
        row=db.execute("SELECT state,payload FROM ws_jobs").fetchone()
        assert row["state"]=="previewed"
        assert "Reviewed exact message" in row["payload"]


def test_viewer_cannot_reach_owner_page(workspace_app,tmp_path):
    store,token,business=sign_in(workspace_app,tmp_path)
    accounts=Accounts(store)
    invite=accounts.invite(token,business,"viewer@example.com","viewer")
    viewer,_=accounts.accept_invite(invite,"viewer@example.com","viewer password long")
    workspace_app.session_state.ws_token=viewer
    workspace_app.run()
    assert not workspace_app.exception
    # Backend checks are covered independently; navigation must also hide owner controls.
    assert "Team & integrations" not in str(workspace_app.get("navigation"))


def test_demo_entry_stays_isolated_preview(workspace_app,monkeypatch):
    monkeypatch.setattr(config,"IS_DEMO",True)
    workspace_app.run()
    assert not workspace_app.exception
    assert workspace_app.session_state.step=="upload"


def test_inaccessible_database_has_clear_startup_error(workspace_app,tmp_path,monkeypatch):
    parent=tmp_path/"not-a-directory"
    parent.write_text("file")
    monkeypatch.setenv("WORKSPACE_DATABASE_PATH",str(parent/"workspace.sqlite3"))
    workspace_app.run()
    assert not workspace_app.exception
    assert any("could not be opened" in message.value for message in workspace_app.error)


def test_demo_buttons_prefill_and_sign_in(workspace_app,tmp_path,monkeypatch):
    from demo_workspace import seed_demo_workspace
    monkeypatch.setenv("WORKSPACE_DEMO_LOGIN_ENABLED","true")
    monkeypatch.setenv("WORKSPACE_DEMO_PASSWORD","demo test password long")
    monkeypatch.setenv("WORKSPACE_LIVE_ENABLED","false")
    store=WorkspaceStore(tmp_path/"workspace.sqlite3")
    accounts=Accounts(store)
    token,business=accounts.bootstrap("demo@example.com","demo test password long","Demo")
    seed_demo_workspace(store,token,business)
    accounts.logout(token)
    app=workspace_app.run()
    app.segmented_control[0].set_value("Use invitation").run()
    button(app,"Fill demo login").click().run()
    assert not app.exception
    assert app.segmented_control[0].value=="Sign in"
    assert app.text_input[0].value=="demo@example.com"
    assert app.text_input[1].value=="demo test password long"
    button(app,"Continue").click().run()
    assert not app.exception
    assert any(t.value=="Overview" for t in app.title)
    button(app,"Sign out").click().run()
    button(app,"Open demo workspace").click().run()
    assert not app.exception
    assert any(t.value=="Overview" for t in app.title)
    assert len(Ledger(store,app.session_state.ws_token,business).invoices())==10


def test_demo_buttons_hidden_without_opt_in(workspace_app,tmp_path):
    accounts=Accounts(WorkspaceStore(tmp_path/"workspace.sqlite3"))
    accounts.bootstrap("owner@example.com","test owner password long","Real business")
    app=workspace_app.run()
    assert not app.exception
    assert "Open demo workspace" not in [b.label for b in app.button]
    assert "Fill demo login" not in [b.label for b in app.button]


def test_overview_currency_switch_does_not_mix_balances(workspace_app,tmp_path):
    store,token,business=sign_in(workspace_app,tmp_path)
    Ledger(store,token,business).import_records([dict(invoice_no="USD-001",client_name="Export customer",
        email="export@example.com",currency="USD",amount="123.45",due_date="2026-09-01",status="unpaid")])
    app=workspace_app.run()
    next(w for w in app.segmented_control if w.label=="Currency").set_value("USD").run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=="Total outstanding").value=="$123.45"
    assert set(app.dataframe[0].value["Currency"])=={"USD"}


def test_invoice_filters_work_together_after_dashboard_navigation(workspace_app,tmp_path):
    from datetime import date,timedelta
    store,token,business=sign_in(workspace_app,tmp_path)
    Ledger(store,token,business).import_records([
        dict(invoice_no="USD-OPEN",client_name="Export customer",email="export@example.com",currency="USD",
             amount="123.45",due_date=(date.today()-timedelta(days=10)).isoformat(),status="unpaid"),
        dict(invoice_no="USD-PAID",client_name="Export customer",email="export@example.com",currency="USD",
             amount="80",due_date=(date.today()-timedelta(days=10)).isoformat(),status="paid")])
    app=workspace_app.run()
    button(app,"View all invoices").click().run()
    assert not app.exception
    assert any(t.value=="Invoices & customers" for t in app.title)
    # AppTest doesn't persist st.switch_page's destination for later widget runs.
    app.switch_page("app_pages/invoice_book.py").run()
    next(w for w in app.selectbox if w.label=="Currency").set_value("USD").run()
    next(w for w in app.selectbox if w.label=="Show invoices").set_value("Overdue").run()
    assert not app.exception
    assert list(app.dataframe[0].value["Invoice"])==["USD-OPEN"]
    next(w for w in app.text_input if w.label=="Search invoices or customers").set_value("no-match").run()
    assert any("No invoices match" in i.value for i in app.info)
