"""Focused regressions for profile contracts and V2 renderers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import discord
import pytest

from bill.components.profile import (
    ORIENTATION_LABELS,
    PROFILE_WIZARD_BUTTON_ACTIONS,
    PROFILE_WIZARD_SELECT_ACTIONS,
    ProfileSelectDynamic,
    ProfileWizardDynamic,
    _identity_values,
    profile_wizard_view,
    wizard_custom_id,
)
from bill.components.public_profile import public_profile_view
from bill.components.setup import setup_custom_id, setup_view
from bill.worker_client import (
    CreateLinkImportResult,
    DmStatus,
    DraftDocument,
    DraftScope,
    DraftStatus,
    DraftStep,
    DraftStepKey,
    GuildSetupSession,
    LinkType,
    Orientation,
    ProfileDraft,
    ProfileLink,
    ProfileSelections,
    PublicProfile,
    ServerProfileMode,
    WorkerClient,
)


class Response:
    def __init__(self, payload: Any) -> None:
        self.status = 200
        self.payload = payload

    async def __aenter__(self) -> Response:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def json(self) -> Any:
        return self.payload


class Session:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.last_kwargs: dict[str, Any] = {}

    def request(self, *_: object, **kwargs: Any) -> Response:
        self.last_kwargs = kwargs
        return Response(self.payload)


def draft(*, next_step: DraftStepKey | None = DraftStepKey.ORIENTATION) -> ProfileDraft:
    return ProfileDraft(
        id="draft_1",
        owner_user_id="1",
        origin_guild_id="2",
        target_scope=DraftScope.GLOBAL,
        guild_id=None,
        server_mode=None,
        status=DraftStatus.ACTIVE,
        revision=3,
        base_version=0,
        current_step=next_step,
        next_step=next_step,
        steps=(DraftStep(DraftStepKey.ORIENTATION, "pending", None),),
        governing_orientation=None,
        document=DraftDocument(
            None,
            None,
            False,
            ProfileSelections((), (), ()),
            (),
            (),
            (),
            (),
            None,
            None,
        ),
        throne_prefill=None,
        created_at=None,
        updated_at=None,
        published_at=None,
    )


@pytest.mark.asyncio
async def test_profile_lookup_is_parsed_into_frozen_contracts() -> None:
    payload = {
        "ok": True,
        "data": {
            "global_available": True,
            "profile": {
                "scope": "global",
                "mode": None,
                "owner_user_id": "1",
                "orientation": "domme",
                "dm_status": "open",
                "bio": "hello",
                "public_send_stats": False,
                "selections": {
                    "pronouns": ["She/Her"],
                    "honourifics": ["Goddess"],
                    "submissive_labels": [],
                },
                "aliases": [],
                "links": [
                    {
                        "id": "l",
                        "platform": "Throne",
                        "public_label": "Tribute",
                        "username": "a",
                        "normalized_url": "https://throne.com/a",
                        "link_type": "payment",
                        "sort_order": 0,
                    }
                ],
                "preferred_payment_link_id": "l",
                "throne_connected": True,
                "send_stats": None,
                "version": 4,
                "published_at": "2026-01-01T00:00:00Z",
            },
        },
    }
    session = Session(payload)
    client = WorkerClient(base_url="https://usebill.dev", api_token="secret", session=session)  # type: ignore[arg-type]

    lookup = await client.get_profile(guild_id=2, user_id=1)

    assert lookup.profile is not None
    assert lookup.profile.orientation is Orientation.DOMME
    assert lookup.profile.links[0].link_type is LinkType.PAYMENT
    assert session.last_kwargs["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_link_import_creation_uses_the_mutation_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "ok": True,
        "data": {
            "import": {
                "id": "import_1",
                "draft_id": "draft_1",
                "source_url": "https://example.com",
                "provider": "generic",
                "status": "ready",
                "candidates": [],
            },
            "draft": {"id": "draft_1", "revision": 4},
        },
    }
    client = WorkerClient(
        base_url="https://usebill.dev",
        api_token="secret",
        session=Session(payload),  # type: ignore[arg-type]
    )
    updated = replace(draft(), revision=4)

    async def get_updated_draft(*_: object, **__: object) -> ProfileDraft:
        return updated

    monkeypatch.setattr(client, "get_draft", get_updated_draft)

    result = await client.create_link_import(
        "draft_1",
        owner_user_id=1,
        expected_revision=3,
        source_url="https://example.com",
    )

    assert isinstance(result, CreateLinkImportResult)
    assert result.draft.revision == 4


def test_orientation_wizard_uses_v2_container_and_all_four_options() -> None:
    view = profile_wizard_view(draft())

    assert isinstance(view, discord.ui.LayoutView)
    assert len(ORIENTATION_LABELS) == 4
    encoded = str(view.to_components())
    assert "bill:p:rdraft_1:1:2:3:orientation" in encoded


def test_public_profile_escapes_bio_and_exposes_only_safe_link_controls() -> None:
    profile = PublicProfile(
        DraftScope.GLOBAL,
        None,
        "1",
        Orientation.DOMME,
        DmStatus.OPEN,
        "@everyone **not markup**",
        False,
        ProfileSelections(("She/Her",), (), ()),
        (),
        (ProfileLink("link", "Throne", "Tribute", None, "https://throne.com/a", LinkType.PAYMENT),),
        "link",
        True,
        None,
        1,
        None,
    )

    view = public_profile_view(profile, guild_id=2, owner_view=True)

    encoded = str(view.to_components())
    assert "Payment Links" in encoded
    assert "Edit" in encoded
    assert "@everyone" not in encoded


def test_setup_view_collapses_completed_channel() -> None:
    session = GuildSetupSession(
        "setup", "2", "1", "active", "confirm", "3", 4, None, None, None, None, None
    )

    assert "Confirm setup" in str(setup_view(session).to_components())


@pytest.mark.parametrize(
    ("orientation", "honourifics", "labels", "aliases", "stats"),
    [
        (Orientation.DOMME, ["Goddess"], [], [], False),
        (Orientation.SUBMISSIVE, [], ["Pet"], ["name"], True),
        (Orientation.SWITCH_DOMME, ["Mistress"], ["Brat"], ["name"], True),
        (Orientation.SWITCH_SUBMISSIVE, ["Mommy"], ["Sub"], ["name"], True),
    ],
)
def test_identity_payload_obeys_each_orientation_capability(
    orientation: Orientation,
    honourifics: list[str],
    labels: list[str],
    aliases: list[str],
    stats: bool,
) -> None:
    values = _identity_values(
        replace(draft(), governing_orientation=orientation),
        "She/Her",
        ",".join(honourifics),
        ",".join(labels),
        ",".join(aliases),
        "open | on | hello",
    )

    assert values["honourifics"] == honourifics
    assert values["submissive_labels"] == labels
    assert values["aliases"] == aliases
    assert values["public_send_stats"] is stats


def test_linked_identity_can_inherit_every_field_sparsely() -> None:
    linked = replace(
        draft(),
        target_scope=DraftScope.SERVER,
        guild_id="2",
        server_mode=ServerProfileMode.LINKED,
        governing_orientation=Orientation.SWITCH_DOMME,
    )

    values = _identity_values(linked, "", "", "", "", "inherit | inherit | ")

    assert values["overrides"] == []
    assert values["dm_status"] is None
    assert values["bio"] is None


def test_setup_custom_id_binds_initiator_guild_and_revision() -> None:
    session = GuildSetupSession(
        "setup",
        "2",
        "1",
        "active",
        "select_channel",
        None,
        4,
        None,
        None,
        None,
        None,
        None,
    )

    custom_id = setup_custom_id(session, "channel")

    assert custom_id == "bill:s:rsetup:1:2:4:channel"
    assert len(custom_id) <= 100


def _matching_profile_dispatchers(custom_id: str) -> list[type[discord.ui.DynamicItem]]:
    dispatchers = [ProfileWizardDynamic, ProfileSelectDynamic]
    return [
        dispatcher
        for dispatcher in dispatchers
        if dispatcher.__discord_ui_compiled_template__.fullmatch(custom_id)
    ]


@pytest.mark.parametrize("action", PROFILE_WIZARD_SELECT_ACTIONS)
def test_profile_select_actions_match_only_select_dispatcher(action: str) -> None:
    custom_id = wizard_custom_id(draft(), action)

    assert _matching_profile_dispatchers(custom_id) == [ProfileSelectDynamic]


@pytest.mark.parametrize("action", PROFILE_WIZARD_BUTTON_ACTIONS)
def test_profile_button_actions_match_only_button_dispatcher(action: str) -> None:
    custom_id = wizard_custom_id(draft(), action)

    assert _matching_profile_dispatchers(custom_id) == [ProfileWizardDynamic]


def test_every_emittable_profile_action_has_exactly_one_dispatcher() -> None:
    actions = (*PROFILE_WIZARD_BUTTON_ACTIONS, *PROFILE_WIZARD_SELECT_ACTIONS)

    assert len(actions) == len(set(actions))
    assert all(
        len(_matching_profile_dispatchers(wizard_custom_id(draft(), action))) == 1
        for action in actions
    )
    with pytest.raises(ValueError, match="Unsupported Bill profile component action"):
        wizard_custom_id(draft(), "unknown")


def test_realistic_persistent_ids_fit_discord_limit() -> None:
    realistic = replace(
        draft(),
        id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        owner_user_id="9999999999999999999",
        origin_guild_id="9999999999999999999",
        revision=2_147_483_647,
    )
    setup = GuildSetupSession(
        "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "9999999999999999999",
        "9999999999999999999",
        "active",
        "confirm",
        "9999999999999999999",
        2_147_483_647,
        None,
        None,
        None,
        None,
        None,
    )

    assert len(wizard_custom_id(realistic, "complete-links")) <= 100
    assert len(setup_custom_id(setup, "complete")) <= 100
