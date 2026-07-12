from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import discord

from bot.ui import copy
from bot.ui.components import (
    action_section,
    make_container,
    media_gallery,
    separator,
    simple_view,
    subtle,
    text_block,
    thumbnail_section,
)
from bot.ui.theme import (
    COLOR_DANGER,
    COLOR_EVENT,
    COLOR_INFO,
    COLOR_PROFILE,
    COLOR_SUCCESS,
    COLOR_WARNING,
    PROFILE_COLOR_PRESETS,
    ROB_BLUE,
    ROB_DARK,
    ROB_GOLD,
    ROB_GREEN,
    ROB_GREY,
    ROB_PINK,
    ROB_PURPLE,
    ROB_RED,
)

HelpPage = tuple[str, int, str, tuple[tuple[str, str], ...]]


def _colour(value: int | discord.Colour) -> discord.Colour:
    return discord.Colour(value) if isinstance(value, int) else value


def _profile_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _line_block(name: str, value: str | None) -> discord.ui.TextDisplay | None:
    if not value:
        return None
    return text_block(f"**{name}**\n{value}")


def _smart_link_line(url: str | None) -> str | None:
    if not url or not url.strip():
        return None
    link = url.strip()
    lowered = link.casefold()
    if "throne" in lowered:
        label = "Throne"
    elif "paypal" in lowered:
        label = "PayPal"
    elif "cash.app" in lowered or "cashapp" in lowered:
        label = "Cash App"
    elif "venmo" in lowered:
        label = "Venmo"
    elif "wishtender" in lowered:
        label = "Wishtender"
    elif "onlyfans" in lowered:
        label = "OnlyFans"
    elif "fansly" in lowered:
        label = "Fansly"
    elif "loyalfans" in lowered:
        label = "LoyalFans"
    else:
        label = "Link"
    return f"**{label}:** {link}"


def _link_lines(*urls: str | None) -> list[str]:
    return [line for url in urls if (line := _smart_link_line(url))]


def _selected_colour_label(profile_color: int) -> str:
    return next(
        (
            label
            for value, _emoji, label in PROFILE_COLOR_PRESETS
            if value == profile_color
        ),
        f"#{profile_color:06X}",
    )


def success_card(message: str, detail: str | None = None) -> discord.ui.Container:
    body = message if detail is None else f"{message}\n\n{detail}"
    return make_container(
        "✅ Rob | Success | Update",
        body,
        accent_color=COLOR_SUCCESS,
        footer=copy.SUCCESS_FOOTER,
    )


def error_card(message: str, detail: str | None = None) -> discord.ui.Container:
    body = message if detail is None else f"{message}\n\n{detail}"
    return make_container(
        "⚠️ Rob | Errors | Notice",
        body,
        accent_color=COLOR_DANGER,
        footer=copy.ERROR_FOOTER,
    )


def warning_card(message: str, detail: str | None = None) -> discord.ui.Container:
    body = message if detail is None else f"{message}\n\n{detail}"
    return make_container(
        "⚠️ Rob | Errors | Warning",
        body,
        accent_color=COLOR_WARNING,
        footer=copy.WARNING_FOOTER,
    )


def info_card(message: str, detail: str | None = None) -> discord.ui.Container:
    body = message if detail is None else f"{message}\n\n{detail}"
    return make_container(
        "🎉 Rob | Events | Info",
        body,
        accent_color=COLOR_INFO,
        footer=copy.INFO_FOOTER,
    )


def success_view(
    message: str,
    detail: str | None = None,
    *,
    timeout: float | None = 60,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    view.add_item(success_card(message, detail))
    return view


def error_view(
    message: str,
    detail: str | None = None,
    *,
    timeout: float | None = 60,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    view.add_item(error_card(message, detail))
    return view


def warning_view(
    message: str,
    detail: str | None = None,
    *,
    timeout: float | None = 60,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    view.add_item(warning_card(message, detail))
    return view


def info_view(
    message: str,
    detail: str | None = None,
    *,
    timeout: float | None = 60,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    view.add_item(info_card(message, detail))
    return view


def verification_panel_card(button: discord.ui.Button) -> discord.ui.Container:
    return make_container(
        "🔒 Verification",
        (
            "18+ server. Rob needs proof before the doors open.\n\n"
            "Use an approved service. Rob does the boring handoff."
        ),
        sections=[
            separator(),
            action_section("Start the verification flow in DMs.", button),
        ],
        footer="Keep your DMs open. Rob is not psychic.",
        accent_color=ROB_PURPLE,
    )


def verification_intro_card(notice: str | None = None) -> discord.ui.Container:
    sections: list[discord.ui.Item] = [
        separator(),
        text_block(
            f"**{copy.step_label(1, 3)}**\nSend one valid link or one clear screenshot."
        ),
        text_block(
            "**What happens next**\nRob asks your role. Staff get the paperwork."
        ),
    ]
    if notice:
        sections.extend(
            [
                separator(),
                text_block(f"**That last submission did not quite land**\n{notice}"),
            ]
        )
    return make_container(
        "🔒 Verification",
        "Keep it simple. Keep it real.",
        sections=sections,
        footer="Five minute timeout. Rob hates waiting.",
        accent_color=ROB_PURPLE,
    )


def verification_submitted_card(
    selected_role: str | None = None,
) -> discord.ui.Container:
    extra = f"\n\n**Selected role**\n{selected_role}" if selected_role else ""
    return make_container(
        "🧾 Verification Submitted",
        (f"Request sent. Staff have the buttons now.{extra}"),
        footer="Now we wait for the button people.",
        accent_color=ROB_BLUE,
    )


def role_selection_card(
    domme_button: discord.ui.Button,
    submissive_button: discord.ui.Button,
    *,
    selected_role: str | None = None,
) -> discord.ui.Container:
    sections: list[discord.ui.Item] = [
        separator(),
        text_block(
            f"**{copy.step_label(2, 3)}**\n"
            "Pick the role staff should apply after approval."
        ),
        action_section("I am signing up as a Domme.", domme_button),
        action_section("I am signing up as a Submissive.", submissive_button),
    ]
    if selected_role:
        sections.extend([separator(), text_block(f"**Selected**\n{selected_role}")])
    return make_container(
        "🎭 Role Selection",
        "Pick the role staff should hand you if this clears.",
        sections=sections,
        footer="This is just paperwork.",
        accent_color=ROB_PINK,
    )


def staff_review_card(
    *,
    request_id: int,
    user_mention: str,
    user_id: int,
    selected_role: str | None,
    submitted_service: str | None,
    submitted_value: str | None,
    status: str,
    approve_button: discord.ui.Button,
    deny_underage_button: discord.ui.Button,
    deny_invalid_button: discord.ui.Button,
    link_button: discord.ui.Button | None = None,
    avatar_url: str | None = None,
) -> discord.ui.Container:
    submitted = submitted_value or "No submission text."
    sections: list[discord.ui.Item] = [
        separator(),
        text_block(f"**User**\n{user_mention}\n`{user_id}`"),
        text_block(f"**Selected role**\n{selected_role or 'Not provided'}"),
        text_block(f"**Service**\n{submitted_service or 'Unknown'}"),
        text_block(f"**Status**\n{status}"),
        text_block(f"**Submitted**\n{submitted}"),
        separator(),
        action_section("Approve this verification request.", approve_button),
        action_section("Deny because the user is under 18.", deny_underage_button),
        action_section(
            "Deny because the service or proof is not valid.", deny_invalid_button
        ),
    ]
    if link_button is not None:
        sections.insert(
            6, action_section("Open the submitted link in a browser.", link_button)
        )

    container_items: list[discord.ui.Item]
    if avatar_url:
        container_items = [
            thumbnail_section("## 🛂 Verification Review", avatar_url),
            *sections,
        ]
        return discord.ui.Container(*container_items, accent_color=ROB_PURPLE)

    return make_container(
        "🛂 Verification Review",
        None,
        sections=sections,
        footer=f"Request #{request_id}",
        accent_color=ROB_PURPLE,
    )


def staff_review_complete_card(
    *,
    request_id: int,
    user_mention: str,
    status: str,
    detail: str | None = None,
) -> discord.ui.Container:
    body = f"**User**\n{user_mention}\n\n**Status**\n{status}"
    if detail:
        body = f"{body}\n\n**Detail**\n{detail}"
    return make_container(
        "🛂 Verification Review Complete",
        body,
        footer=f"Request #{request_id}",
        accent_color=ROB_BLUE,
    )


def build_help_pages(
    *,
    is_domme: bool,
    is_sub: bool,
    is_moderator: bool,
) -> list[HelpPage]:
    pages: list[HelpPage] = [
        (
            "General",
            ROB_BLUE.value,
            copy.HELP_INTRO,
            (
                ("/help", "Open the manual."),
                ("/register action:domme", "Register as a Domme."),
                ("/register action:sub", "Register as a Sub."),
            ),
        ),
    ]
    if is_domme:
        pages.append(
            (
                "Domme Profiles",
                ROB_PINK.value,
                "Profile tools for Dommes.",
                (
                    ("/domme", "Show your profile or start setup."),
                    ("/domme user:@Someone", "Show another profile."),
                    ("/domme action:leaderboard", "Show your send board."),
                    ("/domme action:delete", "Delete your profile."),
                ),
            )
        )
    if is_sub:
        pages.append(
            (
                "Sub Profiles",
                ROB_DARK.value,
                "Profile tools for Subs.",
                (
                    ("/sub", "Show your profile or start setup."),
                    ("/sub action:delete", "Delete your profile."),
                ),
            )
        )
    if is_moderator:
        pages.extend(
            [
                (
                    "Verification",
                    ROB_PURPLE.value,
                    "Verification and cleanup tools.",
                    (
                        ("!setup_verification", "Post the verification panel."),
                        ("!verify_status <user>", "Check one verification record."),
                        (
                            "!verify_cleanup",
                            "Show users still holding the unverified role.",
                        ),
                    ),
                ),
                (
                    "System",
                    ROB_GREY.value,
                    "Admin bits. Button wrench included.",
                    (
                        ("!throne refresh", "Show the webhook-only tracking reminder."),
                        (
                            "!throne status",
                            "Show Throne registration and webhook tracking health.",
                        ),
                        (
                            "!throne list",
                            "List registered Throne users and webhook status.",
                        ),
                        (
                            "!throne search <@user|id>",
                            "Show one Throne creator record.",
                        ),
                        (
                            "!throne webhook refresh <@user|id>",
                            "Dev-only webhook secret rotation + DM.",
                        ),
                        ("!event status", "Show loaded event config and current mode."),
                        ("!event reload", "Reload `config/events.json`."),
                        ("/reaction_role_setup", "Create a reaction-role message."),
                        ("!resync [guild|clear|global]", "Re-sync slash commands."),
                    ),
                ),
            ]
        )
    return pages


def help_page_card(
    page_index: int,
    total_pages: int,
    pages: list[HelpPage] | None = None,
) -> discord.ui.Container:
    pages = pages or build_help_pages(is_domme=True, is_sub=True, is_moderator=True)
    page_index = max(0, min(page_index, len(pages) - 1))
    section_name, colour_value, blurb, entries = pages[page_index]

    sections: list[discord.ui.Item] = [
        separator(),
        text_block(f"**{section_name}**\n{blurb}"),
    ]
    for name, description in entries:
        sections.extend(
            [separator(visible=False), text_block(f"**{name}**\n{description}")]
        )

    return make_container(
        "🧭 Rob Help",
        "Pick a page. Press buttons.",
        sections=sections,
        footer=f"Page {page_index + 1}/{total_pages}",
        accent_color=_colour(colour_value),
    )


def profile_setup_step_card(
    *,
    title: str,
    body: str,
    step: str,
    previews: list[tuple[str, str | None]] | None = None,
    footer: str | None = None,
    accent_color: discord.Colour | int = COLOR_PROFILE,
) -> discord.ui.Container:
    sections: list[discord.ui.Item] = [separator(), text_block(f"**{step}**\n{body}")]
    for label, value in previews or []:
        block = _line_block(label, value)
        if block is not None:
            sections.extend([separator(visible=False), block])
    return make_container(
        title,
        None,
        sections=sections,
        footer=footer,
        accent_color=accent_color,
    )


def domme_setup_intro_card() -> discord.ui.Container:
    return make_container(
        "💋 Domme Profile Setup",
        ("Rob is building your card.\n\nSkip whatever you do not want to share."),
        sections=[
            separator(),
            text_block(f"**{copy.step_label(1, 6)}**\nStart with the basics."),
        ],
        footer="Rob is helpful, unfortunately.",
        accent_color=ROB_PINK,
    )


def domme_setup_name_card(
    *, name: str | None, honorific: str | None
) -> discord.ui.Container:
    return profile_setup_step_card(
        title="💋 Name & Honorific",
        body="How should people address you?",
        step=copy.step_label(1, 6),
        previews=[
            ("Name", _profile_value(name)),
            ("Honorific", _profile_value(honorific)),
        ],
        footer="Skip it if mystery is the brand.",
        accent_color=ROB_PINK,
    )


def domme_setup_details_card(
    *,
    pronouns: str | None,
    age: str | None,
    tribute_price: str | None,
    kinks: str | None,
    limits: str | None,
) -> discord.ui.Container:
    return profile_setup_step_card(
        title="📝 Details",
        body="Add the usual details.",
        step=copy.step_label(2, 6),
        previews=[
            ("Pronouns", _profile_value(pronouns)),
            ("Age", _profile_value(age)),
            ("Tribute", _profile_value(tribute_price)),
            ("Kinks", _profile_value(kinks)),
            ("Limits", _profile_value(limits)),
        ],
        footer="Empty fields stay quiet.",
        accent_color=ROB_PURPLE,
    )


def domme_setup_payments_card(
    *,
    throne: str | None,
    tribute_link: str | None,
    payment_link1: str | None,
    payment_link2: str | None,
    payment_link3: str | None,
    payment_link4: str | None,
    content_link1: str | None,
    content_link2: str | None,
    content_link3: str | None,
    content_link4: str | None,
) -> discord.ui.Container:
    payment_lines = _link_lines(
        payment_link1, payment_link2, payment_link3, payment_link4
    )
    content_lines = _link_lines(
        content_link1, content_link2, content_link3, content_link4
    )
    return profile_setup_step_card(
        title="🔗 Links",
        body="Add your payment and content links.",
        step=copy.step_label(3, 6),
        previews=[
            ("Throne", _profile_value(throne)),
            ("Tribute Link", _profile_value(tribute_link)),
            ("Payment Links", "\n".join(payment_lines) if payment_lines else None),
            ("Content Links", "\n".join(content_lines) if content_lines else None),
        ],
        footer="This saves everyone some typing.",
        accent_color=ROB_PURPLE,
    )


def domme_setup_throne_card(*, throne: str | None) -> discord.ui.Container:
    return profile_setup_step_card(
        title="👑 Throne Tracking",
        body="You added a Throne link. Want Rob to watch it for sends?",
        step=copy.step_label(4, 6),
        previews=[("Current Throne", _profile_value(throne))],
        footer="Optional. Slightly chaotic.",
        accent_color=ROB_PINK,
    )


def domme_setup_color_card(*, profile_color: int) -> discord.ui.Container:
    return profile_setup_step_card(
        title="🎨 Profile Colour",
        body="Pick your profile colour.",
        step=copy.step_label(5, 6),
        previews=[("Selected Colour", _selected_colour_label(profile_color))],
        footer="Tasteful drama is allowed.",
        accent_color=profile_color,
    )


def domme_setup_review_card(
    *,
    name: str | None,
    honorific: str | None,
    pronouns: str | None,
    age: str | None,
    tribute_price: str | None,
    throne: str | None,
    tribute_link: str | None,
    payment_link1: str | None,
    payment_link2: str | None,
    payment_link3: str | None,
    payment_link4: str | None,
    content_link1: str | None,
    content_link2: str | None,
    content_link3: str | None,
    content_link4: str | None,
    profile_color: int,
    throne_tracking_enabled: bool,
    kinks: str | None,
    limits: str | None,
) -> discord.ui.Container:
    payment_lines = _link_lines(
        payment_link1, payment_link2, payment_link3, payment_link4
    )
    content_lines = _link_lines(
        content_link1, content_link2, content_link3, content_link4
    )
    return profile_setup_step_card(
        title="✅ Review Your Domme Profile",
        body="Last look before Rob files it.",
        step=copy.step_label(6, 6),
        previews=[
            ("Name", _profile_value(name)),
            ("Honorific", _profile_value(honorific)),
            ("Pronouns", _profile_value(pronouns)),
            ("Age", _profile_value(age)),
            ("Tribute", _profile_value(tribute_price)),
            ("Kinks", _profile_value(kinks)),
            ("Limits", _profile_value(limits)),
            ("Throne", _profile_value(throne)),
            ("Tribute Link", _profile_value(tribute_link)),
            ("Payment Links", "\n".join(payment_lines) if payment_lines else None),
            ("Content Links", "\n".join(content_lines) if content_lines else None),
            (
                "Throne Tracking",
                "Enabled" if throne_tracking_enabled else "Not enabled",
            ),
            ("Profile Colour", _selected_colour_label(profile_color)),
        ],
        footer="If it looks right, hit save.",
        accent_color=profile_color,
    )


def domme_setup_complete_card() -> discord.ui.Container:
    return success_card(
        "Saved.",
        "Use `/domme` or `!domme` to show it off.",
    )


def domme_setup_later_card() -> discord.ui.Container:
    return info_card(
        "Cancelled.",
        "Run `/domme` or `!domme` when you want round two.",
    )


def domme_setup_cancelled_card() -> discord.ui.Container:
    return warning_card(
        "Nothing saved.",
        "Rob put the draft back in the void.",
    )


def sub_setup_intro_card() -> discord.ui.Container:
    return make_container(
        "🪢 Sub Profile Setup",
        ("Rob is linking your Throne name and basic profile bits."),
        sections=[
            separator(),
            text_block(f"**{copy.step_label(1, 6)}**\nStart with your Throne name."),
        ],
        footer="Short setup. Useful little gremlin work.",
        accent_color=ROB_DARK,
    )


def sub_setup_name_card(*, throne_name: str | None) -> discord.ui.Container:
    return profile_setup_step_card(
        title="🪪 Throne Name",
        body="What name do you use when sending on Throne?",
        step=copy.step_label(1, 6),
        previews=[("Current Throne Name", _profile_value(throne_name))],
        footer="This is how Rob does the maths.",
        accent_color=ROB_DARK,
    )


def sub_setup_details_card(
    *,
    name: str | None,
    pronouns: str | None,
    age: str | None,
) -> discord.ui.Container:
    return profile_setup_step_card(
        title="🙋 Details",
        body="Optional basics for your profile.",
        step=copy.step_label(2, 6),
        previews=[
            ("Name", _profile_value(name)),
            ("Pronouns", _profile_value(pronouns)),
            ("Age", _profile_value(age)),
        ],
        footer="Keep it sparse. Rob can cope.",
        accent_color=ROB_BLUE,
    )


def sub_setup_kinks_limits_card(
    *,
    kinks: str | None,
    limits: str | None,
) -> discord.ui.Container:
    return profile_setup_step_card(
        title="🧠 Kinks & Limits",
        body="Add what is in and what is out.",
        step=copy.step_label(3, 6),
        previews=[
            ("Kinks", _profile_value(kinks)),
            ("Limits", _profile_value(limits)),
        ],
        footer="Useful. Also less weird.",
        accent_color=ROB_BLUE,
    )


def sub_setup_color_card(*, profile_color: int) -> discord.ui.Container:
    return profile_setup_step_card(
        title="🎨 Profile Colour",
        body="Pick your profile colour.",
        step=copy.step_label(4, 6),
        previews=[("Selected Colour", _selected_colour_label(profile_color))],
        footer="Tiny choice. Big colour.",
        accent_color=profile_color,
    )


def sub_setup_owner_card(
    *,
    owned_by_label: str | None,
) -> discord.ui.Container:
    return profile_setup_step_card(
        title="🫶 Owned By",
        body="Optional. Link yourself to a Domme profile.",
        step=copy.step_label(5, 6),
        previews=[("Current Selection", _profile_value(owned_by_label))],
        footer="Leave it blank if not.",
        accent_color=ROB_BLUE,
    )


def sub_setup_review_card(
    *,
    throne_name: str | None,
    name: str | None,
    pronouns: str | None,
    age: str | None,
    kinks: str | None,
    limits: str | None,
    profile_color: int,
    owned_by_label: str | None,
) -> discord.ui.Container:
    return profile_setup_step_card(
        title="✅ Review Your Sub Profile",
        body="Last look before Rob files it.",
        step=copy.step_label(6, 6),
        previews=[
            ("Throne Name", _profile_value(throne_name)),
            ("Name", _profile_value(name)),
            ("Pronouns", _profile_value(pronouns)),
            ("Age", _profile_value(age)),
            ("Kinks", _profile_value(kinks)),
            ("Limits", _profile_value(limits)),
            ("Owned By", _profile_value(owned_by_label)),
            ("Profile Colour", _selected_colour_label(profile_color)),
        ],
        footer="If it looks good, save it.",
        accent_color=profile_color,
    )


def sub_setup_complete_card() -> discord.ui.Container:
    return success_card(
        "Saved.",
        "Use `/sub` to show it or tweak it later.",
    )


def sub_setup_later_card() -> discord.ui.Container:
    return info_card(
        "Cancelled.",
        "Run `/sub` whenever you want back in.",
    )


def sub_setup_cancelled_card() -> discord.ui.Container:
    return warning_card(
        "Nothing saved.",
        "Rob binned the draft respectfully.",
    )


def delete_confirm_card(
    *,
    title: str,
    body: str,
    delete_button: discord.ui.Button,
    cancel_button: discord.ui.Button,
) -> discord.ui.Container:
    return make_container(
        title,
        body,
        sections=[
            separator(),
            action_section("Yep. Delete it.", delete_button),
            action_section("Leave it alone.", cancel_button),
        ],
        footer="No undo. Very exciting.",
        accent_color=COLOR_DANGER,
    )


def import_ids_upload_card(button: discord.ui.Button) -> discord.ui.Container:
    return make_container(
        "📥 Import Server IDs",
        copy.IMPORT_INTRO,
        sections=[
            separator(),
            text_block(
                "**Accepted formats**\n"
                '`JSON` — `{"GUILD_ID": 123, ...}`\n'
                "`Text` — `GUILD_ID=123` or `guild_id: 123`"
            ),
            action_section("Upload the file.", button),
        ],
        footer="Admin only. Nothing saves until you confirm it.",
        accent_color=ROB_PURPLE,
    )


def import_ids_confirm_card(
    parsed: dict[str, int], warnings: list[str]
) -> discord.ui.Container:
    field_labels = {
        "GUILD_ID": "Guild",
        "REGISTRATION_CHANNEL_ID": "Registration Channel",
        "LEADERBOARD_CHANNEL_ID": "Leaderboard Channel",
        "SEND_TRACK_CHANNEL_ID": "Send Track Channel",
        "DOMME_ROLE_ID": "Domme Role",
        "SUBMISSIVE_ROLE_ID": "Submissive Role",
        "MODERATION_ROLE_ID": "Moderation Role",
        "EVENT_BAN_ROLE_ID": "Event Ban Role",
    }
    sections: list[discord.ui.Item] = [separator()]
    for key, value in parsed.items():
        label = field_labels.get(key, key)
        sections.extend(
            [text_block(f"**{label}**\n`{value}`"), separator(visible=False)]
        )
    if warnings:
        sections.extend(
            [
                separator(),
                text_block(
                    "**Warnings**\n" + "\n".join(f"• {warning}" for warning in warnings)
                ),
            ]
        )
    return make_container(
        "✅ Confirm ID Import",
        "Rob parsed the file. Give it a quick squint.",
        sections=sections,
        footer="Confirm writes `bot/channels.py`.",
        accent_color=ROB_GREEN,
    )


def form_link_card(button: discord.ui.Button) -> discord.ui.Container:
    return make_container(
        "📝 Extra Questions",
        "Staff have your request. There is also a short form here, if you are feeling helpful.",
        sections=[
            separator(),
            action_section("Open the form in your browser.", button),
        ],
        footer="Optional. Weirdly useful.",
        accent_color=ROB_BLUE,
    )


def event_status_card(
    *,
    event_name: str,
    status_line: str,
    domme_count: int,
    sub_count: int,
    send_count: int,
    send_total_usd: str,
) -> discord.ui.Container:
    return make_container(
        f"📊 Event Status — {event_name}",
        status_line,
        sections=[
            separator(),
            text_block(f"**Dommes registered**\n{domme_count}"),
            text_block(f"**Subs registered**\n{sub_count}"),
            text_block(f"**Total sends**\n{send_count} — {send_total_usd}"),
        ],
        accent_color=ROB_BLUE,
        footer="Live numbers. Mostly.",
    )


def leaderboard_card(
    *,
    title: str,
    status_line: str | None,
    rows_text: str,
    accent_color: discord.Colour | int,
    footer: str | None = None,
) -> discord.ui.Container:
    sections: list[discord.ui.Item] = []
    if status_line:
        sections.extend([separator(), text_block(status_line)])
    sections.extend([separator(), text_block(rows_text)])
    return make_container(
        title,
        None,
        sections=sections,
        accent_color=accent_color,
        footer=footer,
    )


def send_notification_card(
    *,
    sub_label: str,
    domme_label: str,
    amount_label: str,
    item_name: str | None,
    item_image_url: str | None,
    sub_rank: int | None,
    domme_send_count: int,
) -> discord.ui.Container:
    details_lines = [
        f"**Sub:** {sub_label}",
        f"**Domme:** {domme_label}",
        f"**Amount:** {amount_label}",
    ]
    if item_name:
        details_lines.append(f"**Item:** {item_name}")

    footer_parts: list[str] = []
    if sub_rank is not None:
        footer_parts.append(f"Sub rank: **#{sub_rank}**")
    footer_parts.append(f"Domme total sends: **{domme_send_count}**")

    sections: list[discord.ui.Item] = [separator()]
    detail_text = "\n".join(details_lines)
    if item_image_url:
        sections.append(thumbnail_section(detail_text, item_image_url))
    else:
        sections.append(text_block(detail_text))

    if footer_parts:
        sections.extend([separator(), subtle(" · ".join(footer_parts))])

    return make_container(
        copy.SEND_NOTIFICATION_TITLE,
        None,
        sections=sections,
        accent_color=ROB_GREEN,
    )


def domme_profile_card(
    profile: Mapping[str, Any],
    *,
    display_name: str,
    avatar_url: str | None = None,
    is_verified: bool = False,
) -> discord.ui.Container:
    header_lines = [f"**Display name:** {display_name}"]
    for label, key in (
        ("Honorific", "honorific"),
        ("Pronouns", "pronouns"),
        ("Age", "age"),
        ("Tribute", "tribute_price"),
    ):
        value = _profile_value(profile.get(key))
        if value:
            header_lines.append(f"**{label}:** {value}")
    header_lines.append("**Verified:** Yes" if is_verified else "**Verified:** No")

    sections: list[discord.ui.Item] = [separator()]
    if avatar_url:
        sections.append(thumbnail_section("\n".join(header_lines), avatar_url))
    else:
        sections.append(text_block("\n".join(header_lines)))

    for label, key in (("Kinks", "kinks"), ("Limits", "limits"), ("Throne", "throne")):
        block = _line_block(label, _profile_value(profile.get(key)))
        if block:
            sections.extend([separator(visible=False), block])

    payment_lines = _link_lines(
        profile.get("payment_link1"),
        profile.get("payment_link2"),
        profile.get("payment_link3"),
        profile.get("payment_link4"),
    )
    if payment_lines:
        sections.extend(
            [separator(), text_block("**Payment Links**\n" + "\n".join(payment_lines))]
        )

    content_lines = _link_lines(
        profile.get("content_link1"),
        profile.get("content_link2"),
        profile.get("content_link3"),
        profile.get("content_link4"),
    )
    if content_lines:
        sections.extend(
            [separator(), text_block("**Content Links**\n" + "\n".join(content_lines))]
        )

    return make_container(
        f"✨ {display_name}",
        None,
        sections=sections,
        accent_color=profile.get("profile_color", COLOR_PROFILE),
    )


def sub_profile_card(
    profile: Mapping[str, Any],
    *,
    display_name: str,
    avatar_url: str | None = None,
) -> discord.ui.Container:
    header_lines = [f"**Display name:** {display_name}"]
    for label, key in (
        ("Name", "name"),
        ("Pronouns", "pronouns"),
        ("Age", "age"),
        ("Throne Name", "throne_name"),
    ):
        value = _profile_value(profile.get(key))
        if value:
            header_lines.append(f"**{label}:** {value}")

    sections: list[discord.ui.Item] = [separator()]
    if avatar_url:
        sections.append(thumbnail_section("\n".join(header_lines), avatar_url))
    else:
        sections.append(text_block("\n".join(header_lines)))

    for label, key in (
        ("Kinks", "kinks"),
        ("Limits", "limits"),
        ("Owned By", "owned_by_label"),
    ):
        block = _line_block(label, _profile_value(profile.get(key)))
        if block:
            sections.extend([separator(visible=False), block])

    return make_container(
        f"🫶 {display_name}",
        None,
        sections=sections,
        accent_color=profile.get("profile_color", ROB_BLUE),
    )
