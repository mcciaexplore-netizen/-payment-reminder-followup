# MCCIA branding

Verified against the [official MCCIA website](https://mcciapune.com/) on
16 September 2026. The workspace and the legacy preview use the same branding.

## Original assets

The following files are downloaded originals, without redrawing or recoloring:

| Local asset | Official source | Use |
| --- | --- | --- |
| `static/branding/mccia-logo.png` | [Header logo](https://mcciapune.com/static/assets/images/logos/logo-mccia-white-blue-new.png) | Sign-in header and navigation in light mode |
| `static/branding/mccia-logo-white.png` | [Footer logo](https://mcciapune.com/static/assets/images/logos/logo-mccia-white.png) | Header and navigation in dark mode |
| `static/branding/mccia-favicon.png` | [Favicon](https://mcciapune.com/static/assets/images/logos/Favicon.png) | Browser tab and collapsed navigation |

Assets load locally through Streamlit's image handling, so opening the app does
not require a connection to the MCCIA website. Clicking the navigation logo opens
the official website. These are MCCIA brand assets, not an original app logo.

## Typography

The site's [main stylesheet](https://mcciapune.com/static/assets/scss/main.css)
declares `font-family: "Candara", sans-serif` on the universal selector. Some
buttons and sections override this with `Segoe` or `Segoe UI`. The homepage's
Poppins and Lato Google Fonts links are commented out; they are not its active
main font.

The app's Streamlit theme uses `Candara, 'Segoe UI', sans-serif` for body text
and headings. Candara is installed on the current Windows machine. The official
website does not provide a downloadable Candara webfont in its active stylesheet;
no font binary is bundled or copied from Windows. On devices without Candara,
text uses the next installed font in the fallback list. Matching typography on
every device would require an appropriately licensed webfont.

The reading scale uses a 17px base, 38px page headings and 23px section headings.
Captions use a darker blue-gray (`#43576B`), field and summary labels have stronger
weight, and monetary values use bold tabular figures with less compressed spacing.
Chart labels use the same font stack, with 14px text for readability.

The app uses a white theme: white page and sidebar backgrounds, soft gray input
and table surfaces, dark text, and MCCIA blue accents. The primary blue (`#146CAA`)
comes from the official site's button style. Both original logo variants remain
available; the shared branding helper selects the one for the active theme.

Configuration lives in `.streamlit/config.toml`; shared logo rendering lives in
`branding.py`. Restart Streamlit after changing the theme configuration.

## Interface design

The workspace uses a white design with blue accents, readable dark text,
consistent spacing and visible keyboard focus. `static/branding/workspace.css`
contains the presentation layer for the pinned Streamlit version; account forms
and navigation remain native Streamlit controls. `auth_view.py` provides the
responsive sign-in layout, and `ui_design.py` contains shared page headings,
amount formatting and invoice-table presentation.

The overview separates currencies, uses grouped monetary values and a compact
horizontal aging chart, and links directly to the invoice book and reminders.
Invoice tables use readable headings, formatted dates/amounts and text status
labels in addition to color. Invoice filters and exports use the same selection.
All dashboard numbers come from the ledger; no illustrative trends are added.

Desktop panels stack on narrow screens. Transitions respect reduced-motion
preferences. CSS customizations should be visually rechecked when upgrading
Streamlit, particularly navigation, form spacing and the sign-in breakpoint.
