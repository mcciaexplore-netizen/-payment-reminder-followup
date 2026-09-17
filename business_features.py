"""Installments, transparent forecasts, customer access and backup exports."""
import io
import json
import secrets
import sqlite3
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from accounts import authorize,digest
from ledger import Ledger,amount,get_invoice,minor
from workspace_store import WorkspaceError,business_today,encode,utcnow


def installments_due(db,business,key,inv,today):
    plan=db.execute("SELECT due_date,amount FROM ws_installments WHERE business_id=? AND invoice_key=? ORDER BY sequence",(business,key.casefold())).fetchall()
    if not plan:
        return None
    # Plan amounts describe the original total; all settled amounts allocate oldest first.
    settled=minor(inv["amount_paid"])
    result=[]
    for item in plan:
        remaining=max(0,item["amount"]-settled)
        settled=max(0,settled-item["amount"])
        if remaining:
            result.append({"due_date":item["due_date"],"amount_minor":remaining})
    return result


class BusinessFeatures(Ledger):
    def save_installments(self,key,rows):
        if not isinstance(rows,list) or not 1<=len(rows)<=36:
            raise WorkspaceError("Create 1–36 installments.")
        parsed=[]
        for row in rows:
            parsed.append((date.fromisoformat(str(row["due_date"])).isoformat(),minor(row["amount"])))
        if any(v<=0 for _,v in parsed) or parsed!=sorted(parsed) or len({d for d,_ in parsed})!=len(parsed):
            raise WorkspaceError("Installments need positive amounts and distinct dates in increasing order.")
        with self.store.transaction() as db:
            actor=self.auth(db,"manage")
            inv=get_invoice(db,self.business,key)
            if sum(v for _,v in parsed)!=minor(inv["amount"]):
                raise WorkspaceError("Installments must add up to the full invoice amount. Receipts allocate to the oldest installment first.")
            db.execute("DELETE FROM ws_installments WHERE business_id=? AND invoice_key=?",(self.business,key.casefold()))
            db.executemany("INSERT INTO ws_installments VALUES(?,?,?,?,?)",[(self.business,key.casefold(),i,d,v) for i,(d,v) in enumerate(parsed,1)])
            db.execute("UPDATE ws_invoices SET revision=revision+1 WHERE business_id=? AND invoice_key=?",(self.business,key.casefold()))
            self.store.audit(db,self.business,actor,"installments.saved",{"invoice":key,"plan":parsed},self.clock())

    def installments(self,key):
        with self.store.transaction() as db:
            self.auth(db)
            inv=get_invoice(db,self.business,key)
            return installments_due(db,self.business,key,inv,self.clock().date()) or []

    def branding(self):
        with self.store.transaction() as db:
            self.auth(db)
            row=db.execute("SELECT settings FROM ws_branding WHERE business_id=?",(self.business,)).fetchone()
            return json.loads(row[0]) if row else {"display_name":"","reply_email":"","terms":"","tone":"polite","default_currency":"INR","upi_id":""}

    def save_branding(self,display_name,reply_email,terms,tone,default_currency,upi_id):
        import re
        from invoices import email_address
        if not 1<=len(display_name.strip())<=200 or len(terms)>2000 or tone not in {"polite","formal","firm"}:
            raise WorkspaceError("Enter a business name, a valid tone and terms under 2,000 characters.")
        if reply_email:
            reply_email=email_address(reply_email)
        if not re.fullmatch(r"[A-Z]{3}",default_currency) or (upi_id and not re.fullmatch(r"[A-Za-z0-9._-]{2,128}@[A-Za-z0-9._-]{2,64}",upi_id)):
            raise WorkspaceError("Enter a three-letter currency and a valid UPI ID.")
        data=dict(display_name=display_name.strip(),reply_email=reply_email,terms=terms,tone=tone,default_currency=default_currency,upi_id=upi_id)
        with self.store.transaction() as db:
            actor=self.auth(db,"manage")
            db.execute("INSERT OR REPLACE INTO ws_branding VALUES(?,?)",(self.business,encode(data)))
            # Branding changes invalidate unsent snapshots so old branding is not sent silently.
            db.execute("UPDATE ws_jobs SET state='cancelled',detail='Business branding changed; review a new draft' WHERE business_id=? AND state IN ('queued','awaiting_approval')",(self.business,))
            self.store.audit(db,self.business,actor,"branding.saved",data,self.clock())

    def forecast(self):
        """Planning estimates, grouped by currency. No model invents receipts."""
        with self.store.transaction() as db:
            self.auth(db)
            today=business_today(db,self.business,self.clock())
            history=[]
            for receipt in db.execute("SELECT invoice_key,received_on FROM ws_receipts WHERE business_id=? AND kind='payment' AND amount>0 AND id NOT IN (SELECT substr(reference,10) FROM ws_receipts WHERE business_id=? AND kind='reversal')",(self.business,self.business)):
                inv=get_invoice(db,self.business,receipt["invoice_key"])
                history.append(max(0,(date.fromisoformat(receipt["received_on"])-date.fromisoformat(inv["due_date"])).days))
            typical=sorted(history)[len(history)//2] if history else None
            rows=[]
            for item in db.execute("SELECT invoice_key FROM ws_invoices WHERE business_id=?",(self.business,)).fetchall():
                inv=get_invoice(db,self.business,item[0])
                if inv["status"] not in {"unpaid","partially_paid"}:
                    continue
                plan=installments_due(db,self.business,item[0],inv,today)
                portions=plan if plan is not None else [{"due_date":inv["due_date"],"amount_minor":minor(inv["outstanding_amount"])}]
                for p in portions:
                    expected=date.fromisoformat(p["due_date"])+timedelta(days=typical or 0)
                    basis=f"Due date + median observed delay ({typical} days)" if typical is not None else "Due date; no receipt history yet"
                    if inv["promise_date"]:
                        expected=date.fromisoformat(inv["promise_date"])
                        basis="Customer payment promise"
                    rows.append({"invoice_no":inv["invoice_no"],"currency":inv["currency"],"expected_date":max(today,expected).isoformat(),"amount":amount(p["amount_minor"]),"basis":basis})
            return rows

    def create_portal(self,email,days=7):
        if type(days) is not int or not 1<=days<=30:
            raise WorkspaceError("Customer access may last 1–30 days.")
        from invoices import email_address
        email=email_address(email).casefold()
        token=secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            actor=self.auth(db,"collect")
            if not any(json.loads(r[0])["email"].casefold()==email for r in db.execute("SELECT data FROM ws_invoices WHERE business_id=?",(self.business,))):
                raise WorkspaceError("Customer has no invoices in this business.")
            db.execute("INSERT INTO ws_portals VALUES(?,?,?,?,0)",(digest(token),self.business,email,(self.clock()+timedelta(days=days)).isoformat()))
            self.store.audit(db,self.business,actor,"portal.created",{"email":email,"days":days},self.clock())
        return token

    def revoke_portals(self,email):
        with self.store.transaction() as db:
            actor=self.auth(db,"collect")
            db.execute("UPDATE ws_portals SET revoked=1 WHERE business_id=? AND email=?",(self.business,email.casefold()))
            self.store.audit(db,self.business,actor,"portal.revoked",{"email":email},self.clock())

    def export_business(self):
        """Portable business archive; never includes passwords, sessions or connector keys."""
        tables={"ws_invoices","ws_receipts","ws_templates","ws_rules","ws_jobs","ws_links","ws_audit","ws_installments","ws_branding"}
        output=io.BytesIO()
        with self.store.transaction() as db:
            self.auth(db,"admin")
            with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
                for table in sorted(tables):
                    rows=[dict(r) for r in db.execute("SELECT * FROM "+table+" WHERE business_id=?",(self.business,))]
                    archive.writestr(table+".json",encode(rows))
                archive.writestr("README.txt","Business data export. Contains private invoice and collection data. Not a full server restore backup. Credentials, sessions and customer access tokens are excluded.")
        return output.getvalue()


def portal_data(store,token,now=None):
    now=now or utcnow()
    with store.transaction() as db:
        access=db.execute("SELECT * FROM ws_portals WHERE token=? AND revoked=0 AND expires>?",(digest(token),now.isoformat())).fetchone()
        if not access:
            raise WorkspaceError("This customer link is invalid or expired.")
        company=db.execute("SELECT name FROM ws_businesses WHERE id=?",(access["business_id"],)).fetchone()[0]
        rows=[]
        for item in db.execute("SELECT invoice_key FROM ws_invoices WHERE business_id=?",(access["business_id"],)).fetchall():
            inv=get_invoice(db,access["business_id"],item[0])
            if inv["email"].casefold()!=access["email"]:
                continue
            safe={k:inv[k] for k in ("invoice_no","client_name","amount","amount_paid","outstanding_amount","currency","due_date","status","promise_date")}
            link=db.execute("SELECT url FROM ws_links WHERE business_id=? AND invoice_key=? AND state='active' AND amount=?",(access["business_id"],item[0],minor(inv["outstanding_amount"]))).fetchone()
            safe["payment_url"]=link[0] if link else ""
            rows.append(safe)
        return company,rows


def portal_action(store,token,action,promise_date="",now=None):
    now=now or utcnow()
    if action not in {"opt_out","promise"}:
        raise WorkspaceError("Unknown customer action.")
    with store.transaction() as db:
        access=db.execute("SELECT * FROM ws_portals WHERE token=? AND revoked=0 AND expires>?",(digest(token),now.isoformat())).fetchone()
        if not access:
            raise WorkspaceError("This customer link is invalid or expired.")
        today = business_today(db, access["business_id"], now)
        if action=="promise" and not today<=date.fromisoformat(promise_date)<=today+timedelta(days=90):
            raise WorkspaceError("Choose a payment date within the next 90 days.")
        for row in db.execute("SELECT * FROM ws_invoices WHERE business_id=?",(access["business_id"],)).fetchall():
            if json.loads(row["data"])["email"].casefold()==access["email"]:
                profile=json.loads(row["profile"])
                if action=="opt_out":
                    profile["opted_out"]=True
                else:
                    profile["promise_date"]=promise_date
                db.execute("UPDATE ws_invoices SET profile=?,revision=revision+1 WHERE business_id=? AND invoice_key=?",(encode(profile),access["business_id"],row["invoice_key"]))
        store.audit(db,access["business_id"],"customer-portal","customer."+action,{"email":access["email"],"promise_date":promise_date},now)
