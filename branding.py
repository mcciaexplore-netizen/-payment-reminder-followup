import base64
from pathlib import Path

import streamlit as st


ASSETS = Path(__file__).resolve().parent / "static" / "branding"
WEBSITE = "https://mcciapune.com/"


def get_logo_base64(name="mccia-logo.png"):
    img_path = ASSETS / name
    if img_path.exists():
        with open(img_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    return ""


def logo_path():
    try:
        theme = getattr(st.context, "theme", None)
        theme_type = getattr(theme, "type", "light") if theme else "light"
        name = "mccia-logo-white.png" if theme_type == "dark" else "mccia-logo.png"
    except Exception:
        name = "mccia-logo.png"
    return str(ASSETS / name)


def configure_page(*, layout="wide"):
    favicon = ASSETS / "mccia-favicon.png"
    st.set_page_config(
        page_title="MCCIA | Payment follow-up",
        page_icon=str(favicon) if favicon.exists() else "💼",
        layout=layout,
    )
    logo_file = ASSETS / "mccia-logo.png"
    if logo_file.exists():
        st.logo(
            str(logo_file),
            size="large",
            link=WEBSITE,
            icon_image=str(favicon) if favicon.exists() else None,
        )
    st.html(ASSETS / "workspace.css")


def show_header_logo():
    b64 = get_logo_base64()
    if b64:
        st.html(
            f'''<div class="auth-header-brand">
                <img src="data:image/png;base64,{b64}" alt="MCCIA" class="auth-brand-logo-img" />
                <span class="auth-brand-pipe"></span>
                <span class="auth-brand-app-title">PAYMENT FOLLOW-UP</span>
            </div>'''
        )

