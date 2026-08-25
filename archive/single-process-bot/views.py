from __future__ import annotations

import asyncio
import json as _json
import pathlib
import re
from typing import TYPE_CHECKING

import discord

from bot.ui.cards import (
    PROFILE_COLOR_PRESETS,
    build_help_pages,
    delete_confirm_card,
    domme_setup_cancelled_card,
    domme_setup_color_card,
    domme_setup_details_card,
    domme_setup_intro_card,
    domme_setup_later_card,
    domme_setup_name_card,
    domme_setup_payments_card,
    domme_setup_review_card,
    domme_setup_throne_card,
    form_link_card,
    help_page_card,
    import_ids_confirm_card,
    import_ids_upload_card,
    info_view,
    role_selection_card,
    staff_review_card,
    sub_setup_cancelled_card,
    sub_setup_color_card,
    sub_setup_details_card,
    sub_setup_intro_card,
    sub_setup_kinks_limits_card,
    sub_setup_later_card,
    sub_setup_name_card,
    sub_setup_owner_card,
    sub_setup_review_card,
    success_view,
    verification_panel_card,
)
from bot.ui.components import disable_all, replace_container
from bot.ui.copy import (
    FORM_URL,
    NOT_YOUR_CONFIRM,
    NOT_YOUR_HELP,
    NOT_YOUR_SETUP,
    NOT_YOURS,
)

if TYPE_CHECKING:
    from bot.verification import (
        DommeProfileService,
        DommeProfileSession,
        ReactionRoleService,
        SubProfileService,
        SubProfileSession,
        VerificationService,
    )


def _clean_optional(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


class VerificationPanelView(discord.ui.LayoutView):
    def __init__(self, service: VerificationService) -> None:
        super().__init__(timeout=None)
        self.service = service
        button = discord.ui.Button(
            label="Verify",
            style=discord.ButtonStyle.primary,
            custom_id="verify_start",
        )
        button.callback = self.verify_start
        self._set_container(verification_panel_card(button))

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)

    async def verify_start(self, interaction: discord.Interaction) -> None:
        await self.service.start_verification(interaction)


class RoleSelectionView(discord.ui.LayoutView):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.selection: str | None = None
        self.message: discord.Message | None = None
        domme_button = discord.ui.Button(
            label="Domme", style=discord.ButtonStyle.secondary
        )
        domme_button.callback = self._domme
        submissive_button = discord.ui.Button(
            label="Submissive",
            style=discord.ButtonStyle.secondary,
        )
        submissive_button.callback = self._submissive
        self._set_container(
            role_selection_card(
                domme_button,
                submissive_button,
                selected_role=self.selection,
            )
        )

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            NOT_YOURS,
            ephemeral=True,
        )
        return False

    async def _domme(self, interaction: discord.Interaction) -> None:
        await self._select(interaction, "Domme")

    async def _submissive(self, interaction: discord.Interaction) -> None:
        await self._select(interaction, "Submissive")

    async def _select(self, interaction: discord.Interaction, value: str) -> None:
        self.selection = value
        self._set_container(
            role_selection_card(
                discord.ui.Button(
                    label="Domme", style=discord.ButtonStyle.secondary, disabled=True
                ),
                discord.ui.Button(
                    label="Submissive",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                ),
                selected_role=self.selection,
            )
        )
        disable_all(self)
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        disable_all(self)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class StaffReviewView(discord.ui.LayoutView):
    def __init__(
        self,
        service: VerificationService | None,
        request_id: int,
        *,
        link_url: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.service = service
        self.request_id = request_id

        approve_button = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"verification:approve:{request_id}",
            disabled=disabled,
        )
        approve_button.callback = self._approve

        deny_underage_button = discord.ui.Button(
            label="Deny (Under 18)",
            style=discord.ButtonStyle.danger,
            custom_id=f"verification:deny_underage:{request_id}",
            disabled=disabled,
        )
        deny_underage_button.callback = self._deny_underage

        deny_invalid_button = discord.ui.Button(
            label="Deny (Invalid Service)",
            style=discord.ButtonStyle.danger,
            custom_id=f"verification:deny_invalid:{request_id}",
            disabled=disabled,
        )
        deny_invalid_button.callback = self._deny_invalid

        link_button: discord.ui.Button | None = None
        if link_url:
            link_button = discord.ui.Button(
                label="Open Link",
                style=discord.ButtonStyle.link,
                url=link_url,
            )

        self._set_container(
            staff_review_card(
                request_id=request_id,
                user_mention="Pending member lookup",
                user_id=0,
                selected_role=None,
                submitted_service=None,
                submitted_value=link_url,
                status="Pending review",
                approve_button=approve_button,
                deny_underage_button=deny_underage_button,
                deny_invalid_button=deny_invalid_button,
                link_button=link_button,
            )
        )

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)

    async def _approve(self, interaction: discord.Interaction) -> None:
        if self.service:
            await self.service.review_request(interaction, self.request_id, "approve")

    async def _deny_underage(self, interaction: discord.Interaction) -> None:
        if self.service:
            await self.service.review_request(
                interaction, self.request_id, "deny_underage"
            )

    async def _deny_invalid(self, interaction: discord.Interaction) -> None:
        if self.service:
            await self.service.review_request(
                interaction, self.request_id, "deny_invalid"
            )


class FormLinkView(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Open Form",
            style=discord.ButtonStyle.link,
            url=FORM_URL,
        )
        self._set_container(form_link_card(button))

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)


class ReactionRoleSetupModal(discord.ui.Modal, title="Create Reaction Role Message"):
    def __init__(
        self, service: "ReactionRoleService", *, default_channel_id: int
    ) -> None:
        super().__init__(timeout=900)
        self.service = service

        self.channel_id_input = discord.ui.TextInput(
            label="Target Channel ID",
            default=str(default_channel_id),
            required=True,
            max_length=30,
            placeholder="1498996917277954098",
        )
        self.title_input = discord.ui.TextInput(
            label="Embed Title",
            required=True,
            max_length=120,
            placeholder="Choose your roles",
        )
        self.description_input = discord.ui.TextInput(
            label="Embed Description",
            required=True,
            max_length=1500,
            style=discord.TextStyle.paragraph,
            placeholder="React below to get your roles.",
        )
        self.color_input = discord.ui.TextInput(
            label="Embed Colour (hex, optional)",
            required=False,
            max_length=7,
            placeholder="#B565FF",
        )
        self.mappings_input = discord.ui.TextInput(
            label="Emoji = Role ID or role mention (one per line)",
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph,
            placeholder="💗 = 1498997125739053087\n🔥 = <@&1498997103400194191>\n<:custom:1234567890> = 1498997148845478051",
        )
        self.add_item(self.channel_id_input)
        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.color_input)
        self.add_item(self.mappings_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.service.create_message_from_modal(
            interaction=interaction,
            channel_id_raw=self.channel_id_input.value,
            title=self.title_input.value,
            description=self.description_input.value,
            color_raw=self.color_input.value,
            mappings_raw=self.mappings_input.value,
        )


class HelpView(discord.ui.LayoutView):
    def __init__(
        self,
        user_id: int,
        pages: list[tuple[str, int, str, tuple[tuple[str, str], ...]]] | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.pages = pages if pages is not None else []
        self.current_page = 0
        self.total_pages = max(1, len(self.pages))

        self.previous_button = discord.ui.Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            custom_id=f"help:previous:{user_id}",
        )
        self.previous_button.callback = self._previous
        self.add_item(self.previous_button)

        self.page_button = discord.ui.Button(
            label=f"Page 1/{self.total_pages}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.add_item(self.page_button)

        self.next_button = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.secondary,
            custom_id=f"help:next:{user_id}",
        )
        self.next_button.callback = self._next
        self.add_item(self.next_button)

        self.close_button = discord.ui.Button(
            label="Close",
            style=discord.ButtonStyle.danger,
            custom_id=f"help:close:{user_id}",
        )
        self.close_button.callback = self._close
        self.add_item(self.close_button)
        self._sync_buttons()
        self._set_container()

    def _set_container(self) -> None:
        replace_container(
            self,
            help_page_card(self.current_page, self.total_pages, self.pages),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            NOT_YOUR_HELP,
            ephemeral=True,
        )
        return False

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.current_page = max(0, self.current_page - 1)
        await self._update(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        await self._update(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        disable_all(self)
        await interaction.response.edit_message(
            view=info_view("Closed.", "Rob returns to the cupboard."),
        )
        self.stop()

    async def _update(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        self._set_container()
        await interaction.response.edit_message(view=self)

    def _sync_buttons(self) -> None:
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        self.page_button.label = f"Page {self.current_page + 1}/{self.total_pages}"


class DommeSetupView(discord.ui.LayoutView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.session.user_id:
            return True
        await interaction.response.send_message(NOT_YOUR_SETUP, ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        if self.session.current_view is not self:
            return
        disable_all(self)
        if self.session.message:
            try:
                await self.session.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.session.current_view = None
        self.service.finish_session(self.session.user_id)


class DommeSetupIntroView(DommeSetupView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(service, session)
        self._set_container(domme_setup_intro_card())

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_name_step(self.session, interaction)

    @discord.ui.button(label="Later", style=discord.ButtonStyle.secondary)
    async def later_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.service.finish_session(self.session.user_id)
        await interaction.response.edit_message(
            view=self.service.build_later_view(),
        )
        self.stop()


class DommeSetupNameView(DommeSetupView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(service, session)
        self._set_container(
            domme_setup_name_card(
                name=session.name,
                honorific=session.honorific,
            )
        )

    @discord.ui.button(label="Name + Honorific", style=discord.ButtonStyle.primary)
    async def open_modal(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            DommeNameModal(self.service, self.session)
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_details_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_intro_step(self.session, interaction)


class DommeSetupDetailsView(DommeSetupView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(service, session)
        self._set_container(
            domme_setup_details_card(
                pronouns=session.pronouns,
                age=session.age,
                tribute_price=session.tribute_price,
                kinks=session.kinks,
                limits=session.limits,
            )
        )

    @discord.ui.button(label="Add Details", style=discord.ButtonStyle.primary)
    async def open_modal(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            DommeDetailsModal(self.service, self.session)
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_payments_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_name_step(self.session, interaction)


class DommeSetupPaymentsView(DommeSetupView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(service, session)
        self._set_container(
            domme_setup_payments_card(
                throne=session.throne,
                tribute_link=session.tribute_link,
                payment_link1=session.payment_link1,
                payment_link2=session.payment_link2,
                payment_link3=session.payment_link3,
                payment_link4=session.payment_link4,
                content_link1=session.content_link1,
                content_link2=session.content_link2,
                content_link3=session.content_link3,
                content_link4=session.content_link4,
            )
        )

    @discord.ui.button(label="Throne & Tribute", style=discord.ButtonStyle.primary)
    async def throne_tribute_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            DommeThroneLinksModal(self.service, self.session)
        )

    @discord.ui.button(label="Payment Links", style=discord.ButtonStyle.primary)
    async def payment_links_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            DommePaymentLinksModal(self.service, self.session)
        )

    @discord.ui.button(label="Content Links", style=discord.ButtonStyle.primary)
    async def content_links_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            DommeContentLinksModal(self.service, self.session)
        )

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.advance_after_payments(self.session, interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.advance_after_payments(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_details_step(self.session, interaction)


class DommeSetupThroneView(DommeSetupView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(service, session)
        self._set_container(domme_setup_throne_card(throne=session.throne))

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success)
    async def sign_up_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.session.throne_tracking_enabled = True
        await self.service.show_color_step(self.session, interaction)

    @discord.ui.button(label="Not Now", style=discord.ButtonStyle.secondary)
    async def not_now_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.session.throne_tracking_enabled = False
        await self.service.show_color_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_payments_step(self.session, interaction)


class DommeSetupColorView(DommeSetupView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(service, session)
        self._set_container(domme_setup_color_card(profile_color=session.profile_color))

    @discord.ui.select(
        placeholder="Choose a profile colour…",
        options=[
            discord.SelectOption(label=label, value=str(value), emoji=emoji)
            for value, emoji, label in PROFILE_COLOR_PRESETS
        ],
    )
    async def color_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        self.session.profile_color = int(select.values[0])
        await self.service.show_color_step(self.session, interaction)

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_review_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if self.session.throne:
            await self.service.show_throne_step(self.session, interaction)
        else:
            await self.service.show_payments_step(self.session, interaction)


class DommeSetupReviewView(DommeSetupView):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(service, session)
        self._set_container(
            domme_setup_review_card(
                name=session.name,
                honorific=session.honorific,
                pronouns=session.pronouns,
                age=session.age,
                tribute_price=session.tribute_price,
                throne=session.throne,
                tribute_link=session.tribute_link,
                payment_link1=session.payment_link1,
                payment_link2=session.payment_link2,
                payment_link3=session.payment_link3,
                payment_link4=session.payment_link4,
                content_link1=session.content_link1,
                content_link2=session.content_link2,
                content_link3=session.content_link3,
                content_link4=session.content_link4,
                profile_color=session.profile_color,
                throne_tracking_enabled=session.throne_tracking_enabled,
                kinks=session.kinks,
                limits=session.limits,
            )
        )

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.save_profile(self.session, interaction)

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.service.finish_session(self.session.user_id)
        await interaction.response.edit_message(
            view=self.service.build_cancelled_view(),
        )
        self.stop()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_color_step(self.session, interaction)


class DommeDeleteConfirmView(discord.ui.LayoutView):
    def __init__(self, service: DommeProfileService, user_id: int) -> None:
        super().__init__(timeout=120)
        self.service = service
        self.user_id = user_id
        self.message: discord.Message | None = None
        delete_button = discord.ui.Button(
            label="Delete",
            style=discord.ButtonStyle.danger,
        )
        delete_button.callback = self._delete
        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )
        cancel_button.callback = self._cancel
        replace_container(
            self,
            delete_confirm_card(
                title="🗑️ Delete Domme Profile?",
                body="This removes the saved Domme profile and its public card.",
                delete_button=delete_button,
                cancel_button=cancel_button,
            ),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(NOT_YOUR_CONFIRM, ephemeral=True)
        return False

    async def _delete(self, interaction: discord.Interaction) -> None:
        await self.service.delete_profile(interaction, self.user_id)
        self.stop()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            view=info_view(
                "Cancelled.",
                "No changes. No witnesses.",
            )
        )
        self.stop()

    async def on_timeout(self) -> None:
        disable_all(self)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class DommeNameModal(discord.ui.Modal, title="Name + Honorific"):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.name_input = discord.ui.TextInput(
            label="Name",
            default=session.name or "",
            required=False,
            max_length=100,
        )
        self.honorific_input = discord.ui.TextInput(
            label="Honorific (comma separated)",
            default=session.honorific or "",
            required=False,
            max_length=200,
        )
        self.add_item(self.name_input)
        self.add_item(self.honorific_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.name = _clean_optional(self.name_input.value)
        self.session.honorific = _clean_optional(self.honorific_input.value)
        await interaction.response.defer()
        await self.service.show_details_step(self.session, interaction)


class DommeDetailsModal(discord.ui.Modal, title="The Nitty Gritty"):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.pronouns_input = discord.ui.TextInput(
            label="Pronouns",
            default=session.pronouns or "",
            required=False,
            max_length=100,
        )
        self.age_input = discord.ui.TextInput(
            label="Age",
            default=session.age or "",
            required=False,
            max_length=50,
        )
        self.tribute_price_input = discord.ui.TextInput(
            label="Tribute Fee Price",
            default=session.tribute_price or "",
            required=False,
            max_length=100,
        )
        self.kinks_input = discord.ui.TextInput(
            label="Kinks",
            default=session.kinks or "",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.limits_input = discord.ui.TextInput(
            label="Limits",
            default=session.limits or "",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.pronouns_input)
        self.add_item(self.age_input)
        self.add_item(self.tribute_price_input)
        self.add_item(self.kinks_input)
        self.add_item(self.limits_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.pronouns = _clean_optional(self.pronouns_input.value)
        self.session.age = _clean_optional(self.age_input.value)
        self.session.tribute_price = _clean_optional(self.tribute_price_input.value)
        self.session.kinks = _clean_optional(self.kinks_input.value)
        self.session.limits = _clean_optional(self.limits_input.value)
        await interaction.response.defer()
        await self.service.show_payments_step(self.session, interaction)


class DommeThroneLinksModal(discord.ui.Modal, title="Throne & Tribute"):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.throne_input = discord.ui.TextInput(
            label="Throne URL",
            default=session.throne or "",
            required=False,
            max_length=200,
            placeholder="https://throne.com/yourname",
        )
        self.tribute_input = discord.ui.TextInput(
            label="Preferred Tribute Link",
            default=session.tribute_link or "",
            required=False,
            max_length=200,
            placeholder="Your main tribute link — shown as a button on your profile",
        )
        self.add_item(self.throne_input)
        self.add_item(self.tribute_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.throne = _clean_optional(self.throne_input.value)
        self.session.tribute_link = _clean_optional(self.tribute_input.value)
        await interaction.response.defer()
        await self.service.refresh_payments_step(self.session, interaction)


class DommePaymentLinksModal(discord.ui.Modal, title="Payment Links"):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.link1_input = discord.ui.TextInput(
            label="Payment Link 1 (e.g. PayPal, CashApp…)",
            default=session.payment_link1 or "",
            required=False,
            max_length=200,
        )
        self.link2_input = discord.ui.TextInput(
            label="Payment Link 2",
            default=session.payment_link2 or "",
            required=False,
            max_length=200,
        )
        self.link3_input = discord.ui.TextInput(
            label="Payment Link 3",
            default=session.payment_link3 or "",
            required=False,
            max_length=200,
        )
        self.link4_input = discord.ui.TextInput(
            label="Payment Link 4",
            default=session.payment_link4 or "",
            required=False,
            max_length=200,
        )
        self.add_item(self.link1_input)
        self.add_item(self.link2_input)
        self.add_item(self.link3_input)
        self.add_item(self.link4_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.payment_link1 = _clean_optional(self.link1_input.value)
        self.session.payment_link2 = _clean_optional(self.link2_input.value)
        self.session.payment_link3 = _clean_optional(self.link3_input.value)
        self.session.payment_link4 = _clean_optional(self.link4_input.value)
        await interaction.response.defer()
        await self.service.refresh_payments_step(self.session, interaction)


class DommeContentLinksModal(discord.ui.Modal, title="Content Links"):
    def __init__(
        self, service: DommeProfileService, session: DommeProfileSession
    ) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.link1_input = discord.ui.TextInput(
            label="Content Link 1 (e.g. OnlyFans, Fansly…)",
            default=session.content_link1 or "",
            required=False,
            max_length=200,
        )
        self.link2_input = discord.ui.TextInput(
            label="Content Link 2",
            default=session.content_link2 or "",
            required=False,
            max_length=200,
        )
        self.link3_input = discord.ui.TextInput(
            label="Content Link 3",
            default=session.content_link3 or "",
            required=False,
            max_length=200,
        )
        self.link4_input = discord.ui.TextInput(
            label="Content Link 4",
            default=session.content_link4 or "",
            required=False,
            max_length=200,
        )
        self.add_item(self.link1_input)
        self.add_item(self.link2_input)
        self.add_item(self.link3_input)
        self.add_item(self.link4_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.content_link1 = _clean_optional(self.link1_input.value)
        self.session.content_link2 = _clean_optional(self.link2_input.value)
        self.session.content_link3 = _clean_optional(self.link3_input.value)
        self.session.content_link4 = _clean_optional(self.link4_input.value)
        await interaction.response.defer()
        await self.service.refresh_payments_step(self.session, interaction)


# ---------------------------------------------------------------------------
# Sub profile setup views and modals
# ---------------------------------------------------------------------------


class SubSetupView(discord.ui.LayoutView):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.session.user_id:
            return True
        await interaction.response.send_message(NOT_YOUR_SETUP, ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        if self.session.current_view is not self:
            return
        disable_all(self)
        if self.session.message:
            try:
                await self.session.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.session.current_view = None
        self.service.finish_session(self.session.user_id)


class SubSetupIntroView(SubSetupView):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(service, session)
        self._set_container(sub_setup_intro_card())

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_name_step(self.session, interaction)

    @discord.ui.button(label="Later", style=discord.ButtonStyle.secondary)
    async def later_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.service.finish_session(self.session.user_id)
        await interaction.response.edit_message(
            view=self.service.build_later_view(),
        )
        self.stop()


class SubSetupNameView(SubSetupView):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(service, session)
        self._set_container(sub_setup_name_card(throne_name=session.throne_name))

    @discord.ui.button(label="Set Throne Name", style=discord.ButtonStyle.primary)
    async def set_name_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            SubThroneNameModal(self.service, self.session)
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_details_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_intro_step(self.session, interaction)


class SubSetupReviewView(SubSetupView):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(service, session)
        owner_label = None
        if session.owned_by_domme_user_id is not None:
            owner_label = f"<@{session.owned_by_domme_user_id}>"
        self._set_container(
            sub_setup_review_card(
                throne_name=session.throne_name,
                name=session.name,
                pronouns=session.pronouns,
                age=session.age,
                kinks=session.kinks,
                limits=session.limits,
                profile_color=session.profile_color,
                owned_by_label=owner_label,
            )
        )

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.save_profile(self.session, interaction)

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.service.finish_session(self.session.user_id)
        await interaction.response.edit_message(
            view=self.service.build_cancelled_view(),
        )
        self.stop()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_owner_step(self.session, interaction)


class SubDeleteConfirmView(discord.ui.LayoutView):
    def __init__(self, service: SubProfileService, user_id: int) -> None:
        super().__init__(timeout=120)
        self.service = service
        self.user_id = user_id
        self.message: discord.Message | None = None
        delete_button = discord.ui.Button(
            label="Delete",
            style=discord.ButtonStyle.danger,
        )
        delete_button.callback = self._delete
        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )
        cancel_button.callback = self._cancel
        replace_container(
            self,
            delete_confirm_card(
                title="🗑️ Delete Sub Profile?",
                body="This removes the saved Sub profile and its public card.",
                delete_button=delete_button,
                cancel_button=cancel_button,
            ),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(NOT_YOUR_CONFIRM, ephemeral=True)
        return False

    async def _delete(self, interaction: discord.Interaction) -> None:
        await self.service.delete_profile(interaction, self.user_id)
        self.stop()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            view=info_view(
                "Cancelled.",
                "No changes made.",
            )
        )
        self.stop()

    async def on_timeout(self) -> None:
        disable_all(self)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SubThroneNameModal(discord.ui.Modal, title="Throne Name"):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.throne_name_input = discord.ui.TextInput(
            label="Your Throne sending name",
            default=session.throne_name or "",
            required=False,
            max_length=100,
            placeholder="The name shown when you send on Throne",
        )
        self.add_item(self.throne_name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.throne_name = _clean_optional(self.throne_name_input.value)
        await interaction.response.defer()
        await self.service.show_details_step(self.session, interaction)


class SubSetupDetailsView(SubSetupView):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(service, session)
        self._set_container(
            sub_setup_details_card(
                name=session.name,
                pronouns=session.pronouns,
                age=session.age,
            )
        )

    @discord.ui.button(label="Add Details", style=discord.ButtonStyle.primary)
    async def open_modal(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            SubDetailsModal(self.service, self.session)
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_kinks_limits_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_name_step(self.session, interaction)


class SubDetailsModal(discord.ui.Modal, title="Personal Details"):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.name_input = discord.ui.TextInput(
            label="Name",
            default=session.name or "",
            required=False,
            max_length=100,
        )
        self.pronouns_input = discord.ui.TextInput(
            label="Pronouns",
            default=session.pronouns or "",
            required=False,
            max_length=100,
        )
        self.age_input = discord.ui.TextInput(
            label="Age",
            default=session.age or "",
            required=False,
            max_length=50,
        )
        self.add_item(self.name_input)
        self.add_item(self.pronouns_input)
        self.add_item(self.age_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.name = _clean_optional(self.name_input.value)
        self.session.pronouns = _clean_optional(self.pronouns_input.value)
        self.session.age = _clean_optional(self.age_input.value)
        await interaction.response.defer()
        await self.service.show_kinks_limits_step(self.session, interaction)


class SubSetupKinksLimitsView(SubSetupView):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(service, session)
        self._set_container(
            sub_setup_kinks_limits_card(
                kinks=session.kinks,
                limits=session.limits,
            )
        )

    @discord.ui.button(label="Add Kinks & Limits", style=discord.ButtonStyle.primary)
    async def open_modal(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            SubKinksLimitsModal(self.service, self.session)
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_color_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_details_step(self.session, interaction)


class SubKinksLimitsModal(discord.ui.Modal, title="Kinks & Limits"):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.session = session

        self.kinks_input = discord.ui.TextInput(
            label="Kinks",
            default=session.kinks or "",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
            placeholder="Things you enjoy…",
        )
        self.limits_input = discord.ui.TextInput(
            label="Limits",
            default=session.limits or "",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
            placeholder="Hard limits or things to avoid…",
        )
        self.add_item(self.kinks_input)
        self.add_item(self.limits_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.session.kinks = _clean_optional(self.kinks_input.value)
        self.session.limits = _clean_optional(self.limits_input.value)
        await interaction.response.defer()
        await self.service.show_color_step(self.session, interaction)


class SubSetupColorView(SubSetupView):
    def __init__(self, service: SubProfileService, session: SubProfileSession) -> None:
        super().__init__(service, session)
        self._set_container(sub_setup_color_card(profile_color=session.profile_color))

    @discord.ui.select(
        placeholder="Choose a profile colour…",
        options=[
            discord.SelectOption(label=label, value=str(value), emoji=emoji)
            for value, emoji, label in PROFILE_COLOR_PRESETS
        ],
    )
    async def color_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        self.session.profile_color = int(select.values[0])
        await self.service.show_color_step(self.session, interaction)

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_owner_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_kinks_limits_step(self.session, interaction)


class _OwnerSelect(discord.ui.Select["SubSetupOwnerView"]):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Choose a Domme (or None)…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        self.view.session.owned_by_domme_user_id = (
            None if value == "none" else int(value)
        )
        await self.view.service.refresh_owner_step(
            self.view.session, interaction, self.options
        )


class SubSetupOwnerView(SubSetupView):
    def __init__(
        self,
        service: SubProfileService,
        session: SubProfileSession,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(service, session)
        self.add_item(_OwnerSelect(options))
        selected = None
        if session.owned_by_domme_user_id is not None:
            selected = f"<@{session.owned_by_domme_user_id}>"
        self._set_container(sub_setup_owner_card(owned_by_label=selected))

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_review_step(self.session, interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.session.owned_by_domme_user_id = None
        await self.service.show_review_step(self.session, interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.service.show_color_step(self.session, interaction)


# ---------------------------------------------------------------------------
# Legacy ID import helper (currently unused by the live runtime)
# ---------------------------------------------------------------------------

_IMPORT_FIELD_NAMES = (
    "GUILD_ID",
    "REGISTRATION_CHANNEL_ID",
    "LEADERBOARD_CHANNEL_ID",
    "SEND_TRACK_CHANNEL_ID",
    "DOMME_ROLE_ID",
    "SUBMISSIVE_ROLE_ID",
    "MODERATION_ROLE_ID",
    "EVENT_BAN_ROLE_ID",
)

_OPTIONAL_IMPORT_FIELDS = {"EVENT_BAN_ROLE_ID"}

_IMPORT_IDS_BUTTON_TIMEOUT_SECONDS = 300
_IMPORT_IDS_UPLOAD_TIMEOUT_SECONDS = 120
_IMPORT_IDS_CONFIRM_TIMEOUT_SECONDS = 120

_DIGITS_ONLY_RE = re.compile(r"\d+")

_CHANNELS_PY_TEMPLATE = """\
from __future__ import annotations

GUILD_ID = {GUILD_ID}

REGISTRATION_CHANNEL_ID = {REGISTRATION_CHANNEL_ID}
LEADERBOARD_CHANNEL_ID = {LEADERBOARD_CHANNEL_ID}
SEND_TRACK_CHANNEL_ID = {SEND_TRACK_CHANNEL_ID}

DOMME_ROLE_ID = {DOMME_ROLE_ID}
SUBMISSIVE_ROLE_ID = {SUBMISSIVE_ROLE_ID}
MODERATION_ROLE_ID = {MODERATION_ROLE_ID}
EVENT_BAN_ROLE_ID = {EVENT_BAN_ROLE_ID}
"""

# Fuzzy aliases: lowercase fragment → canonical field name
_IMPORT_ALIASES: dict[str, str] = {
    "guild": "GUILD_ID",
    "guild_id": "GUILD_ID",
    "server": "GUILD_ID",
    "server_id": "GUILD_ID",
    "registration": "REGISTRATION_CHANNEL_ID",
    "registration_channel": "REGISTRATION_CHANNEL_ID",
    "reg": "REGISTRATION_CHANNEL_ID",
    "verify_channel": "REGISTRATION_CHANNEL_ID",
    "verification_channel": "REGISTRATION_CHANNEL_ID",
    "leaderboard": "LEADERBOARD_CHANNEL_ID",
    "leaderboard_channel": "LEADERBOARD_CHANNEL_ID",
    "lb": "LEADERBOARD_CHANNEL_ID",
    "lb_channel": "LEADERBOARD_CHANNEL_ID",
    "send_track": "SEND_TRACK_CHANNEL_ID",
    "send_track_channel": "SEND_TRACK_CHANNEL_ID",
    "sends": "SEND_TRACK_CHANNEL_ID",
    "sends_channel": "SEND_TRACK_CHANNEL_ID",
    "track": "SEND_TRACK_CHANNEL_ID",
    "track_channel": "SEND_TRACK_CHANNEL_ID",
    "domme": "DOMME_ROLE_ID",
    "domme_role": "DOMME_ROLE_ID",
    "dom": "DOMME_ROLE_ID",
    "dom_role": "DOMME_ROLE_ID",
    "submissive": "SUBMISSIVE_ROLE_ID",
    "submissive_role": "SUBMISSIVE_ROLE_ID",
    "sub": "SUBMISSIVE_ROLE_ID",
    "sub_role": "SUBMISSIVE_ROLE_ID",
    "moderation": "MODERATION_ROLE_ID",
    "moderation_role": "MODERATION_ROLE_ID",
    "mod": "MODERATION_ROLE_ID",
    "mod_role": "MODERATION_ROLE_ID",
    "staff": "MODERATION_ROLE_ID",
    "staff_role": "MODERATION_ROLE_ID",
    "event_ban": "EVENT_BAN_ROLE_ID",
    "event_ban_role": "EVENT_BAN_ROLE_ID",
    "ban_role": "EVENT_BAN_ROLE_ID",
    "eventban": "EVENT_BAN_ROLE_ID",
}


def _resolve_key(raw_key: str) -> str | None:
    """Map a raw key from a file to a canonical _IMPORT_FIELD_NAMES entry."""
    normalised = raw_key.strip().lower().replace("-", "_").replace(" ", "_")
    without_id = normalised.removesuffix("_id")
    # Exact match against canonical names (uppercase)
    if normalised.upper() in _IMPORT_FIELD_NAMES:
        return normalised.upper()
    # Alias lookup
    if normalised in _IMPORT_ALIASES:
        return _IMPORT_ALIASES[normalised]
    if without_id in _IMPORT_ALIASES:
        return _IMPORT_ALIASES[without_id]
    return None


def _parse_ids_file(content: str, filename: str) -> tuple[dict[str, int], list[str]]:
    """Parse a JSON or text file and return (parsed_ids, warnings)."""
    parsed: dict[str, int] = {}
    warnings: list[str] = []
    raw_pairs: list[tuple[str, str]] = []

    # --- try JSON first ---
    if filename.lower().endswith(".json") or content.lstrip().startswith("{"):
        try:
            data = _json.loads(content)
            if isinstance(data, dict):
                for k, v in data.items():
                    raw_pairs.append((str(k), str(v)))
            else:
                warnings.append("That JSON is weird. Trying text mode.")
                raw_pairs = []  # ensure text parser runs
        except _json.JSONDecodeError:
            warnings.append("That JSON fell over. Trying text mode.")

    # --- text parsing (KEY=VALUE or KEY: VALUE) ---
    if not raw_pairs:
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            # Try = then :
            if "=" in line:
                key, _, value = line.partition("=")
            elif ":" in line:
                key, _, value = line.partition(":")
            else:
                continue
            raw_pairs.append((key.strip(), value.strip().strip('"').strip("'")))

    # --- resolve and validate each pair ---
    for raw_key, raw_value in raw_pairs:
        canonical = _resolve_key(raw_key)
        if canonical is None:
            warnings.append(f"Unknown key `{raw_key}`. Rob ignored it.")
            continue
        # Strip quotes / whitespace
        value_clean = raw_value.strip().strip('"').strip("'")
        if not _DIGITS_ONLY_RE.fullmatch(value_clean):
            warnings.append(f"`{canonical}` wants a Discord ID. Got `{value_clean}`.")
            continue
        if canonical in parsed:
            warnings.append(f"`{canonical}` showed up twice. Rob kept the first one.")
            continue
        parsed[canonical] = int(value_clean)

    return parsed, warnings


def _write_channels_py(parsed: dict[str, int]) -> str | None:
    """Write channels.py from parsed IDs. Returns error message or None on success."""
    channels_path = pathlib.Path(__file__).parent / "channels.py"
    try:
        channels_path.write_text(
            _CHANNELS_PY_TEMPLATE.format(**parsed), encoding="utf-8"
        )
    except OSError as exc:
        return str(exc)
    return None


class ImportIdsConfirmView(discord.ui.LayoutView):
    """Confirm / Cancel buttons shown after the file is parsed."""

    def __init__(
        self,
        parsed: dict[str, int],
        warnings: list[str],
        *,
        invoker_id: int,
    ) -> None:
        super().__init__(timeout=_IMPORT_IDS_CONFIRM_TIMEOUT_SECONDS)
        self._parsed = parsed
        self._invoker_id = invoker_id
        self._set_container(import_ids_confirm_card(parsed, warnings))

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._invoker_id:
            return True
        await interaction.response.send_message(
            NOT_YOUR_CONFIRM,
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        disable_all(self)
        err = _write_channels_py(self._parsed)
        if err:
            await interaction.response.edit_message(
                view=info_view(
                    "Could not write `channels.py`.",
                    err,
                ),
            )
        else:
            await interaction.response.edit_message(
                view=success_view(
                    "Saved.",
                    "Restart the bot so Rob can use the new IDs.",
                ),
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✗")
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        disable_all(self)
        await interaction.response.edit_message(
            view=info_view(
                "Cancelled.",
                "Nothing changed.",
            ),
        )
        self.stop()


class ImportIdsUploadView(discord.ui.LayoutView):
    """Container button — starts the file-upload flow when clicked."""

    def __init__(self, *, invoker_id: int) -> None:
        super().__init__(timeout=_IMPORT_IDS_BUTTON_TIMEOUT_SECONDS)
        self._invoker_id = invoker_id
        button = discord.ui.Button(
            label="Upload File",
            style=discord.ButtonStyle.primary,
            emoji="📂",
        )
        button.callback = self._upload_file
        self._set_container(import_ids_upload_card(button))

    def _set_container(self, container: discord.ui.Container) -> None:
        replace_container(self, container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._invoker_id:
            return True
        await interaction.response.send_message(
            NOT_YOUR_CONFIRM,
            ephemeral=True,
        )
        return False

    async def _upload_file(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"📎 Drop the `.json` or `.txt` file in this channel.\n"
            f"You have {_IMPORT_IDS_UPLOAD_TIMEOUT_SECONDS} seconds.",
            ephemeral=True,
        )
        self.stop()
        disable_all(self)

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == interaction.user.id
                and m.channel.id == (interaction.channel_id or 0)
                and bool(m.attachments)
            )

        try:
            msg: discord.Message = await interaction.client.wait_for(
                "message",
                check=check,
                timeout=_IMPORT_IDS_UPLOAD_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏱️ Timed out. Rob gave up waiting for the file.",
                ephemeral=True,
            )
            return

        attachment = msg.attachments[0]
        # Silently clean up the upload message so the channel stays tidy
        try:
            await msg.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            raw_bytes = await attachment.read()
            content = raw_bytes.decode("utf-8", errors="replace")
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Could not read the file: {exc}",
                ephemeral=True,
            )
            return

        parsed, warnings = _parse_ids_file(content, attachment.filename)

        # Check required fields are present
        missing = [
            f
            for f in _IMPORT_FIELD_NAMES
            if f not in parsed and f not in _OPTIONAL_IMPORT_FIELDS
        ]
        if missing:
            await interaction.followup.send(
                f"❌ Rob could not find these IDs:\n"
                + "\n".join(f"• `{f}`" for f in missing)
                + "\n\nCheck the field names and try again.",
                ephemeral=True,
            )
            return

        # Fill optional fields with 0 if absent
        for field in _OPTIONAL_IMPORT_FIELDS:
            parsed.setdefault(field, 0)

        confirm_view = ImportIdsConfirmView(
            parsed,
            warnings,
            invoker_id=interaction.user.id,
        )
        await interaction.followup.send(
            view=confirm_view,
            ephemeral=True,
        )
