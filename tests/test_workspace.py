import hashlib
import hmac
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
import zipfile

import httpx
import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from accounts import Accounts,digest
from business_features import BusinessFeatures,portal_data,portal_action
from connectors import Connectors,Vault,ConnectorRejected,ConnectorUnknown
from invoices import read_invoice_file
from ledger import Ledger,minor
from manage_workspace import backup
from scheduling import Scheduling,Worker
from webhooks import create_app
from workspace_store import WorkspaceError,WorkspaceStore


@pytest.fixture(params=["sqlite", "libsql"])
def ws(tmp_path, request, monkeypatch):
    if request.param == "libsql":
        # Exercise the actual libSQL driver/row adapter, using an isolated local
        # database so money, authorization and concurrency tests need no secrets.
        from workspace_storage import RemoteConnection
        monkeypatch.setattr(WorkspaceStore, "connect", lambda self: RemoteConnection(str(self.path), ""))
    store=WorkspaceStore(tmp_path/"workspace.sqlite3")
    instant=[datetime(2026,9,16,6,0,tzinfo=timezone.utc)]
    clock=lambda:instant[0]
    accounts=Accounts(store,clock)
    token,business=accounts.bootstrap("owner@example.com","a long test password","First Business")
    ledger=Ledger(store,token,business,clock)
    invoice={"invoice_no":"INV-1","client_name":"Customer","email":"customer@example.com","amount":"1000.00","amount_paid":"0.00","due_date":"2026-09-01","status":"unpaid","currency":"INR"}
    ledger.import_records([invoice])
    ledger.update_collection("INV-1",phone="+919876543210",consents=["email","sms","whatsapp"])
    scheduling=Scheduling(store,token,business,clock)
    vault=Vault(tmp_path,key=Fernet.generate_key())
    calls=[]
    def respond(request):
        data=json.loads(request.content)
        calls.append(data)
        result={"request_id":data["request_id"]}
        if data["operation"]=="message.send":
            result.update(provider_id="message-"+data["request_id"],status="submitted")
        elif data["operation"]=="payment_link.create":
            result.update(provider_id="link-"+data["request_id"],url="https://pay.example/payment",amount_minor=data["payload"]["amount_minor"],currency=data["payload"]["currency"])
        elif data["operation"]=="invoices.list":
            result.update(complete=True,invoices=[invoice])
        return httpx.Response(200,json=result)
    client=httpx.Client(transport=httpx.MockTransport(respond))
    gateway=Connectors(store,vault,client=client,allowed_hosts={"gateway.example"},clock=clock)
    for kind in ("email","sms","whatsapp","payments","accounting"):
        gateway.save(token,business,kind,"https://gateway.example/api","x"*32,"y"*32)
    worker=Worker(store,gateway,clock,live_enabled=True)
    return dict(store=store,instant=instant,clock=clock,accounts=accounts,token=token,business=business,ledger=ledger,
                scheduling=scheduling,gateway=gateway,worker=worker,calls=calls,invoice=invoice,
                features=BusinessFeatures(store,token,business,clock),client=client)


def webhook(ws,event,kind="payments"):
    raw=json.dumps(event).encode()
    stamp=str(int(ws["clock"]().timestamp()))
    sig=hmac.new(b"y"*32,stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    return ws["gateway"].webhook(ws["business"],kind,raw,stamp,sig)


def test_bootstrap_only_once_and_no_plain_password(ws):
    with pytest.raises(WorkspaceError,match="already"):
        ws["accounts"].bootstrap("second@example.com","another password test","Other")
    with ws["store"].transaction() as db:
        assert "password" not in db.execute("SELECT password FROM ws_users").fetchone()[0]
        assert db.execute("SELECT token FROM ws_sessions").fetchone()[0]!=ws["token"]


def test_login_rate_limit_and_logout(ws):
    for _ in range(5):
        with pytest.raises(WorkspaceError,match="incorrect"):
            ws["accounts"].login("owner@example.com","wrong long password")
    with pytest.raises(WorkspaceError,match="Too many"):
        ws["accounts"].login("owner@example.com","a long test password")
    ws["instant"][0]+=timedelta(minutes=16)
    token=ws["accounts"].login("owner@example.com","a long test password")
    assert ws["accounts"].businesses(token)
    ws["accounts"].logout(token)
    assert not ws["accounts"].businesses(token)


@pytest.mark.parametrize("role",["viewer","collector","manager"])
def test_roles_checked_by_services_not_only_ui(ws,role):
    code=ws["accounts"].invite(ws["token"],ws["business"],role+"@example.com",role)
    token,business=ws["accounts"].accept_invite(code,role+"@example.com","another long password")
    ledger=Ledger(ws["store"],token,business,ws["clock"])
    assert len(ledger.invoices())==1
    with pytest.raises(WorkspaceError):
        ws["gateway"].save(token,business,"sms","https://gateway.example/api","x"*32,"y"*32)
    if role=="viewer":
        with pytest.raises(WorkspaceError):
            ledger.receipt("INV-1","10","payment","test-receipt","2026-09-16")
    else:
        ledger.receipt("INV-1","10","payment","test-receipt","2026-09-16")
    if role!="manager":
        with pytest.raises(WorkspaceError):
            ledger.import_records([ws["invoice"]])
    with pytest.raises(WorkspaceError):
        ws["accounts"].accept_invite(code,role+"@example.com","another long password")


def test_tenants_cannot_read_or_mutate_each_other(ws):
    other=ws["accounts"].create_business(ws["token"],"Second Business")
    code=ws["accounts"].invite(ws["token"],other,"other@example.com","owner")
    token,_=ws["accounts"].accept_invite(code,"other@example.com","another long password")
    outsider=Ledger(ws["store"],token,ws["business"],ws["clock"])
    for action in [outsider.invoices,outsider.audit,lambda:outsider.receipt("INV-1","1","payment","ref-1","2026-09-16")]:
        with pytest.raises(WorkspaceError):
            action()
    assert Ledger(ws["store"],token,other,ws["clock"]).invoices()==[]


def test_last_owner_and_password_revokes_sessions(ws):
    uid=ws["accounts"].members(ws["token"],ws["business"])[0]["id"]
    with pytest.raises(WorkspaceError,match="at least one"):
        ws["accounts"].set_role(ws["token"],ws["business"],uid,"removed")
    second=ws["accounts"].login("owner@example.com","a long test password")
    new=ws["accounts"].change_password(ws["token"],"a long test password","new password long enough")
    assert not ws["accounts"].businesses(second)
    assert ws["accounts"].businesses(new)


def test_receipts_credits_idempotency_overpayment_and_reversal(ws):
    receipt=ws["ledger"].receipt("INV-1","250","payment","bank-1","2026-09-15")
    assert ws["ledger"].receipt("INV-1","250","payment","bank-1","2026-09-15")==receipt
    ws["ledger"].receipt("INV-1","50","credit","credit-1","2026-09-15")
    assert ws["ledger"].invoices()[0]["outstanding_amount"]=="700.00"
    with pytest.raises(WorkspaceError):
        ws["ledger"].receipt("INV-1","800","payment","bank-2","2026-09-15")
    with pytest.raises(WorkspaceError):
        ws["ledger"].receipt("INV-1","251","payment","bank-1","2026-09-15")
    ws["ledger"].reverse_receipt(receipt,"Bank transfer was returned")
    assert ws["ledger"].invoices()[0]["outstanding_amount"]=="950.00"
    with pytest.raises(WorkspaceError,match="already reversed"):
        ws["ledger"].reverse_receipt(receipt,"Bank transfer was returned")


def test_import_is_atomic_and_cannot_overwrite_local_payments(ws):
    ws["ledger"].receipt("INV-1","250","payment","bank-1","2026-09-15")
    with pytest.raises(WorkspaceError,match="differs"):
        ws["ledger"].import_records([{**ws["invoice"],"invoice_no":"NEW"},ws["invoice"]])
    assert len(ws["ledger"].invoices())==1
    ws["ledger"].import_records([{**ws["invoice"],"amount_paid":"250","status":"partially_paid"}])
    assert ws["ledger"].invoices()[0]["outstanding_amount"]=="750.00"


@pytest.mark.parametrize("change",["paid","opted_out","disputed","promise"])
def test_stop_sending_after_customer_or_invoice_changes(ws,change):
    draft=ws["scheduling"].draft("INV-1")
    job=ws["scheduling"].approve_manual(draft,draft["subject"],draft["body"],"live")
    if change=="paid":
        ws["ledger"].receipt("INV-1","1000","payment","bank-full","2026-09-16")
    else:
        ws["ledger"].update_collection("INV-1",consents=["email"],opted_out=change=="opted_out",status="disputed" if change=="disputed" else "active",promise_date="2026-09-20" if change=="promise" else "")
    assert ws["worker"].run_one(job)["state"]=="blocked"
    assert ws["calls"]==[]


@pytest.mark.parametrize("channel",["email","sms","whatsapp"])
def test_preview_and_live_connectors_have_distinct_results(ws,channel):
    draft=ws["scheduling"].draft("INV-1",channel)
    preview=ws["scheduling"].approve_manual(draft,"Reviewed subject","Reviewed body")
    assert ws["worker"].run_one(preview)["state"]=="previewed"
    assert ws["calls"]==[]
    live=ws["scheduling"].approve_manual(draft,"Reviewed subject","Reviewed body","live")
    assert ws["worker"].run_one(live)["state"]=="submitted"
    assert ws["calls"][0]["payload"]["body"]=="Reviewed body"
    assert ws["worker"].run_one(live) is None
    second=ws["scheduling"].approve_manual(draft,"Reviewed subject","Reviewed body","live")
    assert ws["worker"].run_one(second)["state"]=="blocked"
    assert len(ws["calls"])==1


def test_workers_atomically_claim_once(ws):
    draft=ws["scheduling"].draft("INV-1")
    job=ws["scheduling"].approve_manual(draft,draft["subject"],draft["body"],"live")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:ws["worker"].run_one(job),range(2)))
    assert sum(r is not None for r in results)==1
    assert len(ws["calls"])==1


def test_schedule_stage_catchup_restart_quiet_hours_and_pause(ws):
    rule=ws["scheduling"].save_rule("Follow-up",channel="email",days=[-3,0,7,14,30],auto_send=True,active=True)
    assert ws["worker"].plan()==1
    assert Worker(ws["store"],ws["gateway"],ws["clock"]).plan()==0
    job=ws["scheduling"].jobs()[0]
    assert json.loads(job["payload"])["stage"]==14
    ws["instant"][0]=ws["instant"][0].replace(hour=14)
    assert ws["worker"].run_one(job["id"])["state"]=="deferred"
    ws["scheduling"].pause_rule(rule)
    assert ws["scheduling"].jobs()[0]["state"]=="cancelled"


def test_review_schedule_and_approval_expiration(ws):
    ws["scheduling"].save_rule("Review",channel="email",days=[7],active=True)
    ws["worker"].plan()
    job=ws["scheduling"].jobs()[0]
    assert job["state"]=="awaiting_approval"
    assert ws["worker"].run_one(job["id"]) is None
    ws["scheduling"].approve_job(job["id"],"Reviewed","Reviewed message")
    ws["instant"][0]+=timedelta(minutes=31)
    assert ws["worker"].run_one(job["id"])["state"]=="blocked"


def test_live_server_gate_and_unknown_outcomes(ws):
    draft=ws["scheduling"].draft("INV-1")
    job=ws["scheduling"].approve_manual(draft,draft["subject"],draft["body"],"live")
    assert Worker(ws["store"],ws["gateway"],ws["clock"],live_enabled=False).run_one(job)["state"]=="blocked"
    def fail(request):
        raise httpx.ReadTimeout("lost response")
    ws["gateway"].client=httpx.Client(transport=httpx.MockTransport(fail))
    job=ws["scheduling"].approve_manual(draft,draft["subject"],draft["body"],"live")
    assert ws["worker"].run_one(job)["state"]=="unknown"
    next_job=ws["scheduling"].approve_manual(draft,draft["subject"],draft["body"],"live")
    assert ws["worker"].run_one(next_job)["state"]=="blocked"
    ws["scheduling"].reconcile(job,"failed","Provider confirmed no submission")
    assert next(j for j in ws["scheduling"].jobs() if j["id"]==job)["state"]=="failed"


def test_demotion_prevents_previously_authorized_work(ws):
    code=ws["accounts"].invite(ws["token"],ws["business"],"collector@example.com","collector")
    token,_=ws["accounts"].accept_invite(code,"collector@example.com","collector password long")
    service=Scheduling(ws["store"],token,ws["business"],ws["clock"])
    draft=service.draft("INV-1")
    job=service.approve_manual(draft,draft["subject"],draft["body"],"live")
    user=next(r for r in ws["accounts"].members(ws["token"],ws["business"]) if r["email"]=="collector@example.com")
    ws["accounts"].set_role(ws["token"],ws["business"],user["id"],"viewer")
    assert ws["worker"].run_one(job)["state"]=="blocked"
    assert ws["calls"]==[]


def test_secret_encryption_and_gateway_host_allowlist(ws):
    with ws["store"].transaction() as db:
        stored=db.execute("SELECT secret FROM ws_connectors LIMIT 1").fetchone()[0]
        assert "x"*32 not in stored and "y"*32 not in stored
    with pytest.raises(WorkspaceError,match="allowed|ALLOW|host"):
        ws["gateway"].save(ws["token"],ws["business"],"sms","https://evil.example/api","x"*32,"y"*32)
    assert "secret" not in ws["gateway"].list(ws["token"],ws["business"])[0]


def test_payment_link_verified_receipt_and_replay_protection(ws):
    link=ws["gateway"].create_link(ws["token"],ws["business"],"INV-1")
    assert ws["gateway"].create_link(ws["token"],ws["business"],"INV-1")["id"]==link["id"]
    event={"event_id":"e1","type":"payment.received","link_id":link["provider_id"],"payment_id":"payment-1","amount_minor":100000,"currency":"INR"}
    assert webhook(ws,event)=="processed"
    assert webhook(ws,event)=="duplicate"
    assert webhook(ws,{**event,"event_id":"e2"})=="processed"
    assert len(ws["ledger"].receipts())==1
    assert ws["ledger"].invoices()[0]["status"]=="paid"


@pytest.mark.parametrize("field,value",[("currency","USD"),("amount_minor",100001),("amount_minor",True),("link_id","unmatched")])
def test_payment_mismatches_cannot_change_ledger(ws,field,value):
    link=ws["gateway"].create_link(ws["token"],ws["business"],"INV-1")
    event={"event_id":"e1","type":"payment.received","link_id":link["provider_id"],"payment_id":"payment-1","amount_minor":100000,"currency":"INR",field:value}
    with pytest.raises(WorkspaceError):
        webhook(ws,event)
    assert ws["ledger"].invoices()[0]["outstanding_amount"]=="1000.00"


def test_invalid_webhook_signature_and_old_timestamp(ws):
    raw=b'{"event_id":"e1"}'
    with pytest.raises(WorkspaceError):
        ws["gateway"].webhook(ws["business"],"payments",raw,str(int(ws["clock"]().timestamp())),"bad")
    stamp=str(int(ws["clock"]().timestamp())-301)
    sig=hmac.new(b"y"*32,stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    with pytest.raises(WorkspaceError):
        ws["gateway"].webhook(ws["business"],"payments",raw,stamp,sig)


def test_delivery_events_and_optout(ws):
    draft=ws["scheduling"].draft("INV-1")
    job=ws["scheduling"].approve_manual(draft,draft["subject"],draft["body"],"live")
    ws["worker"].run_one(job)
    webhook(ws,{"event_id":"delivered","type":"message.status","message_id":"message-"+job,"status":"delivered"},"email")
    webhook(ws,{"event_id":"read","type":"message.status","message_id":"message-"+job,"status":"read"},"email")
    webhook(ws,{"event_id":"old","type":"message.status","message_id":"message-"+job,"status":"bounced"},"email")
    assert ws["scheduling"].jobs()[0]["state"]=="read"
    webhook(ws,{"event_id":"stop","type":"contact.opted_out","recipient":"customer@example.com"},"email")
    with pytest.raises(WorkspaceError,match="permitted"):
        ws["scheduling"].draft("INV-1")


def test_installments_allocate_receipts_and_update_reminder_dates(ws):
    ws["features"].save_installments("INV-1",[{"due_date":"2026-09-01","amount":"300"},{"due_date":"2026-10-01","amount":"700"}])
    draft=ws["scheduling"].draft("INV-1")
    assert "300.00" in draft["body"]
    ws["ledger"].receipt("INV-1","300","payment","first-part","2026-09-16")
    draft=ws["scheduling"].draft("INV-1")
    assert draft["invoice"]["collection_due_date"]=="2026-10-01"
    ws["scheduling"].save_rule("Installments",channel="email",days=[0,7],active=True)
    assert ws["worker"].plan()==0


def test_branding_localization_and_template_injection(ws):
    ws["features"].save_branding("Our Business","reply@example.com","Net 30","firm","INR","business@bank")
    draft=ws["scheduling"].draft("INV-1")
    assert "Our Business" in draft["body"] and "upi://pay?" in draft["body"]
    assert "Net 30" in draft["body"]
    with pytest.raises(WorkspaceError):
        ws["scheduling"].save_template("Bad","en","Reminder","{client_name.__class__}")
    ws["ledger"].update_collection("INV-1",consents=["email"],language="hi")
    assert "नमस्ते" in ws["scheduling"].draft("INV-1")["body"]


def test_portal_scoping_expiration_revocation_and_actions(ws):
    ws["ledger"].import_records([{**ws["invoice"],"invoice_no":"PRIVATE","email":"other@example.com"}])
    token=ws["features"].create_portal("customer@example.com")
    company,rows=portal_data(ws["store"],token,ws["clock"]())
    assert len(rows)==1 and rows[0]["invoice_no"]=="INV-1"
    assert "followup_note" not in rows[0]
    portal_action(ws["store"],token,"promise","2026-09-20",ws["clock"]())
    with pytest.raises(WorkspaceError,match="promised"):
        ws["scheduling"].draft("INV-1")
    portal_action(ws["store"],token,"opt_out",now=ws["clock"]())
    assert ws["ledger"].invoices()[0]["opted_out"]
    ws["features"].revoke_portals("customer@example.com")
    with pytest.raises(WorkspaceError):
        portal_data(ws["store"],token,ws["clock"]())


def test_webhook_http_limits_and_portal_html_escaping(ws,monkeypatch):
    import config
    monkeypatch.setattr(config,"IS_DEMO",False)
    client=TestClient(create_app(ws["gateway"]))
    assert client.get("/health").status_code==200
    assert client.post("/webhooks/"+ws["business"]+"/payments",content=b"x"*(512*1024+1)).status_code==413
    assert client.post("/webhooks/"+ws["business"]+"/payments",json={}).status_code==400
    token=ws["features"].create_portal("customer@example.com",30)
    # Portal uses the real clock; give this fixture an unambiguous future expiry.
    with ws["store"].transaction() as db:
        db.execute("UPDATE ws_portals SET expires='2099-01-01T00:00:00+00:00'")
    response=client.get("/portal/"+token)
    assert response.status_code==200 and "Customer statement" in response.text
    assert response.headers["cache-control"]=="no-store"
    assert client.post("/portal/"+token,data={"action":"opt_out","csrf":"bad"}).status_code==400


def test_forecast_reports_and_backup(ws,tmp_path):
    ws["ledger"].import_records([{**ws["invoice"],"invoice_no":"USD-1","currency":"USD","amount":"10"}])
    report=ws["ledger"].reports()
    assert report["INR"]["outstanding"]=="1000.00" and report["USD"]["outstanding"]=="10.00"
    assert all("no receipt history" in r["basis"] for r in ws["features"].forecast())
    ws["ledger"].receipt("INV-1","100","payment","paid-bank","2026-09-10")
    assert any("9 days" in r["basis"] for r in ws["features"].forecast())
    data=ws["features"].export_business()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "ws_invoices.json" in archive.namelist()
        assert not any("connector" in p or "session" in p or "user" in p for p in archive.namelist())
    destination=backup(ws["store"].path,tmp_path/"backup.sqlite3")
    assert WorkspaceStore(destination).path.is_file()
    with pytest.raises(FileExistsError):
        backup(ws["store"].path,destination)


def test_flexible_column_mapping():
    file=io.BytesIO(b'Bill,Customer,Mail,Total,Due,State\n1,Customer,c@example.com,500,2026-09-01,unpaid\n')
    headers=read_invoice_file(file,"custom.csv",headers_only=True)
    assert "bill" in headers
    rows=read_invoice_file(file,"custom.csv",column_mapping=dict(bill="invoice_no",customer="client_name",mail="email",total="amount",due="due_date",state="status"))
    assert rows[0]["invoice_no"]=="1"


def test_contact_optout_survives_new_invoice_import(ws):
    ws["ledger"].update_collection("INV-1",consents=["email"],opted_out=True)
    ws["ledger"].import_records([{**ws["invoice"],"invoice_no":"INV-2"}])
    assert all(row["opted_out"] for row in ws["ledger"].invoices())
    with pytest.raises(WorkspaceError):
        ws["scheduling"].draft("INV-2")


def test_customer_change_does_not_transfer_contact_permission(ws):
    ws["ledger"].update_collection("INV-1", phone="+919876543210",
        consents=["email", "sms"], promise_date="2026-09-20")
    ws["ledger"].import_records([{**ws["invoice"], "email": "new@example.com"}])
    invoice = ws["ledger"].invoices()[0]
    assert invoice["phone"] == ""
    assert invoice["consents"] == []
    assert invoice["promise_date"] == ""
    with pytest.raises(WorkspaceError, match="permitted"):
        ws["scheduling"].draft("INV-1")


def test_customer_change_honors_existing_target_optout(ws):
    ws["ledger"].import_records([{**ws["invoice"], "invoice_no": "INV-2", "email": "new@example.com"}])
    ws["ledger"].update_collection("INV-2", phone="+919000000001", consents=["email"], opted_out=True)
    ws["ledger"].import_records([{**ws["invoice"], "email": "new@example.com"}])
    changed = next(row for row in ws["ledger"].invoices() if row["invoice_no"] == "INV-1")
    assert changed["opted_out"]
    assert changed["phone"] == "+919000000001"


@pytest.mark.parametrize("zone,instant,local_date", [
    ("Asia/Kolkata", "2026-09-16T19:00:00+00:00", "2026-09-17"),
    ("America/Los_Angeles", "2026-09-16T02:00:00+00:00", "2026-09-15"),
])
def test_payment_and_portal_dates_follow_business_timezone(ws, zone, instant, local_date):
    from datetime import date

    with ws["store"].transaction() as db:
        db.execute("UPDATE ws_businesses SET timezone=? WHERE id=?", (zone, ws["business"]))
    ws["instant"][0] = datetime.fromisoformat(instant)
    ws["token"] = ws["accounts"].login("owner@example.com", "a long test password")
    ws["ledger"].token = ws["features"].token = ws["token"]
    today = date.fromisoformat(local_date)
    assert ws["features"].forecast()[0]["expected_date"] == local_date
    receipt = ws["ledger"].receipt("INV-1", "100", "payment", "local-payment", local_date)
    with pytest.raises(WorkspaceError, match="future"):
        ws["ledger"].receipt("INV-1", "100", "payment", "future-payment", (today + timedelta(days=1)).isoformat())
    ws["ledger"].reverse_receipt(receipt, "Customer bank transfer was reversed")
    reversal = next(r for r in ws["ledger"].receipts() if r["kind"] == "reversal")
    assert reversal["received_on"] == local_date
    token = ws["features"].create_portal("customer@example.com")
    portal_action(ws["store"], token, "promise", local_date, ws["clock"]())
    with pytest.raises(WorkspaceError, match="90 days"):
        portal_action(ws["store"], token, "promise", (today - timedelta(days=1)).isoformat(), ws["clock"]())
    ws["gateway"].create_link(ws["token"], ws["business"], "INV-1")
    link = ws["gateway"].links(ws["token"], ws["business"])[0]
    webhook(ws, {"event_id": "local-event", "type": "payment.received", "link_id": link["provider_id"],
        "payment_id": "local-gateway-payment", "amount_minor": 1000, "currency": "INR"})
    received = next(r for r in ws["ledger"].receipts() if r["reference"] == "gateway:local-gateway-payment")
    assert received["received_on"] == local_date


def test_portal_rejects_non_ascii_csrf_without_server_error(ws, monkeypatch):
    import config
    monkeypatch.setattr(config, "IS_DEMO", False)
    client = TestClient(create_app(ws["gateway"]))
    response = client.post("/portal/invalid-token", data={"action": "opt_out", "csrf": "invalid-\u00e9"})
    assert response.status_code == 400


def test_failed_backup_does_not_leave_an_apparently_usable_snapshot(tmp_path):
    import sqlite3
    source = tmp_path / "corrupt.sqlite3"
    source.write_bytes(b"this is not a sqlite database")
    destination = tmp_path / "backup.sqlite3"
    with pytest.raises(sqlite3.DatabaseError):
        backup(source, destination)
    assert not destination.exists()


def test_worker_finishes_current_send_before_honoring_shutdown(ws):
    from threading import Event
    stopped = Event()
    ws["ledger"].import_records([{**ws["invoice"], "invoice_no": "INV-2"}])
    jobs = []
    for key in ("INV-1", "INV-2"):
        draft = ws["scheduling"].draft(key)
        jobs.append(ws["scheduling"].approve_manual(draft, draft["subject"], draft["body"], "live"))
    def respond(request):
        stopped.set()  # Simulate a termination request while the provider responds.
        request_id = json.loads(request.content)["request_id"]
        return httpx.Response(200, json={"request_id": request_id, "provider_id": "accepted-message", "status": "submitted"})
    ws["gateway"].client = httpx.Client(transport=httpx.MockTransport(respond))
    progress = []
    results = ws["worker"].tick(stop_requested=stopped.is_set, on_progress=progress.append)
    assert len(results) == 1 and results[0]["state"] == "submitted"
    assert progress and all(count == 1 for count in progress)
    with ws["store"].transaction() as db:
        states = [r[0] for r in db.execute("SELECT state FROM ws_jobs")]
    assert sorted(states) == ["queued", "submitted"]


def test_concurrent_receipts_cannot_overpay(ws):
    def record(reference):
        try:
            ws["ledger"].receipt("INV-1","600","payment",reference,"2026-09-16")
            return True
        except WorkspaceError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(record,["bank-A","bank-B"]))
    assert results.count(True)==1
    assert ws["ledger"].invoices()[0]["outstanding_amount"]=="400.00"


def test_gateway_request_authentication_and_no_redirects(ws):
    def check(request):
        assert request.headers["authorization"]=="Bearer "+"x"*32
        expected=hmac.new(b"x"*32,request.headers["x-reminder-timestamp"].encode()+b"."+request.content,hashlib.sha256).hexdigest()
        assert request.headers["x-reminder-signature"]==expected
        return httpx.Response(302,headers={"location":"https://other.example/"})
    ws["gateway"].client=httpx.Client(transport=httpx.MockTransport(check),follow_redirects=False)
    with pytest.raises(ConnectorUnknown):
        ws["gateway"].accounting_preview(ws["token"],ws["business"])


def test_link_created_after_concurrent_payment_needs_reconciliation(ws):
    def respond(request):
        data=json.loads(request.content)
        ws["ledger"].receipt("INV-1","100","payment","parallel-bank","2026-09-16")
        return httpx.Response(200,json={"request_id":data["request_id"],"provider_id":"stale-link","url":"https://pay.example/link?token=abc","amount_minor":100000,"currency":"INR"})
    ws["gateway"].client=httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(ConnectorUnknown,match="invoice changed"):
        ws["gateway"].create_link(ws["token"],ws["business"],"INV-1")
    assert ws["gateway"].links(ws["token"],ws["business"])[0]["state"]=="unknown"
    token=ws["features"].create_portal("customer@example.com")
    assert portal_data(ws["store"],token,ws["clock"]())[1][0]["payment_url"]==""


def test_session_expiry_and_invitation_expiry(ws):
    invite=ws["accounts"].invite(ws["token"],ws["business"],"later@example.com","viewer")
    ws["instant"][0]+=timedelta(hours=73)
    with pytest.raises(WorkspaceError):
        ws["ledger"].invoices()
    with pytest.raises(WorkspaceError,match="expired"):
        ws["accounts"].accept_invite(invite,"later@example.com","a different password")


def test_accounting_import_requires_complete_review_batch(ws):
    assert ws["gateway"].accounting_preview(ws["token"],ws["business"])[0]["invoice_no"]=="INV-1"
    ws["gateway"].client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={"request_id":json.loads(r.content)["request_id"],"complete":False,"invoices":[]})))
    with pytest.raises(WorkspaceError,match="complete"):
        ws["gateway"].accounting_preview(ws["token"],ws["business"])


def test_installment_total_and_currency_cannot_be_changed_by_import(ws):
    ws["features"].save_installments("INV-1",[{"due_date":"2026-09-01","amount":"1000"}])
    with pytest.raises(WorkspaceError,match="installment"):
        ws["ledger"].import_records([{**ws["invoice"],"amount":"1200"}])
    with pytest.raises(WorkspaceError,match="currency"):
        ws["ledger"].import_records([{**ws["invoice"],"currency":"USD"}])
