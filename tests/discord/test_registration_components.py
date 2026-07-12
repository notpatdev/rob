from __future__ import annotations

from rob.discord.cogs.registration import _SubRegistrationModal
from rob.ui.cards.registration import domme_registered_card, registration_card
from rob.ui.copy import DOMME_REGISTERED_BODY, DOMME_REGISTERED_TITLE


def test_registration_card_has_no_footer_unless_explicit():
    msg = registration_card(title="Rob | Registered", summary="All set.")
    contents = "\n".join(str(getattr(ch, "content", "")) for ch in msg.view.children[0].children)
    assert "-#" not in contents

    msg = registration_card(
        title="Rob | Registered",
        summary="All set.",
        footer="Explicit footer only",
    )
    contents = "\n".join(str(getattr(ch, "content", "")) for ch in msg.view.children[0].children)
    assert "-# Explicit footer only" in contents


def test_domme_registered_card_has_no_footer_unless_explicit():
    msg = domme_registered_card()
    contents = "\n".join(str(getattr(ch, "content", "")) for ch in msg.view.children[0].children)
    assert f"## {DOMME_REGISTERED_TITLE}" in contents
    assert DOMME_REGISTERED_BODY in contents
    assert "-#" not in contents

    msg = domme_registered_card(footer="Explicit footer only")
    contents = "\n".join(str(getattr(ch, "content", "")) for ch in msg.view.children[0].children)
    assert "-# Explicit footer only" in contents


def test_sub_registration_modal_supports_three_fields_with_required_first():
    modal = _SubRegistrationModal(
        cog=object(),
        guild_id=1,
        discord_user_id=2,
    )
    assert modal.send_name_1.required is True
    assert modal.send_name_2.required is False
    assert modal.send_name_3.required is False
