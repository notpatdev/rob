from __future__ import annotations

import pytest
import discord

from rob.ui.cards.registration import throne_setup_card
from rob.ui.render import CardSection, RenderedMessage, RobCard, add_card_actions, build_action_row, render_card, supports_components_v2


def test_components_v2_support_check_exposes_required_runtime():
    assert supports_components_v2() is True


def test_render_card_returns_layoutview_not_embed():
    msg = render_card(RobCard(title="T", body="B", sections=[CardSection(title="S", text="V")]))
    assert msg.view is not None
    assert msg.mode == "components_v2"


def test_send_kwargs_do_not_include_embed_fields():
    kwargs = RenderedMessage(view=None).send_kwargs()
    assert "embed" not in kwargs
    assert "embeds" not in kwargs


def test_v2_edit_kwargs_clear_legacy_fields_and_keep_view():
    msg = render_card(RobCard(title="T", body="B"))
    kwargs = msg.edit_kwargs()
    assert kwargs["content"] is None
    assert "embed" not in kwargs
    assert kwargs["embeds"] == []
    assert kwargs["attachments"] == []
    assert kwargs["view"] is msg.view


def test_setup_success_card_accepts_image_url_without_embed_mutation():
    msg = throne_setup_card("ok", image_url="https://example.com/test.gif")
    assert msg.view is not None


def test_render_card_raises_if_components_v2_missing(monkeypatch: pytest.MonkeyPatch):
    import discord

    monkeypatch.delattr(discord.ui, "LayoutView", raising=False)
    with pytest.raises(RuntimeError):
        render_card(RobCard(title="X", body="Y"))


def test_render_card_rejects_prepopulated_layout_to_enforce_container_first_order():
    import discord

    view = discord.ui.LayoutView()
    view.add_item(discord.ui.Button(label="X"))
    with pytest.raises(RuntimeError):
        render_card(RobCard(title="T", body="B"), view=view)


def test_title_uses_h2_markdown():
    msg = render_card(RobCard(title="Hello", body="Body"))
    assert "## Hello" in str(msg.view.children[0].children[0].content)


def test_add_card_actions_puts_buttons_in_top_level_action_row():
    msg = throne_setup_card("hello")
    add_card_actions(msg.view, discord.ui.Button(label="Continue"), discord.ui.Button(label="Not Now"))
    assert type(msg.view.children[0]).__name__ == "Container"
    assert type(msg.view.children[1]).__name__ == "ActionRow"
    assert type(msg.view.children[1].children[0]).__name__ == "Button"
    assert type(msg.view.children[1].children[1]).__name__ == "Button"


def test_build_action_row_wraps_buttons():
    row = build_action_row(discord.ui.Button(label="A"), discord.ui.Button(label="B"))
    assert type(row).__name__ == "ActionRow"
    assert len(row.children) == 2


def test_card_without_footer_renders_no_footer_line():
    msg = render_card(RobCard(title="T", body="B", footer=None))
    contents = "\n".join(str(getattr(ch, "content", "")) for ch in msg.view.children[0].children)
    assert "-#" not in contents


def test_card_with_explicit_footer_renders_footer_line():
    msg = render_card(RobCard(title="T", body="B", footer="Rob kept the paperwork tidy."))
    contents = "\n".join(str(getattr(ch, "content", "")) for ch in msg.view.children[0].children)
    assert "-# Rob kept the paperwork tidy." in contents
