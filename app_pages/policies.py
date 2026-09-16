import json
import streamlit as st
from scheduling import DEFAULT_TEMPLATES,FIELDS
from workspace_ui import context,require_role,notify

ctx=context()
rules=ctx["scheduling"].rules()
st.write("Create reminder stages before, on, and after the due date. Only the latest reached stage is prepared when a worker catches up.")
st.dataframe([{ "id":r["id"],"name":r["name"],"active":bool(r["active"]),**json.loads(r["settings"])} for r in rules],hide_index=True)
require_role(ctx,"manage")
templates=ctx["scheduling"].templates()
with st.expander("Create or edit a reminder schedule",expanded=not rules):
    editing=st.selectbox("Schedule",[None]+[r["id"] for r in rules],format_func=lambda x:"New schedule" if x is None else next(r["name"] for r in rules if r["id"]==x))
    original=next((r for r in rules if r["id"]==editing),None)
    defaults=json.loads(original["settings"]) if original else {}
    with st.form("rule_"+str(editing)):
        name=st.text_input("Schedule name",value=original["name"] if original else "",placeholder="Seven-day email follow-up")
        channel=st.selectbox("Channel",["email","whatsapp","sms"],index=["email","whatsapp","sms"].index(defaults.get("channel","email")))
        days=st.text_input("Days relative to the due date",value=", ".join(str(d) for d in defaults.get("days",[-3,0,7,14,30])),help="Negative days are before the due date. Use comma-separated whole numbers.")
        template_options=[None]+[r["id"] for r in templates]
        template=st.selectbox("Template",template_options,index=template_options.index(defaults.get("template_id")),format_func=lambda x:"Customer language and business default" if x is None else next(t["name"] for t in templates if t["id"]==x))
        weekdays=st.multiselect("Sending days",list(range(7)),default=defaults.get("weekdays",[0,1,2,3,4]),format_func=lambda i:["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][i])
        start=st.number_input("Start hour in business timezone",0,23,defaults.get("start_hour",9))
        end=st.number_input("End hour in business timezone",1,24,defaults.get("end_hour",18))
        cooldown=st.number_input("Minimum days between live reminders for one invoice",1,365,defaults.get("cooldown_days",7))
        mode=st.selectbox("Mode",["dry_run","live"],index=0 if defaults.get("mode","dry_run")=="dry_run" else 1,format_func=lambda x:"Preview only" if x=="dry_run" else "Live submission")
        auto=st.checkbox("I authorize automatic processing of messages produced by this policy",value=False)
        active=st.checkbox("Activate this schedule",value=bool(original["active"]) if original else False)
        st.caption("Without automatic authorization, messages wait for individual review. Payment, opt-out, pauses, permissions and cooldowns are checked again before submission.")
        save=st.form_submit_button("Save schedule",type="primary")
    if save:
        ctx["scheduling"].save_rule(name,channel=channel,days=[int(d.strip()) for d in days.split(",")],template_id=template,mode=mode,auto_send=auto,start_hour=int(start),end_hour=int(end),weekdays=weekdays,cooldown_days=int(cooldown),active=active,rule_id=editing)
        notify("Reminder schedule saved.")
if rules:
    selected=st.selectbox("Schedule to pause",[r["id"] for r in rules],format_func=lambda x:next(r["name"] for r in rules if r["id"]==x))
    if st.button("Pause schedule and cancel unsent work"):
        ctx["scheduling"].pause_rule(selected)
        notify("Schedule paused.")
if st.button("Prepare currently due scheduled drafts"):
    # This action plans only. Live submission remains the separate worker's responsibility.
    count=ctx["worker"].plan(ctx["business"])
    notify(f"Prepared {count} scheduled reminders within the permitted sending window.")
st.subheader("Message templates")
st.caption("Supported fields: "+", ".join("{"+v+"}" for v in sorted(FIELDS)))
language=st.selectbox("Template language",["en","hi"],format_func=lambda x:"English" if x=="en" else "Hindi")
template_edit=st.selectbox("Edit template",[None]+[t["id"] for t in templates if t["language"]==language],format_func=lambda x:"New template" if x is None else next(t["name"] for t in templates if t["id"]==x))
original_template=next((t for t in templates if t["id"]==template_edit),None)
subject_default,body_default=DEFAULT_TEMPLATES[language]
with st.form("template_"+language+str(template_edit)):
    name=st.text_input("Template name",value=original_template["name"] if original_template else "")
    subject=st.text_input("Subject template",value=original_template["subject"] if original_template else subject_default)
    body=st.text_area("Body template",value=original_template["body"] if original_template else body_default,height=240)
    save=st.form_submit_button("Save template")
if save:
    ctx["scheduling"].save_template(name,language,subject,body,template_edit)
    notify("Template saved.")
st.dataframe([{k:t[k] for k in ("name","language","subject","body")} for t in templates],hide_index=True)
