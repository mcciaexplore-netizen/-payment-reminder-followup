# Demo workspace

The local demo account is `demo@example.com`. Its password is configured privately
in `.env`, never in application source. The account is created separately through
normal account setup; enabling the buttons does not create accounts or reset
passwords.

## Demo login buttons

Set the following in the server's `.env` and restart Streamlit:

```dotenv
WORKSPACE_DEMO_LOGIN_ENABLED=true
WORKSPACE_DEMO_PASSWORD=your-existing-demo-account-password
WORKSPACE_LIVE_ENABLED=false
```

- **Open demo workspace** signs in through the normal password and session checks.
- **Fill demo login** fills the existing sign-in form; choose **Continue** to sign in.

The demo account must own exactly one workspace. Buttons are disabled by default,
and hidden when live sending is enabled or a demo password is missing. This is
shared owner access for product demonstrations: keep fictional data in this
workspace. Turn the setting off before using this deployment for real business
data. This feature is separate from `APP_MODE=demo`, which opens the legacy,
per-session upload preview instead of the authenticated workspace.

## Sample data

After configuring the existing demo account, run:

```powershell
.venv\Scripts\python.exe -m demo_workspace
```

The command adds:

- 8 fictional customers and 10 invoices using `example.com` addresses.
- INR and USD balances, with upcoming and overdue dates across aging buckets.
- Partial and full payment examples, a credit, a dispute, a hold, an opt-out and
  a future payment promise.
- One installment plan, with the first installment settled.
- English and Hindi reminder templates and a paused preview schedule.
- One actual reminder preview; no live messages are sent.

Dates are relative to the day samples are loaded. Samples persist across sign-ins;
they are not refreshed on every login. All records are created in one transaction
through the existing services. An audit marker makes repeated setup a no-op,
preserving demo edits. Setup refuses non-demo users, unauthorized businesses and
an existing invoice book that has not previously been seeded.
