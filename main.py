"""
UbeKeso Cart - Event-Driven Programming final project (PyScript).

How this file is organised
1. Business settings   - defaults (the server sends the real values)
2. App state           - one dictionary holding everything the app knows
3. Helpers             - formatting, server calls, toast, modal
4. Screen renderers    - one function per screen -> returns HTML
5. Event handlers      - what happens when the user clicks, types, submits...
6. Event wiring        - addEventListener calls and the live timer at the bottom

Three kinds of people use the app:
  customer - creates an account, orders ahead, follows the order live
  staff    - sees every order, fries / marks ready / hands over, sells, stock
  admin    - the owner: sees everything, creates staff accounts, watches the activity log

Event-driven idea: the app does nothing until something happens. A click,
a form submit, a typed letter, a timer tick or a message from the server fires
an event, the matching handler updates `state`, and render() redraws.
Realtime: a timer event polls the server every 3 seconds. When the server says
something changed (a new order, a status change...), the screen redraws itself.
"""
import asyncio
import html as _html
import json

from pyscript import document, window
from pyodide.ffi import create_proxy
from pyodide.http import pyfetch

# ---------------------------------------------------------------------------
# 1. BUSINESS SETTINGS (defaults; the server overrides them on sign in)
# ---------------------------------------------------------------------------
PRICE = 25
EXTRA_CHEESE_PRICE = 5
COST_PER_PIECE = 10
DAILY_TARGET = 40
MONTH_GOAL = 26000
STAMPS_FOR_FREE = 10
LOW_AT = 10
RUNNING_LOW_AT = 20
STOCK_BAR_MAX = 60
MIN_PASSWORD = 8

POLL_MS = 3000
TOKEN_KEY = "ubekeso_token"

# Starting history for the reports (Mon..Sat). All zero: real numbers appear as you make sales.
WEEK_SAMPLE = [0, 0, 0, 0, 0, 0]
MONTH_SAMPLE_WEEKS = [0, 0, 0]
MONTH_DAYS_BEFORE_THIS_WEEK = 0

DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
DAY_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def apply_config(cfg):
    global PRICE, EXTRA_CHEESE_PRICE, COST_PER_PIECE, DAILY_TARGET, MONTH_GOAL
    global STAMPS_FOR_FREE, LOW_AT, RUNNING_LOW_AT, STOCK_BAR_MAX, MIN_PASSWORD
    PRICE = cfg.get("price", PRICE)
    EXTRA_CHEESE_PRICE = cfg.get("extra_cheese_price", EXTRA_CHEESE_PRICE)
    COST_PER_PIECE = cfg.get("cost_per_piece", COST_PER_PIECE)
    DAILY_TARGET = cfg.get("daily_target", DAILY_TARGET)
    MONTH_GOAL = cfg.get("month_goal", MONTH_GOAL)
    STAMPS_FOR_FREE = cfg.get("stamps_for_free", STAMPS_FOR_FREE)
    LOW_AT = cfg.get("low_at", LOW_AT)
    RUNNING_LOW_AT = cfg.get("running_low_at", RUNNING_LOW_AT)
    STOCK_BAR_MAX = cfg.get("stock_bar_max", STOCK_BAR_MAX)
    MIN_PASSWORD = cfg.get("min_password", MIN_PASSWORD)


# ---------------------------------------------------------------------------
# 2. APP STATE
# ---------------------------------------------------------------------------
def new_sale():
    return {"qty": 1, "extra": False, "card": None, "pay": "cash"}


def default_pickup():
    d = window.Date.new(window.Date.now() + 30 * 60 * 1000)
    return "{:02d}:{:02d}".format(int(d.getHours()), int(d.getMinutes()))


def new_cust():
    return {"qty": 1, "extra": False, "pay": "cash", "pickup": default_pickup()}


def new_form():
    return {"kind": "email", "name": "", "ident": ""}


state = {
    "screen": "signin",
    "token": None,
    "me": None,                  # {id, name, role, email, phone, must_change}
    "version": "",
    "synced": False,             # True after the first state arrived (used for "new order" toasts)
    "dirty": False,              # new data arrived while the user was typing
    "busy": False,               # a request is running (blocks double clicks)
    "polling": False,
    "conn_ok": True,
    "err": "",
    # data from the server
    "sold_today": 0, "extra_rev": 0, "week_hist": [None] * 6,
    "cards": [], "orders": [], "stock": [], "staff": [], "activity": [], "customers": 0, "my_card": None,
    "promo": None,               # {src, type} when media/promo.mp4 exists, else None (empty space is shown)
    # screen state
    "sale": new_sale(),
    "cust": None,
    "form": new_form(),
    "reset": None,
    "receipt": None,
    "picking": False,
    "loyalty_query": "",
    "selected_card": "UK-001",
    "order_filter": "new",
    "act_filter": "all",
    "range": "week",
    "_last": None,
}

# ---------------------------------------------------------------------------
# 3. HELPERS
# ---------------------------------------------------------------------------


def peso(n):
    return "\u20b1{:,}".format(int(n))


def esc(s):
    return _html.escape(str(s), quote=True)


def el(element_id):
    return document.getElementById(element_id)


def role():
    return (state["me"] or {}).get("role")


def is_staffish():
    return role() in ("staff", "admin")


def first_name(name):
    parts = str(name or "").split()
    return parts[0] if parts else ""


def date_label():
    d = window.Date.new()
    return "{}, {} {}".format(DAY_FULL[int(d.getDay())], MONTHS[int(d.getMonth())], int(d.getDate()))


def today_index():
    """0 = Mon ... 5 = Sat (Sunday counts as Saturday)."""
    return min((int(window.Date.new().getDay()) + 6) % 7, 5)


def fmt_time(t):
    try:
        h, m = t.split(":")
        h = int(h)
        return "{}:{} {}".format(h % 12 or 12, m, "AM" if h < 12 else "PM")
    except Exception:
        return t


def ts_label(ts):
    """Seconds since 1970 -> '3:42 PM' (today) or 'Oct 16, 3:42 PM'."""
    if not ts:
        return "never"
    d = window.Date.new(ts * 1000)
    h, m = int(d.getHours()), int(d.getMinutes())
    clock = "{}:{:02d} {}".format(h % 12 or 12, m, "AM" if h < 12 else "PM")
    if d.toDateString() == window.Date.new().toDateString():
        return clock
    return "{} {}, {}".format(MONTHS[int(d.getMonth())], int(d.getDate()), clock)


def get_card(card_id):
    for c in state["cards"]:
        if c["id"] == card_id:
            return c
    return None


def stock_status(left):
    if left < LOW_AT:
        return "low", "Low"
    if left < RUNNING_LOW_AT:
        return "running", "Running low"
    return "good", "Good"


def low_items():
    items = [s for s in state["stock"] if stock_status(s["left"])[0] != "good"]
    return sorted(items, key=lambda s: s["left"])


def sale_total(sale=None):
    sale = sale or state["sale"]
    unit = PRICE + (EXTRA_CHEESE_PRICE if sale["extra"] else 0)
    return sale["qty"] * unit


def week_values():
    ti = today_index()
    hist = state["week_hist"]
    vals = []
    for i in range(6):
        if i < ti:
            h = hist[i] if i < len(hist) else None
            vals.append(WEEK_SAMPLE[i] if h is None else h)
        elif i == ti:
            vals.append(state["sold_today"])
        else:
            vals.append(0)
    return vals


def month_pieces():
    return sum(MONTH_SAMPLE_WEEKS) + sum(week_values())


def revenue_for(pieces):
    return pieces * PRICE + state["extra_rev"]


def waiting_orders():
    return len([o for o in state["orders"] if o["status"] in ("new", "frying")])


def count_status(status):
    return len([o for o in state["orders"] if o["status"] == status])


# --- toast (small message that disappears by itself: a timer event) --------
_toast_timer = None
_toast_hide = create_proxy(lambda: el("toast").classList.remove("show"))


def toast(message):
    global _toast_timer
    box = el("toast")
    box.textContent = message
    box.classList.add("show")
    if _toast_timer is not None:
        window.clearTimeout(_toast_timer)
    _toast_timer = window.setTimeout(_toast_hide, 2800)


# --- modal (bottom sheet used for forms) -----------------------------------
def sheet(title, body):
    return (
        '<div class="modal">'
        '<div class="backdrop" data-action="close-modal"></div>'
        '<div class="sheet" role="dialog" aria-modal="true" aria-label="{t}">'
        '<div class="sheet-head"><h2>{t}</h2>'
        '<button class="icon-btn" data-action="close-modal" aria-label="Close">&times;</button></div>'
        "{b}</div></div>"
    ).format(t=esc(title), b=body)


def open_modal(markup):
    root = el("modal-root")
    root.innerHTML = markup
    first = root.querySelector("input, select")
    if first is not None:
        first.focus()


def close_modal():
    el("modal-root").innerHTML = ""


def modal_open():
    return el("modal-root").innerHTML != ""


def field_value(form, name):
    node = form.querySelector('[name="{}"]'.format(name))
    return node.value if node is not None else ""


def set_error(error_id, message, input_id=None):
    box = el(error_id)
    if box is not None:
        box.textContent = message
    if input_id:
        box2 = el(input_id)
        if box2 is not None:
            if message:
                box2.classList.add("invalid")
            else:
                box2.classList.remove("invalid")


def parse_identifier(text):
    """('email', value) or ('phone', 09XXXXXXXXX) or (None, None). The server checks again."""
    text = (text or "").strip()
    if "@" in text:
        e = text.lower()
        parts = e.split("@")
        ok = (len(parts) == 2 and parts[0] != "" and "." in parts[1] and " " not in e
              and not parts[1].startswith(".") and not parts[1].endswith("."))
        return ("email", e) if ok else (None, None)
    digits = "".join(ch for ch in text if ch not in " -().")
    if digits.startswith("+"):
        digits = digits[1:]
    if not digits or not all(ch in "0123456789" for ch in digits):
        return None, None
    if digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    if len(digits) == 11 and digits.startswith("09"):
        return "phone", digits
    return None, None


# --- talking to the server --------------------------------------------------
def store_token(token):
    try:
        if token:
            window.localStorage.setItem(TOKEN_KEY, token)
        else:
            window.localStorage.removeItem(TOKEN_KEY)
    except Exception:
        pass


def load_token():
    try:
        value = window.localStorage.getItem(TOKEN_KEY)
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


async def api(method, path, body=None):
    """Returns (status, data). status 0 means the server could not be reached."""
    headers = {"Content-Type": "application/json"}
    if state["token"]:
        headers["Authorization"] = "Bearer " + state["token"]
    kwargs = {"method": method, "headers": headers}
    if body is not None:
        kwargs["body"] = json.dumps(body)
    try:
        resp = await pyfetch(path, **kwargs)
        status = resp.status
        data = await resp.json()
    except Exception:
        return 0, {"error": "Cannot reach the server. Start it with: python run.py"}
    if status == 401 and state["token"]:
        local_signout("Please sign in again.")
    return status, data


async def call(method, path, body=None):
    """api() + keep the token and the shared data up to date. Returns (ok, data)."""
    status, data = await api(method, path, body)
    if status == 200 and isinstance(data, dict):
        if data.get("token"):
            state["token"] = data["token"]
            store_token(data["token"])
        if data.get("state"):
            apply_state(data["state"])
        return True, data
    state["err"] = (data or {}).get("error", "Something went wrong.") if isinstance(data, dict) else "Something went wrong."
    return False, data if isinstance(data, dict) else {}


async def request(path, body=None, method="POST", quiet=False):
    """One guarded request. Returns the data dict, or None on failure (error toast unless quiet)."""
    if state["busy"]:
        state["err"] = "Please wait a moment."
        return None
    state["busy"] = True
    try:
        ok, data = await call(method, path, body)
    finally:
        state["busy"] = False
    if not ok:
        if not quiet and state["err"]:
            toast(state["err"])
        return None
    return data


def apply_state(p):
    old_orders = state["orders"]
    if p.get("config"):
        apply_config(p["config"])
    state["me"] = p.get("me")
    state["version"] = p.get("version", "")
    state["orders"] = p.get("orders", [])
    state["my_card"] = p.get("my_card")
    state["cards"] = p.get("cards", [])
    state["stock"] = p.get("stock", [])
    state["staff"] = p.get("staff", [])
    state["activity"] = p.get("activity", [])
    state["customers"] = p.get("customers", 0)
    state["promo"] = p.get("promo")
    state["sold_today"] = p.get("sold_today", 0)
    state["extra_rev"] = p.get("extra_rev", 0)
    state["week_hist"] = p.get("week_hist", [None] * 6)
    if state["synced"]:
        notify_changes(old_orders, state["orders"])
    state["synced"] = True


def notify_changes(old, new):
    """Small toasts when something changes by itself (an order was placed or moved)."""
    previous = {o["id"]: o["status"] for o in old}
    if role() == "customer":
        said = {
            "frying": "Your order is being fried now.",
            "ready": "Your order is ready! Come to the cart.",
            "done": "Order picked up. Thank you!",
            "cancelled": "An order was cancelled.",
        }
        for o in new:
            before = previous.get(o["id"])
            if before is not None and before != o["status"] and o["status"] in said:
                toast("Order #{}: {}".format(o["id"], said[o["status"]]))
    elif is_staffish():
        for o in new:
            if o["id"] not in previous and o["status"] == "new" and o.get("customer_id"):
                toast("New order from {}: {} piece{}.".format(
                    o["name"], o["pieces"], "" if o["pieces"] == 1 else "s"))


# ---------------------------------------------------------------------------
# 4. SCREEN RENDERERS (each returns an HTML string)
# ---------------------------------------------------------------------------
ICONS = {
    "home": '<path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "sell": '<path d="M6 7h12l1 13H5z"/><path d="M9 7a3 3 0 0 1 6 0"/>',
    "orders": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4h6v3H9z"/><path d="M9 12h6M9 16h4"/>',
    "loyalty": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    "stock": '<path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.5 3-5.5 6.5-5.5s6.5 2 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.8c2.2.7 3.5 2.4 3.5 5.2"/>',
}
TABS = {
    "customer": [("corder", "Order", "sell"), ("cmine", "My orders", "orders"),
                 ("ccard", "My card", "loyalty"), ("account", "Account", "user")],
    "staff": [("home", "Home", "home"), ("sell", "Sell", "sell"), ("orders", "Orders", "orders"),
              ("loyalty", "Loyalty", "loyalty"), ("stock", "Stock", "stock")],
    "admin": [("home", "Home", "home"), ("orders", "Orders", "orders"), ("staff", "Staff", "users"),
              ("stock", "Stock", "stock"), ("loyalty", "Loyalty", "loyalty")],
}
TAB_FOR_SCREEN = {"reports": "home", "complete": "sell"}
ALLOWED = {
    None: ("signin", "signup", "about"),
    "customer": ("corder", "cmine", "ccard", "account", "about"),
    "staff": ("home", "sell", "complete", "orders", "loyalty", "stock", "about"),
    "admin": ("home", "sell", "complete", "orders", "loyalty", "stock", "staff", "reports", "about"),
}


def start_screen():
    return {"customer": "corder", "staff": "home", "admin": "home"}.get(role(), "signin")


def tab_badge(key):
    if key == "orders" and is_staffish():
        return count_status("new")
    if key == "cmine":
        return count_status("ready")
    return 0


def render_tabbar():
    tabs = TABS.get(role(), [])
    keys = [t[0] for t in tabs]
    active = TAB_FOR_SCREEN.get(state["screen"], state["screen"])
    if active not in keys:
        active = "home" if "home" in keys else (keys[0] if keys else "")
    out = []
    for key, label, icon in tabs:
        on = " active" if key == active else ""
        n = tab_badge(key)
        badge = '<span class="badge">{}</span>'.format(n) if n else ""
        out.append(
            '<button class="tab{on}" data-action="go" data-arg="{k}" aria-label="{l}">'
            '<span class="ico"><svg viewBox="0 0 24 24" aria-hidden="true">{i}</svg></span>{l}{b}</button>'.format(
                on=on, k=key, l=label, i=ICONS[icon], b=badge)
        )
    return "".join(out)


def password_field(field_id, label, name="password", autocomplete="current-password", error_id=None, show_arg=None):
    show = show_arg or field_id
    return (
        '<div class="field"><label for="{id}">{label}</label>'
        '<div class="pass-wrap"><input id="{id}" name="{name}" type="password" autocomplete="{ac}" '
        'placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;">'
        '<button type="button" class="show-btn" data-action="toggle-pass" data-arg="{show}">Show</button></div>'
        '<p class="field-error" id="{err}"></p></div>'
    ).format(id=field_id, label=label, name=name, ac=autocomplete, show=show, err=error_id or (field_id + "-error"))


# --- Promo video space + About / FAQs -------------------------------------------
PLAY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>'


def promo_html():
    """The promotional video space. Shows media/promo.mp4 when it exists, otherwise a friendly empty space."""
    promo = state.get("promo")
    if promo:
        inner = (
            '<video class="promo-video" controls playsinline preload="metadata" poster="/media/logo.jpg" '
            'src="{src}">Your browser cannot play this video.</video>'
        ).format(src=esc(promo["src"]))
    else:
        tip = ""
        if is_staffish():
            tip = '<span class="promo-tip">Owner tip: save your video as <b>media/promo.mp4</b> in the project folder, then refresh this page.</span>'
        inner = (
            '<div class="promo-empty"><span class="promo-play">{icon}</span>'
            '<b>Promo video coming soon</b><span>Watch how we make our crispy ube &amp; cheese turon.</span>{tip}</div>'
        ).format(icon=PLAY_ICON, tip=tip)
    return '<div class="card promo"><div class="promo-title">Watch: Ube-Kesoturon</div>{}</div>'.format(inner)


def faq_items():
    return [
        ("What is Ube-Kesoturon?",
         "A crispy turon with a sweet ube filling and melted cheese. We make it with lumpia wrappers, malagkit rice, "
         "ube condensed milk and cheese, and sell it from our cart in Cebu City."),
        ("How much is it?",
         "{price} per piece. You can add extra cheese for +{extra} per piece.".format(price=peso(PRICE), extra=peso(EXTRA_CHEESE_PRICE))),
        ("Do I need an account to buy?",
         "No. You can walk up to the cart and buy any time. An account is only needed to order ahead, follow your order "
         "live and keep your loyalty card in the app."),
        ("How do I order ahead?",
         "Tap Create a customer account on the sign in screen, then open the Order tab. Pick how many pieces, turn extra "
         "cheese on or off, choose your pickup time and tap Place order."),
        ("How do I pay?",
         "You pay when you pick up, with cash or e-wallet. There is no online payment in the app."),
        ("How do I know when my order is ready?",
         "Open My orders. It shows each step (New, Frying, Ready, Done) and updates by itself every few seconds. "
         "You also get a message when your order is ready."),
        ("Can I cancel an order?",
         "Yes, as long as the order is still New. Once we start frying it, it can no longer be cancelled."),
        ("How does the loyalty card work?",
         "Every purchase or picked-up order adds one stamp to your card. Collect {n} stamps and your next piece is free. "
         "Customers with an account can see their card under My card.".format(n=STAMPS_FOR_FREE)),
        ("I forgot my password. What do I do?",
         "On the sign in screen tap Forgot password?, enter your email or mobile number, then type the 6-digit code and a "
         "new password. The code works for 10 minutes."),
        ("Is my information safe?",
         "Your password is never stored as plain text. We only use your name and your email or mobile number for your "
         "account and your orders."),
    ]


def render_about():
    faqs = "".join(
        '<details class="faq"><summary>{q}</summary><p>{a}</p></details>'.format(q=esc(q), a=esc(a))
        for q, a in faq_items()
    )
    return """
    <div class="head"><div class="row">
      <button class="link-btn" data-action="about-back">&lsaquo; Back</button>
      <span class="eyebrow">About us &amp; FAQs</span></div></div>
    <div class="body about">
      <div class="about-hero">
        <img class="brand-logo" src="/media/logo.jpg" alt="Ube-Kesoturon - Crispy Ube &amp; Cheese Delight" width="168" height="168">
        <h1>Ube-Kesoturon</h1>
        <p class="muted">Crispy Ube &amp; Cheese Delight</p>
      </div>
      <div class="card">
        <div class="section-title" style="margin-top:0">About us</div>
        <p class="about-text">UbeKeso Cart is a turon cart in Cebu City. We take the Filipino favorite turon and fill it with
        sweet ube and gooey cheese, then fry it until the wrapper is golden and crunchy. You can order ahead from this
        app and pick it up hot at the cart.</p>
      </div>
      <div class="about-points">
        <div class="card"><b>Crispy</b><span>Golden, crunchy wrapper</span></div>
        <div class="card"><b>Ube</b><span>Sweet purple filling</span></div>
        <div class="card"><b>Cheesy</b><span>Melty cheese in every bite</span></div>
      </div>
      {promo}
      <div class="section-title">Frequently asked questions</div>
      <div class="card faq-list">{faqs}</div>
      <p class="muted small center-text">Still have a question? Ask the staff at the cart.</p>
    </div>
    """.format(promo=promo_html(), faqs=faqs)


# --- Sign in ------------------------------------------------------------------
def render_signin():
    return """
    <div class="signin">
      <img class="brand-logo" src="/media/logo.jpg" alt="Ube-Kesoturon - Crispy Ube &amp; Cheese Delight" width="132" height="132">
      <h1>Welcome back</h1>
      <p class="sub">Sign in with your email or mobile number.</p>
      <form id="signin-form" novalidate>
        <div class="field">
          <label for="email">Email or mobile number</label>
          <input id="email" name="identifier" type="text" placeholder="name@example.com or 09171234567" autocomplete="username">
          <p class="field-error" id="email-error"></p>
        </div>
        {pw}
        <button type="button" class="link-btn" data-action="forgot">Forgot password?</button>
        <div class="spacer"></div>
        <p class="field-error center" id="form-error"></p>
        <button type="submit" class="btn btn-primary" id="signin-btn">Sign in</button>
        <button type="button" class="btn btn-outline" data-action="to-signup">Create a customer account</button>
        <button type="button" class="link-btn center-link" data-action="go" data-arg="about">About us &amp; FAQs</button>
      </form>
      <p class="foot">UbeKeso Cart &middot; Cebu City</p>
    </div>
    """.format(pw=password_field("password", "Password", error_id="password-error"))


# --- Sign up (customers only) -------------------------------------------------
def render_signup():
    f = state["form"]
    email = f["kind"] == "email"
    return """
    <div class="signin">
      <img class="brand-logo" src="/media/logo.jpg" alt="Ube-Kesoturon - Crispy Ube &amp; Cheese Delight" width="132" height="132">
      <h1>Create account</h1>
      <p class="sub">Order ahead and collect loyalty stamps.</p>
      <form id="signup-form" novalidate>
        <div class="field">
          <label for="su-name">Your name</label>
          <input id="su-name" name="name" type="text" autocomplete="name" placeholder="e.g. Maria Santos" value="{name}">
          <p class="field-error" id="su-name-error"></p>
        </div>
        <div class="seg">
          <button type="button" class="{e_on}" data-action="idkind" data-arg="email">Email</button>
          <button type="button" class="{p_on}" data-action="idkind" data-arg="phone">Mobile number</button>
        </div>
        <div class="field">
          <label for="su-ident">{label}</label>
          <input id="su-ident" name="identifier" type="{typ}" autocomplete="{ac}" placeholder="{ph}" value="{ident}">
          <p class="field-error" id="su-ident-error"></p>
        </div>
        {pw}
        {cf}
        <p class="small-note">At least {minpw} characters.</p>
        <div class="spacer"></div>
        <p class="field-error center" id="su-form-error"></p>
        <button type="submit" class="btn btn-primary" id="signup-btn">Create account</button>
        <button type="button" class="btn btn-outline" data-action="to-signin">I already have an account</button>
      </form>
    </div>
    """.format(
        name=esc(f["name"]), ident=esc(f["ident"]),
        e_on="on" if email else "", p_on="" if email else "on",
        label="Email" if email else "Mobile number", typ="email" if email else "tel",
        ac="email" if email else "tel", ph="name@example.com" if email else "09171234567",
        pw=password_field("su-password", "Password", autocomplete="new-password", error_id="su-password-error"),
        cf=password_field("su-confirm", "Confirm password", name="confirm", autocomplete="new-password", error_id="su-confirm-error"),
        minpw=MIN_PASSWORD,
    )


# --- Customer: order ahead ----------------------------------------------------
def cust_total():
    c = state["cust"]
    return c["qty"] * (PRICE + (EXTRA_CHEESE_PRICE if c["extra"] else 0))


def render_corder():
    if state["cust"] is None:
        state["cust"] = new_cust()
    c = state["cust"]
    qty = c["qty"]
    total = cust_total()
    breakdown = ""
    if c["extra"] and qty:
        breakdown = '<div class="breakdown">{q} \u00d7 {p} + {q} \u00d7 {e} extra cheese</div>'.format(
            q=qty, p=peso(PRICE), e=peso(EXTRA_CHEESE_PRICE))
    return """
    <div class="head"><div class="eyebrow">Hi, {name}</div><h1>Order ahead</h1></div>
    <div class="body">
      {promo}
      <div class="card product">
        <div class="top"><div class="photo">\U0001F360</div>
          <div><div class="name">Ube cheese turon</div><div class="muted">{price} per piece</div></div></div>
        <div class="stepper-row"><span class="muted">Quantity</span>
          <div class="stepper">
            <button data-action="cqty" data-arg="-1" aria-label="Less" {minus_dis}>&minus;</button>
            <span class="qty">{qty}</span>
            <button class="plus" data-action="cqty" data-arg="1" aria-label="More">+</button>
          </div></div>
      </div>
      <div class="card row-between">
        <div><b>Extra cheese</b><div class="muted small">+{extra} per piece</div></div>
        <label class="switch"><input type="checkbox" data-change="cextra" aria-label="Extra cheese" {checked}><span class="slider"></span></label>
      </div>
      <div class="card">
        <div class="field" style="margin:0"><label for="c-pickup">Pickup time today</label>
          <input id="c-pickup" type="time" data-change="cpickup" value="{pickup}">
          <p class="field-error" id="cp-error"></p></div>
      </div>
      <div><div class="section-label">Pay at pickup</div>
        <div class="seg">
          <button class="{cash_on}" data-action="cpay" data-arg="cash">Cash</button>
          <button class="{ew_on}" data-action="cpay" data-arg="ewallet">E-wallet</button>
        </div></div>
    </div>
    <div class="pinned">
      {breakdown}
      <div class="total-line"><span>Total &middot; {qty} piece{pl}</span><span class="amt">{total}</span></div>
      <button class="btn btn-primary" data-action="place-order" {dis}>Place order {total}</button>
    </div>
    """.format(
        name=esc(first_name((state["me"] or {}).get("name"))), price=peso(PRICE), extra=peso(EXTRA_CHEESE_PRICE),
        promo=promo_html(), qty=qty, minus_dis="disabled" if qty <= 1 else "", checked="checked" if c["extra"] else "",
        pickup=esc(c["pickup"]), cash_on="on" if c["pay"] == "cash" else "", ew_on="on" if c["pay"] == "ewallet" else "",
        breakdown=breakdown, pl="" if qty == 1 else "s", total=peso(total), dis="disabled" if qty <= 0 else "",
    )


# --- Customer: my orders (live) -----------------------------------------------
STATUS_LABEL = {"new": "New", "frying": "Frying", "ready": "Ready", "done": "Done", "cancelled": "Cancelled"}
STATUS_TEXT = {
    "new": "Received. Waiting for the cart to start it.",
    "frying": "Being fried right now.",
    "ready": "Ready! Please come to the cart.",
    "done": "Picked up. Thank you!",
    "cancelled": "This order was cancelled.",
}
STEP_INDEX = {"new": 0, "frying": 1, "ready": 2, "done": 3}


def render_cmine():
    orders = sorted(state["orders"], key=lambda o: -o["id"])
    cards = ""
    for o in orders:
        steps = ""
        if o["status"] != "cancelled":
            idx = STEP_INDEX[o["status"]]
            steps = '<div class="steps">' + "".join(
                '<span class="s{}">{}</span>'.format(" on" if i <= idx else "", label)
                for i, label in enumerate(["Received", "Frying", "Ready"])) + "</div>"
        cancel = ""
        if o["status"] == "new":
            cancel = '<button class="btn btn-danger-outline btn-sm" data-action="order-cancel" data-arg="{}">Cancel order</button>'.format(o["id"])
        extra = ", extra cheese" if o["extra"] else ""
        cards += (
            '<div class="card order {s}"><div class="top"><b>Order #{i}</b><span class="pill {s}">{l}</span></div>'
            '<div class="meta"><span>{p} piece{pl}{e}</span><span>Pickup {t}</span></div>'
            '{steps}<div class="status-text">{txt}</div>'
            '<div class="meta"><span class="muted">Placed {placed}</span><span class="total">{tot} &middot; {pay}</span></div>{cancel}</div>'
        ).format(s=o["status"], i=o["id"], l=STATUS_LABEL[o["status"]], p=o["pieces"], pl="" if o["pieces"] == 1 else "s",
                 e=extra, t=fmt_time(o["pickup"]), steps=steps, txt=STATUS_TEXT[o["status"]],
                 placed=ts_label(o["created"]), tot=peso(o["total"]),
                 pay="Cash" if o.get("pay") == "cash" else "E-wallet", cancel=cancel)
    if not cards:
        cards = ('<div class="empty">You have no orders yet.</div>'
                 '<button class="btn btn-primary" data-action="go" data-arg="corder">Order now</button>')
    return """
    <div class="head"><div class="eyebrow">Live updates</div><h1>My orders</h1></div>
    <div class="body">{cards}</div>
    """.format(cards=cards)


# --- Loyalty card markup (customer + staff) -----------------------------------
def stamp_card_html(card):
    stamps = card["stamps"]
    full = stamps >= STAMPS_FOR_FREE
    circles = "".join('<span class="stamp{}"></span>'.format(" filled" if i < stamps else "")
                      for i in range(STAMPS_FOR_FREE))
    left_msg = "Ready: 1 free piece" if full else "{} more for 1 free piece".format(STAMPS_FOR_FREE - stamps)
    badge = '<span class="tag">Sample</span>' if card.get("sample") else ""
    return (
        '<div class="loy-card"><div class="top"><div><div class="holder">Card holder &middot; {id}</div>'
        '<div class="nm">{name}</div></div>{badge}</div>'
        '<div class="stamps">{circles}</div>'
        '<div class="foot"><span><b>{st} of {mx}</b> stamps</span><span>{left}</span></div></div>'
    ).format(id=esc(card["id"]), name=esc(card["name"]), badge=badge, circles=circles,
             st=stamps, mx=STAMPS_FOR_FREE, left=left_msg)


def render_ccard():
    card = state["my_card"]
    body = stamp_card_html(card) if card else '<div class="empty">No loyalty card yet.</div>'
    return """
    <div class="head"><div class="eyebrow">Loyalty</div><h1>My card</h1></div>
    <div class="body">{body}
      <div class="note-green">Buy {n} pieces, get the {n1}th free. A stamp is added each time an order is picked up.</div>
    </div>
    """.format(body=body, n=STAMPS_FOR_FREE, n1=STAMPS_FOR_FREE + 1)


# --- Account (customers) -------------------------------------------------------
def render_account():
    me = state["me"] or {}
    return """
    <div class="head"><div class="eyebrow">Account</div><h1>Your account</h1></div>
    <div class="body">
      <div class="card who-card"><div class="nm">{name}</div>
        <div class="muted">{contact}</div></div>
      <button class="btn btn-outline" data-action="go" data-arg="about">About us &amp; FAQs</button>
      <button class="btn btn-outline" data-action="change-pass">Change password</button>
      <button class="btn btn-primary" data-action="signout">Sign out</button>
    </div>
    """.format(name=esc(me.get("name", "")), contact=esc(me.get("email") or me.get("phone") or ""))


# --- Staff / admin: Home -------------------------------------------------------
def render_attention():
    new_n = count_status("new")
    staff = [s for s in state["staff"] if s["active"]]
    online = len([s for s in staff if s["online"]])
    row1 = (
        '<div class="item hot"><span>{n} new order{p} waiting</span>'
        '<button class="btn-pill" data-action="go" data-arg="orders">Open</button></div>'
    ).format(n=new_n, p="" if new_n == 1 else "s") if new_n else '<div class="item"><span class="muted">No new orders waiting</span></div>'
    row2 = (
        '<div class="item"><span>{on} of {total} staff online</span>'
        '<button class="btn-pill" data-action="go" data-arg="staff">See staff</button></div>'
    ).format(on=online, total=len(staff))
    return '<div class="card attn"><b>Needs attention</b>{}{}</div>'.format(row1, row2)


def render_home():
    me = state["me"] or {}
    admin = role() == "admin"
    sold = state["sold_today"]
    met = sold >= DAILY_TARGET
    pct = min(100, round(sold / DAILY_TARGET * 100))
    note = "Target met. Great work today!" if met else "{} more to reach today's target".format(DAILY_TARGET - sold)
    revenue = sold * PRICE + state["extra_rev"]
    sub = "{} pcs \u00d7 {}".format(sold, peso(PRICE))
    if state["extra_rev"]:
        sub += " + {} extras".format(peso(state["extra_rev"]))

    alert = ""
    low = low_items()
    if low:
        more = " (+{} more item{})".format(len(low) - 1, "" if len(low) == 2 else "s") if len(low) > 1 else ""
        alert = (
            '<div class="alert"><div><b>{n} is running low</b><span>Reorder before tomorrow\'s batch.{m}</span></div>'
            '<button class="btn-pill" data-action="go" data-arg="stock">Open stock</button></div>'
        ).format(n=esc(low[0]["name"]), m=more)

    pw_alert = ""
    if me.get("must_change"):
        pw_alert = (
            '<div class="alert"><div><b>Change your password</b>'
            '<span>You are using a temporary or default password.</span></div>'
            '<button class="btn-pill" data-action="change-pass">Change</button></div>'
        )

    attention = render_attention() if admin else ""
    goal = ""
    if admin:
        month_rev = revenue_for(month_pieces())
        month_pct = min(100, round(month_rev / MONTH_GOAL * 100))
        goal = (
            '<div class="card goal"><div class="top"><b>Monthly revenue goal</b><span class="muted">{goal}</span></div>'
            '<div class="track"><div class="fill" style="width:{mpct}%"></div></div>'
            '<div class="bottom"><button class="link-btn" data-action="go" data-arg="reports">See reports</button>'
            '<span class="muted small">{mrev} so far</span></div></div>'
        ).format(goal=peso(MONTH_GOAL), mpct=month_pct, mrev=peso(month_rev))

    return """
    <div class="head">
      <div class="row"><span class="eyebrow">UbeKeso Cart &middot; {date}</span>
        <span><button class="link-btn small" data-action="change-pass">Password</button>
        &nbsp;<button class="link-btn small" data-action="signout">Sign out</button></span></div>
      <div class="row"><h1>Today</h1><span class="tag">{who}</span></div>
      <div class="eyebrow">Signed in as {name}</div>
    </div>
    <div class="body">
      {pw_alert}
      <div class="progress-card{met_cls}">
        <div class="lbl">Pieces sold today</div>
        <div class="big">{sold}<span>of {target} target</span></div>
        <div class="track"><div class="fill" style="width:{pct}%"></div></div>
        <div class="note">{note}</div>
      </div>
      <div class="tiles">
        <div class="card tile"><div class="lbl">Revenue today</div><div class="val">{rev}</div><div class="sub">{sub}</div></div>
        <div class="card tile"><div class="lbl">Pre-orders waiting</div><div class="val">{wait}</div>
          <button class="sub link" data-action="go" data-arg="orders">See pre-orders</button></div>
      </div>
      {attention}
      {alert}
      <button class="btn btn-primary" data-action="new-sale">Start a new sale</button>
      {promo}
      {goal}
      <button class="btn btn-outline" data-action="go" data-arg="about">About us &amp; FAQs</button>
    </div>
    """.format(
        date=esc(date_label()), who="Owner" if admin else "Staff", name=esc(first_name(me.get("name", ""))), pw_alert=pw_alert,
        met_cls=" met" if met else "", sold=sold, target=DAILY_TARGET, pct=pct, note=note, rev=peso(revenue),
        sub=sub, wait=waiting_orders(), attention=attention, alert=alert, goal=goal, promo=promo_html(),
    )


# --- Staff: New sale -----------------------------------------------------------
def render_sell():
    s = state["sale"]
    qty = s["qty"]
    total = sale_total()

    if s["card"]:
        c = get_card(s["card"])
        loyalty = (
            '<div class="card loyalty-row row-between"><div><b>Loyalty card</b>'
            '<div class="attached">\u2713 {n} &middot; {st} of {mx} stamps</div></div>'
            '<div><button class="btn-pill" data-action="find-card">Change</button></div></div>'
            '<button class="link-btn" data-action="remove-card">Remove card from this sale</button>'
        ).format(n=esc(c["name"]) if c else "?", st=c["stamps"] if c else 0, mx=STAMPS_FOR_FREE)
    else:
        loyalty = (
            '<div class="card loyalty-row row-between"><div><b>Loyalty card</b>'
            '<div class="muted small">Add a stamp to this sale</div></div>'
            '<button class="btn-pill" data-action="find-card">Find card</button></div>'
        )

    breakdown = ""
    if s["extra"] and qty:
        breakdown = '<div class="breakdown">{q} \u00d7 {p} + {q} \u00d7 {e} extra cheese</div>'.format(
            q=qty, p=peso(PRICE), e=peso(EXTRA_CHEESE_PRICE))

    return """
    <div class="head"><div class="eyebrow">Sell</div><h1>New sale</h1></div>
    <div class="body">
      <div class="card product">
        <div class="top"><div class="photo">\U0001F360</div>
          <div><div class="name">Ube cheese turon</div><div class="muted">{price} per piece</div></div></div>
        <div class="stepper-row"><span class="muted">Quantity</span>
          <div class="stepper">
            <button data-action="qty" data-arg="-1" aria-label="Less" {minus_dis}>&minus;</button>
            <span class="qty">{qty}</span>
            <button class="plus" data-action="qty" data-arg="1" aria-label="More">+</button>
          </div></div>
      </div>
      <div class="card row-between">
        <div><b>Extra cheese</b><div class="muted small">+{extra} per piece</div></div>
        <label class="switch"><input type="checkbox" data-change="extra" aria-label="Extra cheese" {checked}><span class="slider"></span></label>
      </div>
      {loyalty}
      <div><div class="section-label">Payment</div>
        <div class="seg">
          <button class="{cash_on}" data-action="pay" data-arg="cash">Cash</button>
          <button class="{ew_on}" data-action="pay" data-arg="ewallet">E-wallet</button>
        </div></div>
    </div>
    <div class="pinned">
      {breakdown}
      <div class="total-line"><span>Total &middot; {qty} piece{pl}</span><span class="amt">{total}</span></div>
      <button class="btn btn-primary" data-action="charge" {charge_dis}>Charge {total}</button>
    </div>
    """.format(
        price=peso(PRICE), extra=peso(EXTRA_CHEESE_PRICE), qty=qty, minus_dis="disabled" if qty <= 0 else "",
        checked="checked" if s["extra"] else "", loyalty=loyalty,
        cash_on="on" if s["pay"] == "cash" else "", ew_on="on" if s["pay"] == "ewallet" else "",
        breakdown=breakdown, pl="" if qty == 1 else "s", total=peso(total),
        charge_dis="disabled" if qty <= 0 else "",
    )


# --- Staff: Sale complete ------------------------------------------------------
def render_complete():
    r = state["receipt"]
    if r is None:
        return render_home()
    lines = '<div class="line"><span>Ube cheese turon \u00d7 {q}</span><span>{a}</span></div>'.format(
        q=r["qty"], a=peso(r["qty"] * PRICE))
    if r["extra"]:
        lines += '<div class="line"><span>Extra cheese \u00d7 {q}</span><span>{a}</span></div>'.format(
            q=r["qty"], a=peso(r["qty"] * EXTRA_CHEESE_PRICE))
    lines += '<div class="line"><span>Payment</span><span>{}</span></div>'.format(
        "Cash" if r["pay"] == "cash" else "E-wallet")
    if r["pay"] == "ewallet":
        lines += '<div class="line"><span>Reference</span><span>{}</span></div>'.format(esc(r["ref"]))
    lines += '<div class="line total"><span>Total</span><span>{}</span></div>'.format(peso(r["total"]))
    stamp = '<div class="note-gold">{}</div>'.format(esc(r["stamp_msg"])) if r["stamp_msg"] else ""
    return """
    <div class="body complete">
      <div class="check"><span>\u2713</span></div>
      <h1>Sale complete</h1>
      <div class="card receipt">{lines}</div>
      {stamp}
      <div class="spacer"></div>
      <button class="btn btn-primary" data-action="new-sale">New sale</button>
      <button class="btn btn-outline" data-action="go" data-arg="home">Back to home</button>
    </div>
    """.format(lines=lines, stamp=stamp)


# --- Staff / admin: orders (live) ----------------------------------------------
def render_orders():
    flt = state["order_filter"]
    counts = {k: count_status(k) for k in STATUS_LABEL}
    chips = ""
    for key, label in STATUS_LABEL.items():
        text = "{} \u00b7 {}".format(label, counts[key]) if counts[key] else label
        chips += '<button class="chip{on}" data-action="filter" data-arg="{k}">{t}</button>'.format(
            on=" on" if key == flt else "", k=key, t=text)

    shown = [o for o in state["orders"] if o["status"] == flt]
    shown.sort(key=lambda o: o["pickup"] if flt in ("new", "frying", "ready") else "", reverse=False)
    if flt in ("done", "cancelled"):
        shown.sort(key=lambda o: -o["updated"])
    cards = ""
    for o in shown:
        extra = ", extra cheese" if o["extra"] else ""
        details = '<button class="btn btn-outline btn-sm" data-action="order-details" data-arg="{}">Details</button>'.format(o["id"])
        if o["status"] == "new":
            buttons = '<div class="btn-row">{d}<button class="btn btn-primary btn-sm" data-action="order-next" data-arg="{i}">Start frying</button></div>'.format(d=details, i=o["id"])
        elif o["status"] == "frying":
            buttons = '<div class="btn-row">{d}<button class="btn btn-green btn-sm" data-action="order-next" data-arg="{i}">Mark ready</button></div>'.format(d=details, i=o["id"])
        elif o["status"] == "ready":
            buttons = '<div class="btn-row">{d}<button class="btn btn-outline-green btn-sm" data-action="order-next" data-arg="{i}">Picked up and paid</button></div>'.format(d=details, i=o["id"])
        else:
            buttons = '<div class="btn-row">{}</div>'.format(details)
        source = "App" if o.get("customer_id") else "Walk-in"
        sub = '<span class="src">{}</span><span>#{}</span><span>Placed {}</span>'.format(source, o["id"], ts_label(o["created"]))
        if o.get("handled_by"):
            sub += "<span>Started by {}</span>".format(esc(first_name(o["handled_by"])))
        cards += (
            '<div class="card order {s}"><div class="top"><b>{n}</b><span class="pill {s}">{l}</span></div>'
            '<div class="meta"><span>{p} piece{pl}{e}</span><span>Pickup {t}</span></div>'
            '<div class="sub-meta">{sub}</div>{b}</div>'
        ).format(s=o["status"], n=esc(o["name"]), l=STATUS_LABEL[o["status"]], p=o["pieces"],
                 pl="" if o["pieces"] == 1 else "s", e=extra, t=fmt_time(o["pickup"]), sub=sub, b=buttons)
    if not cards:
        cards = '<div class="empty">No {} orders right now.</div>'.format(STATUS_LABEL[flt].lower())

    return """
    <div class="head"><div class="eyebrow">{eyebrow}</div>
      <div class="row"><h1>Pre-orders</h1>
      <button class="round-add" data-action="add-order" aria-label="Add pre-order">+</button></div></div>
    <div class="body"><div class="chips">{chips}</div>{cards}</div>
    """.format(eyebrow="All orders &middot; live" if role() == "admin" else "Orders &middot; live", chips=chips, cards=cards)


# --- Staff / admin: Loyalty -----------------------------------------------------
def find_cards(query):
    q = query.strip().lower()
    if not q:
        return []
    return [c for c in state["cards"] if q in c["name"].lower() or q in c["id"].lower()]


def render_loyalty_body():
    """Everything under the search box. Redrawn on every keystroke (input event)."""
    query = state["loyalty_query"].strip()
    rule = '<div class="note-green">Rule from the plan: buy {} pieces, get the 11th free.</div>'.format(STAMPS_FOR_FREE)
    chips = ""
    if query:
        matches = find_cards(query)
        if not matches:
            return (
                '<div class="card nocard"><b>No card found</b>'
                '<span class="muted">Nothing matches &ldquo;{q}&rdquo;. Create a card for this customer.</span></div>'
                '<button class="btn btn-primary" data-action="new-card">Create a new card</button>{r}'
            ).format(q=esc(query), r=rule)
        if state["selected_card"] not in [m["id"] for m in matches]:
            state["selected_card"] = matches[0]["id"]
        if len(matches) > 1:
            chips = '<div class="match-chips">' + "".join(
                '<button class="chip{on}" data-action="pick-card" data-arg="{i}">{n}</button>'.format(
                    on=" on" if m["id"] == state["selected_card"] else "", i=m["id"], n=esc(m["name"]))
                for m in matches) + "</div>"

    card = get_card(state["selected_card"]) or (state["cards"][0] if state["cards"] else None)
    if card is None:
        return '<div class="empty">No loyalty cards yet.</div><button class="btn btn-primary" data-action="new-card">Create a new card</button>'
    full = card["stamps"] >= STAMPS_FOR_FREE

    if full:
        add_btn = '<button class="btn btn-primary" disabled>Card is full. Redeem first</button>'
        redeem = '<button class="btn btn-green" data-action="redeem">Redeem free piece</button>'
    else:
        add_btn = '<button class="btn btn-primary" data-action="add-stamp">{}</button>'.format(
            "Add a stamp to this sale" if state["picking"] else "Add a stamp")
        redeem = '<button class="btn" disabled>Redeem free piece (needs {} stamps)</button>'.format(STAMPS_FOR_FREE)

    return '{chips}{card}{add}{redeem}<button class="btn btn-outline" data-action="new-card">Create a new card</button>{rule}'.format(
        chips=chips, card=stamp_card_html(card), add=add_btn, redeem=redeem, rule=rule)


def render_loyalty():
    return """
    <div class="head"><div class="eyebrow">Loyalty</div><h1>Loyalty card</h1></div>
    <div class="body">
      <div class="search"><input id="loyalty-search" type="search" placeholder="Find card by name or number"
        value="{q}" autocomplete="off" aria-label="Find card by name or number"></div>
      <div id="loyalty-body" class="body" style="padding:0;overflow:visible">{body}</div>
    </div>
    """.format(q=esc(state["loyalty_query"]), body=render_loyalty_body())


# --- Staff / admin: Stock ---------------------------------------------------------
def render_stock():
    low = low_items()
    if low:
        names = [s["name"].lower() for s in low]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        joined = joined[0].upper() + joined[1:]
        banner = '<div class="banner"><b>{n} item{p} need{v} reordering</b><span>{j} {be} below the minimum.</span></div>'.format(
            n=len(low), p="" if len(low) == 1 else "s", v="s" if len(low) == 1 else "", j=esc(joined),
            be="is" if len(low) == 1 else "are")
    else:
        banner = '<div class="banner ok"><b>All ingredients are stocked</b><span>Nothing to reorder right now.</span></div>'

    rows = ""
    for s in state["stock"]:
        key, label = stock_status(s["left"])
        width = min(100, round(s["left"] / STOCK_BAR_MAX * 100))
        rows += (
            '<div class="card ing {k}"><div class="top"><b>{n}</b><span class="pill {k}">{l}</span></div>'
            '<div class="level"><i class="{k}" style="width:{w}%"></i></div>'
            '<div class="meta"><span>Enough for about {left} pieces</span><span>Minimum: 1 day</span></div></div>'
        ).format(k=key, n=esc(s["name"]), l=label, w=width, left=s["left"])

    return """
    <div class="head"><div class="eyebrow">Stock</div>
      <div class="row"><h1>Ingredients</h1></div></div>
    <div class="body">{banner}{rows}
      <div class="btn-row">
        <button class="btn btn-outline" data-action="log-usage">Log usage</button>
        <button class="btn btn-primary" data-action="add-delivery">Add delivery</button>
      </div></div>
    """.format(banner=banner, rows=rows)


# --- Admin: Reports ------------------------------------------------------------------
def render_reports():
    week = state["range"] == "week"
    ti = today_index()
    if week:
        vals, labels, target, today_i = week_values(), DAY_SHORT, DAILY_TARGET, ti
        title, legend = "Pieces sold per day", "Light bar: today so far &nbsp;&middot;&nbsp; Dashed line: target"
        pieces, days = sum(vals), ti + 1
    else:
        vals = MONTH_SAMPLE_WEEKS + [sum(week_values())]
        labels, target, today_i = ["Wk 1", "Wk 2", "Wk 3", "Wk 4"], DAILY_TARGET * 6, 3
        title, legend = "Pieces sold per week", "Light bar: this week so far &nbsp;&middot;&nbsp; Dashed line: target"
        pieces, days = month_pieces(), MONTH_DAYS_BEFORE_THIS_WEEK + ti + 1

    revenue = revenue_for(pieces)
    avg = round(pieces / days)
    profit = revenue - pieces * COST_PER_PIECE
    met = avg >= DAILY_TARGET
    scale = max(target, max(vals)) * 1.12

    bars = "".join(
        '<div class="bar-col"><div class="bar{t}" style="height:{h}%"></div></div>'.format(
            t=" today" if i == today_i else "", h=round(v / scale * 100, 1))
        for i, v in enumerate(vals))
    lbls = "".join('<div>{l}<b>{v}</b></div>'.format(l=l, v=v) for l, v in zip(labels, vals))
    avg_sub = '<div class="sub">Target met</div>' if met else '<div class="sub bad">{} below target</div>'.format(DAILY_TARGET - avg)

    return """
    <div class="head"><button class="back" data-action="go" data-arg="home">&lsaquo; Home</button>
      <div class="row"><h1>Reports</h1></div></div>
    <div class="body">
      <div class="segmented">
        <button class="{w}" data-action="range" data-arg="week">This week</button>
        <button class="{m}" data-action="range" data-arg="month">This month</button>
      </div>
      <div class="card chart-card">
        <h3>{title}<span>Target {target}</span></h3>
        <div class="chart-wrap">
          <div class="target-line" style="bottom:{tl}%"></div>
          <div class="bars">{bars}</div>
        </div>
        <div class="labels">{lbls}</div>
        <div class="legend">{legend}</div>
      </div>
      <div class="tiles">
        <div class="card tile"><div class="lbl">Revenue</div><div class="val">{rev}</div>
          <div class="sub" style="color:var(--muted);font-weight:500">{pieces} pcs \u00d7 {price}</div></div>
        <div class="card tile{met_cls}"><div class="lbl">Average per day</div><div class="val">{avg}</div>{avg_sub}</div>
      </div>
      <div class="profit"><div><b>Profit before other costs</b>
        <span>{price} price \u2212 {cost} ingredients per piece</span></div><div class="amt">{profit}</div></div>
    </div>
    """.format(
        w="on" if week else "", m="" if week else "on", title=title, target=target,
        tl=round(target / scale * 100, 1), bars=bars, lbls=lbls, legend=legend, rev=peso(revenue),
        pieces=pieces, price=peso(PRICE), met_cls=" met" if met else "", avg=avg, avg_sub=avg_sub,
        cost=peso(COST_PER_PIECE), profit=peso(profit),
    )


# --- Admin: Staff accounts + activity log ----------------------------------------
ROLE_LABEL = {"admin": "Owner", "staff": "Staff", "customer": "Customer", "system": "System"}


def render_staff():
    staff = state["staff"]
    active = [s for s in staff if s["active"]]
    online = len([s for s in active if s["online"]])

    cards = ""
    for s in staff:
        if not s["active"]:
            key, label = "disabled", "Disabled"
        elif s["online"]:
            key, label = "online", "Online"
        else:
            key, label = "offline", "Offline"
        toggle = (
            '<button class="btn btn-outline btn-sm" data-action="staff-active" data-arg="{i}:0">Disable account</button>'
            if s["active"] else
            '<button class="btn btn-primary btn-sm" data-action="staff-active" data-arg="{i}:1">Enable account</button>'
        ).format(i=esc(s["id"]))
        cards += (
            '<div class="card staff-card"><div class="top"><b>{n}</b><span class="pill {k}">{l}</span></div>'
            '<div class="meta muted small"><span>{c}</span><span>Last sign in {ls}</span></div>{t}</div>'
        ).format(n=esc(first_name(s["name"])), k=key, l=label, c=esc(s["email"] or s["phone"] or ""),
                 ls=ts_label(s["last_login"]), t=toggle)
    if not cards:
        cards = '<div class="empty">No staff accounts yet. Tap + to create one.</div>'

    flt = state["act_filter"]
    chips = '<button class="chip{on}" data-action="act-filter" data-arg="all">Everyone</button>'.format(
        on=" on" if flt == "all" else "")
    for s in staff:
        chips += '<button class="chip{on}" data-action="act-filter" data-arg="{i}">{n}</button>'.format(
            on=" on" if flt == s["id"] else "", i=esc(s["id"]), n=esc(first_name(s["name"])))
    entries = [a for a in state["activity"] if flt == "all" or a.get("actor_id") == flt][:60]
    clear_btn = ""
    if entries:
        who_name = next((first_name(s["name"]) for s in staff if s["id"] == flt), "")
        clear_btn = '<button class="link-btn act-clear" data-action="act-clear">{}</button>'.format(
            "Clear all" if flt == "all" else "Clear {}'s entries".format(who_name))
    feed = "".join(
        '<div class="act"><div class="who"><b>{who}<span class="role {r}">{rl}</span></b><span>{t}</span></div>'
        '<div class="act-row"><div class="what">{txt}</div>'
        '<button class="act-del" data-action="act-delete" data-arg="{aid}" aria-label="Delete this entry">Delete</button></div></div>'.format(
            who=esc(first_name(a["actor"]) if a["role"] == "staff" else a["actor"]), r=esc(a["role"]),
            rl=ROLE_LABEL.get(a["role"], ""), t=ts_label(a["ts"]), txt=esc(a["text"]), aid=esc(a.get("id", "")))
        for a in entries) or '<div class="empty">No activity yet.</div>'

    return """
    <div class="head"><div class="eyebrow">Owner</div>
      <div class="row"><h1>Staff</h1>
      <button class="round-add" data-action="add-staff" aria-label="Create staff account">+</button></div></div>
    <div class="body">
      <div class="tiles">
        <div class="card tile"><div class="lbl">Staff online</div><div class="val">{on} of {total}</div></div>
        <div class="card tile"><div class="lbl">Customers</div><div class="val">{cust}</div></div>
      </div>
      {cards}
      <div class="row-between"><div class="section-title">Activity log</div>{clear}</div>
      <div class="chips">{chips}</div>
      <div class="card">{feed}</div>
    </div>
    """.format(on=online, total=len(active), cust=state["customers"], cards=cards, chips=chips, feed=feed, clear=clear_btn)


RENDERERS = {
    "about": render_about, "signin": render_signin, "signup": render_signup, "corder": render_corder, "cmine": render_cmine,
    "ccard": render_ccard, "account": render_account, "home": render_home, "sell": render_sell,
    "complete": render_complete, "orders": render_orders, "loyalty": render_loyalty, "stock": render_stock,
    "reports": render_reports, "staff": render_staff,
}


def render():
    """Redraw the current screen and the tab bar from `state`."""
    screen = state["screen"]
    holder = el("screen")
    previous = holder.querySelector(".body")
    keep = previous.scrollTop if (previous is not None and state["_last"] == screen) else 0

    # A playing promo video must not restart every time the screen redraws (live order updates redraw often).
    kept = holder.querySelector("video.promo-video") if state["_last"] == screen else None
    was_playing = False
    if kept is not None:
        was_playing = not kept.paused
        kept.remove()

    holder.innerHTML = RENDERERS[screen]()

    if kept is not None:
        slot = holder.querySelector("video.promo-video")
        if slot is not None and str(slot.getAttribute("src")) == str(kept.getAttribute("src")):
            slot.replaceWith(kept)
            if was_playing:
                try:
                    kept.play()
                except Exception:
                    pass

    body = holder.querySelector(".body")
    if body is not None and keep:
        body.scrollTop = keep
    state["_last"] = screen

    phone = el("phone")
    if screen in ("signin", "signup") or (screen == "about" and role() is None):
        phone.classList.add("no-tabs")
        el("tabbar").innerHTML = ""
    else:
        phone.classList.remove("no-tabs")
        el("tabbar").innerHTML = render_tabbar()


# ---------------------------------------------------------------------------
# 5. EVENT HANDLERS
# ---------------------------------------------------------------------------
def navigate(screen):
    close_modal()
    if screen not in ALLOWED.get(role(), ()):
        screen = start_screen()
    state["screen"] = screen
    render()


def begin_session():
    """Called after a successful sign in or sign up."""
    state["sale"] = new_sale()
    state["cust"] = new_cust()
    state["form"] = new_form()
    state["order_filter"] = "new"
    state["act_filter"] = "all"
    navigate(start_screen())


def local_signout(message=None):
    store_token(None)
    state.update({"token": None, "me": None, "version": "", "synced": False, "orders": [], "cards": [],
                  "stock": [], "staff": [], "activity": [], "my_card": None, "sold_today": 0, "extra_rev": 0,
                  "sale": new_sale(), "cust": None, "receipt": None, "picking": False})
    state["screen"] = "signin"
    close_modal()
    render()
    if message:
        toast(message)


# ----- click actions (arg comes from data-arg in the HTML) -----
def act_go(arg):
    state["picking"] = False
    if arg == "loyalty":
        state["loyalty_query"] = ""
    navigate(arg)


def act_about_back(_arg=None):
    navigate(start_screen())


def act_to_signup(_arg=None):
    state["form"] = new_form()
    navigate("signup")


def act_to_signin(_arg=None):
    navigate("signin")


def act_idkind(arg):
    """Switch the sign up form between email and mobile number without losing what was typed."""
    f = state["form"]
    for key, eid in (("name", "su-name"),):
        node = el(eid)
        if node is not None:
            f[key] = node.value
    pw, cf = el("su-password"), el("su-confirm")
    pw_v = pw.value if pw is not None else ""
    cf_v = cf.value if cf is not None else ""
    f["kind"] = arg
    f["ident"] = ""
    render()
    if el("su-password") is not None:
        el("su-password").value = pw_v
        el("su-confirm").value = cf_v


def act_new_sale(_arg=None):
    state["sale"] = new_sale()
    state["picking"] = False
    navigate("sell")


async def act_signout(_arg=None):
    await api("POST", "/api/logout", {})
    local_signout()


def act_toggle_pass(arg):
    box = el(arg)
    btn = document.querySelector('[data-action="toggle-pass"][data-arg="{}"]'.format(arg))
    if box is None:
        return
    showing = box.type == "text"
    box.type = "password" if showing else "text"
    if btn is not None:
        btn.textContent = "Show" if showing else "Hide"


def act_close_modal(_arg=None):
    close_modal()


# ----- forgot password (email or mobile number) -----
def forgot_form_html():
    ident = esc(state["reset"]["ident"]) if state["reset"] else ""
    return (
        '<form id="forgot-form" novalidate>'
        '<p class="small-note" style="margin-top:0">Enter the email or mobile number of your account. We will send you a 6-digit code.</p>'
        '<div class="field"><label for="f-ident">Email or mobile number</label>'
        '<input id="f-ident" name="identifier" type="text" placeholder="name@example.com or 09171234567" value="{v}">'
        '<p class="field-error" id="f-error"></p></div>'
        '<button class="btn btn-primary" type="submit">Send code</button></form>'
    ).format(v=ident)


def reset_form_html():
    r = state["reset"]
    demo = ""
    if r.get("demo"):
        demo = ('<div class="note-green">Demo mode: no real SMS or email is sent. Your code is <b>{}</b>. '
                'It is also printed in the server terminal.</div>').format(esc(r["demo"]))
    return (
        '<form id="reset-form" novalidate>'
        '<p class="small-note" style="margin-top:0">If an account exists for {ident}, a 6-digit code was sent. It works for 10 minutes.</p>'
        '{demo}'
        '<div class="field"><label for="r-code">6-digit code</label>'
        '<input id="r-code" name="code" type="text" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="123456">'
        '<p class="field-error" id="r-code-error"></p></div>'
        '{pw}{cf}'
        '<p class="field-error center" id="r-error"></p>'
        '<button class="btn btn-primary" type="submit">Change password</button>'
        '<button type="button" class="link-btn" data-action="forgot-again">Use a different email or number</button></form>'
    ).format(
        ident=esc(r["ident"]), demo=demo,
        pw=password_field("r-pass", "New password", name="password", autocomplete="new-password", error_id="r-pass-error"),
        cf=password_field("r-confirm", "Confirm new password", name="confirm", autocomplete="new-password", error_id="r-confirm-error"),
    )


def act_forgot(_arg=None):
    state["reset"] = None
    open_modal(sheet("Reset password", forgot_form_html()))


def act_forgot_again(_arg=None):
    open_modal(sheet("Reset password", forgot_form_html()))


# ----- change password (signed in) -----
def act_change_pass(_arg=None):
    body = (
        '<form id="pass-form" novalidate>{old}{new}{cf}'
        '<p class="field-error center" id="p-error"></p>'
        '<button class="btn btn-primary" type="submit">Save new password</button></form>'
    ).format(
        old=password_field("p-old", "Current password", name="old", autocomplete="current-password", error_id="p-old-error"),
        new=password_field("p-new", "New password", name="new", autocomplete="new-password", error_id="p-new-error"),
        cf=password_field("p-confirm", "Confirm new password", name="confirm", autocomplete="new-password", error_id="p-confirm-error"),
    )
    open_modal(sheet("Change password", body))


# ----- customer ordering -----
def act_cqty(arg):
    c = state["cust"]
    c["qty"] = max(1, min(60, c["qty"] + int(arg)))
    render()


def act_cpay(arg):
    state["cust"]["pay"] = arg
    render()


async def act_place_order(_arg=None):
    c = state["cust"]
    if c["qty"] <= 0:
        return
    if not c["pickup"]:
        set_error("cp-error", "Choose a pickup time.", "c-pickup")
        return
    data = await request("/api/orders", {"pieces": c["qty"], "extra": c["extra"], "pickup": c["pickup"], "pay": c["pay"]})
    if data is None:
        return
    state["cust"] = new_cust()
    navigate("cmine")
    toast("Order #{} placed. Watch it here.".format(data["order_id"]))


async def act_order_cancel(arg):
    data = await request("/api/orders/action", {"id": int(arg), "action": "cancel"})
    if data is not None:
        render()
        toast(data.get("message", "Order cancelled."))


# ----- selling (staff) -----
def act_qty(arg):
    state["sale"]["qty"] = max(0, state["sale"]["qty"] + int(arg))
    render()


def act_pay(arg):
    state["sale"]["pay"] = arg
    render()


def act_find_card(_arg=None):
    state["picking"] = True
    state["loyalty_query"] = ""
    if state["sale"]["card"]:
        state["selected_card"] = state["sale"]["card"]
    navigate("loyalty")


def act_remove_card(_arg=None):
    state["sale"]["card"] = None
    render()


async def act_charge(_arg=None):
    s = state["sale"]
    if s["qty"] <= 0:
        return
    data = await request("/api/sale", {"qty": s["qty"], "extra": s["extra"], "pay": s["pay"], "card_id": s["card"]})
    if data is None:
        return
    state["receipt"] = data["receipt"]
    state["sale"] = new_sale()
    navigate("complete")


# ----- loyalty (staff) -----
def act_pick_card(arg):
    state["selected_card"] = arg
    render_loyalty_area()


async def act_add_stamp(_arg=None):
    card = get_card(state["selected_card"])
    if card is None:
        return
    if state["picking"]:
        state["sale"]["card"] = card["id"]
        state["picking"] = False
        navigate("sell")
        toast("Card attached. The stamp is added when you charge.")
        return
    data = await request("/api/cards/stamp", {"id": card["id"]})
    if data is not None:
        toast(data.get("message", "Stamp added."))
        render_loyalty_area()


async def act_redeem(_arg=None):
    card = get_card(state["selected_card"])
    if card is None:
        return
    data = await request("/api/cards/redeem", {"id": card["id"]})
    if data is not None:
        toast(data.get("message", "Redeemed."))
        render_loyalty_area()


def act_new_card(_arg=None):
    body = (
        '<form id="card-form" novalidate><div class="field"><label for="c-name">Customer name</label>'
        '<input id="c-name" name="name" type="text" value="{v}" placeholder="e.g. Maria Santos">'
        '<p class="field-error" id="c-error"></p></div>'
        '<button class="btn btn-primary" type="submit">Create card</button></form>'
    ).format(v=esc(state["loyalty_query"]))
    open_modal(sheet("New loyalty card", body))


def render_loyalty_area():
    area = el("loyalty-body")
    if area is not None:
        area.innerHTML = render_loyalty_body()
    else:
        render()


# ----- orders (staff / admin) -----
def act_filter(arg):
    state["order_filter"] = arg
    render()


def find_order(order_id):
    for o in state["orders"]:
        if str(o["id"]) == str(order_id):
            return o
    return None


async def act_order_next(arg):
    data = await request("/api/orders/action", {"id": int(arg), "action": "next"})
    if data is None:
        return
    o = find_order(arg)
    if o is not None and o["status"] != "done":
        state["order_filter"] = o["status"]
    render()
    toast(data.get("message", "Updated."))


def act_order_details(arg):
    o = find_order(arg)
    if o is None:
        return
    rows = [
        ("Order", "#{}".format(o["id"])),
        ("Source", "App" if o.get("customer_id") else "Walk-in or phone"),
        ("Contact", o.get("contact") or "-"),
        ("Pieces", str(o["pieces"])),
        ("Extra cheese", "Yes" if o["extra"] else "No"),
        ("Pickup", fmt_time(o["pickup"])),
        ("Pay at pickup", "Cash" if o.get("pay") == "cash" else "E-wallet"),
        ("Placed", ts_label(o["created"])),
        ("Started by", first_name(o.get("handled_by")) or "-"),
        ("Order total", peso(o["total"])),
    ]
    items = '<div><span>Status</span><span class="pill {s}">{sl}</span></div>'.format(
        s=o["status"], sl=STATUS_LABEL[o["status"]])
    items += "".join('<div><span>{}</span><b>{}</b></div>'.format(esc(k), esc(v)) for k, v in rows)
    body = '<div class="detail-list">{}</div><button class="btn btn-outline" data-action="close-modal">Close</button>'.format(items)
    open_modal(sheet(o["name"], body))


def act_add_order(_arg=None):
    body = (
        '<form id="order-form" novalidate>'
        '<div class="field"><label for="o-name">Customer name</label><input id="o-name" name="name" type="text" placeholder="e.g. Liza Fernandez">'
        '<p class="field-error" id="o-name-error"></p></div>'
        '<div class="field"><label for="o-pieces">Pieces</label><input id="o-pieces" name="pieces" type="number" min="1" value="3">'
        '<p class="field-error" id="o-pieces-error"></p></div>'
        '<div class="field"><label for="o-time">Pickup time</label><input id="o-time" name="pickup" type="time" value="{t}">'
        '<p class="field-error" id="o-time-error"></p></div>'
        '<label class="check-row"><input type="checkbox" name="extra"> Extra cheese</label>'
        '<button class="btn btn-primary" type="submit">Save pre-order</button></form>'
    ).format(t=default_pickup())
    open_modal(sheet("New pre-order", body))


# ----- stock -----
def stock_form(mode):
    options = "".join('<option value="{i}">{n}</option>'.format(i=i, n=esc(s["name"])) for i, s in enumerate(state["stock"]))
    label = "Pieces' worth added" if mode == "delivery" else "Pieces' worth used"
    btn = "Save delivery" if mode == "delivery" else "Save usage"
    body = (
        '<form id="stock-form" data-mode="{m}" novalidate>'
        '<div class="field"><label for="s-item">Ingredient</label><select id="s-item" name="item">{o}</select></div>'
        '<div class="field"><label for="s-amt">{l}</label><input id="s-amt" name="amount" type="number" min="1" placeholder="e.g. 20">'
        '<p class="field-error" id="s-error"></p></div>'
        '<button class="btn btn-primary" type="submit">{b}</button></form>'
    ).format(m=mode, o=options, l=label, b=btn)
    open_modal(sheet("Add delivery" if mode == "delivery" else "Log usage", body))


def act_add_delivery(_arg=None):
    stock_form("delivery")


def act_log_usage(_arg=None):
    stock_form("usage")


# ----- reports -----
def act_range(arg):
    state["range"] = arg
    render()


# ----- staff accounts (admin) -----
def act_add_staff(_arg=None):
    body = (
        '<form id="staff-form" novalidate>'
        '<div class="field"><label for="st-name">Staff name</label><input id="st-name" name="name" type="text" placeholder="e.g. Pedro Cruz">'
        '<p class="field-error" id="st-name-error"></p></div>'
        '<div class="field"><label for="st-ident">Email or mobile number</label>'
        '<input id="st-ident" name="identifier" type="text" placeholder="name@example.com or 09171234567">'
        '<p class="field-error" id="st-ident-error"></p></div>'
        '{pw}'
        '<p class="small-note">Give this temporary password to your staff. They will be asked to change it.</p>'
        '<button class="btn btn-primary" type="submit">Create staff account</button></form>'
    ).format(pw=password_field("st-pass", "Temporary password", autocomplete="new-password", error_id="st-pass-error"))
    open_modal(sheet("New staff account", body))


async def act_staff_active(arg):
    staff_id, flag = arg.rsplit(":", 1)
    data = await request("/api/staff/active", {"id": staff_id, "active": flag == "1"})
    if data is not None:
        render()
        toast(data.get("message", "Updated."))


def act_act_filter(arg):
    state["act_filter"] = arg
    render()


async def act_act_delete(arg):
    data = await request("/api/activity/delete", {"ids": [arg]})
    if data is not None:
        render()
        toast(data.get("message", "Entry deleted."))


async def act_act_clear(_arg=None):
    flt = state["act_filter"]
    if flt == "all":
        question = "Delete the whole activity log? This cannot be undone."
        body = {"all": True}
    else:
        ids = [a["id"] for a in state["activity"] if a.get("actor_id") == flt and a.get("id")]
        question = "Delete all of this person's entries? This cannot be undone."
        body = {"ids": ids}
    if not window.confirm(question):
        return
    data = await request("/api/activity/delete", body)
    if data is not None:
        render()
        toast(data.get("message", "Deleted."))


CLICK_ACTIONS = {
    "go": act_go, "new-sale": act_new_sale, "signout": act_signout, "to-signup": act_to_signup,
    "to-signin": act_to_signin, "idkind": act_idkind, "toggle-pass": act_toggle_pass, "forgot": act_forgot,
    "forgot-again": act_forgot_again, "change-pass": act_change_pass, "close-modal": act_close_modal,
    "cqty": act_cqty, "cpay": act_cpay, "place-order": act_place_order, "order-cancel": act_order_cancel,
    "qty": act_qty, "pay": act_pay, "find-card": act_find_card, "remove-card": act_remove_card,
    "charge": act_charge, "pick-card": act_pick_card, "add-stamp": act_add_stamp, "redeem": act_redeem,
    "new-card": act_new_card, "filter": act_filter, "order-next": act_order_next,
    "order-details": act_order_details, "add-order": act_add_order, "add-delivery": act_add_delivery,
    "log-usage": act_log_usage, "range": act_range, "add-staff": act_add_staff,
    "staff-active": act_staff_active, "act-filter": act_act_filter,
    "act-delete": act_act_delete, "act-clear": act_act_clear, "about-back": act_about_back,
}


# ----- form submissions -----
async def submit_signin(form):
    ident = field_value(form, "identifier").strip()
    password = field_value(form, "password")
    ok = True
    kind, _value = parse_identifier(ident)
    if kind is None:
        set_error("email-error", "Enter a valid email or mobile number (like 09171234567).", "email")
        ok = False
    else:
        set_error("email-error", "", "email")
    if not password:
        set_error("password-error", "Enter your password.", "password")
        ok = False
    else:
        set_error("password-error", "", "password")
    set_error("form-error", "")
    if not ok:
        return

    btn = el("signin-btn")
    btn.disabled = True
    btn.textContent = "Signing in\u2026"
    data = await request("/api/login", {"identifier": ident, "password": password}, quiet=True)
    if data is None:
        set_error("form-error", state["err"])
        btn = el("signin-btn")
        if btn is not None:
            btn.disabled = False
            btn.textContent = "Sign in"
        return
    begin_session()
    toast("Welcome, {}!".format(first_name(state["me"]["name"])))


async def submit_signup(form):
    name = field_value(form, "name").strip()
    ident = field_value(form, "identifier").strip()
    password = field_value(form, "password")
    confirm = field_value(form, "confirm")
    f = state["form"]
    f["name"], f["ident"] = name, ident
    ok = True
    for eid, iid in (("su-name-error", "su-name"), ("su-ident-error", "su-ident"),
                     ("su-password-error", "su-password"), ("su-confirm-error", "su-confirm")):
        set_error(eid, "", iid)
    set_error("su-form-error", "")
    if len(name) < 2:
        set_error("su-name-error", "Enter your name.", "su-name")
        ok = False
    kind, _value = parse_identifier(ident)
    if kind != f["kind"]:
        msg = "Enter a valid email, like name@example.com." if f["kind"] == "email" else "Enter a valid mobile number, like 09171234567."
        set_error("su-ident-error", msg, "su-ident")
        ok = False
    if len(password) < MIN_PASSWORD:
        set_error("su-password-error", "Password needs at least {} characters.".format(MIN_PASSWORD), "su-password")
        ok = False
    if confirm != password:
        set_error("su-confirm-error", "The two passwords do not match.", "su-confirm")
        ok = False
    if not ok:
        return

    btn = el("signup-btn")
    btn.disabled = True
    btn.textContent = "Creating account\u2026"
    data = await request("/api/signup", {"name": name, "identifier": ident, "password": password}, quiet=True)
    if data is None:
        set_error("su-form-error", state["err"])
        btn = el("signup-btn")
        if btn is not None:
            btn.disabled = False
            btn.textContent = "Create account"
        return
    begin_session()
    toast("Account created. Welcome, {}!".format(first_name(state["me"]["name"])))


async def submit_forgot(form):
    ident = field_value(form, "identifier").strip()
    kind, _value = parse_identifier(ident)
    if kind is None:
        set_error("f-error", "Enter a valid email or mobile number.", "f-ident")
        return
    data = await request("/api/forgot/request", {"identifier": ident}, quiet=True)
    if data is None:
        set_error("f-error", state["err"])
        return
    state["reset"] = {"ident": ident, "demo": data.get("demo_code")}
    open_modal(sheet("Enter your code", reset_form_html()))


async def submit_reset(form):
    code = field_value(form, "code").strip()
    password = field_value(form, "password")
    confirm = field_value(form, "confirm")
    ok = True
    for eid, iid in (("r-code-error", "r-code"), ("r-pass-error", "r-pass"), ("r-confirm-error", "r-confirm")):
        set_error(eid, "", iid)
    set_error("r-error", "")
    if len(code) != 6 or not code.isdigit():
        set_error("r-code-error", "Enter the 6-digit code.", "r-code")
        ok = False
    if len(password) < MIN_PASSWORD:
        set_error("r-pass-error", "Password needs at least {} characters.".format(MIN_PASSWORD), "r-pass")
        ok = False
    if confirm != password:
        set_error("r-confirm-error", "The two passwords do not match.", "r-confirm")
        ok = False
    if not ok:
        return
    ident = state["reset"]["ident"]
    data = await request("/api/forgot/reset", {"identifier": ident, "code": code, "password": password}, quiet=True)
    if data is None:
        set_error("r-error", state["err"])
        return
    close_modal()
    state["reset"] = None
    box = el("email")
    if box is not None:
        box.value = ident
    toast("Password changed. Please sign in with your new password.")


async def submit_pass(form):
    old = field_value(form, "old")
    new = field_value(form, "new")
    confirm = field_value(form, "confirm")
    ok = True
    for eid, iid in (("p-old-error", "p-old"), ("p-new-error", "p-new"), ("p-confirm-error", "p-confirm")):
        set_error(eid, "", iid)
    set_error("p-error", "")
    if not old:
        set_error("p-old-error", "Enter your current password.", "p-old")
        ok = False
    if len(new) < MIN_PASSWORD:
        set_error("p-new-error", "Password needs at least {} characters.".format(MIN_PASSWORD), "p-new")
        ok = False
    if confirm != new:
        set_error("p-confirm-error", "The two passwords do not match.", "p-confirm")
        ok = False
    if not ok:
        return
    data = await request("/api/password/change", {"old": old, "new": new}, quiet=True)
    if data is None:
        set_error("p-error", state["err"])
        return
    close_modal()
    render()
    toast("Password changed.")


async def submit_card(form):
    name = field_value(form, "name").strip()
    if len(name) < 2:
        set_error("c-error", "Enter the customer's name.", "c-name")
        return
    data = await request("/api/cards", {"name": name}, quiet=True)
    if data is None:
        set_error("c-error", state["err"], "c-name")
        return
    state["selected_card"] = data["card_id"]
    state["loyalty_query"] = ""
    close_modal()
    toast("Card {} created for {}.".format(data["card_id"], name))
    if state["screen"] == "loyalty":
        render()


async def submit_order(form):
    name = field_value(form, "name").strip()
    ok = True
    try:
        pieces = int(field_value(form, "pieces"))
    except ValueError:
        pieces = 0
    pickup = field_value(form, "pickup")
    set_error("o-name-error", "", "o-name")
    set_error("o-pieces-error", "", "o-pieces")
    set_error("o-time-error", "", "o-time")
    if len(name) < 2:
        set_error("o-name-error", "Enter the customer's name.", "o-name")
        ok = False
    if pieces < 1:
        set_error("o-pieces-error", "Enter at least 1 piece.", "o-pieces")
        ok = False
    if not pickup:
        set_error("o-time-error", "Choose a pickup time.", "o-time")
        ok = False
    if not ok:
        return
    extra = bool(form.querySelector('[name="extra"]').checked)
    data = await request("/api/orders", {"name": name, "pieces": pieces, "pickup": pickup, "extra": extra, "pay": "cash"}, quiet=True)
    if data is None:
        set_error("o-name-error", state["err"], "o-name")
        return
    state["order_filter"] = "new"
    close_modal()
    render()
    toast("Pre-order saved for {}.".format(name))


async def submit_stock(form):
    mode = form.getAttribute("data-mode")
    try:
        amount = int(field_value(form, "amount"))
        index = int(field_value(form, "item"))
    except ValueError:
        amount, index = 0, 0
    if amount < 1:
        set_error("s-error", "Enter a number of 1 or more.")
        return
    data = await request("/api/stock", {"index": index, "amount": amount, "mode": mode}, quiet=True)
    if data is None:
        set_error("s-error", state["err"])
        return
    close_modal()
    render()
    toast(data.get("message", "Saved."))


async def submit_staff(form):
    name = field_value(form, "name").strip()
    ident = field_value(form, "identifier").strip()
    password = field_value(form, "password")
    ok = True
    for eid, iid in (("st-name-error", "st-name"), ("st-ident-error", "st-ident"), ("st-pass-error", "st-pass")):
        set_error(eid, "", iid)
    if len(name) < 2:
        set_error("st-name-error", "Enter the staff member's name.", "st-name")
        ok = False
    kind, _value = parse_identifier(ident)
    if kind is None:
        set_error("st-ident-error", "Enter a valid email or mobile number.", "st-ident")
        ok = False
    if len(password) < MIN_PASSWORD:
        set_error("st-pass-error", "Password needs at least {} characters.".format(MIN_PASSWORD), "st-pass")
        ok = False
    if not ok:
        return
    data = await request("/api/staff", {"name": name, "identifier": ident, "password": password}, quiet=True)
    if data is None:
        set_error("st-ident-error", state["err"], "st-ident")
        return
    close_modal()
    render()
    toast("Staff account created for {}.".format(name))


SUBMIT_HANDLERS = {
    "signin-form": submit_signin, "signup-form": submit_signup, "forgot-form": submit_forgot,
    "reset-form": submit_reset, "pass-form": submit_pass, "card-form": submit_card,
    "order-form": submit_order, "stock-form": submit_stock, "staff-form": submit_staff,
}


# ----- live updates (a timer event + a "tab visible again" event) -----
def can_redraw():
    """Do not redraw under the user's fingers while a form is open or a field has focus."""
    if modal_open():
        return False
    node = document.activeElement
    if node is None:
        return True
    tag = str(node.tagName)
    if tag in ("SELECT", "TEXTAREA"):
        return False
    if tag == "INPUT" and str(node.type) not in ("checkbox", "radio", "button", "submit"):
        return False
    return True


async def poll():
    if state["polling"] or state["busy"] or not state["token"] or state["screen"] in ("signin", "signup"):
        return
    state["polling"] = True
    try:
        status, data = await api("GET", "/api/state?v=" + str(window.encodeURIComponent(state["version"])))
        if status == 200:
            if not state["conn_ok"]:
                state["conn_ok"] = True
                toast("Back online.")
            if not data.get("unchanged"):
                apply_state(data)
                state["dirty"] = True
        elif status == 0 and state["conn_ok"]:
            state["conn_ok"] = False
            toast("Connection lost. Trying again...")
        if state["dirty"] and state["token"] and state["screen"] != "about" and can_redraw():
            state["dirty"] = False
            render()
    finally:
        state["polling"] = False


async def load_public():
    """Fetch what everyone may see before signing in (the promo video and the prices)."""
    status, data = await api("GET", "/api/public")
    if status == 200 and isinstance(data, dict):
        if data.get("config"):
            apply_config(data["config"])
        state["promo"] = data.get("promo")
        if state["screen"] == "about" and can_redraw():
            render()


async def restore():
    """If this browser already has a saved sign in, go straight to the right screen."""
    status, data = await api("GET", "/api/state")
    if status == 200 and data.get("me"):
        apply_state(data)
        begin_session()
    else:
        store_token(None)
        state["token"] = None
        state["screen"] = "signin"
        render()


# ----- DOM event listeners (called by the browser) -----
def run(result):
    if asyncio.iscoroutine(result):
        asyncio.ensure_future(result)


def on_click(event):
    node = event.target.closest("[data-action]")
    if node is None or node.disabled:
        return
    handler = CLICK_ACTIONS.get(node.getAttribute("data-action"))
    if handler is not None:
        run(handler(node.getAttribute("data-arg")))


def on_submit(event):
    event.preventDefault()                      # stop the browser reloading the page
    handler = SUBMIT_HANDLERS.get(event.target.id)
    if handler is not None:
        run(handler(event.target))


def on_input(event):
    # Typing in the loyalty search box filters cards live.
    if event.target.id == "loyalty-search":
        state["loyalty_query"] = event.target.value
        render_loyalty_area()


def on_change(event):
    kind = event.target.getAttribute("data-change")
    if kind == "extra":                          # staff sale: Extra cheese switch
        state["sale"]["extra"] = bool(event.target.checked)
        render()
    elif kind == "cextra":                       # customer order: Extra cheese switch
        state["cust"]["extra"] = bool(event.target.checked)
        render()
    elif kind == "cpickup":                      # customer order: pickup time
        state["cust"]["pickup"] = event.target.value
        set_error("cp-error", "", "c-pickup")


def on_keydown(event):
    # Escape closes any open form sheet.
    # Browser autofill can fire a keydown event that has no "key" at all, so read it safely.
    if getattr(event, "key", None) == "Escape":
        close_modal()


def on_visible(event):
    # Coming back to the tab: refresh right away instead of waiting for the timer.
    if document.visibilityState == "visible":
        asyncio.ensure_future(poll())


def on_tick():
    asyncio.ensure_future(poll())


# ---------------------------------------------------------------------------
# 6. EVENT WIRING + FIRST DRAW
# ---------------------------------------------------------------------------
phone = el("phone")
phone.addEventListener("click", create_proxy(on_click))
phone.addEventListener("submit", create_proxy(on_submit))
phone.addEventListener("input", create_proxy(on_input))
phone.addEventListener("change", create_proxy(on_change))
document.addEventListener("keydown", create_proxy(on_keydown))
document.addEventListener("visibilitychange", create_proxy(on_visible))
window.setInterval(create_proxy(on_tick), POLL_MS)

asyncio.ensure_future(load_public())
_saved = load_token()
if _saved:
    state["token"] = _saved
    asyncio.ensure_future(restore())
else:
    render()
