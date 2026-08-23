"""Durable private profile wizard components.

All state lives in Worker drafts.  Dynamic IDs carry enough routing context to
reject replayed controls after a restart, but every callback still reloads the
draft because an ID is not an authorization decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import discord

from bill.components.custom_ids import (
    decode_resource_id,
    decode_uint,
    encode_resource_id,
    encode_uint,
)
from bill.worker_client import (
    DmStatus,
    DraftScope,
    DraftStepKey,
    LinkType,
    Orientation,
    ProfileDraft,
    ProfileLink,
    ServerProfileMode,
    WizardStage,
    WorkerAPIError,
)

if TYPE_CHECKING:
    from bill.bot import BillBot

PRONOUNS = (
    "She/Her",
    "He/Him",
    "They/Them",
    "It/Its",
    "She/They",
    "He/They",
    "Any Pronouns",
    "Ask Me",
)
HONOURIFICS = (
    "Goddess",
    "Mistress",
    "Princess",
    "Temptress",
    "Enchantress",
    "Mommy",
    "Master",
    "Daddy",
    "CashMaster",
)
SUBMISSIVE_LABELS = ("Submissive", "Sub", "Brat", "Pet", "Good boy", "Good girl", "Good pet", "Toy")
ORIENTATION_LABELS = {
    Orientation.DOMME: "Dom/me",
    Orientation.SUBMISSIVE: "Submissive",
    Orientation.SWITCH_DOMME: "Switch (Dom/me lean)",
    Orientation.SWITCH_SUBMISSIVE: "Switch (submissive lean)",
}
DM_STATUS_OPTIONS = (
    (DmStatus.OPEN, "Open", "DMs are welcome"),
    (DmStatus.BY_REQUEST, "By Request", "Ask before sending a DM"),
    (DmStatus.AFTER_TRIBUTE, "After Tribute", "DMs open after tribute"),
    (DmStatus.CLOSED, "Closed", "Not accepting DMs"),
)
PROFILE_COLOR_OPTIONS = (
    ("Blue", 0x5865F2),
    ("Purple", 0x9B59B6),
    ("Rose", 0xE0568A),
    ("Red", 0xE74C3C),
    ("Orange", 0xE67E22),
    ("Gold", 0xD4A72C),
    ("Emerald", 0x2EAD78),
    ("Teal", 0x2AA198),
)
PROFILE_WIZARD_BUTTON_ACTIONS = (
    "start",
    "publish",
    "restart",
    "continue",
    "back",
    "use-global",
    "bio",
    "skip-bio",
    "aliases",
    "link-social",
    "link-payment",
    "import",
    "visibility",
    "complete-links",
    "skip-links",
    "throne",
    "skip-throne",
    "check-throne",
    "rotate",
    "custom-color",
    "edit-orientation",
    "edit-pronouns",
    "edit-titles",
    "edit-dm",
    "edit-bio",
    "edit-color",
    "edit-links",
    "edit-throne",
    "edit-details",
)
PROFILE_WIZARD_SELECT_ACTIONS = (
    "orientation",
    "pronouns",
    "honourifics",
    "labels",
    "dm-status",
    "profile-color",
    "stats",
    "link-select",
    "creator-select",
)
_PROFILE_WIZARD_ACTIONS = frozenset(
    (*PROFILE_WIZARD_BUTTON_ACTIONS, *PROFILE_WIZARD_SELECT_ACTIONS)
)
_PROFILE_WIZARD_ID_PATTERN = (
    r"bill:p:(?P<draft>[A-Za-z0-9_-]+):(?P<owner>[a-z0-9]+):"
    r"(?P<guild>[a-z0-9]+):(?P<revision>[a-z0-9]+):"
)
_PROFILE_WIZARD_BUTTON_TEMPLATE = re.compile(
    _PROFILE_WIZARD_ID_PATTERN
    + rf"(?P<action>{'|'.join(map(re.escape, PROFILE_WIZARD_BUTTON_ACTIONS))})$"
)
_PROFILE_WIZARD_SELECT_TEMPLATE = re.compile(
    _PROFILE_WIZARD_ID_PATTERN
    + rf"(?P<action>{'|'.join(map(re.escape, PROFILE_WIZARD_SELECT_ACTIONS))})$"
)


def safe_text(value: str, *, limit: int = 300) -> str:
    """Escape user-provided strings before V2 rendering and prevent pings."""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))[:limit]


@dataclass(frozen=True, slots=True)
class MemberPresentation:
    display_name: str
    avatar_url: str | None = None


def member_presentation(user: object) -> MemberPresentation:
    """Read transient Discord presentation data without adding it to Worker state."""
    display_name = (
        getattr(user, "display_name", None)
        or getattr(user, "global_name", None)
        or getattr(user, "name", None)
        or "Bill member"
    )
    avatar = getattr(user, "display_avatar", None)
    avatar_url = getattr(avatar, "url", None)
    return MemberPresentation(str(display_name), str(avatar_url) if avatar_url else None)


def wizard_custom_id(draft: ProfileDraft, action: str) -> str:
    """Build a <=100-character persistent ID bound to all durable auth context."""
    if action not in _PROFILE_WIZARD_ACTIONS:
        raise ValueError(f"Unsupported Bill profile component action: {action}")
    custom_id = (
        f"bill:p:{encode_resource_id(draft.id)}:{encode_uint(draft.owner_user_id)}:"
        f"{encode_uint(draft.origin_guild_id)}:{encode_uint(draft.revision)}:{action}"
    )
    if len(custom_id) > 100:
        raise ValueError("Bill profile component ID exceeds Discord's 100-character limit")
    return custom_id


def _caps(orientation: Orientation | None) -> tuple[bool, bool, bool, bool, bool]:
    if orientation is Orientation.DOMME:
        return True, False, False, True, False
    if orientation is Orientation.SUBMISSIVE:
        return False, True, True, False, True
    return True, True, True, True, True


def _summary(draft: ProfileDraft, key: DraftStepKey) -> str:
    if key is DraftStepKey.ORIENTATION:
        return ORIENTATION_LABELS.get(draft.governing_orientation, "Chosen orientation")
    if key is DraftStepKey.IDENTITY:
        values = (
            *draft.document.selections.pronouns,
            *draft.document.selections.honourifics,
            *draft.document.selections.submissive_labels,
        )
        return ", ".join(safe_text(value, limit=70) for value in values) or "Identity saved"
    if key is DraftStepKey.LINKS:
        return f"{len(draft.document.links)} link(s) saved"
    if key is DraftStepKey.THRONE:
        return "Throne connected" if draft.document.throne_creator_id else "Throne skipped"
    return "Ready to publish"


def _is_linked(draft: ProfileDraft) -> bool:
    return (
        draft.target_scope is DraftScope.SERVER
        and draft.server_mode is ServerProfileMode.LINKED
    )


def wizard_stages(
    orientation: Orientation | None,
    *,
    linked: bool = False,
) -> tuple[WizardStage, ...]:
    stages = [WizardStage.PRONOUNS]
    if not linked:
        stages.insert(0, WizardStage.ORIENTATION)
    honourifics, labels, aliases, _, stats = _caps(orientation)
    if honourifics:
        stages.append(WizardStage.HONOURIFICS)
    if labels:
        stages.append(WizardStage.SUBMISSIVE_LABELS)
    stages.extend(
        (
            WizardStage.DM_STATUS,
            WizardStage.BIO,
            WizardStage.PROFILE_COLOR,
            WizardStage.LINKS,
        )
    )
    if orientation is not None and _caps(orientation)[3] and not linked:
        stages.append(WizardStage.THRONE)
    if aliases or stats:
        stages.append(WizardStage.DETAILS)
    stages.append(WizardStage.REVIEW)
    return tuple(stages)


def _legacy_stage(draft: ProfileDraft) -> WizardStage:
    current = draft.next_step or draft.current_step or DraftStepKey.REVIEW
    return {
        DraftStepKey.ORIENTATION: WizardStage.ORIENTATION,
        DraftStepKey.IDENTITY: WizardStage.PRONOUNS,
        DraftStepKey.LINKS: WizardStage.LINKS,
        DraftStepKey.THRONE: WizardStage.THRONE,
        DraftStepKey.REVIEW: WizardStage.REVIEW,
    }[current]


def _stage(draft: ProfileDraft) -> WizardStage:
    stage = draft.wizard_stage or _legacy_stage(draft)
    stages = wizard_stages(draft.governing_orientation, linked=_is_linked(draft))
    return stage if stage in stages else stages[-1]


def _stage_title(stage: WizardStage) -> str:
    return {
        WizardStage.ORIENTATION: "Orientation",
        WizardStage.PRONOUNS: "Pronouns",
        WizardStage.HONOURIFICS: "Titles and honourifics",
        WizardStage.SUBMISSIVE_LABELS: "Submissive labels",
        WizardStage.DM_STATUS: "DM status",
        WizardStage.BIO: "Bio",
        WizardStage.PROFILE_COLOR: "Profile colour",
        WizardStage.LINKS: "Links",
        WizardStage.THRONE: "Connect Throne",
        WizardStage.DETAILS: "Aliases and stats",
        WizardStage.REVIEW: "Review and publish",
    }[stage]


def _colour_name(value: int | None) -> str:
    if value is None:
        return "No colour"
    return next(
        (name for name, preset in PROFILE_COLOR_OPTIONS if preset == value),
        f"#{value:06X}",
    )


def _button(
    draft: ProfileDraft, label: str, action: str, style: discord.ButtonStyle
) -> discord.ui.Button:
    return discord.ui.Button(label=label, custom_id=wizard_custom_id(draft, action), style=style)


def _add_navigation(
    container: discord.ui.Container,
    draft: ProfileDraft,
    *,
    inherit: bool = False,
) -> None:
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    controls = [
        _button(draft, "Back", "back", discord.ButtonStyle.secondary),
        _button(draft, "Continue", "continue", discord.ButtonStyle.primary),
    ]
    if inherit and _is_linked(draft):
        controls.insert(
            0,
            _button(draft, "Use global setting", "use-global", discord.ButtonStyle.secondary),
        )
    container.add_item(
        discord.ui.ActionRow(*controls)
    )


def _review_preview(
    draft: ProfileDraft,
    presentation: MemberPresentation,
) -> discord.ui.Container:
    color = draft.document.profile_color
    container = discord.ui.Container(
        accent_color=None if color is None else discord.Color(color)
    )
    container.add_item(discord.ui.TextDisplay("-# Profile preview"))
    orientation = ORIENTATION_LABELS.get(draft.governing_orientation, "Not selected")
    status = (
        draft.document.dm_status.value.replace("_", " ").title()
        if draft.document.dm_status
        else "Use global setting"
    )
    container.add_item(
        _member_section(
            presentation,
            current_label=f"{orientation} · DMs: {status}",
            progress_label="Preview",
            scope_label=_colour_name(color),
        )
    )
    identity = (
        *draft.document.selections.pronouns,
        *draft.document.selections.honourifics,
        *draft.document.selections.submissive_labels,
    )
    summary = []
    if identity:
        summary.append(f"> **Identity:** {safe_text(', '.join(identity), limit=250)}")
    if draft.document.bio:
        summary.append(f"> {safe_text(draft.document.bio, limit=300)}")
    if draft.document.aliases:
        summary.append(
            f"> **Aliases:** {safe_text(', '.join(draft.document.aliases), limit=200)}"
        )
    summary.append(f"> **Links:** {len(draft.document.links)} saved")
    if draft.document.throne_creator_id:
        summary.append("> **Throne:** Connected")
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(discord.ui.TextDisplay("\n".join(summary)))
    return container


def profile_intro_view(draft: ProfileDraft) -> discord.ui.View:
    """Build the normal, durable DM introduction shown before Components V2."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Start",
            custom_id=wizard_custom_id(draft, "start"),
            style=discord.ButtonStyle.success,
        )
    )
    return view


def _member_section(
    presentation: MemberPresentation,
    *,
    current_label: str,
    progress_label: str,
    scope_label: str,
) -> discord.ui.Section | discord.ui.TextDisplay:
    title = f"### {safe_text(presentation.display_name, limit=80)}"
    metadata = (
        f"-# {safe_text(progress_label, limit=80)} · {safe_text(current_label, limit=80)}",
        f"-# Profile: {safe_text(scope_label, limit=80)} · progress saves automatically",
    )
    if presentation.avatar_url:
        return discord.ui.Section(
            discord.ui.TextDisplay(title),
            *(discord.ui.TextDisplay(row) for row in metadata),
            accessory=discord.ui.Thumbnail(
                presentation.avatar_url,
                description=f"{safe_text(presentation.display_name, limit=80)}'s avatar",
            ),
        )
    return discord.ui.TextDisplay("\n".join((title, *metadata)))


def profile_wizard_view(
    draft: ProfileDraft,
    *,
    presentation: MemberPresentation | None = None,
) -> discord.ui.LayoutView:
    """Render the sole editable V2 wizard message from the latest Worker state."""
    presentation = presentation or MemberPresentation("Bill member")
    current = _stage(draft)
    stages = wizard_stages(draft.governing_orientation, linked=_is_linked(draft))
    position = stages.index(current) + 1
    scope_label = (
        "Global"
        if draft.target_scope is DraftScope.GLOBAL
        else (
            "Server (linked)"
            if draft.server_mode is ServerProfileMode.LINKED
            else "Server (independent)"
        )
    )
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay("-# Bill Profile Setup"))
    container.add_item(
        _member_section(
            presentation,
            current_label=_stage_title(current),
            progress_label=f"Step {position} of {len(stages)}",
            scope_label=scope_label,
        )
    )
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    if current is WizardStage.ORIENTATION:
        container.add_item(
            discord.ui.TextDisplay(
                "Choose the orientation that best fits this profile. This only changes which "
                "relevant setup screens Bill shows next."
            )
        )
        container.add_item(discord.ui.ActionRow(OrientationSelect(draft)))
    elif current is WizardStage.PRONOUNS:
        container.add_item(
            discord.ui.TextDisplay(
                "Choose the pronouns shown on your profile. Your saved choice is selected when "
                "you return to this screen."
            )
        )
        container.add_item(
            discord.ui.ActionRow(IdentitySelect(draft, "pronouns", PRONOUNS, "Choose pronouns"))
        )
        _add_navigation(container, draft, inherit=True)
    elif current is WizardStage.HONOURIFICS:
        container.add_item(
            discord.ui.TextDisplay(
                "Choose any titles or honourifics you want displayed. Leave the menu empty if "
                "you do not use one."
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                IdentitySelect(draft, "honourifics", HONOURIFICS, "Choose titles")
            )
        )
        _add_navigation(container, draft, inherit=True)
    elif current is WizardStage.SUBMISSIVE_LABELS:
        container.add_item(
            discord.ui.TextDisplay(
                "Choose any submissive labels you want displayed. Leave the menu empty if none "
                "fit."
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                IdentitySelect(draft, "labels", SUBMISSIVE_LABELS, "Choose labels")
            )
        )
        _add_navigation(container, draft, inherit=True)
    elif current is WizardStage.DM_STATUS:
        explanation = "Choose how people should approach your DMs."
        if _is_linked(draft):
            explanation += " **Use global setting** keeps this server profile linked."
        container.add_item(discord.ui.TextDisplay(explanation))
        container.add_item(discord.ui.ActionRow(DmStatusSelect(draft)))
        _add_navigation(container, draft)
    elif current is WizardStage.BIO:
        saved = safe_text(draft.document.bio, limit=300) if draft.document.bio else "No bio saved"
        container.add_item(
            discord.ui.TextDisplay(
                "Add a short public bio, edit the saved one, or skip this optional screen.\n\n"
                f"> {saved}"
            )
        )
        bio_controls = [
            _button(
                draft,
                "Edit bio" if draft.document.bio else "Add bio",
                "bio",
                discord.ButtonStyle.primary,
            ),
            _button(draft, "Skip", "skip-bio", discord.ButtonStyle.secondary),
            _button(draft, "Back", "back", discord.ButtonStyle.secondary),
        ]
        if _is_linked(draft):
            bio_controls.insert(
                1,
                _button(
                    draft,
                    "Use global bio",
                    "use-global",
                    discord.ButtonStyle.secondary,
                ),
            )
        container.add_item(discord.ui.ActionRow(*bio_controls))
    elif current is WizardStage.PROFILE_COLOR:
        container.add_item(
            discord.ui.TextDisplay(
                "Choose the accent used on your published profile card. Setup stays neutral. "
                f"Current choice: **{_colour_name(draft.document.profile_color)}**."
            )
        )
        container.add_item(discord.ui.ActionRow(ProfileColorSelect(draft)))
        container.add_item(
            discord.ui.ActionRow(
                _button(draft, "Custom hex", "custom-color", discord.ButtonStyle.secondary),
                _button(draft, "Back", "back", discord.ButtonStyle.secondary),
                _button(draft, "Continue", "continue", discord.ButtonStyle.primary),
            )
        )
    elif current is WizardStage.LINKS:
        _, _, _, payment, _ = _caps(draft.governing_orientation)
        summary = "\n".join(
            f"> **{safe_text(link.public_label, limit=50)}** · {safe_text(link.platform, limit=40)}"
            for link in draft.document.links[:8]
        )
        container.add_item(
            discord.ui.TextDisplay(
                "Add one link manually or import a supported public page. Only enabled HTTPS "
                "links are published."
                + (f"\n\n**Saved links**\n{summary}" if summary else "\n\n> No links saved yet")
            )
        )
        links = [
            _button(draft, "Add social link", "link-social", discord.ButtonStyle.primary),
            _button(draft, "Import page", "import", discord.ButtonStyle.secondary),
            _button(draft, "Continue", "complete-links", discord.ButtonStyle.success),
            _button(draft, "Back", "back", discord.ButtonStyle.secondary),
        ]
        if payment:
            links.insert(
                1,
                _button(
                    draft,
                    "Add payment link",
                    "link-payment",
                    discord.ButtonStyle.primary,
                ),
            )
        if not draft.document.links:
            links.append(_button(draft, "Skip", "skip-links", discord.ButtonStyle.secondary))
        if (
            draft.target_scope is DraftScope.SERVER
            and draft.server_mode is ServerProfileMode.LINKED
        ):
            links.append(
                _button(draft, "Inherited visibility", "visibility", discord.ButtonStyle.secondary)
            )
        for index in range(0, len(links), 5):
            container.add_item(discord.ui.ActionRow(*links[index : index + 5]))
        if draft.document.links:
            container.add_item(discord.ui.ActionRow(LinkSelect(draft, payment=payment)))
    elif current is WizardStage.THRONE:
        connected = draft.document.throne_creator_id is not None
        verified = draft.wizard_substep == "verified"
        if verified:
            copy = (
                "Throne has confirmed the private webhook connection. You can continue or rotate "
                "the webhook if you need a new one."
            )
        elif connected:
            copy = (
                "Finish the connection in Throne: open your creator settings, find webhooks, "
                "paste and save the private URL Bill showed you, run **Test Webhook**, then "
                "return here and press **Check Connection**."
            )
        elif draft.wizard_substep == "confirm":
            copy = (
                "Confirm the resolved Throne handle shown below before Bill attaches it or "
                "issues a private webhook."
            )
        else:
            copy = (
                "Connect Throne through a guided private verification, reuse an owned creator, "
                "or skip for now."
            )
        container.add_item(
            discord.ui.TextDisplay(copy)
        )
        if connected:
            controls = [
                _button(draft, "Check Connection", "check-throne", discord.ButtonStyle.primary),
                _button(draft, "Rotate webhook", "rotate", discord.ButtonStyle.danger),
                _button(draft, "Skip for now", "skip-throne", discord.ButtonStyle.secondary),
                _button(draft, "Back", "back", discord.ButtonStyle.secondary),
            ]
            if verified:
                controls.insert(
                    0, _button(draft, "Continue", "continue", discord.ButtonStyle.success)
                )
        else:
            controls = [
                _button(draft, "Enter Throne profile", "throne", discord.ButtonStyle.primary),
                _button(draft, "Skip for now", "skip-throne", discord.ButtonStyle.secondary),
                _button(draft, "Back", "back", discord.ButtonStyle.secondary),
            ]
        container.add_item(discord.ui.ActionRow(*controls))
        if not connected and draft.throne_prefill and draft.throne_prefill.owned_creators:
            options = [
                discord.SelectOption(
                    label=safe_text(creator.handle, limit=80),
                    value=creator.id,
                    description="Already verified" if creator.id == (
                        draft.throne_prefill.existing_registration_creator_id
                    ) else "Owned Throne creator",
                )
                for creator in draft.throne_prefill.owned_creators[:25]
            ]
            container.add_item(discord.ui.ActionRow(ThroneCreatorSelect(draft, options)))
    elif current is WizardStage.DETAILS:
        _, _, aliases, _, stats = _caps(draft.governing_orientation)
        details = []
        if aliases:
            alias_summary = safe_text(", ".join(draft.document.aliases), limit=200) or "None"
            details.append(
                f"> **Aliases:** {alias_summary}"
            )
        if stats:
            details.append(
                "> **Public send stats:** "
                + ("Shown" if draft.document.public_send_stats else "Hidden")
            )
        container.add_item(
            discord.ui.TextDisplay(
                "Choose the optional details relevant to this orientation.\n\n"
                + "\n".join(details)
            )
        )
        if stats:
            container.add_item(discord.ui.ActionRow(StatsSelect(draft)))
        controls = []
        if aliases:
            controls.append(
                _button(draft, "Edit aliases", "aliases", discord.ButtonStyle.secondary)
            )
        if _is_linked(draft):
            controls.append(
                _button(
                    draft,
                    "Use global details",
                    "use-global",
                    discord.ButtonStyle.secondary,
                )
            )
        controls.extend(
            (
                _button(draft, "Back", "back", discord.ButtonStyle.secondary),
                _button(draft, "Continue", "continue", discord.ButtonStyle.primary),
            )
        )
        container.add_item(discord.ui.ActionRow(*controls))
    else:
        container.add_item(
            discord.ui.TextDisplay(
                "Review the compact preview below. Use an Edit control to revisit one section; "
                "nothing becomes public until you choose **Publish**. You can also change your "
                "DM status below, including restoring the global setting for a linked profile."
            )
        )
        container.add_item(discord.ui.ActionRow(DmStatusSelect(draft)))
        edits = [
            _button(draft, "Edit orientation", "edit-orientation", discord.ButtonStyle.secondary),
            _button(draft, "Edit identity", "edit-pronouns", discord.ButtonStyle.secondary),
            _button(draft, "Edit DMs", "edit-dm", discord.ButtonStyle.secondary),
            _button(draft, "Edit bio", "edit-bio", discord.ButtonStyle.secondary),
            _button(draft, "Edit colour", "edit-color", discord.ButtonStyle.secondary),
        ]
        container.add_item(discord.ui.ActionRow(*edits[:5]))
        more_edits = [
            _button(draft, "Edit titles", "edit-titles", discord.ButtonStyle.secondary),
            _button(draft, "Edit links", "edit-links", discord.ButtonStyle.secondary),
        ]
        if _caps(draft.governing_orientation)[3]:
            more_edits.append(
                _button(draft, "Edit Throne", "edit-throne", discord.ButtonStyle.secondary)
            )
        if _caps(draft.governing_orientation)[2] or _caps(draft.governing_orientation)[4]:
            more_edits.append(
                _button(draft, "Edit details", "edit-details", discord.ButtonStyle.secondary)
            )
        container.add_item(discord.ui.ActionRow(*more_edits))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        container.add_item(
            discord.ui.ActionRow(
                _button(draft, "Publish", "publish", discord.ButtonStyle.success),
                _button(draft, "Restart", "restart", discord.ButtonStyle.danger),
            )
        )
    view.add_item(container)
    if current is WizardStage.REVIEW:
        view.add_item(_review_preview(draft, presentation))
    return view


def _wizard_for(
    interaction: discord.Interaction[discord.Client],
    draft: ProfileDraft,
) -> discord.ui.LayoutView:
    return profile_wizard_view(draft, presentation=member_presentation(interaction.user))


def _adjacent_stage(
    draft: ProfileDraft,
    *,
    forward: bool,
) -> WizardStage:
    stages = wizard_stages(draft.governing_orientation, linked=_is_linked(draft))
    current = _stage(draft)
    if forward and draft.wizard_substep == "review":
        return WizardStage.REVIEW
    index = stages.index(current)
    offset = 1 if forward else -1
    return stages[max(0, min(len(stages) - 1, index + offset))]


async def _move_wizard(
    bot: BillBot,
    interaction: discord.Interaction[discord.Client],
    draft: ProfileDraft,
    stage: WizardStage,
    *,
    substep: str | None = None,
) -> ProfileDraft | None:
    try:
        return await bot.require_worker().set_draft_wizard_stage(
            draft.id,
            owner_user_id=interaction.user.id,
            expected_revision=draft.revision,
            stage=stage,
            substep=substep,
        )
    except WorkerAPIError as exc:
        await interaction.response.send_message(
            f"Bill could not move the profile wizard: {exc}",
            ephemeral=True,
        )
        return None


def _validate_continue(draft: ProfileDraft) -> None:
    current = _stage(draft)
    if current is WizardStage.PRONOUNS and not (
        draft.document.selections.pronouns
        or (_is_linked(draft) and "pronouns" not in draft.document.overridden_fields)
    ):
        raise ValueError("choose at least one pronoun")
    if current is WizardStage.DM_STATUS and not (
        draft.document.dm_status is not None
        or (_is_linked(draft) and "dm_status" not in draft.document.overridden_fields)
    ):
        raise ValueError("choose a DM status")
    if current is WizardStage.THRONE and draft.wizard_substep != "verified":
        raise ValueError("verify the Throne connection or choose Skip for now")


async def _load_draft(
    bot: BillBot,
    interaction: discord.Interaction[discord.Client],
    draft_id: str,
    owner: str,
    origin: str,
    revision: int,
) -> ProfileDraft | None:
    if str(interaction.user.id) != owner:
        await interaction.response.send_message(
            "That profile control belongs to someone else.", ephemeral=True
        )
        return None
    try:
        draft = await bot.require_worker().get_draft(draft_id, owner_user_id=interaction.user.id)
    except WorkerAPIError:
        await interaction.response.send_message(
            "That profile control is no longer available. Please use `/profile` to resume it.",
            ephemeral=True,
        )
        return None
    # DMs have no guild_id. Comparing the encoded guild with the durable draft
    # preserves origin authorization even after a bot restart.
    if (
        draft.owner_user_id != owner
        or draft.origin_guild_id != origin
        or (interaction.guild_id is not None and str(interaction.guild_id) != origin)
    ):
        await interaction.response.send_message(
            "That profile control belongs to a different profile session.", ephemeral=True
        )
        return None
    if draft.revision != revision or draft.status.value != "active":
        await interaction.response.send_message(
            "That profile control is stale. Please use the latest wizard message.", ephemeral=True
        )
        return None
    return draft


class OrientationSelect(discord.ui.Select):
    def __init__(self, draft: ProfileDraft) -> None:
        super().__init__(
            custom_id=wizard_custom_id(draft, "orientation"),
            placeholder="Choose an orientation",
            options=[
                discord.SelectOption(
                    label=label,
                    value=value.value,
                    default=draft.governing_orientation is value,
                )
                for value, label in ORIENTATION_LABELS.items()
            ],
        )


class IdentitySelect(discord.ui.Select):
    def __init__(
        self,
        draft: ProfileDraft,
        field: str,
        choices: tuple[str, ...],
        placeholder: str,
    ) -> None:
        if field == "pronouns":
            selected = set(draft.document.selections.pronouns)
        elif field == "honourifics":
            selected = set(draft.document.selections.honourifics)
        else:
            selected = set(draft.document.selections.submissive_labels)
        super().__init__(
            custom_id=wizard_custom_id(draft, field),
            placeholder=placeholder,
            min_values=1 if field == "pronouns" else 0,
            max_values=len(choices),
            options=[
                discord.SelectOption(label=choice, value=choice, default=choice in selected)
                for choice in choices
            ],
        )


class DmStatusSelect(discord.ui.Select):
    def __init__(self, draft: ProfileDraft) -> None:
        linked = (
            draft.target_scope is DraftScope.SERVER
            and draft.server_mode is ServerProfileMode.LINKED
        )
        overridden = set(draft.document.overridden_fields)
        inherited = linked and "dm_status" not in overridden
        options = [
            discord.SelectOption(
                label=label,
                value=status.value,
                description=description,
                default=(
                    draft.dm_status_selected
                    and not inherited
                    and draft.document.dm_status is status
                ),
            )
            for status, label, description in DM_STATUS_OPTIONS
        ]
        if linked:
            options.append(
                discord.SelectOption(
                    label="Use global setting",
                    value="inherit",
                    description="Follow your current global DM status",
                    default=draft.dm_status_selected and inherited,
                )
            )
        super().__init__(
            custom_id=wizard_custom_id(draft, "dm-status"),
            placeholder="Choose a DM status",
            min_values=1,
            max_values=1,
            options=options,
        )


class ProfileColorSelect(discord.ui.Select):
    def __init__(self, draft: ProfileDraft) -> None:
        linked = _is_linked(draft)
        overridden = set(draft.document.overridden_fields)
        inherited = linked and "profile_color" not in overridden
        options = [
            discord.SelectOption(
                label=name,
                value=f"{value:06x}",
                default=not inherited and draft.document.profile_color == value,
            )
            for name, value in PROFILE_COLOR_OPTIONS
        ]
        options.append(
            discord.SelectOption(
                label="No colour",
                value="none",
                description="Publish a neutral profile card",
                default=not inherited
                and draft.document.profile_color is None
                and (not linked or "profile_color" in overridden),
            )
        )
        if linked:
            options.append(
                discord.SelectOption(
                    label="Use global colour",
                    value="inherit",
                    description="Follow the global profile colour",
                    default=inherited,
                )
            )
        super().__init__(
            custom_id=wizard_custom_id(draft, "profile-color"),
            placeholder="Choose a profile colour",
            min_values=1,
            max_values=1,
            options=options,
        )


class StatsSelect(discord.ui.Select):
    def __init__(self, draft: ProfileDraft) -> None:
        linked = _is_linked(draft)
        overridden = set(draft.document.overridden_fields)
        inherited = linked and "public_send_stats" not in overridden
        options = [
            discord.SelectOption(
                label="Show send stats",
                value="show",
                default=not inherited and draft.document.public_send_stats,
            ),
            discord.SelectOption(
                label="Hide send stats",
                value="hide",
                default=not inherited and not draft.document.public_send_stats,
            ),
        ]
        if linked:
            options.append(
                discord.SelectOption(
                    label="Use global setting",
                    value="inherit",
                    default=inherited,
                )
            )
        super().__init__(
            custom_id=wizard_custom_id(draft, "stats"),
            placeholder="Choose public send stats",
            min_values=1,
            max_values=1,
            options=options,
        )


class LinkSelect(discord.ui.Select):
    def __init__(self, draft: ProfileDraft, *, payment: bool) -> None:
        options = [
            discord.SelectOption(
                label=safe_text(link.public_label, limit=70),
                value=link.id,
                description=link.link_type.value,
            )
            for link in draft.document.links[:25]
        ]
        super().__init__(
            custom_id=wizard_custom_id(draft, "link-select"),
            placeholder="Edit, remove, or prefer a link",
            options=options,
        )


class ThroneCreatorSelect(discord.ui.Select):
    def __init__(self, draft: ProfileDraft, options: list[discord.SelectOption]) -> None:
        super().__init__(
            custom_id=wizard_custom_id(draft, "creator-select"),
            placeholder="Use a saved Throne creator",
            options=options,
        )


class ProfileWizardDynamic(
    discord.ui.DynamicItem[discord.ui.Button],
    template=_PROFILE_WIZARD_BUTTON_TEMPLATE,
):
    """Persistent action dispatcher; only Worker state decides whether it is valid."""

    def __init__(
        self,
        item: discord.ui.Button,
        draft_id: str,
        owner: str,
        guild: str,
        revision: int,
        action: str,
    ) -> None:
        super().__init__(item)
        self.draft_id, self.owner, self.guild, self.revision, self.action = (
            draft_id,
            owner,
            guild,
            revision,
            action,
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[discord.Client],
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> ProfileWizardDynamic:
        return cls(
            item,
            decode_resource_id(match["draft"]),
            str(decode_uint(match["owner"])),
            str(decode_uint(match["guild"])),
            decode_uint(match["revision"]),
            match["action"],
        )

    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        draft = await _load_draft(
            bot, interaction, self.draft_id, self.owner, self.guild, self.revision
        )
        if draft is None:
            return
        message = interaction.message
        if self.action == "start":
            if message is None:
                await interaction.response.send_message(
                    "Please reopen your profile setup with `/profile`.", ephemeral=True
                )
                return
            await interaction.response.edit_message(
                content=None,
                view=_wizard_for(interaction, draft),
            )
            return
        if self.action == "publish":
            try:
                await bot.require_worker().publish_draft(
                    draft.id, owner_user_id=interaction.user.id, expected_revision=draft.revision
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not publish this profile: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(
                view=None, content="Your Bill profile is published."
            )
            return
        if self.action == "restart":
            await interaction.response.send_message(
                "Restart this private draft? This replaces unsaved progress.",
                view=RestartConfirmView(draft),
                ephemeral=True,
            )
            return
        if message is None:
            await interaction.response.send_message(
                "Please reopen your wizard with `/profile`.", ephemeral=True
            )
            return
        if self.action in {"continue", "back"}:
            try:
                if self.action == "continue":
                    _validate_continue(draft)
                working = draft
                if self.action == "continue" and _stage(draft) in {
                    WizardStage.PROFILE_COLOR,
                    WizardStage.DETAILS,
                    WizardStage.THRONE,
                }:
                    step = (
                        DraftStepKey.THRONE
                        if _stage(draft) is WizardStage.THRONE
                        else DraftStepKey.IDENTITY
                    )
                    values = (
                        {
                            "throne_creator_id": draft.document.throne_creator_id,
                            "preferred_payment_link_id": (
                                draft.document.preferred_payment_link_id
                            ),
                        }
                        if step is DraftStepKey.THRONE
                        else _identity_step_values(draft, complete=True)
                    )
                    working = await bot.require_worker().update_draft_step(
                        draft.id,
                        step=step,
                        owner_user_id=interaction.user.id,
                        expected_revision=draft.revision,
                        values=values,
                    )
                target = _adjacent_stage(working, forward=self.action == "continue")
                updated = await bot.require_worker().set_draft_wizard_stage(
                    working.id,
                    owner_user_id=interaction.user.id,
                    expected_revision=working.revision,
                    stage=target,
                )
            except (ValueError, WorkerAPIError) as exc:
                await interaction.response.send_message(
                    f"Bill could not continue: {exc}",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        if self.action == "use-global":
            try:
                values = _inherit_current_values(draft)
                saved = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.IDENTITY,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values=values,
                )
                if _stage(draft) is WizardStage.BIO:
                    updated = await bot.require_worker().set_draft_wizard_stage(
                        saved.id,
                        owner_user_id=interaction.user.id,
                        expected_revision=saved.revision,
                        stage=_adjacent_stage(saved, forward=True),
                    )
                else:
                    updated = saved
            except (ValueError, WorkerAPIError) as exc:
                await interaction.response.send_message(
                    f"Bill could not restore the global setting: {exc}",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        edit_stages = {
            "edit-orientation": WizardStage.ORIENTATION,
            "edit-pronouns": WizardStage.PRONOUNS,
            "edit-titles": (
                WizardStage.HONOURIFICS
                if _caps(draft.governing_orientation)[0]
                else WizardStage.SUBMISSIVE_LABELS
            ),
            "edit-dm": WizardStage.DM_STATUS,
            "edit-bio": WizardStage.BIO,
            "edit-color": WizardStage.PROFILE_COLOR,
            "edit-links": WizardStage.LINKS,
            "edit-throne": WizardStage.THRONE,
            "edit-details": WizardStage.DETAILS,
        }
        if self.action in edit_stages:
            updated = await _move_wizard(
                bot,
                interaction,
                draft,
                edit_stages[self.action],
                substep="review",
            )
            if updated is not None:
                await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        if self.action == "bio":
            await interaction.response.send_modal(BioModal(draft, message))
            return
        if self.action == "aliases":
            await interaction.response.send_modal(AliasModal(draft, message))
            return
        if self.action == "custom-color":
            await interaction.response.send_modal(ProfileColorModal(draft, message))
            return
        if self.action == "skip-bio":
            updated = await _move_wizard(
                bot,
                interaction,
                draft,
                _adjacent_stage(draft, forward=True),
            )
            if updated is not None:
                await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        if self.action in {"link-social", "link-payment"}:
            await interaction.response.send_modal(
                LinkModal(
                    draft,
                    message,
                    link_type=(
                        LinkType.PAYMENT
                        if self.action == "link-payment"
                        else LinkType.SOCIAL
                    ),
                )
            )
            return
        if self.action == "import":
            await interaction.response.send_modal(LinkImportModal(draft, message))
            return
        if self.action == "visibility":
            try:
                lookup = await bot.require_worker().get_profile(
                    guild_id=bot.settings.home_guild_id,
                    user_id=interaction.user.id,
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not load inherited links: {exc}", ephemeral=True
                )
                return
            if lookup.profile is None or not lookup.profile.links:
                await interaction.response.send_message(
                    "Your global profile has no links to configure here.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                "Choose global links to hide in this server.",
                view=InheritedLinkVisibilityView(draft, lookup.profile.links, message),
                ephemeral=True,
            )
            return
        if self.action in {"complete-links", "skip-links"}:
            try:
                saved = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.LINKS,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values=_links_step_values(draft),
                )
                updated = await bot.require_worker().set_draft_wizard_stage(
                    saved.id,
                    owner_user_id=interaction.user.id,
                    expected_revision=saved.revision,
                    stage=_adjacent_stage(saved, forward=True),
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not save links: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        if self.action == "throne":
            await interaction.response.send_modal(ThroneModal(draft, message))
            return
        if self.action == "skip-throne":
            try:
                skipped = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.THRONE,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values={
                        "throne_creator_id": None,
                        "preferred_payment_link_id": draft.document.preferred_payment_link_id,
                    },
                )
                updated = await bot.require_worker().set_draft_wizard_stage(
                    skipped.id,
                    owner_user_id=interaction.user.id,
                    expected_revision=skipped.revision,
                    stage=_adjacent_stage(skipped, forward=True),
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not skip Throne: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        if self.action == "check-throne":
            try:
                status = await bot.require_worker().get_throne_status(
                    draft.id,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                )
                if not status.verified:
                    await interaction.response.send_message(
                        "Throne has not confirmed the connection yet. Check that you saved the "
                        "private webhook URL, run **Test Webhook** in Throne, then try "
                        "**Check Connection** again.",
                        ephemeral=True,
                    )
                    return
                updated = await bot.require_worker().set_draft_wizard_stage(
                    draft.id,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    stage=WizardStage.THRONE,
                    substep="verified",
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not check the Throne connection: {exc}",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        if self.action == "rotate":
            try:
                rotated = await bot.require_worker().rotate_throne(
                    draft.id, owner_user_id=interaction.user.id, expected_revision=draft.revision
                )
                updated = await bot.require_worker().set_draft_wizard_stage(
                    rotated.draft.id,
                    owner_user_id=interaction.user.id,
                    expected_revision=rotated.draft.revision,
                    stage=WizardStage.THRONE,
                    substep="awaiting_verification",
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not rotate that webhook: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            if rotated.webhook_url:
                await interaction.followup.send(
                    _throne_webhook_instructions(rotated.webhook_url),
                    ephemeral=True,
                )
            return
        await interaction.response.send_message(
            "That action is no longer available. Use the latest wizard.", ephemeral=True
        )


class _ProfileSelectDynamic(
    discord.ui.DynamicItem[discord.ui.Select],
    template=_PROFILE_WIZARD_SELECT_TEMPLATE,
):
    def __init__(
        self,
        item: discord.ui.Select,
        draft_id: str,
        owner: str,
        guild: str,
        revision: int,
        action: str,
    ) -> None:
        super().__init__(item)
        self.draft_id, self.owner, self.guild, self.revision, self.action = (
            draft_id,
            owner,
            guild,
            revision,
            action,
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[discord.Client],
        item: discord.ui.Select,
        match: re.Match[str],
        /,
    ) -> _ProfileSelectDynamic:
        return cls(
            item,
            decode_resource_id(match["draft"]),
            str(decode_uint(match["owner"])),
            str(decode_uint(match["guild"])),
            decode_uint(match["revision"]),
            match["action"],
        )

    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        draft = await _load_draft(
            bot, interaction, self.draft_id, self.owner, self.guild, self.revision
        )
        if draft is None:
            return
        if not self.item.values and self.action not in {"honourifics", "labels"}:
            await interaction.response.send_message(
                "Choose an option before continuing.",
                ephemeral=True,
            )
            return
        if self.action == "orientation":
            try:
                saved = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.ORIENTATION,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values={"orientation": Orientation(self.item.values[0]).value},
                )
                updated = await bot.require_worker().set_draft_wizard_stage(
                    saved.id,
                    owner_user_id=interaction.user.id,
                    expected_revision=saved.revision,
                    stage=WizardStage.PRONOUNS,
                )
            except (ValueError, WorkerAPIError) as exc:
                await interaction.response.send_message(
                    f"Bill could not save that orientation: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        if self.action in {
            "pronouns",
            "honourifics",
            "labels",
            "dm-status",
            "profile-color",
            "stats",
        }:
            try:
                updated = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.IDENTITY,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values=_partial_identity_values(
                        draft,
                        self.action,
                        tuple(self.item.values),
                    ),
                )
            except (ValueError, WorkerAPIError) as exc:
                await interaction.response.send_message(
                    f"Bill could not save that identity selection: {exc}",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(view=_wizard_for(interaction, updated))
            return
        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "Please reopen your wizard with `/profile`.", ephemeral=True
            )
            return
        if self.action == "link-select":
            link = next(
                (item for item in draft.document.links if item.id == self.item.values[0]), None
            )
            if link is None:
                await interaction.response.send_message(
                    "That link no longer exists.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                "Manage this link.", view=LinkManagerView(draft, link.id, message), ephemeral=True
            )
            return
        creator_id = self.item.values[0]
        try:
            attached = await bot.require_worker().attach_throne(
                draft.id,
                owner_user_id=interaction.user.id,
                expected_revision=draft.revision,
                existing_creator_id=creator_id,
            )
            updated = await bot.require_worker().update_draft_step(
                attached.draft.id,
                step=DraftStepKey.THRONE,
                owner_user_id=interaction.user.id,
                expected_revision=attached.draft.revision,
                values={
                    "throne_creator_id": attached.draft.document.throne_creator_id,
                    "preferred_payment_link_id": attached.draft.document.preferred_payment_link_id,
                },
            )
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not connect that creator: {exc}", ephemeral=True
            )
            return
        await interaction.response.edit_message(view=_wizard_for(interaction, updated))
        if attached.webhook_url:
            await interaction.followup.send(
                "Your private Throne webhook URL (save it now):\n"
                f"```text\n{attached.webhook_url}\n```",
                ephemeral=True,
            )


ProfileSelectDynamic = _ProfileSelectDynamic


def _partial_identity_values(
    draft: ProfileDraft,
    field: str,
    selected: tuple[str, ...],
) -> dict[str, object]:
    linked = (
        draft.target_scope is DraftScope.SERVER and draft.server_mode is ServerProfileMode.LINKED
    )
    overrides = set(draft.document.overridden_fields)
    honourific_available, label_available, aliases_available, _, stats_available = _caps(
        draft.governing_orientation
    )
    if not honourific_available:
        overrides.discard("honourifics")
    if not label_available:
        overrides.discard("submissive_labels")
    if not aliases_available:
        overrides.discard("aliases")
    if not stats_available:
        overrides.discard("public_send_stats")
    status = draft.document.dm_status.value if draft.document.dm_status else None
    profile_color = draft.document.profile_color
    public_send_stats = draft.document.public_send_stats
    if field == "dm-status":
        if len(selected) != 1:
            raise ValueError("choose one DM status")
        if selected[0] == "inherit":
            if not linked:
                raise ValueError("only linked profiles can use the global DM setting")
            status = None
            overrides.discard("dm_status")
        else:
            try:
                status = DmStatus(selected[0]).value
            except ValueError as exc:
                raise ValueError("choose a valid DM status") from exc
            if linked:
                overrides.add("dm_status")
    elif field in {"pronouns", "honourifics", "labels"}:
        if linked:
            overrides.add(
                {
                    "pronouns": "pronouns",
                    "honourifics": "honourifics",
                    "labels": "submissive_labels",
                }[field]
            )
    elif field == "profile-color":
        if len(selected) != 1:
            raise ValueError("choose one profile colour")
        choice = selected[0]
        if choice == "inherit":
            if not linked:
                raise ValueError("only linked profiles can inherit a profile colour")
            profile_color = None
            overrides.discard("profile_color")
        elif choice == "none":
            profile_color = None
            if linked:
                overrides.add("profile_color")
        else:
            try:
                profile_color = int(choice, 16)
            except ValueError as exc:
                raise ValueError("choose a valid profile colour") from exc
            if not 0 <= profile_color <= 0xFFFFFF:
                raise ValueError("choose a valid profile colour")
            if linked:
                overrides.add("profile_color")
    elif field == "stats":
        if len(selected) != 1:
            raise ValueError("choose one send-stat setting")
        choice = selected[0]
        if choice == "inherit":
            if not linked:
                raise ValueError("only linked profiles can inherit send stats")
            overrides.discard("public_send_stats")
        elif choice in {"show", "hide"}:
            public_send_stats = choice == "show"
            if linked:
                overrides.add("public_send_stats")
        else:
            raise ValueError("choose a valid send-stat setting")
    else:
        raise ValueError("choose a valid identity field")
    pronouns = selected if field == "pronouns" else draft.document.selections.pronouns
    honourifics = selected if field == "honourifics" else draft.document.selections.honourifics
    labels = selected if field == "labels" else draft.document.selections.submissive_labels
    values: dict[str, object] = {
        "pronouns": list(pronouns),
        "honourifics": list(honourifics),
        "submissive_labels": list(labels),
        "dm_status": status,
        "bio": draft.document.bio,
        "public_send_stats": public_send_stats,
        "aliases": list(draft.document.aliases),
        "profile_color": profile_color,
        "complete": False,
    }
    if field == "dm-status":
        values["dm_status_selected"] = True
    if linked:
        values["overrides"] = sorted(overrides)
    return values


def _identity_step_values(
    draft: ProfileDraft,
    *,
    complete: bool,
) -> dict[str, object]:
    values: dict[str, object] = {
        "pronouns": list(draft.document.selections.pronouns),
        "honourifics": list(draft.document.selections.honourifics),
        "submissive_labels": list(draft.document.selections.submissive_labels),
        "dm_status": draft.document.dm_status.value if draft.document.dm_status else None,
        "bio": draft.document.bio,
        "public_send_stats": draft.document.public_send_stats,
        "aliases": list(draft.document.aliases),
        "profile_color": draft.document.profile_color,
        "complete": complete,
    }
    if _is_linked(draft):
        values["overrides"] = list(draft.document.overridden_fields)
    return values


def _inherit_current_values(draft: ProfileDraft) -> dict[str, object]:
    if not _is_linked(draft):
        raise ValueError("only linked server profiles can use a global setting")
    values = _identity_step_values(draft, complete=False)
    overrides = set(draft.document.overridden_fields)
    stage = _stage(draft)
    if stage is WizardStage.PRONOUNS:
        values["pronouns"] = []
        overrides.discard("pronouns")
    elif stage is WizardStage.HONOURIFICS:
        values["honourifics"] = []
        overrides.discard("honourifics")
    elif stage is WizardStage.SUBMISSIVE_LABELS:
        values["submissive_labels"] = []
        overrides.discard("submissive_labels")
    elif stage is WizardStage.BIO:
        values["bio"] = None
        overrides.discard("bio")
    elif stage is WizardStage.DETAILS:
        values["aliases"] = []
        values["public_send_stats"] = False
        overrides.discard("aliases")
        overrides.discard("public_send_stats")
    else:
        raise ValueError("this screen has its own global-setting choice")
    values["overrides"] = sorted(overrides)
    return values


def _links_step_values(
    draft: ProfileDraft, *, hidden_inherited_link_ids: Iterable[str] | None = None
) -> dict[str, object]:
    links = [
        {
            "id": link.id,
            "platform": link.platform,
            "public_label": link.public_label,
            "username": link.username,
            "normalized_url": link.normalized_url,
            "link_type": link.link_type.value,
            "enabled": link.enabled,
        }
        for link in draft.document.links
    ]
    if draft.target_scope is DraftScope.SERVER and draft.server_mode is ServerProfileMode.LINKED:
        return {
            "local_links": links,
            "hidden_inherited_link_ids": list(
                draft.document.hidden_inherited_link_ids
                if hidden_inherited_link_ids is None
                else hidden_inherited_link_ids
            ),
            "preferred_payment_link_id": draft.document.preferred_payment_link_id,
        }
    return {"links": links}


class InheritedLinkSelect(discord.ui.Select):
    def __init__(
        self,
        owner: InheritedLinkVisibilityView,
        links: tuple[ProfileLink, ...],
    ) -> None:
        self.owner = owner
        hidden = set(owner.draft.document.hidden_inherited_link_ids)
        super().__init__(
            placeholder="Select inherited links to hide",
            min_values=0,
            max_values=len(links),
            options=[
                discord.SelectOption(
                    label=safe_text(link.public_label, limit=80),
                    value=link.id,
                    default=link.id in hidden,
                )
                for link in links
            ],
        )

    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self.owner.save(interaction, tuple(self.values))


class InheritedLinkVisibilityView(discord.ui.View):
    """Short-lived private editor for sparse linked-profile visibility overrides."""

    def __init__(
        self,
        draft: ProfileDraft,
        links: tuple[ProfileLink, ...],
        message: discord.Message,
    ) -> None:
        super().__init__(timeout=180)
        self.draft, self.message = draft, message
        self.add_item(InheritedLinkSelect(self, links[:12]))

    async def interaction_check(self, interaction: discord.Interaction[discord.Client]) -> bool:
        if str(interaction.user.id) == self.draft.owner_user_id:
            return True
        await interaction.response.send_message(
            "Only the profile owner can change inherited links.", ephemeral=True
        )
        return False

    async def save(
        self,
        interaction: discord.Interaction[discord.Client],
        hidden_ids: tuple[str, ...],
    ) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            updated = await bot.require_worker().update_draft_step(
                self.draft.id,
                step=DraftStepKey.LINKS,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                values=_links_step_values(
                    self.draft,
                    hidden_inherited_link_ids=hidden_ids,
                ),
            )
        except WorkerAPIError as exc:
            await interaction.response.edit_message(
                content=f"Bill could not save inherited visibility: {exc}",
                view=None,
            )
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        await interaction.response.edit_message(
            content=f"Hidden {len(hidden_ids)} inherited link(s) in this server.",
            view=None,
        )

    @discord.ui.button(label="Keep all inherited links", style=discord.ButtonStyle.success)
    async def keep_all(
        self,
        interaction: discord.Interaction[discord.Client],
        _: discord.ui.Button,
    ) -> None:
        await self.save(interaction, ())


def _throne_webhook_instructions(webhook_url: str) -> str:
    return (
        "Your private Throne webhook URL is shown once below. Do not share it.\n\n"
        "1. Open your Throne creator settings.\n"
        "2. Find the webhooks section.\n"
        "3. Paste this URL and save it.\n"
        "4. Run **Test Webhook** in Throne.\n"
        "5. Return to Bill and press **Check Connection**.\n"
        f"```text\n{webhook_url}\n```"
    )


class BioModal(discord.ui.Modal, title="Profile bio"):
    def __init__(self, draft: ProfileDraft, message: discord.Message) -> None:
        super().__init__()
        self.draft, self.message = draft, message
        self.bio = discord.ui.TextInput(
            label="Public bio",
            default=draft.document.bio or "",
            required=False,
            max_length=300,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.bio)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = cast("BillBot", interaction.client)
        overrides = set(self.draft.document.overridden_fields)
        value = self.bio.value.strip() or None
        if _is_linked(self.draft):
            overrides.add("bio")
        values = _identity_step_values(self.draft, complete=False)
        values["bio"] = value
        if _is_linked(self.draft):
            values["overrides"] = sorted(overrides)
        try:
            saved = await bot.require_worker().update_draft_step(
                self.draft.id,
                step=DraftStepKey.IDENTITY,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                values=values,
            )
            updated = await bot.require_worker().set_draft_wizard_stage(
                saved.id,
                owner_user_id=interaction.user.id,
                expected_revision=saved.revision,
                stage=_adjacent_stage(saved, forward=True),
            )
        except WorkerAPIError as exc:
            await interaction.followup.send(f"Bill could not save that bio: {exc}", ephemeral=True)
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        await interaction.followup.send("Bio saved.", ephemeral=True)


class AliasModal(discord.ui.Modal, title="Profile aliases"):
    def __init__(self, draft: ProfileDraft, message: discord.Message) -> None:
        super().__init__()
        self.draft, self.message = draft, message
        self.aliases = discord.ui.TextInput(
            label="Aliases, one per line",
            default="\n".join(draft.document.aliases),
            required=False,
            max_length=300,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.aliases)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        await interaction.response.defer(ephemeral=True)
        aliases = tuple(
            dict.fromkeys(
                line.strip()
                for line in self.aliases.value.splitlines()
                if line.strip()
            )
        )
        values = _identity_step_values(self.draft, complete=False)
        values["aliases"] = list(aliases)
        if _is_linked(self.draft):
            overrides = set(self.draft.document.overridden_fields)
            overrides.add("aliases")
            values["overrides"] = sorted(overrides)
        bot = cast("BillBot", interaction.client)
        try:
            updated = await bot.require_worker().update_draft_step(
                self.draft.id,
                step=DraftStepKey.IDENTITY,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                values=values,
            )
        except WorkerAPIError as exc:
            await interaction.followup.send(
                f"Bill could not save those aliases: {exc}",
                ephemeral=True,
            )
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        await interaction.followup.send("Aliases saved.", ephemeral=True)


class ProfileColorModal(discord.ui.Modal, title="Custom profile colour"):
    def __init__(self, draft: ProfileDraft, message: discord.Message) -> None:
        super().__init__()
        self.draft, self.message = draft, message
        default = (
            f"#{draft.document.profile_color:06X}"
            if draft.document.profile_color is not None
            else ""
        )
        self.color = discord.ui.TextInput(
            label="Hex colour (#RRGGBB or RRGGBB)",
            default=default,
            min_length=6,
            max_length=7,
        )
        self.add_item(self.color)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        value = self.color.value.strip()
        if re.fullmatch(r"#?[0-9A-Fa-f]{6}", value) is None:
            await interaction.response.send_message(
                "Enter exactly six hexadecimal digits, with an optional leading `#`.",
                ephemeral=True,
            )
            return
        color = int(value.removeprefix("#"), 16)
        values = _identity_step_values(self.draft, complete=False)
        values["profile_color"] = color
        if _is_linked(self.draft):
            overrides = set(self.draft.document.overridden_fields)
            overrides.add("profile_color")
            values["overrides"] = sorted(overrides)
        await interaction.response.defer(ephemeral=True)
        bot = cast("BillBot", interaction.client)
        try:
            updated = await bot.require_worker().update_draft_step(
                self.draft.id,
                step=DraftStepKey.IDENTITY,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                values=values,
            )
        except WorkerAPIError as exc:
            await interaction.followup.send(
                f"Bill could not save that colour: {exc}",
                ephemeral=True,
            )
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        await interaction.followup.send(f"Saved custom colour **#{color:06X}**.", ephemeral=True)


class LinkModal(discord.ui.Modal, title="Add a profile link"):
    def __init__(
        self,
        draft: ProfileDraft,
        message: discord.Message,
        *,
        link_type: LinkType,
        link_id: str | None = None,
    ) -> None:
        super().__init__()
        self.draft, self.message, self.link_id, self.link_type = (
            draft,
            message,
            link_id,
            link_type,
        )
        link = next((item for item in draft.document.links if item.id == link_id), None)
        self.label = discord.ui.TextInput(
            label="Public label", default=link.public_label if link else "", max_length=40
        )
        self.url = discord.ui.TextInput(
            label="HTTPS URL", default=link.normalized_url if link else "", max_length=500
        )
        self.platform = discord.ui.TextInput(
            label="Platform (optional)",
            default=link.platform if link else "",
            required=False,
            max_length=80,
        )
        self.add_item(self.label)
        self.add_item(self.url)
        self.add_item(self.platform)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = cast("BillBot", interaction.client)
        try:
            common = {
                "owner_user_id": interaction.user.id,
                "expected_revision": self.draft.revision,
                "public_label": self.label.value,
                "normalized_url": self.url.value,
                "link_type": self.link_type,
                "platform": self.platform.value or None,
            }
            updated = await (
                bot.require_worker().edit_link(self.draft.id, self.link_id, **common)
                if self.link_id
                else bot.require_worker().add_link(self.draft.id, **common)
            )
        except (ValueError, WorkerAPIError) as exc:
            await interaction.followup.send(
                f"Bill could not save that link: {exc}", ephemeral=True
            )
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        await interaction.followup.send(
            "Link saved. Choose **Continue** when your links are ready.", ephemeral=True
        )


class LinkImportModal(discord.ui.Modal, title="Import a link page"):
    def __init__(self, draft: ProfileDraft, message: discord.Message) -> None:
        super().__init__()
        self.draft, self.message = draft, message
        self.url = discord.ui.TextInput(
            label="HTTPS Linktree, AllMyLinks, Beacons, or page URL", max_length=500
        )
        self.add_item(self.url)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = cast("BillBot", interaction.client)
        try:
            result = await bot.require_worker().create_link_import(
                self.draft.id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                source_url=self.url.value,
            )
        except WorkerAPIError as exc:
            await interaction.followup.send(
                f"Bill could not import that page: {exc}", ephemeral=True
            )
            return
        await self.message.edit(view=_wizard_for(interaction, result.draft))
        labels = (
            ", ".join(
                safe_text(candidate.public_label, limit=40)
                for candidate in result.link_import.candidates
            )
            or "No public links found"
        )
        await interaction.followup.send(
            f"Imported candidates: {labels}",
            view=ImportConfirmView(
                result.draft,
                result.link_import.id,
                tuple(candidate.id for candidate in result.link_import.candidates),
                self.message,
            ),
            ephemeral=True,
        )


class ThroneModal(discord.ui.Modal, title="Connect Throne"):
    def __init__(self, draft: ProfileDraft, message: discord.Message) -> None:
        super().__init__()
        self.draft, self.message = draft, message
        self.throne = discord.ui.TextInput(label="Throne username or profile URL", max_length=200)
        self.add_item(self.throne)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            attached = await bot.require_worker().attach_throne(
                self.draft.id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                throne_input=self.throne.value,
            )
            updated = await bot.require_worker().update_draft_step(
                attached.draft.id,
                step=DraftStepKey.THRONE,
                owner_user_id=interaction.user.id,
                expected_revision=attached.draft.revision,
                values={
                    "throne_creator_id": attached.draft.document.throne_creator_id,
                    "preferred_payment_link_id": attached.draft.document.preferred_payment_link_id,
                },
            )
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not connect that Throne account: {exc}", ephemeral=True
            )
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        text = "Throne connected."
        if attached.webhook_url:
            text += (
                f" Your private webhook URL (save it now):\n```text\n{attached.webhook_url}\n```"
            )
        await interaction.response.send_message(text, ephemeral=True)


class ImportConfirmView(discord.ui.View):
    def __init__(
        self,
        draft: ProfileDraft,
        import_id: str,
        candidate_ids: tuple[str, ...],
        message: discord.Message,
    ) -> None:
        super().__init__(timeout=300)
        self.draft, self.import_id, self.candidate_ids, self.message = (
            draft,
            import_id,
            candidate_ids,
            message,
        )

    @discord.ui.button(label="Looks Good!", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            result = await bot.require_worker().confirm_link_import(
                self.draft.id,
                self.import_id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                candidate_ids=self.candidate_ids,
            )
        except WorkerAPIError as exc:
            await interaction.response.edit_message(
                content=f"Bill could not confirm these links: {exc}", view=None
            )
            return
        await interaction.response.edit_message(
            content=f"Added {result.added_link_count} link(s).", view=None
        )
        await self.message.edit(view=_wizard_for(interaction, result.draft))

    @discord.ui.button(label="Not Quite", style=discord.ButtonStyle.secondary)
    async def manual(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="No links were imported. Return to the wizard to add links manually.", view=None
        )


class LinkManagerView(discord.ui.View):
    def __init__(self, draft: ProfileDraft, link_id: str, message: discord.Message) -> None:
        super().__init__(timeout=180)
        self.draft, self.link_id, self.message = draft, link_id, message

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
    async def edit(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        link = next(item for item in self.draft.document.links if item.id == self.link_id)
        await interaction.response.send_modal(
            LinkModal(
                self.draft,
                self.message,
                link_type=link.link_type,
                link_id=self.link_id,
            )
        )

    @discord.ui.button(label="Remove", style=discord.ButtonStyle.danger)
    async def remove(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            updated = await bot.require_worker().delete_link(
                self.draft.id,
                self.link_id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
            )
        except WorkerAPIError as exc:
            await interaction.response.edit_message(
                content=f"Bill could not remove that link: {exc}", view=None
            )
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        await interaction.response.edit_message(content="Link removed.", view=None)

    @discord.ui.button(label="Prefer payment", style=discord.ButtonStyle.secondary)
    async def preferred(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        link = next((item for item in self.draft.document.links if item.id == self.link_id), None)
        if link is None or link.link_type is not LinkType.PAYMENT:
            await interaction.response.send_message(
                "Only a payment link can be preferred.", ephemeral=True
            )
            return
        bot = cast("BillBot", interaction.client)
        try:
            updated = await bot.require_worker().edit_link(
                self.draft.id,
                link.id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                public_label=link.public_label,
                normalized_url=link.normalized_url,
                link_type=link.link_type,
                platform=link.platform,
                username=link.username,
                enabled=link.enabled,
                preferred=True,
            )
        except WorkerAPIError as exc:
            await interaction.response.edit_message(
                content=f"Bill could not prefer that link: {exc}", view=None
            )
            return
        await self.message.edit(view=_wizard_for(interaction, updated))
        await interaction.response.edit_message(
            content="Preferred payment link updated.", view=None
        )


class RestartConfirmView(discord.ui.View):
    def __init__(self, draft: ProfileDraft) -> None:
        super().__init__(timeout=60)
        self.draft = draft

    @discord.ui.button(label="Restart draft", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            draft = await bot.require_worker().restart_draft(
                self.draft.id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
            )
        except WorkerAPIError as exc:
            await interaction.response.edit_message(
                content=f"Bill could not restart this draft: {exc}", view=None
            )
            return
        await interaction.response.edit_message(
            content="Draft restarted. Use `/profile` to reopen its private wizard.", view=None
        )
        try:
            await interaction.user.create_dm()
            await interaction.user.dm_channel.send(view=_wizard_for(interaction, draft))  # type: ignore[union-attr]
        except discord.Forbidden:
            return

    @discord.ui.button(label="Keep draft", style=discord.ButtonStyle.success)
    async def cancel(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(content="Restart cancelled.", view=None)
