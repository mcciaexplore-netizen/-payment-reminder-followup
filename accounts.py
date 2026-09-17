"""Password accounts, expiring sessions, invitations and transaction-time authorization."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from invoices import email_address
from workspace_store import WorkspaceError, utcnow

ROLES = {"owner", "manager", "collector", "viewer"}
PERMISSIONS = {
    "read": ROLES,
    "collect": {"owner", "manager", "collector"},
    "manage": {"owner", "manager"},
    "admin": {"owner"},
}


def digest(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise WorkspaceError("Use a password containing 12 to 256 characters.")
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    return salt + ":" + value


def check_password(password, stored):
    try:
        return hmac.compare_digest(password_hash(password, stored.split(":")[0]), stored)
    except (ValueError, TypeError, AttributeError):
        return False


def authorize(db, token, business, permission="read", now=None):
    now = now or utcnow()
    row = db.execute("""SELECT m.user_id,m.role FROM ws_sessions s JOIN ws_members m
        ON m.user_id=s.user_id WHERE s.token=? AND s.expires>? AND m.business_id=?""",
        (digest(token), now.isoformat(), business)).fetchone()
    if row is None or row["role"] not in PERMISSIONS[permission]:
        raise WorkspaceError("Your session or role does not permit this action. Sign in or contact an owner.")
    return row["user_id"]


class Accounts:
    def __init__(self, store, clock=utcnow):
        self.store, self.clock = store, clock

    def initialized(self):
        with self.store.transaction() as db:
            return bool(db.execute("SELECT 1 FROM ws_users LIMIT 1").fetchone())

    def _session(self, db, user):
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM ws_sessions WHERE expires<=?", (self.clock().isoformat(),))
        db.execute("INSERT INTO ws_sessions VALUES(?,?,?)",
                   (digest(token), user, (self.clock()+timedelta(hours=12)).isoformat()))
        return token

    def bootstrap(self, email, password, name, timezone="Asia/Kolkata", *, setup_token=""):
        expected = os.getenv("WORKSPACE_SETUP_TOKEN", "")
        if os.getenv("VERCEL") == "1" or expected or getattr(self.store,"remote",False):
            if len(expected) < 32 or not hmac.compare_digest(expected.encode(), str(setup_token).encode()):
                raise WorkspaceError("A valid workspace setup code is required from the administrator.")
        email, hashed = email_address(email).casefold(), password_hash(password)
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM ws_users LIMIT 1").fetchone():
                raise WorkspaceError("The workspace is already set up. Sign in or use an invitation.")
            user = secrets.token_hex(16)
            db.execute("INSERT INTO ws_users VALUES(?,?,?)", (user, email, hashed))
            business = self._business(db, user, name, timezone)
            return self._session(db, user), business

    def _business(self, db, user, name, timezone):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
            raise WorkspaceError("Enter a business name of at most 200 characters.")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise WorkspaceError("Choose a valid timezone.") from None
        business = secrets.token_hex(16)
        db.execute("INSERT INTO ws_businesses VALUES(?,?,?,?)",
                   (business, name.strip(), timezone, self.clock().isoformat()))
        db.execute("INSERT INTO ws_members VALUES(?,?,'owner')", (business, user))
        self.store.audit(db, business, user, "business.created", {"name": name}, self.clock())
        return business

    def create_business(self, token, name, timezone="Asia/Kolkata"):
        with self.store.transaction() as db:
            row = db.execute("SELECT user_id FROM ws_sessions WHERE token=? AND expires>?",
                             (digest(token), self.clock().isoformat())).fetchone()
            if not row:
                raise WorkspaceError("Sign in again.")
            return self._business(db, row[0], name, timezone)

    def login(self, email, password):
        email = str(email).strip().casefold()
        now, valid = self.clock(), False
        # Commit failed attempts before raising, so restarting the app cannot reset limits.
        with self.store.transaction() as db:
            fail = db.execute("SELECT * FROM ws_login_failures WHERE email=?", (email,)).fetchone()
            if fail and now-datetime.fromisoformat(fail["since"]) < timedelta(minutes=15) and fail["failures"] >= 5:
                raise WorkspaceError("Too many attempts. Try again in 15 minutes.")
            user = db.execute("SELECT * FROM ws_users WHERE email=?", (email,)).fetchone()
            stored = user["password"] if user else ("0"*32 + ":" + "0"*64)
            valid = check_password(password, stored) and user is not None
            if valid:
                db.execute("DELETE FROM ws_login_failures WHERE email=?", (email,))
                token = self._session(db, user["id"])
            else:
                count = fail["failures"]+1 if fail and now-datetime.fromisoformat(fail["since"]) < timedelta(minutes=15) else 1
                since = fail["since"] if count > 1 else now.isoformat()
                db.execute("INSERT INTO ws_login_failures VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET failures=excluded.failures,since=excluded.since", (email, count, since))
        if not valid:
            raise WorkspaceError("Email or password is incorrect.")
        return token

    def logout(self, token):
        with self.store.transaction() as db:
            db.execute("DELETE FROM ws_sessions WHERE token=?", (digest(token),))

    def businesses(self, token):
        with self.store.transaction() as db:
            return [dict(r) for r in db.execute("""SELECT b.*,m.role FROM ws_businesses b
                JOIN ws_members m ON m.business_id=b.id JOIN ws_sessions s ON s.user_id=m.user_id
                WHERE s.token=? AND s.expires>? ORDER BY b.name""", (digest(token), self.clock().isoformat()))]

    def invite(self, token, business, email, role):
        if role not in ROLES:
            raise WorkspaceError("Choose a valid role.")
        invite = secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            user = authorize(db, token, business, "admin", self.clock())
            email = email_address(email).casefold()
            db.execute("INSERT INTO ws_invites VALUES(?,?,?,?,?,0)",
                       (digest(invite), business, email, role, (self.clock()+timedelta(hours=72)).isoformat()))
            self.store.audit(db, business, user, "member.invited", {"email": email, "role": role}, self.clock())
        return invite

    def accept_invite(self, invite, email, password):
        email = email_address(email).casefold()
        hashed = password_hash(password)
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM ws_invites WHERE token=? AND email=? AND used=0 AND expires>?",
                             (digest(invite), email, self.clock().isoformat())).fetchone()
            if not row:
                raise WorkspaceError("Invitation is invalid, expired, or already used.")
            user = db.execute("SELECT * FROM ws_users WHERE email=?", (email,)).fetchone()
            if user and not check_password(password, user["password"]):
                raise WorkspaceError("Use your existing account password.")
            uid = user["id"] if user else secrets.token_hex(16)
            if not user:
                db.execute("INSERT INTO ws_users VALUES(?,?,?)", (uid, email, hashed))
            # Existing members' roles must only be changed by the explicit role operation.
            db.execute("INSERT INTO ws_members VALUES(?,?,?) ON CONFLICT DO NOTHING", (row["business_id"], uid, row["role"]))
            db.execute("UPDATE ws_invites SET used=1 WHERE token=?", (digest(invite),))
            self.store.audit(db, row["business_id"], uid, "member.joined", {}, self.clock())
            return self._session(db, uid), row["business_id"]

    def members(self, token, business):
        with self.store.transaction() as db:
            authorize(db, token, business, "admin", self.clock())
            return [dict(r) for r in db.execute("SELECT u.id,u.email,m.role FROM ws_members m JOIN ws_users u ON u.id=m.user_id WHERE m.business_id=?", (business,))]

    def set_role(self, token, business, user, role):
        if role not in ROLES | {"removed"}:
            raise WorkspaceError("Invalid role.")
        with self.store.transaction() as db:
            actor = authorize(db, token, business, "admin", self.clock())
            current = db.execute("SELECT role FROM ws_members WHERE business_id=? AND user_id=?", (business,user)).fetchone()
            if not current:
                raise WorkspaceError("Member not found.")
            if current[0] == "owner" and role != "owner" and db.execute("SELECT COUNT(*) FROM ws_members WHERE business_id=? AND role='owner'", (business,)).fetchone()[0] <= 1:
                raise WorkspaceError("A business must retain at least one owner.")
            if role == "removed":
                db.execute("DELETE FROM ws_members WHERE business_id=? AND user_id=?", (business,user))
            else:
                db.execute("UPDATE ws_members SET role=? WHERE business_id=? AND user_id=?", (role,business,user))
            db.execute("UPDATE ws_invites SET used=1 WHERE business_id=? AND email=(SELECT email FROM ws_users WHERE id=?)", (business,user))
            self.store.audit(db, business, actor, "member.role", {"user":user,"role":role}, self.clock())

    def change_password(self, token, old, new):
        hashed = password_hash(new)
        with self.store.transaction() as db:
            row = db.execute("SELECT u.* FROM ws_users u JOIN ws_sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?", (digest(token),self.clock().isoformat())).fetchone()
            if not row or not check_password(old, row["password"]):
                raise WorkspaceError("Current password is incorrect or the session expired.")
            db.execute("UPDATE ws_users SET password=? WHERE id=?", (hashed,row["id"]))
            db.execute("DELETE FROM ws_sessions WHERE user_id=?", (row["id"],))
            return self._session(db, row["id"])
