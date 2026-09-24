"""Business-scoped invoice ledger, collection controls, receipts and reports."""
import csv
import io
import json
import re
import secrets
from datetime import date
from decimal import Decimal

from accounts import authorize
from invoices import money, validate_records
from workspace_store import WorkspaceError, business_today, encode, utcnow


def minor(value):
    return int(money(value)*100)


def amount(value):
    return str((Decimal(value)/100).quantize(Decimal("0.01")))


def csv_bytes(rows):
    if not rows:
        return b""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "'"+str(v) if str(v).lstrip().startswith(("=","+","-","@")) else v for k,v in row.items()})
    return output.getvalue().encode("utf-8-sig")


def get_invoice(db, business, key):
    row = db.execute("SELECT * FROM ws_invoices WHERE business_id=? AND invoice_key=?", (business,str(key).casefold())).fetchone()
    if not row:
        raise WorkspaceError("Invoice not found in this business.")
    inv, profile = json.loads(row["data"]), json.loads(row["profile"])
    adjustments = db.execute("SELECT COALESCE(SUM(amount),0) FROM ws_receipts WHERE business_id=? AND invoice_key=?", (business,row["invoice_key"])).fetchone()[0]
    settled = minor(inv["amount_paid"]) + adjustments
    outstanding = minor(inv["amount"]) - settled
    inv.update(amount_paid=amount(settled), outstanding_amount=amount(outstanding), revision=row["revision"], **profile)
    if outstanding == 0 and inv["status"] != "cancelled":
        inv["status"] = "paid"
    elif inv["status"] not in {"cancelled","disputed","on_hold"}:
        inv["status"] = "partially_paid" if settled else "unpaid"
    return inv


def record_receipt(db, store, business, key, value, kind, reference, received_on, actor, now):
    """Atomic helper used by manual receipts and verified payment events."""
    if kind not in {"payment","credit"} or not isinstance(reference,str) or not 3 <= len(reference.strip()) <= 200:
        raise WorkspaceError("Use a payment or credit and a unique reference of 3–200 characters.")
    reference = reference.strip()
    value = minor(value)
    if value <= 0:
        raise WorkspaceError("Receipt or credit must be greater than zero.")
    when = date.fromisoformat(str(received_on))
    if when > business_today(db, business, now):
        raise WorkspaceError("A received payment cannot be dated in the future.")
    current = get_invoice(db,business,key)
    existing = db.execute("SELECT * FROM ws_receipts WHERE business_id=? AND reference=?", (business,reference)).fetchone()
    if existing:
        if existing["invoice_key"] != key.casefold() or existing["amount"] != value or existing["kind"] != kind:
            raise WorkspaceError("This reference is already used by a different receipt.")
        return existing["id"]
    if current["status"] == "cancelled" or value > minor(current["outstanding_amount"]):
        raise WorkspaceError("The amount exceeds the invoice balance or the invoice is cancelled.")
    receipt = secrets.token_hex(16)
    db.execute("INSERT INTO ws_receipts VALUES(?,?,?,?,?,?,?,?,?)",
               (receipt,business,key.casefold(),value,kind,reference,when.isoformat(),now.isoformat(),actor))
    db.execute("UPDATE ws_invoices SET revision=revision+1 WHERE business_id=? AND invoice_key=?", (business,key.casefold()))
    store.audit(db,business,actor,"receipt.recorded",{"invoice":key,"amount":amount(value),"kind":kind,"reference":reference},now)
    return receipt


class Ledger:
    def __init__(self, store, token, business, clock=utcnow):
        self.store,self.token,self.business,self.clock = store,token,business,clock

    def auth(self, db, permission="read"):
        return authorize(db,self.token,self.business,permission,self.clock())

    def today(self):
        with self.store.transaction() as db:
            self.auth(db)
            return business_today(db, self.business, self.clock())

    def import_records(self, records):
        valid = validate_records(records)
        with self.store.transaction() as db:
            actor = self.auth(db,"manage")
            customer_profiles={}
            for existing in db.execute("SELECT data,profile FROM ws_invoices WHERE business_id=?",(self.business,)):
                email=json.loads(existing["data"])["email"].casefold()
                if email:
                    customer_profiles.setdefault(email,json.loads(existing["profile"]))
            for inv in valid:
                key = inv["invoice_no"].casefold()
                old = db.execute("SELECT data,profile FROM ws_invoices WHERE business_id=? AND invoice_key=?", (self.business,key)).fetchone()
                if old:
                    current = get_invoice(db,self.business,key)
                    if inv["amount"]!=current["amount"] and db.execute("SELECT 1 FROM ws_installments WHERE business_id=? AND invoice_key=?",(self.business,key)).fetchone():
                        raise WorkspaceError(f"{inv['invoice_no']}: total cannot change while an installment plan exists.")
                    if inv["currency"] != current["currency"]:
                        raise WorkspaceError(f"{inv['invoice_no']}: currency cannot be changed after import.")
                    if inv["amount_paid"] != current["amount_paid"]:
                        raise WorkspaceError(f"{inv['invoice_no']}: imported Amount Paid differs from the ledger. Record or reverse receipts before importing.")
                    # Preserve the opening paid balance; local receipts remain separate.
                    inv["amount_paid"] = json.loads(old[0])["amount_paid"]
                    inv["outstanding_amount"] = amount(minor(inv["amount"])-minor(inv["amount_paid"]))
                    profile = json.loads(old["profile"])
                    if inv["email"].casefold() != current["email"].casefold():
                        # Permissions and promises belong to the customer, not the invoice number.
                        profile.update(phone="", consents=[], opted_out=False, language="en", promise_date="")
                        previous = customer_profiles.get(inv["email"].casefold())
                        if previous:
                            profile.update({k: previous[k] for k in ("phone", "consents", "opted_out", "language")})
                    db.execute("UPDATE ws_invoices SET data=?,profile=?,revision=revision+1 WHERE business_id=? AND invoice_key=?", (encode(inv),encode(profile),self.business,key))
                    if inv["email"]:
                        customer_profiles.setdefault(inv["email"].casefold(), profile)
                else:
                    profile = {"phone":"", "consents":[], "opted_out":False, "language":"en",
                               "promise_date":"", "owner":"", "followup_note":""}
                    # Contact preferences survive later invoice imports for the same customer.
                    previous=customer_profiles.get(inv["email"].casefold())
                    if previous:
                        profile.update({k:previous[k] for k in ("phone","consents","opted_out","language")})
                    if inv["email"]:
                        customer_profiles.setdefault(inv["email"].casefold(),profile)
                    db.execute("INSERT INTO ws_invoices VALUES(?,?,?,?,1)", (self.business,key,encode(inv),encode(profile)))
            self.store.audit(db,self.business,actor,"invoices.imported",{"count":len(valid)},self.clock())
        return len(valid)

    def invoices(self):
        with self.store.transaction() as db:
            self.auth(db)
            return [get_invoice(db,self.business,r[0]) for r in db.execute("SELECT invoice_key FROM ws_invoices WHERE business_id=? ORDER BY invoice_key",(self.business,))]

    def update_collection(self, key, *, phone="", consents=(), opted_out=False, language="en", promise_date="", owner="", note="", status=None):
        if phone and not re.fullmatch(r"\+[1-9]\d{7,14}",phone):
            raise WorkspaceError("Phone must include the country code, for example +919876543210.")
        if not isinstance(consents,(list,tuple)) or not set(consents) <= {"email","sms","whatsapp"}:
            raise WorkspaceError("Invalid contact permissions.")
        if language not in {"en","hi"} or type(opted_out) is not bool:
            raise WorkspaceError("Invalid language or opt-out value.")
        if promise_date:
            date.fromisoformat(str(promise_date))
        if len(note)>2000 or len(owner)>200:
            raise WorkspaceError("Follow-up note or owner is too long.")
        with self.store.transaction() as db:
            actor = self.auth(db,"collect")
            current = get_invoice(db,self.business,key)
            row = db.execute("SELECT data FROM ws_invoices WHERE business_id=? AND invoice_key=?",(self.business,key.casefold())).fetchone()
            data = json.loads(row[0])
            if status is not None:
                if status not in {"active","disputed","on_hold","cancelled"}:
                    raise WorkspaceError("Choose active, disputed, on hold, or cancelled.")
                data["status"] = ("partially_paid" if minor(current["amount_paid"]) else "unpaid") if status=="active" else status
            profile = dict(phone=phone,consents=sorted(set(consents)),opted_out=opted_out,language=language,
                           promise_date=str(promise_date),owner=owner.strip(),followup_note=note.strip())
            db.execute("UPDATE ws_invoices SET data=?,profile=?,revision=revision+1 WHERE business_id=? AND invoice_key=?",(encode(data),encode(profile),self.business,key.casefold()))
            for customer in db.execute("SELECT invoice_key,data,profile FROM ws_invoices WHERE business_id=? AND invoice_key!=?",(self.business,key.casefold())).fetchall():
                if json.loads(customer["data"])["email"].casefold()==current["email"].casefold() and current["email"]:
                    other=json.loads(customer["profile"])
                    other.update({k:profile[k] for k in ("phone","consents","opted_out","language")})
                    db.execute("UPDATE ws_invoices SET profile=?,revision=revision+1 WHERE business_id=? AND invoice_key=?",(encode(other),self.business,customer["invoice_key"]))
            self.store.audit(db,self.business,actor,"collection.updated",{"invoice":key,**profile,"status":data["status"]},self.clock())

    def receipt(self, key, value, kind, reference, received_on):
        with self.store.transaction() as db:
            actor = self.auth(db,"collect")
            return record_receipt(db,self.store,self.business,key,value,kind,reference,received_on,actor,self.clock())

    def reverse_receipt(self, receipt_id, reason):
        if not 10<=len(reason.strip())<=1000:
            raise WorkspaceError("Explain the reversal in 10–1000 characters.")
        with self.store.transaction() as db:
            actor=self.auth(db,"manage")
            row=db.execute("SELECT * FROM ws_receipts WHERE id=? AND business_id=?",(receipt_id,self.business)).fetchone()
            if not row or row["amount"]<=0:
                raise WorkspaceError("Choose an original receipt or credit.")
            reference="reversal:"+receipt_id
            if db.execute("SELECT 1 FROM ws_receipts WHERE business_id=? AND reference=?",(self.business,reference)).fetchone():
                raise WorkspaceError("This receipt was already reversed.")
            now = self.clock()
            db.execute("INSERT INTO ws_receipts VALUES(?,?,?,?,?,?,?,?,?)",(secrets.token_hex(16),self.business,row["invoice_key"],-row["amount"],"reversal",reference,business_today(db,self.business,now).isoformat(),now.isoformat(),actor))
            db.execute("UPDATE ws_invoices SET revision=revision+1 WHERE business_id=? AND invoice_key=?",(self.business,row["invoice_key"]))
            self.store.audit(db,self.business,actor,"receipt.reversed",{"receipt":receipt_id,"reason":reason},self.clock())

    def receipts(self):
        with self.store.transaction() as db:
            self.auth(db)
            return [dict(r) for r in db.execute("SELECT * FROM ws_receipts WHERE business_id=? ORDER BY created DESC",(self.business,))]

    def audit(self):
        with self.store.transaction() as db:
            self.auth(db)
            return [dict(r) for r in db.execute("SELECT actor,action,detail,created FROM ws_audit WHERE business_id=? ORDER BY id DESC",(self.business,))]

    def reports(self):
        rows=self.invoices()
        with self.store.transaction() as db:
            self.auth(db)
            today=business_today(db,self.business,self.clock())
        currencies={}
        for inv in rows:
            if inv["status"]=="cancelled":
                continue
            c=currencies.setdefault(inv["currency"],{"outstanding":0,"overdue":0,"disputed":0,"promised":0,"not_due":0,"1–30":0,"31–60":0,"61–90":0,"90+":0})
            balance=minor(inv["outstanding_amount"])
            days=(today-date.fromisoformat(inv["due_date"])).days
            c["outstanding"]+=balance
            c["overdue"]+=balance if days>0 else 0
            c["disputed"]+=balance if inv["status"]=="disputed" else 0
            c["promised"]+=balance if inv["promise_date"] and date.fromisoformat(inv["promise_date"])>=today else 0
            bucket="not_due" if days<=0 else "1–30" if days<=30 else "31–60" if days<=60 else "61–90" if days<=90 else "90+"
            c[bucket]+=balance
        return {currency:{k:amount(v) for k,v in values.items()} for currency,values in currencies.items()}

    def statement(self, email):
        return [r for r in self.invoices() if r["email"].casefold()==email.casefold()]

    def process_bank_email(self, subject, body, received_on=None):
        from bank_email import match_and_process_bank_email
        with self.store.transaction() as db:
            actor = self.auth(db, "collect")
            return match_and_process_bank_email(db, self.store, self.business, subject, body, received_on, actor, self.clock())

