import asyncio
import base64
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    Page,
    sync_playwright,
)

# ------------------ PAGE CONFIG ------------------ #
st.set_page_config(
    page_title="Cuh AI Form Engine SaaS",
    page_icon="⚡",
    layout="wide"
)

PRIMARY = "#D4AF37"
REQUIRED_PROFILE_FIELDS = (
    "first_name", "last_name", "email", "phone",
    "address", "city", "state", "zip"
)

PROFILE_STORE_PATH = Path("saved_autofill_profile.json")

# Windows compatibility
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


# ------------------ CAPTCHA SELECTOR ------------------ #
_CAPTCHA_SELECTOR = ", ".join([
    "iframe[src*='recaptcha']",
    "iframe[title*='captcha' i]",
    ".g-recaptcha",
    "#captcha",
    "[id*='captcha']",
    "[class*='captcha']",
    "[data-sitekey]",
])


# ------------------ PACKAGES ------------------ #
_PACKAGE_OPTIONS = [
    {"id": "p100", "prs": "100 PR's", "price": "$10"},
    {"id": "p300", "prs": "300 PR's", "price": "$25"},
    {"id": "p500", "prs": "500 PR's", "price": "$45"},
    {"id": "p750", "prs": "750 PR's", "price": "$65"},
    {"id": "p1000", "prs": "1000 PR's", "price": "$90"},
]
_PACKAGE_MAP = {p["id"]: p for p in _PACKAGE_OPTIONS}


# ------------------ BACKGROUND ------------------ #
@st.cache_data
def _encode_bg(image_file: str) -> str | None:
    path = Path(image_file)
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode()


def set_bg(image_file: str, overlay_opacity: float = 0.85) -> None:
    encoded = _encode_bg(image_file)
    if not encoded:
        return

    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: url("data:image/png;base64,{encoded}");
            background-size: cover;
            background-position: center;
        }}
        .stApp::before {{
            content: "";
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,{overlay_opacity});
            z-index: -1;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


set_bg("background.png")


# ------------------ PROFILE ------------------ #
def load_saved_profile():
    if PROFILE_STORE_PATH.exists():
        return json.loads(PROFILE_STORE_PATH.read_text())
    return {}


def save_profile(profile):
    PROFILE_STORE_PATH.write_text(json.dumps(profile, indent=2))


if "profile" not in st.session_state:
    st.session_state.profile = load_saved_profile()

if "jobs" not in st.session_state:
    st.session_state.jobs = []


# ------------------ CAPTCHA ------------------ #
def captcha_present(page: Page) -> bool:
    try:
        return page.query_selector(_CAPTCHA_SELECTOR) is not None
    except:
        return False


def wait_for_manual_captcha(page: Page, max_wait=180):
    end = time.time() + max_wait
    while time.time() < end:
        if not captcha_present(page):
            return True
        time.sleep(2)
    return False


# ------------------ FIELD DETECTION ------------------ #
def scan_fields(page: Page):
    fields = page.query_selector_all("input, textarea, select")
    schema = []
    for f in fields:
        schema.append({
            "el": f,
            "name": f.get_attribute("name") or "",
            "id": f.get_attribute("id") or "",
            "placeholder": f.get_attribute("placeholder") or "",
        })
    return schema


def detect_field(field):
    text = " ".join([
        field.get("name", ""),
        field.get("id", ""),
        field.get("placeholder", "")
    ]).lower()

    mapping = {
        "first_name": ["first", "fname"],
        "last_name": ["last", "lname"],
        "email": ["email"],
        "phone": ["phone"],
        "address": ["address", "street"],
        "city": ["city"],
        "state": ["state"],
        "zip": ["zip", "postal"],
    }

    for key, kws in mapping.items():
        if any(k in text for k in kws):
            return key
    return None


def get_value(profile, key):
    return profile.get(key, "")


def build_plan(schema, profile):
    plan = []
    for field in schema:
        key = detect_field(field)
        val = get_value(profile, key)
        if val:
            plan.append((field["el"], val))
    return plan


# ------------------ SUBMIT ------------------ #
def click_submit(page):
    buttons = page.query_selector_all("button, input[type='submit']")
    for b in buttons:
        try:
            b.click(timeout=2000)
            return True
        except:
            continue
    return False


def submit(page):
    if not click_submit(page):
        return "no_submit"

    time.sleep(1)

    if captcha_present(page):
        return "captcha"

    return "submitted"


# ------------------ UI ------------------ #
st.title("🚀 Cuh AI Form Engine SaaS")


# ------------------ RUN ENGINE ------------------ #
if st.button("Start Processing"):

    profile = st.session_state.profile

    with sync_playwright() as p:

        # ✅ FIXED PLAYWRIGHT LAUNCH FOR STREAMLIT CLOUD
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        for i, job in enumerate(st.session_state.jobs):

            url = job["url"]
            job["status"] = "processing"

            page = browser.new_page()

            try:
                page.goto(url, timeout=60000)

                schema = scan_fields(page)
                plan = build_plan(schema, profile)

                for el, val in plan:
                    try:
                        el.fill(str(val))
                    except:
                        pass

                result = submit(page)

                if result == "submitted":
                    job["status"] = "completed"
                elif result == "captcha":
                    job["status"] = "captcha"
                    wait_for_manual_captcha(page)

                else:
                    job["status"] = "failed"

            except PlaywrightTimeoutError:
                job["status"] = "timeout"
            except Exception:
                job["status"] = "error"
            finally:
                page.close()

        browser.close()

    st.success("Done 🚀")
