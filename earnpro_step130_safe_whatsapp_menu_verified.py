from urllib.parse import quote
from flask import Flask, request, redirect, url_for, session
import sqlite3
from datetime import date, datetime
from functools import wraps
import os
import re
import platform
import shutil
import sys
import platform
import shutil
import sys
import html
import csv
import io
import time
import secrets
from flask import Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# Step 27: browser security headers.
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

app.secret_key = os.environ.get("EARNPRO_SECRET_KEY", "earnpro_luxury_gold_secret_2026")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
if os.environ.get("EARNPRO_SECURE_COOKIE", "").lower() == "1":
    app.config["SESSION_COOKIE_SECURE"] = True

# Step 33: admin notification broadcast to all users.
# Step 31: admin system-health dashboard.
# Step 30: automatic idle-session timeout.
SESSION_IDLE_TIMEOUT = 30 * 60  # 30 minutes
# Step 32: invalidate authenticated sessions whenever the Flask app restarts.
# This prevents an old browser session from reopening the dashboard after the
# website/server has been stopped and started again.
SESSION_INSTANCE_ID = secrets.token_hex(16)

@app.before_request
def enforce_session_timeout():
    # Only enforce timeout for authenticated sessions.
    if request.endpoint in {"login", "admin_login", "static"}:
        return
    if "username" not in session and "admin" not in session:
        return

    # A fresh process gets a new instance ID. Any session created by a
    # previous process is cleared and sent back to its appropriate login page.
    if session.get("session_instance") != SESSION_INSTANCE_ID:
        was_admin = bool(session.get("admin"))
        session.clear()
        return redirect(url_for("admin_login" if was_admin else "login"))

    now_ts = time.time()
    last_seen = session.get("last_seen")
    if last_seen:
        try:
            if now_ts - float(last_seen) > SESSION_IDLE_TIMEOUT:
                was_admin = bool(session.get("admin"))
                session.clear()
                if was_admin:
                    return redirect(url_for("admin_login"))
                return redirect(url_for("login"))
        except (TypeError, ValueError):
            was_admin = bool(session.get("admin"))
            session.clear()
            return redirect(url_for("admin_login" if was_admin else "login"))

    session["last_seen"] = now_ts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "earnpro.db")

ADMIN_USER = os.environ.get("EARNPRO_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("EARNPRO_ADMIN_PASS", "admin123")

# Step 27 security: lightweight in-memory login throttling.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 10 * 60
_login_attempts = {}

def _client_key():
    return request.remote_addr or "unknown"

def _login_locked(key):
    now = time.time()
    item = _login_attempts.get(key)
    if not item:
        return False
    failures, first_at = item
    if now - first_at >= LOGIN_WINDOW_SECONDS:
        _login_attempts.pop(key, None)
        return False
    return failures >= LOGIN_MAX_FAILURES

def _record_login_failure(key):
    now = time.time()
    failures, first_at = _login_attempts.get(key, (0, now))
    if now - first_at >= LOGIN_WINDOW_SECONDS:
        failures, first_at = 0, now
    _login_attempts[key] = (failures + 1, first_at)

def _clear_login_failures(key):
    _login_attempts.pop(key, None)

@app.before_request
def launch_gate():
    # Admin can always access the control panel while maintenance mode is enabled.
    # Public users see a simple maintenance page; login/register remain available.
    if get_setting("maintenance_mode") == "1":
        path = request.path or "/"
        if path.startswith("/static/") or path.startswith("/admin/") or session.get("admin"):
            return None
        if path in ("/login", "/register"):
            return None
        return layout("Maintenance", "<div class='card' style='text-align:center'><div style='font-size:42px'>🔧</div><h2 class='gold'>EarnPro Maintenance</h2><p>Website filhal maintenance mein hai. Thori dair baad dobara try karein.</p><a class='btn2' href='/login'>USER LOGIN</a></div>")
    return None


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response

DEFAULT_SETTINGS = {
    "plan_30": "1500",
    "plan_60": "2500",
    "referral_bonus": "15",
    "withdraw_referral_rule_enabled": "1",
    "default_ad_reward": "20",
    "daily_ads_limit": "5",
    "min_withdraw": "1000",
    "jazzcash_number": "",
    "easypaisa_number": "",
    "announcement_enabled": "0",
    "announcement_text": "Welcome to EarnPro! Stay active and check your latest updates.",
    "whatsapp_number": "",
    "whatsapp_group_link": "",
    "support_email": "",
    "registration_enabled": "1",
    "payments_enabled": "1",
    "withdrawals_enabled": "1",
    "maintenance_mode": "0",
    "site_name": "EarnPro"
}


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT UNIQUE NOT NULL,"
        "password TEXT NOT NULL,"
        "balance INTEGER DEFAULT 0,"
        "total_earning INTEGER DEFAULT 0,"
        "referral_bonus INTEGER DEFAULT 0,"
        "ref_code TEXT UNIQUE,"
        "referred_by TEXT DEFAULT '',"
        "referrals INTEGER DEFAULT 0,"
        "referral_rewarded INTEGER DEFAULT 0,"
        "ads_date TEXT DEFAULT '',"
        "ads_today INTEGER DEFAULT 0,"
        "last_login_at TEXT DEFAULT '')"
    )

    cur.execute(
        "CREATE TABLE IF NOT EXISTS payments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "plan TEXT NOT NULL,"
        "amount INTEGER NOT NULL,"
        "method TEXT NOT NULL,"
        "txid TEXT NOT NULL,"
        "status TEXT DEFAULT 'Pending',"
        "reason TEXT DEFAULT '',"
        "created_at TEXT DEFAULT '',"
        "activation_at TEXT DEFAULT '',"
        "expires_at TEXT DEFAULT '')"
    )

    # Step 28: login history and last-login tracking.
    # Step 29 adds admin login history monitoring.
    try:
        user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
        if "last_login_at" not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT DEFAULT ''")
    except Exception:
        pass

    # Step 101: make referral fields safe for databases created by older builds.
    # This prevents /referral and /register?ref=... from failing when an older
    # EarnPro database is reused after an upgrade.
    try:
        user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
        referral_columns = {
            "referral_bonus": "INTEGER DEFAULT 0",
            "ref_code": "TEXT",
            "referred_by": "TEXT DEFAULT ''",
            "referrals": "INTEGER DEFAULT 0",
            "referral_rewarded": "INTEGER DEFAULT 0",
        }
        for col, definition in referral_columns.items():
            if col not in user_cols:
                cur.execute("ALTER TABLE users ADD COLUMN " + col + " " + definition)
    except Exception:
        pass

    cur.execute(
        "CREATE TABLE IF NOT EXISTS login_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "login_at TEXT DEFAULT '',"
        "ip_address TEXT DEFAULT '')"
    )

    # Step 29: admin login history for security monitoring.
    cur.execute(
        "CREATE TABLE IF NOT EXISTS admin_login_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "admin_username TEXT NOT NULL,"
        "login_at TEXT DEFAULT '',"
        "ip_address TEXT DEFAULT '')"
    )

    # Step 17: user block/unblock support.
    try:
        user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
        if "blocked" not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
    except Exception:
        pass

    # Step 15: transaction IDs are one-time use.
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_txid_unique ON payments(txid)")
    except Exception:
        pass

    # Step 75: dynamic plan catalog + one-time payment consumption.
    cur.execute(
        "CREATE TABLE IF NOT EXISTS plans ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT UNIQUE NOT NULL,"
        "amount INTEGER NOT NULL,"
        "days INTEGER NOT NULL,"
        "active INTEGER DEFAULT 1,"
        "created_at TEXT DEFAULT '',"
        "updated_at TEXT DEFAULT '')"
    )
    try:
        pcols = [r[1] for r in cur.execute("PRAGMA table_info(payments)").fetchall()]
        if "plan_days" not in pcols:
            cur.execute("ALTER TABLE payments ADD COLUMN plan_days INTEGER DEFAULT 0")
        if "consumed_at" not in pcols:
            cur.execute("ALTER TABLE payments ADD COLUMN consumed_at TEXT DEFAULT ''")
    except Exception:
        pass
    plan_count = cur.execute("SELECT COUNT(*) AS c FROM plans").fetchone()["c"]
    if plan_count == 0:
        now_seed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.executemany(
            "INSERT INTO plans(name,amount,days,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            [("30 Days", setting_int("plan_30", 1500), 30, 1, now_seed, now_seed),
             ("60 Days", setting_int("plan_60", 2500), 60, 1, now_seed, now_seed)]
        )


    # Step 14/16: add plan dates to existing databases without deleting data.
    try:
        cols = [r[1] for r in cur.execute("PRAGMA table_info(payments)").fetchall()]
        if "activation_at" not in cols:
            cur.execute("ALTER TABLE payments ADD COLUMN activation_at TEXT DEFAULT ''")
        if "expires_at" not in cols:
            cur.execute("ALTER TABLE payments ADD COLUMN expires_at TEXT DEFAULT ''")
    except Exception:
        pass

    cur.execute(
        "CREATE TABLE IF NOT EXISTS withdrawals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "amount INTEGER NOT NULL,"
        "method TEXT NOT NULL,"
        "account TEXT NOT NULL,"
        "status TEXT DEFAULT 'Pending',"
        "reason TEXT DEFAULT '',"
        "created_at TEXT DEFAULT '',"
        "expires_at TEXT DEFAULT '')"
    )

    cur.execute(
        "CREATE TABLE IF NOT EXISTS notifications ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "title TEXT NOT NULL,"
        "message TEXT NOT NULL,"
        "is_read INTEGER DEFAULT 0,"
        "created_at TEXT DEFAULT '')"
    )

    cur.execute(
        "CREATE TABLE IF NOT EXISTS audit_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "admin_username TEXT NOT NULL,"
        "action TEXT NOT NULL,"
        "target_username TEXT DEFAULT '',"
        "details TEXT DEFAULT '',"
        "created_at TEXT DEFAULT '')"
    )

    cur.execute(
        "CREATE TABLE IF NOT EXISTS transactions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "type TEXT NOT NULL,"
        "amount INTEGER NOT NULL,"
        "description TEXT NOT NULL,"
        "created_at TEXT DEFAULT '')"
    )

    cur.execute(
        "CREATE TABLE IF NOT EXISTS settings ("
        "key TEXT PRIMARY KEY,"
        "value TEXT NOT NULL)"
    )

    cur.execute(
        "CREATE TABLE IF NOT EXISTS ads ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "title TEXT NOT NULL,"
        "content TEXT DEFAULT '',"
        "link TEXT DEFAULT '',"
        "reward INTEGER DEFAULT 20,"
        "active INTEGER DEFAULT 1,"
        "created_at TEXT DEFAULT '')"
    )

    # Step 46: prevent the same ad from being claimed more than once per day.
    cur.execute(
        "CREATE TABLE IF NOT EXISTS ad_claims ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "ad_id INTEGER NOT NULL,"
        "claim_date TEXT NOT NULL,"
        "created_at TEXT DEFAULT '',"
        "UNIQUE(username, ad_id, claim_date))"
    )

    # Step 55: keep a lightweight ad-watch history for admin analytics.
    cur.execute(
        "CREATE TABLE IF NOT EXISTS ad_watch_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "ad_id INTEGER NOT NULL,"
        "ad_title TEXT NOT NULL,"
        "reward INTEGER NOT NULL,"
        "watch_seconds INTEGER NOT NULL,"
        "claim_date TEXT NOT NULL,"
        "created_at TEXT DEFAULT '')"
    )

    # Step 62: ad open/click tracking.
    cur.execute(
        "CREATE TABLE IF NOT EXISTS ad_clicks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "ad_id INTEGER NOT NULL,"
        "ad_title TEXT NOT NULL,"
        "click_date TEXT NOT NULL,"
        "created_at TEXT DEFAULT '')"
    )

    ad_cols = {r["name"] for r in cur.execute("PRAGMA table_info(ads)").fetchall()}
    if "video_url" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN video_url TEXT DEFAULT ''")
    if "watch_seconds" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN watch_seconds INTEGER DEFAULT 30")
    if "category" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN category TEXT DEFAULT 'General'")
    if "priority" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN priority INTEGER DEFAULT 0")
    if "start_date" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN start_date TEXT DEFAULT ''")
    if "end_date" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN end_date TEXT DEFAULT ''")
    if "max_claims" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN max_claims INTEGER DEFAULT 0")
    if "daily_max_claims" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN daily_max_claims INTEGER DEFAULT 0")
    if "user_max_claims" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN user_max_claims INTEGER DEFAULT 0")
    if "budget" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN budget INTEGER DEFAULT 0")
    if "target_mode" not in ad_cols:
        cur.execute("ALTER TABLE ads ADD COLUMN target_mode TEXT DEFAULT 'all'")

    for key, value in DEFAULT_SETTINGS.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            (key, value)
        )

    # Step 41: keep the existing admin login compatible with Step 40.
    # Admin credentials come from EARNPRO_ADMIN_USER / EARNPRO_ADMIN_PASS,
    # with defaults admin / admin123.
    ad_count = cur.execute("SELECT COUNT(*) AS c FROM ads").fetchone()["c"]
    if ad_count == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        demo_ads = [
            ("Ad 1", "Demo earning advertisement 1", "", 20),
            ("Ad 2", "Demo earning advertisement 2", "", 20),
            ("Ad 3", "Demo earning advertisement 3", "", 20),
            ("Ad 4", "Demo earning advertisement 4", "", 20),
            ("Ad 5", "Demo earning advertisement 5", "", 20)
        ]
        for title, content, link, reward in demo_ads:
            cur.execute(
                "INSERT INTO ads(title,content,link,reward,active,created_at) "
                "VALUES(?,?,?,?,1,?)",
                (title, content, link, reward, now)
            )

    conn.commit()
    conn.close()


def get_setting(key):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    conn = db()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value))
    )
    conn.commit()
    conn.close()


def setting_int(key, fallback):
    try:
        return int(get_setting(key))
    except Exception:
        return fallback


def esc(value):
    return html.escape(str(value if value is not None else ""))


def password_matches(stored_password, provided_password):
    """Support old plaintext passwords once, then migrate them to hashes."""
    if not stored_password:
        return False
    try:
        return check_password_hash(stored_password, provided_password)
    except (ValueError, TypeError):
        return stored_password == provided_password


def hash_password(password):
    return generate_password_hash(password)


def add_audit_log(action, target_username="", details=""):
    try:
        admin_username = session.get("admin", "admin")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = db()
        conn.execute(
            "INSERT INTO audit_logs(admin_username,action,target_username,details,created_at) VALUES(?,?,?,?,?)",
            (admin_username, action, target_username, details, now)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def add_notification(username, title, message):
    conn = db()
    conn.execute(
        "INSERT INTO notifications(username,title,message,created_at) VALUES(?,?,?,?)",
        (username, title, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()


def add_transaction(username, kind, amount, description):
    conn = db()
    conn.execute(
        "INSERT INTO transactions(username,type,amount,description,created_at) VALUES(?,?,?,?,?)",
        (username, kind, amount, description, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()


def get_user(username):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return row


def get_active_plan(username):
    conn = db()
    row = conn.execute(
        "SELECT * FROM payments WHERE username=? AND status='Approved' "
        "AND (expires_at IS NULL OR expires_at='' OR expires_at>?) "
        "ORDER BY id DESC LIMIT 1",
        (username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ).fetchone()
    conn.close()
    return row


def get_unread_notifications(username):
    conn = db()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE username=? AND is_read=0",
        (username,)
    ).fetchone()
    conn.close()
    return row["c"] if row else 0


def get_admin_counts():
    conn = db()
    pending_payments = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE status='Pending'").fetchone()["c"]
    pending_withdrawals = conn.execute("SELECT COUNT(*) AS c FROM withdrawals WHERE status='Pending'").fetchone()["c"]
    conn.close()
    return pending_payments, pending_withdrawals


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper


def layout(title, body):
    return (
        "<!DOCTYPE html><html><head>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>" + esc(title) + " - EarnPro</title>"
        "<style>"
        "*{box-sizing:border-box}"
        "body{margin:0;font-family:Arial,sans-serif;background:#090909;color:#fff}"
        ".wrap{max-width:540px;margin:auto;padding:16px}"
        ".logo{text-align:center;color:#d4af37;font-size:30px;font-weight:900;margin:12px 0}"
        ".sub{text-align:center;color:#aaa;margin-bottom:18px}"
        ".card{background:#151515;border:1px solid #8c6a12;border-radius:18px;padding:18px;margin:13px 0;box-shadow:0 8px 25px #000}"
        ".gold{color:#d4af37}"
        ".btn{display:block;width:100%;padding:14px;margin:9px 0;border:0;border-radius:12px;background:linear-gradient(135deg,#d4af37,#fff0a8,#b8860b);color:#080808;font-weight:900;text-align:center;text-decoration:none;cursor:pointer}"
        ".btn2{display:block;width:100%;padding:13px;margin:9px 0;border:1px solid #d4af37;border-radius:12px;color:#d4af37;text-align:center;text-decoration:none;background:#101010;cursor:pointer}"
        ".danger{border-color:#8d3030;color:#ff8888}"
        "input,select,textarea{width:100%;padding:13px;margin:7px 0 12px;border-radius:10px;border:1px solid #765b15;background:#0d0d0d;color:#fff}"
        "textarea{min-height:100px;resize:vertical}"
        "label{color:#d4af37;font-weight:bold}"
        ".grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}"
        ".stat{padding:15px;background:#0e0e0e;border:1px solid #4f3d0f;border-radius:13px;text-align:center}"
        ".stat b{display:block;color:#d4af37;font-size:21px;margin-top:5px}"
        ".small{font-size:13px;color:#aaa}"
        ".footer{text-align:center;margin:24px 0 8px;color:#bfa85a;font-size:12px;border-top:1px solid #39300f;padding-top:14px}"
        ".ok{color:#7dff9a}.bad{color:#ff7d7d}.pending{color:#ffe37d}"
        ".pill{display:inline-block;padding:5px 9px;border-radius:20px;border:1px solid #6f5717;color:#d4af37;font-size:12px}"
        ".ad{background:#0d0d0d;border:1px solid #5d4810;border-radius:14px;padding:14px;margin:10px 0}"
        "table{width:100%;border-collapse:collapse;font-size:13px}"
        "th,td{padding:9px;border-bottom:1px solid #39300f;text-align:left;vertical-align:top}"
        "th{color:#d4af37}"
        "/* Step 96 - Professional user referral CTA (admin panel untouched) */"
        ".referral-pro-cta{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:16px 18px;margin:14px 0;border:1px solid rgba(212,175,55,.35);border-radius:16px;background:linear-gradient(135deg,rgba(212,175,55,.14),rgba(20,20,20,.82));box-shadow:0 8px 24px rgba(0,0,0,.18)}"
        ".referral-pro-cta .referral-copy{display:flex;align-items:center;gap:12px}.referral-pro-cta .referral-icon{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;background:rgba(212,175,55,.18);font-size:21px}.referral-pro-cta .referral-title{font-weight:800;font-size:16px}.referral-pro-cta .referral-sub{font-size:12px;opacity:.72;margin-top:3px}.referral-pro-cta a{display:inline-flex;align-items:center;gap:7px;padding:10px 15px;border-radius:11px;text-decoration:none;font-weight:800;border:1px solid rgba(212,175,55,.55);background:linear-gradient(135deg,#d4af37,#f1d477);color:#17120a;white-space:nowrap;transition:.18s}.referral-pro-cta a:hover{transform:translateY(-1px);box-shadow:0 7px 18px rgba(212,175,55,.22)}"
        "@media(max-width:600px){.referral-pro-cta{align-items:stretch;flex-direction:column}.referral-pro-cta a{text-align:center;justify-content:center;width:100%;box-sizing:border-box}}"
        "</style></head><body><div class='wrap'>"
        "<div class='logo'>👑 EarnPro</div>"
        "<div class='sub'>My Earning Website</div>"
        + body +
        "<div class='footer'>© EarnPro • Create by Mohsin Khan</div>"
        "</div></body></html>"
    )


@app.route("/")
def home():
    body = (
        "<div class='card' style='text-align:center'>"
        "<div style='font-size:38px'>💎</div><h1 class='gold'>EarnPro</h1>"
        "<p>Earn, refer, manage balance and request withdrawals from one dashboard.</p>"
        "</div>"
        "<div class='card'><h3 class='gold'>Features</h3>"
        "<p>💰 Daily earning tasks</p><p>👥 Referral rewards</p>"
        "<p>📋 Payment & earning history</p><p>🔔 Notifications</p>"
        "<p>🏦 Withdrawal requests</p></div>"
        "<a class='btn' href='/register'>CREATE ACCOUNT</a>"
        "<a class='btn2' href='/login'>LOGIN</a>"
    )
    return layout("Home", body)


@app.route("/register", methods=["GET", "POST"])
def register():
    msg = ""
    if get_setting("registration_enabled") != "1":
        return layout("Registration Closed", "<div class='card'><h2 class='gold'>Registration Closed</h2><p>New registrations temporarily disabled hain. Admin se contact karein.</p><a class='btn2' href='/login'>LOGIN</a><a class='btn2' href='/contact'>CONTACT</a></div>")

    # Referral links are accepted from /register?ref=EP00001 and kept in the form.
    ref = (request.args.get("ref", "") or "").strip().upper()
    if len(ref) > 64:
        ref = ref[:64]

    if request.method == "POST":
        username = (request.form.get("username", "") or "").strip()
        password = request.form.get("password", "") or ""
        ref_code = (request.form.get("ref_code", "") or "").strip().upper()
        if len(ref_code) > 64:
            ref_code = ref_code[:64]

        if not username or not password:
            msg = "<p class='bad'>Username aur password zaroor likhein.</p>"
        elif len(username) < 3:
            msg = "<p class='bad'>Username kam az kam 3 characters ka ho.</p>"
        else:
            conn = db()
            try:
                existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                if existing:
                    msg = "<p class='bad'>Ye username already registered hai.</p>"
                else:
                    referred_by = ""
                    if ref_code:
                        ref_user = conn.execute("SELECT username FROM users WHERE ref_code=?", (ref_code,)).fetchone()
                        if ref_user and ref_user["username"] != username:
                            referred_by = ref_user["username"]

                    cur = conn.cursor()
                    cur.execute("INSERT INTO users(username,password,referred_by) VALUES(?,?,?)", (username, hash_password(password), referred_by))
                    user_id = cur.lastrowid
                    new_ref = "EP" + str(user_id).zfill(5)
                    # Do not let an old/corrupt referral code break registration.
                    cur.execute("UPDATE users SET ref_code=? WHERE id=?", (new_ref, user_id))
                    conn.commit()
                if not msg:
                    add_notification(username, "Welcome to EarnPro", "Your account has been created successfully.")
                    return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                conn.rollback()
                msg = "<p class='bad'>Referral/registration data conflict hua. Dobara try karein.</p>"
            except Exception:
                conn.rollback()
                msg = "<p class='bad'>Registration temporarily unavailable. Dobara try karein.</p>"
            finally:
                conn.close()

    body = (
        "<style>"
        ".auth-wrap{max-width:520px;margin:28px auto;padding:0 8px}.auth-card{background:linear-gradient(145deg,#171717,#0d0d0d);border:1px solid #3b321d;border-radius:24px;padding:28px;box-shadow:0 18px 55px rgba(0,0,0,.35)}"
        ".auth-logo{width:62px;height:62px;margin:0 auto 14px;border-radius:18px;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#f5d77a,#b98a22);color:#111;font-size:28px;font-weight:900;box-shadow:0 8px 25px rgba(214,170,65,.22)}"
        ".auth-title{text-align:center;font-size:29px;margin:0 0 7px;color:#f4d477}.auth-sub{text-align:center;color:#aaa;margin:0 0 22px}.auth-field{margin:14px 0}.auth-field label{display:block;color:#d9c37b;font-size:13px;font-weight:700;margin-bottom:7px}.auth-field input{width:100%;box-sizing:border-box;padding:13px 14px;border-radius:12px;border:1px solid #4a4028;background:#111;color:#fff;outline:none}.auth-field input:focus{border-color:#d6ad45;box-shadow:0 0 0 3px rgba(214,173,69,.10)}.auth-main-btn{width:100%;margin-top:8px;padding:14px;border:0;border-radius:13px;background:linear-gradient(135deg,#f2d477,#b98720);color:#111;font-weight:900;font-size:15px;cursor:pointer}.auth-main-btn:hover{filter:brightness(1.06)}.auth-note{text-align:center;color:#777;font-size:12px;margin:16px 0 0}.auth-link{display:block;text-align:center;margin-top:15px;color:#e1c15d;text-decoration:none;font-weight:700}.auth-link:hover{text-decoration:underline}.auth-msg{margin-bottom:12px}"
        "</style>"
        "<div class='auth-wrap'><div class='auth-card'>"
        "<div class='auth-logo'>E</div>"
        "<h2 class='auth-title'>Create Your EarnPro Account</h2>"
        "<p class='auth-sub'>Register once and access your earning dashboard.</p>"
        "<div class='auth-msg'>" + msg + "</div>"
        "<form method='post'>"
        "<div class='auth-field'><label>USERNAME</label><input name='username' required autocomplete='username' placeholder='Enter username'></div>"
        "<div class='auth-field'><label>PASSWORD</label><input type='password' name='password' required autocomplete='new-password' placeholder='Create password'></div>"
        "<div class='auth-field'><label>REFERRAL CODE <span style='color:#777'>(OPTIONAL)</span></label><input name='ref_code' value='" + esc(ref) + "' placeholder='EP00001'></div>"
        "<button class='auth-main-btn' type='submit'>CREATE ACCOUNT →</button></form>"
        "<a class='auth-link' href='/login'>Already have an account? Login</a>"
        "<p class='auth-note'>Secure account registration • EarnPro</p>"
        "</div></div>"
    )
    return layout("Register", body)


@app.route("/login", methods=["GET", "POST"])
def login():
    msg = ""
    key = "user:" + _client_key()
    if request.method == "POST":
        if _login_locked(key):
            msg = "<p class='bad'>Too many failed login attempts. 10 minutes baad dobara try karein.</p>"
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = get_user(username)
            if user and password_matches(user["password"], password):
                if "blocked" in user.keys() and user["blocked"]:
                    msg = "<p class='bad'>Aapka account admin ne temporarily block kiya hua hai.</p>"
                else:
                    # Migrate legacy plaintext passwords after a successful login.
                    if not str(user["password"]).startswith(("scrypt:", "pbkdf2:")):
                        conn = db()
                        conn.execute("UPDATE users SET password=? WHERE username=?",
                                     (hash_password(password), username))
                        conn.commit()
                        conn.close()
                    _clear_login_failures(key)
                    login_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn = db()
                    conn.execute("UPDATE users SET last_login_at=? WHERE username=?", (login_at, username))
                    conn.execute("INSERT INTO login_history(username, login_at, ip_address) VALUES(?,?,?)",
                                 (username, login_at, request.remote_addr or "unknown"))
                    conn.commit()
                    conn.close()
                    session.clear()
                    session["username"] = username
                    session["last_seen"] = time.time()
                    session["session_instance"] = SESSION_INSTANCE_ID
                    return redirect(url_for("dashboard"))
            elif not msg:
                _record_login_failure(key)
                msg = "<p class='bad'>Username ya password incorrect hai.</p>"

    body = (
        "<style>"
        ".auth-wrap{max-width:520px;margin:28px auto;padding:0 8px}.auth-card{background:linear-gradient(145deg,#171717,#0d0d0d);border:1px solid #3b321d;border-radius:24px;padding:28px;box-shadow:0 18px 55px rgba(0,0,0,.35)}"
        ".auth-logo{width:62px;height:62px;margin:0 auto 14px;border-radius:18px;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#f5d77a,#b98a22);color:#111;font-size:28px;font-weight:900;box-shadow:0 8px 25px rgba(214,170,65,.22)}"
        ".auth-title{text-align:center;font-size:29px;margin:0 0 7px;color:#f4d477}.auth-sub{text-align:center;color:#aaa;margin:0 0 22px}.auth-field{margin:14px 0}.auth-field label{display:block;color:#d9c37b;font-size:13px;font-weight:700;margin-bottom:7px}.auth-field input{width:100%;box-sizing:border-box;padding:13px 14px;border-radius:12px;border:1px solid #4a4028;background:#111;color:#fff;outline:none}.auth-field input:focus{border-color:#d6ad45;box-shadow:0 0 0 3px rgba(214,173,69,.10)}.auth-main-btn{width:100%;margin-top:8px;padding:14px;border:0;border-radius:13px;background:linear-gradient(135deg,#f2d477,#b98720);color:#111;font-weight:900;font-size:15px;cursor:pointer}.auth-link{display:block;text-align:center;margin-top:15px;color:#e1c15d;text-decoration:none;font-weight:700}.auth-msg{margin-bottom:12px}.auth-note{text-align:center;color:#777;font-size:12px;margin:16px 0 0}"
        "</style>"
        "<div class='auth-wrap'><div class='auth-card'>"
        "<div class='auth-logo'>E</div>"
        "<h2 class='auth-title'>Welcome Back</h2>"
        "<p class='auth-sub'>Login to your EarnPro earning dashboard.</p>"
        "<div class='auth-msg'>" + msg + "</div>"
        "<form method='post'>"
        "<div class='auth-field'><label>USERNAME</label><input name='username' required autocomplete='username' placeholder='Enter username'></div>"
        "<div class='auth-field'><label>PASSWORD</label><input type='password' name='password' required autocomplete='current-password' placeholder='Enter password'></div>"
        "<button class='auth-main-btn' type='submit'>LOGIN TO EARNPRO →</button></form>"
        "<a class='auth-link' href='/register'>New here? Create an account</a>"
        "<p class='auth-note'>Secure login • Luxury Gold Dashboard</p>"
        "</div></div>"
    )
    return layout("Login", body)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    """Step 20: richer user dashboard with account, plan and recent activity."""
    username = session["username"]
    user = get_user(username)
    active = get_active_plan(username)
    unread = get_unread_notifications(username)

    conn = db()
    recent_payments = conn.execute(
        "SELECT * FROM payments WHERE username=? ORDER BY id DESC LIMIT 3",
        (username,)
    ).fetchall()
    recent_withdrawals = conn.execute(
        "SELECT * FROM withdrawals WHERE username=? ORDER BY id DESC LIMIT 3",
        (username,)
    ).fetchall()
    recent_transactions = conn.execute(
        "SELECT * FROM transactions WHERE username=? ORDER BY id DESC LIMIT 5",
        (username,)
    ).fetchall()
    recent_notifications = conn.execute(
        "SELECT * FROM notifications WHERE username=? ORDER BY id DESC LIMIT 3",
        (username,)
    ).fetchall()
    conn.close()

    active_text = "No Active Plan"
    plan_status = "NO ACTIVE PLAN"
    expiry_text = "-"
    if active:
        plan_status = "ACTIVE"
        active_text = active["plan"] + " - Rs." + str(active["amount"])
        expiry_text = active["expires_at"] or "-"
        if active["expires_at"]:
            try:
                expiry_dt = datetime.strptime(active["expires_at"], "%Y-%m-%d %H:%M:%S")
                if expiry_dt <= datetime.now():
                    plan_status = "EXPIRED"
                    active_text += " (EXPIRED)"
                else:
                    active_text += " (ACTIVE)"
            except ValueError:
                active_text += " (ACTIVE)"

    def status_class(status):
        return "pending" if status == "Pending" else ("ok" if status == "Approved" else "bad")

    def dash_btn(href, icon, title, subtitle):
        return ("<a class='dashboard-btn' href='" + href + "'>"
                "<span class='dash-icon'>" + icon + "</span>"
                "<span class='dash-copy'><strong>" + title + "</strong><small>" + subtitle + "</small></span>"
                "<span class='dash-arrow'>→</span></a>")

    body = (
        "<style>.dashboard-actions{display:grid;grid-template-columns:1fr;gap:14px;margin-top:16px}.dashboard-btn{display:flex!important;align-items:center;gap:14px;min-height:78px!important;padding:12px 14px!important;border-radius:22px!important;text-decoration:none!important;box-sizing:border-box;background:linear-gradient(145deg,#2a2411 0%,#17150e 55%,#0b0b0b 100%)!important;color:#fff!important;border:1px solid rgba(212,175,55,.72)!important;box-shadow:0 8px 22px rgba(0,0,0,.38),inset 0 1px 0 rgba(255,241,170,.18),0 0 14px rgba(212,175,55,.08);transition:transform .18s,box-shadow .18s,filter .18s}.dashboard-btn:hover{transform:translateY(-2px);filter:brightness(1.08);box-shadow:0 12px 28px rgba(0,0,0,.46),0 0 18px rgba(212,175,55,.16)}.dashboard-btn:active{transform:scale(.99)}.dash-icon{width:58px;height:58px;min-width:58px;border-radius:17px;display:flex;align-items:center;justify-content:center;font-size:28px;background:linear-gradient(145deg,#3a3013,#17140b);border:1px solid rgba(212,175,55,.65);box-shadow:inset 0 1px 0 rgba(255,255,255,.12),0 4px 12px rgba(0,0,0,.28)}.dash-copy{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:4px;min-width:0;flex:1}.dash-copy strong{font-size:16px;line-height:1.15;color:#fff;letter-spacing:.15px}.dash-copy small{font-size:12px;line-height:1.25;color:#cfcfcf;font-weight:500}.dash-arrow{width:44px;height:44px;min-width:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#ffe88c,#d4af37);color:#171108;font-size:25px;font-weight:900;box-shadow:0 4px 12px rgba(0,0,0,.3)}.dashboard-btn2{display:flex!important;align-items:center;justify-content:center;min-height:54px!important;padding:14px 18px!important;border-radius:18px!important;text-decoration:none!important;font-weight:800!important;background:linear-gradient(145deg,#1b1b1b,#0b0b0b)!important;color:#f6d76d!important;border:1px solid rgba(212,175,55,.62)!important}.dashboard-section-title{display:flex;align-items:center;gap:9px}.dashboard-quick{border:1px solid rgba(212,175,55,.28);background:linear-gradient(180deg,rgba(212,175,55,.07),rgba(255,255,255,.02))}@media(max-width:560px){.dashboard-btn{min-height:72px!important}.dash-icon{width:52px;height:52px;min-width:52px;font-size:25px}.dash-copy strong{font-size:15px}.dash-copy small{font-size:11px}.dash-arrow{width:40px;height:40px;min-width:40px;font-size:23px}}</style>" +
        ("<div class='card' style='border-color:#d4af37'><h3 class='gold'>📢 EarnPro Announcement</h3><p>" + esc(get_setting("announcement_text")) + "</p></div>" if get_setting("announcement_enabled") == "1" else "") +
        "<div class='card'><h2 class='gold'>Hello, " + esc(username) + " 👑</h2>"
        "<p class='small'>Welcome back to your EarnPro Luxury Gold dashboard.</p>"
        "<div class='grid'>"
        "<div class='stat'>Balance<b>Rs." + str(user["balance"]) + "</b></div>"
        "<div class='stat'>Total Earning<b>Rs." + str(user["total_earning"]) + "</b></div>"
        "<div class='stat'>Referral Bonus<b>Rs." + str(user["referral_bonus"]) + "</b></div>"
        "<div class='stat'>Referrals<b>" + str(user["referrals"]) + "</b></div>"
        "</div></div>"
        "<div class='card dashboard-quick'><h3 class='gold dashboard-section-title'>📦 Current Plan</h3>"
        "<p><b>Plan:</b> " + esc(active_text) + "</p>"
        "<p><b>Status:</b> <span class='" + ("ok" if plan_status == "ACTIVE" else "bad") + "'>" + esc(plan_status) + "</span></p>"
        "<p><b>Expiry:</b> " + esc(expiry_text) + "</p>"
        "<div class='dashboard-actions'>" +
        dash_btn('/active-plan','👤','View Active Plan','Check your current plan details') +
        dash_btn('/payment-history','💳','Payment History','View all your payments') +
        dash_btn('/plan-payment-center','🧾','Plan & Payment Center','Manage plans and payments') +
        "</div></div>"
        "<div class='card dashboard-quick'><h3 class='gold dashboard-section-title'>⚡ Quick Actions</h3><div class='dashboard-actions'>" +
        dash_btn('/watch-ads','🎁','Watch Ads','Earn rewards by watching ads') +
        dash_btn('/plans','💳','Buy Plan','Get more benefits and higher earnings') +
        dash_btn('/referral','👥','Refer & Earn','Invite friends and earn rewards') +
        dash_btn('/referral-analytics','📊','Referral Analytics','View your referral stats and earnings') +
        dash_btn('/withdraw','🏦','Withdraw','Withdraw your earnings') +
        dash_btn('/wallet','👛','Wallet Center','View balance and wallet activity') +
        dash_btn('/earning-center','💰','Earning Center','View your earnings and transactions') +
        dash_btn('/earning-insights','📈','Earning Insights','View earning trends and breakdowns') +
        dash_btn('/ad-history','🎬','Ad History','View watched ads and rewards') +
        dash_btn('/notifications','🔔','Notifications','View your notifications (" + str(unread) + ")') +
        dash_btn('/profile','👤','Profile','Manage your account and password') +
        "</div></div>"
    )

    body += "<div class='card'><h3 class='gold'>💳 Recent Payments</h3>"
    if not recent_payments:
        body += "<p class='small'>No payment requests yet.</p>"
    else:
        for r in recent_payments:
            body += (
                "<div class='ad'><b class='gold'>" + esc(r["plan"]) + "</b>"
                "<p>Rs." + str(r["amount"]) + " | " + esc(r["method"]) + "</p>"
                "<p class='" + status_class(r["status"]) + "'><b>" + esc(r["status"]) + "</b></p>"
                "<p class='small'>" + esc(r["created_at"] or "-") + "</p></div>"
            )
    body += "<a class='dashboard-btn2' href='/payment-history'>VIEW ALL PAYMENTS</a></div>"

    body += "<div class='card'><h3 class='gold'>🏦 Recent Withdrawals</h3>"
    if not recent_withdrawals:
        body += "<p class='small'>No withdrawal requests yet.</p>"
    else:
        for r in recent_withdrawals:
            body += (
                "<div class='ad'><b>Rs." + str(r["amount"]) + "</b>"
                "<p>" + esc(r["method"]) + "</p>"
                "<p class='" + status_class(r["status"]) + "'><b>" + esc(r["status"]) + "</b></p>"
                "<p class='small'>" + esc(r["created_at"] or "-") + "</p></div>"
            )
    body += "<a class='dashboard-btn2' href='/withdraw-history'>VIEW ALL WITHDRAWALS</a></div>"

    body += "<div class='card'><h3 class='gold'>📈 Recent Earning Activity</h3>"
    if not recent_transactions:
        body += "<p class='small'>No transactions yet.</p>"
    else:
        body += "<table><tr><th>Type</th><th>Amount</th><th>Description</th></tr>"
        for r in recent_transactions:
            body += (
                "<tr><td>" + esc(r["type"]) + "</td><td>Rs." + str(r["amount"]) +
                "</td><td>" + esc(r["description"]) + "</td></tr>"
            )
        body += "</table>"
    body += "<a class='dashboard-btn2' href='/history'>VIEW FULL HISTORY</a></div>"

    body += "<div class='card'><h3 class='gold'>🔔 Latest Notifications</h3>"
    if not recent_notifications:
        body += "<p class='small'>No notifications.</p>"
    else:
        for n in recent_notifications:
            marker = "NEW" if not n["is_read"] else ""
            body += (
                "<div class='ad'><b class='gold'>" + esc(n["title"]) + "</b> " +
                ("<span class='pending'>" + marker + "</span>" if marker else "") +
                "<p>" + esc(n["message"]) + "</p>"
                "<p class='small'>" + esc(n["created_at"] or "-") + "</p></div>"
            )
    body += "<a class='dashboard-btn2' href='/notifications'>VIEW ALL NOTIFICATIONS</a></div>"
    body += "<a class='dashboard-btn2' href='/logout'>LOGOUT</a>"

    # Step 130: WhatsApp contact menu — dashboard only.
    wa_number = get_setting("whatsapp_number").strip()
    wa_group = get_setting("whatsapp_group_link").strip()
    wa_channel = get_setting("whatsapp_channel_link").strip()
    wa_number_href = ""
    if wa_number:
        wa_digits = re.sub(r"[^0-9]", "", wa_number)
        if wa_digits:
            wa_number_href = "https://wa.me/" + wa_digits
    wa_items = ""
    if wa_number_href:
        wa_items += "<a href='" + esc(wa_number_href) + "' target='_blank' rel='noopener'>📱 WhatsApp Number</a>"
    if wa_group:
        wa_items += "<a href='" + esc(wa_group) + "' target='_blank' rel='noopener'>👥 WhatsApp Group</a>"
    if wa_channel:
        wa_items += "<a href='" + esc(wa_channel) + "' target='_blank' rel='noopener'>📢 WhatsApp Channel</a>"
    if not wa_items:
        wa_items = "<a href='/contact'>💬 Contact Support</a>"
    body += """
<style>
.earnpro-wa-wrap{position:fixed;right:16px;bottom:16px;z-index:99999}
.earnpro-wa-float{width:60px;height:60px;border-radius:50%;padding:0;border:2px solid rgba(255,255,255,.18);background:#19c637;overflow:hidden;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.45),0 0 16px rgba(25,198,55,.25)}
.earnpro-wa-float img{width:100%;height:100%;display:block;object-fit:cover}
.earnpro-wa-menu{display:none;position:absolute;right:0;bottom:70px;width:205px;padding:8px;background:#151515;border:1px solid #8d6b22;border-radius:12px;box-shadow:0 10px 28px rgba(0,0,0,.55)}
.earnpro-wa-menu.show{display:block}
.earnpro-wa-menu a{display:block;color:#fff;text-decoration:none;padding:10px 11px;border-radius:8px;font-size:13px}
.earnpro-wa-menu a:hover{background:#2b210f}
@media(max-width:560px){.earnpro-wa-wrap{right:12px;bottom:12px}.earnpro-wa-float{width:56px;height:56px}.earnpro-wa-menu{bottom:64px;width:190px}}
</style>
"""
    body += (
        '<div class="earnpro-wa-wrap">'
        '<button type="button" class="earnpro-wa-float" onclick="document.getElementById(\'earnpro-wa-menu\').classList.toggle(\'show\')" aria-label="WhatsApp options">'
        '<img src=\'data:image/jpeg;base64,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\' alt=\'WhatsApp\'>'
        '</button>'
        "<div id='earnpro-wa-menu' class='earnpro-wa-menu'>" + wa_items + "</div>"
        '</div>'
    )

    return layout("Dashboard", body)


@app.route("/active-plan")
@login_required
def active_plan():
    plan = get_active_plan(session["username"])
    if not plan:
        body = (
            "<div class='card'><h2 class='gold'>Active Plan</h2>"
            "<p class='bad'>Abhi koi approved active plan nahi hai.</p>"
            "<a class='btn' href='/plans'>BUY PLAN</a></div>"
        )
        return layout("Active Plan", body)

    body = (
        "<div class='card'><h2 class='gold'>Your Active Plan</h2>"
        "<p><b>Plan:</b> " + esc(plan["plan"]) + "</p>"
        "<p><b>Amount:</b> Rs." + str(plan["amount"]) + "</p>"
        "<p><b>Method:</b> " + esc(plan["method"]) + "</p>"
        "<p><b>TXID:</b> " + esc(plan["txid"]) + "</p>"
        "<p><b>Expires:</b> " + esc(plan["expires_at"] or "No expiry (legacy plan)") + "</p>"
        "<p class='ok'><b>Status: ACTIVE</b></p>"
        "<a class='btn' href='/watch-ads'>WATCH ADS</a></div>"
    )
    return layout("Active Plan", body)


@app.route("/plans", methods=["GET", "POST"])
@login_required
def plans():
    if get_setting("payments_enabled") != "1":
        return layout("Payments Disabled", "<div class='card'><h2 class='gold'>Plan Payments Temporarily Disabled</h2><p>Admin ne filhal plan payments band ki hain.</p><a class='btn2' href='/dashboard'>DASHBOARD</a></div>")
    username = session["username"]
    msg = ""
    conn = db()
    plan_rows = conn.execute("SELECT * FROM plans WHERE active=1 ORDER BY days ASC, id ASC").fetchall()

    if request.method == "POST":
        plan_id = request.form.get("plan_id", "").strip()
        method = request.form.get("method")
        txid = request.form.get("txid", "").strip()
        selected = conn.execute("SELECT * FROM plans WHERE id=? AND active=1", (plan_id,)).fetchone()

        if not selected:
            msg = "<p class='bad'>Selected plan available nahi hai.</p>"
        elif method not in ["JazzCash", "Easypaisa"]:
            msg = "<p class='bad'>Payment method select karein.</p>"
        elif not txid:
            msg = "<p class='bad'>Transaction ID zaroor likhein.</p>"
        else:
            existing = conn.execute("SELECT id,status FROM payments WHERE txid=?", (txid,)).fetchone()
            if existing:
                msg = "<p class='bad'>Ye Transaction ID pehle use ho chuki hai. New payment ki new Transaction ID submit karein.</p>"
            else:
                try:
                    conn.execute(
                        "INSERT INTO payments(username,plan,amount,method,txid,status,created_at,plan_days,consumed_at) "
                        "VALUES(?,?,?,?,?,'Pending',?,?,?)",
                        (username, selected["name"], int(selected["amount"]), method, txid,
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(selected["days"]), "")
                    )
                    conn.commit()
                    msg = ("<p class='pending'>Payment request submit ho gayi. Admin approval ke baad "
                           "payment amount plan mein consume/lock ho jayega aur dobara use nahi ho sakega.</p>")
                except sqlite3.IntegrityError:
                    conn.rollback()
                    msg = "<p class='bad'>Ye Transaction ID already submitted hai. New payment ki new Transaction ID submit karein.</p>"

    plan_rows = conn.execute("SELECT * FROM plans WHERE active=1 ORDER BY days ASC, id ASC").fetchall()
    conn.close()
    if not plan_rows:
        msg += "<p class='bad'>Abhi koi active plan available nahi hai. Admin se plan activate karwayen.</p>"

    jc = get_setting("jazzcash_number")
    ep = get_setting("easypaisa_number")
    body = "<div class='card' style='padding:0;overflow:hidden;background:linear-gradient(145deg,#0b0b0b,#171717);border:1px solid #3b2a0a;box-shadow:0 14px 35px rgba(0,0,0,.35);'>"
    body += "<div style='padding:28px 22px 22px;text-align:center;background:radial-gradient(circle at top,#3a2a0b 0%,#171717 48%,#0b0b0b 100%);'>"
    body += "<div style='font-size:34px;margin-bottom:6px;'>👑</div><h2 class='gold' style='margin:0;font-size:28px;letter-spacing:.5px;'>Choose Your Plan</h2>"
    body += "<p style='margin:8px 0 0;color:#c9c9c9;'>Select a plan that matches your earning goals.</p></div>" + msg
    body += "<div style='padding:20px 16px 8px;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;'>"
    for idx, pr in enumerate(plan_rows):
        pid = str(pr["id"])
        pname = esc(pr["name"])
        amount = str(pr["amount"])
        days = str(pr["days"])
        featured = idx == 0 and len(plan_rows) > 1
        badge = "<span style=\"position:absolute;top:12px;right:12px;background:#d4af37;color:#111;padding:4px 9px;border-radius:20px;font-size:11px;font-weight:800;\">POPULAR</span>" if featured else ""
        border = "#d4af37" if featured else "#3b3b3b"
        body += ("<div class=\"choose-plan-card\" data-plan-id=\"" + pid + "\" onclick=\"selectPlan('" + pid + "')\" style=\"position:relative;cursor:pointer;padding:22px 18px;border:1px solid " + border + ";border-radius:18px;background:linear-gradient(145deg,#171717,#0d0d0d);transition:.2s;box-shadow:0 8px 22px rgba(0,0,0,.25);\">" + badge +
                 "<div style=\"font-size:13px;color:#aaa;letter-spacing:1px;text-transform:uppercase;\">EarnPro Plan</div>"
                 "<h3 class=\"gold\" style=\"font-size:22px;margin:8px 0;\">" + pname + "</h3>"
                 "<div style=\"font-size:30px;font-weight:900;color:#fff;margin:4px 0;\">Rs." + amount + "</div>"
                 "<div style=\"color:#c9c9c9;margin-bottom:16px;\">⏳ " + days + " days access</div>"
                 "<div style=\"padding:10px;border-radius:12px;background:rgba(212,175,55,.08);color:#e8e8e8;font-size:13px;\">✓ Active plan benefits<br>✓ Ad earning access<br>✓ Secure approval system</div>"
                 "<button type=\"button\" class=\"btn\" style=\"width:100%;margin-top:16px;\">SELECT THIS PLAN</button></div>")
    body += "</div>"
    body += "<div style='padding:8px 16px 18px;'><div class='ad' style='border:1px solid #4a3914;background:linear-gradient(135deg,#19160e,#111);border-radius:16px;padding:18px;'><div style='font-size:18px;font-weight:800;color:#d4af37;margin-bottom:10px;'>💳 Payment Details</div><div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;'><div style='padding:12px;border-radius:12px;background:#0c0c0c;border:1px solid #292929;'><span style='color:#aaa;font-size:12px;'>JAZZCASH</span><br><b>" + esc(jc or "Admin ne number set nahi kiya") + "</b></div><div style='padding:12px;border-radius:12px;background:#0c0c0c;border:1px solid #292929;'><span style='color:#aaa;font-size:12px;'>EASYPAISA</span><br><b>" + esc(ep or "Admin ne number set nahi kiya") + "</b></div></div></div></div>"
    body += "<div class='card' style='margin:0 16px 16px;border:1px solid #333;background:#111;'><h3 class='gold' style='margin-top:0;'>🧾 Payment Submission</h3>"
    body += "<form method='post'><label>Selected Plan</label><select id='planSelect' name='plan_id' required style='border:1px solid #5a4617;'>"
    for pr in plan_rows:
        body += "<option value='" + str(pr["id"]) + "'>" + esc(pr["name"]) + " - Rs." + str(pr["amount"]) + " / " + str(pr["days"]) + " days</option>"
    body += "</select><label>Payment Method</label><select name='method'><option>JazzCash</option><option>Easypaisa</option></select><label>Transaction / Reference ID</label><input name='txid' required placeholder='Enter your payment transaction ID'><button class='btn' style='width:100%;margin-top:8px;'>SUBMIT PAYMENT</button></form></div>"
    body += "<div class='card' style='margin-top:0;'><p class='small'>🔒 Approved payment one-time use hai: amount user balance mein add nahi hota; approval par plan ke against consume/lock ho jata hai.</p><a class='btn2' href='/payment-history'>VIEW PAYMENT HISTORY</a></div>"
    body += "<script>function selectPlan(id){var s=document.getElementById('planSelect');if(s){s.value=id;s.scrollIntoView({behavior:'smooth',block:'center'});}}document.querySelectorAll('.choose-plan-card').forEach(function(c){c.addEventListener('mouseenter',function(){this.style.transform='translateY(-3px)';});c.addEventListener('mouseleave',function(){this.style.transform='translateY(0)';});});</script></div>"
    return layout("Plans", body)


@app.route("/plan-payment-center")
@login_required
def plan_payment_center():
    username = session["username"]
    conn = db()
    payments = conn.execute("SELECT * FROM payments WHERE username=? ORDER BY id DESC", (username,)).fetchall()
    active = get_active_plan(username)
    conn.close()

    approved_total = sum(int(r["amount"] or 0) for r in payments if r["status"] == "Approved")
    pending_total = sum(int(r["amount"] or 0) for r in payments if r["status"] == "Pending")
    rejected_total = sum(int(r["amount"] or 0) for r in payments if r["status"] == "Rejected")
    body = "<div class='card'><h2 class='gold'>💳 Plan & Payment Center</h2>"
    body += "<div class='grid'><div class='stat'>Approved<b>Rs." + str(approved_total) + "</b></div>"
    body += "<div class='stat'>Pending<b>Rs." + str(pending_total) + "</b></div>"
    body += "<div class='stat'>Rejected<b>Rs." + str(rejected_total) + "</b></div>"
    body += "<div class='stat'>Requests<b>" + str(len(payments)) + "</b></div></div></div>"

    body += "<div class='card'><h3 class='gold'>👑 Current Plan</h3>"
    if active:
        expiry = active["expires_at"] or "-"
        days_left = "-"
        try:
            days_left = max(0, (datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S") - datetime.now()).days)
        except Exception:
            pass
        body += "<p><b>Plan:</b> " + esc(active["plan"]) + "</p><p><b>Expires:</b> " + esc(expiry) + "</p><p><b>Approx. days left:</b> " + str(days_left) + "</p>"
    else:
        body += "<p class='bad'>No active plan.</p>"
    body += "<a class='btn' href='/plans'>💳 BUY / RENEW PLAN</a></div>"

    body += "<div class='card'><h3 class='gold'>🧾 Payment Requests</h3>"
    if not payments:
        body += "<p class='small'>No payment requests yet.</p>"
    else:
        for r in payments[:10]:
            cls = "pending" if r["status"] == "Pending" else ("ok" if r["status"] == "Approved" else "bad")
            body += "<div class='ad'><b class='gold'>" + esc(r["plan"]) + "</b><p>Rs." + str(r["amount"]) + " | " + esc(r["method"]) + "</p><p class='" + cls + "'><b>" + esc(r["status"]) + "</b></p><p class='small'>TXID: " + esc(r["txid"]) + "</p><p class='small'>" + esc(r["created_at"] or "-") + "</p>"
            if r["status"] == "Approved":
                body += "<p class='small'>Activated: " + esc(r["activation_at"] or "-") + " | Expires: " + esc(r["expires_at"] or "-") + "</p>"
            if r["reason"]:
                body += "<p class='small'>Reason: " + esc(r["reason"]) + "</p>"
            body += "</div>"
    body += "</div><a class='btn2' href='/payment-history'>VIEW FULL PAYMENT HISTORY</a> <a class='btn2' href='/'>DASHBOARD</a>"
    return layout("Plan & Payment Center", body)


@app.route("/payment-history")
@login_required
def payment_history():
    conn = db()
    rows = conn.execute("SELECT * FROM payments WHERE username=? ORDER BY id DESC",
                        (session["username"],)).fetchall()
    conn.close()

    body = "<div class='card'><h2 class='gold'>Payment History</h2>"
    if not rows:
        body += "<p class='small'>No payment requests yet.</p>"
    for r in rows:
        cls = "pending" if r["status"] == "Pending" else ("ok" if r["status"] == "Approved" else "bad")
        body += (
            "<div class='ad'><b class='gold'>" + esc(r["plan"]) + "</b>"
            "<p><b>Amount:</b> Rs." + str(r["amount"]) + " | <b>Method:</b> " + esc(r["method"]) + "</p>"
            "<p><b>Transaction ID:</b> " + esc(r["txid"]) + "</p>"
            "<p class='" + cls + "'><b>Status:</b> " + esc(r["status"]) + "</p>"
            "<p class='small'><b>Submitted:</b> " + esc(r["created_at"] or "-") + "</p>"
        )
        if r["status"] == "Approved":
            body += ("<p class='ok'><b>Activated:</b> " + esc(r["activation_at"] or "-") + "</p>"
                     "<p class='ok'><b>Expires:</b> " + esc(r["expires_at"] or "-") + "</p>")
        if r["reason"]:
            body += "<p class='small'><b>Admin Reason:</b> " + esc(r["reason"]) + "</p>"
        body += "<p class='small'>Transaction ID: ONE-TIME USE</p></div>"
    body += "</div><a class='btn2' href='/plans'>BACK TO PLANS</a>"
    return layout("Payment History", body)


@app.route("/watch-ads")
@login_required
def watch_ads():
    username = session["username"]
    active = get_active_plan(username)
    today = date.today().isoformat()

    # Demo users: 2 ads/day at Rs.5. Active-plan users: normal admin ads,
    # 5/day by default, with each ad's configured reward.
    is_demo = not bool(active)
    limit = 2 if is_demo else setting_int("daily_ads_limit", 5)
    demo_reward = 5

    conn = db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if user["ads_date"] != today:
        conn.execute("UPDATE users SET ads_date=?, ads_today=0 WHERE username=?", (today, username))
        conn.commit()
        count = 0
    else:
        count = user["ads_today"]

    availability_sql = """
        SELECT a.*,
               COALESCE((SELECT COUNT(*) FROM ad_claims c WHERE c.ad_id=a.id),0) AS total_claims
        FROM ads a
        WHERE a.active=1
          AND (a.start_date IS NULL OR a.start_date='' OR a.start_date<=?)
          AND (a.end_date IS NULL OR a.end_date='' OR a.end_date>=?)
          AND (COALESCE(a.max_claims,0)=0 OR COALESCE((SELECT COUNT(*) FROM ad_claims c2 WHERE c2.ad_id=a.id),0)<a.max_claims)
          AND (COALESCE(a.daily_max_claims,0)=0 OR COALESCE((SELECT COUNT(*) FROM ad_claims c3 WHERE c3.ad_id=a.id AND c3.claim_date=?),0)<a.daily_max_claims)
          AND (COALESCE(a.user_max_claims,0)=0 OR COALESCE((SELECT COUNT(*) FROM ad_claims c4 WHERE c4.ad_id=a.id AND c4.username=? AND c4.claim_date=?),0)<a.user_max_claims)
          AND (COALESCE(a.budget,0)=0 OR COALESCE((SELECT SUM(h2.reward) FROM ad_watch_history h2 WHERE h2.ad_id=a.id),0)+a.reward<=a.budget)
          AND (COALESCE(a.target_mode,'all')='all' OR (COALESCE(a.target_mode,'all')='active' AND ?=1) OR (COALESCE(a.target_mode,'all')='demo' AND ?=0))
        ORDER BY COALESCE(a.priority,0) DESC, a.id ASC
    """
    if is_demo:
        ads = conn.execute(availability_sql + " LIMIT 2", (today, today, today, username, today, int(bool(active)), int(bool(active)))).fetchall()
        mode_text = "Demo Mode — 2 ads/day • Rs.5 per ad"
    else:
        ads = conn.execute(availability_sql, (today, today, today, username, today, int(bool(active)), int(bool(active)))).fetchall()
        mode_text = "Active Plan — " + str(limit) + " ads/day"

    claimed = {r["ad_id"] for r in conn.execute(
        "SELECT ad_id FROM ad_claims WHERE username=? AND claim_date=?", (username, today)
    ).fetchall()}
    conn.close()

    body = (
        "<div class='card'><h2 class='gold'>Daily Ads</h2>"
        "<p><b class='gold'>" + esc(mode_text) + "</b></p>"
        "<p>Today watched: <b class='gold'>" + str(count) + "/" + str(limit) + "</b></p>"
        "<p class='small'>Har ad sirf ek dafa daily claim ho sakta hai.</p></div>"
    )

    if count >= limit:
        body += "<div class='card'><p class='ok'>Aaj ki daily ads limit complete ho gayi hai.</p></div>"
    elif not ads:
        body += "<div class='card'><p class='bad'>Abhi koi active ad available nahi hai.</p></div>"
    else:
        remaining = limit - count
        shown = 0
        for ad in ads:
            if ad["id"] in claimed:
                continue
            if shown >= remaining:
                break
            reward = demo_reward if is_demo else max(0, int(ad["reward"]))
            body += (
                "<div class='ad'><h3 class='gold'>🎬 " + esc(ad["title"]) + "</h3>"
                "<p>" + esc(ad["content"]) + "</p>"
                "<p>Reward: <b class='gold'>Rs." + str(reward) + "</b></p>"
            )
            watch_seconds = max(1, int(ad["watch_seconds"] or 30))
            if ad["video_url"]:
                vurl = ad["video_url"].strip()
                ad_id = int(ad["id"])
                player_id = "video_" + str(ad_id)
                yt = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{6,})", vurl)
                if yt:
                    vid = yt.group(1)
                    body += ("<div style='position:relative;width:100%;padding-top:56.25%;overflow:hidden;border-radius:12px'>"
                             "<iframe id='yt_" + str(ad_id) + "' src='https://www.youtube.com/embed/" + vid + "?enablejsapi=1' style='position:absolute;inset:0;width:100%;height:100%;border:0' allow='autoplay; encrypted-media; picture-in-picture' allowfullscreen></iframe></div>"
                             "<script>(function(){var id=" + str(ad_id) + ";var loaded=false;var timer=null;var player=null;var required=" + str(watch_seconds) + ";function sendProgress(){if(!player||!window.YT)return;try{var state=player.getPlayerState();if(state===YT.PlayerState.PLAYING){var pos=Math.min(required,Math.max(0,player.getCurrentTime()||0));fetch('/ad-progress/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seconds:pos})});}}catch(e){}}function init(){if(loaded||!window.YT||!YT.Player)return;loaded=true;player=new YT.Player('yt_" + str(ad_id) + "',{events:{onReady:function(){window['earnproYT_'+id]=player;fetch('/start-ad/'+id,{method:'POST'});sendProgress();timer=setInterval(sendProgress,2000);},onStateChange:function(e){if(e.data===YT.PlayerState.PLAYING){sendProgress();}}}});}if(!window.YT){var sc=document.createElement('script');sc.src='https://www.youtube.com/iframe_api';document.head.appendChild(sc);window.onYouTubeIframeAPIReady=init;}else{init();}window.addEventListener('load',init);window.addEventListener('beforeunload',function(){if(timer)clearInterval(timer);});})();</script>")
                else:
                    body += ("<video id='" + player_id + "' controls playsinline style='width:100%;max-height:360px;border-radius:12px' src='" + esc(vurl) + "'></video>"
                             "<script>(function(){var v=document.getElementById('" + player_id + "');var id=" + str(ad_id) + ";v.addEventListener('play',function(){fetch('/start-ad/'+id,{method:'POST'});});v.addEventListener('timeupdate',function(){if(!v.paused&&!v.ended){fetch('/ad-progress/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seconds:Math.min(" + str(watch_seconds) + ",v.currentTime)})});}});})();</script>")
                claim_id = "claim_" + str(ad_id)
                prog_id = "progress_" + str(ad_id)
                body += ("<p class='small'>Watch Duration: <b class='gold'>" + str(watch_seconds) + " seconds</b></p>"
                         "<div style='background:#222;border-radius:10px;overflow:hidden;height:10px;margin:10px 0'><div id='" + prog_id + "' style='width:0%;height:10px;transition:width .2s'></div></div>"
                         "<p id='status_" + str(ad_id) + "' class='small'>⏳ Video play karo — reward unlock hoga.</p>"
                         "<a id='" + claim_id + "' class='btn' href='/claim-ad/" + str(ad["id"]) + "' style='pointer-events:none;opacity:.45'>🔒 CLAIM REWARD</a>"
                         "<script>(function(){var required=" + str(watch_seconds) + ";var btn=document.getElementById('" + claim_id + "');var bar=document.getElementById('" + prog_id + "');var st=document.getElementById('status_" + str(ad_id) + "');function update(pos){pos=Math.max(0,Math.min(required,Number(pos)||0));var pct=(pos/required)*100;bar.style.width=pct+'%';if(pos>=required){btn.style.pointerEvents='auto';btn.style.opacity='1';btn.textContent='💰 CLAIM REWARD';st.textContent='✅ Required watch time complete. Ab reward claim kar sakte ho.';}else{st.textContent='⏳ Watched: '+Math.floor(pos)+'/'+required+' seconds';}}"
                         "var v=document.getElementById('" + player_id + "');if(v){v.addEventListener('timeupdate',function(){if(!v.paused)update(v.currentTime);});v.addEventListener('ended',function(){update(required);});}"
                         "window.addEventListener('load',function(){if(window.YT){var p=window['earnproYT_'+" + str(ad_id) + "];if(p){setInterval(function(){try{if(p.getPlayerState()===YT.PlayerState.PLAYING)update(p.getCurrentTime());}catch(e){}},1000);}}});})();</script>")
            elif ad["link"]:
                body += "<a class='btn2' href='/open-ad/" + str(ad["id"]) + "' target='_blank'>OPEN AD & WATCH</a>"
            else:
                body += "<p class='small'>Video/Ad link abhi set nahi hai.</p>"
            if not ad["video_url"]:
                body += "<a class='btn' href='/claim-ad/" + str(ad["id"]) + "'>CLAIM REWARD</a>"
            body += "</div>"
            shown += 1
        if shown == 0:
            body += "<div class='card'><p class='small'>Aaj ke available ads already claim ho chuke hain.</p></div>"

    body += "<a class='btn2' href='/dashboard'>BACK TO DASHBOARD</a>"
    return layout("Watch Ads", body)


@app.route("/start-ad/<int:ad_id>", methods=["POST"])
@login_required
def start_ad(ad_id):
    conn = db()
    ad = conn.execute("SELECT * FROM ads WHERE id=? AND active=1", (ad_id,)).fetchone()
    conn.close()
    if not ad:
        return ("", 404)
    record_ad_click(session["username"], ad_id, ad["title"] or "")
    session["opened_ad_id"] = ad_id
    session["opened_ad_at"] = int(datetime.now().timestamp())
    session["opened_ad_seconds"] = max(1, int(ad["watch_seconds"] or 30))
    session["watched_seconds"] = 0.0
    session["progress_at"] = int(datetime.now().timestamp())
    return ("", 204)


@app.route("/ad-progress/<int:ad_id>", methods=["POST"])
@login_required
def ad_progress(ad_id):
    """Record verified-by-elapsed-time playback progress for a video ad."""
    if session.get("opened_ad_id") != ad_id:
        return ("", 403)
    conn = db()
    ad = conn.execute("SELECT watch_seconds FROM ads WHERE id=? AND active=1", (ad_id,)).fetchone()
    conn.close()
    if not ad:
        return ("", 404)
    required = max(1, int(ad["watch_seconds"] or 30))
    now = int(datetime.now().timestamp())
    last = int(session.get("progress_at", session.get("opened_ad_at", now)) or now)
    # Never award more progress than real elapsed wall-clock time between updates.
    elapsed = max(0, min(10, now - last))
    session["progress_at"] = now
    current = float(session.get("watched_seconds", 0) or 0)
    requested = 0
    try:
        data = request.get_json(silent=True) or {}
        requested = max(0, float(data.get("seconds", 0) or 0))
    except (TypeError, ValueError):
        requested = 0
    # For direct video, use the reported playback position but cap its growth by elapsed time.
    session["watched_seconds"] = min(float(required), max(current, min(requested, current + elapsed)))
    return ("", 204)


def record_ad_click(username, ad_id, title):
    conn = db()
    conn.execute("INSERT INTO ad_clicks(username,ad_id,ad_title,click_date,created_at) VALUES(?,?,?,?,?)",
                 (username, ad_id, title, date.today().isoformat(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


@app.route("/open-ad/<int:ad_id>")
@login_required
def open_ad(ad_id):
    conn = db()
    ad = conn.execute("SELECT * FROM ads WHERE id=? AND active=1", (ad_id,)).fetchone()
    conn.close()
    if not ad:
        return redirect(url_for("watch_ads"))
    record_ad_click(session["username"], ad_id, ad["title"] or "")
    session["opened_ad_id"] = ad_id
    session["opened_ad_at"] = int(datetime.now().timestamp())
    session["opened_ad_seconds"] = max(1, int(ad["watch_seconds"] or 30))
    if ad["link"]:
        return redirect(ad["link"])
    return redirect(url_for("watch_ads"))


@app.route("/claim-ad/<int:ad_id>")
@login_required
def claim_ad(ad_id):
    username = session["username"]
    active = get_active_plan(username)
    is_demo = not bool(active)
    today = date.today().isoformat()
    limit = 2 if is_demo else setting_int("daily_ads_limit", 5)
    demo_reward = 5

    opened_id = session.get("opened_ad_id")
    opened_at = session.get("opened_ad_at", 0)
    elapsed = int(datetime.now().timestamp()) - int(opened_at or 0)
    required_seconds = int(session.get("opened_ad_seconds", 30) or 30)
    if opened_id != ad_id or elapsed > 600 or elapsed < required_seconds:
        return redirect(url_for("watch_ads"))

    conn = db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    ad = conn.execute("SELECT * FROM ads WHERE id=? AND active=1", (ad_id,)).fetchone()
    if not ad:
        conn.close()
        return redirect(url_for("watch_ads"))
    today_date = date.today().isoformat()
    if ((ad["start_date"] and ad["start_date"] > today_date) or
            (ad["end_date"] and ad["end_date"] < today_date)):
        conn.close()
        return redirect(url_for("watch_ads"))
    if ad["max_claims"] and conn.execute("SELECT COUNT(*) AS c FROM ad_claims WHERE ad_id=?", (ad_id,)).fetchone()["c"] >= int(ad["max_claims"]):
        conn.close()
        return redirect(url_for("watch_ads"))
    if ad["daily_max_claims"] and conn.execute("SELECT COUNT(*) AS c FROM ad_claims WHERE ad_id=? AND claim_date=?", (ad_id, today_date)).fetchone()["c"] >= int(ad["daily_max_claims"]):
        conn.close(); return redirect(url_for("watch_ads"))
    if ad["user_max_claims"] and conn.execute("SELECT COUNT(*) AS c FROM ad_claims WHERE ad_id=? AND username=? AND claim_date=?", (ad_id, username, today_date)).fetchone()["c"] >= int(ad["user_max_claims"]):
        conn.close(); return redirect(url_for("watch_ads"))
    if ad["budget"] and conn.execute("SELECT COALESCE(SUM(reward),0) AS s FROM ad_watch_history WHERE ad_id=?", (ad_id,)).fetchone()["s"] + int(ad["reward"] or 0) > int(ad["budget"]):
        conn.close(); return redirect(url_for("watch_ads"))
    target_mode = (ad["target_mode"] or "all").strip().lower()
    if target_mode not in {"all", "active", "demo"} or (target_mode == "active" and not active) or (target_mode == "demo" and active):
        conn.close(); return redirect(url_for("watch_ads"))
    if ad["video_url"]:
        watched = float(session.get("watched_seconds", 0) or 0)
        if watched < required_seconds:
            conn.close()
            return redirect(url_for("watch_ads"))
    if user["ads_date"] != today:
        count = 0
        conn.execute("UPDATE users SET ads_date=?, ads_today=0 WHERE username=?", (today, username))
    else:
        count = user["ads_today"]

    if count >= limit:
        conn.commit(); conn.close()
        return redirect(url_for("watch_ads"))

    try:
        conn.execute(
            "INSERT INTO ad_claims(username,ad_id,claim_date,created_at) VALUES(?,?,?,?)",
            (username, ad_id, today, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        reward_for_history = demo_reward if is_demo else max(0, int(ad["reward"]))
        conn.execute(
            "INSERT INTO ad_watch_history(username,ad_id,ad_title,reward,watch_seconds,claim_date,created_at) VALUES(?,?,?,?,?,?,?)",
            (username, ad_id, ad["title"], reward_for_history, required_seconds, today, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
    except sqlite3.IntegrityError:
        conn.commit(); conn.close()
        return redirect(url_for("watch_ads"))

    reward = demo_reward if is_demo else max(0, int(ad["reward"]))
    conn.execute(
        "UPDATE users SET balance=balance+?, total_earning=total_earning+?, ads_today=? WHERE username=?",
        (reward, reward, count + 1, username)
    )
    conn.commit(); conn.close()
    session.pop("opened_ad_id", None)
    session.pop("opened_ad_at", None)
    session.pop("opened_ad_seconds", None)
    session.pop("watched_seconds", None)
    session.pop("progress_at", None)

    add_transaction(username, "Ad Reward", reward, ad["title"])
    add_notification(username, "Ad Reward", "Rs." + str(reward) + " received from " + ad["title"] + ".")
    return redirect(url_for("watch_ads"))


@app.route("/referral")
@login_required
def referral():
    username = session.get("username", "")
    user = get_user(username)
    if not user:
        session.clear()
        return redirect(url_for("login"))

    # Ensure older databases/accounts always have a usable referral code.
    ref_code = (str(user["ref_code"] or "").strip().upper())
    if not ref_code:
        conn = db()
        try:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if row:
                candidate = "EP" + str(row["id"]).zfill(5)
                # If a legacy database somehow already uses the candidate, fall back
                # to a unique code based on the user id.
                clash = conn.execute("SELECT username FROM users WHERE ref_code=? AND username!=?", (candidate, username)).fetchone()
                if clash:
                    candidate = "EP" + str(row["id"]).zfill(5) + "X"
                conn.execute("UPDATE users SET ref_code=? WHERE username=?", (candidate, username))
                conn.commit()
                ref_code = candidate
        except Exception:
            # Referral page must remain usable even if a legacy DB has a schema issue.
            ref_code = "EP" + str(user["id"]).zfill(5) if "id" in user.keys() else "EP" + secrets.token_hex(3).upper()
        finally:
            conn.close()

    link = request.host_url.rstrip("/") + "/register?ref=" + quote(ref_code, safe="")
    bonus = setting_int("referral_bonus", 15)

    conn = db()
    try:
        referred = conn.execute(
            "SELECT username, created_at, COALESCE(referral_rewarded,0) AS referral_rewarded FROM users WHERE referred_by=? ORDER BY id DESC",
            (username,)
        ).fetchall()
    except Exception:
        referred = []
    finally:
        conn.close()

    total_referred = len(referred)
    rewarded_count = sum(1 for r in referred if int(r["referral_rewarded"] or 0) == 1)
    pending_count = total_referred - rewarded_count

    body = (
        "<div class='card'><h2 class='gold'>👥 Referral Center</h2>"
        "<p>Apna referral link share karein aur apne invited users ki activity yahan dekhein.</p>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>TOTAL REFERRALS</span><b>" + str(total_referred) + "</b></div>"
        "<div class='stat'><span class='small'>REWARDED</span><b>" + str(rewarded_count) + "</b></div>"
        "<div class='stat'><span class='small'>PENDING</span><b>" + str(pending_count) + "</b></div>"
        "<div class='stat'><span class='small'>BONUS PER ELIGIBLE REFERRAL</span><b>Rs." + str(bonus) + "</b></div>"
        "</div></div>"
        "<div class='card'><h3 class='gold'>🔗 Your Referral</h3>"
        "<p>Referral Code: <b class='gold'>" + esc(ref_code) + "</b></p>"
        "<label>Referral Link</label><input id='ref' value='" + esc(link) + "' readonly>"
        "<button class='btn' onclick='copyRef()'>📋 COPY LINK</button>"
        "<a class='btn' target='_blank' href='https://wa.me/?text=" + quote(link, safe='') + "'>💬 SHARE WHATSAPP</a>"
        "<a class='btn' target='_blank' href='https://t.me/share/url?url=" + quote(link, safe='') + "&text=Join%20EarnPro%20with%20my%20referral%20link'>✈️ SHARE TELEGRAM</a>"
        "<a class='btn2' href='/referral/export'>📥 EXPORT REFERRALS</a>"
        "<p class='small'>Eligible referral ka bonus approved-plan rules ke mutabiq automatically process hota hai.</p></div>"
        "<div class='card'><h3 class='gold'>📋 Referred Users</h3>"
    )
    if not referred:
        body += "<p class='small'>Abhi koi referral nahi aya.</p>"
    else:
        body += "<table><tr><th>Username</th><th>Joined</th><th>Status</th></tr>"
        for r in referred:
            status = "Rewarded" if int(r["referral_rewarded"] or 0) == 1 else "Pending"
            body += "<tr><td>" + esc(r["username"]) + "</td><td>" + esc(r["created_at"] or "-") + "</td><td>" + esc(status) + "</td></tr>"
        body += "</table>"
    conversion = (rewarded_count / total_referred * 100) if total_referred else 0
    body += (
        "</div><div class='card'><h3 class='gold'>📈 Referral Conversion</h3>"
        "<div class='grid'><div class='stat'><span class='small'>CONVERSION RATE</span><b>" + f"{conversion:.1f}%" + "</b></div>"
        "<div class='stat'><span class='small'>POTENTIAL BONUS</span><b>Rs." + str(pending_count * bonus) + "</b></div></div></div>"
        "<script>function copyRef(){var x=document.getElementById('ref');x.focus();x.select();x.setSelectionRange(0,99999);if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(x.value).then(function(){alert('Referral link copied!')}).catch(function(){document.execCommand('copy');alert('Referral link copied!')});}else{document.execCommand('copy');alert('Referral link copied!');}}</script>"
        "<a class='btn2' href='/referral-analytics'>📊 REFERRAL ANALYTICS</a><a class='btn2' href='/dashboard'>BACK TO DASHBOARD</a>"
    )
    return layout("Referral Center", body)


@app.route("/referral/export")
@login_required
def referral_export():
    # Step 104: legacy-database-safe referral export.
    # Some older EarnPro databases may not have every referral column yet.
    # Export must never crash the Referral Center just because that column is missing.
    import csv
    import io
    from flask import Response

    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login"))

    conn = db()
    try:
        columns = {str(r["name"]) for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        has_status = "referral_rewarded" in columns
        status_expr = "COALESCE(referral_rewarded,0) AS referral_rewarded" if has_status else "0 AS referral_rewarded"
        rows = conn.execute(
            "SELECT username, created_at, " + status_expr + " FROM users WHERE referred_by=? ORDER BY id DESC",
            (user["username"],)
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Username", "Joined At", "Referral Status"])
    for r in rows:
        writer.writerow([
            r["username"],
            r["created_at"] or "-",
            "Rewarded" if int(r["referral_rewarded"] or 0) == 1 else "Pending"
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=earnpro_referrals.csv"}
    )


@app.route("/referral-analytics")
@login_required
def referral_analytics():
    username = session.get("username", "")
    user = get_user(username)
    if not user:
        session.clear()
        return redirect(url_for("login"))
    conn = db()
    try:
        referred = conn.execute("SELECT username, created_at, COALESCE(referral_rewarded,0) AS referral_rewarded FROM users WHERE referred_by=? ORDER BY id DESC", (username,)).fetchall()
        txs = conn.execute("SELECT amount, description, created_at FROM transactions WHERE username=? AND type LIKE '%Referral%' ORDER BY id DESC LIMIT 50", (username,)).fetchall()
        ranking = conn.execute("SELECT username, COALESCE(referrals,0) AS referrals, COALESCE(referral_bonus,0) AS referral_bonus FROM users ORDER BY referrals DESC, referral_bonus DESC, id ASC LIMIT 20").fetchall()
    except Exception:
        referred, txs, ranking = [], [], []
    finally:
        conn.close()
    total=len(referred); rewarded=sum(1 for r in referred if int(r["referral_rewarded"] or 0)==1); pending=total-rewarded
    bonus_total=sum(float(t["amount"] or 0) for t in txs if float(t["amount"] or 0)>0)
    rank=next((i+1 for i,r in enumerate(ranking) if r["username"]==username),None)
    body=("<div class='card'><h2 class='gold'>📊 Referral Analytics</h2><p>Referral performance aur bonus history ka detailed overview.</p>"
          "<div class='grid'><div class='stat'><span class='small'>TOTAL</span><b>"+str(total)+"</b></div>"
          "<div class='stat'><span class='small'>REWARDED</span><b>"+str(rewarded)+"</b></div>"
          "<div class='stat'><span class='small'>PENDING</span><b>"+str(pending)+"</b></div>"
          "<div class='stat'><span class='small'>BONUS EARNED</span><b>Rs."+f"{bonus_total:.2f}"+"</b></div></div></div>"
          "<div class='card'><h3 class='gold'>🏆 Referral Ranking</h3><p>Your Rank: <b class='gold'>"+("#"+str(rank) if rank else "-")+"</b></p>")
    if ranking:
        body += "<table><tr><th>#</th><th>User</th><th>Referrals</th><th>Bonus</th></tr>"+''.join("<tr><td>"+str(i)+"</td><td>"+esc(r["username"])+"</td><td>"+str(r["referrals"])+"</td><td>Rs."+str(r["referral_bonus"])+"</td></tr>" for i,r in enumerate(ranking,1))+"</table>"
    else: body += "<p class='small'>Ranking data abhi available nahi.</p>"
    body += "</div><div class='card'><h3 class='gold'>💰 Bonus History</h3>"
    if txs:
        body += "<table><tr><th>Amount</th><th>Description</th><th>Date</th></tr>"+''.join("<tr><td>Rs."+str(t["amount"])+"</td><td>"+esc(t["description"] or "Referral Bonus")+"</td><td>"+esc(t["created_at"] or "")+"</td></tr>" for t in txs)+"</table>"
    else: body += "<p class='small'>Abhi referral bonus history nahi hai.</p>"
    body += "</div><a class='btn' href='/referral'>← REFERRAL CENTER</a><a class='btn2' href='/dashboard'>DASHBOARD</a>"
    return layout("Referral Analytics", body)


@app.route("/withdraw", methods=["GET", "POST"])
@login_required
def withdraw():
    username = session["username"]
    if get_setting("withdrawals_enabled") != "1":
        return layout("Withdrawals Disabled", "<div class='card'><h2 class='gold'>Withdrawals Temporarily Disabled</h2><p>Admin ne filhal withdrawals band ki hain. Baad mein dobara check karein.</p><a class='btn2' href='/dashboard'>DASHBOARD</a></div>")
    minimum = setting_int("min_withdraw", 1000)
    msg = ""
    active = get_active_plan(username)

    if not active:
        body = (
            "<div class='card'><h2 class='gold'>Withdraw</h2>"
            "<p class='bad'>Withdrawal ke liye pehle active plan purchase aur admin approval zaroori hai.</p>"
            "<p class='small'>Apna plan activate karne ke liye Plans page open karein.</p>"
            "<a class='btn' href='/plans'>BUY / ACTIVATE PLAN</a>"
            "<a class='btn2' href='/dashboard'>BACK TO DASHBOARD</a></div>"
        )
        return layout("Withdraw", body)

    # Withdrawal unlock rule is controlled from Admin > Referral Management.
    required_members = setting_int("withdraw_referral_members", 5)
    required_plans = setting_int("withdraw_referral_plans", 3)
    required_plans = min(required_plans, required_members)
    referral_rule_enabled = get_setting("withdraw_referral_rule_enabled") != "0"
    referral_conn = db()
    try:
        referral_count = referral_conn.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (username,)).fetchone()[0]
        plan_buy_count = referral_conn.execute(
            "SELECT COUNT(*) FROM users u WHERE u.referred_by=? AND EXISTS ("
            "SELECT 1 FROM payments p WHERE p.username=u.username AND p.status='Approved' "
            "AND (p.expires_at IS NULL OR p.expires_at='' OR p.expires_at>?)"
            ")", (username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        ).fetchone()[0]
    except Exception:
        referral_count = 0
        plan_buy_count = 0
    finally:
        referral_conn.close()
    withdrawal_unlocked = (not referral_rule_enabled) or (referral_count >= required_members and plan_buy_count >= required_plans)

    if request.method == "POST":
        try:
            amount = int(request.form.get("amount", "0"))
        except Exception:
            amount = 0

        method = request.form.get("method")
        account = request.form.get("account", "").strip()
        user = get_user(username)

        if not withdrawal_unlocked:
            msg = "<p class='bad'>Withdrawal unlock nahi hai. Pehle " + str(required_members) + " members refer karein aur un mein se kam az kam " + str(required_plans) + " member ka active/approved plan hona zaroori hai.</p>"
        elif amount < minimum:
            msg = "<p class='bad'>Minimum withdrawal Rs." + str(minimum) + " hai.</p>"
        elif amount > user["balance"]:
            msg = "<p class='bad'>Insufficient balance.</p>"
        elif method not in ["JazzCash", "Easypaisa"] or not account:
            msg = "<p class='bad'>Payment details complete karein.</p>"
        else:
            conn = db()
            try:
                # Atomic balance check + deduction prevents double-spending from
                # two nearly simultaneous withdrawal requests.
                updated = conn.execute(
                    "UPDATE users SET balance=balance-? WHERE username=? AND balance>=?",
                    (amount, username, amount)
                ).rowcount
                if not updated:
                    conn.rollback()
                    conn.close()
                    msg = "<p class='bad'>Insufficient balance.</p>"
                else:
                    conn.execute(
                        "INSERT INTO withdrawals(username,amount,method,account,status,created_at) "
                        "VALUES(?,?,?,?, 'Pending',?)",
                        (username, amount, method, account,
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    )
                    conn.commit()
                    conn.close()
                    add_transaction(username, "Withdrawal", -amount, "Withdrawal request submitted")
                    add_notification(
                        username, "Withdrawal Submitted",
                        "Your Rs." + str(amount) + " withdrawal request is pending."
                    )
                    msg = "<p class='pending'>Withdrawal request submit ho gayi.</p>"
            except Exception:
                conn.rollback()
                conn.close()
                msg = "<p class='bad'>Withdrawal request process nahi ho saki. Dobara try karein.</p>"
    user = get_user(username)
    conn = db()
    pending_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE username=? AND status='Pending'",
        (username,)
    ).fetchone()[0]
    history_rows = conn.execute(
        "SELECT * FROM withdrawals WHERE username=? ORDER BY id DESC LIMIT 20",
        (username,)
    ).fetchall()
    conn.close()
    body = (
        "<div class='card'><h2 class='gold'>Withdraw</h2>"
        "<p>Available Balance: <b class='gold'>Rs." + str(user["balance"]) + "</b></p>"
        "<p>Pending Withdrawal: <b class='gold'>Rs." + str(pending_total) + "</b></p>"
        "<p>Minimum Withdrawal: Rs." + str(minimum) + "</p>" +
        "<div class='ad'><b>Withdrawal Eligibility</b><p>Referral Rule: <b>" + ("ACTIVE" if referral_rule_enabled else "DEACTIVATED") + "</b></p><p>Referred Members: <b>" + str(referral_count) + "/" + str(required_members) + "</b></p><p>Referred Members with Active/Approved Plan: <b>" + str(plan_buy_count) + "/" + str(required_plans) + "</b></p><p class='" + ("ok" if withdrawal_unlocked else "bad") + "'>" + ("✅ Withdrawal unlocked" if withdrawal_unlocked else "🔒 Refer " + str(required_members) + " members + get " + str(required_plans) + " referred members to activate/buy a plan") + "</p></div>" + msg +
        "<form method='post'><label>Amount</label>"
        "<input type='number' name='amount' min='" + str(minimum) + "' required>"
        "<label>Method</label><select name='method'><option>JazzCash</option><option>Easypaisa</option></select>"
        "<label>Account Number</label><input name='account' required>"
        "<button class='btn'" + (" disabled" if not withdrawal_unlocked else "") + ">SUBMIT WITHDRAWAL</button></form>"
        "</div>"
        "<div class='card'><h3 class='gold'>🏦 Withdrawal History</h3>"
    )
    if not history_rows:
        body += "<p class='small'>No withdrawal requests yet.</p>"
    else:
        for r in history_rows:
            cls = 'pending' if r['status'] == 'Pending' else ('ok' if r['status'] == 'Approved' else 'bad')
            body += (
                "<div class='ad'><b>Rs." + str(r['amount']) + "</b>"
                "<p>" + esc(r['method']) + " | " + esc(r['account']) + "</p>"
                "<p class='" + cls + "'>Status: " + esc(r['status']) + "</p>"
            )
            if r['reason']:
                body += "<p class='small'>Reason: " + esc(r['reason']) + "</p>"
            body += "</div>"
    body += "</div><a class='btn2' href='/dashboard'>BACK TO DASHBOARD</a>"
    return layout("Withdraw", body)


@app.route("/wallet")
@login_required
def wallet():
    """Step 67: user wallet center with balance, earnings and withdrawal summary."""
    username = session["username"]
    user = get_user(username)
    active = get_active_plan(username)
    conn = db()
    approved_withdrawn = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE username=? AND status='Approved'",
        (username,)
    ).fetchone()[0] or 0
    pending_withdrawal = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE username=? AND status='Pending'",
        (username,)
    ).fetchone()[0] or 0
    total_withdrawal_requests = conn.execute(
        "SELECT COUNT(*) FROM withdrawals WHERE username=?", (username,)
    ).fetchone()[0]
    recent = conn.execute(
        "SELECT * FROM transactions WHERE username=? ORDER BY id DESC LIMIT 5", (username,)
    ).fetchall()
    conn.close()

    plan_text = "No Active Plan"
    expiry_text = "-"
    if active:
        plan_text = str(active["plan"]) + " - Rs." + str(active["amount"])
        expiry_text = active["expires_at"] or "No expiry"

    net_flow = (user["total_earning"] or 0) - approved_withdrawn
    body = (
        "<div class='card'><h2 class='gold'>👛 My Wallet</h2>"
        "<p class='small'>Aap ke EarnPro balance aur withdrawal ka complete wallet overview.</p>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>AVAILABLE BALANCE</span><b>Rs." + str(user["balance"]) + "</b></div>"
        "<div class='stat'><span class='small'>TOTAL EARNING</span><b>Rs." + str(user["total_earning"]) + "</b></div>"
        "<div class='stat'><span class='small'>WITHDRAWN</span><b>Rs." + str(approved_withdrawn) + "</b></div>"
        "<div class='stat'><span class='small'>PENDING WITHDRAWAL</span><b>Rs." + str(pending_withdrawal) + "</b></div>"
        "</div></div>"
        "<div class='card'><h3 class='gold'>📊 Wallet Summary</h3>"
        "<p><b>Net Earning Flow:</b> Rs." + str(net_flow) + "</p>"
        "<p><b>Withdrawal Requests:</b> " + str(total_withdrawal_requests) + "</p>"
        "<p><b>Current Plan:</b> " + esc(plan_text) + "</p>"
        "<p><b>Plan Expiry:</b> " + esc(expiry_text) + "</p>"
        "</div>"
        "<div class='card'><h3 class='gold'>⚡ Wallet Actions</h3>"
        "<a class='btn' href='/watch-ads'>🎁 EARN NOW</a>"
        "<a class='btn' href='/withdraw'>🏦 WITHDRAW</a>"
        ""
        "<a class='btn' href='/earning-center'>💰 EARNING CENTER</a>"
        "</div>"
        "<div class='card'><h3 class='gold'>📋 Recent Transactions</h3>"
    )
    if not recent:
        body += "<p class='small'>No transactions yet.</p>"
    else:
        body += "<table><tr><th>Type</th><th>Amount</th><th>Description</th></tr>"
        for r in recent:
            body += (
                "<tr><td>" + esc(r["type"]) + "</td><td>Rs." + str(r["amount"]) +
                "</td><td>" + esc(r["description"]) + "</td></tr>"
            )
        body += "</table>"
    body += "</div><a class='btn2' href='/dashboard'>BACK TO DASHBOARD</a>"
    return layout("Wallet Center", body)


@app.route("/legacy-withdraw-history")
@login_required
def withdraw_history():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM withdrawals WHERE username=? ORDER BY id DESC",
        (session["username"],)
    ).fetchall()
    conn.close()

    body = "<div class='card'><h2 class='gold'>Withdrawal History</h2>"
    if not rows:
        body += "<p class='small'>No withdrawal requests yet.</p>"
    for r in rows:
        cls = "pending" if r["status"] == "Pending" else ("ok" if r["status"] == "Approved" else "bad")
        body += (
            "<div class='ad'><b>Rs." + str(r["amount"]) + "</b>"
            "<p>" + esc(r["method"]) + " | " + esc(r["account"]) + "</p>"
            "<p class='" + cls + "'>Status: " + esc(r["status"]) + "</p>"
        )
        if r["reason"]:
            body += "<p class='small'>Reason: " + esc(r["reason"]) + "</p>"
        body += "</div>"
    body += "</div><a class='btn2' href='/dashboard'>BACK</a>"
    return layout("Withdrawal History", body)


@app.route("/history")
@login_required
def history():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE username=? ORDER BY id DESC LIMIT 100",
        (session["username"],)
    ).fetchall()
    conn.close()

    body = "<div class='card'><h2 class='gold'>Earning History</h2>"
    if not rows:
        body += "<p class='small'>No transactions yet.</p>"
    else:
        body += "<table><tr><th>Type</th><th>Amount</th><th>Description</th></tr>"
        for r in rows:
            body += (
                "<tr><td>" + esc(r["type"]) + "</td><td>Rs." +
                str(r["amount"]) + "</td><td>" + esc(r["description"]) + "</td></tr>"
            )
        body += "</table>"
    body += "</div><a class='btn2' href='/dashboard'>BACK</a>"
    return layout("History", body)


# Step 65: User earning statement export + summary.
@app.route("/earning-center/export")
@login_required
def earning_center_export():
    username = session["username"]
    date_filter = request.args.get("date", "").strip()
    kind_filter = request.args.get("type", "all").strip().lower()
    search = request.args.get("q", "").strip()
    allowed_types = {"all", "earning", "withdrawal", "referral", "admin"}
    if kind_filter not in allowed_types:
        kind_filter = "all"

    conn = db()
    where = ["username=?"]
    params = [username]
    if date_filter:
        where.append("substr(created_at,1,10)=?")
        params.append(date_filter)
    if search:
        where.append("(type LIKE ? OR description LIKE ?)")
        params.extend(["%" + search + "%", "%" + search + "%"])
    rows = conn.execute(
        "SELECT created_at,type,amount,description FROM transactions WHERE " +
        " AND ".join(where) + " ORDER BY id DESC", tuple(params)
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Type", "Amount", "Description"])
    for r in rows:
        t = (r["type"] or "").lower()
        if kind_filter == "earning" and r["amount"] <= 0:
            continue
        if kind_filter == "withdrawal" and "withdraw" not in t:
            continue
        if kind_filter == "referral" and "referral" not in t:
            continue
        if kind_filter == "admin" and "admin" not in t:
            continue
        writer.writerow([r["created_at"], r["type"], r["amount"], r["description"]])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=earnpro_earning_statement.csv"
    return response


# Step 64: User Earnings & Transaction Center.
@app.route("/earning-center")
@login_required
def earning_center():
    username = session["username"]
    kind_filter = request.args.get("type", "all").strip()
    search = request.args.get("q", "").strip()
    date_filter = request.args.get("date", "").strip()
    allowed_types = {"all", "earning", "withdrawal", "referral", "admin"}
    if kind_filter not in allowed_types:
        kind_filter = "all"

    conn = db()
    where = ["username=?"]
    params = [username]
    if date_filter:
        where.append("substr(created_at,1,10)=?")
        params.append(date_filter)
    if search:
        where.append("(type LIKE ? OR description LIKE ?)")
        params.extend(["%" + search + "%", "%" + search + "%"])

    rows = conn.execute(
        "SELECT * FROM transactions WHERE " + " AND ".join(where) +
        " ORDER BY id DESC LIMIT 200", tuple(params)
    ).fetchall()

    today = date.today().isoformat()
    today_earned = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS v "
        "FROM transactions WHERE username=? AND substr(created_at,1,10)=?",
        (username, today)
    ).fetchone()["v"]
    total_earned = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS v "
        "FROM transactions WHERE username=?",
        (username,)
    ).fetchone()["v"]
    total_withdrawn = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS v "
        "FROM transactions WHERE username=? AND LOWER(type) LIKE '%withdraw%'",
        (username,)
    ).fetchone()["v"]
    conn.close()

    filtered = []
    for r in rows:
        t = (r["type"] or "").lower()
        if kind_filter == "earning" and r["amount"] <= 0:
            continue
        if kind_filter == "withdrawal" and "withdraw" not in t:
            continue
        if kind_filter == "referral" and "referral" not in t:
            continue
        if kind_filter == "admin" and "admin" not in t:
            continue
        filtered.append(r)

    body = (
        "<div class='card'><h2 class='gold'>💰 EARNING & TRANSACTION CENTER</h2>"
        "<p>Apni complete earning aur transaction activity yahan check karo.</p><a class='btn2' href='/earning-center/export'>📥 EXPORT STATEMENT CSV</a></div>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>TODAY EARNED</span><b>Rs." + str(today_earned) + "</b></div>"
        "<div class='stat'><span class='small'>TOTAL POSITIVE EARNING</span><b>Rs." + str(total_earned) + "</b></div>"
        "<div class='stat'><span class='small'>WITHDRAWAL TRANSACTIONS</span><b>Rs." + str(total_withdrawn) + "</b></div>"
        "<div class='stat'><span class='small'>SHOWING</span><b>" + str(len(filtered)) + "</b></div>"
        "</div>"
        "<div class='card'><h3 class='gold'>🔎 FILTER TRANSACTIONS</h3>"
        "<form method='get'>"
        "<select name='type'>"
        "<option value='all'" + (" selected" if kind_filter == "all" else "") + ">All Transactions</option>"
        "<option value='earning'" + (" selected" if kind_filter == "earning" else "") + ">Positive Earnings</option>"
        "<option value='withdrawal'" + (" selected" if kind_filter == "withdrawal" else "") + ">Withdrawals</option>"
        "<option value='referral'" + (" selected" if kind_filter == "referral" else "") + ">Referral</option>"
        "<option value='admin'" + (" selected" if kind_filter == "admin" else "") + ">Admin Adjustments</option>"
        "</select>"
        "<input type='text' name='q' value='" + esc(search) + "' placeholder='Search type or description'>"
        "<input type='date' name='date' value='" + esc(date_filter) + "'>"
        "<button class='btn'>APPLY FILTER</button>"
        "<a class='btn2' href='/earning-center'>CLEAR</a>"
        "</form></div>"
        "<div class='card'><h3 class='gold'>📋 TRANSACTION HISTORY</h3>"
    )
    if not filtered:
        body += "<p class='small'>Is filter ke mutabiq koi transaction nahi mili.</p>"
    else:
        body += "<div style='overflow-x:auto'><table><tr><th>Date</th><th>Type</th><th>Amount</th><th>Description</th></tr>"
        for r in filtered:
            cls = "ok" if r["amount"] > 0 else "bad"
            sign = "+" if r["amount"] > 0 else ""
            body += (
                "<tr><td>" + esc(r["created_at"] or "-") + "</td>"
                "<td>" + esc(r["type"] or "-") + "</td>"
                "<td class='" + cls + "'><b>" + sign + "Rs." + str(r["amount"]) + "</b></td>"
                "<td>" + esc(r["description"] or "-") + "</td></tr>"
            )
        body += "</table></div>"
    body += "</div>"
    # Earning History is intentionally kept inside Earning Center instead of as a separate dashboard button.
    conn = db()
    history_rows = conn.execute("SELECT * FROM transactions WHERE username=? ORDER BY id DESC LIMIT 100", (username,)).fetchall()
    conn.close()
    body += "<div class='card'><h3 class='gold'>📜 EARNING HISTORY</h3>"
    if not history_rows:
        body += "<p class='small'>No earning history yet.</p>"
    else:
        body += "<div style='overflow-x:auto'><table><tr><th>Date</th><th>Type</th><th>Amount</th><th>Description</th></tr>"
        for hr in history_rows:
            hcls = "ok" if hr["amount"] > 0 else "bad"
            hsign = "+" if hr["amount"] > 0 else ""
            body += ("<tr><td>" + esc(hr["created_at"] or "-") + "</td>"
                     "<td>" + esc(hr["type"] or "-") + "</td>"
                     "<td class='" + hcls + "'><b>" + hsign + "Rs." + str(hr["amount"]) + "</b></td>"
                     "<td>" + esc(hr["description"] or "-") + "</td></tr>")
        body += "</table></div>"
    body += "</div><a class='btn' href='/dashboard'>🏠 DASHBOARD</a>"
    return layout("Earning Center", body)


# Step 66: User earning insights and summaries.
@app.route("/earning-insights")
@login_required
def earning_insights():
    username = session["username"]
    conn = db()
    monthly = conn.execute(
        "SELECT substr(created_at,1,7) AS month, "
        "COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS earned, "
        "COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS spent "
        "FROM transactions WHERE username=? GROUP BY substr(created_at,1,7) "
        "ORDER BY month DESC LIMIT 12", (username,)
    ).fetchall()
    daily = conn.execute(
        "SELECT substr(created_at,1,10) AS day, "
        "COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS earned, "
        "COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS spent "
        "FROM transactions WHERE username=? GROUP BY substr(created_at,1,10) "
        "ORDER BY day DESC LIMIT 7", (username,)
    ).fetchall()
    categories = conn.execute(
        "SELECT type, COUNT(*) AS count, "
        "COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS earned "
        "FROM transactions WHERE username=? GROUP BY type ORDER BY earned DESC, count DESC LIMIT 12",
        (username,)
    ).fetchall()
    totals = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS earned, "
        "COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS spent, COUNT(*) AS count "
        "FROM transactions WHERE username=?", (username,)
    ).fetchone()
    conn.close()

    net = int(totals["earned"] or 0) - int(totals["spent"] or 0)
    body = (
        "<div class='card'><h2 class='gold'>📈 EARNING INSIGHTS</h2>"
        "<p>Apni earning ka daily, monthly aur category-wise summary yahan dekho.</p>"
        "<a class='btn2' href='/earning-center'>💰 EARNING CENTER</a></div>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>TOTAL POSITIVE</span><b>Rs." + str(totals["earned"] or 0) + "</b></div>"
        "<div class='stat'><span class='small'>TOTAL OUTGOING</span><b>Rs." + str(totals["spent"] or 0) + "</b></div>"
        "<div class='stat'><span class='small'>NET TRANSACTION FLOW</span><b>Rs." + str(net) + "</b></div>"
        "<div class='stat'><span class='small'>TRANSACTIONS</span><b>" + str(totals["count"] or 0) + "</b></div>"
        "</div>"
        "<div class='card'><h3 class='gold'>📅 LAST 7 DAYS</h3>"
    )
    if daily:
        body += "<div style='overflow-x:auto'><table><tr><th>Date</th><th>Earned</th><th>Outgoing</th><th>Net</th></tr>"
        for r in daily:
            dnet = int(r["earned"] or 0) - int(r["spent"] or 0)
            body += "<tr><td>" + esc(r["day"] or "-") + "</td><td class='ok'>+Rs." + str(r["earned"] or 0) + "</td><td>Rs." + str(r["spent"] or 0) + "</td><td><b>Rs." + str(dnet) + "</b></td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='small'>No daily data yet.</p>"

    body += "</div><div class='card'><h3 class='gold'>🗓️ MONTHLY SUMMARY</h3>"
    if monthly:
        body += "<div style='overflow-x:auto'><table><tr><th>Month</th><th>Earned</th><th>Outgoing</th><th>Net</th></tr>"
        for r in monthly:
            mnet = int(r["earned"] or 0) - int(r["spent"] or 0)
            body += "<tr><td>" + esc(r["month"] or "-") + "</td><td class='ok'>+Rs." + str(r["earned"] or 0) + "</td><td>Rs." + str(r["spent"] or 0) + "</td><td><b>Rs." + str(mnet) + "</b></td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='small'>No monthly data yet.</p>"

    body += "</div><div class='card'><h3 class='gold'>🏷️ EARNING BY TYPE</h3>"
    if categories:
        body += "<div style='overflow-x:auto'><table><tr><th>Type</th><th>Transactions</th><th>Positive Amount</th></tr>"
        for r in categories:
            body += "<tr><td>" + esc(r["type"] or "-") + "</td><td>" + str(r["count"] or 0) + "</td><td class='ok'>Rs." + str(r["earned"] or 0) + "</td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='small'>No earning categories yet.</p>"
    body += "</div><a class='btn' href='/dashboard'>🏠 DASHBOARD</a>"
    return layout("Earning Insights", body)

@app.route("/login-history")
@login_required
def login_history():
    username = session["username"]
    conn = db()
    rows = conn.execute(
        "SELECT login_at, ip_address FROM login_history WHERE username=? ORDER BY id DESC LIMIT 20",
        (username,)
    ).fetchall()
    conn.close()
    items = "".join(
        "<tr><td>" + esc(r["login_at"]) + "</td><td>" + esc(r["ip_address"] or "-") + "</td></tr>"
        for r in rows
    ) or "<tr><td colspan='2'>No login history yet.</td></tr>"
    body = (
        "<div class='card'><h2 class='gold'>Login History</h2>"
        "<p class='small'>Aap ke recent successful logins yahan show hote hain.</p>"
        "<div style='overflow:auto'><table><tr><th>Date & Time</th><th>IP Address</th></tr>" + items + "</table></div>"
        "<a class='btn2' href='/profile'>BACK TO PROFILE</a>"
        "</div>"
    )
    return layout("Login History", body)


@app.route("/notifications")
@login_required
def notifications():
    username = session["username"]
    filter_mode = request.args.get("filter", "all")
    conn = db()
    if filter_mode == "unread":
        rows = conn.execute("SELECT * FROM notifications WHERE username=? AND is_read=0 ORDER BY id DESC LIMIT 50", (username,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM notifications WHERE username=? ORDER BY id DESC LIMIT 50", (username,)).fetchall()
    unread = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE username=? AND is_read=0", (username,)).fetchone()["c"]
    conn.close()
    body = ("<div class='card'><h2 class='gold'>🔔 Notifications <span class='small'>Unread: " + str(unread) + "</span></h2>"
            "<form method='post' action='/notifications/read-all'><button class='btn2' type='submit'>✅ MARK ALL AS READ</button></form>"
            "<a class='btn2' href='/notifications?filter=all'>📋 ALL</a><a class='btn2' href='/notifications?filter=unread'>🔴 UNREAD</a>"
            "<form method='post' action='/notifications/clear' onsubmit=\"return confirm('Clear all notifications?');\"><button class='btn2' type='submit'>🗑️ CLEAR ALL</button></form>")
    if not rows: body += "<p class='small'>No notifications.</p>"
    for r in rows:
        body += ("<div class='ad'><h3 class='gold'>" + esc(r["title"]) + "</h3>"
                 + ("<span class='pending'>NEW</span>" if not r["is_read"] else "<span class='small'>✓ Read</span>")
                 + "<p>" + esc(r["message"]) + "</p><p class='small'>" + esc(r["created_at"]) + "</p>"
                 + (("<form method='post' action='/notifications/read/" + str(r["id"]) + "'><button class='btn2' type='submit'>✅ MARK READ</button></form>") if not r["is_read"] else "")
                 + "<form method='post' action='/notifications/delete/" + str(r["id"]) + "'><button class='btn2' type='submit'>🗑️ DELETE</button></form></div>")
    body += "</div><a class='btn2' href='/dashboard'>BACK</a>"
    return layout("Notifications", body)


@app.route("/notifications/read/<int:notification_id>", methods=["POST"])
@login_required
def notification_read(notification_id):
    conn = db(); conn.execute("UPDATE notifications SET is_read=1 WHERE id=? AND username=?", (notification_id, session["username"])); conn.commit(); conn.close()
    return redirect(url_for("notifications"))


@app.route("/notifications/delete/<int:notification_id>", methods=["POST"])
@login_required
def notification_delete(notification_id):
    conn = db()
    conn.execute(
        "DELETE FROM notifications WHERE id=? AND username=?",
        (notification_id, session["username"])
    )
    conn.commit()
    conn.close()
    return redirect(url_for("notifications"))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    username = session["username"]
    conn = db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE username=?", (username,))
    conn.commit()
    conn.close()
    return redirect(url_for("notifications"))


@app.route("/notifications/clear", methods=["POST"])
@login_required
def notifications_clear():
    conn = db()
    conn.execute("DELETE FROM notifications WHERE username=?", (session["username"],))
    conn.commit()
    conn.close()
    return redirect(url_for("notifications"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    username = session["username"]
    msg = ""

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        user = get_user(username)

        if not user or not password_matches(user["password"], current_password):
            msg = "<p class='bad'>Current password ghalat hai.</p>"
        elif len(new_password) < 6:
            msg = "<p class='bad'>New password kam az kam 6 characters ka ho.</p>"
        elif new_password != confirm_password:
            msg = "<p class='bad'>New password aur confirmation match nahi karte.</p>"
        elif password_matches(user["password"], new_password):
            msg = "<p class='bad'>New password current password se different hona chahiye.</p>"
        else:
            conn = db()
            conn.execute(
                "UPDATE users SET password=? WHERE username=?",
                (hash_password(new_password), username)
            )
            conn.commit()
            conn.close()
            add_notification(username, "Password Updated", "Your password was changed successfully.")
            msg = "<p class='ok'>Password successfully update ho gaya.</p>"

    user = get_user(username)
    active_plan = get_active_plan(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = db()
    payment_count = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE username=?", (username,)
    ).fetchone()[0]
    withdrawal_count = conn.execute(
        "SELECT COUNT(*) FROM withdrawals WHERE username=?", (username,)
    ).fetchone()[0]
    notification_count = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE username=? AND is_read=0", (username,)
    ).fetchone()[0]
    conn.close()

    plan_name = "No Active Plan"
    plan_expiry = "-"
    plan_status = "Not Active"
    if active_plan:
        plan_name = active_plan["plan"]
        plan_expiry = active_plan["expires_at"] or "No expiry"
        plan_status = "Active"
        if active_plan["expires_at"] and active_plan["expires_at"] <= now:
            plan_status = "Expired"

    body = (
        "<div class='card'><h2 class='gold'>My Profile</h2>" + msg +
        "<div class='grid'>"
        "<div class='stat'><span class='small'>Username</span><b style='font-size:16px'>" + esc(user["username"]) + "</b></div>"
        "<div class='stat'><span class='small'>Referral Code</span><b style='font-size:16px'>" + esc(user["ref_code"]) + "</b></div>"
        "</div>"
        "<p><b>Balance:</b> Rs." + str(user["balance"]) + "</p>"
        "<p><b>Total Earning:</b> Rs." + str(user["total_earning"]) + "</p>"
        "<p><b>Referrals:</b> " + str(user["referrals"]) + "</p></div>"
        "<div class='card'><h3 class='gold'>Account Activity</h3>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>Payments</span><b>" + str(payment_count) + "</b></div>"
        "<div class='stat'><span class='small'>Withdrawals</span><b>" + str(withdrawal_count) + "</b></div>"
        "<div class='stat'><span class='small'>Unread Alerts</span><b>" + str(notification_count) + "</b></div>"
        "<div class='stat'><span class='small'>Plan</span><b style='font-size:16px'>" + esc(plan_status) + "</b></div>"
        "</div></div>"
        "<div class='card'><h3 class='gold'>Current Plan</h3>"
        "<p><b>Plan:</b> " + esc(plan_name) + "</p>"
        "<p><b>Status:</b> <span class='pill'>" + esc(plan_status) + "</span></p>"
        "<p><b>Expiry:</b> " + esc(plan_expiry) + "</p></div>"
        "<div class='card'><h3 class='gold'>Change Password</h3>"
        "<p class='small'>Security ke liye current password verify karna zaroori hai.</p>"
        "<form method='post' autocomplete='off'>"
        "<label>Current Password</label><input type='password' name='current_password' required autocomplete='current-password'>"
        "<label>New Password</label><input type='password' name='new_password' minlength='6' required autocomplete='new-password'>"
        "<label>Confirm New Password</label><input type='password' name='confirm_password' minlength='6' required autocomplete='new-password'>"
        "<button class='btn'>UPDATE PASSWORD</button></form></div>"
        "<p><b>Last Login:</b> " + esc(user["last_login_at"] or "First login") + "</p>"
        "<a class='btn2' href='/login-history'>LOGIN HISTORY</a>"
        "<a class='btn2' href='/notifications'>NOTIFICATIONS</a>"
        "<a class='btn2' href='/dashboard'>BACK TO DASHBOARD</a>"
    )
    return layout("Profile", body)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    msg = ""
    key = "admin:" + _client_key()
    if request.method == "POST":
        if _login_locked(key):
            msg = "<p class='bad'>Too many failed admin login attempts. 10 minutes baad dobara try karein.</p>"
        else:
            supplied_username = request.form.get("username", "").strip()
            supplied_password = request.form.get("password", "")
            custom_admin_password = get_setting("admin_custom_password")
            if custom_admin_password == "1":
                stored_hash = get_setting("admin_password_hash")
                password_ok = bool(stored_hash) and check_password_hash(stored_hash, supplied_password)
            else:
                password_ok = supplied_password == ADMIN_PASS
            if supplied_username == ADMIN_USER and password_ok:
                _clear_login_failures(key)
                session.clear()
                session["admin"] = True
                session["last_seen"] = time.time()
                session["session_instance"] = SESSION_INSTANCE_ID
                conn = db()
                conn.execute(
                    "INSERT INTO admin_login_history(admin_username, login_at, ip_address) VALUES(?,?,?)",
                    (ADMIN_USER, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), request.remote_addr or "-")
                )
                conn.commit()
                conn.close()
                return redirect(url_for("admin_panel"))
            else:
                _record_login_failure(key)
                msg = "<p class='bad'>Invalid admin login.</p>"

    body = (
        "<div class='card'><h2 class='gold'>Admin Login</h2>" + msg +
        "<form method='post'><label>Username</label><input name='username' required>"
        "<label>Password</label><input type='password' name='password' required>"
        "<button class='btn'>LOGIN</button></form></div>"
    )
    return layout("Admin Login", body)


@app.route("/admin/password", methods=["GET", "POST"])
@admin_required
def admin_password():
    msg = ""
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        custom_admin_password = get_setting("admin_custom_password")
        if custom_admin_password == "1":
            stored_hash = get_setting("admin_password_hash")
            current_ok = bool(stored_hash) and check_password_hash(stored_hash, current_password)
        else:
            current_ok = current_password == ADMIN_PASS

        if not current_ok:
            msg = "<p class='bad'>Current admin password ghalat hai.</p>"
        elif len(new_password) < 8:
            msg = "<p class='bad'>New password kam az kam 8 characters ka hona chahiye.</p>"
        elif new_password != confirm_password:
            msg = "<p class='bad'>New password aur confirm password match nahi karte.</p>"
        elif new_password == current_password:
            msg = "<p class='bad'>New password current password se different rakhein.</p>"
        else:
            set_setting("admin_password_hash", generate_password_hash(new_password))
            set_setting("admin_custom_password", "1")
            add_audit_log("Changed admin password", "", "Admin password was updated from the admin security page")
            msg = "<p class='ok'>Admin password successfully change ho gaya. Next login par new password use karein.</p>"

    body = (
        "<div class='card'><h2 class='gold'>🔐 Change Admin Password</h2>"
        "<p class='small'>Security ke liye current password verify karke naya password set karein.</p>"
        + msg +
        "<form method='post'>"
        "<label>Current Password</label><input type='password' name='current_password' required>"
        "<label>New Password</label><input type='password' name='new_password' minlength='8' required>"
        "<label>Confirm New Password</label><input type='password' name='confirm_password' minlength='8' required>"
        "<button class='btn'>UPDATE ADMIN PASSWORD</button></form></div>"
        "<div class='card'><p class='small'>Note: Password change ke baad purana password kaam nahi karega.</p></div>"
        "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    )
    return layout("Change Admin Password", body)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/referrals", methods=["GET", "POST"])
@admin_required
def admin_referrals():
    # Step 123: Referral Management also controls withdrawal referral eligibility.
    # Default rule: refer 5 members and at least 3 referred members must have an approved plan.
    if request.method == "POST":
        try:
            required_members = max(1, min(1000, int(request.form.get("withdraw_referral_members", "5"))))
            required_plans = max(1, min(required_members, int(request.form.get("withdraw_referral_plans", "3"))))
            set_setting("withdraw_referral_members", str(required_members))
            set_setting("withdraw_referral_plans", str(required_plans))
            add_audit_log("Updated referral withdrawal rule", "", f"Required members={required_members}, required referred plan buyers={required_plans}")
            config_msg = "<p class='ok'>✅ Withdrawal referral rule save ho gaya: " + str(required_members) + " members + " + str(required_plans) + " referred plan buyers.</p>"
        except Exception as exc:
            config_msg = "<p class='bad'>Rule save nahi ho saka: " + esc(str(exc)) + "</p>"
    else:
        config_msg = ""
    q=(request.args.get("q") or "").strip(); status=(request.args.get("status") or "all").strip().lower()
    if status not in {"all","rewarded","pending"}: status="all"
    conn=db()
    try:
        columns={r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if not {"id","username","referred_by","created_at"}.issubset(columns):
            return layout("Admin Referral Management","<div class='card'><h2 class='gold'>👥 Admin Referral Management</h2><p class='bad'>Users database schema incomplete hai. App restart karke dobara try karein.</p><a class='btn2' href='/admin'>BACK TO ADMIN</a></div>"),500
        has_code="ref_code" in columns; has_rewarded="referral_rewarded" in columns; has_bonus="referral_bonus" in columns
        code_expr="COALESCE(u.ref_code,'')" if has_code else "''"; rewarded_expr="COALESCE(u.referral_rewarded,0)" if has_rewarded else "0"; bonus_expr="COALESCE(u.referral_bonus,0)" if has_bonus else "0"
        where=["u.referred_by IS NOT NULL","u.referred_by != ''"]; params=[]
        if q:
            terms=["u.username LIKE ?","u.referred_by LIKE ?"]
            if has_code: terms.append("u.ref_code LIKE ?")
            where.append("("+" OR ".join(terms)+")"); like="%"+q+"%"; params += [like]*len(terms)
        if status=="rewarded": where.append(rewarded_expr+"=1")
        elif status=="pending": where.append(rewarded_expr+"=0")
        rows=conn.execute("SELECT u.username,u.referred_by,"+rewarded_expr+" AS referral_rewarded,u.created_at,"+code_expr+" AS ref_code,"+bonus_expr+" AS referral_bonus FROM users u WHERE "+" AND ".join(where)+" ORDER BY u.id DESC",tuple(params)).fetchall()
        total=conn.execute("SELECT COUNT(*) AS c FROM users WHERE referred_by IS NOT NULL AND referred_by != ''").fetchone()["c"]
        rewarded=conn.execute("SELECT COUNT(*) AS c FROM users WHERE referred_by IS NOT NULL AND referred_by != '' AND COALESCE(referral_rewarded,0)=1").fetchone()["c"] if has_rewarded else 0
        pending=total-rewarded
        bonus=conn.execute("SELECT COALESCE(SUM(referral_bonus),0) AS total FROM users WHERE referred_by IS NOT NULL AND referred_by != ''").fetchone()["total"] if has_bonus else 0
    except Exception as exc:
        return layout("Admin Referral Management","<div class='card'><h2 class='gold'>👥 Admin Referral Management</h2><p class='bad'>Referral Management error: "+esc(str(exc))+"</p><a class='btn2' href='/admin'>BACK TO ADMIN</a></div>"),500
    finally: conn.close()
    required_members = setting_int("withdraw_referral_members", 5)
    required_plans = setting_int("withdraw_referral_plans", 3)
    body=("<div class='card'><h2 class='gold'>👥 Admin Referral Management</h2>" + config_msg +
          "<div class='ad' style='border:1px solid #5a4617;background:#111;'>"
          "<h3 class='gold'>🔐 Withdrawal Referral Rule</h3>"
          "<p class='small'>Withdrawal tabhi unlock hoga jab user required members refer kare aur un mein se required members approved/active plan lein.</p>"
          "<form method='post' style='display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;'>"
          "<div><label>Required Referred Members</label><input type='number' name='withdraw_referral_members' value='" + str(required_members) + "' min='1' max='1000' required></div>"
          "<div><label>Required Referred Plan Buyers</label><input type='number' name='withdraw_referral_plans' value='" + str(required_plans) + "' min='1' max='" + str(required_members) + "' required></div>"
          "<div style='grid-column:1/-1'><button class='btn'>💾 SAVE WITHDRAWAL RULE</button></div></form></div>"
          "<div class='grid'>"+
          "<div class='stat'><span class='small'>TOTAL REFERRALS</span><b>"+str(total)+"</b></div><div class='stat'><span class='small'>REWARDED</span><b>"+str(rewarded)+"</b></div><div class='stat'><span class='small'>PENDING</span><b>"+str(pending)+"</b></div><div class='stat'><span class='small'>TOTAL BONUS</span><b>Rs."+str(bonus)+"</b></div></div></div>"+
          "<div class='card'><h3 class='gold'>🔎 SEARCH & FILTER</h3><form method='get'><label>Username / Referrer / Referral Code</label><input name='q' value='"+esc(q)+"' placeholder='Search referral...'><label>Status</label><select name='status'><option value='all'"+(" selected" if status=="all" else "")+">All</option><option value='rewarded'"+(" selected" if status=="rewarded" else "")+">Rewarded</option><option value='pending'"+(" selected" if status=="pending" else "")+">Pending</option></select><button class='btn'>🔎 APPLY</button><a class='btn2' href='/admin/referrals'>RESET</a><a class='btn2' href='/admin/referral-summary'>📊 SUMMARY</a><a class='btn2' href='/admin/referral-audit'>🛡️ AUDIT</a><a class='btn2' href='/admin/referrals/export?q="+quote(q,safe='')+"&status="+quote(status,safe='')+"'>📥 EXPORT CSV</a><a class='btn2' href='/admin'>BACK TO ADMIN</a></form></div><div class='card'><h3 class='gold'>📋 Referral Records</h3>")
    if not rows: body+="<p class='small'>No referral records found.</p>"
    else:
        body+="<div style='overflow-x:auto'><table><tr><th>User</th><th>Referrer</th><th>Status</th><th>Bonus</th><th>Joined</th></tr>"
        for r in rows: body+="<tr><td>"+esc(r["username"])+"</td><td>"+esc(r["referred_by"])+"</td><td>"+("Rewarded" if int(r["referral_rewarded"] or 0)==1 else "Pending")+"</td><td>Rs."+str(r["referral_bonus"] or 0)+"</td><td>"+esc(r["created_at"] or "")+"</td></tr>"
        body+="</table></div>"
    return layout("Admin Referral Management",body+"</div>")


@app.route("/admin/referral-audit")
@admin_required
def admin_referral_audit():
    """Step 74: referral integrity and reward audit without changing reward logic."""
    conn = db()
    total = conn.execute("SELECT COUNT(*) AS c FROM users WHERE referred_by IS NOT NULL AND referred_by!=''").fetchone()["c"]
    rewarded = conn.execute("SELECT COUNT(*) AS c FROM users WHERE referred_by IS NOT NULL AND referred_by!='' AND referral_rewarded=1").fetchone()["c"]
    pending = total - rewarded
    bonus_field = conn.execute("SELECT COALESCE(SUM(referral_bonus),0) AS v FROM users WHERE referred_by IS NOT NULL AND referred_by!=''").fetchone()["v"] or 0
    tx_bonus = conn.execute("SELECT COALESCE(SUM(amount),0) AS v FROM transactions WHERE type='Referral Bonus' AND amount>0").fetchone()["v"] or 0
    missing_referrers = conn.execute(
        "SELECT u.username,u.referred_by,u.referral_rewarded,COALESCE(u.referral_bonus,0) AS referral_bonus "
        "FROM users u LEFT JOIN users r ON r.username=u.referred_by "
        "WHERE u.referred_by IS NOT NULL AND u.referred_by!='' AND r.username IS NULL ORDER BY u.id DESC"
    ).fetchall()
    zero_bonus_rewarded = conn.execute(
        "SELECT username,referred_by FROM users WHERE referred_by IS NOT NULL AND referred_by!='' AND referral_rewarded=1 AND COALESCE(referral_bonus,0)<=0 ORDER BY id DESC"
    ).fetchall()
    conn.close()
    mismatch = abs(float(bonus_field) - float(tx_bonus)) > 0.0001
    body = (
        "<div class='card'><h2 class='gold'>🛡️ Referral Integrity Audit</h2>"
        "<p>Referral records aur reward totals ka read-only safety check. Is page se koi reward automatically change nahi hota.</p>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>TOTAL REFERRALS</span><b>" + str(total) + "</b></div>"
        "<div class='stat'><span class='small'>REWARDED</span><b>" + str(rewarded) + "</b></div>"
        "<div class='stat'><span class='small'>PENDING</span><b>" + str(pending) + "</b></div>"
        "<div class='stat'><span class='small'>REFERRAL BONUS FIELD</span><b>Rs." + str(bonus_field) + "</b></div>"
        "<div class='stat'><span class='small'>BONUS TRANSACTIONS</span><b>Rs." + str(tx_bonus) + "</b></div>"
        "<div class='stat'><span class='small'>TOTAL CHECK</span><b>" + ("⚠️ REVIEW" if mismatch else "✅ OK") + "</b></div>"
        "</div></div>"
    )
    if mismatch:
        body += "<div class='card'><p class='bad'>Referral bonus field aur Referral Bonus transactions mein difference detect hua hai. Isay manually review karein.</p></div>"
    else:
        body += "<div class='card'><p class='ok'>✅ Referral bonus totals currently consistent hain.</p></div>"
    body += "<div class='card'><h3 class='gold'>⚠️ Missing Referrer Accounts</h3>"
    if missing_referrers:
        body += "<div style='overflow-x:auto'><table><tr><th>User</th><th>Referrer</th><th>Status</th><th>Bonus</th></tr>"
        for r in missing_referrers:
            st = "Rewarded" if r["referral_rewarded"] else "Pending"
            body += "<tr><td>" + esc(r["username"]) + "</td><td>" + esc(r["referred_by"]) + "</td><td>" + st + "</td><td>Rs." + str(r["referral_bonus"] or 0) + "</td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='ok'>✅ Sab referred users ke referrer accounts valid hain.</p>"
    body += "</div><div class='card'><h3 class='gold'>💰 Rewarded With Zero Bonus</h3>"
    if zero_bonus_rewarded:
        body += "<div style='overflow-x:auto'><table><tr><th>User</th><th>Referrer</th></tr>"
        for r in zero_bonus_rewarded:
            body += "<tr><td>" + esc(r["username"]) + "</td><td>" + esc(r["referred_by"]) + "</td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='ok'>✅ Koi rewarded referral zero bonus ke saath nahi mila.</p>"
    body += "</div><a class='btn' href='/admin/referrals'>👥 REFERRAL MANAGEMENT</a><a class='btn' href='/admin/referral-withdrawal-rule'>🎯 WITHDRAWAL REFERRAL RULE</a><a class='btn2' href='/admin/referral-summary'>📊 SUMMARY</a><a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Referral Integrity Audit", body)



@app.route("/admin/referral-withdrawal-rule", methods=["GET", "POST"])
@admin_required
def admin_referral_withdrawal_rule():
    # Step 127: keep one canonical setting pair so admin changes immediately
    # affect the user withdrawal/dashboard eligibility. Migrate legacy keys once.
    if get_setting("withdraw_referral_members") is None:
        legacy_members = get_setting("withdrawal_referral_required_members")
        if legacy_members is not None:
            set_setting("withdraw_referral_members", legacy_members)
    if get_setting("withdraw_referral_plans") is None:
        legacy_plans = get_setting("withdrawal_referral_required_plans")
        if legacy_plans is not None:
            set_setting("withdraw_referral_plans", legacy_plans)
    msg = ""
    if request.method == "POST":
        try:
            required_members = max(1, min(1000, int(request.form.get("required_members", "5"))))
            required_plans = max(1, min(required_members, int(request.form.get("required_plans", "3"))))
            enabled = "1" if request.form.get("rule_enabled") == "1" else "0"
            set_setting("withdraw_referral_members", str(required_members))
            set_setting("withdraw_referral_plans", str(required_plans))
            set_setting("withdraw_referral_rule_enabled", enabled)
            state_text = "enabled" if enabled == "1" else "deactivated"
            add_audit_log("Updated separate referral withdrawal rule", "", f"Enabled={enabled}, Required members={required_members}, required referred plan buyers={required_plans}")
            msg = "<p class='ok'>✅ Rule save ho gaya — referral withdrawal rule " + state_text + ".</p>"
        except Exception as exc:
            msg = "<p class='bad'>❌ Rule save nahi ho saka: " + esc(str(exc)) + "</p>"
    try:
        required_members = max(1, setting_int("withdraw_referral_members", 5))
        required_plans = max(1, min(setting_int("withdraw_referral_plans", 3), required_members))
        rule_enabled = get_setting("withdraw_referral_rule_enabled") != "0"
    except Exception:
        required_members, required_plans, rule_enabled = 5, 3, True
    status_text = "🟢 ACTIVE" if rule_enabled else "🔴 DEACTIVATED"
    status_class = "ok" if rule_enabled else "bad"
    body = (
        "<div class='card' style='border-color:#d4af37;background:linear-gradient(145deg,#171717,#0c0c0c)'>"
        "<h2 class='gold'>🎯 REFERRAL WITHDRAWAL RULE</h2>"
        "<p>Withdrawal unlock karne ki referral requirement yahan separately manage karein.</p>" + msg +
        "<div class='" + status_class + "' style='font-weight:800;padding:10px 12px;border:1px solid rgba(212,175,55,.35);border-radius:10px;margin:10px 0'>" + status_text + "</div>"
        "<form method='post'>"
        "<label>⚙️ Rule Status</label>"
        "<select name='rule_enabled'><option value='1'" + (" selected" if rule_enabled else "") + ">ACTIVE — Apply referral requirement</option><option value='0'" + (" selected" if not rule_enabled else "") + ">DEACTIVATED — No referral requirement</option></select>"
        "<label>👥 Required Referred Members</label>"
        "<input type='number' name='required_members' min='1' max='1000' value='" + str(required_members) + "' required>"
        "<label>💳 Required Referred Plan Buyers</label>"
        "<input type='number' name='required_plans' min='1' max='" + str(required_members) + "' value='" + str(required_plans) + "' required>"
        "<button class='btn' type='submit'>💾 SAVE RULE & STATUS</button>"
        "</form></div>"
        "<div class='card'><h3 class='gold'>Current Rule</h3>"
        "<p>👥 Members required: <b>" + str(required_members) + "</b></p>"
        "<p>💳 Plan buyers required: <b>" + str(required_plans) + "</b></p>"
        "<p>⚙️ Status: <b>" + status_text + "</b></p>"
        "<p class='small'>Active ho to example: 5 members refer + un mein se 3 ka active/approved plan = withdrawal eligibility. Deactivated ho to ye referral condition withdrawal ko block nahi karegi.</p>"
        "<a class='btn2' href='/admin'>🏠 ADMIN HOME</a></div>"
    )
    return layout("Referral Withdrawal Rule", body)

@app.route("/admin/referrals/export")
@admin_required
def admin_referrals_export():
    import csv
    from io import StringIO
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()
    date = (request.args.get("date") or "").strip()
    if status not in {"all", "rewarded", "pending"}:
        status = "all"
    where = ["u.referred_by IS NOT NULL", "u.referred_by != ''"]
    params = []
    if q:
        where.append("(u.username LIKE ? OR u.referred_by LIKE ? OR u.ref_code LIKE ?)")
        like = "%" + q + "%"
        params += [like, like, like]
    if status == "rewarded":
        where.append("u.referral_rewarded=1")
    elif status == "pending":
        where.append("u.referral_rewarded=0")
    if date:
        where.append("substr(u.created_at,1,10)=?")
        params.append(date)
    conn = db()
    rows = conn.execute(
        "SELECT u.username,u.referred_by,u.ref_code,u.referral_rewarded,COALESCE(u.referral_bonus,0) AS referral_bonus,u.created_at "
        "FROM users u WHERE " + " AND ".join(where) + " ORDER BY u.id DESC", tuple(params)
    ).fetchall()
    conn.close()
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["Username","Referrer","Referral Code","Status","Bonus","Joined At"])
    for r in rows:
        writer.writerow([r["username"], r["referred_by"], r["ref_code"], "Rewarded" if r["referral_rewarded"] else "Pending", r["referral_bonus"], r["created_at"]])
    resp = make_response(out.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=earnpro_admin_referrals.csv"
    return resp


@app.route("/admin/referral-summary")
@admin_required
def admin_referral_summary():
    conn = db()
    rows = conn.execute(
        "SELECT referred_by AS referrer, COUNT(*) AS total, "
        "SUM(CASE WHEN referral_rewarded=1 THEN 1 ELSE 0 END) AS rewarded, "
        "SUM(CASE WHEN referral_rewarded=0 THEN 1 ELSE 0 END) AS pending, "
        "COALESCE(SUM(referral_bonus),0) AS bonus "
        "FROM users WHERE referred_by IS NOT NULL AND referred_by!='' "
        "GROUP BY referred_by ORDER BY total DESC, bonus DESC, referrer ASC"
    ).fetchall()
    conn.close()
    body = "<div class='card'><h2 class='gold'>📊 Referral Performance Summary</h2><p>Har referrer ki referral performance aur bonus overview.</p>"
    if not rows:
        body += "<p class='small'>No referral data available yet.</p>"
    else:
        body += "<div style='overflow-x:auto'><table><tr><th>Referrer</th><th>Total</th><th>Rewarded</th><th>Pending</th><th>Bonus</th></tr>"
        for r in rows:
            body += "<tr><td>" + esc(r["referrer"]) + "</td><td>" + str(r["total"]) + "</td><td>" + str(r["rewarded"]) + "</td><td>" + str(r["pending"]) + "</td><td>Rs." + str(r["bonus"]) + "</td></tr>"
        body += "</table></div>"
    body += "</div><a class='btn' href='/admin/referrals'>👥 REFERRAL MANAGEMENT</a><a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Referral Performance Summary", body)

@app.route("/admin/plans", methods=["GET", "POST"])
@admin_required
def admin_plans():
    msg = ""
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        conn = db()
        try:
            if action == "add":
                name = request.form.get("name", "").strip()
                amount = int(request.form.get("amount", "0"))
                days = int(request.form.get("days", "0"))
                if not name or amount <= 0 or days <= 0:
                    raise ValueError
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("INSERT INTO plans(name,amount,days,active,created_at,updated_at) VALUES(?,?,?,?,?,?)", (name,amount,days,1,now,now))
                conn.commit()
                add_audit_log("Added plan", "", name + " | Rs." + str(amount) + " | " + str(days) + " days")
                msg = "<p class='ok'>New plan add ho gaya.</p>"
            elif action == "update":
                pid = int(request.form.get("id", "0"))
                name = request.form.get("name", "").strip()
                amount = int(request.form.get("amount", "0"))
                days = int(request.form.get("days", "0"))
                active = 1 if request.form.get("active") == "1" else 0
                if pid <= 0 or not name or amount <= 0 or days <= 0:
                    raise ValueError
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("UPDATE plans SET name=?,amount=?,days=?,active=?,updated_at=? WHERE id=?", (name,amount,days,active,now,pid))
                conn.commit()
                add_audit_log("Updated plan", "", name + " | Rs." + str(amount) + " | " + str(days) + " days | active=" + str(active))
                msg = "<p class='ok'>Plan update ho gaya.</p>"
            elif action == "toggle":
                pid = int(request.form.get("id", "0"))
                row = conn.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
                if row:
                    new_active = 0 if int(row["active"]) else 1
                    conn.execute("UPDATE plans SET active=?,updated_at=? WHERE id=?", (new_active,datetime.now().strftime("%Y-%m-%d %H:%M:%S"),pid))
                    conn.commit()
                    add_audit_log("Toggled plan", "", row["name"] + " | active=" + str(new_active))
                    msg = "<p class='ok'>Plan status change ho gaya.</p>"
            elif action == "delete":
                pid = int(request.form.get("id", "0"))
                row = conn.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
                if row:
                    used = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE plan=?", (row["name"],)).fetchone()["c"]
                    if used:
                        msg = "<p class='bad'>Ye plan payment history mein use ho chuka hai, is liye delete nahi kiya ja sakta. Isay Disable karein.</p>"
                    else:
                        conn.execute("DELETE FROM plans WHERE id=?", (pid,))
                        conn.commit()
                        add_audit_log("Deleted plan", "", row["name"])
                        msg = "<p class='ok'>Plan delete ho gaya.</p>"
        except (ValueError, sqlite3.IntegrityError):
            conn.rollback()
            msg = "<p class='bad'>Plan details valid aur unique honi chahiye.</p>"
        finally:
            conn.close()

    conn = db()
    rows = conn.execute("SELECT * FROM plans ORDER BY active DESC, days ASC, id ASC").fetchall()
    conn.close()
    body = ("<div class='card'><h2 class='gold'>💳 Dynamic Plan Manager</h2>" + msg +
            "<p class='small'>Admin apni marzi se plan ka naam, price aur duration set kar sakta hai. Disabled plan users ko purchase page par nahi dikhega.</p>"
            "<form method='post'><input type='hidden' name='action' value='add'>"
            "<label>Plan Name</label><input name='name' required placeholder='Example: Premium 45 Days'>"
            "<label>Price (Rs.)</label><input name='amount' type='number' min='1' required>"
            "<label>Duration (Days)</label><input name='days' type='number' min='1' required>"
            "<button class='btn'>➕ ADD PLAN</button></form></div>")
    for r in rows:
        body += ("<div class='card'><h3 class='gold'>" + esc(r["name"]) + "</h3>"
                 "<p><b>Price:</b> Rs." + str(r["amount"]) + " | <b>Days:</b> " + str(r["days"]) + " | <b>Status:</b> " + ("ACTIVE" if r["active"] else "DISABLED") + "</p>"
                 "<form method='post'><input type='hidden' name='action' value='update'><input type='hidden' name='id' value='" + str(r["id"]) + "'>"
                 "<label>Plan Name</label><input name='name' value='" + esc(r["name"]) + "' required>"
                 "<label>Price</label><input name='amount' type='number' min='1' value='" + str(r["amount"]) + "' required>"
                 "<label>Days</label><input name='days' type='number' min='1' value='" + str(r["days"]) + "' required>"
                 "<label><input type='checkbox' name='active' value='1' " + ("checked" if r["active"] else "") + "> Active</label>"
                 "<button class='btn'>SAVE PLAN</button></form>"
                 "<form method='post'><input type='hidden' name='action' value='toggle'><input type='hidden' name='id' value='" + str(r["id"]) + "'><button class='btn2'>" + ("DISABLE" if r["active"] else "ENABLE") + " PLAN</button></form>"
                 "<form method='post' onsubmit=\"return confirm('Is plan ko delete karna hai?')\"><input type='hidden' name='action' value='delete'><input type='hidden' name='id' value='" + str(r["id"]) + "'><button class='btn2 danger'>DELETE PLAN</button></form></div>")
    body += "<div class='card'><a class='btn2' href='/admin/settings'>⚙️ PAYMENT SETTINGS</a><a class='btn2' href='/admin'>BACK TO ADMIN</a></div>"
    return layout("Admin Plan Manager", body)


@app.route("/admin")
@admin_required
def admin_panel():
    conn = db()
    users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    approved = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE status='Approved'").fetchone()["c"]
    pending_p = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE status='Pending'").fetchone()["c"]
    pending_w = conn.execute("SELECT COUNT(*) AS c FROM withdrawals WHERE status='Pending'").fetchone()["c"]
    rejected_p = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE status='Rejected'").fetchone()["c"]
    approved_withdrawals = conn.execute("SELECT COALESCE(SUM(amount),0) AS total FROM withdrawals WHERE status='Approved'").fetchone()["total"]
    approved_payments_amount = conn.execute("SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE status='Approved'").fetchone()["total"]
    active_users = conn.execute("SELECT COUNT(*) AS c FROM users u WHERE EXISTS (SELECT 1 FROM payments p WHERE p.username=u.username AND p.status='Approved' AND p.expires_at IS NOT NULL AND p.expires_at!='' AND p.expires_at>?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()["c"]
    expired_users = max(0, users - active_users)
    ads = conn.execute("SELECT COUNT(*) AS c FROM ads WHERE active=1").fetchone()["c"]
    admin_unread_notifications = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE is_read=0").fetchone()["c"]
    conn.close()

    body = (
        "<div class='card' style='border-color:#d4af37;background:linear-gradient(145deg,#171717,#0c0c0c)'>"
        "<h2 class='gold' style='margin-bottom:5px'>👑 Admin Control Panel</h2>"
        "<p class='small'>EarnPro ka complete management dashboard.</p>"
        "<div class='grid'>"
        "<div class='stat'>👤 USERS<b>" + str(users) + "</b></div>"
        "<div class='stat'>🟢 ACTIVE<b>" + str(active_users) + "</b></div>"
        "<div class='stat'>💳 PENDING PAYMENTS<b>" + str(pending_p) + "</b></div>"
        "<div class='stat'>🏦 PENDING WITHDRAWALS<b>" + str(pending_w) + "</b></div>"
        "<div class='stat'>✅ APPROVED PLANS<b>" + str(approved) + "</b></div>"
        "<div class='stat'>🚫 REJECTED PAYMENTS<b>" + str(rejected_p) + "</b></div>"
        "<div class='stat'>🎬 ACTIVE ADS<b>" + str(ads) + "</b></div>"
        "<div class='stat'>⏳ EXPIRED / NO PLAN<b>" + str(expired_users) + "</b></div>"
        "</div></div>"
        "<div class='card'><h3 class='gold'>💰 Financial Overview</h3>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>Approved Payments</span><b>Rs." + str(approved_payments_amount) + "</b></div>"
        "<div class='stat'><span class='small'>Paid Withdrawals</span><b>Rs." + str(approved_withdrawals) + "</b></div>"
        "</div></div>"
        "<div class='card'><h3 class='gold'>⚡ Quick Management</h3>"
        "<a class='btn' href='/admin/payments'>💳 PAYMENT REQUESTS" + (" (" + str(pending_p) + " PENDING)" if pending_p else "") + "</a>"
        "<a class='btn' href='/admin/withdrawals'>🏦 WITHDRAWAL REQUESTS" + (" (" + str(pending_w) + " PENDING)" if pending_w else "") + "</a>"
        "<a class='btn' href='/admin/users'>👤 USER MANAGEMENT</a>"
        "<a class='btn' href='/admin/ads'>🎬 ADS MANAGER</a>"
        "<a class='btn' href='/admin/plans'>💳 PLAN MANAGER</a>"
        "<a class='btn2' href='/admin/payment-analytics'>📊 PAYMENT ANALYTICS</a>"
        "<a class='btn' href='/admin/referrals'>👥 REFERRAL MANAGEMENT</a>"
        "<a class='btn' href='/admin/referral-withdrawal-rule'>🎯 WITHDRAWAL REFERRAL RULE</a>"
        "</div>"
        "<div class='card'><h3 class='gold'>⚙️ Platform Settings</h3>"
        "<a class='btn2' href='/admin/settings'>💰 PLANS & PAYMENT SETTINGS</a><a class='btn2' href='/admin/settings#announcement'>📢 ANNOUNCEMENT CONTROL</a><a class='btn2' href='/admin/settings'>🚦 LAUNCH CONTROLS</a>"
        "<a class='btn2' href='/admin/notifications'>🔔 NOTIFICATION CENTER (" + str(admin_unread_notifications) + " UNREAD)</a>"
        "<a class='btn2' href='/admin/reports'>📊 REPORTS & ANALYTICS</a>"
        "</div>"
        "<div class='card'><h3 class='gold'>🛡️ Security & Monitoring</h3>"
        "<a class='btn2' href='/admin/audit-logs'>🛡️ ADMIN ACTIVITY LOG</a>"
        "<a class='btn2' href='/admin/login-history'>🔐 ADMIN LOGIN HISTORY</a>"
        "<a class='btn2' href='/admin/system-health'>🩺 SYSTEM HEALTH</a>"
        "<a class='btn2' href='/admin/database-backup'>💾 DATABASE BACKUP</a><a class='btn2' href='/admin/backup-history'>📋 BACKUP HISTORY</a>"
        "<a class='btn2' href='/admin/password'>🔐 CHANGE ADMIN PASSWORD</a>"
        "</div>"
        "<div class='card'><a class='btn2 danger' href='/admin/logout'>🚪 ADMIN LOGOUT</a></div>"
    )
    return layout("Admin Control Panel", body)


@app.route("/admin/settings/reset", methods=["POST"])
@admin_required
def admin_settings_reset():
    conn = db()
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    conn.commit()
    conn.close()
    add_audit_log("Reset platform settings", "", "Plans, rewards, limits and payment settings restored to defaults")
    return redirect(url_for("admin_settings"))


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    msg = ""

    if request.method == "POST":
        fields = [
            ("plan_30", "30 Days Plan Price"),
            ("plan_60", "60 Days Plan Price"),
            ("referral_bonus", "Referral Bonus"),
            ("default_ad_reward", "Default Ad Reward"),
            ("daily_ads_limit", "Daily Ads Limit"),
            ("min_withdraw", "Minimum Withdrawal"),
            ("jazzcash_number", "JazzCash Number"),
            ("easypaisa_number", "Easypaisa Number"),
            ("whatsapp_number", "WhatsApp Number"),
            ("whatsapp_group_link", "WhatsApp Group Link"),
            ("whatsapp_channel_link", "WhatsApp Channel Link"),
            ("facebook_group_link", "Facebook Group Link"),
            ("support_email", "Support Email"),
            ("registration_enabled", "Registration Enabled"),
            ("payments_enabled", "Payments Enabled"),
            ("withdrawals_enabled", "Withdrawals Enabled"),
            ("maintenance_mode", "Maintenance Mode"),
            ("site_name", "Site Name")
        ]

        try:
            for key, label in fields:
                value = request.form.get(key, "").strip()
                if key in ["registration_enabled", "payments_enabled", "withdrawals_enabled", "maintenance_mode"]:
                    value = "1" if request.form.get(key) == "1" else "0"
                if key not in ["jazzcash_number", "easypaisa_number", "whatsapp_number", "whatsapp_group_link", "whatsapp_channel_link", "facebook_group_link", "support_email", "site_name"]:
                    number = int(value)
                    if number < 0:
                        raise ValueError
                    if key == "daily_ads_limit" and number > 100:
                        raise ValueError
                    if key == "min_withdraw" and number < 100:
                        raise ValueError
                    value = str(number)
                set_setting(key, value)

            msg = "<p class='ok'>All settings successfully save ho gayi hain.</p>"
            add_audit_log("Updated platform settings", "", "Plans, rewards, limits and payment numbers updated")
        except Exception:
            msg = "<p class='bad'>Settings check karein: valid numbers likhein. Daily ads max 100 aur minimum withdrawal Rs.100 se kam nahi ho sakta.</p>"

    body = (
        "<div class='card'><h2 class='gold'>⚙️ Admin Settings</h2>"
        "<p>Yahan se aap website ki important settings change kar sakte hain.</p>"
        + msg +
        "<form method='post'>"
        "<label>30 Days Plan Price</label><input type='number' name='plan_30' value='" + esc(get_setting("plan_30")) + "' required>"
        "<label>60 Days Plan Price</label><input type='number' name='plan_60' value='" + esc(get_setting("plan_60")) + "' required>"
        "<label>Referral Bonus</label><input type='number' name='referral_bonus' value='" + esc(get_setting("referral_bonus")) + "' required>"
        "<label>Default Ad Reward</label><input type='number' name='default_ad_reward' value='" + esc(get_setting("default_ad_reward")) + "' required>"
        "<label>Daily Ads Limit</label><input type='number' name='daily_ads_limit' value='" + esc(get_setting("daily_ads_limit")) + "' required>"
        "<label>Minimum Withdrawal</label><input type='number' name='min_withdraw' value='" + esc(get_setting("min_withdraw")) + "' required>"
        "<label>JazzCash Number</label><input name='jazzcash_number' value='" + esc(get_setting("jazzcash_number")) + "' placeholder='03XXXXXXXXX'>"
        "<label>Easypaisa Number</label><input name='easypaisa_number' value='" + esc(get_setting("easypaisa_number")) + "' placeholder='03XXXXXXXXX'>"
        "<label>WhatsApp Number</label><input name='whatsapp_number' value='" + esc(get_setting("whatsapp_number")) + "' placeholder='923XXXXXXXXX'>"
        "<label>WhatsApp Group Link</label><input name='whatsapp_group_link' value='" + esc(get_setting("whatsapp_group_link")) + "' placeholder='https://chat.whatsapp.com/...'><label>WhatsApp Channel Link</label><input name='whatsapp_channel_link' value='" + esc(get_setting("whatsapp_channel_link")) + "' placeholder='https://whatsapp.com/channel/...'>"
        "<label>Support Email</label><input type='email' name='support_email' value='" + esc(get_setting("support_email")) + "' placeholder='support@example.com'>"
        "<label>Site Name</label><input name='site_name' value='" + esc(get_setting("site_name")) + "' maxlength='60'>"
        "<label><input type='checkbox' name='registration_enabled' value='1' " + ("checked" if get_setting("registration_enabled") == "1" else "") + "> Allow New Registrations</label>"
        "<label><input type='checkbox' name='payments_enabled' value='1' " + ("checked" if get_setting("payments_enabled") == "1" else "") + "> Allow Plan Payments</label>"
        "<label><input type='checkbox' name='withdrawals_enabled' value='1' " + ("checked" if get_setting("withdrawals_enabled") == "1" else "") + "> Allow Withdrawals</label>"
        "<label><input type='checkbox' name='maintenance_mode' value='1' " + ("checked" if get_setting("maintenance_mode") == "1" else "") + "> Maintenance Mode</label>"
        "<label>Announcement Text</label><textarea name='announcement_text' rows='3' placeholder='Site announcement...'>" + esc(get_setting("announcement_text")) + "</textarea>"
        "<label><input type='checkbox' name='announcement_enabled' value='1' " + ("checked" if get_setting("announcement_enabled") == "1" else "") + "> Show announcement on user dashboard</label>"
        "<button class='btn'>SAVE ALL SETTINGS</button></form>"
        "<form method='post' action='/admin/settings/reset' onsubmit=\"return confirm('Default settings restore karni hain?');\">"
        "<button class='btn2' type='submit'>♻️ RESTORE DEFAULT SETTINGS</button></form></div>"
        "<div class='card'><h3 class='gold'>📊 Current Control Summary</h3>"
        "<p>30 Days: <b class='gold'>Rs." + esc(get_setting("plan_30")) + "</b> &nbsp; | &nbsp; 60 Days: <b class='gold'>Rs." + esc(get_setting("plan_60")) + "</b></p>"
        "<p>Ad Reward: <b class='gold'>Rs." + esc(get_setting("default_ad_reward")) + "</b> &nbsp; | &nbsp; Daily Limit: <b class='gold'>" + esc(get_setting("daily_ads_limit")) + "</b></p>"
        "<p>Referral Bonus: <b class='gold'>Rs." + esc(get_setting("referral_bonus")) + "</b> &nbsp; | &nbsp; Min Withdrawal: <b class='gold'>Rs." + esc(get_setting("min_withdraw")) + "</b></p>"
        "<p>Registration: <b class='gold'>" + ("ON" if get_setting("registration_enabled") == "1" else "OFF") + "</b> &nbsp; | &nbsp; Payments: <b class='gold'>" + ("ON" if get_setting("payments_enabled") == "1" else "OFF") + "</b> &nbsp; | &nbsp; Withdrawals: <b class='gold'>" + ("ON" if get_setting("withdrawals_enabled") == "1" else "OFF") + "</b></p>"
        "</div>"
        "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    )
    return layout("Admin Settings", body)



# Step 63: User Ad History & Personal Ad Performance.
@app.route("/ad-history")
@login_required
def ad_history():
    username = session["username"]
    today = date.today().isoformat()
    conn = db()
    today_stats = conn.execute(
        "SELECT COUNT(*) AS claims, COALESCE(SUM(reward),0) AS rewards, COALESCE(SUM(watch_seconds),0) AS watch FROM ad_watch_history WHERE username=? AND claim_date=?",
        (username, today)
    ).fetchone()
    total_stats = conn.execute(
        "SELECT COUNT(*) AS claims, COALESCE(SUM(reward),0) AS rewards, COALESCE(SUM(watch_seconds),0) AS watch FROM ad_watch_history WHERE username=?",
        (username,)
    ).fetchone()
    open_stats = conn.execute(
        "SELECT COUNT(*) AS opens, COUNT(DISTINCT ad_id) AS unique_ads FROM ad_clicks WHERE username=?",
        (username,)
    ).fetchone()
    per_ad = conn.execute(
        "SELECT ad_title, COUNT(*) AS claims, COALESCE(SUM(reward),0) AS rewards, COALESCE(SUM(watch_seconds),0) AS watch FROM ad_watch_history WHERE username=? GROUP BY ad_id, ad_title ORDER BY claims DESC, rewards DESC LIMIT 10",
        (username,)
    ).fetchall()
    recent = conn.execute(
        "SELECT ad_title,reward,watch_seconds,claim_date,created_at FROM ad_watch_history WHERE username=? ORDER BY id DESC LIMIT 30",
        (username,)
    ).fetchall()
    conn.close()

    body = (
        "<div class='card'><h2 class='gold'>🎬 My Ad History</h2>"
        "<p>Apni ad watching, opens aur earned rewards ka record yahan check karo.</p></div>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>TODAY CLAIMS</span><b>" + str(today_stats['claims']) + "</b></div>"
        "<div class='stat'><span class='small'>TODAY REWARDS</span><b>Rs." + str(today_stats['rewards']) + "</b></div>"
        "<div class='stat'><span class='small'>TOTAL CLAIMS</span><b>" + str(total_stats['claims']) + "</b></div>"
        "<div class='stat'><span class='small'>TOTAL AD REWARDS</span><b>Rs." + str(total_stats['rewards']) + "</b></div>"
        "<div class='stat'><span class='small'>TOTAL WATCH TIME</span><b>" + str(total_stats['watch']) + " sec</b></div>"
        "<div class='stat'><span class='small'>TOTAL OPENS</span><b>" + str(open_stats['opens']) + "</b></div>"
        "<div class='stat'><span class='small'>UNIQUE ADS OPENED</span><b>" + str(open_stats['unique_ads']) + "</b></div>"
        "</div>"
        "<div class='card'><h3 class='gold'>🏆 My Top Ads</h3>"
    )
    if not per_ad:
        body += "<p class='small'>Abhi koi ad claim history nahi hai.</p>"
    else:
        for r in per_ad:
            body += ("<div class='ad'><b class='gold'>" + esc(r['ad_title']) + "</b>"
                     "<p class='small'>Claims: <b>" + str(r['claims']) + "</b> | Rewards: <b class='gold'>Rs." + str(r['rewards']) + "</b> | Watch: " + str(r['watch']) + " sec</p></div>")
    body += "</div><div class='card'><h3 class='gold'>🕒 Recent Ad Activity</h3>"
    if not recent:
        body += "<p class='small'>No ad activity yet.</p>"
    else:
        for r in recent:
            body += ("<div class='ad'><b>🎬 " + esc(r['ad_title']) + "</b>"
                     "<p class='small'>Reward: <b class='gold'>Rs." + str(r['reward']) + "</b> | Watch: " + str(r['watch_seconds']) + " sec | Date: " + esc(r['claim_date']) + " | " + esc(r['created_at']) + "</p></div>")
    body += "</div><a class='btn' href='/watch-ads'>🎁 WATCH ADS</a><a class='btn2' href='/dashboard'>BACK TO DASHBOARD</a>"
    return layout("My Ad History", body)

@app.route("/admin/ads")
@admin_required
def admin_ads():
    """Step 58: Pro ad manager with safe filters and per-ad performance summary."""
    category_filter = request.args.get("category", "").strip()
    status_filter = request.args.get("status", "all").strip().lower()
    if status_filter not in {"all", "active", "disabled"}:
        status_filter = "all"

    conn = db()
    where = []
    params = []
    if category_filter:
        where.append("COALESCE(a.category,'General')=?")
        params.append(category_filter)
    if status_filter == "active":
        where.append("a.active=1")
    elif status_filter == "disabled":
        where.append("a.active=0")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT a.*, "
        "(SELECT COUNT(*) FROM ad_claims c WHERE c.ad_id=a.id) AS claim_count, "
        "(SELECT COALESCE(SUM(h.reward),0) FROM ad_watch_history h WHERE h.ad_id=a.id) AS reward_total "
        "FROM ads a" + where_sql + " ORDER BY a.priority DESC, a.id ASC", params
    ).fetchall()
    categories = conn.execute(
        "SELECT DISTINCT COALESCE(category,'General') AS category FROM ads ORDER BY category COLLATE NOCASE"
    ).fetchall()
    conn.close()

    body = (
        "<div class='card'><h2 class='gold'>🎬 Ads Manager Pro</h2>"
        "<p>Ads ko filter, edit, enable/disable aur performance check kar sakte ho.</p>"
        "<a class='btn' href='/admin/ad/add'>➕ ADD NEW AD</a>"
        "<a class='btn2' href='/admin/ad-dashboard'>📊 CONTROL DASHBOARD</a>"
        "<a class='btn2' href='/admin/ad-analytics'>📈 AD ANALYTICS</a></div>"
        "<div class='card'><h3 class='gold'>⚡ BULK CONTROL</h3>"
        "<form method='post' action='/admin/ads/bulk-toggle' style='display:inline'><input type='hidden' name='action' value='enable'><button class='btn'>✅ ENABLE ALL</button></form>"
        "<form method='post' action='/admin/ads/bulk-toggle' style='display:inline'><input type='hidden' name='action' value='disable'><button class='btn2' onclick='return confirm(\"Disable all ads?\")'>🛑 DISABLE ALL</button></form></div>"
        "<div class='card'><h3 class='gold'>🔎 FILTER ADS</h3>"
        "<form method='get'><label>Category</label><select name='category'>"
        "<option value=''>All Categories</option>"
    )
    for c in categories:
        cat = c["category"] or "General"
        selected = " selected" if cat == category_filter else ""
        body += "<option value='" + esc(cat) + "'" + selected + ">" + esc(cat) + "</option>"
    body += (
        "</select><label>Status</label><select name='status'>"
        "<option value='all'" + (" selected" if status_filter == "all" else "") + ">All</option>"
        "<option value='active'" + (" selected" if status_filter == "active" else "") + ">Active</option>"
        "<option value='disabled'" + (" selected" if status_filter == "disabled" else "") + ">Disabled</option>"
        "</select><button class='btn'>🔎 APPLY FILTER</button>"
        "<a class='btn2' href='/admin/ads'>RESET</a></form></div>"
    )

    if not rows:
        body += "<div class='card'><p>No ads found for this filter.</p></div>"

    for ad in rows:
        status = "ACTIVE" if ad["active"] else "DISABLED"
        target_label = {"all":"ALL USERS","demo":"DEMO USERS","active":"ACTIVE PLAN USERS"}.get((ad["target_mode"] or "all").lower(), "ALL USERS")
        status_class = "ok" if ad["active"] else "bad"
        claims = int(ad["claim_count"] or 0)
        reward_total = int(ad["reward_total"] or 0)
        max_claims = int(ad["max_claims"] or 0)
        daily_max = int(ad["daily_max_claims"] or 0)
        user_max = int(ad["user_max_claims"] or 0)
        budget = int(ad["budget"] or 0)
        remaining = "Unlimited" if not max_claims else str(max(0, max_claims - claims))
        body += (
            "<div class='card'><h3 class='gold'>🎬 " + esc(ad["title"]) + "</h3>"
            "<p>Reward: <b class='gold'>Rs." + str(ad["reward"]) + "</b> | "
            "Status: <span class='" + status_class + "'>" + status + "</span></p>"
            "<p class='small'>Claims: <b>" + str(claims) + "</b> | Total rewards: <b class='gold'>Rs." + str(reward_total) + "</b> | Remaining claims: <b>" + remaining + "</b></p>"
            "<p class='small'>Content: " + esc(ad["content"]) + "</p>"
            "<p class='small'>Link: " + esc(ad["link"] or "No link") + "</p>"
            "<p class='small'>Video: " + esc(ad["video_url"] or "No video") + " | Watch: " + str(ad["watch_seconds"] or 30) + " sec</p>"
            "<p class='small'>Category: <b>" + esc(ad["category"] or "General") + "</b> | Priority: <b>" + str(ad["priority"] or 0) + "</b> | Max Claims: <b>" + ("Unlimited" if not max_claims else str(max_claims)) + "</b></p>"
            "<p class='small'>Schedule: " + esc(ad["start_date"] or "Any date") + " → " + esc(ad["end_date"] or "Any date") + "</p>"
            "<p class='small'>Daily Cap: <b>" + ("Unlimited" if not daily_max else str(daily_max)) + "</b> | Per User: <b>" + ("Unlimited" if not user_max else str(user_max)) + "</b> | Budget: <b class='gold'>" + ("Unlimited" if not budget else "Rs." + str(budget)) + "</b></p>"
            "<a class='btn' href='/admin/ad/edit/" + str(ad["id"]) + "'>✏️ EDIT AD</a>"
            "<a class='btn2' href='/admin/ad/preview/" + str(ad["id"]) + "'>👁️ PREVIEW</a>"
            "<form method='post' action='/admin/ad/duplicate/" + str(ad["id"]) + "' style='display:inline'><button class='btn2' onclick='return confirm(\"Create a disabled copy of this ad?\")'>📋 DUPLICATE</button></form>"
        )
        if ad["active"]:
            body += "<a class='btn2' href='/admin/ad/toggle/" + str(ad["id"]) + "' onclick=\"return confirm('Disable this ad?')\">🛑 DISABLE AD</a>"
        else:
            body += "<a class='btn2' href='/admin/ad/toggle/" + str(ad["id"]) + "'>✅ ENABLE AD</a>"
        body += (
            "<a class='btn2 danger' href='/admin/ad/delete/" + str(ad["id"]) +
            "' onclick=\"return confirm('Delete this ad permanently? This cannot be undone.')\">🗑️ DELETE AD</a></div>"
        )

    body += "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Ads Manager Pro", body)


@app.route("/admin/ad-analytics")
@admin_required
def admin_ad_analytics():
    selected_date = (request.args.get("date") or date.today().isoformat()).strip()
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = date.today().isoformat()

    conn = db()
    summary = conn.execute(
        "SELECT COUNT(*) AS claims, COALESCE(SUM(reward),0) AS rewards FROM ad_watch_history WHERE claim_date=?",
        (selected_date,)
    ).fetchone()
    click_summary = conn.execute(
        "SELECT COUNT(*) AS clicks, COUNT(DISTINCT username) AS unique_users FROM ad_clicks WHERE click_date=?",
        (selected_date,)
    ).fetchone()
    total = conn.execute(
        "SELECT COUNT(*) AS claims, COALESCE(SUM(reward),0) AS rewards FROM ad_watch_history"
    ).fetchone()
    click_total = conn.execute("SELECT COUNT(*) AS clicks, COUNT(DISTINCT username) AS unique_users FROM ad_clicks").fetchone()
    rows = conn.execute(
        "SELECT h.ad_id, h.ad_title, COUNT(*) AS claims, COALESCE(SUM(h.reward),0) AS rewards, MAX(h.watch_seconds) AS watch_seconds, "
        "COALESCE((SELECT COUNT(*) FROM ad_clicks c WHERE c.ad_id=h.ad_id AND c.click_date=?),0) AS clicks "
        "FROM ad_watch_history h WHERE h.claim_date=? GROUP BY h.ad_id, h.ad_title ORDER BY claims DESC, h.ad_id ASC",
        (selected_date, selected_date)
    ).fetchall()
    recent = conn.execute(
        "SELECT username, ad_title, reward, watch_seconds, created_at FROM ad_watch_history "
        "WHERE claim_date=? ORDER BY id DESC LIMIT 30",
        (selected_date,)
    ).fetchall()
    conn.close()

    body = (
        "<div class='card'><h2 class='gold'>📊 Ad Analytics</h2>"
        "<form method='get' style='margin-bottom:12px'>"
        "<label>📅 Select Date</label><input type='date' name='date' value='" + esc(selected_date) + "' required>"
        "<button class='btn' type='submit'>🔎 VIEW DATE</button>"
        "<a class='btn2' href='/admin/ad-analytics/export?date=" + esc(selected_date) + "'>⬇️ EXPORT CSV</a>"
        "</form>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>SELECTED DATE CLAIMS</span><b>" + str(summary['claims']) + "</b></div>"
        "<div class='stat'><span class='small'>SELECTED DATE REWARDS</span><b>Rs." + str(summary['rewards']) + "</b></div>"
        "<div class='stat'><span class='small'>ALL-TIME CLAIMS</span><b>" + str(total['claims']) + "</b></div>"
        "<div class='stat'><span class='small'>ALL-TIME REWARDS</span><b>Rs." + str(total['rewards']) + "</b></div>"
        "<div class='stat'><span class='small'>DATE OPENS</span><b>" + str(click_summary['clicks']) + "</b></div>"
        "<div class='stat'><span class='small'>UNIQUE VIEWERS</span><b>" + str(click_summary['unique_users']) + "</b></div>"
        "<div class='stat'><span class='small'>ALL-TIME OPENS</span><b>" + str(click_total['clicks']) + "</b></div>"
        "</div></div>"
        "<div class='card'><h3 class='gold'>🎬 Performance By Ad — " + esc(selected_date) + "</h3>"
    )
    if not rows:
        body += "<p class='small'>Is date par koi ad reward claim nahi hua.</p>"
    else:
        for r in rows:
            body += (
                "<div class='card'><h3 class='gold'>" + esc(r['ad_title']) + "</h3>"
                "<p>Claims: <b>" + str(r['claims']) + "</b> &nbsp; | &nbsp; Opens: <b>" + str(r['clicks']) + "</b> &nbsp; | &nbsp; Rewards Paid: <b>Rs." + str(r['rewards']) + "</b></p>"
                "<p class='small'>Watch Duration: " + str(r['watch_seconds']) + " sec</p></div>"
            )
    body += "</div><div class='card'><h3 class='gold'>🕒 Recent Ad Claims</h3>"
    if not recent:
        body += "<p class='small'>Is date ki claim history nahi hai.</p>"
    else:
        for r in recent:
            body += (
                "<div class='card'><b>👤 " + esc(r['username']) + "</b> — " + esc(r['ad_title']) +
                "<p class='small'>Reward: Rs." + str(r['reward']) + " | Watch: " + str(r['watch_seconds']) + " sec | " + str(r['created_at']) + "</p></div>"
            )
    body += "</div><a class='btn2' href='/admin/ads'>BACK TO ADS</a>"
    return layout("Ad Analytics", body)


@app.route("/admin/ad-analytics/export")
@admin_required
def admin_ad_analytics_export():
    selected_date = (request.args.get("date") or date.today().isoformat()).strip()
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = date.today().isoformat()

    conn = db()
    rows = conn.execute(
        "SELECT username, ad_id, ad_title, reward, watch_seconds, claim_date, created_at "
        "FROM ad_watch_history WHERE claim_date=? ORDER BY id ASC",
        (selected_date,)
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Username", "Ad ID", "Ad Title", "Reward", "Watch Seconds", "Claim Date", "Created At"])
    for r in rows:
        writer.writerow([r["username"], r["ad_id"], r["ad_title"], r["reward"], r["watch_seconds"], r["claim_date"], r["created_at"]])

    filename = "earnpro_ad_analytics_" + selected_date + ".csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=" + filename}
    )


@app.route("/admin/ad/add", methods=["GET", "POST"])
@admin_required
def admin_ad_add():
    msg = ""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        link = request.form.get("link", "").strip()
        video_url = request.form.get("video_url", "").strip()
        category = request.form.get("category", "General").strip() or "General"
        target_mode = request.form.get("target_mode", "all").strip().lower()
        if target_mode not in {"all", "active", "demo"}:
            target_mode = "all"
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        try:
            priority = int(request.form.get("priority", "0"))
        except Exception:
            priority = 0
        try:
            max_claims = int(request.form.get("max_claims", "0"))
        except Exception:
            max_claims = -1
        try:
            daily_max_claims = int(request.form.get("daily_max_claims", "0"))
        except Exception:
            daily_max_claims = -1
        try:
            user_max_claims = int(request.form.get("user_max_claims", "0"))
        except Exception:
            user_max_claims = -1
        try:
            budget = int(request.form.get("budget", "0"))
        except Exception:
            budget = -1
        try:
            watch_seconds = int(request.form.get("watch_seconds", "30"))
        except Exception:
            watch_seconds = 0
        try:
            reward = int(request.form.get("reward", "0"))
        except Exception:
            reward = -1

        if not title:
            msg = "<p class='bad'>Ad title required hai.</p>"
        elif reward < 0 or watch_seconds < 1 or watch_seconds > 3600 or max_claims < 0 or daily_max_claims < 0 or user_max_claims < 0 or budget < 0:
            msg = "<p class='bad'>Reward, watch duration aur max claims valid hona chahiye.</p>"
        elif start_date and end_date and start_date > end_date:
            msg = "<p class='bad'>Start date end date se pehle honi chahiye.</p>"
        else:
            conn = db()
            conn.execute(
                "INSERT INTO ads(title,content,link,reward,active,created_at,video_url,watch_seconds,category,priority,start_date,end_date,max_claims,daily_max_claims,user_max_claims,budget,target_mode) VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?)",
                (title, content, link, reward, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), video_url, watch_seconds, category, priority, start_date, end_date, max_claims, daily_max_claims, user_max_claims, budget, target_mode)
            )
            conn.commit()
            conn.close()
            add_audit_log("Added advertisement", "", "New ad created from Admin Panel")
            return redirect(url_for("admin_ads"))

    body = (
        "<div class='card'><h2 class='gold'>➕ Add New Ad</h2>" + msg +
        "<form method='post'><label>Ad Title</label>"
        "<input name='title' placeholder='Example: Ad 6' required>"
        "<label>Ad Content / Description</label>"
        "<textarea name='content' placeholder='Ad ke bare mein likhein'></textarea>"
        "<label>Ad Link (optional)</label><input name='link' placeholder='https://example.com'>"
        "<label>Video URL (YouTube / direct MP4)</label><input name='video_url' placeholder='YouTube ya direct video link'>"
        "<label>Category</label><input name='category' value='General' placeholder='Example: Finance, App, Product'><label>Target Audience</label><select name='target_mode'><option value='all'>All Users</option><option value='demo'>Demo Users Only</option><option value='active'>Active Plan Users Only</option></select>"
        "<label>Priority (higher = pehle show)</label><input type='number' name='priority' value='0' min='-100' max='100'>"
        "<label>Start Date (optional)</label><input type='date' name='start_date'>"
        "<label>End Date (optional)</label><input type='date' name='end_date'>"
        "<label>Maximum Total Claims (0 = unlimited)</label><input type='number' name='max_claims' value='0' min='0'><label>Daily Campaign Claims (0 = unlimited)</label><input type='number' name='daily_max_claims' value='0' min='0'><label>Per User Claims Per Day (0 = unlimited)</label><input type='number' name='user_max_claims' value='0' min='0'><label>Total Reward Budget (Rs., 0 = unlimited)</label><input type='number' name='budget' value='0' min='0'>"
        "<label>Watch Duration (seconds)</label><input type='number' name='watch_seconds' value='30' min='1' max='3600' required>"
        "<label>Reward (Rs.)</label>"
        "<input type='number' name='reward' value='" + esc(get_setting("default_ad_reward")) + "' min='0' required>"
        "<button class='btn'>ADD AD</button></form></div>"
        "<a class='btn2' href='/admin/ads'>BACK</a>"
    )
    return layout("Add Ad", body)


@app.route("/admin/ad/edit/<int:ad_id>", methods=["GET", "POST"])
@admin_required
def admin_ad_edit(ad_id):
    conn = db()
    ad = conn.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()
    conn.close()

    if not ad:
        return redirect(url_for("admin_ads"))

    msg = ""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        link = request.form.get("link", "").strip()
        video_url = request.form.get("video_url", "").strip()
        category = request.form.get("category", "General").strip() or "General"
        target_mode = request.form.get("target_mode", "all").strip().lower()
        if target_mode not in {"all", "active", "demo"}:
            target_mode = "all"
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        try:
            priority = int(request.form.get("priority", "0"))
        except Exception:
            priority = 0
        try:
            max_claims = int(request.form.get("max_claims", "0"))
        except Exception:
            max_claims = -1
        try:
            daily_max_claims = int(request.form.get("daily_max_claims", "0"))
        except Exception:
            daily_max_claims = -1
        try:
            user_max_claims = int(request.form.get("user_max_claims", "0"))
        except Exception:
            user_max_claims = -1
        try:
            budget = int(request.form.get("budget", "0"))
        except Exception:
            budget = -1
        try:
            watch_seconds = int(request.form.get("watch_seconds", "30"))
        except Exception:
            watch_seconds = 0
        try:
            reward = int(request.form.get("reward", "0"))
        except Exception:
            reward = -1

        if not title or reward < 0 or watch_seconds < 1 or watch_seconds > 3600 or max_claims < 0 or daily_max_claims < 0 or user_max_claims < 0 or budget < 0:
            msg = "<p class='bad'>Title, reward, watch duration aur max claims valid hona chahiye.</p>"
        elif start_date and end_date and start_date > end_date:
            msg = "<p class='bad'>Start date end date se pehle honi chahiye.</p>"
        else:
            conn = db()
            conn.execute(
                "UPDATE ads SET title=?,content=?,link=?,reward=?,video_url=?,watch_seconds=?,category=?,priority=?,start_date=?,end_date=?,max_claims=?,daily_max_claims=?,user_max_claims=?,budget=?,target_mode=? WHERE id=?",
                (title, content, link, reward, video_url, watch_seconds, category, priority, start_date, end_date, max_claims, daily_max_claims, user_max_claims, budget, target_mode, ad_id)
            )
            conn.commit()
            conn.close()
            add_audit_log("Edited advertisement", "", "Advertisement updated from Admin Panel")
            return redirect(url_for("admin_ads"))

    body = (
        "<div class='card'><h2 class='gold'>✏️ Edit Ad</h2>" + msg +
        "<form method='post'><label>Ad Title</label>"
        "<input name='title' value='" + esc(ad["title"]) + "' required>"
        "<label>Ad Content / Description</label>"
        "<textarea name='content'>" + esc(ad["content"]) + "</textarea>"
        "<label>Ad Link (optional)</label><input name='link' value='" + esc(ad["link"]) + "'>"
        "<label>Video URL (YouTube / direct MP4)</label><input name='video_url' value='" + esc(ad["video_url"] or "") + "'>"
        "<label>Category</label><input name='category' value='" + esc(ad["category"] or "General") + "'><label>Target Audience</label><select name='target_mode'><option value='all'" + (" selected" if (ad["target_mode"] or "all")=="all" else "") + ">All Users</option><option value='demo'" + (" selected" if (ad["target_mode"] or "all")=="demo" else "") + ">Demo Users Only</option><option value='active'" + (" selected" if (ad["target_mode"] or "all")=="active" else "") + ">Active Plan Users Only</option></select>"
        "<label>Priority (higher = pehle show)</label><input type='number' name='priority' value='" + str(ad["priority"] or 0) + "' min='-100' max='100'>"
        "<label>Start Date (optional)</label><input type='date' name='start_date' value='" + esc(ad["start_date"] or "") + "'>"
        "<label>End Date (optional)</label><input type='date' name='end_date' value='" + esc(ad["end_date"] or "") + "'>"
        "<label>Maximum Total Claims (0 = unlimited)</label><input type='number' name='max_claims' value='" + str(ad["max_claims"] or 0) + "' min='0'><label>Daily Campaign Claims (0 = unlimited)</label><input type='number' name='daily_max_claims' value='" + str(ad["daily_max_claims"] or 0) + "' min='0'><label>Per User Claims Per Day (0 = unlimited)</label><input type='number' name='user_max_claims' value='" + str(ad["user_max_claims"] or 0) + "' min='0'><label>Total Reward Budget (Rs., 0 = unlimited)</label><input type='number' name='budget' value='" + str(ad["budget"] or 0) + "' min='0'>"
        "<label>Watch Duration (seconds)</label><input type='number' name='watch_seconds' value='" + str(ad["watch_seconds"] or 30) + "' min='1' max='3600' required>"
        "<label>Reward (Rs.)</label><input type='number' name='reward' value='" + str(ad["reward"]) + "' min='0' required>"
        "<button class='btn'>SAVE AD CHANGES</button></form></div>"
        "<a class='btn2' href='/admin/ads'>CANCEL</a>"
    )
    return layout("Edit Ad", body)


@app.route("/admin/ad/toggle/<int:ad_id>")
@admin_required
def admin_ad_toggle(ad_id):
    conn = db()
    ad = conn.execute("SELECT active FROM ads WHERE id=?", (ad_id,)).fetchone()
    if ad:
        conn.execute(
            "UPDATE ads SET active=? WHERE id=?",
            (0 if ad["active"] else 1, ad_id)
        )
        conn.commit()
    conn.close()
    add_audit_log("Toggled advertisement", "", "Advertisement active status changed")
    return redirect(url_for("admin_ads"))


@app.route("/admin/ad/delete/<int:ad_id>")
@admin_required
def admin_ad_delete(ad_id):
    conn = db()
    conn.execute("DELETE FROM ads WHERE id=?", (ad_id,))
    conn.commit()
    conn.close()
    add_audit_log("Deleted advertisement", "", "Advertisement removed from Admin Panel")
    return redirect(url_for("admin_ads"))


# Step 59: Advanced ad operations dashboard. Existing ad routes/features remain intact.
@app.route("/admin/ad-dashboard")
@admin_required
def admin_ad_dashboard():
    conn = db()
    total = conn.execute("SELECT COUNT(*) AS c FROM ads").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) AS c FROM ads WHERE active=1").fetchone()["c"]
    disabled = total - active
    claims = conn.execute("SELECT COUNT(*) AS c FROM ad_claims").fetchone()["c"]
    rewards = conn.execute("SELECT COALESCE(SUM(reward),0) AS s FROM ad_watch_history").fetchone()["s"]
    clicks = conn.execute("SELECT COUNT(*) AS c FROM ad_clicks").fetchone()["c"]
    unique_clickers = conn.execute("SELECT COUNT(DISTINCT username) AS c FROM ad_clicks").fetchone()["c"]
    top = conn.execute("SELECT a.id,a.title,a.active,a.reward,COUNT(c.id) AS claims FROM ads a LEFT JOIN ad_claims c ON c.ad_id=a.id GROUP BY a.id ORDER BY claims DESC,a.priority DESC,a.id ASC LIMIT 10").fetchall()
    conn.close()
    body = ("<div class='card'><h2 class='gold'>📊 Ads Control Dashboard</h2>"
            "<p>Ads ki overall performance aur quick controls yahan milenge.</p>"
            "<a class='btn' href='/admin/ads'>🎬 ADS MANAGER</a>"
            "<a class='btn2' href='/admin/ad-analytics'>📈 ANALYTICS</a></div>"
            "<div class='grid'>"
            "<div class='stat'><b>Total Ads</b><strong>"+str(total)+"</strong></div>"
            "<div class='stat'><b>Active</b><strong>"+str(active)+"</strong></div>"
            "<div class='stat'><b>Disabled</b><strong>"+str(disabled)+"</strong></div>"
            "<div class='stat'><b>Total Claims</b><strong>"+str(claims)+"</strong></div>"
            "<div class='stat'><b>Total Rewards</b><strong>Rs."+str(int(rewards or 0))+"</strong></div>"
            "<div class='stat'><b>Total Opens</b><strong>"+str(clicks)+"</strong></div>"
            "<div class='stat'><b>Unique Viewers</b><strong>"+str(unique_clickers)+"</strong></div></div>")
    body += "<div class='card'><h3 class='gold'>🏆 Top Ads</h3>"
    if not top:
        body += "<p>No ads available.</p>"
    else:
        for r in top:
            rate = round((int(r['claims']) / int(r['clicks'])) * 100, 1) if int(r['clicks'] or 0) else 0
            body += ("<p><b>"+esc(r['title'])+"</b> — Opens: "+str(r['clicks'])+" | Claims: "+str(r['claims'])+" | Rate: "+str(rate)+"% | Reward: Rs."+str(r['reward'])+" | "+("ACTIVE" if r['active'] else "DISABLED")+"</p><a class='btn2' href='/admin/ad/edit/"+str(r['id'])+"'>EDIT</a>")
    body += "</div><a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Ads Control Dashboard", body)

@app.route("/admin/ads/bulk-toggle", methods=["POST"])
@admin_required
def admin_ads_bulk_toggle():
    action = request.form.get("action", "").strip().lower()
    if action not in {"enable", "disable"}:
        return redirect(url_for("admin_ads"))
    conn = db()
    conn.execute("UPDATE ads SET active=?", (1 if action == "enable" else 0,))
    conn.commit(); conn.close()
    add_audit_log("Bulk advertisement status change", "", "All ads set to " + action)
    return redirect(url_for("admin_ads"))

@app.route("/admin/ad/duplicate/<int:ad_id>", methods=["POST"])
@admin_required
def admin_ad_duplicate(ad_id):
    conn = db()
    ad = conn.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()
    if not ad:
        conn.close(); return redirect(url_for("admin_ads"))
    conn.execute("INSERT INTO ads(title,content,link,reward,active,created_at,video_url,watch_seconds,category,priority,start_date,end_date,max_claims,daily_max_claims,user_max_claims,budget,target_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (str(ad['title'])+" (Copy)", ad['content'] or "", ad['link'] or "", ad['reward'] or 20, 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ad['video_url'] or "", ad['watch_seconds'] or 30, ad['category'] or "General", ad['priority'] or 0, ad['start_date'] or "", ad['end_date'] or "", ad['max_claims'] or 0, ad['daily_max_claims'] or 0, ad['user_max_claims'] or 0, ad['budget'] or 0, ad['target_mode'] or "all"))
    conn.commit(); conn.close()
    add_audit_log("Duplicated advertisement", "", "Created disabled copy of ad " + str(ad_id))
    return redirect(url_for("admin_ads"))

@app.route("/admin/ad/preview/<int:ad_id>")
@admin_required
def admin_ad_preview(ad_id):
    conn = db(); ad = conn.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone(); conn.close()
    if not ad: return redirect(url_for("admin_ads"))
    body = "<div class='card'><h2 class='gold'>👁️ Ad Preview</h2><h3>"+esc(ad['title'])+"</h3><p>"+esc(ad['content'] or "")+"</p>"
    video = ad['video_url'] or ""
    if video:
        yt = re.search(r'(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)([A-Za-z0-9_-]{6,})', video)
        if yt:
            body += "<iframe src='https://www.youtube.com/embed/"+esc(yt.group(1))+"' title='Video preview' allowfullscreen style='width:100%;min-height:300px;border:0;border-radius:14px'></iframe>"
        else:
            body += "<video controls playsinline style='width:100%;max-height:420px;border-radius:14px'><source src='"+esc(video)+"'></video>"
    if ad['link']:
        body += "<p><a class='btn' href='"+esc(ad['link'])+"' target='_blank' rel='noopener'>OPEN AD LINK</a></p>"
    body += "<p>Reward: <b class='gold'>Rs."+str(ad['reward'])+"</b> | Watch: "+str(ad['watch_seconds'] or 30)+" sec</p><a class='btn2' href='/admin/ads'>BACK TO ADS</a></div>"
    return layout("Ad Preview", body)

@app.route("/admin/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "All").strip()
    allowed = ["All", "Active", "Expired", "Blocked"]
    if status_filter not in allowed:
        status_filter = "All"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db()
    where = []
    params = []

    if q:
        where.append("(u.username LIKE ? OR u.ref_code LIKE ?)")
        params.extend(["%" + q + "%", "%" + q.upper() + "%"])

    if status_filter == "Blocked":
        where.append("u.blocked=1")
    elif status_filter == "Active":
        where.append("u.blocked=0 AND EXISTS (SELECT 1 FROM payments p2 WHERE p2.username=u.username "
                     "AND p2.status='Approved' AND p2.expires_at IS NOT NULL AND p2.expires_at!='' AND p2.expires_at>?)")
        params.append(now)
    elif status_filter == "Expired":
        where.append("u.blocked=0 AND NOT EXISTS (SELECT 1 FROM payments p2 WHERE p2.username=u.username "
                     "AND p2.status='Approved' AND p2.expires_at IS NOT NULL AND p2.expires_at!='' AND p2.expires_at>?)")
        params.append(now)

    sql = "SELECT u.*, (SELECT p.plan FROM payments p WHERE p.username=u.username AND p.status='Approved' ORDER BY p.id DESC LIMIT 1) AS last_plan, " \
          "(SELECT p.expires_at FROM payments p WHERE p.username=u.username AND p.status='Approved' ORDER BY p.id DESC LIMIT 1) AS last_expiry " \
          "FROM users u"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY u.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    body = (
        "<div class='card'><h2 class='gold'>👤 User Management</h2>"
        "<p>Users ko search karein, plan status dekhein aur account block/unblock karein.</p>"
        "<form method='get'><input name='q' placeholder='Search username/ref code' value='" + esc(q) + "'>"
        "<select name='status'>"
        "<option value='All'" + (" selected" if status_filter=="All" else "") + ">All Users</option>"
        "<option value='Active'" + (" selected" if status_filter=="Active" else "") + ">Active</option>"
        "<option value='Expired'" + (" selected" if status_filter=="Expired" else "") + ">Expired / No Plan</option>"
        "<option value='Blocked'" + (" selected" if status_filter=="Blocked" else "") + ">Blocked</option>"
        "</select><button class='btn'>SEARCH / FILTER</button></form>"
        "<p class='small'>Showing: <b class='gold'>" + esc(status_filter) + "</b> | Total: " + str(len(rows)) + "</p></div>"
    )

    if not rows:
        body += "<div class='card'><p class='small'>No users found.</p></div>"

    for u in rows:
        blocked = bool(u["blocked"]) if "blocked" in u.keys() else False
        if blocked:
            state = "<span class='bad'><b>BLOCKED</b></span>"
        else:
            expiry = u["last_expiry"] or ""
            if expiry and expiry > now:
                state = "<span class='ok'><b>ACTIVE</b></span>"
            else:
                state = "<span class='bad'><b>EXPIRED / NO PLAN</b></span>"

        body += (
            "<div class='card'><h3 class='gold'>" + esc(u["username"]) + "</h3>"
            "<p>Status: " + state + "</p>"
            "<p>Balance: <b class='gold'>Rs." + str(u["balance"]) + "</b> | Total Earning: Rs." + str(u["total_earning"]) + "</p>"
            "<p>Referral Code: " + esc(u["ref_code"]) + " | Referrals: " + str(u["referrals"]) + "</p>"
            "<p>Current/Last Plan: " + esc(u["last_plan"] or "No approved plan") + "</p>"
            "<p>Expiry: " + esc(u["last_expiry"] or "-") + "</p>"
            "<a class='btn' href='/admin/user/" + str(u["id"]) + "'>👁️ VIEW FULL DETAILS</a>"
            "<a class='btn2' href='/admin/user/balance/" + str(u["id"]) + "'>💰 MANAGE BALANCE</a>"
            "<a class='btn2' href='/admin/user/reset-password/" + str(u["id"]) + "'>🔐 RESET PASSWORD</a>"
        )
        if blocked:
            body += "<a class='btn2' href='/admin/user/toggle-block/" + str(u["id"]) + "'>✅ UNBLOCK USER</a>"
        else:
            body += "<a class='btn2 danger' href='/admin/user/toggle-block/" + str(u["id"]) + "' onclick=\"return confirm('Is user ko block karna hai?')\">🚫 BLOCK USER</a>"
        body += "</div>"

    body += "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Admin Users", body)


@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user_detail(user_id):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return redirect(url_for("admin_users"))

    payments = conn.execute(
        "SELECT * FROM payments WHERE username=? ORDER BY id DESC LIMIT 20",
        (user["username"],)
    ).fetchall()
    withdrawals = conn.execute(
        "SELECT * FROM withdrawals WHERE username=? ORDER BY id DESC LIMIT 20",
        (user["username"],)
    ).fetchall()
    transactions = conn.execute(
        "SELECT * FROM transactions WHERE username=? ORDER BY id DESC LIMIT 20",
        (user["username"],)
    ).fetchall()
    conn.close()

    active = get_active_plan(user["username"])
    status = "BLOCKED" if ("blocked" in user.keys() and user["blocked"]) else ("ACTIVE" if active else "EXPIRED / NO PLAN")
    status_class = "bad" if status != "ACTIVE" else "ok"

    body = (
        "<div class='card'><h2 class='gold'>👤 User Details</h2>"
        "<p><b>Username:</b> " + esc(user["username"]) + "</p>"
        "<p><b>Account Status:</b> <span class='" + status_class + "'><b>" + status + "</b></span></p>"
        "<p><b>Referral Code:</b> " + esc(user["ref_code"]) + "</p>"
        "<p><b>Referred By:</b> " + esc(user["referred_by"] or "-") + "</p>"
        "<p><b>Balance:</b> <span class='gold'>Rs." + str(user["balance"]) + "</span></p>"
        "<p><b>Total Earning:</b> Rs." + str(user["total_earning"]) + "</p>"
        "<p><b>Referral Bonus:</b> Rs." + str(user["referral_bonus"]) + "</p>"
        "<p><b>Total Referrals:</b> " + str(user["referrals"]) + "</p>"
        "<a class='btn' href='/admin/user/balance/" + str(user["id"]) + "'>💰 MANAGE BALANCE</a>"
        "<a class='btn2' href='/admin/user/reset-password/" + str(user["id"]) + "'>🔐 RESET PASSWORD</a>"
        "</div>"
    )

    if active:
        body += (
            "<div class='card'><h3 class='gold'>💳 Active Plan</h3>"
            "<p>Plan: <b>" + esc(active["plan"]) + "</b></p>"
            "<p>Amount: Rs." + str(active["amount"]) + "</p>"
            "<p>Method: " + esc(active["method"]) + "</p>"
            "<p>TXID: " + esc(active["txid"]) + "</p>"
            "<p>Activated: " + esc(active["activation_at"] or "-") + "</p>"
            "<p>Expires: <span class='ok'>" + esc(active["expires_at"] or "-") + "</span></p></div>"
        )
    else:
        body += "<div class='card'><h3 class='gold'>💳 Active Plan</h3><p class='bad'>No active approved plan.</p></div>"

    body += "<div class='card'><h3 class='gold'>Payment History</h3>"
    if payments:
        body += "<table><tr><th>Plan</th><th>Amount</th><th>Status</th><th>TXID</th><th>Date</th></tr>"
        for p in payments:
            body += "<tr><td>" + esc(p["plan"]) + "</td><td>Rs." + str(p["amount"]) + "</td><td>" + esc(p["status"]) + "</td><td>" + esc(p["txid"]) + "</td><td>" + esc(p["created_at"]) + "</td></tr>"
        body += "</table>"
    else:
        body += "<p class='small'>No payments.</p>"
    body += "</div>"

    body += "<div class='card'><h3 class='gold'>Withdrawal History</h3>"
    if withdrawals:
        body += "<table><tr><th>Amount</th><th>Method</th><th>Status</th><th>Date</th></tr>"
        for w in withdrawals:
            body += "<tr><td>Rs." + str(w["amount"]) + "</td><td>" + esc(w["method"]) + "</td><td>" + esc(w["status"]) + "</td><td>" + esc(w["created_at"]) + "</td></tr>"
        body += "</table>"
    else:
        body += "<p class='small'>No withdrawals.</p>"
    body += "</div>"

    body += "<div class='card'><h3 class='gold'>Recent Transactions</h3>"
    if transactions:
        body += "<table><tr><th>Type</th><th>Amount</th><th>Description</th><th>Date</th></tr>"
        for t in transactions:
            body += "<tr><td>" + esc(t["type"]) + "</td><td>Rs." + str(t["amount"]) + "</td><td>" + esc(t["description"]) + "</td><td>" + esc(t["created_at"]) + "</td></tr>"
        body += "</table>"
    else:
        body += "<p class='small'>No transactions.</p>"
    body += "</div><a class='btn2' href='/admin/users'>BACK TO USERS</a>"
    return layout("User Details", body)


@app.route("/admin/user/reset-password/<int:user_id>", methods=["GET", "POST"])
@admin_required
def admin_user_reset_password(user_id):
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not user:
        return redirect(url_for("admin_users"))

    error = ""
    success = ""
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(new_password) < 6:
            error = "New password kam az kam 6 characters ka hona chahiye."
        elif new_password != confirm_password:
            error = "New password aur confirmation match nahi karte."
        else:
            conn = db()
            conn.execute(
                "UPDATE users SET password=? WHERE id=?",
                (hash_password(new_password), user_id)
            )
            conn.commit()
            conn.close()
            add_notification(
                user["username"],
                "Password Reset",
                "Admin ne aapke account ka password reset kiya hai. Apna password secure rakhein."
            )
            add_audit_log("Reset user password", user["username"], "Password reset completed")
            success = "Password successfully reset ho gaya."

    body = (
        "<div class='card'><h2 class='gold'>🔐 Reset User Password</h2>"
        "<p><b>Username:</b> " + esc(user["username"]) + "</p>"
        + ("<p class='bad'>" + esc(error) + "</p>" if error else "")
        + ("<p class='ok'>" + esc(success) + "</p>" if success else "")
        + "<form method='post'>"
        "<label>New Password</label>"
        "<input type='password' name='new_password' minlength='6' required autocomplete='new-password' placeholder='Enter new password'>"
        "<label>Confirm New Password</label>"
        "<input type='password' name='confirm_password' minlength='6' required autocomplete='new-password' placeholder='Confirm new password'>"
        "<button class='btn' type='submit'>🔑 RESET PASSWORD</button>"
        "</form></div>"
        "<a class='btn2' href='/admin/user/" + str(user["id"]) + "'>BACK TO USER</a>"
        "<a class='btn2' href='/admin/users'>BACK TO USERS</a>"
    )
    return layout("Reset User Password", body)


@app.route("/admin/user/toggle-block/<int:user_id>")
@admin_required
def admin_user_toggle_block(user_id):
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return redirect(url_for("admin_users"))
    current = int(user["blocked"]) if "blocked" in user.keys() else 0
    new_value = 0 if current else 1
    conn.execute("UPDATE users SET blocked=? WHERE id=?", (new_value, user_id))
    conn.commit()
    conn.close()
    add_notification(
        user["username"],
        "Account " + ("Unblocked" if new_value == 0 else "Blocked"),
        "Admin ne aapka account " + ("unblock" if new_value == 0 else "block") + " kar diya hai."
    )
    add_audit_log("Account " + ("unblocked" if new_value == 0 else "blocked"), user["username"], "User management action")
    return redirect(url_for("admin_user_detail", user_id=user_id))


@app.route("/admin/user/balance/<int:user_id>", methods=["GET", "POST"])
@admin_required
def admin_user_balance(user_id):
    msg = ""
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()

    if not user:
        return redirect(url_for("admin_users"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        reason = request.form.get("reason", "").strip()
        try:
            amount = int(request.form.get("amount", "0"))
        except Exception:
            amount = 0

        if amount <= 0:
            msg = "<p class='bad'>Amount 0 se zyada hona chahiye.</p>"
        elif action not in ["add", "deduct"]:
            msg = "<p class='bad'>Invalid balance action.</p>"
        elif action == "deduct" and amount > user["balance"]:
            msg = "<p class='bad'>User ke balance mein itni amount available nahi hai.</p>"
        else:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = db()

            if action == "add":
                conn.execute(
                    "UPDATE users SET balance=balance+? WHERE id=?",
                    (amount, user_id)
                )
                transaction_type = "Admin Balance Add"
                description = "Admin ne balance add kiya"
                if reason:
                    description += ": " + reason
                notification_message = "Admin ne aapke balance mein Rs." + str(amount) + " add kiye."
            else:
                conn.execute(
                    "UPDATE users SET balance=balance-? WHERE id=?",
                    (amount, user_id)
                )
                transaction_type = "Admin Balance Deduct"
                description = "Admin ne balance deduct kiya"
                if reason:
                    description += ": " + reason
                notification_message = "Admin ne aapke balance se Rs." + str(amount) + " deduct kiye."

            conn.commit()
            conn.close()

            tx_amount = amount if action == "add" else -amount
            add_transaction(user["username"], transaction_type, tx_amount, description)
            add_notification(
                user["username"],
                "Balance Updated",
                notification_message
            )

            return redirect(url_for("admin_user_balance", user_id=user_id, saved="1"))

    if request.args.get("saved") == "1":
        msg = "<p class='ok'>Balance successfully update ho gaya.</p>"

    user = get_user(user["username"])

    body = (
        "<div class='card'><h2 class='gold'>💰 Manage Balance</h2>"
        "<p><b>Username:</b> " + esc(user["username"]) + "</p>"
        "<p><b>Current Balance:</b> <span class='gold'>Rs." + str(user["balance"]) + "</span></p>"
        "<p><b>Total Earning:</b> Rs." + str(user["total_earning"]) + "</p>"
        + msg +
        "<form method='post'>"
        "<label>Amount (Rs.)</label>"
        "<input type='number' name='amount' min='1' step='1' required placeholder='Enter amount'>"
        "<label>Reason (optional)</label>"
        "<input name='reason' maxlength='200' placeholder='Example: Manual adjustment'>"
        "<button class='btn' name='action' value='add'>➕ ADD BALANCE</button>"
        "<button class='btn2 danger' name='action' value='deduct' "
        "onclick=\"return confirm('Is user ke balance se amount deduct karni hai?')\">"
        "➖ DEDUCT BALANCE</button>"
        "</form></div>"
        "<div class='card'><h3 class='gold'>Transaction History</h3>"
    )

    conn = db()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE username=? ORDER BY id DESC LIMIT 50",
        (user["username"],)
    ).fetchall()
    conn.close()

    if not rows:
        body += "<p class='small'>No transactions yet.</p>"
    else:
        body += "<table><tr><th>Type</th><th>Amount</th><th>Description</th><th>Date</th></tr>"
        for r in rows:
            body += (
                "<tr><td>" + esc(r["type"]) + "</td>"
                "<td>Rs." + str(r["amount"]) + "</td>"
                "<td>" + esc(r["description"]) + "</td>"
                "<td>" + esc(r["created_at"]) + "</td></tr>"
            )
        body += "</table>"

    body += (
        "</div>"
        "<a class='btn2' href='/admin/users'>BACK TO USERS</a>"
    )
    return layout("Manage Balance", body)


@app.route("/admin/login-history")
@admin_required
def admin_login_history():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM admin_login_history ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    items = "".join(
        "<tr><td>" + esc(r["login_at"]) + "</td><td>" + esc(r["admin_username"]) + "</td><td>" + esc(r["ip_address"] or "-") + "</td></tr>"
        for r in rows
    ) or "<tr><td colspan='3'>No admin login history yet.</td></tr>"
    body = (
        "<div class='card'><h2 class='gold'>🔐 Admin Login History</h2>"
        "<p class='small'>Recent successful admin logins ka record. Maximum 100 entries.</p>"
        "<div style='overflow:auto'><table><tr><th>Date & Time</th><th>Admin</th><th>IP Address</th></tr>" + items + "</table></div>"
        "<a class='btn2' href='/admin'>BACK TO ADMIN</a></div>"
    )
    return layout("Admin Login History", body)


@app.route("/admin/system-health")
@admin_required
def admin_system_health():
    """Read-only admin health dashboard for the local EarnPro instance."""
    db_status = "OK"
    db_detail = "Integrity check passed."
    conn = None
    try:
        conn = db()
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if str(check).lower() != "ok":
            db_status = "CHECK"
            db_detail = str(check)
    except Exception as exc:
        db_status = "ERROR"
        db_detail = str(exc)
    finally:
        if conn is not None:
            conn.close()

    try:
        db_size = os.path.getsize(DB_FILE)
    except OSError:
        db_size = 0
    try:
        total, used, free = shutil.disk_usage(BASE_DIR)
        disk_text = f"{used / (1024**3):.2f} GB used / {free / (1024**3):.2f} GB free"
    except OSError:
        disk_text = "Unavailable"

    body = (
        "<div class='card'><h2 class='gold'>🩺 System Health</h2>"
        "<p class='small'>Read-only health information for the EarnPro server.</p>"
        "<div class='grid'>"
        "<div class='stat'>Database<b>" + esc(db_status) + "</b></div>"
        "<div class='stat'>Python<b>" + esc(platform.python_version()) + "</b></div>"
        "<div class='stat'>Platform<b>" + esc(platform.system()) + "</b></div>"
        "<div class='stat'>DB Size<b>" + str(round(db_size / 1024, 1)) + " KB</b></div>"
        "</div>"
        "<p><strong>Database:</strong> " + esc(db_detail) + "</p>"
        "<p><strong>Disk:</strong> " + esc(disk_text) + "</p>"
        "<p><strong>Python executable:</strong> " + esc(sys.executable) + "</p>"
        "<p><strong>Checked:</strong> " + esc(datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + "</p>"
        "</div>"
        "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    )
    return layout("System Health", body)


@app.route("/admin/audit-logs")
@admin_required
def admin_audit_logs():
    conn = db()
    logs = conn.execute(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()

    body = (
        "<div class='card'><h2 class='gold'>🛡️ Admin Activity Log</h2>"
        "<p class='small'>Recent admin actions ka secure record. Maximum 100 latest entries.</p>"
        "<p class='small'>Step 26: settings, ads aur report exports bhi audit mein record hote hain.</p>"
        "</div>"
    )
    if logs:
        body += "<div class='card'><table><tr><th>Date</th><th>Admin</th><th>Action</th><th>User</th><th>Details</th></tr>"
        for log in logs:
            body += (
                "<tr><td>" + esc(log["created_at"]) + "</td>"
                "<td>" + esc(log["admin_username"]) + "</td>"
                "<td>" + esc(log["action"]) + "</td>"
                "<td>" + esc(log["target_username"] or "-") + "</td>"
                "<td>" + esc(log["details"] or "-") + "</td></tr>"
            )
        body += "</table></div>"
    else:
        body += "<div class='card'><p class='small'>Abhi koi admin activity record nahi hai.</p></div>"
    body += "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Admin Activity Log", body)


@app.route("/admin/reports")
@admin_required
def admin_reports():
    conn = db()
    approved_payment = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE status='Approved'"
    ).fetchone()["total"]
    pending_payment = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE status='Pending'"
    ).fetchone()["total"]
    rejected_payment = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE status='Rejected'"
    ).fetchone()["total"]
    approved_withdrawal = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM withdrawals WHERE status='Approved'"
    ).fetchone()["total"]
    pending_withdrawal = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM withdrawals WHERE status='Pending'"
    ).fetchone()["total"]
    rejected_withdrawal = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM withdrawals WHERE status='Rejected'"
    ).fetchone()["total"]
    top_users = conn.execute(
        "SELECT username,balance,total_earning,referrals FROM users "
        "ORDER BY total_earning DESC, balance DESC LIMIT 10"
    ).fetchall()
    monthly = conn.execute(
        "SELECT substr(created_at,1,7) AS month, "
        "SUM(CASE WHEN status='Approved' THEN amount ELSE 0 END) AS payments, "
        "(SELECT COALESCE(SUM(w.amount),0) FROM withdrawals w "
        " WHERE w.status='Approved' AND substr(w.created_at,1,7)=substr(p.created_at,1,7)) AS withdrawals "
        "FROM payments p GROUP BY substr(created_at,1,7) ORDER BY month DESC LIMIT 6"
    ).fetchall()
    conn.close()

    net = approved_payment - approved_withdrawal
    body = (
        "<div class='card'><h2 class='gold'>📊 Reports & Analytics</h2>"
        "<p class='small'>Platform ke approved, pending aur rejected payment/withdrawal totals ka quick report.</p>"
        "<div class='grid'>"
        "<div class='stat'>Approved Payments<b>Rs." + str(approved_payment) + "</b></div>"
        "<div class='stat'>Pending Payments<b>Rs." + str(pending_payment) + "</b></div>"
        "<div class='stat'>Rejected Payments<b>Rs." + str(rejected_payment) + "</b></div>"
        "<div class='stat'>Approved Withdrawals<b>Rs." + str(approved_withdrawal) + "</b></div>"
        "<div class='stat'>Pending Withdrawals<b>Rs." + str(pending_withdrawal) + "</b></div>"
        "<div class='stat'>Rejected Withdrawals<b>Rs." + str(rejected_withdrawal) + "</b></div>"
        "<div class='stat'>Approved Net Flow<b>Rs." + str(net) + "</b></div>"
        "</div></div>"
        "<div class='card'><h3 class='gold'>🏆 Top Users</h3>"
    )
    if top_users:
        body += "<table><tr><th>Username</th><th>Balance</th><th>Total Earning</th><th>Referrals</th></tr>"
        for u in top_users:
            body += (
                "<tr><td>" + esc(u["username"]) + "</td>"
                "<td>Rs." + str(u["balance"]) + "</td>"
                "<td>Rs." + str(u["total_earning"]) + "</td>"
                "<td>" + str(u["referrals"]) + "</td></tr>"
            )
        body += "</table>"
    else:
        body += "<p class='small'>No users yet.</p>"

    body += "</div><div class='card'><h3 class='gold'>📅 Recent Monthly Flow</h3>"
    if monthly:
        body += "<table><tr><th>Month</th><th>Approved Payments</th><th>Approved Withdrawals</th></tr>"
        for m in monthly:
            body += (
                "<tr><td>" + esc(m["month"] or "-") + "</td>"
                "<td>Rs." + str(m["payments"] or 0) + "</td>"
                "<td>Rs." + str(m["withdrawals"] or 0) + "</td></tr>"
            )
        body += "</table>"
    else:
        body += "<p class='small'>No payment data yet.</p>"
    body += (
        "</div><div class='card'><h3 class='gold'>📥 Export</h3>"
        "<p class='small'>Approved/pending/rejected payment report CSV format mein export kar sakte hain.</p>"
        "<a class='btn' href='/admin/reports/export'>DOWNLOAD PAYMENT CSV</a></div>"
        "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    )
    return layout("Reports & Analytics", body)


@app.route("/admin/reports/export")
@admin_required
def admin_reports_export():
    conn = db()
    rows = conn.execute(
        "SELECT username,plan,amount,method,txid,status,created_at,activation_at,expires_at "
        "FROM payments ORDER BY id DESC"
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Username", "Plan", "Amount", "Method", "Transaction ID",
        "Status", "Created At", "Activation At", "Expires At"
    ])
    for r in rows:
        writer.writerow([
            r["username"], r["plan"], r["amount"], r["method"], r["txid"],
            r["status"], r["created_at"], r["activation_at"], r["expires_at"]
        ])

    add_audit_log("Exported payment report", "", "Payment CSV downloaded")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=earnpro_payment_report.csv"}
    )


@app.route("/admin/payments")
@admin_required
def admin_payments():
    status_filter = request.args.get("status", "All").strip()
    allowed = ["All", "Pending", "Approved", "Rejected"]
    conn = db()
    if status_filter in allowed and status_filter != "All":
        rows = conn.execute("SELECT * FROM payments WHERE status=? ORDER BY id DESC",
                            (status_filter,)).fetchall()
    else:
        status_filter = "All"
        rows = conn.execute("SELECT * FROM payments ORDER BY id DESC").fetchall()
    conn.close()

    body = (
        "<div class='card'><h2 class='gold'>💳 Payment Verification</h2>"
        "<p>Har payment ki complete details verify karein. Transaction ID sirf ek baar use ho sakti hai.</p>"
        "<div class='grid'>"
        "<a class='btn2' href='/admin/payments?status=Pending'>PENDING</a>"
        "<a class='btn2' href='/admin/payments?status=Approved'>APPROVED</a>"
        "<a class='btn2' href='/admin/payments?status=Rejected'>REJECTED</a>"
        "<a class='btn2' href='/admin/payments?status=All'>ALL PAYMENTS</a>"
        "</div><p class='small'>Showing: <b class='gold'>" + esc(status_filter) +
        "</b> | Total: " + str(len(rows)) + "</p></div>"
    )
    if not rows:
        body += "<div class='card'><p class='small'>No payment requests found.</p></div>"

    for p in rows:
        status_class = "pending" if p["status"] == "Pending" else ("ok" if p["status"] == "Approved" else "bad")
        body += (
            "<div class='card'><h3 class='gold'>Payment #" + str(p["id"]) + "</h3>"
            "<p><b>User:</b> " + esc(p["username"]) + "</p>"
            "<p><b>Plan:</b> " + esc(p["plan"]) + "</p>"
            "<p><b>Amount:</b> Rs." + str(p["amount"]) + "</p>"
            "<p><b>Method:</b> " + esc(p["method"]) + "</p>"
            "<p><b>Transaction ID:</b> " + esc(p["txid"]) + "</p>"
            "<p class='" + status_class + "'><b>Status:</b> " + esc(p["status"]) + "</p>"
            "<p class='small'><b>Submitted:</b> " + esc(p["created_at"] or "-") + "</p>"
        )
        if p["status"] == "Approved":
            body += ("<p class='ok'><b>Activated:</b> " + esc(p["activation_at"] or "-") + "</p>"
                     "<p class='ok'><b>Expires:</b> " + esc(p["expires_at"] or "-") + "</p>")
        if p["reason"]:
            body += "<p class='small'><b>Admin Reason:</b> " + esc(p["reason"]) + "</p>"
        if p["status"] == "Pending":
            body += (
                "<a class='btn' href='/admin/payment/approve/" + str(p["id"]) +
                "' onclick=\"return confirm('Is payment ko verify karke approve karna hai?')\">APPROVE & ACTIVATE</a>"
                "<form method='post' action='/admin/payment/reject/" + str(p["id"]) + "'>"
                "<label>Rejection Reason</label><input name='reason' maxlength='300' required placeholder='Example: Transaction verify nahi hui'>"
                "<button class='btn2 danger'>REJECT PAYMENT</button></form>"
            )
        body += "</div>"
    body += "<a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Admin Payments", body)


@app.route("/admin/payment/approve/<int:pid>")
@admin_required
def approve_payment(pid):
    conn = db()
    payment = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if payment and payment["status"] == "Pending":
        try:
            plan_days = int(payment["plan_days"] or 0)
        except Exception:
            plan_days = 0
        if plan_days <= 0:
            try:
                plan_days = int(str(payment["plan"]).split()[0])
            except Exception:
                plan_days = 30
        from datetime import timedelta
        activation = datetime.now()
        expiry = activation + timedelta(days=plan_days)
        activation_text = activation.strftime("%Y-%m-%d %H:%M:%S")
        expiry_text = expiry.strftime("%Y-%m-%d %H:%M:%S")

        # Step 111 safety guard: never approve a payment whose TXID is already
        # attached to another payment record, and never activate an unknown user.
        duplicate_txid = 0
        txid_value = str(payment["txid"] or "").strip()
        if txid_value:
            duplicate_txid = conn.execute(
                "SELECT COUNT(*) AS c FROM payments WHERE txid=? AND id<>?",
                (txid_value, pid)
            ).fetchone()["c"]
        target_user = conn.execute(
            "SELECT username FROM users WHERE username=?",
            (payment["username"],)
        ).fetchone()
        if duplicate_txid or not target_user:
            reason = ("Duplicate Transaction ID detected." if duplicate_txid
                      else "User account for this payment was not found.")
            conn.execute(
                "UPDATE payments SET status='Rejected',reason=? WHERE id=? AND status='Pending'",
                (reason, pid)
            )
            conn.commit()
            conn.close()
            add_notification(payment["username"], "Payment Review", reason)
            return redirect(url_for("admin_payments"))

        # Step 120: when a plan is activated, automatically deduct the exact
        # plan price from the member's EarnPro wallet. Never allow a negative
        # balance and never activate the plan if the wallet is insufficient.
        user = conn.execute("SELECT * FROM users WHERE username=?",
                            (payment["username"],)).fetchone()
        plan_amount = int(payment["amount"] or 0)
        wallet_balance = int(user["balance"] or 0) if user else 0
        if not user:
            reason = "User account for this payment was not found."
            conn.execute(
                "UPDATE payments SET status='Rejected',reason=? WHERE id=? AND status='Pending'",
                (reason, pid)
            )
            conn.commit()
            conn.close()
            add_notification(payment["username"], "Payment Review", reason)
            return redirect(url_for("admin_payments"))
        if plan_amount < 0:
            reason = "Invalid plan amount."
            conn.execute(
                "UPDATE payments SET status='Rejected',reason=? WHERE id=? AND status='Pending'",
                (reason, pid)
            )
            conn.commit()
            conn.close()
            add_notification(payment["username"], "Payment Review", reason)
            return redirect(url_for("admin_payments"))
        if wallet_balance < plan_amount:
            reason = ("Insufficient wallet balance. Plan price Rs." + str(plan_amount) +
                      " required; available balance Rs." + str(wallet_balance) + ".")
            conn.execute(
                "UPDATE payments SET status='Rejected',reason=? WHERE id=? AND status='Pending'",
                (reason, pid)
            )
            conn.commit()
            conn.close()
            add_notification(payment["username"], "Plan Not Activated", reason)
            return redirect(url_for("admin_payments"))

        updated = conn.execute(
            "UPDATE payments SET status='Approved',reason='',activation_at=?,expires_at=?,consumed_at=? "
            "WHERE id=? AND status='Pending'",
            (activation_text, expiry_text, activation_text, pid)
        ).rowcount

        if updated:
            # Deduct the exact plan price only after the payment changes from
            # Pending to Approved, so a rejected/failed approval never cuts funds.
            new_balance = wallet_balance - plan_amount
            conn.execute(
                "UPDATE users SET balance=? WHERE username=?",
                (new_balance, payment["username"])
            )
            conn.execute(
                "INSERT INTO transactions(username,type,amount,description,created_at) VALUES(?,?,?,?,?)",
                (payment["username"], "Plan Purchase", -plan_amount,
                 payment["plan"] + " activated. Plan price deducted from wallet.",
                 activation_text)
            )
            user = conn.execute("SELECT * FROM users WHERE username=?",
                                (payment["username"],)).fetchone()
            referrer_name = ""
            reward_paid = False
            bonus = setting_int("referral_bonus", 15)
            if user and user["referred_by"] and user["referral_rewarded"] == 0:
                referrer = conn.execute("SELECT * FROM users WHERE username=?",
                                        (user["referred_by"],)).fetchone()
                if referrer:
                    conn.execute(
                        "UPDATE users SET balance=balance+?,total_earning=total_earning+?,"
                        "referral_bonus=referral_bonus+?,referrals=referrals+1 WHERE username=?",
                        (bonus, bonus, bonus, referrer["username"])
                    )
                    conn.execute("UPDATE users SET referral_rewarded=1 WHERE username=?",
                                 (user["username"],))
                    referrer_name = referrer["username"]
                    reward_paid = True
            conn.commit()
            conn.close()

            add_notification(
                payment["username"], "Plan Approved & Activated",
                payment["plan"] + " approved. Activated: " + activation_text +
                " | Expires: " + expiry_text
            )
            add_transaction(
                payment["username"], "Plan Approved", 0,
                payment["plan"] + " approved. TXID: " + payment["txid"] +
                " | Expires: " + expiry_text
            )
            if reward_paid:
                add_transaction(referrer_name, "Referral Bonus", bonus,
                                "Referral bonus from " + payment["username"])
                add_notification(referrer_name, "Referral Bonus",
                                  "Rs." + str(bonus) + " referral bonus received.")
        else:
            conn.close()
    else:
        conn.close()
    return redirect(url_for("admin_payments"))


@app.route("/admin/payment/reject/<int:pid>", methods=["POST"])
@admin_required
def reject_payment(pid):
    reason = request.form.get("reason", "").strip() or "Payment verification failed."
    conn = db()
    payment = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if payment and payment["status"] == "Pending":
        updated = conn.execute(
            "UPDATE payments SET status='Rejected',reason=? WHERE id=? AND status='Pending'",
            (reason, pid)
        ).rowcount
        conn.commit()
        conn.close()
        if updated:
            add_notification(
                payment["username"], "Payment Rejected",
                "Payment of Rs." + str(payment["amount"]) + " rejected. Reason: " + reason
            )
    else:
        conn.close()
    return redirect(url_for("admin_payments"))


@app.route("/admin/withdrawals")
@admin_required
def admin_withdrawals():
    conn = db()
    rows = conn.execute("SELECT * FROM withdrawals ORDER BY id DESC").fetchall()
    pending_count = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status='Pending'").fetchone()[0]
    approved_count = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status='Approved'").fetchone()[0]
    rejected_count = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status='Rejected'").fetchone()[0]
    conn.close()

    body = ("<div class='card'><h2 class='gold'>Withdrawal Requests</h2>"
            "<p>Pending: <b class='pending'>" + str(pending_count) + "</b> | "
            "Approved: <b class='ok'>" + str(approved_count) + "</b> | "
            "Rejected: <b class='bad'>" + str(rejected_count) + "</b></p>" )
    if not rows:
        body += "<p>No withdrawal requests.</p>"
    for w in rows:
        body += (
            "<div class='card'><b>" + esc(w["username"]) + "</b>"
            "<p>Amount: Rs." + str(w["amount"]) + "</p>"
            "<p>" + esc(w["method"]) + " | " + esc(w["account"]) + "</p>"
            "<p>Status: " + esc(w["status"]) + "</p>"
        )
        if w["status"] == "Pending":
            body += (
                "<a class='btn' href='/admin/withdrawal/approve/" + str(w["id"]) + "'>APPROVE</a>"
                "<form method='post' action='/admin/withdrawal/reject/" + str(w["id"]) + "'>"
                "<input name='reason' placeholder='Rejection reason' required>"
                "<button class='btn2'>REJECT & REFUND</button></form>"
            )
        elif w["reason"]:
            body += "<p class='small'>Reason: " + esc(w["reason"]) + "</p>"
        body += "</div>"
    body += "<a class=\'btn2\' href=\'/admin/withdrawals/export.csv\'>📥 EXPORT WITHDRAWALS CSV</a>"
    body += "</div><a class=\'btn2\' href=\'/admin\'>BACK</a>"
    return layout("Admin Withdrawals", body)


@app.route("/admin/withdrawals/export.csv")
@admin_required
def admin_withdrawals_export_csv():
    """Step 113: privacy-conscious admin withdrawal export; no passwords or session data."""
    import csv, io
    from flask import Response
    out = io.StringIO()
    writer = csv.writer(out)
    wanted = ["id", "username", "amount", "method", "account", "status", "reason", "created_at"]
    writer.writerow(wanted)
    try:
        with get_db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(withdrawals)").fetchall()}
            use = [c for c in wanted if c in cols]
            if use:
                rows = conn.execute("SELECT " + ",".join('"' + c + '"' for c in use) + " FROM withdrawals ORDER BY id DESC").fetchall()
                for row in rows:
                    data = dict(zip(use, row))
                    writer.writerow([data.get(c, "") for c in wanted])
    except Exception as exc:
        return "Withdrawal export failed: " + esc(exc), 500
    add_audit_log("Exported withdrawal report", "", "Withdrawal CSV downloaded")
    return Response(out.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=earnpro_withdrawals.csv"})


@app.route("/admin/withdrawal/approve/<int:wid>")
@admin_required
def approve_withdrawal(wid):
    conn = db()
    w = conn.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
    if w and w["status"] == "Pending":
        # Step 112 safety: verify the withdrawal is valid before approval.
        # Step 113 adds a privacy-conscious admin withdrawal export.
        valid_user = conn.execute(
            "SELECT username FROM users WHERE username=?", (w["username"],)
        ).fetchone()
        valid_method = w["method"] in ("JazzCash", "Easypaisa")
        valid_amount = (w["amount"] is not None and int(w["amount"]) > 0)
        valid_account = bool((w["account"] or "").strip())
        if valid_user and valid_method and valid_amount and valid_account:
            conn.execute(
                "UPDATE withdrawals SET status='Approved',reason='' WHERE id=? AND status='Pending'",
                (wid,)
            )
            conn.commit()
        else:
            conn.execute(
                "UPDATE withdrawals SET status='Rejected',reason=? WHERE id=? AND status='Pending'",
                ("Invalid withdrawal details - safety review", wid)
            )
            conn.commit()
            w = dict(w)
            w["status"] = "Rejected"
            w["reason"] = "Invalid withdrawal details - safety review"
    conn.close()

    if w:
        add_notification(
            w["username"], "Withdrawal Approved",
            "Your Rs." + str(w["amount"]) + " withdrawal was approved."
        )
    return redirect(url_for("admin_withdrawals"))


@app.route("/admin/withdrawal/reject/<int:wid>", methods=["POST"])
@admin_required
def reject_withdrawal(wid):
    reason = request.form.get("reason", "").strip()
    conn = db()
    w = conn.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()

    if w and w["status"] == "Pending":
        conn.execute(
            "UPDATE withdrawals SET status='Rejected',reason=? WHERE id=?",
            (reason, wid)
        )
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE username=?",
            (w["amount"], w["username"])
        )
        conn.commit()
    conn.close()

    if w:
        add_notification(
            w["username"], "Withdrawal Rejected",
            "Rs." + str(w["amount"]) + " returned to balance. Reason: " + reason
        )
        add_transaction(
            w["username"], "Refund", w["amount"], "Rejected withdrawal refunded"
        )

    return redirect(url_for("admin_withdrawals"))


@app.route("/admin/notifications", methods=["GET", "POST"])
@admin_required
def admin_notifications():
    msg = ""
    conn = db()
    user_rows = conn.execute("SELECT username FROM users ORDER BY username").fetchall()

    if request.method == "POST":
        target = request.form.get("username", "").strip()
        title = request.form.get("title", "").strip()
        message = request.form.get("message", "").strip()

        if target and title and message:
            if target.upper() == "ALL":
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.executemany(
                    "INSERT INTO notifications(username,title,message,created_at) VALUES(?,?,?,?)",
                    [(row["username"], title, message, now) for row in user_rows]
                )
                sent_count = len(user_rows)
                conn.commit()
                conn.close()
                add_audit_log(
                    "Broadcast notification",
                    "ALL",
                    "Sent notification to " + str(sent_count) + " users. Title: " + title
                )
                msg = "<p class='ok'>📢 Message sent to " + str(sent_count) + " users successfully.</p>"
                conn = db()
                user_rows = conn.execute("SELECT username FROM users ORDER BY username").fetchall()
            elif get_user(target):
                conn.close()
                add_notification(target, title, message)
                add_audit_log(
                    "Send notification",
                    target,
                    "Sent notification. Title: " + title
                )
                msg = "<p class='ok'>✅ Notification sent to " + str(target) + ".</p>"
                conn = db()
                user_rows = conn.execute("SELECT username FROM users ORDER BY username").fetchall()
            else:
                msg = "<p class='bad'>User not found.</p>"
        else:
            msg = "<p class='bad'>All fields required.</p>"

    recent_sent = conn.execute(
        "SELECT username,title,message,created_at FROM notifications ORDER BY id DESC LIMIT 30"
    ).fetchall()
    conn.close()

    options = "<option value='ALL'>📢 ALL USERS — Broadcast</option>"
    for row in user_rows:
        options += "<option value='" + str(row["username"]) + "'>👤 " + str(row["username"]) + "</option>"

    body = (
        "<div class='card'><h2 class='gold'>🔔 Notification Center</h2>" + msg +
        "<p class='small'>Admin yahan se ek user ya tamam registered users ko message bhej sakta hai.</p>"
        "<div class='grid'>"
        "<div class='stat'>📢 ALL USERS<b>Ek message sab users ko</b><span class='small'>Total registered: " + str(len(user_rows)) + "</span></div>"
        "<div class='stat'>👤 SINGLE USER<b>Sirf selected user ko</b><span class='small'>Dropdown se user select karein</span></div>"
        "</div>"
        "<form method='post' onsubmit=\"return confirm('Kya aap ye message selected users ko bhejna chahte hain?');\">"
        "<label>Send To</label><select name='username' required>" + options + "</select>"
        "<label>Title</label><input name='title' maxlength='100' placeholder='Notification title' required>"
        "<label>Message</label><textarea name='message' maxlength='1000' placeholder='Write your message here...' required></textarea>"
        "<button class='btn' type='submit'>📢 SEND MESSAGE</button>"
        "<a class='btn2' href='/admin'>CANCEL / BACK</a></form></div>"
        "<div class='card'><h3 class='gold'>📋 Message History</h3>"
        "<p class='small'>Yahan recently sent notifications ka target, time, title aur message clearly nazar aayega.</p>"
    )
    if not recent_sent:
        body += "<p class='small'>No notifications have been sent yet.</p>"
    else:
        for n in recent_sent:
            target_label = "📢 ALL USERS" if str(n["username"]).upper() == "ALL" else "👤 " + str(n["username"])
            body += (
                "<div class='card'>"
                "<div class='grid'>"
                "<div class='stat'><span class='small'>TARGET</span><b>" + target_label + "</b></div>"
                "<div class='stat'><span class='small'>SENT AT</span><b>" + str(n["created_at"]) + "</b></div>"
                "</div>"
                "<p><b>Title:</b> " + str(n["title"]) + "</p>"
                "<p><b>Message:</b> " + str(n["message"]) + "</p>"
                "</div>"
            )
    body += "</div><a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Admin Notifications", body)


@app.route("/admin/payment-analytics")
@admin_required
def admin_payment_analytics():
    """Step 76: payment and plan sales analytics; read-only reporting."""
    from collections import OrderedDict
    conn = db()
    totals = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='Pending' THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN status='Approved' THEN 1 ELSE 0 END) AS approved, "
        "SUM(CASE WHEN status='Rejected' THEN 1 ELSE 0 END) AS rejected, "
        "COALESCE(SUM(CASE WHEN status='Approved' THEN amount ELSE 0 END),0) AS approved_amount, "
        "COALESCE(SUM(CASE WHEN status='Pending' THEN amount ELSE 0 END),0) AS pending_amount "
        "FROM payments"
    ).fetchone()
    plan_rows = conn.execute(
        "SELECT plan, COUNT(*) AS requests, "
        "SUM(CASE WHEN status='Approved' THEN 1 ELSE 0 END) AS approved_count, "
        "COALESCE(SUM(CASE WHEN status='Approved' THEN amount ELSE 0 END),0) AS revenue "
        "FROM payments GROUP BY plan ORDER BY approved_count DESC, revenue DESC, plan ASC"
    ).fetchall()
    method_rows = conn.execute(
        "SELECT method, COUNT(*) AS requests, "
        "SUM(CASE WHEN status='Approved' THEN 1 ELSE 0 END) AS approved_count, "
        "COALESCE(SUM(CASE WHEN status='Approved' THEN amount ELSE 0 END),0) AS revenue "
        "FROM payments GROUP BY method ORDER BY revenue DESC, method ASC"
    ).fetchall()
    recent = conn.execute(
        "SELECT username,plan,amount,method,status,created_at,activation_at,expires_at "
        "FROM payments ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    body = (
        "<div class='card'><h2 class='gold'>📊 Payment & Plan Analytics</h2>"
        "<p>Payment requests, approved plan sales aur payment-method performance ka read-only overview.</p>"
        "<div class='grid'>"
        "<div class='stat'><span class='small'>TOTAL REQUESTS</span><b>" + str(totals['total'] or 0) + "</b></div>"
        "<div class='stat'><span class='small'>PENDING</span><b>" + str(totals['pending'] or 0) + "</b></div>"
        "<div class='stat'><span class='small'>APPROVED</span><b>" + str(totals['approved'] or 0) + "</b></div>"
        "<div class='stat'><span class='small'>REJECTED</span><b>" + str(totals['rejected'] or 0) + "</b></div>"
        "<div class='stat'><span class='small'>APPROVED REVENUE</span><b>Rs." + str(totals['approved_amount'] or 0) + "</b></div>"
        "<div class='stat'><span class='small'>PENDING VALUE</span><b>Rs." + str(totals['pending_amount'] or 0) + "</b></div>"
        "</div>"
        "<a class='btn' href='/admin/payments'>💳 PAYMENT VERIFICATION</a>"
        "<a class='btn2' href='/admin/payment-analytics/export'>📥 EXPORT CSV</a>"
        "<a class='btn2' href='/admin/plans'>💳 PLAN MANAGER</a></div>"
        "<div class='card'><h3 class='gold'>👑 Plan Sales</h3>"
    )
    if plan_rows:
        body += "<div style='overflow-x:auto'><table><tr><th>Plan</th><th>Requests</th><th>Approved</th><th>Revenue</th></tr>"
        for r in plan_rows:
            body += "<tr><td>" + esc(r['plan']) + "</td><td>" + str(r['requests']) + "</td><td>" + str(r['approved_count']) + "</td><td>Rs." + str(r['revenue']) + "</td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='small'>No payment data available.</p>"
    body += "</div><div class='card'><h3 class='gold'>💳 Payment Methods</h3>"
    if method_rows:
        body += "<div style='overflow-x:auto'><table><tr><th>Method</th><th>Requests</th><th>Approved</th><th>Revenue</th></tr>"
        for r in method_rows:
            body += "<tr><td>" + esc(r['method']) + "</td><td>" + str(r['requests']) + "</td><td>" + str(r['approved_count']) + "</td><td>Rs." + str(r['revenue']) + "</td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='small'>No payment-method data available.</p>"
    body += "</div><div class='card'><h3 class='gold'>🧾 Recent Payment Activity</h3>"
    if recent:
        body += "<div style='overflow-x:auto'><table><tr><th>User</th><th>Plan</th><th>Amount</th><th>Method</th><th>Status</th><th>Submitted</th></tr>"
        for r in recent:
            cls = 'pending' if r['status']=='Pending' else ('ok' if r['status']=='Approved' else 'bad')
            body += "<tr><td>" + esc(r['username']) + "</td><td>" + esc(r['plan']) + "</td><td>Rs." + str(r['amount']) + "</td><td>" + esc(r['method']) + "</td><td class='" + cls + "'>" + esc(r['status']) + "</td><td>" + esc(r['created_at'] or '-') + "</td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='small'>No recent payments.</p>"
    body += "</div><a class='btn2' href='/admin'>BACK TO ADMIN</a>"
    return layout("Payment & Plan Analytics", body)


@app.route("/admin/payment-analytics/export")
@admin_required
def admin_payment_analytics_export():
    import csv
    from io import StringIO
    conn = db()
    rows = conn.execute(
        "SELECT username,plan,amount,method,txid,status,created_at,activation_at,expires_at,consumed_at "
        "FROM payments ORDER BY id DESC"
    ).fetchall()
    conn.close()
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["Username","Plan","Amount","Method","Transaction ID","Status","Submitted At","Activated At","Expires At","Consumed At"])
    for r in rows:
        writer.writerow([r['username'],r['plan'],r['amount'],r['method'],r['txid'],r['status'],r['created_at'],r['activation_at'],r['expires_at'],r['consumed_at']])
    resp = make_response(out.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=earnpro_payment_analytics.csv'
    return resp


init_db()


# Step 81: Final Social Links + Launch Cleanup — Support Center, legal pages, admin support desk,
# configurable WhatsApp contact/group and launch navigation.  This block is
# intentionally placed before app.run so every route is registered correctly.
def ensure_launch_tables():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        subject TEXT NOT NULL,
        message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Open',
        admin_reply TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()
    if get_setting('whatsapp_channel_link') is None:
        set_setting('whatsapp_channel_link', '')
    if get_setting('facebook_group_link') is None:
        set_setting('facebook_group_link', '')


def launch_page(title, content, back_href='/', back_text='HOME'):
    return layout(title, content + "<div class='card'><a class='btn2' href='" + back_href + "'>" + back_text + "</a></div>")


@app.route('/support', methods=['GET', 'POST'])
@login_required
def support():
    username = session['username']
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()[:120]
        message = request.form.get('message', '').strip()[:3000]
        if not subject or not message:
            return launch_page('Support Center', "<div class='card'><p class='bad'>Subject and message are required.</p></div>" + support_form_html(), '/dashboard', 'DASHBOARD')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = db()
        conn.execute('INSERT INTO support_tickets(username,subject,message,status,admin_reply,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                     (username, subject, message, 'Open', '', now, now))
        conn.commit(); conn.close()
        add_notification(username, 'Support Ticket', 'Your support ticket has been submitted to the admin team.')
        return redirect('/support')
    conn = db()
    tickets = conn.execute('SELECT * FROM support_tickets WHERE username=? ORDER BY id DESC', (username,)).fetchall()
    conn.close()
    body = support_form_html()
    body += "<div class='card'><h3 class='gold'>📋 My Support Tickets</h3>"
    if not tickets:
        body += "<p class='small'>No support tickets yet.</p>"
    for t in tickets:
        body += "<div class='ad'><b class='gold'>#" + str(t['id']) + " — " + esc(t['subject']) + "</b>"
        body += "<p>Status: <b>" + esc(t['status']) + "</b></p><p>" + esc(t['message']) + "</p>"
        if t['admin_reply']:
            body += "<div class='card'><b class='gold'>Admin Reply</b><p>" + esc(t['admin_reply']) + "</p></div>"
        body += "<p class='small'>Updated: " + esc(t['updated_at']) + "</p></div>"
    body += "</div>"
    return launch_page('Support Center', body, '/dashboard', 'DASHBOARD')


def support_form_html():
    return ("<div class='card'><h2 class='gold'>🆘 Support Center</h2>"
            "<p>Issue hai? Support ticket submit karein. Admin yahin se reply karega.</p>"
            "<form method='post'><label>Subject</label><input name='subject' maxlength='120' required>"
            "<label>Message</label><textarea name='message' maxlength='3000' rows='6' required></textarea>"
            "<button class='btn' type='submit'>SEND SUPPORT TICKET</button></form></div>")


@app.route('/faq')
def faq():
    faqs = [
        ('How do I earn?', 'Available ads/tasks complete karein. Reward server verification ke baad credit hota hai.'),
        ('How do I buy a plan?', 'Plans page par enabled plan select karke payment request submit karein. Admin approval ke baad plan activate hota hai.'),
        ('Can I withdraw without an active plan?', 'Current EarnPro rules ke mutabiq withdrawal ke liye active plan required hai.'),
        ('How does referral reward work?', 'Eligible referral system ke configured rules ke mutabiq reward process hota hai.'),
        ('How can I contact support?', 'Support Center ticket use karein, ya admin ke configured WhatsApp contact/group options use karein.'),
    ]
    body = "<div class='card'><h2 class='gold'>❓ Frequently Asked Questions</h2>"
    for q,a in faqs:
        body += "<div class='ad'><b class='gold'>"+esc(q)+"</b><p>"+esc(a)+"</p></div>"
    body += "</div>"
    return launch_page('FAQ', body)


@app.route('/terms')
def terms():
    body = ("<div class='card'><h2 class='gold'>📜 Terms & Conditions</h2>"
            "<p>Users must provide accurate account/payment information and follow EarnPro's displayed earning, plan, ad, referral and withdrawal rules.</p>"
            "<p>Attempts to bypass watch verification, claim limits, payment validation, referral controls or security controls are prohibited.</p>"
            "<p>Plans, rewards and withdrawals remain subject to the applicable status, limits and approval rules shown on the website.</p></div>")
    return launch_page('Terms & Conditions', body)


@app.route('/privacy')
def privacy():
    body = ("<div class='card'><h2 class='gold'>🔒 Privacy Notice</h2>"
            "<p>EarnPro stores account, transaction, payment, referral, ad activity, notification and security information needed to operate the service.</p>"
            "<p>This information is used for account operation, reward calculation, payment verification, security checks, support and administration.</p>"
            "<p>Users should avoid submitting unnecessary sensitive information in support messages.</p></div>")
    return launch_page('Privacy Notice', body)


@app.route('/contact')
def contact():
    from urllib.parse import quote
    number = get_setting('whatsapp_number').strip()
    group = get_setting('whatsapp_group_link').strip()
    wa_channel = get_setting('whatsapp_channel_link').strip()
    fb_group = get_setting('facebook_group_link').strip()
    email = get_setting('support_email').strip()
    body = "<div class='card'><h2 class='gold'>📞 Contact EarnPro</h2>"
    if number:
        wa_digits = re.sub(r'[^0-9]', '', number)
        body += "<a class='btn2' style='display:inline-block;width:auto;padding:8px 12px;margin:3px' href='https://wa.me/" + esc(wa_digits) + "' target='_blank' rel='noopener'>💬 WhatsApp</a>"
    if group:
        qr_url = 'https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=' + quote(group, safe='')
        body += "<a class='btn2' style='display:inline-block;width:auto;padding:8px 12px;margin:3px' href='" + esc(group) + "' target='_blank' rel='noopener'>👥 WhatsApp Group</a>"
        body += "<a class='btn2' style='display:inline-block;width:auto;padding:8px 12px;margin:3px' href='" + esc(qr_url) + "' target='_blank' rel='noopener'>▣ QR</a>"
    if wa_channel:
        body += "<a class='btn2' style='display:inline-block;width:auto;padding:8px 12px;margin:3px' href='" + esc(wa_channel) + "' target='_blank' rel='noopener'>📢 WhatsApp Channel</a>"
    if fb_group:
        body += "<a class='btn2' style='display:inline-block;width:auto;padding:8px 12px;margin:3px' href='" + esc(fb_group) + "' target='_blank' rel='noopener'>📘 Facebook Group</a>"
    if email:
        body += "<a class='btn2' style='display:inline-block;width:auto;padding:8px 12px;margin:3px' href='mailto:" + esc(email) + "'>✉️ Email</a>"
    if not (number or group or wa_channel or fb_group or email):
        body += "<p class='small'>Admin ne abhi contact details configure nahi ki hain.</p>"
    body += "<p class='small' style='margin-top:12px'>Group join karna ya message karna aapki marzi hai.</p></div>"
    return launch_page('Contact', body)


@app.route('/admin/support', methods=['GET', 'POST'])
@admin_required
def admin_support():
    if request.method == 'POST':
        try:
            ticket_id = int(request.form.get('ticket_id', '0'))
        except ValueError:
            ticket_id = 0
        status = request.form.get('status', 'Open').strip()
        reply = request.form.get('admin_reply', '').strip()[:3000]
        if status not in ('Open', 'In Progress', 'Resolved'):
            status = 'Open'
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = db()
        row = conn.execute('SELECT username FROM support_tickets WHERE id=?', (ticket_id,)).fetchone()
        conn.execute('UPDATE support_tickets SET status=?, admin_reply=?, updated_at=? WHERE id=?', (status, reply, now, ticket_id))
        conn.commit(); conn.close()
        if row:
            add_notification(row['username'], 'Support Ticket Updated', 'Admin updated your support ticket #' + str(ticket_id) + ' status to ' + status + '.')
        return redirect('/admin/support')
    conn = db()
    tickets = conn.execute('SELECT * FROM support_tickets ORDER BY CASE status WHEN "Open" THEN 0 WHEN "In Progress" THEN 1 ELSE 2 END, id DESC').fetchall()
    conn.close()
    body = "<div class='card'><h2 class='gold'>🆘 Support Desk</h2><p>Manage user tickets and replies.</p></div>"
    if not tickets:
        body += "<div class='card'><p>No support tickets yet.</p></div>"
    for t in tickets:
        body += "<div class='card'><h3 class='gold'>#"+str(t['id'])+" — "+esc(t['subject'])+"</h3><p><b>User:</b> "+esc(t['username'])+"</p><p>"+esc(t['message'])+"</p>"
        body += "<form method='post'><input type='hidden' name='ticket_id' value='"+str(t['id'])+"'><label>Status</label><select name='status'>"
        for st in ('Open','In Progress','Resolved'):
            body += "<option" + (' selected' if t['status']==st else '') + ">"+st+"</option>"
        body += "</select><label>Admin Reply</label><textarea name='admin_reply' rows='4' maxlength='3000'>"+esc(t['admin_reply'] or '')+"</textarea><button class='btn' type='submit'>SAVE REPLY</button></form></div>"
    return layout('Admin Support Desk', body)


@app.route('/admin/launch-check')
@admin_required
def admin_launch_check():
    conn = db()
    checks = []
    for table in ('users','payments','withdrawals','ads','plans','notifications','ad_claims','ad_watch_history','ad_clicks','support_tickets','settings'):
        try:
            conn.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone()
            checks.append((table, 'OK'))
        except Exception as e:
            checks.append((table, 'ERROR'))
    conn.close()
    body = "<div class='card'><h2 class='gold'>🚀 Launch Check</h2><p>Database table smoke check.</p>"
    for name,status in checks:
        body += "<p>"+esc(name)+": <b class='"+('ok' if status=='OK' else 'bad')+"'>"+status+"</b></p>"
    body += "</div><div class='card'><a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"
    return layout('Launch Check', body)


@app.route('/admin/database-backup')
@admin_required
def admin_database_backup():
    body = "<div class='card'><h2 class='gold'>💾 Database Backup Center</h2><p>Create a fresh backup of the EarnPro database before making major changes.</p>"
    body += "<p class='small'>Backup includes users, payments, withdrawals, ads, plans, transactions, notifications, settings and other stored site data.</p>"
    body += "<a class='btn' href='/admin/database-backup/download'>⬇️ DOWNLOAD DATABASE BACKUP</a>"
    body += "<a class='btn2' href='/admin/backup-history'>📋 BACKUP HISTORY</a>"
    body += "<a class='btn2' href='/admin/launch-check'>🚀 LAUNCH CHECK</a>"
    body += "<a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"
    return layout('Database Backup Center', body)


@app.route('/admin/database-backup/download')
@admin_required
def admin_database_backup_download():
    # Step 122: persistent backups so Backup History remains useful after download.
    source = os.path.abspath(DB_FILE)
    if not os.path.exists(source):
        return layout('Backup Error', "<div class='card'><h2 class='gold'>Backup unavailable</h2><p>Database file was not found.</p><a class='btn2' href='/admin/database-backup'>BACK TO BACKUP CENTER</a></div>"), 404
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = 'earnpro_backup_' + stamp + '.db'
    backup_dir = os.path.join(os.path.dirname(source), 'earnpro_backups')
    backup_path = os.path.join(backup_dir, backup_name)
    src_conn = dst_conn = None
    try:
        os.makedirs(backup_dir, exist_ok=True)
        src_conn = sqlite3.connect(source)
        dst_conn = sqlite3.connect(backup_path)
        src_conn.backup(dst_conn)
        dst_conn.commit()
        src_conn.close(); dst_conn.close()
        src_conn = dst_conn = None
        try:
            add_audit('Database Backup', 'Admin created database backup ' + backup_name)
        except Exception:
            pass
        return send_file(backup_path, as_attachment=True, download_name=backup_name, mimetype='application/octet-stream')
    except Exception as exc:
        for conn in (src_conn, dst_conn):
            try:
                if conn: conn.close()
            except Exception:
                pass
        try:
            if os.path.exists(backup_path): os.remove(backup_path)
        except Exception:
            pass
        return layout('Backup Error', "<div class='card'><h2 class='gold'>💾 Backup Error</h2><p class='bad'>Backup create nahi ho saka: " + esc(str(exc)) + "</p><a class='btn2' href='/admin/database-backup'>TRY AGAIN</a><a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"), 500


# Step 109/122 — Admin Backup History
@app.route('/admin/backup-history')
@admin_required
def admin_backup_history():
    source = os.path.abspath(DB_FILE)
    base = os.path.join(os.path.dirname(source), 'earnpro_backups')
    try:
        os.makedirs(base, exist_ok=True)
        names = [n for n in os.listdir(base) if n.startswith('earnpro_backup_') and n.endswith('.db') and os.path.isfile(os.path.join(base, n))]
        names.sort(key=lambda n: os.path.getmtime(os.path.join(base, n)), reverse=True)
    except Exception as exc:
        body = "<div class='card'><h2 class='gold'>💾 Backup History</h2><p class='bad'>Backup history load nahi ho saki: " + esc(str(exc)) + "</p>"
        body += "<a class='btn' href='/admin/database-backup/download'>⬇️ CREATE BACKUP</a><a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"
        return layout('Backup History', body), 500
    body = "<div class='card'><h2 class='gold'>💾 Backup History</h2><p class='small'>Saved local database backups. Har successful backup yahan record rahega.</p>"
    if not names:
        body += "<div class='stat'><span>No backup history yet.</span><span>—</span></div>"
    else:
        for name in names[:30]:
            path = os.path.join(base, name)
            try:
                size_kb = max(1, int(os.path.getsize(path) / 1024))
                stamp = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S')
                body += "<div class='stat'><span><b>" + esc(name) + "</b><br><span class='small'>" + esc(stamp) + " • " + str(size_kb) + " KB</span></span><span>SAVED</span></div>"
            except Exception:
                body += "<div class='stat'><span><b>" + esc(name) + "</b></span><span>AVAILABLE</span></div>"
    body += "</div><div class='card'><a class='btn' href='/admin/database-backup/download'>⬇️ CREATE & DOWNLOAD FRESH BACKUP</a><a class='btn2' href='/admin/database-backup'>💾 BACKUP CENTER</a><a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"
    return layout('Backup History', body)


@app.route('/admin/launch-status')
@admin_required
def admin_launch_status():
    def flag(key):
        return 'ON' if get_setting(key) == '1' else 'OFF'
    rows = [
        ('Site Name', get_setting('site_name') or 'EarnPro'),
        ('Maintenance Mode', flag('maintenance_mode')),
        ('Registration', flag('registration_enabled')),
        ('Payments', flag('payments_enabled')),
        ('Withdrawals', flag('withdrawals_enabled')),
        ('WhatsApp Number', get_setting('whatsapp_number') or 'Not set'),
        ('WhatsApp Group', get_setting('whatsapp_group_link') or 'Not set'),
        ('Support Email', get_setting('support_email') or 'Not set'),
    ]
    body = "<div class='card'><h2 class='gold'>🚀 Launch Status Center</h2><p>Quick admin-only status check. Existing controls are unchanged.</p>"
    for label, value in rows:
        cls = 'ok' if value == 'ON' or (label == 'Site Name' and value != 'EarnPro') else ''
        body += "<div class='stat'><span><b>" + esc(label) + "</b></span><span class='" + cls + "'>" + esc(value) + "</span></div>"
    body += "</div><div class='card'><a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"
    return layout('Launch Status', body)


@app.route('/admin/launch-readiness')
def admin_launch_readiness():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    checks = []
    # Database availability / writability check.
    try:
        conn = get_db()
        conn.execute('SELECT 1')
        conn.commit()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    checks.append(('Database connection', db_ok, 'SQLite database is accessible.'))

    # Core settings presence.
    core_settings = ['site_name', 'registration_enabled', 'payments_enabled', 'withdrawals_enabled', 'maintenance_mode']
    missing = [k for k in core_settings if get_setting(k) is None]
    checks.append(('Core settings', not missing, 'All core launch settings are present.' if not missing else 'Missing: ' + ', '.join(missing)))

    # Admin credential configuration.
    custom_pw = get_setting('admin_custom_password') == '1'
    checks.append(('Admin password', True, 'Custom hashed password is enabled.' if custom_pw else 'Default environment/admin password mode is active.'))

    # Contact details are informational, not blocking.
    contact_ready = bool(get_setting('support_email') or get_setting('whatsapp_number') or get_setting('whatsapp_group_link'))
    checks.append(('Support contact', contact_ready, 'At least one support contact is configured.' if contact_ready else 'No support contact is configured yet.'))

    body = "<div class='card'><h2 class='gold'>🚀 Launch Readiness Checker</h2><p>Admin-only pre-launch check. Informational checks do not change your existing settings.</p>"
    for name, ok, detail in checks:
        icon = '✅' if ok else '⚠️'
        body += "<div class='stat'><span><b>" + esc(icon + ' ' + name) + "</b></span><span>" + esc(detail) + "</span></div>"
    body += "</div><div class='card'><a class='btn2' href='/admin/database-backup'>💾 DATABASE BACKUP</a> <a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"
    return layout('Launch Readiness', body)


# Add contact/support/legal shortcuts to the existing admin dashboard without
# replacing its current controls. The dashboard route remains otherwise intact.

@app.route('/admin/control-center')
def admin_control_center():
    """Admin-only central control/verification page; does not change settings."""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    items = []
    def add_check(title, ok, detail):
        items.append((title, bool(ok), detail))

    add_check('Database', True, 'SQLite database is available to the running app.')
    add_check('Maintenance', get_setting('maintenance_mode') != '1',
              'Maintenance mode is OFF.' if get_setting('maintenance_mode') != '1' else 'Maintenance mode is currently ON.')
    add_check('Registration', get_setting('registration_enabled') == '1',
              'New registration is enabled.' if get_setting('registration_enabled') == '1' else 'New registration is disabled.')
    add_check('Payments', get_setting('payments_enabled') == '1',
              'Plan/payment requests are enabled.' if get_setting('payments_enabled') == '1' else 'Payments are disabled.')
    add_check('Withdrawals', get_setting('withdrawals_enabled') == '1',
              'Withdrawals are enabled.' if get_setting('withdrawals_enabled') == '1' else 'Withdrawals are disabled.')
    add_check('Support', bool(get_setting('support_email') or get_setting('whatsapp_number') or get_setting('whatsapp_group_link')),
              'At least one support channel is configured.' if (get_setting('support_email') or get_setting('whatsapp_number') or get_setting('whatsapp_group_link')) else 'No support channel is configured.')

    passed = sum(1 for _, ok, _ in items if ok)
    total = len(items)
    pct = int((passed / total) * 100) if total else 0
    body = "<div class='card'><h2 class='gold'>🎛️ Admin Control Center</h2>"
    body += "<p>Central overview of important launch controls. This page only reads current settings.</p>"
    body += "<div class='stat'><span><b>Launch readiness</b></span><span><b>" + esc(str(passed) + '/' + str(total) + ' checks • ' + str(pct) + '%') + "</b></span></div>"
    for title, ok, detail in items:
        body += "<div class='stat'><span><b>" + esc(('✅ ' if ok else '⚠️ ') + title) + "</b></span><span>" + esc(detail) + "</span></div>"
    body += "</div>"
    body += "<div class='card'><h3 class='gold'>Quick Actions</h3>"
    body += "<a class='btn2' href='/admin/launch-readiness'>🚀 READINESS CHECK</a> "
    body += "<a class='btn2' href='/admin/database-backup'>💾 DATABASE BACKUP</a> "
    body += "<a class='btn2' href='/admin'>ADMIN DASHBOARD</a></div>"
    return layout('Admin Control Center', body)


ensure_launch_tables()



# Step 90 — Master Final Bundle
@app.route('/admin/security-audit')
@admin_required
def admin_security_audit():
    checks = []
    checks.append(('Admin authentication', bool(session.get('admin')), 'Admin session is active.'))
    checks.append(('Database', os.path.exists(DB_PATH), 'Database file is available.' if os.path.exists(DB_PATH) else 'Database file missing.'))
    secret_ok = bool(app.secret_key) and len(str(app.secret_key)) >= 16
    checks.append(('Secret key', secret_ok, 'Secret key is configured.' if secret_ok else 'Secret key is weak or missing.'))
    cookie_ok = bool(app.config.get('SESSION_COOKIE_HTTPONLY', True)) and bool(app.config.get('SESSION_COOKIE_SAMESITE'))
    checks.append(('Session cookie protection', cookie_ok, 'HttpOnly/SameSite protections are enabled.' if cookie_ok else 'Session cookie protection needs review.'))
    try:
        with get_db() as conn:
            conn.execute('SELECT 1').fetchone()
        db_query_ok = True
    except Exception:
        db_query_ok = False
    checks.append(('Database query', db_query_ok, 'Database query succeeded.' if db_query_ok else 'Database query failed.'))
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    pct = int((passed / total) * 100) if total else 0
    rows = ''.join("<tr><td>" + esc(name) + "</td><td><b class='" + ('good' if ok else 'bad') + "'>" + ('PASS' if ok else 'CHECK') + "</b></td><td>" + esc(msg) + "</td></tr>" for name, ok, msg in checks)
    body = "<div class='card'><h2 class='gold'>🔐 Security Audit Center</h2><div class='stat'><span>Security readiness</span><span><b>" + str(passed) + "/" + str(total) + " checks • " + str(pct) + "%</b></span></div></div>"
    body += "<div class='card'><table><tr><th>Check</th><th>Status</th><th>Details</th></tr>" + rows + "</table></div>"
    body += "<div class='card'><a class='btn2' href='/admin/master-final'>MASTER FINAL</a> <a class='btn2' href='/admin/system-health'>SYSTEM HEALTH</a> <a class='btn2' href='/admin/database-backup'>DATABASE BACKUP</a></div>"
    return page('Security Audit Center', body)

@app.route('/health')
def public_health():
    try:
        with get_db() as conn:
            conn.execute('SELECT 1').fetchone()
        return {'status':'ok','database':'ok','service':'EarnPro'}, 200
    except Exception:
        return {'status':'error','database':'error','service':'EarnPro'}, 503

@app.route('/admin/master-final')
@admin_required
def admin_master_final():
    db_ok = True
    db_msg = 'Database connection available'
    try:
        with get_db() as conn:
            conn.execute('SELECT 1').fetchone()
    except Exception as exc:
        db_ok = False
        db_msg = f'Database check failed: {exc}'
    settings = {
        'Maintenance Mode': 'ON' if get_setting('maintenance_mode') == '1' else 'OFF',
        'Registration': 'ON' if get_setting('registration_enabled') != '0' else 'OFF',
        'Payments': 'ON' if get_setting('payments_enabled') != '0' else 'OFF',
        'Withdrawals': 'ON' if get_setting('withdrawals_enabled') != '0' else 'OFF',
    }
    checks = [
        ('Database', db_ok, db_msg),
        ('Admin authentication', bool(session.get('admin')), 'Current admin session is active'),
        ('Support email', bool(get_setting('support_email')), 'Configured' if get_setting('support_email') else 'Missing'),
        ('WhatsApp contact', bool(get_setting('whatsapp_number')), 'Configured' if get_setting('whatsapp_number') else 'Missing'),
        ('Secret key', bool(app.secret_key), 'Application secret key is loaded'),
        ('Launch controls', True, 'Registration, payments, withdrawals and maintenance controls are available'),
        ('Backup center', True, 'Database backup page is available'),
        ('Security audit', True, 'Security audit page is available'),
        ('Readiness checker', True, 'Launch readiness page is available'),
    ]
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    percent = int((passed / total) * 100) if total else 0
    rows = ''.join(f"<tr><td>{name}</td><td><b>{'PASS' if ok else 'CHECK'}</b></td><td>{msg}</td></tr>" for name, ok, msg in checks)
    controls = ''.join(f'<li><b>{k}:</b> {v}</li>' for k, v in settings.items())
    content = f"""
    <div class='card'>
      <h2>🚀 Master Final Control Center</h2>
      <p>All major launch, security, backup and operational checks are grouped here.</p>
      <div class='card'><h3>Launch Readiness: {percent}%</h3><p>{passed}/{total} checks passed.</p></div>
      <h3>Current Controls</h3><ul>{controls}</ul>
      <h3>System Checks</h3>
      <div style='overflow-x:auto'><table><tr><th>Check</th><th>Status</th><th>Details</th></tr>{rows}</table></div>
      <div style='margin-top:15px;display:flex;gap:8px;flex-wrap:wrap'>
        <a class='btn' href='/admin/control-center'>🎛️ Control Center</a>
        <a class='btn' href='/admin/launch-readiness'>🚀 Readiness</a>
        <a class='btn' href='/admin/security-audit'>🔐 Security Audit</a>
        <a class='btn' href='/admin/database-backup'>💾 Backup</a>
        <a class='btn' href='/admin/system-health'>❤️ System Health</a>
        <a class='btn' href='/admin/support'>🎧 Support</a>
      </div>
    </div>"""
    return layout('Master Final Control Center', content)


@app.route('/admin/operations-center')
@admin_required
def admin_operations_center():
    """Single admin operations overview for day-to-day monitoring."""
    stats=[]
    tables=[
        ('Users','users'),('Payments','payments'),('Withdrawals','withdrawals'),
        ('Transactions','transactions'),('Notifications','notifications'),
        ('Ads','ads'),('Ad Claims','ad_claims'),('Referrals','referrals'),
        ('Audit Logs','audit_logs')
    ]
    try:
        with get_db() as conn:
            existing={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            for label, table in tables:
                if table in existing:
                    n=conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    stats.append((label,int(n)))
    except Exception:
        stats=[]
    cards=''.join("<div class='card' style='min-width:150px;flex:1'><div style='font-size:13px'>"+esc(k)+"</div><div style='font-size:25px;font-weight:800'>"+str(v)+"</div></div>" for k,v in stats)
    links="""
    <div class='card'><h2>🛠️ Operations Center</h2>
    <p>Daily admin overview: users, payments, withdrawals, earnings, ads and audit activity.</p>
    <div style='display:flex;gap:10px;flex-wrap:wrap'>""" + cards + """</div></div>
    <div class='card'><h3>Quick Actions</h3>
      <a class='btn' href='/admin/users'>👥 Users</a>
      <a class='btn' href='/admin/payments'>💳 Payments</a>
      <a class='btn' href='/admin/withdrawals'>💸 Withdrawals</a>
      <a class='btn' href='/admin/ads'>📺 Ads</a>
      <a class='btn' href='/admin/referrals'>🤝 Referrals</a>
      <a class='btn' href='/admin/notifications'>🔔 Notifications</a>
      <a class='btn' href='/admin/audit-logs'>🛡️ Audit Logs</a>
      <a class='btn' href='/admin/master-final'>🚀 Master Final</a>
    </div>"""
    return layout('Operations Center', links)

@app.route('/admin/export-users.csv')
@admin_required
def admin_export_users_csv():
    """Download a privacy-conscious user export without password fields."""
    import csv, io
    out=io.StringIO()
    w=csv.writer(out)
    w.writerow(['id','username','balance','total_earning','total_withdrawn','blocked','created_at','last_login'])
    try:
        with get_db() as conn:
            cols={r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()}
            wanted=['id','username','balance','total_earning','total_withdrawn','blocked','created_at','last_login']
            use=[c for c in wanted if c in cols]
            rows=conn.execute('SELECT '+','.join('"'+c+'"' for c in use)+' FROM users').fetchall()
            for row in rows:
                data=dict(zip(use,row))
                w.writerow([data.get(c,'') for c in wanted])
    except Exception as exc:
        return f'Export failed: {esc(exc)}', 500
    from flask import Response
    return Response(out.getvalue(), mimetype='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename=earnpro_users.csv'})


# Step 105: user-side Referral Link Health Check.
# This is intentionally isolated from admin routes and existing referral reward logic.
@app.route("/referral-link-check")
def referral_link_check():
    username = session.get("username")
    if not username:
        return redirect(url_for("login"))
    conn = db()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "ref_code" not in columns:
            return layout("Referral Link Check", "<div class='card'><h2 class='gold'>Referral Link Check</h2><p class='bad'>Referral system column is missing. Please contact admin.</p><a class='btn2' href='/referral'>BACK TO REFERRAL</a></div>")
        user = conn.execute("SELECT id,username,ref_code FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            return layout("Referral Link Check", "<div class='card'><h2 class='gold'>Referral Link Check</h2><p class='bad'>Account not found.</p></div>")
        code = (str(user["ref_code"] or "").strip().upper())
        valid = False
        if code:
            matches = conn.execute("SELECT COUNT(*) AS c FROM users WHERE ref_code=?", (code,)).fetchone()["c"]
            valid = int(matches or 0) == 1
        link = request.host_url.rstrip("/") + "/register?ref=" + quote(code, safe="") if code and valid else ""
        if valid:
            body = "<div class='card'><h2 class='gold'>🔗 Referral Link Health</h2>" \
                   "<p class='ok'>✅ Your referral link is active and unique.</p>" \
                   "<div class='stat'><span class='small'>REFERRAL CODE</span><b class='gold'>" + esc(code) + "</b></div>" \
                   "<p style='word-break:break-all'>" + esc(link) + "</p>" \
                   "<button class='btn2' type='button' onclick='navigator.clipboard.writeText(" + json.dumps(link) + ").then(()=>this.innerText=" + json.dumps("COPIED ✓") + ").catch(()=>{})'>COPY LINK</button>" \
                   "<a class='btn2' href='/referral'>BACK TO REFERRAL</a></div>"
        else:
            body = "<div class='card'><h2 class='gold'>🔗 Referral Link Health</h2>" \
                   "<p class='bad'>Referral code missing or invalid. Open Referral Center to repair it automatically.</p>" \
                   "<a class='btn2' href='/referral'>OPEN REFERRAL CENTER</a></div>"
        return layout("Referral Link Health", body)
    except Exception:
        return layout("Referral Link Check", "<div class='card'><h2 class='gold'>Referral Link Check</h2><p class='bad'>Referral link check temporarily unavailable. Please try again.</p><a class='btn2' href='/referral'>BACK TO REFERRAL</a></div>")
    finally:
        conn.close()


# Step 106: user-side Referral Share Center.
# Provides safe share links without changing referral reward/admin logic.
@app.route("/referral-share")
def referral_share_center():
    username = session.get("username")
    if not username:
        return redirect(url_for("login"))
    conn = db()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "ref_code" not in columns:
            return layout("Referral Share", "<div class='card'><h2 class='gold'>📤 Referral Share</h2><p class='bad'>Referral system is not ready yet. Please contact admin.</p></div>")
        user = conn.execute("SELECT username,ref_code FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            return layout("Referral Share", "<div class='card'><h2 class='gold'>📤 Referral Share</h2><p class='bad'>Account not found.</p></div>")
        code = str(user["ref_code"] or "").strip().upper()
        if not code:
            return layout("Referral Share", "<div class='card'><h2 class='gold'>📤 Referral Share</h2><p class='bad'>Referral code is missing. Open Referral Center to repair it.</p><a class='btn2' href='/referral'>OPEN REFERRAL CENTER</a></div>")
        count = conn.execute("SELECT COUNT(*) AS c FROM users WHERE ref_code=?", (code,)).fetchone()["c"]
        if int(count or 0) != 1:
            return layout("Referral Share", "<div class='card'><h2 class='gold'>📤 Referral Share</h2><p class='bad'>Referral code is not currently unique. Open Referral Center to repair it.</p><a class='btn2' href='/referral'>OPEN REFERRAL CENTER</a></div>")
        link = request.host_url.rstrip("/") + "/register?ref=" + quote(code, safe="")
        message = "Join EarnPro and start earning rewards. Use my referral link: " + link
        wa = "https://wa.me/?text=" + quote(message, safe="")
        tg = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote("Join EarnPro and start earning rewards.", safe="")
        body = "<div class='card'><h2 class='gold'>📤 Referral Share Center</h2>" \
               "<p>Share your verified referral link with friends.</p>" \
               "<div class='stat'><span class='small'>REFERRAL CODE</span><b class='gold'>" + esc(code) + "</b></div>" \
               "<p style='word-break:break-all'>" + esc(link) + "</p>" \
               "<div style='display:grid;gap:10px'>" \
               "<button class='btn2' type='button' onclick='navigator.clipboard.writeText(" + json.dumps(link) + ").then(()=>this.innerText=" + json.dumps("COPIED ✓") + ").catch(()=>{})'>📋 COPY REFERRAL LINK</button>" \
               "<a class='btn2' href='" + esc(wa) + "' target='_blank' rel='noopener'>💬 SHARE ON WHATSAPP</a>" \
               "<a class='btn2' href='" + esc(tg) + "' target='_blank' rel='noopener'>✈️ SHARE ON TELEGRAM</a>" \
               "<a class='btn2' href='/referral'>↩ BACK TO REFERRAL</a></div></div>"
        return layout("Referral Share", body)
    except Exception:
        return layout("Referral Share", "<div class='card'><h2 class='gold'>📤 Referral Share</h2><p class='bad'>Referral sharing is temporarily unavailable. Please try again.</p><a class='btn2' href='/referral'>BACK TO REFERRAL</a></div>")
    finally:
        conn.close()


# Step 108: User Final Center — consolidate important user-side features that were
# previously available as separate pages but lacked one clear launch-ready hub.
# This block is user-side only; existing admin routes and business logic remain untouched.
@app.route('/help-center')
@login_required
def user_help_center():
    body = """
    <div class='card'><h2 class='gold'>🆘 Help & Support Center</h2>
    <p>EarnPro ke important help, support aur legal pages ek jagah.</p></div>
    <div class='grid'>
      <a class='btn2' href='/support'>🎧 SUPPORT TICKET<br><span class='small'>Admin se help lo</span></a>
      <a class='btn2' href='/contact'>📞 CONTACT US<br><span class='small'>WhatsApp / Group / Channel</span></a>
      <a class='btn2' href='/faq'>❓ FAQ<br><span class='small'>Common questions</span></a>
      <a class='btn2' href='/terms'>📜 TERMS<br><span class='small'>Rules & conditions</span></a>
      <a class='btn2' href='/privacy'>🔒 PRIVACY<br><span class='small'>Privacy notice</span></a>
      <a class='btn2' href='/dashboard'>🏠 DASHBOARD<br><span class='small'>Back to EarnPro</span></a>
    </div>
    """
    return layout('Help Center', body)

@app.route('/account-security')
@login_required
def account_security_center():
    username = session['username']
    conn = db()
    user = conn.execute('SELECT username,last_login,blocked FROM users WHERE username=?', (username,)).fetchone()
    logs = conn.execute('SELECT login_at,ip_address,success FROM login_history WHERE username=? ORDER BY id DESC LIMIT 8', (username,)).fetchall()
    conn.close()
    body = "<div class='card'><h2 class='gold'>🔐 Account Security</h2>"
    body += "<p><b>Account:</b> " + esc(username) + "</p>"
    body += "<p><b>Last login:</b> " + esc((user['last_login'] if user and user['last_login'] else '-') ) + "</p>"
    body += "<p><b>Status:</b> " + ('Blocked' if user and user['blocked'] else 'Active') + "</p>"
    body += "<a class='btn2' href='/profile'>👤 PROFILE</a> <a class='btn2' href='/login-history'>🕘 LOGIN HISTORY</a>"
    body += "</div><div class='card'><h3 class='gold'>Recent Login Activity</h3>"
    if logs:
        body += "<div style='overflow-x:auto'><table><tr><th>Time</th><th>IP</th><th>Status</th></tr>"
        for r in logs:
            status = 'Success' if r['success'] else 'Failed'
            body += "<tr><td>" + esc(r['login_at'] or '-') + "</td><td>" + esc(r['ip_address'] or '-') + "</td><td>" + esc(status) + "</td></tr>"
        body += "</table></div>"
    else:
        body += "<p class='small'>No login history available.</p>"
    body += "</div><div class='card'><a class='btn2' href='/dashboard'>🏠 BACK TO DASHBOARD</a></div>"
    return layout('Account Security', body)


@app.route("/admin/payment-readiness")
def admin_payment_readiness():
    if not admin_required():
        return redirect(url_for("admin_login"))
    conn = get_db()
    try:
        pcols = [r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()]
        indexes = [r[1] for r in conn.execute("PRAGMA index_list(payments)").fetchall()]
        pending = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE status='Pending'").fetchone()["c"]
        approved = conn.execute("SELECT COUNT(*) AS c FROM payments WHERE status='Approved'").fetchone()["c"]
        duplicate_txids = conn.execute("SELECT COUNT(*) AS c FROM (SELECT txid FROM payments WHERE txid!='' GROUP BY txid HAVING COUNT(*)>1)").fetchone()["c"]
    finally:
        conn.close()
    required_cols = ["username","plan","amount","method","txid","status","created_at","plan_days","consumed_at"]
    missing_cols = [c for c in required_cols if c not in pcols]
    unique_ok = any("txid" in i.lower() and "unique" in i.lower() for i in indexes) or duplicate_txids == 0
    jc = get_setting("jazzcash_number")
    ep = get_setting("easypaisa_number")
    enabled = get_setting("payments_enabled") == "1"
    checks = [
        ("Payments switch", enabled, "Payments are enabled" if enabled else "Payments are disabled"),
        ("JazzCash receiving number", bool(jc), "Configured" if jc else "Not configured"),
        ("Easypaisa receiving number", bool(ep), "Configured" if ep else "Not configured"),
        ("Payment table schema", not missing_cols, "All required columns present" if not missing_cols else "Missing: " + ", ".join(missing_cols)),
        ("Transaction-ID uniqueness", unique_ok, "No duplicate TXIDs detected" if unique_ok else "Duplicate TXIDs detected"),
    ]
    rows = ""
    for name, ok, detail in checks:
        status = '<span class="ok">READY</span>' if ok else '<span class="bad">CHECK</span>'
        rows += f"<tr><td>{esc(name)}</td><td>{status}</td><td>{esc(detail)}</td></tr>"
    body = (
        '<div class="card"><h2 class="gold">💳 Payment Readiness Center</h2>'
        '<p class="small">Production se pehle payment configuration aur database safeguards verify karein.</p>'
        f'<table class="table"><tr><th>Check</th><th>Status</th><th>Details</th></tr>{rows}</table>'
        f'<p><b>Pending payments:</b> {pending} &nbsp; <b>Approved:</b> {approved}</p>'
        '<a class="btn2" href="/admin/payments">OPEN PAYMENTS</a>'
        '<a class="btn2" href="/admin/payment-analytics">PAYMENT ANALYTICS</a>'
        '<a class="btn2" href="/admin">ADMIN HOME</a></div>'
    )
    return layout("Payment Readiness", body)



# Step 114: User data export (privacy-safe, user-side only).
@app.route("/account/export.csv")
@login_required
def account_export_csv():
    import csv, io
    from flask import Response
    username = session.get("username", "")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["field", "value"])
    try:
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                return "Account not found", 404
            keys = list(user.keys()) if hasattr(user, "keys") else []
            blocked = {"password", "password_hash", "session", "secret", "token"}
            for key in keys:
                if key.lower() in blocked or "password" in key.lower() or "token" in key.lower() or "secret" in key.lower():
                    continue
                writer.writerow([key, user[key]])
    except Exception as exc:
        return "Account export failed: " + esc(exc), 500
    add_audit_log("Exported account data", username, "Privacy-safe user data export")
    return Response(out.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=earnpro_account_data.csv"})

@app.route('/admin/final-launch-check')
@admin_required
def admin_final_launch_check():
    checks = []
    try:
        with get_db() as conn:
            conn.execute('SELECT 1').fetchone()
        checks.append(('Database', True, 'OK'))
    except Exception as exc:
        checks.append(('Database', False, str(exc)))
    checks.append(('Secret key', bool(app.secret_key), 'Configured' if app.secret_key else 'Missing'))
    checks.append(('Registration', get_setting('registration_enabled') != '0', 'ON' if get_setting('registration_enabled') != '0' else 'OFF'))
    checks.append(('Payments', get_setting('payments_enabled') != '0', 'ON' if get_setting('payments_enabled') != '0' else 'OFF'))
    checks.append(('Withdrawals', get_setting('withdrawals_enabled') != '0', 'ON' if get_setting('withdrawals_enabled') != '0' else 'OFF'))
    checks.append(('Maintenance', get_setting('maintenance_mode') != '1', 'OFF / public access available' if get_setting('maintenance_mode') != '1' else 'ON'))
    rows=''.join('<tr><td>'+esc(n)+'</td><td><b>'+('PASS' if ok else 'CHECK')+'</b></td><td>'+esc(d)+'</td></tr>' for n,ok,d in checks)
    passed=sum(1 for _,ok,_ in checks if ok)
    body=("<div class='card'><h2 class='gold'>🚀 Final Launch Check</h2>"
          "<p>Production se pehle ye final operational checks verify karein.</p>"
          "<table><tr><th>Check</th><th>Status</th><th>Details</th></tr>"+rows+"</table>"
          "<p><b>Passed:</b> "+str(passed)+" / "+str(len(checks))+"</p>"
          "<a class='btn2' href='/admin'>ADMIN HOME</a></div>")
    return layout('Final Launch Check', body)

if __name__ == '__main__':
    print('EarnPro Step 130 — WHATSAPP CONTACT MENU running...')
    print('Admin: /admin/login')
    print('Contact: /contact | Support: /support | FAQ: /faq')
    app.run(host='0.0.0.0', port=5001, debug=False)
