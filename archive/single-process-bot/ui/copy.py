from __future__ import annotations

FORM_URL = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSd6x-wgZ1s-L4zlOdEV76cNcMNgF6JQ8KAV4F9c37uMBZ15mg/"
    "viewform?usp=header"
)

NOT_YOURS = "That button is not for you."
NOT_YOUR_HELP = "That help menu is not for you."
NOT_YOUR_SETUP = "That setup is not for you."
NOT_YOUR_CONFIRM = "That confirmation is not for you."

HELP_INTRO = "Here is what Rob can do."
IMPORT_INTRO = "Upload a `.json` or `.txt` file. Rob will squint at it before saving."
EMPTY_LEADERBOARD = "Nobody is on the board yet. Suspiciously peaceful."
SEND_NOTIFICATION_TITLE = "💸 New send just dropped"
DEPLOY_NOTIFICATION = "Rob survived another deploy."
SUCCESS_FOOTER = "Rob handled the tiny paperwork."
ERROR_FOOTER = "Rob has concerns."
WARNING_FOOTER = "Not ideal. Very on brand."
INFO_FOOTER = "Rob is helpful, unfortunately."

STATUS_LINES = (
    "I am Rob. I do Rob things.",
    "Pris is here.",
    "Rob survived another deploy.",
    "Rob has returned from the void.",
    "Doing Rob things.",
    "Watching buttons get pressed.",
    "Filing tiny paperwork.",
    "Listening for chaos.",
    "Powered by vibes and SQLite.",
    "Existing professionally.",
)


def footer(text: str) -> str:
    return text if text.startswith("-# ") else f"-# {text}"


def step_label(step: int, total: int) -> str:
    return f"Step {step}/{total}"
