"""Small ASGI webhook receiver, separate from the authenticated browser UI."""
import json
import os
import html
import hmac
from urllib.parse import parse_qs
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse,HTMLResponse,RedirectResponse
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool

import config
from connectors import Connectors, Vault
from workspace_store import WorkspaceError, WorkspaceStore
from business_features import portal_data,portal_action
from accounts import digest


def create_app(connectors=None):
    # Delay database/key access until startup; importing this module has no writes.
    async def receive(request:Request):
        if config.IS_DEMO:
            return JSONResponse({"error":"Webhooks are disabled in demo mode."},status_code=403)
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>512*1024:
                return JSONResponse({"error":"Request too large."},status_code=413)
        gateway=connectors
        if gateway is None:
            path=Path(os.getenv("WORKSPACE_DATABASE_PATH",str(config.ROOT/".data"/"workspace.sqlite3")))
            store=WorkspaceStore(path)
            gateway=Connectors(store,Vault(path.parent))
        try:
            result=await run_in_threadpool(gateway.webhook,request.path_params["business"],request.path_params["kind"],bytes(raw),
                request.headers.get("x-reminder-timestamp",""),request.headers.get("x-reminder-signature",""))
            return JSONResponse({"result":result})
        except (WorkspaceError,ValueError,KeyError,TypeError,json.JSONDecodeError):
            # A generic response prevents unauthenticated tenant/configuration probing.
            return JSONResponse({"error":"Event was not accepted. Verify the signature and matching identifiers."},status_code=400)

    async def health(request):
        return JSONResponse({"status":"ok"})

    async def portal(request):
        if config.IS_DEMO:
            return HTMLResponse("Customer access is disabled in demo mode.",status_code=403)
        token=request.path_params["token"]
        path=Path(os.getenv("WORKSPACE_DATABASE_PATH",str(config.ROOT/".data"/"workspace.sqlite3")))
        store=connectors.store if connectors else WorkspaceStore(path)
        headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff",
                 "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"}
        try:
            if request.method=="POST":
                raw=bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw)>4096:
                        return HTMLResponse("Request too large.",status_code=413,headers=headers)
                form=parse_qs(raw.decode())
                if not hmac.compare_digest(form.get("csrf",[""])[0],digest("portal-action:"+token)):
                    raise WorkspaceError("Invalid form.")
                await run_in_threadpool(portal_action,store,token,form.get("action",[""])[0],form.get("promise_date",[""])[0])
                return RedirectResponse(request.url.path,status_code=303,headers=headers)
            company,rows=await run_in_threadpool(portal_data,store,token)
            escape=lambda value:html.escape(str(value),quote=True)
            cards=[]
            for inv in rows:
                payment=f'<p><a href="{escape(inv["payment_url"])}" rel="noreferrer">Open secure payment page</a></p>' if inv["payment_url"] else ""
                cards.append(f'<section><h2>{escape(inv["invoice_no"])}</h2><p>{escape(inv["client_name"])}</p><p>Outstanding: <strong>{escape(inv["currency"])} {escape(inv["outstanding_amount"])}</strong></p><p>Due: {escape(inv["due_date"])} · {escape(inv["status"])}</p><p>Payment promise: {escape(inv["promise_date"] or "None recorded")}</p>{payment}</section>')
            csrf=escape(digest("portal-action:"+token))
            body=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Customer statement</title>
            <style>body{{font-family:Candara,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;color:#152331;background:#f6f8fa}}h1,h2,h3{{font-family:Segoe,sans-serif;font-weight:600}}section,form{{background:white;border:1px solid #d5dce2;padding:20px;margin:16px 0;border-radius:8px}}button,input{{padding:10px;font:inherit}}button{{font-family:Segoe,sans-serif;font-weight:500}}a{{color:#1752a0}}</style>
            <h1>{escape(company)}</h1><p>Your invoice statement. Payments are reflected after confirmation. Keep this private access link secure.</p>{''.join(cards)}
            <form method="post"><h2>Tell us your payment date</h2><p>This pauses reminders through the date you choose for this customer account.</p><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="promise"><label>Payment date <input type="date" name="promise_date" required></label> <button>Record payment promise</button></form>
            <form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="opt_out"><button>Opt out of reminders for this customer account</button></form></html>'''
            return HTMLResponse(body,headers=headers)
        except (WorkspaceError,ValueError,KeyError,UnicodeDecodeError):
            return HTMLResponse("This customer link or request is invalid or expired. Contact the business for assistance.",status_code=400,headers=headers)

    return Starlette(routes=[Route("/health",health),Route("/webhooks/{business}/{kind}",receive,methods=["POST"]),Route("/portal/{token}",portal,methods=["GET","POST"])])


app=create_app()
