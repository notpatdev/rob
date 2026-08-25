from __future__ import annotations

import discord

from rob.ui.components import make_card, render
from rob.ui.render import CardSection, RenderedMessage, require_components_v2
from rob.ui.theme import COLOR_DANGER, COLOR_INFO, COLOR_SUCCESS, COLOR_WARNING


def counting_status_card(*, current_number: int, enabled: bool) -> RenderedMessage:
    return render(
        make_card(
            title="Rob | Counting",
            body="Current counting channel state.",
            color=COLOR_INFO,
            variant="counting",
            eyebrow="Counting",
            sections=[
                CardSection(title="Enabled", text="Yes" if enabled else "No", inline=True),
                CardSection(title="Current Number", text=str(current_number), inline=True),
            ],
        )
    )


def counting_updated_card(number: int) -> RenderedMessage:
    return render(
        make_card(
            title="Rob | Count Updated",
            body=f"Counting has been set to **{number}**.",
            color=COLOR_SUCCESS,
            variant="counting",
            eyebrow="Counting",
        )
    )


def counting_same_user_reminder_card() -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=30)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Counting"),
            discord.ui.TextDisplay("### Hold up there, speedy."),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "One number per person at a time.\n"
                "Let someone else have a go before you count again."
            ),
            accent_color=COLOR_WARNING,
        )
    )
    return RenderedMessage(view=view)


def count_rescue_needed_card(*, remaining_seconds: int, deadline_unix: int) -> RenderedMessage:
    return count_rescue_needed_for_role_card(
        remaining_seconds=remaining_seconds,
        deadline_unix=deadline_unix,
        failed_user_role="sub",
        claimed_restriction=False,
        claimed_unresolved=False,
    )


def count_rescue_needed_for_role_card(
    *,
    remaining_seconds: int,
    deadline_unix: int,
    failed_user_role: str,
    claimed_restriction: bool,
    claimed_unresolved: bool,
) -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=1800)
    remaining_seconds = max(0, remaining_seconds)
    minutes, seconds = divmod(remaining_seconds, 60)
    if failed_user_role == "domme":
        opener = "A Dom/me fumbled the count. Subs have 5 minutes to send to them and save it."
    else:
        opener = "A Sub fumbled the count. They have 5 minutes to send to any Dom/me and save it."
    claim_line = ""
    if failed_user_role == "sub" and claimed_restriction:
        claim_line = "There is a restriction on which Dom/me the recovery send must go to.\n\n"
    if failed_user_role == "sub" and claimed_unresolved:
        claim_line = (
            "Because your claimed Dom/me could not be resolved, staff should review your claim role before retrying.\n\n"
        )
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Counting"),
            discord.ui.TextDisplay("### Count Rescue Needed"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "The count is wobbling.\n"
                f"{opener}\n\n"
                f"{claim_line}"
                "-# Time remaining:\n"
                f"**{minutes}m {seconds:02d}s**\n\n"
                "-# Deadline:\n"
                f"<t:{deadline_unix}:R> / <t:{deadline_unix}:f>"
            ),
            accent_color=COLOR_WARNING,
        )
    )
    return RenderedMessage(view=view)


def count_saved_card(*, next_number: int) -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=600)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Counting"),
            discord.ui.TextDisplay("### Count Saved"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "Rob saw the send and duct-taped the count back together.\n"
                f"Continue from **{next_number}**."
            ),
            accent_color=COLOR_SUCCESS,
        )
    )
    return RenderedMessage(view=view)


def count_failed_card() -> RenderedMessage:
    return count_failed_reset_card()


def count_failed_reset_card() -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=600)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Counting"),
            discord.ui.TextDisplay("### Count Failed"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "No qualifying send arrived in time.\n"
                "Rob has reset the count to **1**."
            ),
            accent_color=COLOR_DANGER,
        )
    )
    return RenderedMessage(view=view)


def count_failed_sub_blocked_card(*, blocked_until_unix: int) -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=600)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Counting"),
            discord.ui.TextDisplay("### Count Failed"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "No recovery send was detected.\n"
                "You fumbled the count and missed the recovery window, so you are blocked from counting for 12 hours.\n\n"
                f"You can count again <t:{blocked_until_unix}:R>."
            ),
            accent_color=COLOR_DANGER,
        )
    )
    return RenderedMessage(view=view)


def count_blocked_sub_card(*, blocked_until_unix: int) -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=120)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Counting"),
            discord.ui.TextDisplay("### Count Blocked"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "You fumbled the count and missed the recovery window, so you are blocked from counting for 12 hours.\n\n"
                f"You can count again <t:{blocked_until_unix}:R>."
            ),
            accent_color=COLOR_DANGER,
        )
    )
    return RenderedMessage(view=view)
