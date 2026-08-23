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
    PROFILE_COLOR_OPTIONS,
    PROFILE_WIZARD_BUTTON_ACTIONS,
    PROFILE_WIZARD_SELECT_ACTIONS,
    AliasModal,
    BioModal,
    DmStatusSelect,
    LinkModal,
    MemberPresentation,
    ProfileColorModal,
    ProfileColorSelect,
    ProfileModalLauncherView,
    ProfileSelectDynamic,
    ProfileWizardDynamic,
    StatsSelect,
    _partial_identity_values,
    profile_intro_view,
    profile_wizard_view,
    wizard_custom_id,
    wizard_stages,
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
    ThronePending,
    WizardStage,
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
        dm_status_selected=False,
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
    dm_status_selected: bool | None = None,
) -> ProfileDraft:
    state = draft(next_step=DraftStepKey.IDENTITY)
    return replace(
        state,
        target_scope=scope,
        guild_id="2" if scope is DraftScope.SERVER else None,
        server_mode=mode,
        current_step=DraftStepKey.IDENTITY,
        dm_status_selected=(
            status is not None or mode is ServerProfileMode.LINKED
            if dm_status_selected is None
            else dm_status_selected
        ),
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
    return [item for item in _all_items(view) if isinstance(item, discord.ui.ActionRow)]


def _profile(*, empty: bool = False, profile_color: int | None = None) -> PublicProfile:
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
        profile_color,
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
                "profile_color": 0x5865F2,
            },
        },
    }
    session = Session(payload)
    client = WorkerClient(base_url="https://usebill.dev", api_token="secret", session=session)  # type: ignore[arg-type]

    lookup = await client.get_profile(guild_id=2, user_id=1)

    assert lookup.profile is not None
    assert lookup.profile.orientation is Orientation.DOMME
    assert lookup.profile.profile_color == 0x5865F2
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
    sections = [item for item in _all_items(view) if isinstance(item, discord.ui.Section)]
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
    assert len([item for item in _all_items(view) if isinstance(item, discord.ui.Separator)]) == 2
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
    assert len([item for item in _all_items(view) if isinstance(item, discord.ui.Separator)]) == 1


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
    assert "Step " in encoded
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

    assert "Throne:** Connected" in encoded
    assert "private_creator_id" not in encoded


def test_review_exposes_revision_bound_dm_status_editor_alongside_identity_modal() -> None:
    state = replace(
        identity_draft(status=DmStatus.OPEN),
        current_step=DraftStepKey.REVIEW,
        next_step=DraftStepKey.REVIEW,
        steps=(
            DraftStep(DraftStepKey.IDENTITY, "completed", None),
            DraftStep(DraftStepKey.REVIEW, "pending", None),
        ),
    )

    items = _all_items(profile_wizard_view(state))
    dm_selects = [item for item in items if isinstance(item, DmStatusSelect)]
    edit_buttons = [
        item
        for item in items
        if isinstance(item, discord.ui.Button) and item.label == "Edit identity"
    ]

    assert len(dm_selects) == 1
    assert dm_selects[0].custom_id == wizard_custom_id(state, "identity-dm-status")
    assert [option.value for option in dm_selects[0].options if option.default] == ["open"]
    assert len(edit_buttons) == 1


def test_throne_confirmation_screen_survives_restart_without_secret_material() -> None:
    state = replace(
        draft(next_step=DraftStepKey.THRONE),
        current_step=DraftStepKey.THRONE,
        governing_orientation=Orientation.DOMME,
        wizard_stage=WizardStage.THRONE,
        wizard_substep="confirm",
        throne_pending=ThronePending("resolvedqueen", "2026-08-23T12:00:00Z"),
    )

    encoded = str(profile_wizard_view(state).to_components())

    assert "@resolvedqueen" in encoded
    assert "Yes, connect this handle" in encoded
    assert "Try another handle" in encoded
    assert "confirmation_token" not in encoded
    assert "/t/" not in encoded


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
            self.deferred = False

        async def defer(self) -> None:
            self.deferred = True

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
        edit_original_response=response.edit_message,
    )
    item = discord.ui.Button(
        label="Start",
        custom_id=wizard_custom_id(draft(), "start"),
    )
    dynamic = ProfileWizardDynamic(item, "draft_1", "1", "2", 3, "start")

    await dynamic.callback(interaction)  # type: ignore[arg-type]

    assert loaded == [("draft_1", 1)]
    assert response.deferred
    assert response.content is None
    assert response.view is not None
    assert "-# Bill Profile Setup" in str(response.view.to_components())


@pytest.mark.asyncio
async def test_modal_actions_acknowledge_before_loading_and_preserve_defaults() -> None:
    state = replace(
        identity_draft(),
        wizard_stage=WizardStage.BIO,
        document=replace(identity_draft().document, bio="Saved bio"),
    )
    events: list[str] = []
    launcher: ProfileModalLauncherView | None = None

    class Worker:
        async def get_draft(self, draft_id: str, *, owner_user_id: int) -> ProfileDraft:
            assert (draft_id, owner_user_id) == (state.id, 1)
            assert events == ["defer"]
            events.append("load")
            return state

    class Bot:
        def require_worker(self) -> Worker:
            return Worker()

    class DynamicResponse:
        async def defer(self) -> None:
            events.append("defer")

        def is_done(self) -> bool:
            return True

    class Followup:
        async def send(
            self,
            _content: str,
            *,
            view: discord.ui.View | None,
            ephemeral: bool,
        ) -> None:
            nonlocal launcher
            assert ephemeral
            assert isinstance(view, ProfileModalLauncherView)
            launcher = view

    interaction = SimpleNamespace(
        client=Bot(),
        user=SimpleNamespace(id=1),
        guild_id=None,
        message=object(),
        response=DynamicResponse(),
        followup=Followup(),
    )
    item = discord.ui.Button(label="Edit bio", custom_id=wizard_custom_id(state, "bio"))
    dynamic = ProfileWizardDynamic(item, state.id, "1", state.origin_guild_id, 3, "bio")

    await dynamic.callback(interaction)  # type: ignore[arg-type]

    assert events == ["defer", "load"]
    assert launcher is not None

    class ModalResponse:
        async def send_modal(self, modal: discord.ui.Modal) -> None:
            assert isinstance(modal, BioModal)
            assert modal.bio.default == "Saved bio"

    await launcher.open_modal(  # type: ignore[arg-type]
        SimpleNamespace(response=ModalResponse())
    )


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
    select = DmStatusSelect(identity_draft(status=DmStatus.AFTER_TRIBUTE, scope=scope, mode=mode))

    assert len(select.options) == 4
    assert [option.value for option in select.options if option.default] == ["after_tribute"]


def test_legacy_implicit_open_is_not_rendered_as_an_explicit_default() -> None:
    select = DmStatusSelect(
        identity_draft(
            status=DmStatus.OPEN,
            dm_status_selected=False,
        )
    )

    assert not any(option.default for option in select.options)


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
    assert "Use global setting" in str(profile_wizard_view(linked).to_components())


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
        "profile_color": None,
        "complete": False,
        "dm_status_selected": True,
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
    assert values["dm_status_selected"] is True


@pytest.mark.asyncio
async def test_dm_status_select_persists_revision_bound_partial_mutation() -> None:
    state = replace(identity_draft(), wizard_stage=WizardStage.DM_STATUS)
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
        async def defer(self) -> None:
            return None

        async def edit_message(self, *, view: discord.ui.LayoutView) -> None:
            assert "Closed" in str(view.to_components())

    response = SelectResponse()
    interaction = SimpleNamespace(
        client=Bot(),
        user=SimpleNamespace(id=1, display_name="Display Name", display_avatar=None),
        guild_id=None,
        response=response,
        edit_original_response=response.edit_message,
    )
    item = discord.ui.Select(
        custom_id=wizard_custom_id(state, "dm-status"),
        options=[discord.SelectOption(label="Closed", value="closed")],
    )
    item._values = ["closed"]
    dynamic = ProfileSelectDynamic(item, "draft_1", "1", "2", 3, "dm-status")

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
                "profile_color": None,
                "complete": False,
                "dm_status_selected": True,
            },
        }
    ]


@pytest.mark.asyncio
async def test_review_dm_status_select_restores_linked_inheritance() -> None:
    state = replace(
        identity_draft(
            status=DmStatus.CLOSED,
            scope=DraftScope.SERVER,
            mode=ServerProfileMode.LINKED,
            overrides=("dm_status", "bio", "submissive_labels", "aliases"),
        ),
        current_step=DraftStepKey.REVIEW,
        next_step=DraftStepKey.REVIEW,
        governing_orientation=Orientation.DOMME,
    )
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
                document=replace(
                    state.document,
                    dm_status=None,
                    overridden_fields=("bio",),
                ),
            )

    class Bot:
        def require_worker(self) -> Worker:
            return Worker()

    class SelectResponse:
        async def edit_message(self, *, view: discord.ui.LayoutView) -> None:
            dm_select = next(
                item for item in view.walk_children() if isinstance(item, DmStatusSelect)
            )
            assert [option.value for option in dm_select.options if option.default] == ["inherit"]

    interaction = SimpleNamespace(
        client=Bot(),
        user=SimpleNamespace(id=1, display_name="Display Name", display_avatar=None),
        guild_id=None,
        response=SelectResponse(),
    )
    item = discord.ui.Select(
        custom_id=wizard_custom_id(state, "identity-dm-status"),
        options=[discord.SelectOption(label="Use global setting", value="inherit")],
    )
    item._values = ["inherit"]
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
                "dm_status": None,
                "bio": None,
                "public_send_stats": False,
                "aliases": [],
                "complete": False,
                "dm_status_selected": True,
                "overrides": ["bio"],
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
        def __init__(self) -> None:
            self.deferred = False

        async def defer(self) -> None:
            self.deferred = True

        def is_done(self) -> bool:
            return self.deferred

        async def send_message(self, content: str, *, ephemeral: bool) -> None:
            assert ephemeral
            messages.append(content)

    class Followup:
        async def send(
            self,
            content: str,
            *,
            view: discord.ui.View | None = None,
            ephemeral: bool,
        ) -> None:
            assert view is None
            assert ephemeral
            messages.append(content)

    interaction = SimpleNamespace(
        client=Bot(),
        user=SimpleNamespace(id=1),
        guild_id=None,
        response=StaleResponse(),
        followup=Followup(),
    )
    item = discord.ui.Select(
        custom_id=wizard_custom_id(state, "dm-status"),
        options=[discord.SelectOption(label="Closed", value="closed")],
    )
    item._values = ["closed"]
    dynamic = ProfileSelectDynamic(item, "draft_1", "1", "2", 3, "dm-status")

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


@pytest.mark.parametrize(
    ("orientation", "expected"),
    [
        (
            Orientation.DOMME,
            (
                WizardStage.ORIENTATION,
                WizardStage.PRONOUNS,
                WizardStage.HONOURIFICS,
                WizardStage.DM_STATUS,
                WizardStage.BIO,
                WizardStage.PROFILE_COLOR,
                WizardStage.LINKS,
                WizardStage.THRONE,
                WizardStage.REVIEW,
            ),
        ),
        (
            Orientation.SUBMISSIVE,
            (
                WizardStage.ORIENTATION,
                WizardStage.PRONOUNS,
                WizardStage.SUBMISSIVE_LABELS,
                WizardStage.DM_STATUS,
                WizardStage.BIO,
                WizardStage.PROFILE_COLOR,
                WizardStage.LINKS,
                WizardStage.DETAILS,
                WizardStage.REVIEW,
            ),
        ),
        (
            Orientation.SWITCH_DOMME,
            tuple(WizardStage),
        ),
        (
            Orientation.SWITCH_SUBMISSIVE,
            tuple(WizardStage),
        ),
    ],
)
def test_conditional_wizard_stage_sequences(
    orientation: Orientation,
    expected: tuple[WizardStage, ...],
) -> None:
    assert wizard_stages(orientation) == expected


def test_linked_wizard_inherits_orientation_but_keeps_conditional_sequence() -> None:
    stages = wizard_stages(Orientation.DOMME, linked=True)

    assert WizardStage.ORIENTATION not in stages
    assert stages[0] is WizardStage.PRONOUNS
    assert WizardStage.THRONE not in stages


@pytest.mark.parametrize("stage", list(WizardStage))
def test_every_guided_stage_is_neutral_restart_safe_and_within_component_limits(
    stage: WizardStage,
) -> None:
    state = replace(
        draft(),
        governing_orientation=Orientation.SWITCH_DOMME,
        wizard_stage=stage,
        document=replace(
            draft().document,
            dm_status=DmStatus.OPEN,
            selections=ProfileSelections(("They/Them",), (), ()),
        ),
    )

    view = profile_wizard_view(state, presentation=MemberPresentation("Member"))
    encoded = str(view.to_components())
    containers = [item for item in view.children if isinstance(item, discord.ui.Container)]

    assert f"Step {wizard_stages(Orientation.SWITCH_DOMME).index(stage) + 1} of " in encoded
    assert containers[0].accent_color is None
    assert all(len(row.children) <= 5 for row in _rows(view))
    assert len(_all_items(view)) <= 40
    assert all(
        len(item.custom_id or "") <= 100
        for item in _all_items(view)
        if isinstance(item, (discord.ui.Button, discord.ui.Select))
    )


def test_bill_palette_is_named_strict_rgb_and_includes_neutral_choice() -> None:
    assert [name for name, _ in PROFILE_COLOR_OPTIONS] == [
        "Blue",
        "Purple",
        "Rose",
        "Red",
        "Orange",
        "Gold",
        "Emerald",
        "Teal",
    ]
    assert all(0 <= value <= 0xFFFFFF for _, value in PROFILE_COLOR_OPTIONS)
    select = ProfileColorSelect(
        replace(
            identity_draft(),
            wizard_stage=WizardStage.PROFILE_COLOR,
        )
    )
    assert select.options[-1].label == "No colour"


def test_linked_colour_distinguishes_inherit_explicit_clear_and_override() -> None:
    linked = replace(
        identity_draft(scope=DraftScope.SERVER, mode=ServerProfileMode.LINKED),
        wizard_stage=WizardStage.PROFILE_COLOR,
    )
    inherited = ProfileColorSelect(linked)
    cleared = ProfileColorSelect(
        replace(
            linked,
            document=replace(
                linked.document,
                overridden_fields=("profile_color",),
            ),
        )
    )
    blue = ProfileColorSelect(
        replace(
            linked,
            document=replace(
                linked.document,
                profile_color=0x5865F2,
                overridden_fields=("profile_color",),
            ),
        )
    )

    assert [option.value for option in inherited.options if option.default] == ["inherit"]
    assert [option.value for option in cleared.options if option.default] == ["none"]
    assert [option.value for option in blue.options if option.default] == ["5865f2"]


def test_linked_review_uses_resolved_global_colour_and_hides_inapplicable_edits() -> None:
    linked = replace(
        identity_draft(scope=DraftScope.SERVER, mode=ServerProfileMode.LINKED),
        wizard_stage=WizardStage.REVIEW,
        resolved_profile_color=0xE0568A,
    )

    view = profile_wizard_view(linked, presentation=MemberPresentation("Member"))
    encoded = str(view.to_components())
    containers = [item for item in view.children if isinstance(item, discord.ui.Container)]

    assert containers[-1].accent_color == discord.Color(0xE0568A)
    assert "Edit orientation" not in encoded
    assert "Edit Throne" not in encoded


def test_free_text_is_confined_to_focused_modals() -> None:
    state = replace(
        identity_draft(status=DmStatus.OPEN),
        wizard_stage=WizardStage.DETAILS,
    )
    bio = BioModal(state, object())  # type: ignore[arg-type]
    aliases = AliasModal(state, object())  # type: ignore[arg-type]
    color = ProfileColorModal(state, object())  # type: ignore[arg-type]
    link = LinkModal(
        state,
        object(),  # type: ignore[arg-type]
        link_type=LinkType.SOCIAL,
    )

    assert len(bio.children) == 1
    assert len(aliases.children) == 1
    assert len(color.children) == 1
    assert all(
        "social or payment" not in str(getattr(item, "label", "")).casefold()
        for item in link.children
    )
    text_fields = (*bio.children, *aliases.children)
    assert not any("|" in str(getattr(item, "label", "")) for item in text_fields)


def test_stats_are_a_menu_not_free_text() -> None:
    state = replace(
        identity_draft(status=DmStatus.OPEN),
        wizard_stage=WizardStage.DETAILS,
    )
    select = StatsSelect(state)

    assert [(option.label, option.value) for option in select.options] == [
        ("Show send stats", "show"),
        ("Hide send stats", "hide"),
    ]


def test_review_preview_and_public_profile_use_selected_accent_only() -> None:
    state = replace(
        identity_draft(status=DmStatus.OPEN),
        governing_orientation=Orientation.SWITCH_DOMME,
        wizard_stage=WizardStage.REVIEW,
        document=replace(identity_draft().document, profile_color=0x2EAD78),
    )
    review = profile_wizard_view(state)
    public = public_profile_view(
        _profile(profile_color=0x2EAD78),
        guild_id=2,
        owner_view=False,
        presentation=MemberPresentation("Member"),
    )

    review_containers = [item for item in review.children if isinstance(item, discord.ui.Container)]
    public_container = next(
        item for item in public.children if isinstance(item, discord.ui.Container)
    )
    assert review_containers[0].accent_color is None
    assert review_containers[1].accent_color == discord.Color(0x2EAD78)
    assert public_container.accent_color == discord.Color(0x2EAD78)


def test_server_setup_container_remains_neutral() -> None:
    session = GuildSetupSession(
        "setup", "2", "1", "active", "select_channel", None, 4, None, None, None, None, None
    )
    view = setup_view(session)
    container = next(item for item in view.children if isinstance(item, discord.ui.Container))

    assert container.accent_color is None
    assert "View Channel" in str(view.to_components())
