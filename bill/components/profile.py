"""Durable private profile wizard components.

All state lives in Worker drafts.  Dynamic IDs carry enough routing context to
reject replayed controls after a restart, but every callback still reloads the
draft because an ID is not an authorization decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
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


def safe_text(value: str, *, limit: int = 300) -> str:
    """Escape user-provided strings before V2 rendering and prevent pings."""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))[:limit]


def wizard_custom_id(draft: ProfileDraft, action: str) -> str:
    """Build a <=100-character persistent ID bound to all durable auth context."""
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


def _csv(value: str, allowed: Iterable[str], field: str) -> list[str]:
    lookup = {entry.casefold(): entry for entry in allowed}
    result: list[str] = []
    for entry in (item.strip() for item in value.split(",")):
        if not entry:
            continue
        normalized = lookup.get(entry.casefold())
        if normalized is None:
            raise ValueError(f"{field} has an unrecognized value: {entry}")
        if normalized not in result:
            result.append(normalized)
    return result


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


def _button(
    draft: ProfileDraft, label: str, action: str, style: discord.ButtonStyle
) -> discord.ui.Button:
    return discord.ui.Button(label=label, custom_id=wizard_custom_id(draft, action), style=style)


def profile_wizard_view(draft: ProfileDraft) -> discord.ui.LayoutView:
    """Render the sole editable V2 wizard message from the latest Worker state."""
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=discord.Color.green())
    container.add_item(discord.ui.TextDisplay("## Build your Bill profile"))
    for step in draft.steps:
        if step.status == "completed":
            container.add_item(
                discord.ui.TextDisplay(
                    f"-# **{step.key.value.title()}**: {_summary(draft, step.key)} (Complete)"
                )
            )
    current = draft.next_step or draft.current_step
    if current is DraftStepKey.ORIENTATION:
        container.add_item(discord.ui.TextDisplay("### 1. Choose your orientation"))
        container.add_item(discord.ui.ActionRow(OrientationSelect(draft)))
    elif current is DraftStepKey.IDENTITY:
        container.add_item(
            discord.ui.TextDisplay(
                "### Identity\nSet fixed selections, DM status, bio, aliases, and public stats "
                "preference."
            )
        )
        container.add_item(
            discord.ui.ActionRow(IdentitySelect(draft, "pronouns", PRONOUNS, "Choose pronouns"))
        )
        honourifics, labels, _, _, _ = _caps(draft.governing_orientation)
        if honourifics:
            container.add_item(
                discord.ui.ActionRow(
                    IdentitySelect(
                        draft,
                        "honourifics",
                        HONOURIFICS,
                        "Choose honourifics",
                    )
                )
            )
        if labels:
            container.add_item(
                discord.ui.ActionRow(
                    IdentitySelect(
                        draft,
                        "labels",
                        SUBMISSIVE_LABELS,
                        "Choose submissive labels",
                    )
                )
            )
        container.add_item(
            discord.ui.ActionRow(
                _button(
                    draft,
                    "Save DM status, bio & aliases",
                    "identity",
                    discord.ButtonStyle.primary,
                )
            )
        )
    elif current is DraftStepKey.LINKS:
        _, _, _, payment, _ = _caps(draft.governing_orientation)
        container.add_item(
            discord.ui.TextDisplay(
                "### Links\nManage individual social/payment links or import a link page."
            )
        )
        links = [
            _button(draft, "Add link", "links", discord.ButtonStyle.primary),
            _button(draft, "Import page", "import", discord.ButtonStyle.secondary),
            _button(draft, "Done", "complete-links", discord.ButtonStyle.success),
        ]
        if not draft.document.links:
            links.append(_button(draft, "Skip", "skip-links", discord.ButtonStyle.secondary))
        if (
            draft.target_scope is DraftScope.SERVER
            and draft.server_mode is ServerProfileMode.LINKED
        ):
            links.append(
                _button(draft, "Inherited visibility", "visibility", discord.ButtonStyle.secondary)
            )
        container.add_item(discord.ui.ActionRow(*links))
        if draft.document.links:
            container.add_item(discord.ui.ActionRow(LinkSelect(draft, payment=payment)))
    elif current is DraftStepKey.THRONE:
        container.add_item(
            discord.ui.TextDisplay(
                "### Throne\nConnect an account, select a saved creator, rotate its webhook, "
                "or skip."
            )
        )
        controls = [
            _button(draft, "Connect Throne", "throne", discord.ButtonStyle.primary),
            _button(draft, "Skip", "skip-throne", discord.ButtonStyle.secondary),
        ]
        if draft.document.throne_creator_id:
            controls.insert(
                1, _button(draft, "Rotate webhook", "rotate", discord.ButtonStyle.danger)
            )
        container.add_item(discord.ui.ActionRow(*controls))
        if draft.throne_prefill and draft.throne_prefill.owned_creators:
            options = [
                discord.SelectOption(label=safe_text(creator.handle, limit=80), value=creator.id)
                for creator in draft.throne_prefill.owned_creators[:25]
            ]
            container.add_item(discord.ui.ActionRow(ThroneCreatorSelect(draft, options)))
    else:
        container.add_item(
            discord.ui.TextDisplay(
                "### Review\nYour saved draft is shown above. Edit a section or publish atomically."
            )
        )
        edits = [
            _button(draft, "Edit identity", "identity", discord.ButtonStyle.secondary),
            _button(draft, "Edit links", "links", discord.ButtonStyle.secondary),
        ]
        if (
            draft.target_scope is DraftScope.SERVER
            and draft.server_mode is ServerProfileMode.LINKED
        ):
            edits.append(
                _button(draft, "Inherited visibility", "visibility", discord.ButtonStyle.secondary)
            )
        if draft.governing_orientation is not Orientation.SUBMISSIVE:
            edits.append(_button(draft, "Edit Throne", "throne", discord.ButtonStyle.secondary))
        container.add_item(discord.ui.ActionRow(*edits))
        container.add_item(
            discord.ui.ActionRow(
                _button(draft, "Publish", "publish", discord.ButtonStyle.success),
                _button(draft, "Restart", "restart", discord.ButtonStyle.danger),
            )
        )
    view.add_item(container)
    return view


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
                discord.SelectOption(label=label, value=value.value)
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
            custom_id=wizard_custom_id(draft, f"identity-{field}"),
            placeholder=placeholder,
            min_values=0,
            max_values=len(choices),
            options=[
                discord.SelectOption(label=choice, value=choice, default=choice in selected)
                for choice in choices
            ],
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
    template=re.compile(
        r"bill:p:(?P<draft>[A-Za-z0-9_-]+):(?P<owner>[a-z0-9]+):"
        r"(?P<guild>[a-z0-9]+):(?P<revision>[a-z0-9]+):(?P<action>[a-z0-9:_-]+)$"
    ),
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
        if self.action == "identity":
            await interaction.response.send_modal(IdentityModal(draft, message))
            return
        if self.action == "links":
            await interaction.response.send_modal(LinkModal(draft, message))
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
                updated = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.LINKS,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values=_links_step_values(draft),
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not save links: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=profile_wizard_view(updated))
            return
        if self.action == "throne":
            await interaction.response.send_modal(ThroneModal(draft, message))
            return
        if self.action == "skip-throne":
            try:
                updated = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.THRONE,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values={
                        "throne_creator_id": None,
                        "preferred_payment_link_id": draft.document.preferred_payment_link_id,
                    },
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not skip Throne: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=profile_wizard_view(updated))
            return
        if self.action == "rotate":
            try:
                rotated = await bot.require_worker().rotate_throne(
                    draft.id, owner_user_id=interaction.user.id, expected_revision=draft.revision
                )
                updated = await bot.require_worker().update_draft_step(
                    rotated.draft.id,
                    step=DraftStepKey.THRONE,
                    owner_user_id=interaction.user.id,
                    expected_revision=rotated.draft.revision,
                    values={
                        "throne_creator_id": rotated.draft.document.throne_creator_id,
                        "preferred_payment_link_id": (
                            rotated.draft.document.preferred_payment_link_id
                        ),
                    },
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not rotate that webhook: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=profile_wizard_view(updated))
            if rotated.webhook_url:
                await interaction.followup.send(
                    "Your new private Throne webhook URL (save it now):\n"
                    f"```text\n{rotated.webhook_url}\n```",
                    ephemeral=True,
                )
            return
        await interaction.response.send_message(
            "That action is no longer available. Use the latest wizard.", ephemeral=True
        )


class _ProfileSelectDynamic(
    discord.ui.DynamicItem[discord.ui.Select],
    template=re.compile(
        r"bill:p:(?P<draft>[A-Za-z0-9_-]+):(?P<owner>[a-z0-9]+):"
        r"(?P<guild>[a-z0-9]+):(?P<revision>[a-z0-9]+):"
        r"(?P<action>orientation|identity-pronouns|identity-honourifics|"
        r"identity-labels|link-select|creator-select)$"
    ),
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
        if draft is None or not self.item.values:
            return
        if self.action == "orientation":
            try:
                updated = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.ORIENTATION,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values={"orientation": Orientation(self.item.values[0]).value},
                )
            except (ValueError, WorkerAPIError) as exc:
                await interaction.response.send_message(
                    f"Bill could not save that orientation: {exc}", ephemeral=True
                )
                return
            await interaction.response.edit_message(view=profile_wizard_view(updated))
            return
        if self.action.startswith("identity-"):
            field = self.action.removeprefix("identity-")
            try:
                updated = await bot.require_worker().update_draft_step(
                    draft.id,
                    step=DraftStepKey.IDENTITY,
                    owner_user_id=interaction.user.id,
                    expected_revision=draft.revision,
                    values=_partial_identity_values(draft, field, tuple(self.item.values)),
                )
            except WorkerAPIError as exc:
                await interaction.response.send_message(
                    f"Bill could not save that identity selection: {exc}",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(view=profile_wizard_view(updated))
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
        await interaction.response.edit_message(view=profile_wizard_view(updated))
        if attached.webhook_url:
            await interaction.followup.send(
                "Your private Throne webhook URL (save it now):\n"
                f"```text\n{attached.webhook_url}\n```",
                ephemeral=True,
            )


ProfileSelectDynamic = _ProfileSelectDynamic


def _identity_values(
    draft: ProfileDraft, pronouns: str, honourifics: str, labels: str, aliases: str, details: str
) -> dict[str, object]:
    orientation = draft.governing_orientation
    if orientation is None:
        raise ValueError("choose an orientation first")
    honourific_available, label_available, aliases_available, _, stats_available = _caps(
        orientation
    )
    detail_parts = [part.strip() for part in details.split("|", 2)]
    if len(detail_parts) != 3:
        raise ValueError("use: DM status | stats on/off | bio")
    dm_raw, stats_raw, bio_raw = detail_parts
    linked = (
        draft.target_scope is DraftScope.SERVER and draft.server_mode is ServerProfileMode.LINKED
    )
    status = (
        None if linked and dm_raw.casefold() == "inherit" else DmStatus(dm_raw.casefold()).value
    )
    if stats_raw.casefold() == "inherit" and linked:
        stats: bool | None = None
    elif stats_raw.casefold() in {"on", "yes", "true"}:
        stats = True
    elif stats_raw.casefold() in {"off", "no", "false"}:
        stats = False
    else:
        raise ValueError("stats must be on, off, or inherit")
    bio_overridden = not linked or bool(bio_raw)
    bio = None if bio_raw == "-" else (bio_raw or None)
    parsed_pronouns = _csv(pronouns, PRONOUNS, "pronouns")
    parsed_honourifics = (
        _csv(honourifics, HONOURIFICS, "honourifics") if honourific_available else []
    )
    parsed_labels = _csv(labels, SUBMISSIVE_LABELS, "submissive labels") if label_available else []
    parsed_aliases = (
        []
        if aliases.strip() == "-"
        else [entry.strip() for entry in aliases.split(",") if entry.strip()]
    )
    values: dict[str, object] = {
        "pronouns": parsed_pronouns,
        "honourifics": parsed_honourifics,
        "submissive_labels": parsed_labels,
        "dm_status": status,
        "bio": bio,
        "public_send_stats": bool(stats) if stats_available else False,
        "aliases": parsed_aliases if aliases_available else [],
    }
    if linked:
        overrides: list[str] = []
        existing_overrides = set(draft.document.overridden_fields)
        if pronouns.strip() or "pronouns" in existing_overrides:
            overrides.append("pronouns")
        if honourific_available and (honourifics.strip() or "honourifics" in existing_overrides):
            overrides.append("honourifics")
        if label_available and (labels.strip() or "submissive_labels" in existing_overrides):
            overrides.append("submissive_labels")
        if status is not None:
            overrides.append("dm_status")
        if bio_overridden:
            overrides.append("bio")
        if aliases_available and (aliases.strip() or "aliases" in existing_overrides):
            overrides.append("aliases")
        if stats_available and stats is not None:
            overrides.append("public_send_stats")
        values["overrides"] = overrides
    return values


def _partial_identity_values(
    draft: ProfileDraft,
    field: str,
    selected: tuple[str, ...],
) -> dict[str, object]:
    pronouns = selected if field == "pronouns" else draft.document.selections.pronouns
    honourifics = selected if field == "honourifics" else draft.document.selections.honourifics
    labels = selected if field == "labels" else draft.document.selections.submissive_labels
    linked = (
        draft.target_scope is DraftScope.SERVER and draft.server_mode is ServerProfileMode.LINKED
    )
    overrides = set(draft.document.overridden_fields)
    if linked:
        overrides.add(
            {
                "pronouns": "pronouns",
                "honourifics": "honourifics",
                "labels": "submissive_labels",
            }[field]
        )
    values: dict[str, object] = {
        "pronouns": list(pronouns),
        "honourifics": list(honourifics),
        "submissive_labels": list(labels),
        "dm_status": (
            draft.document.dm_status.value
            if draft.document.dm_status
            else (None if linked else DmStatus.OPEN.value)
        ),
        "bio": draft.document.bio,
        "public_send_stats": draft.document.public_send_stats,
        "aliases": list(draft.document.aliases),
        "complete": False,
    }
    if linked:
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
        await self.message.edit(view=profile_wizard_view(updated))
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


class IdentityModal(discord.ui.Modal, title="Profile identity"):
    def __init__(self, draft: ProfileDraft, message: discord.Message) -> None:
        super().__init__()
        self.draft, self.message = draft, message
        self.aliases = discord.ui.TextInput(
            label="Aliases, comma separated (- clears)",
            default=", ".join(draft.document.aliases),
            required=False,
            max_length=200,
        )
        linked = (
            draft.target_scope is DraftScope.SERVER
            and draft.server_mode is ServerProfileMode.LINKED
        )
        overridden = set(draft.document.overridden_fields)
        dm_default = (
            draft.document.dm_status.value
            if draft.document.dm_status
            else ("inherit" if linked else DmStatus.OPEN.value)
        )
        stats_default = (
            "inherit"
            if linked and "public_send_stats" not in overridden
            else ("on" if draft.document.public_send_stats else "off")
        )
        bio_default = draft.document.bio or "-" if not linked or "bio" in overridden else ""
        self.details = discord.ui.TextInput(
            label="DM status | stats on/off | bio (- clears)",
            default=f"{dm_default} | {stats_default} | {bio_default}",
            required=True,
            max_length=380,
        )
        for field in (self.aliases, self.details):
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            updated = await bot.require_worker().update_draft_step(
                self.draft.id,
                step=DraftStepKey.IDENTITY,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                values=_identity_values(
                    self.draft,
                    ", ".join(self.draft.document.selections.pronouns),
                    ", ".join(self.draft.document.selections.honourifics),
                    ", ".join(self.draft.document.selections.submissive_labels),
                    self.aliases.value,
                    self.details.value,
                ),
            )
        except (ValueError, WorkerAPIError) as exc:
            await interaction.response.send_message(
                f"Bill could not save identity: {exc}", ephemeral=True
            )
            return
        await self.message.edit(view=profile_wizard_view(updated))
        await interaction.response.send_message("Identity saved.", ephemeral=True)


class LinkModal(discord.ui.Modal, title="Add a profile link"):
    def __init__(
        self, draft: ProfileDraft, message: discord.Message, link_id: str | None = None
    ) -> None:
        super().__init__()
        self.draft, self.message, self.link_id = draft, message, link_id
        link = next((item for item in draft.document.links if item.id == link_id), None)
        self.label = discord.ui.TextInput(
            label="Public label", default=link.public_label if link else "", max_length=40
        )
        self.url = discord.ui.TextInput(
            label="HTTPS URL", default=link.normalized_url if link else "", max_length=500
        )
        self.kind = discord.ui.TextInput(
            label="Type: social or payment",
            default=link.link_type.value if link else "social",
            max_length=7,
        )
        self.platform = discord.ui.TextInput(
            label="Platform (optional)",
            default=link.platform if link else "",
            required=False,
            max_length=80,
        )
        self.add_item(self.label)
        self.add_item(self.url)
        self.add_item(self.kind)
        self.add_item(self.platform)

    async def on_submit(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            kind = LinkType(self.kind.value.strip().casefold())
            common = {
                "owner_user_id": interaction.user.id,
                "expected_revision": self.draft.revision,
                "public_label": self.label.value,
                "normalized_url": self.url.value,
                "link_type": kind,
                "platform": self.platform.value or None,
            }
            updated = await (
                bot.require_worker().edit_link(self.draft.id, self.link_id, **common)
                if self.link_id
                else bot.require_worker().add_link(self.draft.id, **common)
            )
        except (ValueError, WorkerAPIError) as exc:
            await interaction.response.send_message(
                f"Bill could not save that link: {exc}", ephemeral=True
            )
            return
        await self.message.edit(view=profile_wizard_view(updated))
        await interaction.response.send_message(
            "Link saved. Choose **Done** when your links are ready.", ephemeral=True
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
        bot = cast("BillBot", interaction.client)
        try:
            result = await bot.require_worker().create_link_import(
                self.draft.id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
                source_url=self.url.value,
            )
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not import that page: {exc}", ephemeral=True
            )
            return
        await self.message.edit(view=profile_wizard_view(result.draft))
        labels = (
            ", ".join(
                safe_text(candidate.public_label, limit=40)
                for candidate in result.link_import.candidates
            )
            or "No public links found"
        )
        await interaction.response.send_message(
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
        await self.message.edit(view=profile_wizard_view(updated))
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
        await self.message.edit(view=profile_wizard_view(result.draft))

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
        await interaction.response.send_modal(LinkModal(self.draft, self.message, self.link_id))

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
        await self.message.edit(view=profile_wizard_view(updated))
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
        await self.message.edit(view=profile_wizard_view(updated))
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
            await interaction.user.dm_channel.send(view=profile_wizard_view(draft))  # type: ignore[union-attr]
        except discord.Forbidden:
            return

    @discord.ui.button(label="Keep draft", style=discord.ButtonStyle.success)
    async def cancel(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(content="Restart cancelled.", view=None)
