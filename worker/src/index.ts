import type { Env } from "./env.js";
import { type RouteContext, Router } from "./router.js";
import { isAuthorizedBotRequest } from "./util/auth.js";
import { Errors } from "./util/response.js";
import { handleHealth } from "./routes/health.js";
import { handleThroneWebhook } from "./routes/webhookThrone.js";
import { handleGetGuildConfig, handlePutGuildConfig } from "./routes/guildConfig.js";
import { handleRegisterDomme } from "./routes/registration.js";
import { handleGetProfile } from "./routes/profiles.js";
import {
  handleGetDraft,
  handlePublishDraft,
  handlePutDraftStep,
  handleRestartDraft,
  handleStartDraft,
} from "./routes/profileDrafts.js";
import { handleAddLink, handleDeleteLink, handleEditLink } from "./routes/profileLinks.js";
import { handleConfirmLinkImport, handleCreateLinkImport } from "./routes/profileLinkImports.js";
import { handleAttachDraftThrone, handleRotateDraftThrone } from "./routes/profileThrone.js";
import {
  handleCompleteGuildSetupSession,
  handleCreateGuildSetupSession,
  handleGetGuildSetupSession,
  handleSetGuildSetupChannel,
} from "./routes/guildSetupSessions.js";
import {
  handleAckNotification,
  handleLeaseNotifications,
  handleNackNotification,
} from "./routes/notifications.js";

type Handler = (ctx: RouteContext) => Promise<Response> | Response;

/** Wraps a handler so it requires a valid `Authorization: Bearer <token>` header. */
function withAuth(handler: Handler): Handler {
  return async (ctx: RouteContext): Promise<Response> => {
    if (!(await isAuthorizedBotRequest(ctx.request, ctx.env))) {
      return Errors.unauthorized();
    }
    return handler(ctx);
  };
}

const router = new Router();

// Public routes.
router.get("/health", handleHealth);
router.post("/t/:creatorId/:routeSecret", handleThroneWebhook);

// Bearer-protected bot routes.
router.get("/v1/guilds/:guildId/config", withAuth(handleGetGuildConfig));
router.put("/v1/guilds/:guildId/config", withAuth(handlePutGuildConfig));
router.post("/v1/guilds/:guildId/registrations/domme", withAuth(handleRegisterDomme));
router.get("/v1/guilds/:guildId/profiles/:userId", withAuth(handleGetProfile));
router.post("/v1/profile-drafts/start", withAuth(handleStartDraft));
router.get("/v1/profile-drafts/:draftId", withAuth(handleGetDraft));
router.put("/v1/profile-drafts/:draftId/steps/:stepKey", withAuth(handlePutDraftStep));
router.post("/v1/profile-drafts/:draftId/restart", withAuth(handleRestartDraft));
router.post("/v1/profile-drafts/:draftId/publish", withAuth(handlePublishDraft));
router.post("/v1/profile-drafts/:draftId/link-imports", withAuth(handleCreateLinkImport));
router.post("/v1/profile-drafts/:draftId/link-imports/:importId/confirm", withAuth(handleConfirmLinkImport));
router.post("/v1/profile-drafts/:draftId/links", withAuth(handleAddLink));
router.put("/v1/profile-drafts/:draftId/links/:linkId", withAuth(handleEditLink));
router.delete("/v1/profile-drafts/:draftId/links/:linkId", withAuth(handleDeleteLink));
router.post("/v1/profile-drafts/:draftId/throne", withAuth(handleAttachDraftThrone));
router.post("/v1/profile-drafts/:draftId/throne/rotate", withAuth(handleRotateDraftThrone));
router.post("/v1/guild-setup-sessions", withAuth(handleCreateGuildSetupSession));
router.get("/v1/guild-setup-sessions/:sessionId", withAuth(handleGetGuildSetupSession));
router.put("/v1/guild-setup-sessions/:sessionId/channel", withAuth(handleSetGuildSetupChannel));
router.post("/v1/guild-setup-sessions/:sessionId/complete", withAuth(handleCompleteGuildSetupSession));
router.post("/v1/notifications/lease", withAuth(handleLeaseNotifications));
router.post("/v1/notifications/:id/ack", withAuth(handleAckNotification));
router.post("/v1/notifications/:id/nack", withAuth(handleNackNotification));

export default {
  async fetch(request: Request, env: Env, executionCtx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const matched = router.match(request.method, url.pathname);
    if (matched === "method_not_allowed") return Errors.methodNotAllowed();
    if (matched === null) return Errors.notFound("Route not found");

    try {
      return await matched.handler({ request, env, executionCtx, params: matched.params });
    } catch (error) {
      // Never log the request body or secrets; only a bare error message.
      console.error("Unhandled worker error:", error instanceof Error ? error.message : "unknown error");
      return Errors.internal();
    }
  },
} satisfies ExportedHandler<Env>;
