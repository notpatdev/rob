from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from rob.discord.cogs.protected import _GOFUNDME_URL, _MEMORIAL_USER_ID, ProtectedCog
from rob.ui.cards.protected import protected_member_card
from rob.ui.render import add_card_actions
from rob.ui.theme import COLOR_WHITE


def _card_text(rendered) -> str:
    return "\n".join(
        str(getattr(child, "content", ""))
        for child in rendered.view.children[0].children
    )


def test_protected_card_is_white_and_mentions_the_account():
    rendered = protected_member_card(user_id=_MEMORIAL_USER_ID)
    assert rendered.view.children[0].accent_color == COLOR_WHITE
    contents = _card_text(rendered)
    assert "## 🪽 Protected Member 🪽" in contents
    assert f"<@{_MEMORIAL_USER_ID}>" in contents
    assert "In loving memory of Aly" in contents


def test_protected_card_footer_names_the_member_in_both_subtext_lines():
    rendered = protected_member_card(user_id=_MEMORIAL_USER_ID)
    contents = _card_text(rendered)
    # The footer renders as `-#` subtext and names Aly in both lines.
    assert "-# Aly is shielded from the inactivity system" in contents
    assert "-# In every server backup, Aly's account is preserved" in contents


def test_protected_card_renders_into_supplied_view_with_gofundme_button():
    view = discord.ui.LayoutView(timeout=None)
    rendered = protected_member_card(
        user_id=_MEMORIAL_USER_ID,
        view=view,
    )
    add_card_actions(
        view,
        discord.ui.Button(
            label="Help Lay Alyssa Rae to Rest",
            url=_GOFUNDME_URL,
            style=discord.ButtonStyle.link,
        ),
    )
    assert rendered.view is view
    assert type(view.children[0]).__name__ == "Container"
    action_row = view.children[1]
    assert type(action_row).__name__ == "ActionRow"
    button = action_row.children[0]
    assert button.url == _GOFUNDME_URL
    assert button.style == discord.ButtonStyle.link


def test_protected_command_deletes_invocation_and_posts_card():
    sent: dict[str, object] = {}
    deleted = {"called": False}

    async def _send(**kwargs):
        sent.update(kwargs)

    async def _delete():
        deleted["called"] = True

    ctx = SimpleNamespace(
        guild=SimpleNamespace(),
        message=SimpleNamespace(delete=_delete),
        send=_send,
    )
    cog = ProtectedCog(SimpleNamespace())

    asyncio.run(cog.protected.callback(cog, ctx))

    # The invoking !protected message is deleted, leaving only the card — posted
    # as a standalone message (not a reply) with mentions suppressed so the
    # memorial account is never pinged.
    assert deleted["called"] is True
    assert "mention_author" not in sent
    assert sent["view"] is not None
    allowed = sent["allowed_mentions"]
    assert allowed.users is False
    assert allowed.roles is False
    assert allowed.everyone is False


def test_protected_command_still_posts_card_when_delete_is_forbidden():
    sent: dict[str, object] = {}

    async def _send(**kwargs):
        sent.update(kwargs)

    async def _delete():
        raise discord.HTTPException(
            SimpleNamespace(status=403, reason="Forbidden"),
            "missing Manage Messages",
        )

    ctx = SimpleNamespace(
        guild=SimpleNamespace(),
        message=SimpleNamespace(delete=_delete),
        send=_send,
    )
    cog = ProtectedCog(SimpleNamespace())

    asyncio.run(cog.protected.callback(cog, ctx))

    # A failed delete (e.g. no Manage Messages permission) must not swallow the
    # card — it is still posted.
    assert sent["view"] is not None
