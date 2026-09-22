from datetime import datetime
import os
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

from ledger import minor
from ui_design import invoice_table,money_display,status_list
from workspace_ui import context
from worker_health import is_healthy


ctx=context()
reports=ctx["ledger"].reports()
invoices=ctx["ledger"].invoices()
today=datetime.now(ZoneInfo(ctx["company"]["timezone"])).date()
jobs=ctx["scheduling"].jobs()
attention=[r for r in jobs if r["state"] in {"unknown","failed","blocked","bounced","awaiting_approval","claimed"}]
rules=ctx["scheduling"].rules()

if not reports:
    st.html('''
        <div class="empty-hero-card">
            <div class="empty-hero-badge">GET STARTED</div>
            <h2 class="empty-hero-title">Your Receivables &amp; Cash Flow Hub</h2>
            <p class="empty-hero-sub">Bring your outstanding invoices, payment deadlines, and client communications into one intelligent workflow.</p>
            <div class="empty-hero-grid">
                <div class="empty-step-card">
                    <div class="step-num">1</div>
                    <div class="step-content">
                        <strong>Upload Invoices</strong>
                        <p>Import Excel or CSV files or enter invoices manually with client details.</p>
                    </div>
                </div>
                <div class="empty-step-card">
                    <div class="step-num">2</div>
                    <div class="step-content">
                        <strong>Set Reminder Rules</strong>
                        <p>Configure automated multi-step policies before and after due dates.</p>
                    </div>
                </div>
                <div class="empty-step-card">
                    <div class="step-num">3</div>
                    <div class="step-content">
                        <strong>Collect on Time</strong>
                        <p>Track promises, record incoming payments, and keep ledgers updated.</p>
                    </div>
                </div>
            </div>
        </div>
    ''')
    with st.container(key="empty_actions"):
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            if st.button("Add your first invoices", type="primary", icon=":material/upload_file:", width="stretch"):
                st.switch_page("app_pages/invoice_book.py")
        with c2:
            if st.button("Explore Schedules & Templates", icon=":material/tune:", width="stretch"):
                st.switch_page("app_pages/policies.py")
    st.caption("Need a starting template? Download our sample workbook directly from the Invoices page.")
else:
    with st.container(horizontal=True,horizontal_alignment="distribute",vertical_alignment="center"):
        st.caption(f"{len(invoices)} invoices · {len({r['email'] for r in invoices})} customers in this workspace")
        currencies=sorted(reports,key=lambda c:(c!="INR",c))
        currency=st.segmented_control("Currency",currencies,default=currencies[0],key="overview_currency") or currencies[0]
    totals=reports[currency]
    current=[r for r in invoices if r["currency"]==currency and r["status"]!="cancelled"]
    with st.container(horizontal=True,key="summary_metrics"):
        st.metric("Total outstanding",money_display(totals["outstanding"],currency),border=True)
        st.metric("Past due",money_display(totals["overdue"],currency),border=True)
        st.metric("Payment promised",money_display(totals["promised"],currency),border=True)
        st.metric("In dispute",money_display(totals["disputed"],currency),border=True)

    with st.container(key="overview_details"):
        aging,activity=st.columns([1.7,1],gap="medium")
    with aging,st.container(border=True):
        st.subheader("How long has payment been due?")
        st.caption(f"Outstanding balances by invoice due date · {currency}")
        labels=[("not_due","Not due"),("1–30","1–30 days"),("31–60","31–60 days"),("61–90","61–90 days"),("90+","Over 90 days")]
        buckets=pd.DataFrame([{"Age":label,"Balance":float(totals[key])} for key,label in labels])
        st.vega_lite_chart(buckets,{
            "height":230,
            "mark":{"type":"bar","cornerRadiusEnd":6,"height":20},
            "encoding":{
                "y":{"field":"Age","type":"nominal","sort":[label for _,label in labels],"axis":{"title":None,"labelPadding":12}},
                "x":{"field":"Balance","type":"quantitative","axis":{"title":None,"format":"~s","tickCount":4}},
                "color":{"condition":{"test":"datum.Age === 'Not due'","value":"#94a3b8"},"value":"#146caa"},
                "tooltip":[{"field":"Age","type":"nominal"},{"field":"Balance","type":"quantitative","format":",.2f","title":f"Balance ({currency})"}]
            },
            "config":{"view":{"stroke":None},"font":"'Inter', -apple-system, sans-serif","axis":{"labelFont":"'Inter', -apple-system, sans-serif","labelFontSize":13,"labelColor":"#64748b","domain":False,"ticks":False,"gridColor":"#f1f5f9"}}
        },width="stretch")
    with activity,st.container(border=True):
        st.subheader("Collection activity")
        st.caption("A quick check on your follow-up work.")
        status_list([
            (f"Open invoices · {currency}",sum(minor(r["outstanding_amount"])>0 for r in current)),
            ("Reminders needing attention",len(attention)),
            ("Active schedules",sum(r["active"] for r in rules)),
            ("Reminder previews recorded",sum(r["state"]=="previewed" for r in jobs)),
        ])
        if st.button("View reminders",icon=":material/arrow_forward:",width="stretch"):
            st.switch_page("app_pages/reminder_queue.py")
    with st.container(border=True):
        with st.container(horizontal=True,horizontal_alignment="distribute",vertical_alignment="center"):
            st.subheader("Largest outstanding invoices")
            if st.button("View all invoices",type="tertiary",icon=":material/arrow_forward:"):
                st.switch_page("app_pages/invoice_book.py")
        st.caption(f"Highest balances first · {currency}. Disputed and held invoices remain visible for review.")
        largest=sorted((r for r in current if minor(r["outstanding_amount"])>0),key=lambda r:minor(r["outstanding_amount"]),reverse=True)[:5]
        if largest:
            invoice_table(largest,today=today,compact=True)
        else:
            st.success("All invoices in this currency are settled.")

if attention:
    with st.expander(f"Reminders needing attention · {len(attention)}"):
        st.dataframe([{ "Invoice":r["invoice_key"],"Channel":r["channel"].title(),"Status":r["state"].replace("_"," ").title(),"Details":r["detail"]} for r in attention[:50]],hide_index=True)
cron=os.getenv("WORKSPACE_SCHEDULER_MODE")=="cron"
online=is_healthy(ctx["store"].path,max_age=26*3600 if cron else 180,service="cron" if cron else "service")
st.html('<div class="activity-line"><span class="activity-dot'+('' if online else ' offline')+'"></span>'+
        ('Background reminders are up to date.' if online else 'Background reminders are not reporting activity. Check the worker service.')+'</div>')
