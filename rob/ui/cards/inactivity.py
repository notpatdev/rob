from __future__ import annotations

from rob.ui.components import make_card, render
from rob.ui.render import CardSection, RenderedMessage
from rob.ui.theme import COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING


def first_inactivity_warning_card(
    *,
    display_name: str,
    server_name: str,
    remove_at_unix: int,
    main_chat_channel: str,
) -> RenderedMessage:
    """First notice, sent when a member is marked inactive."""

    return render(
        make_card(
            title="We've gone quiet on you",
            eyebrow="Inactivity notice",
            body=(
                f"Hey **{display_name}**,\n\n"
                f"Rob hasn't seen you around **{server_name}** in about a week, so "
                "you've been moved to **inactive** for now. Nothing's gone wrong — "
                "this just keeps the member list tidy.\n\n"
                f"Pop a message in {main_chat_channel} (or react, run a command, "
                "anything) and Rob will switch you straight back to **active** — no "
                "reply to this DM needed."
            ),
            color=COLOR_PRIMARY,
            sections=[
                CardSection(
                    title="If you stay inactive",
                    text=(
                        f"You'll be removed from {server_name} "
                        f"<t:{remove_at_unix}:R> (on <t:{remove_at_unix}:F>). "
                        "Rob will send one more reminder before then."
                    ),
                )
            ],
            footer="Automated message — there's nothing to reply to.",
            variant="warning",
        )
    )


def final_inactivity_warning_card(
    *,
    display_name: str,
    server_name: str,
    remove_at_unix: int,
    main_chat_channel: str,
) -> RenderedMessage:
    """Final notice, sent shortly before the scheduled removal."""

    return render(
        make_card(
            title="Last call before you're removed",
            eyebrow="Final inactivity notice",
            body=(
                f"Hey **{display_name}**,\n\n"
                f"This is the last heads-up: you're still marked inactive in "
                f"**{server_name}**, and Rob is set to remove you "
                f"<t:{remove_at_unix}:R>.\n\n"
                f"You can stop this in seconds — just say something in "
                f"{main_chat_channel} or interact with the server and you'll keep "
                "your spot. Hope to see you around!"
            ),
            color=COLOR_WARNING,
            sections=[
                CardSection(
                    title="Scheduled removal",
                    text=f"<t:{remove_at_unix}:F> (<t:{remove_at_unix}:R>)",
                )
            ],
            footer="Automated message — there's nothing to reply to.",
            variant="warning",
        )
    )


def afk_confirmation_card(*, until_unix: int) -> RenderedMessage:
    """Confirms to the author that they're exempt from the inactivity system."""

    return render(
        make_card(
            title="💤 You're marked AFK",
            body=(
                "Rob won't flag you as inactive or remove you while you're away.\n\n"
                f"Your exemption lifts <t:{until_unix}:R> (on <t:{until_unix}:F>). "
                "Just start chatting again whenever you're back — there's nothing to undo."
            ),
            color=COLOR_SUCCESS,
            variant="success",
        )
    )


def inactivity_test_sent_card(sent_count: int) -> RenderedMessage:
    return render(
        make_card(
            title="Inactivity test sent",
            body=f"Sent **{sent_count}** inactivity test message(s) to your DMs.",
            color=COLOR_SUCCESS,
            variant="success",
        )
    )


def inactivity_empty_list_card() -> RenderedMessage:
    return render(
        make_card(
            title="Inactive members",
            body="No members currently have the Inactive role.",
            color=COLOR_SUCCESS,
            variant="success",
        )
    )


def inactivity_list_card(lines: list[str], total: int) -> RenderedMessage:
    return render(
        make_card(
            title="Inactive members",
            eyebrow="Inactive role holders",
            body=f"Members with the Inactive role: **{total}**\n\n" + "\n".join(lines),
            color=COLOR_PRIMARY,
            variant="default",
        )
    )
