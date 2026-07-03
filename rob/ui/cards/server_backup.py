from __future__ import annotations

import discord

from rob.ui.render import (
    CardSection,
    RenderedMessage,
    render_card,
)
from rob.ui.components import make_card
from rob.ui.theme import COLOR_DANGER, COLOR_NEUTRAL, COLOR_SUCCESS

# Exact requested copy, with the "IS"->"IF" typo fixed.
REVAMP_WARNING = (
    "DO NOT ACCEPT THIS IF YOU ARE DOING A REVAMP UNLESS YOU ARE SURE "
    "EVERYTHING CURRENTLY WORKS"
)


def _approval_progress_text(approvals: list[int], required: int) -> str:
    approved = len(approvals)
    line = f"**{approved} / {required}** moderator approvals."
    if approvals:
        line += "\n" + ", ".join(f"<@{user_id}>" for user_id in approvals)
    return line


def major_change_approval_card(
    *,
    change_lines: list[str],
    mod_mentions: str,
    approvals: list[int],
    required_approvals: int,
    view: discord.ui.LayoutView | None = None,
) -> RenderedMessage:
    """Components V2 messages cannot carry plain content, so the moderator
    mentions live in the card body; pass allowed_mentions when sending or
    they won't ping."""
    changes_block = "\n".join(f"* {line}" for line in change_lines) or "* (no details available)"
    body = (
        "### Major Server Change Detected!\n\n"
        f"Hello {mod_mentions},\n\n"
        "Rob has detected a major change in the server's configuration. The "
        "following settings have been changed since the last hourly backup:\n\n"
        f"{changes_block}\n\n"
        f"Before the next backup can run, this will require approval from at "
        f"least **{required_approvals}** moderators."
    )
    card = make_card(
        title="Server Backup — Approval Required",
        body=body,
        color=COLOR_DANGER,
        sections=[CardSection(title="Approvals", text=_approval_progress_text(approvals, required_approvals))],
        footer=REVAMP_WARNING,
        variant="warning",
        eyebrow="Hourly server backup",
    )
    rendered = render_card(card, view=view)
    return rendered


def backup_decision_card(
    *,
    approved: bool,
    change_lines: list[str],
    decided_by_user_id: int | None,
    approvals: list[int],
    required_approvals: int,
) -> RenderedMessage:
    if approved:
        title = "Server Backup — Change Approved"
        summary = (
            "Moderators approved this change. Rob has saved it as the new backup "
            "baseline and hourly backups have resumed."
        )
        color = COLOR_SUCCESS
        variant = "success"
    else:
        title = "Server Backup — Change Rejected"
        summary = (
            "A moderator rejected this change. Rob kept the previous backup "
            "baseline and did not bless these settings. Rob won't re-prompt for "
            "this same change; if the configuration changes again, the next "
            "hourly check raises a fresh approval."
        )
        color = COLOR_NEUTRAL
        variant = "default"

    changes_block = "\n".join(f"* {line}" for line in change_lines) or "* (no details available)"
    sections = [
        CardSection(title="Changes", text=changes_block),
        CardSection(title="Approvals", text=_approval_progress_text(approvals, required_approvals)),
    ]
    if decided_by_user_id is not None:
        verb = "Approved" if approved else "Rejected"
        sections.append(CardSection(title="Decision", text=f"{verb} by <@{decided_by_user_id}>"))

    return render_card(
        make_card(
            title=title,
            body=summary,
            color=color,
            sections=sections,
            footer="Rob logged this decision for the backup audit trail.",
            variant=variant,
            eyebrow="Hourly server backup",
        )
    )
