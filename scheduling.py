"""Durable reminder policies and a worker with atomic claims and no blind retries."""
import json
import os
import secrets
import string
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from accounts import PERMISSIONS, authorize
from connectors import ConnectorRejected
from ledger import get_invoice, minor
from reminders import validate_message
from workspace_store import WorkspaceError, encode, utcnow

CHANNELS={"email","sms","whatsapp"}
FIELDS={"invoice_no","client_name","currency","balance","due_date","business_name","payment_url","terms","reply_email","upi_url"}
DEFAULT_TEMPLATES={
    "en":("Payment reminder: {invoice_no}","Hello {client_name},\n\nOur records show {currency} {balance} outstanding on invoice {invoice_no}, due {due_date}. Please let us know when we can expect payment, or if you have a question.\n{payment_url}\n\nThank you,\n{business_name}"),
    "hi":("भुगतान अनुस्मारक: {invoice_no}","नमस्ते {client_name},\n\nचालान {invoice_no} की शेष राशि {currency} {balance} है। भुगतान की नियत तारीख {due_date} थी। कृपया भुगतान की संभावित तारीख बताएं या किसी प्रश्न के लिए हमसे संपर्क करें।\n{payment_url}\n\nधन्यवाद,\n{business_name}"),
}


def validate_template(subject,body):
    validate_message(subject,body)
    try:
        for template in (subject,body):
            for _,field,spec,conversion in string.Formatter().parse(template):
                if field is not None and (field not in FIELDS or spec or conversion):
                    raise ValueError()
    except ValueError:
        raise WorkspaceError("Use only the supported placeholders, without format codes or attribute access.") from None


def eligible(inv, channel, today):
    if inv["status"] not in {"unpaid","partially_paid"} or minor(inv["outstanding_amount"])<=0:
        raise WorkspaceError("Invoice is paid, cancelled, disputed or on hold.")
    if inv["opted_out"] or channel not in inv["consents"]:
        raise WorkspaceError("The customer has not permitted this channel or has opted out.")
    if inv["promise_date"] and date.fromisoformat(inv["promise_date"])>=today:
        raise WorkspaceError("Reminders are paused through the promised payment date.")
    recipient=inv["email"] if channel=="email" else inv["phone"]
    if not recipient:
        raise WorkspaceError("The customer has no address for this channel.")
    return recipient


def within_hours(settings,now,tz):
    local=now.astimezone(ZoneInfo(tz))
    return local.weekday() in settings["weekdays"] and settings["start_hour"]<=local.hour<settings["end_hour"]


class Scheduling:
    def __init__(self,store,token,business,clock=utcnow):
        self.store,self.token,self.business,self.clock=store,token,business,clock

    def templates(self):
        with self.store.transaction() as db:
            authorize(db,self.token,self.business,"read",self.clock())
            return [dict(r) for r in db.execute("SELECT * FROM ws_templates WHERE business_id=? ORDER BY name",(self.business,))]

    def save_template(self,name,language,subject,body,template_id=None):
        validate_template(subject,body)
        if not 1<=len(name.strip())<=100 or language not in DEFAULT_TEMPLATES:
            raise WorkspaceError("Enter a template name and select English or Hindi.")
        with self.store.transaction() as db:
            actor=authorize(db,self.token,self.business,"manage",self.clock())
            if template_id:
                if not db.execute("SELECT 1 FROM ws_templates WHERE id=? AND business_id=?",(template_id,self.business)).fetchone():
                    raise WorkspaceError("Template not found.")
                db.execute("UPDATE ws_templates SET name=?,language=?,subject=?,body=? WHERE id=? AND business_id=?",(name,language,subject,body,template_id,self.business))
            else:
                template_id=secrets.token_hex(16)
                db.execute("INSERT INTO ws_templates VALUES(?,?,?,?,?,?)",(template_id,self.business,name,language,subject,body))
            self.store.audit(db,self.business,actor,"template.saved",{"id":template_id,"name":name},self.clock())
        return template_id

    def draft(self,invoice_key,channel="email",template_id=None):
        if channel not in CHANNELS:
            raise WorkspaceError("Unknown reminder channel.")
        with self.store.transaction() as db:
            authorize(db,self.token,self.business,"collect",self.clock())
            return render_draft(db,self.business,invoice_key,channel,template_id,self.clock())

    def approve_manual(self,draft,subject,body,mode="dry_run"):
        validate_message(subject,body)
        if mode not in {"dry_run","live"} or draft.get("channel") not in CHANNELS:
            raise WorkspaceError("Invalid reminder mode or channel.")
        now=self.clock()
        with self.store.transaction() as db:
            actor=authorize(db,self.token,self.business,"collect",now)
            current=render_draft(db,self.business,draft["invoice_key"],draft["channel"],draft.get("template_id"),now)
            if current["invoice"]!=draft["invoice"] or current["recipient"]!=draft["recipient"]:
                raise WorkspaceError("Invoice changed since review. Generate a fresh draft.")
            payload={**current,"subject":subject,"body":body,"cooldown_days":7,"expires":(now+timedelta(minutes=30)).isoformat()}
            job=secrets.token_hex(16)
            db.execute("""INSERT INTO ws_jobs(id,business_id,invoice_key,rule_id,unique_key,channel,mode,state,payload,due_at,created,authorized_by)
                VALUES(?,?,?,NULL,?,?,?,'queued',?,?,?,?)""",(job,self.business,current["invoice_key"],job,current["channel"],mode,encode(payload),now.isoformat(),now.isoformat(),actor))
            self.store.audit(db,self.business,actor,"reminder.approved",{"id":job,"mode":mode,"channel":draft["channel"]},now)
            return job

    def save_rule(self,name,*,channel,days,template_id=None,mode="dry_run",auto_send=False,
                  start_hour=9,end_hour=18,weekdays=(0,1,2,3,4),cooldown_days=7,active=False,rule_id=None):
        if not isinstance(days,list) or not 1<=len(days)<=12 or any(type(d) is not int or not -30<=d<=365 for d in days):
            raise WorkspaceError("Use 1–12 day offsets between -30 and 365; negative days are before the due date.")
        if not 1<=len(name.strip())<=100 or channel not in CHANNELS or mode not in {"dry_run","live"} or type(auto_send) is not bool or type(active) is not bool:
            raise WorkspaceError("Invalid rule name, channel, mode or authorization.")
        if type(start_hour) is not int or type(end_hour) is not int or not 0<=start_hour<end_hour<=24:
            raise WorkspaceError("Use a sending window within one day, with its end after its start.")
        if not weekdays or any(type(d) is not int or not 0<=d<=6 for d in weekdays) or type(cooldown_days) is not int or not 1<=cooldown_days<=365:
            raise WorkspaceError("Choose valid weekdays and a cooldown of 1–365 days.")
        settings=dict(channel=channel,days=sorted(set(days)),template_id=template_id,mode=mode,auto_send=auto_send,
                      start_hour=start_hour,end_hour=end_hour,weekdays=list(weekdays),cooldown_days=cooldown_days)
        with self.store.transaction() as db:
            actor=authorize(db,self.token,self.business,"manage",self.clock())
            if template_id and not db.execute("SELECT 1 FROM ws_templates WHERE id=? AND business_id=?",(template_id,self.business)).fetchone():
                raise WorkspaceError("Template not found.")
            if rule_id:
                if not db.execute("SELECT 1 FROM ws_rules WHERE id=? AND business_id=?",(rule_id,self.business)).fetchone():
                    raise WorkspaceError("Rule not found.")
                # Cancel old unsent work; changing a rule requires new authorization.
                db.execute("UPDATE ws_jobs SET state='cancelled',detail='Rule changed' WHERE business_id=? AND rule_id=? AND state IN ('queued','awaiting_approval')",(self.business,rule_id))
                db.execute("UPDATE ws_rules SET name=?,settings=?,active=?,created_by=? WHERE id=? AND business_id=?",(name,encode(settings),int(active),actor,rule_id,self.business))
            else:
                rule_id=secrets.token_hex(16)
                db.execute("INSERT INTO ws_rules VALUES(?,?,?,?,?,?)",(rule_id,self.business,name,encode(settings),int(active),actor))
            self.store.audit(db,self.business,actor,"rule.saved",{"id":rule_id,"active":active,**settings},self.clock())
        return rule_id

    def rules(self):
        with self.store.transaction() as db:
            authorize(db,self.token,self.business,"read",self.clock())
            return [dict(r) for r in db.execute("SELECT * FROM ws_rules WHERE business_id=?",(self.business,))]

    def pause_rule(self,rule_id):
        with self.store.transaction() as db:
            actor=authorize(db,self.token,self.business,"manage",self.clock())
            if not db.execute("SELECT 1 FROM ws_rules WHERE id=? AND business_id=?",(rule_id,self.business)).fetchone():
                raise WorkspaceError("Rule not found.")
            db.execute("UPDATE ws_rules SET active=0 WHERE id=? AND business_id=?",(rule_id,self.business))
            db.execute("UPDATE ws_jobs SET state='cancelled',detail='Rule paused' WHERE rule_id=? AND business_id=? AND state IN ('queued','awaiting_approval')",(rule_id,self.business))
            self.store.audit(db,self.business,actor,"rule.paused",{"id":rule_id},self.clock())

    def jobs(self):
        with self.store.transaction() as db:
            authorize(db,self.token,self.business,"read",self.clock())
            return [dict(r) for r in db.execute("SELECT * FROM ws_jobs WHERE business_id=? ORDER BY created DESC",(self.business,))]

    def approve_job(self,job_id,subject,body):
        validate_message(subject,body)
        with self.store.transaction() as db:
            actor=authorize(db,self.token,self.business,"collect",self.clock())
            row=db.execute("SELECT * FROM ws_jobs WHERE id=? AND business_id=? AND state='awaiting_approval'",(job_id,self.business)).fetchone()
            if not row:
                raise WorkspaceError("This reminder is not awaiting approval.")
            payload=json.loads(row["payload"])
            current=render_draft(db,self.business,row["invoice_key"],row["channel"],payload.get("template_id"),self.clock())
            if current["invoice"]!=payload["invoice"]:
                raise WorkspaceError("Invoice changed; cancel this reminder and generate a new draft.")
            payload.update(subject=subject,body=body,expires=(self.clock()+timedelta(minutes=30)).isoformat())
            db.execute("UPDATE ws_jobs SET state='queued',payload=?,authorized_by=? WHERE id=?",(encode(payload),actor,job_id))
            self.store.audit(db,self.business,actor,"reminder.approved",{"id":job_id},self.clock())

    def cancel_job(self,job_id):
        with self.store.transaction() as db:
            actor=authorize(db,self.token,self.business,"collect",self.clock())
            result=db.execute("UPDATE ws_jobs SET state='cancelled' WHERE id=? AND business_id=? AND state IN ('queued','awaiting_approval')",(job_id,self.business))
            if not result.rowcount:
                raise WorkspaceError("Only unsent queued reminders can be cancelled.")
            self.store.audit(db,self.business,actor,"reminder.cancelled",{"id":job_id},self.clock())

    def reconcile(self,job_id,outcome,note):
        if outcome not in {"submitted","failed"} or not 10<=len(note.strip())<=1000:
            raise WorkspaceError("Choose a verified outcome and explain how you checked it.")
        with self.store.transaction() as db:
            actor=authorize(db,self.token,self.business,"manage",self.clock())
            row=db.execute("SELECT * FROM ws_jobs WHERE id=? AND business_id=?",(job_id,self.business)).fetchone()
            if not row or row["mode"]!="live" or row["state"] not in {"claimed","unknown"}:
                raise WorkspaceError("This attempt does not need reconciliation.")
            if row["state"]=="claimed" and self.clock()-datetime.fromisoformat(row["attempted_at"])<timedelta(hours=1):
                raise WorkspaceError("Wait one hour before resolving an interrupted worker claim.")
            db.execute("UPDATE ws_jobs SET state=?,detail=?,completed_at=? WHERE id=?",(outcome,note,self.clock().isoformat(),job_id))
            self.store.audit(db,self.business,actor,"reminder.reconciled",{"id":job_id,"outcome":outcome,"note":note},self.clock())


def render_draft(db,business,key,channel,template_id,now):
    inv=get_invoice(db,business,key)
    company=db.execute("SELECT * FROM ws_businesses WHERE id=?",(business,)).fetchone()
    today=now.astimezone(ZoneInfo(company["timezone"])).date()
    recipient=eligible(inv,channel,today)
    from business_features import installments_due
    plan=installments_due(db,business,key,inv,today)
    inv["collection_due_date"]=plan[0]["due_date"] if plan else inv["due_date"]
    from ledger import amount
    inv["collection_balance"]=amount(plan[0]["amount_minor"]) if plan else inv["outstanding_amount"]
    subject,body=DEFAULT_TEMPLATES[inv["language"]]
    language=inv["language"]
    if template_id:
        template=db.execute("SELECT * FROM ws_templates WHERE id=? AND business_id=?",(template_id,business)).fetchone()
        if not template:
            raise WorkspaceError("Template not found.")
        subject,body,language=template["subject"],template["body"],template["language"]
    branding=db.execute("SELECT settings FROM ws_branding WHERE business_id=?",(business,)).fetchone()
    brand=json.loads(branding[0]) if branding else {}
    link=db.execute("SELECT url FROM ws_links WHERE business_id=? AND invoice_key=? AND state='active' AND amount=? ORDER BY created DESC LIMIT 1",(business,key.casefold(),minor(inv["collection_balance"]))).fetchone()
    from urllib.parse import urlencode
    upi="upi://pay?"+urlencode({"pa":brand["upi_id"],"pn":brand.get("display_name") or company["name"],"am":inv["collection_balance"],"cu":"INR","tn":inv["invoice_no"]}) if brand.get("upi_id") and inv["currency"]=="INR" else ""
    if not template_id:
        from datetime import date
        due_dt = date.fromisoformat(inv["collection_due_date"])
        overdue_days = (today - due_dt).days
        
        # Dynamic Escalation Tone
        effective_tone = brand.get("tone") or "polite"
        if overdue_days > 21:
            effective_tone = "formal"
            subject = "FINAL NOTICE: Payment required for {invoice_no}"
            body = "ATTENTION {client_name},\n\nYour account has an overdue balance of {currency} {balance} for invoice {invoice_no}, which was due on {due_date}. Immediate settlement is required to prevent further action.\n{payment_url}\n\nYours sincerely,\n{business_name}"
        elif overdue_days >= 8:
            effective_tone = "firm"
            subject = "Urgent: Payment request for invoice {invoice_no}"
            body = "Hello {client_name},\n\nPlease confirm payment arrangements for invoice {invoice_no}: {currency} {balance}, due {due_date}. Contact us promptly if the invoice is disputed.\n{payment_url}\n\n{business_name}"
        elif effective_tone == "formal":
            body = body.replace("Hello ", "Dear ").replace("Thank you,", "Yours sincerely,")

        if brand.get("terms"):
            body += "\n\n{terms}"
        if brand.get("reply_email"):
            body += "\n{reply_email}"
        if upi:
            body += "\nUPI: {upi_url}"
    values={"invoice_no":inv["invoice_no"],"client_name":inv["client_name"],"currency":inv["currency"],"balance":inv["collection_balance"],"due_date":inv["collection_due_date"],"business_name":brand.get("display_name") or company["name"],"payment_url":link[0] if link else "","terms":brand.get("terms",""),"reply_email":brand.get("reply_email",""),"upi_url":upi}
    validate_template(subject,body)
    subject,body=subject.format_map(values),body.format_map(values)
    validate_message(subject,body)
    return {"invoice_key":key.casefold(),"invoice":inv,"recipient":recipient,"channel":channel,"template_id":template_id,"language":language,"subject":subject,"body":body}


class Worker:
    """Trusted server process, not a user-facing API. Claims persist after a crash."""
    def __init__(self,store,connectors,clock=utcnow,live_enabled=None):
        self.store,self.connectors,self.clock=store,connectors,clock
        self.live_enabled=os.getenv("WORKSPACE_LIVE_ENABLED","").lower()=="true" if live_enabled is None else live_enabled

    def plan(self,business=None):
        now,count=self.clock(),0
        with self.store.transaction() as db:
            rules=db.execute("SELECT r.*,b.timezone FROM ws_rules r JOIN ws_businesses b ON b.id=r.business_id WHERE r.active=1"+ (" AND r.business_id=?" if business else ""),(business,) if business else ()).fetchall()
            for rule in rules:
                settings=json.loads(rule["settings"])
                member=db.execute("SELECT role FROM ws_members WHERE business_id=? AND user_id=?",(rule["business_id"],rule["created_by"])).fetchone()
                if not member or member[0] not in PERMISSIONS["manage"] or not within_hours(settings,now,rule["timezone"]):
                    continue
                today=now.astimezone(ZoneInfo(rule["timezone"])).date()
                for row in db.execute("SELECT invoice_key FROM ws_invoices WHERE business_id=?",(rule["business_id"],)).fetchall():
                    try:
                        draft=render_draft(db,rule["business_id"],row[0],settings["channel"],settings["template_id"],now)
                    except WorkspaceError:
                        continue
                    days=(today-date.fromisoformat(draft["invoice"]["collection_due_date"])).days
                    reached=[d for d in settings["days"] if d<=days]
                    if not reached:
                        continue
                    stage=max(reached)
                    unique=f"{rule['id']}:{row[0]}:{draft['invoice']['collection_due_date']}:{stage}"
                    # One stage per invoice, including across worker restarts and concurrent workers.
                    draft.update(cooldown_days=settings["cooldown_days"],stage=stage)
                    cursor=db.execute("""INSERT INTO ws_jobs(id,business_id,invoice_key,rule_id,unique_key,channel,mode,state,payload,due_at,created,authorized_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",(secrets.token_hex(16),rule["business_id"],row[0],rule["id"],unique,settings["channel"],settings["mode"],"queued" if settings["auto_send"] else "awaiting_approval",encode(draft),now.isoformat(),now.isoformat(),rule["created_by"]))
                    count+=cursor.rowcount
            db.execute("INSERT INTO ws_worker VALUES('scheduler',?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail",(now.isoformat(),f"Queued {count} reminders"))
        return count

    def run_one(self,job_id=None,business=None):
        now=self.clock()
        with self.store.transaction() as db:
            where="state='queued' AND due_at<=?"
            params=[now.isoformat()]
            if job_id:
                where+=" AND id=?"; params.append(job_id)
            if business:
                where+=" AND business_id=?"; params.append(business)
            row=db.execute("SELECT * FROM ws_jobs WHERE "+where+" ORDER BY due_at,created LIMIT 1",params).fetchone()
            if not row:
                return None
            row=dict(row)
            payload=json.loads(row["payload"])
            try:
                member=db.execute("SELECT role FROM ws_members WHERE business_id=? AND user_id=?",(row["business_id"],row["authorized_by"])).fetchone()
                if not member or member[0] not in PERMISSIONS["collect"]:
                    raise WorkspaceError("Approver no longer has collection permission.")
                current=render_draft(db,row["business_id"],row["invoice_key"],row["channel"],payload.get("template_id"),now)
                if current["invoice"]!=payload["invoice"]:
                    raise WorkspaceError("Invoice changed after approval or scheduling. Review a fresh draft.")
                if payload.get("expires") and now>datetime.fromisoformat(payload["expires"]):
                    raise WorkspaceError("Approval expired. Review a fresh draft.")
                if row["rule_id"]:
                    rule=db.execute("SELECT r.*,b.timezone FROM ws_rules r JOIN ws_businesses b ON b.id=r.business_id WHERE r.id=? AND r.business_id=?",(row["rule_id"],row["business_id"])).fetchone()
                    if not rule or not rule["active"]:
                        raise WorkspaceError("Reminder policy was paused.")
                    settings=json.loads(rule["settings"])
                    owner=db.execute("SELECT role FROM ws_members WHERE business_id=? AND user_id=?",(row["business_id"],rule["created_by"])).fetchone()
                    if not owner or owner[0] not in PERMISSIONS["manage"]:
                        raise WorkspaceError("Policy owner no longer has management permission.")
                    if not within_hours(settings,now,rule["timezone"]):
                        db.execute("UPDATE ws_jobs SET due_at=? WHERE id=?",((now+timedelta(minutes=30)).isoformat(),row["id"]))
                        return {"id":row["id"],"state":"deferred"}
                if row["mode"]=="live":
                    if not self.live_enabled:
                        raise WorkspaceError("Live sending is disabled on this server. Enable it after configuring a gateway.")
                    cutoff=(now-timedelta(days=payload["cooldown_days"])).isoformat()
                    previous=db.execute("""SELECT 1 FROM ws_jobs WHERE business_id=? AND invoice_key=? AND mode='live'
                        AND id!=? AND (state IN ('claimed','unknown') OR (state IN ('submitted','delivered','read','bounced') AND attempted_at>=?)) LIMIT 1""",(row["business_id"],row["invoice_key"],row["id"],cutoff)).fetchone()
                    if previous:
                        raise WorkspaceError("A recent or uncertain reminder already exists for this invoice.")
                db.execute("UPDATE ws_jobs SET state='claimed',attempted_at=? WHERE id=?",(now.isoformat(),row["id"]))
            except WorkspaceError as exc:
                db.execute("UPDATE ws_jobs SET state='blocked',detail=?,completed_at=? WHERE id=?",(str(exc),now.isoformat(),row["id"]))
                return {"id":row["id"],"state":"blocked","detail":str(exc)}
        state,detail,provider="previewed","Preview recorded; no message sent.",None
        if row["mode"]=="live":
            try:
                result=self.connectors.request(row["business_id"],row["channel"],"message.send",
                    {k:payload[k] for k in ("recipient","subject","body","language","template_id")},row["id"])
                provider=result.get("provider_id")
                if not isinstance(provider,str) or not 1<=len(provider)<=200 or result.get("status")!="submitted":
                    raise WorkspaceError("Gateway did not confirm submission.")
                state,detail="submitted","Gateway accepted the reminder. Delivery is not yet confirmed."
            except ConnectorRejected as exc:
                state,detail="failed",str(exc)
            except Exception:
                state,detail="unknown","Submission result is uncertain. Check the provider before retrying."
        with self.store.transaction() as db:
            db.execute("UPDATE ws_jobs SET state=?,detail=?,provider_id=?,completed_at=? WHERE id=?",(state,detail,provider,self.clock().isoformat(),row["id"]))
            self.store.audit(db,row["business_id"],"worker","reminder."+state,{"id":row["id"],"channel":row["channel"]},self.clock())
        return {"id":row["id"],"state":state,"detail":detail}

    def _heartbeat(self, count):
        with self.store.transaction() as db:
            db.execute("INSERT INTO ws_worker VALUES('sender',?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail",(self.clock().isoformat(),f"Processed {count} reminders"))

    def tick(self,limit=100,business=None,stop_requested=None,on_progress=None):
        self.plan(business)
        results=[]
        for _ in range(limit):
            if stop_requested and stop_requested():
                break
            result=self.run_one(business=business)
            if result is None:
                break
            results.append(result)
            self._heartbeat(len(results))
            if on_progress:
                on_progress(len(results))
        self._heartbeat(len(results))
        if on_progress:
            on_progress(len(results))
        return results
