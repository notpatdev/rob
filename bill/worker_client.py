"""Typed, secret-safe client for Bill's bearer-protected Worker API.

The Worker owns profile state and revision checks.  This module deliberately turns
its JSON boundary into immutable Python contracts so Discord interaction handlers
cannot accidentally use stale dictionaries or log returned webhook secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import aiohttp

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None


class WorkerAPIError(RuntimeError):
    """A safe Worker failure; ``code`` is suitable for interaction handling."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class Orientation(StrEnum):
    DOMME = "domme"
    SUBMISSIVE = "submissive"
    SWITCH_DOMME = "switch_domme"
    SWITCH_SUBMISSIVE = "switch_submissive"


class DmStatus(StrEnum):
    OPEN = "open"
    BY_REQUEST = "by_request"
    AFTER_TRIBUTE = "after_tribute"
    CLOSED = "closed"


class DraftScope(StrEnum):
    GLOBAL = "global"
    SERVER = "server"


class ServerProfileMode(StrEnum):
    LINKED = "linked"
    INDEPENDENT = "independent"


class DraftStatus(StrEnum):
    ACTIVE = "active"
    PUBLISHED = "published"
    ABANDONED = "abandoned"


class DraftStepKey(StrEnum):
    ORIENTATION = "orientation"
    IDENTITY = "identity"
    LINKS = "links"
    THRONE = "throne"
    REVIEW = "review"


class WizardStage(StrEnum):
    ORIENTATION = "orientation"
    PRONOUNS = "pronouns"
    HONOURIFICS = "honourifics"
    SUBMISSIVE_LABELS = "submissive_labels"
    DM_STATUS = "dm_status"
    BIO = "bio"
    PROFILE_COLOR = "profile_color"
    LINKS = "links"
    THRONE = "throne"
    DETAILS = "details"
    REVIEW = "review"


class LinkType(StrEnum):
    SOCIAL = "social"
    PAYMENT = "payment"


@dataclass(frozen=True, slots=True)
class GuildConfig:
    guild_id: str
    send_channel_id: str


@dataclass(frozen=True, slots=True)
class DommeRegistration:
    """Legacy registration response retained while the bot migrates to profiles."""

    creator_id: str
    throne_handle: str
    webhook_url: str | None
    webhook_state: str


@dataclass(frozen=True, slots=True)
class SendNotification:
    notification_id: str
    lease_token: str
    send_id: str
    guild_id: str
    channel_id: str
    recipient_user_id: str
    throne_handle: str
    amount_minor: int
    currency: str
    sender_name: str | None
    is_private: bool
    is_anonymous: bool
    item_name: str | None
    item_image_url: str | None
    purchased_at: str
    delivery_may_exist: bool


@dataclass(frozen=True, slots=True)
class ProfileSelections:
    pronouns: tuple[str, ...]
    honourifics: tuple[str, ...]
    submissive_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileLink:
    id: str
    platform: str
    public_label: str
    username: str | None
    normalized_url: str
    link_type: LinkType
    enabled: bool = True
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class SendStat:
    currency: str
    count: int
    total_amount_minor: int


@dataclass(frozen=True, slots=True)
class PublicProfile:
    scope: DraftScope
    mode: ServerProfileMode | None
    owner_user_id: str
    orientation: Orientation
    dm_status: DmStatus
    bio: str | None
    public_send_stats: bool
    selections: ProfileSelections
    aliases: tuple[str, ...]
    links: tuple[ProfileLink, ...]
    preferred_payment_link_id: str | None
    throne_connected: bool
    send_stats: tuple[SendStat, ...] | None
    version: int
    published_at: str | None
    profile_color: int | None = None


@dataclass(frozen=True, slots=True)
class ProfileLookup:
    profile: PublicProfile | None
    global_available: bool


@dataclass(frozen=True, slots=True)
class DraftStep:
    key: DraftStepKey
    status: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class DraftDocument:
    dm_status: DmStatus | None
    bio: str | None
    public_send_stats: bool
    selections: ProfileSelections
    aliases: tuple[str, ...]
    links: tuple[ProfileLink, ...]
    overridden_fields: tuple[str, ...]
    hidden_inherited_link_ids: tuple[str, ...]
    throne_creator_id: str | None
    preferred_payment_link_id: str | None
    profile_color: int | None = None


@dataclass(frozen=True, slots=True)
class ThroneCreator:
    id: str
    handle: str


@dataclass(frozen=True, slots=True)
class ThronePrefill:
    owned_creators: tuple[ThroneCreator, ...]
    existing_registration_creator_id: str | None


@dataclass(frozen=True, slots=True)
class ThronePending:
    handle: str
    expires_at: str | None


@dataclass(frozen=True, slots=True)
class ProfileDraft:
    id: str
    owner_user_id: str
    origin_guild_id: str
    target_scope: DraftScope
    guild_id: str | None
    server_mode: ServerProfileMode | None
    status: DraftStatus
    revision: int
    base_version: int
    current_step: DraftStepKey | None
    next_step: DraftStepKey | None
    steps: tuple[DraftStep, ...]
    governing_orientation: Orientation | None
    document: DraftDocument
    throne_prefill: ThronePrefill | None
    created_at: str | None
    updated_at: str | None
    published_at: str | None
    dm_status_selected: bool = False
    wizard_stage: WizardStage | None = None
    wizard_substep: str | None = None
    throne_pending: ThronePending | None = None
    resolved_profile_color: int | None = None


@dataclass(frozen=True, slots=True)
class StartDraftResult:
    resume_required: bool
    draft: ProfileDraft


@dataclass(frozen=True, slots=True)
class LinkImportCandidate:
    id: str
    platform: str
    public_label: str
    username: str | None
    normalized_url: str
    link_type: LinkType
    selected: bool


@dataclass(frozen=True, slots=True)
class LinkImport:
    id: str
    draft_id: str
    source_url: str
    provider: str
    status: str
    candidates: tuple[LinkImportCandidate, ...]


@dataclass(frozen=True, slots=True)
class CreateLinkImportResult:
    """An imported candidate set and the revision it advanced the private draft to."""

    link_import: LinkImport
    draft: ProfileDraft


@dataclass(frozen=True, slots=True)
class LinkImportConfirmation:
    draft: ProfileDraft
    added_link_count: int
    skipped_duplicate_count: int


@dataclass(frozen=True, slots=True)
class ThroneDraftResult:
    draft: ProfileDraft
    webhook_url: str | None
    webhook_state: str


@dataclass(frozen=True, slots=True)
class ThroneResolveResult:
    draft: ProfileDraft
    handle: str
    already_verified: bool


@dataclass(frozen=True, slots=True)
class ThroneDraftStatus:
    handle: str | None
    verified: bool
    verified_at: str | None


@dataclass(frozen=True, slots=True)
class GuildSetupSession:
    id: str
    guild_id: str
    initiator_user_id: str
    status: str
    current_step: str
    selected_channel_id: str | None
    revision: int
    public_message_id: str | None
    expires_at: str | None
    created_at: str | None
    updated_at: str | None
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class StartGuildSetupResult:
    resume_required: bool
    session: GuildSetupSession


@dataclass(frozen=True, slots=True)
class CompleteGuildSetupResult:
    session: GuildSetupSession
    send_channel_id: str


def _snowflake(value: int | str) -> str:
    text = str(value)
    if not text.isdecimal():
        raise ValueError("Discord snowflakes must contain only decimal digits")
    return text


def _record(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkerAPIError(f"Worker returned an invalid {what}")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise WorkerAPIError(f"Worker returned an invalid {field}")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise WorkerAPIError(f"Worker returned an invalid {field}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkerAPIError(f"Worker returned an invalid {field}") from exc


def _optional_color(value: object, field: str = "profile_color") -> int | None:
    if value is None:
        return None
    color = _integer(value, field)
    if not 0 <= color <= 0xFFFFFF:
        raise WorkerAPIError(f"Worker returned an invalid {field}")
    return color


def _enum(enum_type: type[StrEnum], value: object, field: str) -> StrEnum:
    try:
        return enum_type(_string(value, field))
    except ValueError as exc:
        raise WorkerAPIError(f"Worker returned an invalid {field}") from exc


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise WorkerAPIError(f"Worker returned an invalid {field}")
    return tuple(value)


class WorkerClient:
    """Small typed facade over Worker endpoints; request data is never logged."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        session: aiohttp.ClientSession,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def get_guild_config(self, guild_id: int | str) -> GuildConfig | None:
        try:
            data = await self._request("GET", f"/v1/guilds/{_snowflake(guild_id)}/config")
        except WorkerAPIError as exc:
            if exc.status == 404:
                return None
            raise
        return GuildConfig(_snowflake(data["guild_id"]), _snowflake(data["send_channel_id"]))

    async def configure_guild(
        self, *, guild_id: int | str, send_channel_id: int | str
    ) -> GuildConfig:
        data = await self._request(
            "PUT",
            f"/v1/guilds/{_snowflake(guild_id)}/config",
            json={"send_channel_id": _snowflake(send_channel_id)},
        )
        return GuildConfig(_snowflake(data["guild_id"]), _snowflake(data["send_channel_id"]))

    async def register_domme(
        self, *, guild_id: int | str, discord_user_id: int | str, throne: str, reset_webhook: bool
    ) -> DommeRegistration:
        data = await self._request(
            "POST",
            f"/v1/guilds/{_snowflake(guild_id)}/registrations/domme",
            json={
                "discord_user_id": _snowflake(discord_user_id),
                "throne": throne,
                "reset_webhook": reset_webhook,
            },
        )
        return DommeRegistration(
            str(data["creator_id"]),
            str(data["throne_handle"]),
            _optional_string(data.get("webhook_url")),
            str(data["webhook_state"]),
        )

    async def get_profile(self, *, guild_id: int | str, user_id: int | str) -> ProfileLookup:
        data = await self._request(
            "GET", f"/v1/guilds/{_snowflake(guild_id)}/profiles/{_snowflake(user_id)}"
        )
        profile = data.get("profile")
        if profile is not None and not isinstance(profile, dict):
            raise WorkerAPIError("Worker returned an invalid profile lookup")
        global_available = data.get("global_available")
        if not isinstance(global_available, bool):
            raise WorkerAPIError("Worker returned an invalid profile lookup")
        return ProfileLookup(self._parse_profile(profile) if profile else None, global_available)

    async def start_draft(
        self,
        *,
        owner_user_id: int | str,
        origin_guild_id: int | str,
        target_scope: DraftScope,
        guild_id: int | str | None = None,
        server_mode: ServerProfileMode | None = None,
    ) -> StartDraftResult:
        body: dict[str, JSONValue] = {
            "owner_user_id": _snowflake(owner_user_id),
            "origin_guild_id": _snowflake(origin_guild_id),
            "target_scope": target_scope.value,
        }
        if target_scope is DraftScope.SERVER:
            if guild_id is None or server_mode is None:
                raise ValueError("server drafts require guild_id and server_mode")
            body.update({"guild_id": _snowflake(guild_id), "server_mode": server_mode.value})
        data = await self._request("POST", "/v1/profile-drafts/start", json=body)
        if not isinstance(data.get("resume_required"), bool):
            raise WorkerAPIError("Worker returned an invalid draft start")
        return StartDraftResult(data["resume_required"], self._parse_draft(data.get("draft")))

    async def get_draft(self, draft_id: str, *, owner_user_id: int | str) -> ProfileDraft:
        data = await self._request(
            "GET", f"/v1/profile-drafts/{draft_id}?owner_user_id={_snowflake(owner_user_id)}"
        )
        return self._parse_draft(data.get("draft"))

    async def update_draft_step(
        self,
        draft_id: str,
        *,
        step: DraftStepKey,
        owner_user_id: int | str,
        expected_revision: int,
        values: dict[str, JSONValue],
    ) -> ProfileDraft:
        data = await self._request(
            "PUT",
            f"/v1/profile-drafts/{draft_id}/steps/{step.value}",
            json=self._mutation(owner_user_id, expected_revision, values),
        )
        return self._parse_draft(data.get("draft"))

    async def set_draft_wizard_stage(
        self,
        draft_id: str,
        *,
        owner_user_id: int | str,
        expected_revision: int,
        stage: WizardStage,
        substep: str | None = None,
    ) -> ProfileDraft:
        values: dict[str, JSONValue] = {
            "stage": stage.value,
            "substep": substep,
        }
        data = await self._request(
            "PUT",
            f"/v1/profile-drafts/{draft_id}/wizard-stage",
            json=self._mutation(owner_user_id, expected_revision, values),
        )
        return self._parse_draft(data.get("draft"))

    async def restart_draft(
        self, draft_id: str, *, owner_user_id: int | str, expected_revision: int
    ) -> ProfileDraft:
        data = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/restart",
            json=self._mutation(owner_user_id, expected_revision),
        )
        return self._parse_draft(data.get("draft"))

    async def publish_draft(
        self, draft_id: str, *, owner_user_id: int | str, expected_revision: int
    ) -> PublicProfile:
        data = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/publish",
            json=self._mutation(owner_user_id, expected_revision),
        )
        return self._parse_profile(data.get("profile"))

    async def create_link_import(
        self, draft_id: str, *, owner_user_id: int | str, expected_revision: int, source_url: str
    ) -> CreateLinkImportResult:
        data = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/link-imports",
            json=self._mutation(owner_user_id, expected_revision, {"source_url": source_url}),
        )
        # Link import creation is a mutation: its response revision, rather than
        # the caller's prior revision, is the only safe basis for the next action.
        import_result = self._parse_import(data.get("import"))
        mutation_draft = _record(data.get("draft"), "link import draft")
        response_draft_id = _string(mutation_draft.get("id"), "link import draft id")
        response_revision = _integer(mutation_draft.get("revision"), "link import draft revision")
        if response_draft_id != draft_id:
            raise WorkerAPIError("Worker returned a link import for the wrong draft")
        draft = await self.get_draft(draft_id, owner_user_id=owner_user_id)
        if draft.revision < response_revision:
            raise WorkerAPIError("Worker returned an out-of-date link import draft")
        return CreateLinkImportResult(import_result, draft)

    async def confirm_link_import(
        self,
        draft_id: str,
        import_id: str,
        *,
        owner_user_id: int | str,
        expected_revision: int,
        candidate_ids: tuple[str, ...] | None = None,
    ) -> LinkImportConfirmation:
        extra: dict[str, JSONValue] = (
            {} if candidate_ids is None else {"candidate_ids": list(candidate_ids)}
        )
        data = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/link-imports/{import_id}/confirm",
            json=self._mutation(owner_user_id, expected_revision, extra),
        )
        return LinkImportConfirmation(
            await self.get_draft(draft_id, owner_user_id=owner_user_id),
            _integer(data.get("added_link_count"), "added_link_count"),
            _integer(data.get("skipped_duplicate_count"), "skipped_duplicate_count"),
        )

    async def add_link(
        self,
        draft_id: str,
        *,
        owner_user_id: int | str,
        expected_revision: int,
        public_label: str,
        normalized_url: str,
        link_type: LinkType,
        platform: str | None = None,
        username: str | None = None,
        enabled: bool = True,
        preferred: bool | None = None,
    ) -> ProfileDraft:
        body: dict[str, JSONValue] = {
            "public_label": public_label,
            "normalized_url": normalized_url,
            "link_type": link_type.value,
            "enabled": enabled,
        }
        if platform is not None:
            body["platform"] = platform
        if username is not None:
            body["username"] = username
        if preferred is not None:
            body["preferred"] = preferred
        _ = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/links",
            json=self._mutation(owner_user_id, expected_revision, body),
        )
        return await self.get_draft(draft_id, owner_user_id=owner_user_id)

    async def edit_link(
        self,
        draft_id: str,
        link_id: str,
        *,
        owner_user_id: int | str,
        expected_revision: int,
        public_label: str,
        normalized_url: str,
        link_type: LinkType,
        platform: str | None = None,
        username: str | None = None,
        enabled: bool = True,
        preferred: bool | None = None,
    ) -> ProfileDraft:
        body: dict[str, JSONValue] = {
            "public_label": public_label,
            "normalized_url": normalized_url,
            "link_type": link_type.value,
            "enabled": enabled,
        }
        if platform is not None:
            body["platform"] = platform
        if username is not None:
            body["username"] = username
        if preferred is not None:
            body["preferred"] = preferred
        _ = await self._request(
            "PUT",
            f"/v1/profile-drafts/{draft_id}/links/{link_id}",
            json=self._mutation(owner_user_id, expected_revision, body),
        )
        return await self.get_draft(draft_id, owner_user_id=owner_user_id)

    async def delete_link(
        self, draft_id: str, link_id: str, *, owner_user_id: int | str, expected_revision: int
    ) -> ProfileDraft:
        _ = await self._request(
            "DELETE",
            f"/v1/profile-drafts/{draft_id}/links/{link_id}",
            json=self._mutation(owner_user_id, expected_revision),
        )
        return await self.get_draft(draft_id, owner_user_id=owner_user_id)

    async def attach_throne(
        self,
        draft_id: str,
        *,
        owner_user_id: int | str,
        expected_revision: int,
        throne_input: str | None = None,
        existing_creator_id: str | None = None,
        confirm_pending: bool = False,
        rotate_webhook: bool = False,
    ) -> ThroneDraftResult:
        data = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/throne",
            json=self._mutation(
                owner_user_id,
                expected_revision,
                {
                    "throne_input": throne_input,
                    "existing_creator_id": existing_creator_id,
                    "confirm_pending": confirm_pending,
                    "rotate_webhook": rotate_webhook,
                },
            ),
        )
        return self._parse_throne_result(data)

    async def resolve_throne(
        self,
        draft_id: str,
        *,
        owner_user_id: int | str,
        expected_revision: int,
        throne_input: str,
    ) -> ThroneResolveResult:
        data = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/throne/resolve",
            json=self._mutation(
                owner_user_id,
                expected_revision,
                {"throne_input": throne_input},
            ),
        )
        handle = _string(data.get("handle"), "Throne handle")
        already_verified = _bool(data.get("already_verified"), "already_verified")
        return ThroneResolveResult(
            self._parse_draft(data.get("draft")),
            handle,
            already_verified,
        )

    async def rotate_throne(
        self, draft_id: str, *, owner_user_id: int | str, expected_revision: int
    ) -> ThroneDraftResult:
        data = await self._request(
            "POST",
            f"/v1/profile-drafts/{draft_id}/throne/rotate",
            json=self._mutation(owner_user_id, expected_revision),
        )
        return self._parse_throne_result(data)

    async def get_throne_status(
        self,
        draft_id: str,
        *,
        owner_user_id: int | str,
        expected_revision: int,
    ) -> ThroneDraftStatus:
        data = await self._request(
            "GET",
            f"/v1/profile-drafts/{draft_id}/throne/status"
            f"?owner_user_id={_snowflake(owner_user_id)}&expected_revision={expected_revision}",
        )
        return ThroneDraftStatus(
            _optional_string(data.get("handle")),
            _bool(data.get("verified"), "verified"),
            _optional_string(data.get("verified_at")),
        )

    async def start_guild_setup(
        self, *, guild_id: int | str, initiator_user_id: int | str
    ) -> StartGuildSetupResult:
        data = await self._request(
            "POST",
            "/v1/guild-setup-sessions",
            json={
                "guild_id": _snowflake(guild_id),
                "initiator_user_id": _snowflake(initiator_user_id),
            },
        )
        if not isinstance(data.get("resume_required"), bool):
            raise WorkerAPIError("Worker returned an invalid setup session")
        return StartGuildSetupResult(
            data["resume_required"], self._parse_setup_session(data.get("session"))
        )

    async def get_guild_setup(self, session_id: str) -> GuildSetupSession:
        return self._parse_setup_session(
            (await self._request("GET", f"/v1/guild-setup-sessions/{session_id}")).get("session")
        )

    async def set_guild_setup_channel(
        self,
        session_id: str,
        *,
        guild_id: int | str,
        initiator_user_id: int | str,
        expected_revision: int,
        channel_id: int | str,
    ) -> GuildSetupSession:
        data = await self._request(
            "PUT",
            f"/v1/guild-setup-sessions/{session_id}/channel",
            json={
                "guild_id": _snowflake(guild_id),
                "initiator_user_id": _snowflake(initiator_user_id),
                "expected_revision": expected_revision,
                "channel_id": _snowflake(channel_id),
            },
        )
        return self._parse_setup_session(data.get("session"))

    async def complete_guild_setup(
        self,
        session_id: str,
        *,
        guild_id: int | str,
        initiator_user_id: int | str,
        expected_revision: int,
    ) -> CompleteGuildSetupResult:
        data = await self._request(
            "POST",
            f"/v1/guild-setup-sessions/{session_id}/complete",
            json={
                "guild_id": _snowflake(guild_id),
                "initiator_user_id": _snowflake(initiator_user_id),
                "expected_revision": expected_revision,
            },
        )
        return CompleteGuildSetupResult(
            self._parse_setup_session(data.get("session")), _snowflake(data.get("send_channel_id"))
        )

    async def lease_notifications(
        self, *, owner: str, limit: int, lease_seconds: int
    ) -> list[SendNotification]:
        data = await self._request(
            "POST",
            "/v1/notifications/lease",
            json={"owner": owner, "limit": limit, "lease_seconds": lease_seconds},
        )
        rows = data.get("notifications")
        if not isinstance(rows, list):
            raise WorkerAPIError("Worker returned an invalid notification list")
        return [self._parse_notification(row) for row in rows]

    async def ack_notification(
        self, notification_id: str, *, lease_token: str, discord_message_id: int | str
    ) -> None:
        await self._request(
            "POST",
            f"/v1/notifications/{notification_id}/ack",
            json={"lease_token": lease_token, "discord_message_id": _snowflake(discord_message_id)},
        )

    async def nack_notification(
        self, notification_id: str, *, lease_token: str, error: str, permanent: bool
    ) -> None:
        await self._request(
            "POST",
            f"/v1/notifications/{notification_id}/nack",
            json={"lease_token": lease_token, "error": error[:300], "permanent": permanent},
        )

    @staticmethod
    def _mutation(
        owner_user_id: int | str, expected_revision: int, values: dict[str, JSONValue] | None = None
    ) -> dict[str, JSONValue]:
        if expected_revision < 0:
            raise ValueError("expected_revision must not be negative")
        return {
            "owner_user_id": _snowflake(owner_user_id),
            "expected_revision": expected_revision,
            **(values or {}),
        }

    async def _request(
        self, method: str, path: str, *, json: dict[str, JSONValue] | None = None
    ) -> dict[str, Any]:
        # This header is passed only to aiohttp; this client never logs request headers or bodies.
        headers = {"Authorization": f"Bearer {self._api_token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method, f"{self._base_url}{path}", headers=headers, json=json, timeout=self._timeout
            ) as response:
                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise WorkerAPIError(
                        "Worker returned a non-JSON response", status=response.status
                    ) from exc
        except TimeoutError as exc:
            raise WorkerAPIError("Worker request timed out") from exc
        except aiohttp.ClientError as exc:
            raise WorkerAPIError("Worker request failed") from exc
        if not isinstance(payload, dict):
            raise WorkerAPIError("Worker returned an invalid response", status=response.status)
        if response.status >= 400 or payload.get("ok") is not True:
            error = payload.get("error")
            code = (
                str(error.get("code", "worker_error"))
                if isinstance(error, dict)
                else str(payload.get("code", "worker_error"))
            )
            message = (
                str(error.get("message", "Worker request failed"))
                if isinstance(error, dict)
                else str(error or payload.get("message") or "Worker request failed")
            )
            raise WorkerAPIError(message, status=response.status, code=code)
        return _record(payload.get("data"), "response data")

    @staticmethod
    def _parse_profile(value: object) -> PublicProfile:
        data = _record(value, "profile")
        selections = WorkerClient._parse_selections(data.get("selections"))
        links = tuple(
            WorkerClient._parse_link(row, include_order=True)
            for row in _list(data.get("links"), "profile links")
        )
        raw_stats = data.get("send_stats")
        stats = (
            None
            if raw_stats is None
            else tuple(
                SendStat(
                    _string(_record(row, "send stat").get("currency"), "currency"),
                    _integer(_record(row, "send stat").get("count"), "count"),
                    _integer(
                        _record(row, "send stat").get("total_amount_minor"), "total_amount_minor"
                    ),
                )
                for row in _list(raw_stats, "send_stats")
            )
        )
        mode_value = data.get("mode")
        return PublicProfile(
            DraftScope(_enum(DraftScope, data.get("scope"), "scope")),
            None
            if mode_value is None
            else ServerProfileMode(_enum(ServerProfileMode, mode_value, "mode")),
            _snowflake(data.get("owner_user_id")),
            Orientation(_enum(Orientation, data.get("orientation"), "orientation")),
            DmStatus(_enum(DmStatus, data.get("dm_status"), "dm_status")),
            _optional_string(data.get("bio")),
            _bool(data.get("public_send_stats"), "public_send_stats"),
            selections,
            _strings(data.get("aliases"), "aliases"),
            links,
            _optional_string(data.get("preferred_payment_link_id")),
            _bool(data.get("throne_connected"), "throne_connected"),
            stats,
            _integer(data.get("version"), "version"),
            _optional_string(data.get("published_at")),
            _optional_color(data.get("profile_color")),
        )

    @staticmethod
    def _parse_draft(value: object) -> ProfileDraft:
        data = _record(value, "draft")
        document = _record(data.get("document"), "draft document")
        prefill = data.get("throne_prefill")
        parsed_prefill = None if prefill is None else WorkerClient._parse_prefill(prefill)
        pending = data.get("throne_pending")
        parsed_pending = None
        if pending is not None:
            pending_data = _record(pending, "pending Throne confirmation")
            parsed_pending = ThronePending(
                _string(pending_data.get("handle"), "pending Throne handle"),
                _optional_string(pending_data.get("expires_at")),
            )
        current = data.get("current_step")
        next_step = data.get("next_step")
        governing = data.get("governing_orientation")
        return ProfileDraft(
            _string(data.get("id"), "draft id"),
            _snowflake(data.get("owner_user_id")),
            _snowflake(data.get("origin_guild_id")),
            DraftScope(_enum(DraftScope, data.get("target_scope"), "target_scope")),
            _nullable_snowflake(data.get("guild_id")),
            _nullable_enum(ServerProfileMode, data.get("server_mode"), "server_mode"),
            DraftStatus(_enum(DraftStatus, data.get("status"), "status")),
            _integer(data.get("revision"), "revision"),
            _integer(data.get("base_version"), "base_version"),
            None if current is None else DraftStepKey(_enum(DraftStepKey, current, "current_step")),
            None
            if next_step is None
            else DraftStepKey(_enum(DraftStepKey, next_step, "next_step")),
            tuple(WorkerClient._parse_step(row) for row in _list(data.get("steps"), "draft steps")),
            None
            if governing is None
            else Orientation(_enum(Orientation, governing, "governing_orientation")),
            DraftDocument(
                _nullable_enum(DmStatus, document.get("dm_status"), "dm_status"),
                _optional_string(document.get("bio")),
                _bool(document.get("public_send_stats"), "public_send_stats"),
                WorkerClient._parse_selections(document.get("selections")),
                _strings(document.get("aliases"), "aliases"),
                tuple(
                    WorkerClient._parse_link(row)
                    for row in _list(document.get("links"), "draft links")
                ),
                _strings(document.get("overridden_fields"), "overridden_fields"),
                _strings(document.get("hidden_inherited_link_ids"), "hidden_inherited_link_ids"),
                _optional_string(document.get("throne_creator_id")),
                _optional_string(document.get("preferred_payment_link_id")),
                _optional_color(document.get("profile_color")),
            ),
            parsed_prefill,
            _optional_string(data.get("created_at")),
            _optional_string(data.get("updated_at")),
            _optional_string(data.get("published_at")),
            _bool(data.get("dm_status_selected"), "dm_status_selected"),
            _nullable_enum(WizardStage, data.get("wizard_stage"), "wizard_stage"),
            _optional_string(data.get("wizard_substep")),
            parsed_pending,
            _optional_color(data.get("resolved_profile_color"), "resolved_profile_color"),
        )

    @staticmethod
    def _parse_selections(value: object) -> ProfileSelections:
        data = _record(value, "selections")
        return ProfileSelections(
            _strings(data.get("pronouns"), "pronouns"),
            _strings(data.get("honourifics"), "honourifics"),
            _strings(data.get("submissive_labels"), "submissive_labels"),
        )

    @staticmethod
    def _parse_link(value: object, *, include_order: bool = False) -> ProfileLink:
        data = _record(value, "link")
        return ProfileLink(
            _string(data.get("id"), "link id"),
            _string(data.get("platform"), "platform"),
            _string(data.get("public_label"), "public_label"),
            _optional_string(data.get("username")),
            _string(data.get("normalized_url"), "normalized_url"),
            LinkType(_enum(LinkType, data.get("link_type"), "link_type")),
            _bool(data.get("enabled"), "enabled") if "enabled" in data else True,
            _integer(data.get("sort_order"), "sort_order") if include_order else 0,
        )

    @staticmethod
    def _parse_step(value: object) -> DraftStep:
        data = _record(value, "draft step")
        return DraftStep(
            DraftStepKey(_enum(DraftStepKey, data.get("key"), "step key")),
            _string(data.get("status"), "step status"),
            _optional_string(data.get("completed_at")),
        )

    @staticmethod
    def _parse_prefill(value: object) -> ThronePrefill:
        data = _record(value, "throne prefill")
        creators = tuple(
            ThroneCreator(
                _string(_record(row, "creator").get("id"), "creator id"),
                _string(_record(row, "creator").get("handle"), "creator handle"),
            )
            for row in _list(data.get("owned_creators"), "owned_creators")
        )
        return ThronePrefill(
            creators, _optional_string(data.get("existing_registration_creator_id"))
        )

    @staticmethod
    def _parse_import(value: object) -> LinkImport:
        data = _record(value, "link import")
        candidates = tuple(
            LinkImportCandidate(
                _string(_record(row, "import candidate").get("id"), "candidate id"),
                _string(_record(row, "import candidate").get("platform"), "platform"),
                _string(_record(row, "import candidate").get("public_label"), "public_label"),
                _optional_string(_record(row, "import candidate").get("username")),
                _string(_record(row, "import candidate").get("normalized_url"), "normalized_url"),
                LinkType(
                    _enum(LinkType, _record(row, "import candidate").get("link_type"), "link_type")
                ),
                _bool(_record(row, "import candidate").get("selected"), "selected"),
            )
            for row in _list(data.get("candidates"), "import candidates")
        )
        return LinkImport(
            _string(data.get("id"), "import id"),
            _string(data.get("draft_id"), "draft_id"),
            _string(data.get("source_url"), "source_url"),
            _string(data.get("provider"), "provider"),
            _string(data.get("status"), "status"),
            candidates,
        )

    @staticmethod
    def _parse_throne_result(data: dict[str, Any]) -> ThroneDraftResult:
        return ThroneDraftResult(
            WorkerClient._parse_draft(data.get("draft")),
            _optional_string(data.get("webhook_url")),
            _string(data.get("webhook_state"), "webhook_state"),
        )

    @staticmethod
    def _parse_setup_session(value: object) -> GuildSetupSession:
        data = _record(value, "setup session")
        return GuildSetupSession(
            _string(data.get("id"), "session id"),
            _snowflake(data.get("guild_id")),
            _snowflake(data.get("initiator_user_id")),
            _string(data.get("status"), "status"),
            _string(data.get("current_step"), "current_step"),
            _nullable_snowflake(data.get("selected_channel_id")),
            _integer(data.get("revision"), "revision"),
            _nullable_snowflake(data.get("public_message_id")),
            _optional_string(data.get("expires_at")),
            _optional_string(data.get("created_at")),
            _optional_string(data.get("updated_at")),
            _optional_string(data.get("completed_at")),
        )

    @staticmethod
    def _parse_notification(value: object) -> SendNotification:
        data = _record(value, "notification")
        return SendNotification(
            str(data["notification_id"]),
            str(data["lease_token"]),
            str(data["send_id"]),
            _snowflake(data["guild_id"]),
            _snowflake(data["channel_id"]),
            _snowflake(data["recipient_user_id"]),
            str(data["throne_handle"]),
            _integer(data["amount_minor"], "notification amount"),
            str(data["currency"]),
            _optional_string(data.get("sender_name")),
            bool(data.get("is_private")),
            bool(data.get("is_anonymous")),
            _optional_string(data.get("item_name")),
            _optional_string(data.get("item_image_url")),
            str(data["purchased_at"]),
            bool(data.get("delivery_may_exist")),
        )


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise WorkerAPIError(f"Worker returned an invalid {field}")
    return value


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise WorkerAPIError(f"Worker returned an invalid {field}")
    return value


def _nullable_snowflake(value: object) -> str | None:
    return None if value is None else _snowflake(value)


def _nullable_enum(enum_type: type[StrEnum], value: object, field: str) -> Any:
    return None if value is None else _enum(enum_type, value, field)
