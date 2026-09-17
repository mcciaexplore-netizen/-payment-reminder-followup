"""Shared MCCIA branding using the original assets from its official website."""
from pathlib import Path

import streamlit as st


ASSETS = Path(__file__).resolve().parent / "static" / "branding"
WEBSITE = "https://mcciapune.com/"


def logo_path():
    name = "mccia-logo-white.png" if st.context.theme.type == "dark" else "mccia-logo.png"
    return str(ASSETS / name)


def configure_page(*, layout="wide"):
    st.set_page_config(
        page_title="MCCIA | Payment follow-up",
        page_icon=str(ASSETS / "mccia-favicon.png"),
        layout=layout,
    )
    st.logo(
        logo_path(),
        size="large",
        link=WEBSITE,
        icon_image=str(ASSETS / "mccia-favicon.png"),
    )
    st.html(ASSETS / "workspace.css")


def show_header_logo():
    st.image(logo_path(), width=220)
