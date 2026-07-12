from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from rob.config.guilds import BOT_OWNER_USER_IDS
from rob.ui.cards.errors import error_card
from rob.ui.cards.report import report_staff_card, report_submitted_card

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)


class _ReportModal(discord.ui.Modal, title="Report an issue with Rob"):
    def __init__(self, *, cog: "ReportsCog") -> None:
        super().__init__()
        self.cog = cog
        self.issue = discord.ui.TextInput(
            label="What seems to be wrong?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
        )
        self.acknowledgement = discord.ui.TextInput(
            label="Type YES to confirm this is an issue with Rob",
            style=discord.TextStyle.short,
            required=True,
            max_length=3,
        )
        self.file_upload = discord.ui.FileUpload(
            custom_id="report_upload",
            required=False,
            min_values=0,
            max_values=1,
        )
        self.add_item(self.issue)
        self.add_item(self.acknowledgement)
        self.add_item(
            discord.ui.Label(
                text="Optional screenshot or file",
                description="Add one screenshot or file that helps explain the issue.",
                component=self.file_upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = list(getattr(self.file_upload, "values", []) or [])
        attachment = values[0] if values else None
        await self.cog.submit_report(
            interaction,
            issue_text=str(self.issue.value).strip(),
            acknowledgement=str(self.acknowledgement.value).strip(),
            attachment=attachment,
        )


class ReportsCog(commands.Cog):
    def __init__(self, bot: RobBot) -> None:
        self.bot = bot

    @discord.app_commands.command(name="report", description="Report an issue with Rob.")
    @discord.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def report(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_ReportModal(cog=self))

    async def _resolve_destinations(
        self,
        interaction: discord.Interaction,
    ) -> list[discord.abc.Messageable]:
        del interaction
        destinations: list[discord.abc.Messageable] = []
        seen_user_ids: set[int] = set()
        # DM the configured bot owners, not the Discord application owner: that
        # can be a team account or have DMs closed (403).
        for owner_user_id in BOT_OWNER_USER_IDS:
            user = self.bot.get_user(owner_user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(owner_user_id)
                except discord.HTTPException:
                    continue
            resolved_user_id = getattr(user, "id", owner_user_id)
            if resolved_user_id in seen_user_ids:
                continue
            seen_user_ids.add(resolved_user_id)
            destinations.append(user)
        return destinations

    async def _materialize_attachment(
        self,
        attachment: discord.Attachment | None,
    ) -> discord.File | None:
        if attachment is None:
            return None

        try:
            return await attachment.to_file(use_cached=True)
        except TypeError:
            # Test doubles may not accept the keyword-only signature.
            try:
                return await attachment.to_file()
            except (AttributeError, TypeError):
                pass
            except discord.HTTPException:
                pass
        except discord.HTTPException:
            pass

        filename = getattr(attachment, "filename", "report-upload")
        description = getattr(attachment, "description", None)
        for use_cached in (False, True):
            try:
                data = await attachment.read(use_cached=use_cached)
            except TypeError:
                try:
                    data = await attachment.read()
                except (AttributeError, TypeError, discord.HTTPException):
                    continue
            except (AttributeError, discord.HTTPException):
                continue
            return discord.File(io.BytesIO(data), filename=filename, description=description)

        return None

    async def submit_report(
        self,
        interaction: discord.Interaction,
        *,
        issue_text: str,
        acknowledgement: str,
        attachment: discord.Attachment | None,
    ) -> None:
        if not issue_text:
            await interaction.response.send_message(
                **error_card("Please include what seems to be wrong.").send_kwargs(),
                ephemeral=True,
            )
            return

        if acknowledgement.strip().upper() != "YES":
            await interaction.response.send_message(
                **error_card("Please type YES to confirm this report is about Rob.").send_kwargs(),
                ephemeral=True,
            )
            return

        destinations = await self._resolve_destinations(interaction)
        if not destinations:
            await interaction.response.send_message(
                **error_card(
                    "Rob could not find a report destination right now.",
                    "Please contact a moderator while we reconnect the report channel.",
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        submitted_at = datetime.now(timezone.utc)
        server_label = (
            f"{interaction.guild.name} / {interaction.guild.id}"
            if interaction.guild is not None
            else "Direct Message / N/A"
        )
        report_card = report_staff_card(
            reporter_mention=interaction.user.mention,
            issue_text=issue_text,
            server_label=server_label,
            submitted_unix=int(submitted_at.timestamp()),
        )

        try:
            for destination in destinations:
                await destination.send(**report_card.send_kwargs())
                # Components V2 views suppress attachment previews, so the file
                # is sent as its own follow-up message.
                file_obj = await self._materialize_attachment(attachment)
                if file_obj is not None:
                    await destination.send(file=file_obj)
                elif attachment is not None:
                    await destination.send(
                        f"Attached file (couldn't be re-uploaded): {attachment.url}"
                    )
        except discord.HTTPException:
            log.warning("Failed to deliver /report submission.", exc_info=True)
            await interaction.response.send_message(
                **error_card(
                    "Rob could not deliver that report right now.",
                    "Please let a moderator know while this is fixed.",
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            **report_submitted_card().send_kwargs(),
            ephemeral=True,
        )
