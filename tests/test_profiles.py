"""Focused regressions for profile contracts and V2 renderers."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from bill.components.profile import (
    DM_STATUS_OPTIONS,
    ORIENTATION_LABELS,
    PROFILE_WIZARD_BUTTON_ACTIONS,
    PROFILE_WIZARD_SELECT_ACTIONS,
    DmStatusSelect,
    IdentityModal,
    MemberPresentation,
    ProfileSelectDynamic,
    ProfileWizardDynamic,
    _identity_values,
    _partial_identity_values,
    profile_intro_view,
    profile_wizard_view,
    wizard_custom_id,
)
from bill.components.public_profile import profile_links_view, public_profile_view
from bill.components.setup import GuildPresentation, setup_custom_id, setup_view
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
    SendStat,
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


def identity_draft(
    *,
    status: DmStatus | None = None,
    scope: DraftScope = DraftScope.GLOBAL,
    mode: ServerProfileMode | None = None,
    overrides: tuple[str, ...] = (),
) -> ProfileDraft:
    state = draft(next_step=DraftStepKey.IDENTITY)
    return replace(
        state,
        target_scope=scope,
        guild_id="2" if scope is DraftScope.SERVER else None,
        server_mode=mode,
        current_step=DraftStepKey.IDENTITY,
        governing_orientation=Orientation.SWITCH_DOMME,
        document=replace(
            state.document,
            dm_status=status,
            overridden_fields=overrides,
        ),
    )


def _all_items(view: discord.ui.LayoutView) -> list[discord.ui.Item[Any]]:
    return list(view.walk_children())


def _rows(view: discord.ui.LayoutView) -> list[discord.ui.ActionRow[Any]]:
    return [
        item
        for item in _all_items(view)
        if isinstance(item, discord.ui.ActionRow)
    ]


def _profile(*, empty: bool = False) -> PublicProfile:
    return PublicProfile(
        DraftScope.GLOBAL,
        None,
        "1",
        Orientation.SWITCH_DOMME,
        DmStatus.OPEN,
        None if empty else "@everyone **not markup**",
        not empty,
        ProfileSelections(
            () if empty else ("She/Her",),
            () if empty else ("Goddess",),
            () if empty else ("Brat",),
        ),
        () if empty else ("safe_alias",),
        ()
        if empty
        else (
            ProfileLink(
                "payment",
                "Throne",
                "Tribute",
                None,
                "https://throne.com/a",
                LinkType.PAYMENT,
            ),
            ProfileLink(
                "social",
                "Bluesky",
                "Social",
                None,
                "https://bsky.app/profile/example.com",
                LinkType.SOCIAL,
            ),
        ),
        None if empty else "payment",
        not empty,
        None if empty else (SendStat("USD", 2, 1234), SendStat("EUR", 1, 500)),
        1,
        None,
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
    view = profile_wizard_view(
        draft(),
        presentation=MemberPresentation("Display Name", "https://cdn.example/avatar.png"),
    )

    assert isinstance(view, discord.ui.LayoutView)
    assert len(ORIENTATION_LABELS) == 4
    encoded = str(view.to_components())
    assert "-# Bill Profile Setup" in encoded
    sections = [
        item for item in _all_items(view) if isinstance(item, discord.ui.Section)
    ]
    assert len(sections) == 1
    assert isinstance(sections[0].accessory, discord.ui.Thumbnail)
    assert "bill:p:rdraft_1:1:2:3:orientation" in encoded


def test_public_profile_escapes_bio_and_exposes_only_safe_link_controls() -> None:
    view = public_profile_view(
        _profile(),
        guild_id=2,
        owner_view=True,
        presentation=MemberPresentation(
            "Display @everyone",
            "https://cdn.example/avatar.png",
        ),
    )

    encoded = str(view.to_components())
    assert "-# Bill Profile" in encoded
    assert "Payment Links" in encoded
    assert "Socials" in encoded
    assert "Edit" in encoded
    assert "@everyone" not in encoded
    assert "Pronouns" in encoded
    assert "Honourifics" in encoded
    assert "Submissive labels" in encoded
    assert "Aliases" in encoded
    assert "Throne" in encoded
    assert "USD" in encoded and "EUR" in encoded
    assert len(
        [item for item in _all_items(view) if isinstance(item, discord.ui.Separator)]
    ) == 2
    section = next(item for item in _all_items(view) if isinstance(item, discord.ui.Section))
    assert isinstance(section.accessory, discord.ui.Thumbnail)


def test_public_profile_hides_empty_sections_and_viewer_edit_control() -> None:
    view = public_profile_view(
        _profile(empty=True),
        guild_id=2,
        owner_view=False,
        presentation=MemberPresentation("Display Name"),
    )

    encoded = str(view.to_components())
    for hidden in (
        "Pronouns",
        "Honourifics",
        "Submissive labels",
        "Aliases",
        "Throne",
        "Edit",
    ):
        assert hidden not in encoded
    assert not any(isinstance(item, discord.ui.Thumbnail) for item in _all_items(view))
    assert len(
        [item for item in _all_items(view) if isinstance(item, discord.ui.Separator)]
    ) == 1


def test_public_profile_viewer_keeps_link_controls_without_owner_edit() -> None:
    view = public_profile_view(
        _profile(),
        guild_id=2,
        owner_view=False,
        presentation=MemberPresentation("Display Name"),
    )

    encoded = str(view.to_components())
    assert "Payment Links" in encoded and "Socials" in encoded
    assert "'label': 'Edit'" not in encoded


@pytest.mark.parametrize("kind", [LinkType.PAYMENT, LinkType.SOCIAL])
def test_link_detail_uses_member_section_and_groups_five_buttons_per_row(
    kind: LinkType,
) -> None:
    links = tuple(
        ProfileLink(
            f"link-{index}",
            "Platform",
            f"Link {index}",
            None,
            f"https://example.com/{index}",
            kind,
        )
        for index in range(12)
    )

    view = profile_links_view(
        links,
        kind=kind,
        presentation=MemberPresentation("Display Name", "https://cdn.example/avatar.png"),
    )

    assert [len(row.children) for row in _rows(view)] == [5, 5, 2]
    assert any(isinstance(item, discord.ui.Section) for item in _all_items(view))
    assert any(isinstance(item, discord.ui.Thumbnail) for item in _all_items(view))
    assert "<:" not in str(view.to_components())
    assert "<a:" not in str(view.to_components())


def test_link_detail_rejects_non_https_buttons_and_handles_missing_avatar() -> None:
    links = (
        ProfileLink("bad", "Bad", "Unsafe", None, "http://example.com", LinkType.SOCIAL),
        ProfileLink("good", "Good", "Safe", None, "https://example.com", LinkType.SOCIAL),
    )

    view = profile_links_view(
        links,
        kind=LinkType.SOCIAL,
        presentation=MemberPresentation("Bill member"),
    )

    assert [len(row.children) for row in _rows(view)] == [1]
    assert "Unsafe" not in str(view.to_components())
    assert not any(isinstance(item, discord.ui.Thumbnail) for item in _all_items(view))


@pytest.mark.parametrize("step", list(DraftStepKey))
def test_every_profile_wizard_state_has_compact_v2_structure(step: DraftStepKey) -> None:
    state = replace(
        draft(next_step=step),
        current_step=step,
        governing_orientation=Orientation.SWITCH_DOMME,
        steps=(
            DraftStep(DraftStepKey.ORIENTATION, "completed", None),
            DraftStep(step, "pending", None),
        ),
    )

    view = profile_wizard_view(
        state,
        presentation=MemberPresentation("Display Name", "https://cdn.example/avatar.png"),
    )

    encoded = str(view.to_components())
    assert "-# Bill Profile Setup" in encoded
    assert "Current step" in encoded
    assert "Orientation" in encoded
    assert any(isinstance(item, discord.ui.Section) for item in _all_items(view))
    assert len(_all_items(view)) <= 40
    assert all(len(row.children) <= 5 for row in _rows(view))
    assert "<:" not in encoded and "<a:" not in encoded


def test_profile_wizard_gracefully_handles_missing_avatar() -> None:
    view = profile_wizard_view(
        draft(),
        presentation=MemberPresentation("Display Name"),
    )

    assert "Display Name" in str(view.to_components())
    assert not any(isinstance(item, discord.ui.Thumbnail) for item in _all_items(view))


def test_profile_wizard_collapses_throne_state_without_exposing_creator_id() -> None:
    state = replace(
        draft(next_step=DraftStepKey.REVIEW),
        current_step=DraftStepKey.REVIEW,
        governing_orientation=Orientation.DOMME,
        steps=(DraftStep(DraftStepKey.THRONE, "completed", None),),
        document=replace(draft().document, throne_creator_id="private_creator_id"),
    )

    encoded = str(profile_wizard_view(state).to_components())

    assert "Throne connected" in encoded
    assert "private_creator_id" not in encoded


def test_profile_intro_start_control_is_persistent_and_disjoint() -> None:
    view = profile_intro_view(draft())
    button = view.children[0]

    assert view.timeout is None
    assert isinstance(button, discord.ui.Button)
    assert button.label == "Start"
    assert button.custom_id == wizard_custom_id(draft(), "start")
    assert _matching_profile_dispatchers(button.custom_id) == [ProfileWizardDynamic]


@pytest.mark.asyncio
async def test_profile_start_reloads_draft_after_restart_before_rendering() -> None:
    loaded: list[tuple[str, int]] = []

    class Worker:
        async def get_draft(self, draft_id: str, *, owner_user_id: int) -> ProfileDraft:
            loaded.append((draft_id, owner_user_id))
            return draft()

    class Bot:
        def require_worker(self) -> Worker:
            return Worker()

    class StartResponse:
        def __init__(self) -> None:
            self.content: str | None = "unchanged"
            self.view: discord.ui.LayoutView | None = None

        async def edit_message(
            self,
            *,
            content: str | None,
            view: discord.ui.LayoutView,
        ) -> None:
            self.content, self.view = content, view

    response = StartResponse()
    interaction = SimpleNamespace(
        client=Bot(),
        user=SimpleNamespace(id=1, display_name="Display Name", display_avatar=None),
        guild_id=None,
        message=object(),
        response=response,
    )
    item = discord.ui.Button(
        label="Start",
        custom_id=wizard_custom_id(draft(), "start"),
    )
    dynamic = ProfileWizardDynamic(item, "draft_1", "1", "2", 3, "start")

    await dynamic.callback(interaction)  # type: ignore[arg-type]

    assert loaded == [("draft_1", 1)]
    assert response.content is None
    assert response.view is not None
    assert "-# Bill Profile Setup" in str(response.view.to_components())


@pytest.mark.parametrize(
    ("status", "step", "channel_id", "expected"),
    [
        ("active", "select_channel", None, "Select a text channel"),
        ("active", "confirm", "3", "Confirm setup"),
        ("completed", "complete", "3", "Bill is ready"),
    ],
)
def test_every_setup_state_has_server_section_and_expected_controls(
    status: str,
    step: str,
    channel_id: str | None,
    expected: str,
) -> None:
    session = GuildSetupSession(
        "setup", "2", "1", status, step, channel_id, 4, None, None, None, None, None
    )

    view = setup_view(
        session,
        presentation=GuildPresentation(
            "Server Name",
            "Admin Name",
            "https://cdn.example/icon.png",
        ),
    )

    encoded = str(view.to_components())
    assert "-# Bill Server Setup" in encoded
    assert expected in encoded
    section = next(item for item in _all_items(view) if isinstance(item, discord.ui.Section))
    assert isinstance(section.accessory, discord.ui.Thumbnail)
    assert all(len(row.children) <= 5 for row in _rows(view))
    assert "<:" not in encoded and "<a:" not in encoded


def test_setup_view_gracefully_handles_missing_guild_icon() -> None:
    session = GuildSetupSession(
        "setup", "2", "1", "active", "select_channel", None, 4, None, None, None, None, None
    )

    view = setup_view(
        session,
        presentation=GuildPresentation("Server Name", "Admin Name"),
    )

    assert "Server Name" in str(view.to_components())
    assert not any(isinstance(item, discord.ui.Thumbnail) for item in _all_items(view))


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
        replace(identity_draft(status=DmStatus.OPEN), governing_orientation=orientation),
        "She/Her",
        ",".join(honourifics),
        ",".join(labels),
        ",".join(aliases),
        "on",
        "hello",
    )

    assert values["honourifics"] == honourifics
    assert values["submissive_labels"] == labels
    assert values["aliases"] == aliases
    assert values["public_send_stats"] is stats


def test_linked_identity_can_inherit_every_field_sparsely() -> None:
    linked = identity_draft(scope=DraftScope.SERVER, mode=ServerProfileMode.LINKED)

    values = _identity_values(linked, "", "", "", "", "inherit", "")

    assert values["overrides"] == []
    assert values["dm_status"] is None
    assert values["bio"] is None


def test_dm_status_menu_has_exact_options_and_no_implicit_global_default() -> None:
    select = DmStatusSelect(identity_draft())

    assert [(option.label, option.value) for option in select.options] == [
        (label, status.value) for status, label, _ in DM_STATUS_OPTIONS
    ]
    assert not any(option.default for option in select.options)


@pytest.mark.parametrize(
    ("scope", "mode"),
    [
        (DraftScope.GLOBAL, None),
        (DraftScope.SERVER, ServerProfileMode.INDEPENDENT),
    ],
)
def test_non_linked_dm_status_menu_defaults_only_to_saved_choice(
    scope: DraftScope,
    mode: ServerProfileMode | None,
) -> None:
    select = DmStatusSelect(
        identity_draft(status=DmStatus.AFTER_TRIBUTE, scope=scope, mode=mode)
    )

    assert len(select.options) == 4
    assert [option.value for option in select.options if option.default] == ["after_tribute"]


def test_linked_dm_status_menu_defaults_to_inheritance_or_explicit_override() -> None:
    linked = identity_draft(scope=DraftScope.SERVER, mode=ServerProfileMode.LINKED)
    inherited = DmStatusSelect(linked)
    overridden = DmStatusSelect(
        identity_draft(
            status=DmStatus.CLOSED,
            scope=DraftScope.SERVER,
            mode=ServerProfileMode.LINKED,
            overrides=("dm_status",),
        )
    )

    assert [(option.label, option.value) for option in inherited.options][-1] == (
        "Use global setting",
        "inherit",
    )
    assert [option.value for option in inherited.options if option.default] == ["inherit"]
    assert [option.value for option in overridden.options if option.default] == ["closed"]
    assert "Use global setting keeps this server's DM status linked" in str(
        profile_wizard_view(linked).to_components()
    )


def test_other_partial_identity_selection_does_not_default_dm_status() -> None:
    values = _partial_identity_values(identity_draft(), "pronouns", ("She/Her",))

    assert values["pronouns"] == ["She/Her"]
    assert values["dm_status"] is None
    assert values["complete"] is False


def test_dm_status_partial_mutation_preserves_other_identity_fields() -> None:
    state = identity_draft()
    state = replace(
        state,
        document=replace(
            state.document,
            bio="Existing bio",
            public_send_stats=True,
            selections=ProfileSelections(("They/Them",), ("Goddess",), ("Brat",)),
            aliases=("alias",),
        ),
    )

    values = _partial_identity_values(state, "dm-status", ("by_request",))

    assert values == {
        "pronouns": ["They/Them"],
        "honourifics": ["Goddess"],
        "submissive_labels": ["Brat"],
        "dm_status": "by_request",
        "bio": "Existing bio",
        "public_send_stats": True,
        "aliases": ["alias"],
        "complete": False,
    }


def test_linked_inherit_partial_removes_only_dm_status_override() -> None:
    state = identity_draft(
        status=DmStatus.CLOSED,
        scope=DraftScope.SERVER,
        mode=ServerProfileMode.LINKED,
        overrides=("dm_status", "bio"),
    )

    values = _partial_identity_values(state, "dm-status", ("inherit",))

    assert values["dm_status"] is None
    assert values["overrides"] == ["bio"]
    assert values["complete"] is False


def test_identity_completion_requires_non_linked_dm_status() -> None:
    with pytest.raises(ValueError, match="choose a DM status from the menu"):
        _identity_values(identity_draft(), "", "", "", "", "off", "")


def test_identity_modal_contains_no_dm_status_or_pipe_delimited_input() -> None:
    modal = IdentityModal(identity_draft(status=DmStatus.OPEN), object())  # type: ignore[arg-type]
    labels = [item.label for item in modal.children if isinstance(item, discord.ui.TextInput)]

    assert labels == [
        "Aliases, comma separated (- clears)",
        "Public send stats: on/off/inherit",
        "Bio (- clears; blank inherits when linked)",
    ]
    assert all("DM status" not in label and "|" not in label for label in labels)


@pytest.mark.asyncio
async def test_dm_status_select_persists_revision_bound_partial_mutation() -> None:
    state = identity_draft()
    calls: list[dict[str, object]] = []

    class Worker:
        async def get_draft(self, draft_id: str, *, owner_user_id: int) -> ProfileDraft:
            assert (draft_id, owner_user_id) == ("draft_1", 1)
            return state

        async def update_draft_step(self, draft_id: str, **kwargs: object) -> ProfileDraft:
            calls.append({"draft_id": draft_id, **kwargs})
            return replace(
                state,
                revision=4,
                document=replace(state.document, dm_status=DmStatus.CLOSED),
            )

    class Bot:
        def require_worker(self) -> Worker:
            return Worker()

    class SelectResponse:
        async def edit_message(self, *, view: discord.ui.LayoutView) -> None:
            assert "Closed" in str(view.to_components())

    interaction = SimpleNamespace(
        client=Bot(),
        user=SimpleNamespace(id=1, display_name="Display Name", display_avatar=None),
        guild_id=None,
        response=SelectResponse(),
    )
    item = discord.ui.Select(
        custom_id=wizard_custom_id(state, "identity-dm-status"),
        options=[discord.SelectOption(label="Closed", value="closed")],
    )
    item._values = ["closed"]
    dynamic = ProfileSelectDynamic(item, "draft_1", "1", "2", 3, "identity-dm-status")

    await dynamic.callback(interaction)  # type: ignore[arg-type]

    assert calls == [
        {
            "draft_id": "draft_1",
            "step": DraftStepKey.IDENTITY,
            "owner_user_id": 1,
            "expected_revision": 3,
            "values": {
                "pronouns": [],
                "honourifics": [],
                "submissive_labels": [],
                "dm_status": "closed",
                "bio": None,
                "public_send_stats": False,
                "aliases": [],
                "complete": False,
            },
        }
    ]


@pytest.mark.asyncio
async def test_dm_status_select_rejects_stale_revision_before_mutation() -> None:
    state = identity_draft()
    messages: list[str] = []

    class Worker:
        async def get_draft(self, _draft_id: str, *, owner_user_id: int) -> ProfileDraft:
            assert owner_user_id == 1
            return replace(state, revision=4)

        async def update_draft_step(self, *_: object, **__: object) -> ProfileDraft:
            raise AssertionError("stale controls must not mutate drafts")

    class Bot:
        def require_worker(self) -> Worker:
            return Worker()

    class StaleResponse:
        async def send_message(self, content: str, *, ephemeral: bool) -> None:
            assert ephemeral
            messages.append(content)

    interaction = SimpleNamespace(
        client=Bot(),
        user=SimpleNamespace(id=1),
        guild_id=None,
        response=StaleResponse(),
    )
    item = discord.ui.Select(
        custom_id=wizard_custom_id(state, "identity-dm-status"),
        options=[discord.SelectOption(label="Closed", value="closed")],
    )
    item._values = ["closed"]
    dynamic = ProfileSelectDynamic(item, "draft_1", "1", "2", 3, "identity-dm-status")

    await dynamic.callback(interaction)  # type: ignore[arg-type]

    assert messages == ["That profile control is stale. Please use the latest wizard message."]


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

    assert all(
        len(wizard_custom_id(realistic, action)) <= 100
        for action in (*PROFILE_WIZARD_BUTTON_ACTIONS, *PROFILE_WIZARD_SELECT_ACTIONS)
    )
    assert len(setup_custom_id(setup, "complete")) <= 100
