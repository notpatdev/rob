from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from discord import app_commands
from discord.ext import commands

from rob.config.guilds import MAIN_GUILD_ID
from rob.ui.cards.errors import error_card
from rob.ui.cards.registration import registration_card
from rob.utils.money import dollars_to_cents, format_money_from_cents
from rob.utils.text import parse_user_mention

if TYPE_CHECKING:
    import discord

    from rob.discord.client import RobBot


_MANUAL_METHODS = ["cashapp", "venmo", "paypal", "onlyfans", "loyalfans", "youpay", "throne", "other"]


def _resolve_sub_attribution(
    guild: "discord.Guild | None", sub: str | None
) -> tuple[str | None, int | None]:
    """Resolve the free-text ``sub`` field into ``(sub_name, sub_user_id)``.

    When a Dom/me picks a real member from the @-autocomplete, Discord sends the
    raw mention token ("<@123>"). Treat that as a link to the user so the send is
    attributed to them and rendered as a clean mention, falling back to their
    display name as the recorded sending name when the member is in cache.
    """

    cleaned = (sub or "").strip()
    if not cleaned:
        return None, None
    user_id = parse_user_mention(cleaned)
    if user_id is None:
        return cleaned, None
    member = guild.get_member(user_id) if guild is not None else None
    sub_name = member.display_name if member is not None else None
    return sub_name, user_id


class SendsCog(commands.Cog):
    def __init__(self, bot: RobBot) -> None:
        self.bot = bot

    @app_commands.command(name="add", description="Log a manual send for the leaderboard.")
    @app_commands.guilds(MAIN_GUILD_ID)
    @app_commands.describe(
        amount="Amount sent in USD.",
        method="Where the send happened.",
        sub="Optional sending name to attribute.",
        note="Optional item or note for the send.",
    )
    @app_commands.choices(method=[app_commands.Choice(name=value, value=value) for value in _MANUAL_METHODS])
    async def add_send(
        self,
        interaction: "discord.Interaction",
        amount: app_commands.Range[float, 0.01],
        method: app_commands.Choice[str],
        sub: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(
                **error_card("This command can only be used in a server.").send_kwargs(),
                ephemeral=True,
            )
            return

        if await self.bot.maintenance_service.get_wind_down_phase() >= 2:
            await interaction.response.send_message(
                **error_card(
                    "Manual sends are closed — Rob is winding down."
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        domme = await self.bot.dommes_repo.get_by_user_id(
            interaction.guild.id,
            interaction.user.id,
        )
        if domme is None:
            await interaction.response.send_message(
                **error_card("Only registered Dom/mes can use `/add`.").send_kwargs(),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        sub_name, sub_user_id = _resolve_sub_attribution(interaction.guild, sub)
        send = await self.bot.send_service.record_manual_send(
            guild_id=interaction.guild.id,
            domme_id=domme.id,
            domme_user_id=interaction.user.id,
            sub_name=sub_name,
            sub_user_id=sub_user_id,
            amount_cents=dollars_to_cents(float(amount)),
            currency="USD",
            method=method.value,
            note=(note or "").strip() or None,
        )
        if send is None:
            await interaction.followup.send(
                **error_card("That send could not be recorded.").send_kwargs(),
                ephemeral=True,
            )
            return

        tracking_disabled = await self.bot.maintenance_service.send_tracking_disabled_for_guild(interaction.guild.id)
        send_queue = getattr(self.bot, "send_queue_service", None)
        if send_queue is not None and not tracking_disabled:
            await send_queue.notify_send(send.id)

        if tracking_disabled:
            queue_label = "saved with no Discord notification"
        else:
            queue_label = (
                "queued for after maintenance"
                if send.discord_post_status == "queued_maintenance"
                else "queued for posting"
            )
        if send.sub_name:
            sender_label = send.sub_name
        elif send.sub_user_id is not None:
            sender_label = f"<@{send.sub_user_id}>"
        else:
            sender_label = "Unclaimed"
        await interaction.followup.send(
            **registration_card(
                title="Rob | Send Logged",
                summary=f"Recorded {format_money_from_cents(send.amount_cents)} and {queue_label}.",
                details=[
                    ("Method", method.value),
                    ("Sender", sender_label),
                ],
            ).send_kwargs(),
            ephemeral=True,
        )
