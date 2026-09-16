"""Provider-neutral HTTPS gateway contract; secrets never appear in UI exports.

Adapters implement docs/connectors.md. No vendor is silently selected and no
simulation is represented as a successful live submission.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken

from accounts import authorize
from ledger import get_invoice, minor, record_receipt
from workspace_store import WorkspaceError, encode, utcnow

KINDS = {"email","sms","whatsapp","payments","accounting"}


class ConnectorRejected(WorkspaceError):
    """A provider explicitly rejected the operation before accepting it."""


class ConnectorUnknown(WorkspaceError):
    """An operation might have reached the provider. Do not automatically retry."""


def secure_url(url, allowed_hosts=None, *, allow_query=False):
    try:
        p=urlsplit(url)
        if p.scheme!="https" or not p.hostname or p.username or p.password or (p.query and not allow_query) or p.fragment or p.port not in {None,443}:
            raise ValueError()
    except ValueError:
        raise WorkspaceError("Use an HTTPS URL without credentials, query parameters, fragments or a custom port.") from None
    if allowed_hosts is not None and p.hostname.casefold() not in allowed_hosts:
        raise WorkspaceError("This gateway host must be added to CONNECTOR_ALLOWED_HOSTS by the server operator.")
    return url if allow_query else url.rstrip("/")


class Vault:
    def __init__(self, directory, key=None):
        key = key or os.getenv("WORKSPACE_MASTER_KEY")
        if not key:
            path=Path(directory)/"connector.key"
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            try:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            except FileExistsError:
                # Another process may have reserved the key path but not flushed it yet.
                for _ in range(20):
                    key=path.read_bytes()
                    if key:
                        break
                    time.sleep(0.05)
                if not key:
                    raise WorkspaceError("The connector key file is empty. Restore it before starting services.")
            else:
                key=Fernet.generate_key()
                with os.fdopen(fd,"wb") as handle:
                    handle.write(key)
        self.fernet=Fernet(key)

    def seal(self, value):
        return self.fernet.encrypt(encode(value).encode()).decode()

    def open(self, value):
        try:
            return json.loads(self.fernet.decrypt(value.encode()))
        except (InvalidToken,ValueError):
            raise WorkspaceError("Saved credentials cannot be decrypted. Restore the matching encryption key.") from None


class Connectors:
    def __init__(self, store, vault, *, client=None, allowed_hosts=None, clock=utcnow):
        self.store,self.vault,self.client,self.clock=store,vault,client,clock
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else {h.strip().casefold() for h in os.getenv("CONNECTOR_ALLOWED_HOSTS","").split(",") if h.strip()}

    def save(self, token, business, kind, endpoint, api_token, webhook_secret, enabled=True):
        if kind not in KINDS or type(enabled) is not bool:
            raise WorkspaceError("Invalid connector type or state.")
        endpoint=secure_url(endpoint,self.allowed_hosts)
        with self.store.transaction() as db:
            actor=authorize(db,token,business,"admin",self.clock())
            old=db.execute("SELECT secret,endpoint FROM ws_connectors WHERE business_id=? AND kind=?",(business,kind)).fetchone()
            previous=self.vault.open(old["secret"]) if old else {}
            if old and endpoint!=old["endpoint"] and (not api_token or not webhook_secret):
                raise WorkspaceError("Enter new credentials when changing the gateway address.")
            values={"api_token":api_token or previous.get("api_token",""),"webhook_secret":webhook_secret or previous.get("webhook_secret","")}
            if any(not isinstance(v,str) or not 16<=len(v)<=4096 or any(c in v for c in "\r\n") for v in values.values()):
                raise WorkspaceError("API and webhook secrets must contain 16–4096 characters without line breaks.")
            db.execute("INSERT OR REPLACE INTO ws_connectors VALUES(?,?,?,?,?)",(business,kind,endpoint,self.vault.seal(values),int(enabled)))
            self.store.audit(db,business,actor,"connector.saved",{"kind":kind,"endpoint":endpoint,"enabled":enabled},self.clock())

    def list(self, token, business):
        with self.store.transaction() as db:
            authorize(db,token,business,"admin",self.clock())
            return [dict(r) for r in db.execute("SELECT kind,endpoint,enabled FROM ws_connectors WHERE business_id=?",(business,))]

    def _config(self, business, kind):
        with self.store.transaction() as db:
            row=db.execute("SELECT * FROM ws_connectors WHERE business_id=? AND kind=? AND enabled=1",(business,kind)).fetchone()
            if not row:
                raise ConnectorRejected("Configure and enable this connector before live use.")
            result=dict(row)
            result.update(self.vault.open(row["secret"]))
            secure_url(result["endpoint"],self.allowed_hosts)
            return result

    def request(self, business, kind, operation, payload, request_id):
        cfg=self._config(business,kind)
        body=encode({"version":1,"operation":operation,"business_id":business,"request_id":request_id,"payload":payload}).encode()
        timestamp=str(int(self.clock().timestamp()))
        signature=hmac.new(cfg["api_token"].encode(),timestamp.encode()+b"."+body,hashlib.sha256).hexdigest()
        headers={"Authorization":"Bearer "+cfg["api_token"],"Content-Type":"application/json","Idempotency-Key":request_id,
                 "X-Reminder-Timestamp":timestamp,"X-Reminder-Signature":signature}
        client=self.client or httpx.Client(timeout=20,follow_redirects=False,trust_env=False)
        try:
            # Stream to enforce a response limit even if the gateway sends unbounded data.
            with client.stream("POST",cfg["endpoint"],content=body,headers=headers) as response:
                if 400<=response.status_code<500 and response.status_code not in {408,409,429}:
                    raise ConnectorRejected("The gateway rejected this request. Check its configuration and logs.")
                if response.status_code!=200:
                    raise ConnectorUnknown("Gateway result is uncertain; reconcile this operation before retrying.")
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>5*1024*1024:
                        raise ConnectorUnknown("Gateway response exceeded the size limit.")
            result=json.loads(data)
            if not isinstance(result,dict) or result.get("request_id")!=request_id:
                raise ConnectorUnknown("Gateway returned an invalid or unmatched response.")
            return result
        except (ConnectorRejected,ConnectorUnknown):
            raise
        except Exception:
            raise ConnectorUnknown("Gateway connection or response failed; verify the outcome before retrying.") from None
        finally:
            if self.client is None:
                client.close()

    def accounting_preview(self, token, business):
        with self.store.transaction() as db:
            authorize(db,token,business,"manage",self.clock())
        result=self.request(business,"accounting","invoices.list",{},secrets.token_hex(16))
        if result.get("complete") is not True or not isinstance(result.get("invoices"),list):
            raise WorkspaceError("Gateway must return a complete invoice batch for review.")
        from invoices import validate_records
        return validate_records(result["invoices"])

    def create_link(self, token, business, invoice_key):
        with self.store.transaction() as db:
            actor=authorize(db,token,business,"collect",self.clock())
            inv=get_invoice(db,business,invoice_key)
            if minor(inv["outstanding_amount"])<=0 or inv["status"] in {"cancelled","disputed","on_hold"}:
                raise WorkspaceError("This invoice is paid or paused.")
            old=db.execute("SELECT * FROM ws_links WHERE business_id=? AND invoice_key=? AND state IN ('creating','unknown','active') ORDER BY created DESC LIMIT 1",(business,invoice_key.casefold())).fetchone()
            if old:
                if old["state"]=="active" and old["amount"]==minor(inv["outstanding_amount"]):
                    return dict(old)
                raise WorkspaceError("An existing payment link needs reconciliation or retirement before creating another.")
            # Configuration errors must not create a permanent uncertain operation.
            request_id=secrets.token_hex(16)
            db.execute("INSERT INTO ws_links VALUES(?,?,?,?,?,'creating',NULL,NULL,?)",(request_id,business,invoice_key.casefold(),minor(inv["outstanding_amount"]),inv["currency"],self.clock().isoformat()))
            self.store.audit(db,business,actor,"payment_link.requested",{"invoice":invoice_key,"request_id":request_id},self.clock())
        try:
            result=self.request(business,"payments","payment_link.create",{"invoice_no":inv["invoice_no"],"amount_minor":minor(inv["outstanding_amount"]),"currency":inv["currency"],"customer_email":inv["email"]},request_id)
            return self._save_link(business,request_id,result)
        except Exception as exc:
            with self.store.transaction() as db:
                db.execute("UPDATE ws_links SET state=? WHERE id=? AND business_id=?",("failed" if isinstance(exc,ConnectorRejected) else "unknown",request_id,business))
            raise

    def _save_link(self,business,link_id,result):
        url=secure_url(result.get("url",""),allow_query=True)
        provider=str(result.get("provider_id", ""))
        if not 1<=len(provider)<=200:
            raise ConnectorUnknown("Payment gateway did not return a valid link identifier.")
        with self.store.transaction() as db:
            row=db.execute("SELECT * FROM ws_links WHERE id=? AND business_id=?",(link_id,business)).fetchone()
            if result.get("amount_minor")!=row["amount"] or result.get("currency")!=row["currency"]:
                raise ConnectorUnknown("Payment link amount or currency does not match the request.")
            current=get_invoice(db,business,row["invoice_key"])
            stale=minor(current["outstanding_amount"])!=row["amount"] or current["status"] in {"paid","cancelled","disputed","on_hold"}
            db.execute("UPDATE ws_links SET state=?,provider_id=?,url=? WHERE id=? AND business_id=?",("unknown" if stale else "active",provider,url,link_id,business))
            saved=dict(db.execute("SELECT * FROM ws_links WHERE id=? AND business_id=?",(link_id,business)).fetchone())
        if stale:
            raise ConnectorUnknown("The invoice changed while the link was being prepared. Cancel the link at the provider and reconcile it here.")
        return saved

    def reconcile_link(self,token,business,link_id):
        with self.store.transaction() as db:
            authorize(db,token,business,"manage",self.clock())
            row=db.execute("SELECT * FROM ws_links WHERE id=? AND business_id=?",(link_id,business)).fetchone()
            if not row:
                raise WorkspaceError("Payment link not found.")
        result=self.request(business,"payments","payment_link.lookup",{"original_request_id":link_id},secrets.token_hex(16))
        if result.get("state") in {"cancelled","not_created"}:
            with self.store.transaction() as db:
                actor=authorize(db,token,business,"manage",self.clock())
                db.execute("UPDATE ws_links SET state='retired' WHERE id=? AND business_id=?",(link_id,business))
                self.store.audit(db,business,actor,"payment_link.retired",{"link":link_id},self.clock())
            return {"state":"retired"}
        return self._save_link(business,link_id,result)

    def links(self,token,business):
        with self.store.transaction() as db:
            authorize(db,token,business,"read",self.clock())
            return [dict(r) for r in db.execute("SELECT * FROM ws_links WHERE business_id=? ORDER BY created DESC",(business,))]

    def webhook(self,business,kind,raw,timestamp,signature):
        if kind not in KINDS or len(raw)>512*1024:
            raise WorkspaceError("Invalid webhook.")
        cfg=self._config(business,kind)
        try:
            if abs(self.clock().timestamp()-int(timestamp))>300:
                raise ValueError()
            expected=hmac.new(cfg["webhook_secret"].encode(),str(timestamp).encode()+b"."+raw,hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,str(signature)):
                raise ValueError()
        except (ValueError,TypeError):
            raise WorkspaceError("Invalid webhook signature or timestamp.") from None
        event=json.loads(raw)
        if not isinstance(event,dict) or not isinstance(event.get("event_id"),str) or not 1<=len(event["event_id"])<=200:
            raise WorkspaceError("A unique event identifier is required.")
        now=self.clock()
        with self.store.transaction() as db:
            event_id=kind+":"+event["event_id"]
            if db.execute("SELECT 1 FROM ws_events WHERE business_id=? AND event_id=?",(business,event_id)).fetchone():
                return "duplicate"
            if event.get("type")=="payment.received" and kind=="payments":
                link=db.execute("SELECT * FROM ws_links WHERE business_id=? AND provider_id=?",(business,event.get("link_id"))).fetchone()
                if not link or type(event.get("amount_minor")) is not int or not 0<event["amount_minor"]<=link["amount"] or event.get("currency")!=link["currency"]:
                    raise WorkspaceError("Payment does not match a known link, amount or currency.")
                payment_id=event.get("payment_id")
                if not isinstance(payment_id,str) or not 1<=len(payment_id)<=150:
                    raise WorkspaceError("A payment identifier is required.")
                from ledger import amount
                record_receipt(db,self.store,business,link["invoice_key"],amount(event["amount_minor"]),"payment","gateway:"+payment_id,now.date(),"payment-webhook",now)
                if minor(get_invoice(db,business,link["invoice_key"])["outstanding_amount"])==0:
                    db.execute("UPDATE ws_links SET state='paid' WHERE id=?",(link["id"],))
            elif event.get("type")=="message.status" and kind in {"email","sms","whatsapp"}:
                status=event.get("status")
                if status not in {"delivered","bounced","read"}:
                    raise WorkspaceError("Unknown delivery status.")
                job=db.execute("SELECT * FROM ws_jobs WHERE business_id=? AND channel=? AND provider_id=? AND mode='live'",(business,kind,event.get("message_id"))).fetchone()
                if not job:
                    raise WorkspaceError("Message has not been recorded yet; retry this event later.")
                order={"submitted":0,"unknown":0,"claimed":0,"bounced":1,"delivered":2,"read":3}
                if order.get(status,0)>order.get(job["state"],0):
                    db.execute("UPDATE ws_jobs SET state=?,completed_at=? WHERE id=?",(status,now.isoformat(),job["id"]))
            elif event.get("type")=="contact.opted_out" and kind in {"email","sms","whatsapp"}:
                recipient=event.get("recipient")
                if not isinstance(recipient,str) or not recipient:
                    raise WorkspaceError("An opted-out recipient is required.")
                for row in db.execute("SELECT * FROM ws_invoices WHERE business_id=?",(business,)).fetchall():
                    inv,profile=json.loads(row["data"]),json.loads(row["profile"])
                    target=inv["email"] if kind=="email" else profile["phone"]
                    if target.casefold()==recipient.casefold():
                        profile["consents"]=[c for c in profile["consents"] if c!=kind]
                        db.execute("UPDATE ws_invoices SET profile=?,revision=revision+1 WHERE business_id=? AND invoice_key=?",(encode(profile),business,row["invoice_key"]))
            else:
                raise WorkspaceError("Unsupported event type for this connector.")
            db.execute("INSERT INTO ws_events VALUES(?,?,?,?)",(business,event_id,now.isoformat(),"processed"))
            self.store.audit(db,business,"webhook","webhook.processed",{"id":event_id,"type":event["type"]},now)
        return "processed"
