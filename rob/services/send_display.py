from __future__ import annotations

from collections.abc import Iterable

from rob.database.repositories.models import SendRecord
from rob.throne.payloads import is_known_test_sender

UNKNOWN_SUB_DISPLAY = "A sub Rob knows not of"
UNCLAIMED_SUB_DISPLAY = "Sub with no nickname claimed"


def build_sub_display(send: SendRecord, *, test_gifter_usernames: Iterable[str] = ()) -> str:
    sub_name = (send.sub_name or "").strip()
    if sub_name.lower() == "anonymous":
        return "The Flying Dutchman"
    if is_known_test_sender(sub_name, test_gifter_usernames=set(test_gifter_usernames)):
        return "Throne's Test User"
    if send.sub_user_id is not None:
        return f"<@{send.sub_user_id}>"
    if sub_name:
        return UNKNOWN_SUB_DISPLAY
    return UNCLAIMED_SUB_DISPLAY


def format_send_source(send: SendRecord) -> str:
    if send.source == "throne_webhook":
        return "Throne Webhook"
    if send.source.startswith("manual:"):
        method = send.method or send.source.split(":", 1)[1]
        return f"Manual ({method})"
    return send.source.replace("_", " ").title()
