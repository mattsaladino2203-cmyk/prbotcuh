import asyncio
import base64
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import os

if os.environ.get("STREAMLIT_SHARING_MODE", ""):
    os.system("python -m playwright install chromium")

    import os

os.system("python -m playwright install-deps")

import pandas as pd
import streamlit as st
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Page, sync_playwright

# ------------------ PAGE CONFIG ------------------ #
st.set_page_config(page_title="Cuh AI Form Engine SaaS", page_icon="⚡", layout="wide")

PRIMARY = "#D4AF37"
REQUIRED_PROFILE_FIELDS = ("first_name", "last_name", "email", "phone", "address", "city", "state", "zip")
PROFILE_STORE_PATH = Path("saved_autofill_profile.json")

# Playwright needs subprocess support on Windows.
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ------------------ PERF: single combined CAPTCHA selector ------------------ #
# FIX 4: collapsed from 7 sequential query_selector round-trips to one.
_CAPTCHA_SELECTOR = ", ".join([
    "iframe[src*='recaptcha']",
    "iframe[title*='captcha' i]",
    ".g-recaptcha",
    "#captcha",
    "[id*='captcha']",
    "[class*='captcha']",
    "[data-sitekey]",
])

# ------------------ PERF: package lookup dict ------------------ #
# FIX 6: O(1) dict lookup instead of linear next() scan every render.
_PACKAGE_OPTIONS = [
    {"id": "p100",  "prs": "100 PR's",  "price": "$10"},
    {"id": "p300",  "prs": "300 PR's",  "price": "$25"},
    {"id": "p500",  "prs": "500 PR's",  "price": "$45"},
    {"id": "p750",  "prs": "750 PR's",  "price": "$65"},
    {"id": "p1000", "prs": "1000 PR's", "price": "$90"},
]
_PACKAGE_MAP = {p["id"]: p for p in _PACKAGE_OPTIONS}


# ------------------ BACKGROUND ------------------ #
# FIX 5: cache the base64 encoding so disk I/O + encoding runs only once,
# not on every Streamlit script re-run.
@st.cache_data
def _encode_bg(image_file: str) -> str | None:
    path = Path(image_file)
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode()


def set_bg(image_file: str, overlay_opacity: float = 0.85) -> None:
    encoded = _encode_bg(image_file)
    if encoded is None:
        st.warning(f"Background image not found: {image_file}")
        return

    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: url("data:image/png;base64,{encoded}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            color: #F5F7FA;
        }}

        .stApp::before {{
            content: "";
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,{overlay_opacity});
            z-index: -1;
        }}

        section[data-testid="stSidebar"] {{
            background: rgba(14, 17, 23, 0.96);
            border-right: 1px solid rgba(212, 175, 55, 0.35);
        }}

        .main .block-container {{
            max-width: 1200px;
            padding-top: 1.4rem;
        }}

        h1, h2, h3 {{
            color: #F8FAFC;
            letter-spacing: 0.2px;
        }}

        .stTextInput label, .stFileUploader label, .stRadio label {{
            color: #E5E7EB !important;
            font-weight: 600;
        }}

        .stTextInput input {{
            background: rgba(15, 23, 42, 0.75);
            border: 1px solid rgba(148, 163, 184, 0.35);
            border-radius: 10px;
            color: #F8FAFC;
        }}

        .stButton > button {{
            background: linear-gradient(45deg, {PRIMARY}, #F5D27A);
            color: black;
            font-weight: 700;
            border-radius: 12px;
            border: 0;
            padding: 0.55rem 1rem;
            box-shadow: 0 6px 16px rgba(212, 175, 55, 0.25);
        }}

        .stButton > button:hover {{
            filter: brightness(1.03);
            transform: translateY(-1px);
        }}

        [data-testid="stMetric"] {{
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid rgba(148, 163, 184, 0.22);
            border-radius: 12px;
            padding: 0.5rem 0.7rem;
            min-height: 108px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}

        [data-testid="stMetric"] * {{
            color: #FFFFFF !important;
        }}

        [data-testid="stMetricValue"] {{
            font-size: 1.6rem !important;
            line-height: 1.2 !important;
        }}

        [data-testid="stDataFrame"] {{
            background: rgba(15, 23, 42, 0.5);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 12px;
        }}

        .kpi-glass-card {{
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid rgba(148, 163, 184, 0.22);
            border-radius: 12px;
            padding: 0.6rem 0.8rem;
            min-height: 108px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}

        div[data-testid="stMarkdownContainer"] p,
        div[data-testid="stMarkdownContainer"] li,
        div[data-testid="stCaptionContainer"],
        div[data-testid="stText"] {{
            color: #FFFFFF !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def clear_bg() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background-image: none !important;
            background-color: #0F172A !important;
        }
        .stApp::before {
            content: none !important;
            background: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


set_bg("background.png", overlay_opacity=0.85)


# ------------------ SESSION STATE ------------------ #
def load_saved_profile() -> dict[str, str]:
    if not PROFILE_STORE_PATH.exists():
        return {}
    try:
        raw = json.loads(PROFILE_STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def save_profile(profile: dict[str, str]) -> None:
    try:
        PROFILE_STORE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    except OSError:
        pass


if "profile" not in st.session_state:
    st.session_state.profile = load_saved_profile()

if "jobs" not in st.session_state:
    st.session_state.jobs = []

if "profile_last_saved" not in st.session_state:
    st.session_state.profile_last_saved = None

if "generated_usernames" not in st.session_state:
    st.session_state.generated_usernames = []

if "activity_log" not in st.session_state:
    st.session_state.activity_log = []

if "account" not in st.session_state:
    st.session_state.account = {
        "username": "",
        "email": "",
        "password": "",
        "created": False,
        "logged_in": False,
        "created_at": "",
    }
else:
    st.session_state.account.setdefault("logged_in", False)


# ------------------ FORM ENGINE FUNCTIONS ------------------ #
def detect_field(field: dict[str, Any]) -> str | None:
    text = " ".join(
        [
            field.get("name", ""),
            field.get("id", ""),
            field.get("placeholder", ""),
            field.get("aria", ""),
        ]
    ).lower()

    mapping = {
        "username":   ["username", "user name", "user_id", "userid", "login", "handle", "screen name"],
        "full_name":  ["full name", "fullname", "your name", "applicant name", "name"],
        "first_name": ["first", "given", "fname", "forename"],
        "last_name":  ["last", "surname", "family", "lname"],
        "email":      ["email", "e-mail", "mail"],
        "phone":      ["phone", "mobile", "tel", "telephone", "cell", "contact number"],
        "address":    ["address", "street", "address line", "addr", "line1", "line 1"],
        "city":       ["city", "town", "municipality"],
        "state":      ["state", "province", "region", "county"],
        "zip":        ["zip", "postal", "postcode", "zip code", "postal code"],
        "dob":        ["birth", "dob", "date of birth"],
    }

    for key, keywords in mapping.items():
        if any(keyword in text for keyword in keywords):
            if key == "full_name" and any(x in text for x in ("first", "last", "surname", "family", "given")):
                continue
            return key

    return None


def generate_unique_username(profile: dict[str, str], used_usernames: set[str]) -> str:
    first    = re.sub(r"[^a-z0-9]", "", profile.get("first_name", "").lower())
    last     = re.sub(r"[^a-z0-9]", "", profile.get("last_name",  "").lower())
    city     = re.sub(r"[^a-z0-9]", "", profile.get("city",       "").lower())
    zip_code = re.sub(r"[^0-9]",    "", profile.get("zip",        ""))

    base = f"{first}{last}" or f"{first}{city}" or "user"
    base = (base + city)[:16]
    zip_tail       = zip_code[-2:] if zip_code else "00"
    timestamp_tail = str(int(time.time() * 1000))[-5:]

    candidate = f"{base}{zip_tail}{timestamp_tail}"[:30]

    counter = 1
    while candidate in used_usernames:
        suffix    = f"{counter:02d}"
        candidate = f"{base}{zip_tail}{timestamp_tail}{suffix}"[:30]
        counter  += 1

    used_usernames.add(candidate)
    return candidate


def get_profile_value(profile: dict[str, str], key: str | None) -> str:
    if not key:
        return ""
    if key == "full_name":
        first = profile.get("first_name", "").strip()
        last  = profile.get("last_name",  "").strip()
        full  = " ".join(part for part in [first, last] if part).strip()
        if full:
            return full
    if key == "username":
        return profile.get("username", "").strip()
    return profile.get(key, "").strip()


def build_plan(schema: list[dict[str, Any]], profile: dict[str, str]) -> list[tuple[Any, str]]:
    plan: list[tuple[Any, str]] = []
    for field in schema:
        key   = detect_field(field)
        value = get_profile_value(profile, key)
        if value:
            plan.append((field["el"], value))
    return plan


def scan_fields(playwright_page: Page) -> list[dict[str, Any]]:
    fields = playwright_page.query_selector_all("input, textarea, select")
    schema: list[dict[str, Any]] = []
    for field in fields:
        schema.append(
            {
                "el":          field,
                "name":        field.get_attribute("name")       or "",
                "id":          field.get_attribute("id")         or "",
                "placeholder": field.get_attribute("placeholder") or "",
                "aria":        field.get_attribute("aria-label") or "",
            }
        )
    return schema


def check_common_required_boxes(playwright_page: Page) -> None:
    checkbox_selectors = [
        "input[type='checkbox'][required]",
        "input[type='checkbox'][name*='terms' i]",
        "input[type='checkbox'][id*='terms' i]",
        "input[type='checkbox'][name*='policy' i]",
        "input[type='checkbox'][id*='policy' i]",
        "input[type='checkbox'][name*='agree' i]",
        "input[type='checkbox'][id*='agree' i]",
        "input[type='checkbox'][name*='consent' i]",
        "input[type='checkbox'][id*='consent' i]",
    ]
    seen: set[Any] = set()
    for selector in checkbox_selectors:
        for checkbox in playwright_page.query_selector_all(selector):
            if checkbox in seen:
                continue
            seen.add(checkbox)
            try:
                checkbox.check(timeout=1000)
            except PlaywrightError:
                continue


def click_submit_button(playwright_page: Page) -> bool:
    buttons = playwright_page.query_selector_all("button, input[type='submit']")
    intent_terms = (
        "submit", "send", "next", "continue", "apply",
        "sign up", "signup", "register", "create account",
        "create my account", "join", "finish", "complete",
    )

    for button in buttons:
        try:
            text     = (button.inner_text()            or "").lower()
            value    = (button.get_attribute("value")  or "").lower()
            combined = f"{text} {value}"
            if any(word in combined for word in intent_terms):
                button.click(timeout=5000)
                return True
        except PlaywrightError:
            continue

    for button in buttons:
        try:
            if button.is_visible() and button.is_enabled():
                button.click(timeout=5000)
                return True
        except PlaywrightError:
            continue

    return False


# FIX 4: single CSS selector round-trip instead of 7 sequential calls.
def captcha_present(playwright_page: Page) -> bool:
    try:
        return playwright_page.query_selector(_CAPTCHA_SELECTOR) is not None
    except PlaywrightError:
        return False


def submit_with_captcha_flow(playwright_page: Page) -> tuple[str, str]:
    check_common_required_boxes(playwright_page)
    if not click_submit_button(playwright_page):
        return "no_submit", "No submit button found."
    time.sleep(1.0)
    if captcha_present(playwright_page):
        return "captcha", "CAPTCHA detected during submit."
    return "submitted", "Submit button clicked."


def wait_for_manual_captcha(
    playwright_page: Page,
    max_wait_seconds: int = 180,
    poll_seconds: float = 2.0,
) -> bool:
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        if not captcha_present(playwright_page):
            return True
        time.sleep(poll_seconds)
    return False


def validate_profile(profile: dict[str, str]) -> list[str]:
    return [key for key in REQUIRED_PROFILE_FIELDS if not profile.get(key, "").strip()]


def normalize_urls(series: pd.Series) -> list[str]:
    urls = []
    seen: set[str] = set()
    for raw in series.dropna():
        url = str(raw).strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def extract_urls_from_csv(uploaded_file: Any) -> tuple[list[str], str | None]:
    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        return [], f"Could not read CSV: {exc}"

    if df.empty and len(df.columns) == 0:
        return [], "CSV is empty."

    df.columns = df.columns.astype(str).str.strip().str.lower()

    if "url" in df.columns:
        return normalize_urls(df["url"]), None

    if len(df.columns) == 1:
        only_col = df.columns[0]
        header_looks_like_url = str(only_col).strip().startswith(("http://", "https://"))
        url_values = [str(v).strip() for v in df.iloc[:, 0].dropna().tolist()]
        if header_looks_like_url:
            url_values.insert(0, str(only_col).strip())
        return normalize_urls(pd.Series(url_values)), None

    return [], f"Missing 'url' column. Found: {df.columns.tolist()}"


def render_live_tracker(
    jobs: list[dict[str, str]],
    tracker_placeholder: Any,
    stats_placeholder: Any,
    current_url: str,
    current_index: int,
    total_jobs: int,
) -> None:
    tracker_df = pd.DataFrame(jobs)
    if tracker_df.empty:
        tracker_df = pd.DataFrame(columns=["url", "status"])

    stats = tracker_df["status"].value_counts().to_dict() if "status" in tracker_df.columns else {}
    queued_count     = stats.get("queued", 0)
    processing_count = stats.get("processing", 0)
    completed_count  = stats.get("completed", 0)
    failed_count     = (
        stats.get("failed", 0)
        + stats.get("timeout", 0)
        + stats.get("error", 0)
        + stats.get("captcha_timeout", 0)
    )
    captcha_count = stats.get("captcha_required", 0)

    stats_placeholder.markdown(
        (
            f"**Current:** {current_index}/{total_jobs}  \n"
            f"**URL:** `{current_url}`  \n"
            f"**Completed:** {completed_count} | "
            f"**Processing:** {processing_count} | "
            f"**Queued:** {queued_count} | "
            f"**CAPTCHA:** {captcha_count} | "
            f"**Issues:** {failed_count}"
        )
    )
    tracker_placeholder.dataframe(tracker_df, use_container_width=True)


def render_top_header() -> None:
    st.markdown(
        """
        <div style="
            margin: 0 0 1rem 0;
            padding: 0.9rem 1rem;
            border-radius: 14px;
            border: 1px solid rgba(148, 163, 184, 0.25);
            background: linear-gradient(135deg, rgba(15,23,42,0.92), rgba(30,41,59,0.82));
            display: flex;
            align-items: center;
            justify-content: space-between;">
            <div>
                <div style="font-size: 0.78rem; color: #94A3B8; letter-spacing: 0.08em;">CUH AI FORM ENGINE</div>
                <div style="font-size: 1.15rem; font-weight: 700; color: #F8FAFC;">Cuh Consulting Premium PR Bot</div>
            </div>
            <div style="font-size: 0.82rem; color: #CBD5E1;">Smart Fill • Live Tracking • CAPTCHA Assist</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def add_activity(message: str, kind: str = "info") -> None:
    icon_map = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
    stamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.activity_log.insert(0, f"{stamp} {icon_map.get(kind, 'ℹ️')} {message}")
    # FIX 7: cap once here; render_activity_feed slices to its own limit.
    st.session_state.activity_log = st.session_state.activity_log[:80]


def render_activity_feed(container: Any, limit: int = 12) -> None:
    # FIX 7: slice first, then build the string — avoid iterating 80 items to show 12.
    entries = st.session_state.activity_log[:limit]
    if not entries:
        container.caption("No activity yet. Start processing to see live events.")
        return
    container.markdown("\n".join(f"- {entry}" for entry in entries))


# ------------------ SIDEBAR ------------------ #
st.sidebar.title("⚙️ Dashboard")
st.sidebar.caption("Automate onboarding flows with live tracking.")
if st.session_state.account.get("logged_in"):
    current_view = st.sidebar.radio("Navigate", ["Login / Sign Up", "Packages", "Profile", "Jobs", "Run Engine"])
    if st.sidebar.button("Log Out"):
        st.session_state.account["logged_in"] = False
        st.rerun()
else:
    st.sidebar.info("Log in or sign up to unlock the rest of the dashboard.")
    current_view = "Login / Sign Up"


# ------------------ LOGIN / SIGN UP PAGE ------------------ #
if current_view == "Login / Sign Up":
    clear_bg()
    st.title("🔐 Login / Sign Up")
    st.caption("Sign up to create your account, or log in to access your dashboard.")

    account   = st.session_state.account
    auth_mode = st.radio("Choose action", ["Log In", "Sign Up"], horizontal=True)

    if auth_mode == "Sign Up":
        username         = st.text_input("Username",         value=account.get("username", ""), key="signup_username")
        email            = st.text_input("Email",            value=account.get("email",    ""), key="signup_email")
        password         = st.text_input("Password",         type="password",                   key="signup_password")
        confirm_password = st.text_input("Confirm Password", type="password",                   key="signup_confirm_password")

        account_ready = bool(username.strip() and email.strip() and password.strip() and confirm_password.strip())
        if st.button("Create Account", disabled=not account_ready):
            if password != confirm_password:
                st.error("Passwords do not match.")
            else:
                st.session_state.account.update(
                    {
                        "username":    username.strip(),
                        "email":       email.strip(),
                        "password":    password,
                        "created":     True,
                        "logged_in":   True,
                        "created_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                st.success("Account created and logged in.")
                st.rerun()
    else:
        login_identity = st.text_input("Username or Email", key="login_identity")
        login_password = st.text_input("Password", type="password", key="login_password")
        can_login = bool(login_identity.strip() and login_password.strip())

        if st.button("Log In", disabled=not can_login):
            account_username = account.get("username", "").strip().lower()
            account_email    = account.get("email",    "").strip().lower()
            identity         = login_identity.strip().lower()
            matches_identity = identity in {account_username, account_email}
            matches_password = login_password == account.get("password", "")
            if account.get("created") and matches_identity and matches_password:
                st.session_state.account["logged_in"] = True
                st.success("Logged in successfully.")
                st.rerun()
            else:
                st.error("Invalid credentials. Please try again or sign up first.")

    if st.session_state.account.get("created"):
        st.caption(f"Account created at {st.session_state.account.get('created_at')}")


# ------------------ PACKAGES PAGE ------------------ #
elif current_view == "Packages":
    st.title("📦 Packages")
    st.caption("Choose a PR volume plan that fits your campaign size.")

    if "selected_package" not in st.session_state:
        st.session_state.selected_package = _PACKAGE_OPTIONS[0]["id"]

    st.markdown("### Available Plans")
    for row_start in range(0, len(_PACKAGE_OPTIONS), 3):
        cols = st.columns(3)
        row  = _PACKAGE_OPTIONS[row_start : row_start + 3]
        for idx, plan in enumerate(row):
            with cols[idx]:
                is_selected  = st.session_state.selected_package == plan["id"]
                border_color = "#22C55E" if is_selected else "rgba(148, 163, 184, 0.22)"
                glow = (
                    "0 0 0 1px rgba(34,197,94,0.25), 0 10px 24px rgba(2,6,23,0.25)"
                    if is_selected else
                    "0 8px 18px rgba(2,6,23,0.2)"
                )
                st.markdown(
                    f"""
                    <div style="
                        background: rgba(15, 23, 42, 0.72);
                        border: 1px solid {border_color};
                        border-radius: 14px;
                        padding: 0.9rem 1rem;
                        min-height: 135px;
                        box-shadow: {glow};">
                        <div style="font-size: 0.9rem; color: #CBD5E1;">{plan['prs']}</div>
                        <div style="font-size: 1.8rem; font-weight: 800; color: #F8FAFC; margin-top: 0.35rem;">{plan['price']}</div>
                        <div style="font-size: 0.78rem; color: #94A3B8; margin-top: 0.3rem;">One-time package</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("Select", key=f"select_{plan['id']}", use_container_width=True):
                    st.session_state.selected_package = plan["id"]

    # FIX 6: O(1) dict lookup.
    selected = _PACKAGE_MAP.get(st.session_state.selected_package, _PACKAGE_OPTIONS[0])
    st.success(f"Selected package: {selected['prs']} for {selected['price']}")


# ------------------ PROFILE PAGE ------------------ #
elif current_view == "Profile":
    st.title("👤 Profile Setup")
    st.caption("Add your identity and contact details once. Saved autofill data is reused across all forms.")

    fields = [
        ("first_name", "First Name"),
        ("last_name",  "Last Name"),
        ("email",      "Email"),
        ("phone",      "Phone"),
        ("address",    "Address"),
        ("city",       "City"),
        ("state",      "State"),
        ("zip",        "ZIP Code"),
        ("dob",        "DOB"),
    ]

    profile_inputs: dict[str, str] = {}
    for key, label in fields:
        default              = st.session_state.profile.get(key, "")
        profile_inputs[key]  = st.text_input(label, value=default, key=f"profile_input_{key}")

    has_unsaved_changes = any(
        profile_inputs[key].strip() != st.session_state.profile.get(key, "").strip()
        for key, _ in fields
    )
    if has_unsaved_changes:
        st.warning("You have unsaved profile changes.")
    else:
        st.caption("All profile changes are saved.")

    if st.button("💾 Save Profile", disabled=not has_unsaved_changes):
        st.session_state.profile.update({k: v.strip() for k, v in profile_inputs.items()})
        save_profile(st.session_state.profile)
        st.session_state.profile_last_saved = datetime.now().strftime("%H:%M:%S")
        st.success("Profile saved and autofill updated")

    if st.session_state.profile_last_saved:
        st.caption(f"Last saved at {st.session_state.profile_last_saved}")


# ------------------ JOBS PAGE ------------------ #
elif current_view == "Jobs":
    st.title("📂 Job Queue")
    st.caption("Upload a CSV of signup URLs, preview them, then push them into your processing queue.")

    uploaded_file = st.file_uploader("CSV must contain 'url' column", type=["csv"])

    if uploaded_file:
        urls, error_message = extract_urls_from_csv(uploaded_file)
        if error_message:
            st.error(error_message)
        else:
            preview_df = pd.DataFrame({"url": urls})
            st.dataframe(preview_df, use_container_width=True)

            if st.button("➕ Add to Queue"):
                for url in urls:
                    st.session_state.jobs.append({"url": url, "status": "queued"})
                st.success(f"Added {len(urls)} jobs")

    st.subheader("Queue")
    st.dataframe(pd.DataFrame(st.session_state.jobs))


# ------------------ RUN ENGINE PAGE ------------------ #
elif current_view == "Run Engine":
    st.title("🚀 Automation Engine")
    st.caption("Run smart form fill + auto-submit. CAPTCHA pages pause for manual solve, then auto-resume.")

    status_counts    = pd.DataFrame(st.session_state.jobs).get("status", pd.Series(dtype="str")).value_counts().to_dict()
    successful_fills = status_counts.get("completed", 0)
    unsuccessful_count = (
        status_counts.get("failed", 0)
        + status_counts.get("timeout", 0)
        + status_counts.get("error", 0)
        + status_counts.get("captcha_timeout", 0)
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Queued Jobs", len(st.session_state.jobs))
    m2.markdown(
        f"""
        <div class="kpi-glass-card">
            <div style="font-size: 0.84rem; color: #FFFFFF;">Successful Form Fills</div>
            <div style="font-size: 1.75rem; line-height: 1.2; font-weight: 700; color: #22C55E;">{successful_fills}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    m3.markdown(
        f"""
        <div class="kpi-glass-card">
            <div style="font-size: 0.84rem; color: #FFFFFF;">Errors or Unsuccessful</div>
            <div style="font-size: 1.75rem; line-height: 1.2; font-weight: 700; color: #EF4444;">{unsuccessful_count}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tracker_stats_placeholder = st.empty()
    st.markdown("### Live Monitor")
    tracker_col, activity_col = st.columns([2, 1])
    with tracker_col:
        tracker_table_placeholder = st.empty()
    with activity_col:
        st.markdown("#### Activity Feed")
        activity_actions_col1, activity_actions_col2 = st.columns(2)
        with activity_actions_col1:
            if st.button("Clear Activity"):
                st.session_state.activity_log = []
        with activity_actions_col2:
            activity_export_df = pd.DataFrame({"event": list(reversed(st.session_state.activity_log))})
            st.download_button(
                "Export CSV",
                data=activity_export_df.to_csv(index=False),
                file_name="activity_log.csv",
                mime="text/csv",
            )
        activity_feed_placeholder = st.empty()
        render_activity_feed(activity_feed_placeholder)

    render_live_tracker(
        st.session_state.jobs,
        tracker_table_placeholder,
        tracker_stats_placeholder,
        current_url="-",
        current_index=0,
        total_jobs=max(len(st.session_state.jobs), 1),
    )

    if st.button("Start Processing"):
        add_activity("Processing started.", "info")
        profile       = st.session_state.profile
        missing_fields = validate_profile(profile)

        if missing_fields:
            add_activity(f"Blocked: missing profile fields ({', '.join(missing_fields)}).", "warning")
            st.error(f"Please complete your profile first. Missing: {', '.join(missing_fields)}")
            st.stop()

        if not st.session_state.jobs:
            add_activity("Blocked: no jobs in queue.", "warning")
            st.warning("No jobs in queue.")
            st.stop()

        for job in st.session_state.jobs:
            if job.get("status") != "completed":
                job["status"] = "queued"

        progress                 = st.progress(0.0, text="Starting...")
        status_placeholder       = st.empty()
        filled_fields_placeholder = st.empty()

        total_jobs = len(st.session_state.jobs)
        render_live_tracker(
            st.session_state.jobs,
            tracker_table_placeholder,
            tracker_stats_placeholder,
            current_url="-",
            current_index=0,
            total_jobs=total_jobs,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(
    headless=True,
    args=[
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--single-process"
    ]
)

            try:
                # FIX 2: build the used-usernames set ONCE outside the loop.
                used_usernames: set[str] = set(st.session_state.generated_usernames)

                for i, job in enumerate(st.session_state.jobs, start=1):
                    if job.get("status") == "completed":
                        progress.progress(i / total_jobs, text=f"Skipping completed job {i}/{total_jobs}")
                        # FIX 1: single tracker render at the bottom of each iteration.
                        render_live_tracker(
                            st.session_state.jobs,
                            tracker_table_placeholder,
                            tracker_stats_placeholder,
                            current_url=job.get("url", ""),
                            current_index=i,
                            total_jobs=total_jobs,
                        )
                        continue

                    url = job.get("url", "").strip()
                    job["status"] = "processing"

                    # FIX 2: pass existing set; generate_unique_username mutates it in-place.
                    generated_username   = generate_unique_username(profile, used_usernames)
                    profile_for_job      = dict(profile)
                    profile_for_job["username"] = generated_username

                    status_placeholder.write(
                        f"Processing {i}/{total_jobs} -> {url} | Generated username: `{generated_username}`"
                    )
                    add_activity(f"Started job {i}/{total_jobs}: {url}", "info")
                    render_activity_feed(activity_feed_placeholder)

                    playwright_page = browser.new_page()

                    try:
                        playwright_page.goto(url, timeout=60000, wait_until="domcontentloaded")

                        schema = scan_fields(playwright_page)
                        plan   = build_plan(schema, profile_for_job)
                        filled_fields_placeholder.write(
                            f"Matched {len(plan)} fields from saved profile for `{url}`"
                        )
                        add_activity(f"Matched {len(plan)} fields on {url}", "info")
                        render_activity_feed(activity_feed_placeholder)

                        # FIX 3: removed time.sleep(0.05) per field — Playwright's own
                        # timeout handles pacing; the sleep added ~0.4 s per form with
                        # no functional benefit.
                        for element, value in plan:
                            try:
                                element.fill(str(value), timeout=3000)
                            except PlaywrightError:
                                continue

                        submit_result  = "failed"
                        submit_message = "Submission did not complete."
                        max_submit_attempts = 3

                        if captcha_present(playwright_page):
                            job["status"] = "captcha_required"
                            st.warning(
                                f"CAPTCHA detected on {url}. Please solve it manually in the opened browser."
                            )
                            add_activity(f"CAPTCHA detected on {url}", "warning")
                            render_activity_feed(activity_feed_placeholder)
                            solved = wait_for_manual_captcha(playwright_page, max_wait_seconds=240)
                            if not solved:
                                job["status"] = "captcha_timeout"
                                st.error(f"CAPTCHA not completed in time: {url}")
                                add_activity(f"CAPTCHA timeout on {url}", "error")
                                render_activity_feed(activity_feed_placeholder)
                                # FIX 1: single render before continuing.
                                render_live_tracker(
                                    st.session_state.jobs,
                                    tracker_table_placeholder,
                                    tracker_stats_placeholder,
                                    current_url=url,
                                    current_index=i,
                                    total_jobs=total_jobs,
                                )
                                continue
                            st.success(f"CAPTCHA completed. Auto-submitting for {url}")
                            add_activity(f"CAPTCHA solved for {url}. Resuming submit.", "success")
                            render_activity_feed(activity_feed_placeholder)

                        for _ in range(max_submit_attempts):
                            submit_result, submit_message = submit_with_captcha_flow(playwright_page)
                            if submit_result in ("submitted", "no_submit"):
                                break
                            if submit_result == "captcha":
                                job["status"] = "captcha_required"
                                st.warning(
                                    f"CAPTCHA detected on {url}. Please solve it manually in the opened browser."
                                )
                                add_activity(f"CAPTCHA detected during submit on {url}", "warning")
                                render_activity_feed(activity_feed_placeholder)
                                solved = wait_for_manual_captcha(playwright_page, max_wait_seconds=240)
                                if not solved:
                                    submit_result  = "captcha_timeout"
                                    submit_message = "CAPTCHA not completed in time."
                                    break
                                job["status"] = "processing"
                                st.success(f"CAPTCHA completed. Auto-submitting for {url}")
                                add_activity(f"CAPTCHA solved during submit for {url}", "success")
                                render_activity_feed(activity_feed_placeholder)
                                continue

                        if submit_result == "submitted":
                            job["status"] = "completed"
                            st.success(f"Submitted: {url}")
                            add_activity(f"Submitted successfully: {url}", "success")
                        elif submit_result == "captcha_timeout":
                            job["status"] = "captcha_timeout"
                            st.error(f"CAPTCHA not completed in time: {url}")
                            add_activity(f"Submission blocked by CAPTCHA timeout: {url}", "error")
                        else:
                            job["status"] = "failed"
                            st.warning(f"{submit_message} {url}")
                            add_activity(f"Submit failed: {url} ({submit_message})", "warning")

                        render_activity_feed(activity_feed_placeholder)

                    except PlaywrightTimeoutError:
                        job["status"] = "timeout"
                        st.error(f"Timeout loading: {url}")
                        add_activity(f"Timeout loading {url}", "error")
                        render_activity_feed(activity_feed_placeholder)
                    except PlaywrightError as exc:
                        job["status"] = "error"
                        st.error(f"Playwright error on {url} -> {exc}")
                        add_activity(f"Playwright error on {url}", "error")
                        render_activity_feed(activity_feed_placeholder)
                    except Exception as exc:
                        job["status"] = "error"
                        st.error(f"Unexpected error on {url} -> {exc}")
                        add_activity(f"Unexpected error on {url}", "error")
                        render_activity_feed(activity_feed_placeholder)
                    finally:
                        playwright_page.close()

                    progress.progress(i / total_jobs, text=f"Completed {i}/{total_jobs}")
                    # FIX 1: single render_live_tracker call per iteration, here at the bottom.
                    render_live_tracker(
                        st.session_state.jobs,
                        tracker_table_placeholder,
                        tracker_stats_placeholder,
                        current_url=url,
                        current_index=i,
                        total_jobs=total_jobs,
                    )
                    time.sleep(0.5)  # between-job stealth delay — intentional, kept as-is

                # FIX 2: write back the completed set once after the loop.
                st.session_state.generated_usernames = list(used_usernames)

            finally:
                browser.close()

        st.success("All jobs processed 🚀")
        add_activity("All jobs processed.", "success")
        render_activity_feed(activity_feed_placeholder)