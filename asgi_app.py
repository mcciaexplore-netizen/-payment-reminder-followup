"""ASGI entry point; keep the Streamlit UI script separate from server imports.

This exports a real web application for ASGI hosts. Deployment still needs
durable workspace storage and a separately supervised reminder worker.
"""
from pathlib import Path
import logging

import streamlit as st
from webhooks import create_app


# Serve private customer links and signed callbacks on the same HTTPS origin.
# Creating the routes does not open the workspace database.
http_routes = create_app()
app = st.App(
    Path(__file__).resolve().with_name("app.py"),
    routes=http_routes.routes,
    exception_handlers=http_routes.exception_handlers,
)

# Streamlit initializes Uvicorn log handlers during import, after the CLI has
# applied --no-access-log. Disable this logger explicitly to protect portal URLs.
logging.getLogger("uvicorn.access").disabled = True
