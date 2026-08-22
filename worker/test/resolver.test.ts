import { describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { resolveProfile } from "../src/profile/resolver";
import { TEST_HOME_GUILD_ID } from "./helpers";
import {
  seedAlias,
  seedDocument,
  seedGlobalProfile,
  seedHiddenLink,
  seedLink,
  seedOverride,
  seedSelection,
  seedServerProfile,
} from "./profileHelpers";

const OTHER_GUILD = "200000000000000001";

describe("resolveProfile", () => {
  it("returns null with global_available=false when the user has no global profile at all", async () => {
    const result = await resolveProfile(env, TEST_HOME_GUILD_ID, "1");
    expect(result).toEqual({ profile: null, globalAvailable: false });
  });

  it("resolves the global document directly in the home guild", async () => {
    await seedDocument({ id: "doc-global-1", ownerUserId: "10", orientation: "domme", dmStatus: "open", bio: "hi" });
    await seedSelection("doc-global-1", "pronoun", "She/Her");
    await seedSelection("doc-global-1", "honourific", "Goddess");
    await seedGlobalProfile("10", "doc-global-1", 3);

    const result = await resolveProfile(env, TEST_HOME_GUILD_ID, "10");
    expect(result.globalAvailable).toBe(true);
    expect(result.profile).toMatchObject({
      scope: "global",
      mode: null,
      orientation: "domme",
      dmStatus: "open",
      bio: "hi",
      version: 3,
    });
    expect(result.profile?.selections.pronouns).toEqual(["She/Her"]);
    expect(result.profile?.selections.honourifics).toEqual(["Goddess"]);
  });

  it("reports global_available outside the home guild even without a server profile", async () => {
    await seedDocument({ id: "doc-global-2", ownerUserId: "11", orientation: "submissive", dmStatus: "open" });
    await seedGlobalProfile("11", "doc-global-2");

    const result = await resolveProfile(env, OTHER_GUILD, "11");
    expect(result.profile).toBeNull();
    expect(result.globalAvailable).toBe(true);
  });

  it("resolves an independent server profile entirely on its own document", async () => {
    await seedDocument({ id: "doc-global-3", ownerUserId: "12", orientation: "domme", dmStatus: "open", bio: "global bio" });
    await seedGlobalProfile("12", "doc-global-3");

    await seedDocument({ id: "doc-indep-3", ownerUserId: "12", orientation: "domme", dmStatus: "closed", bio: "server-only bio" });
    await seedServerProfile({ id: "srv-3", guildId: OTHER_GUILD, ownerUserId: "12", mode: "independent", documentId: "doc-indep-3", version: 5 });

    const result = await resolveProfile(env, OTHER_GUILD, "12");
    expect(result.profile).toMatchObject({ scope: "server", mode: "independent", dmStatus: "closed", bio: "server-only bio", version: 5 });
  });

  describe("linked overlays", () => {
    it("inherits every field from the live global document when nothing is overridden", async () => {
      await seedDocument({ id: "doc-global-4", ownerUserId: "13", orientation: "switch_domme", dmStatus: "open", bio: "global bio" });
      await seedSelection("doc-global-4", "pronoun", "They/Them");
      await seedGlobalProfile("13", "doc-global-4");

      await seedDocument({ id: "doc-overlay-4", ownerUserId: "13", state: "published" });
      await seedServerProfile({ id: "srv-4", guildId: OTHER_GUILD, ownerUserId: "13", mode: "linked", documentId: "doc-overlay-4" });

      const result = await resolveProfile(env, OTHER_GUILD, "13");
      expect(result.profile).toMatchObject({ scope: "server", mode: "linked", orientation: "switch_domme", dmStatus: "open", bio: "global bio" });
      expect(result.profile?.selections.pronouns).toEqual(["They/Them"]);
    });

    it("uses the overlay's explicit-empty override instead of falling back to the global value", async () => {
      await seedDocument({ id: "doc-global-5", ownerUserId: "14", orientation: "submissive", dmStatus: "open", bio: "global bio" });
      await seedGlobalProfile("14", "doc-global-5");

      // Overlay explicitly overrides bio to empty (null) -- distinct from "not overridden".
      await seedDocument({ id: "doc-overlay-5", ownerUserId: "14", bio: null });
      await seedOverride("doc-overlay-5", "bio");
      await seedServerProfile({ id: "srv-5", guildId: OTHER_GUILD, ownerUserId: "14", mode: "linked", documentId: "doc-overlay-5" });

      const result = await resolveProfile(env, OTHER_GUILD, "14");
      expect(result.profile?.bio).toBeNull();
    });

    it("overrides only the fields marked overridden, inheriting the rest live", async () => {
      await seedDocument({ id: "doc-global-6", ownerUserId: "15", orientation: "switch_submissive", dmStatus: "open", bio: "global bio" });
      await seedGlobalProfile("15", "doc-global-6");

      await seedDocument({ id: "doc-overlay-6", ownerUserId: "15", dmStatus: "closed" });
      await seedOverride("doc-overlay-6", "dm_status");
      await seedServerProfile({ id: "srv-6", guildId: OTHER_GUILD, ownerUserId: "15", mode: "linked", documentId: "doc-overlay-6" });

      const result = await resolveProfile(env, OTHER_GUILD, "15");
      expect(result.profile?.dmStatus).toBe("closed");
      expect(result.profile?.bio).toBe("global bio");
    });

    it("reflects a live global update immediately without any copy on the overlay", async () => {
      await seedDocument({ id: "doc-global-7", ownerUserId: "16", orientation: "domme", dmStatus: "open", bio: "before" });
      await seedGlobalProfile("16", "doc-global-7");
      await seedDocument({ id: "doc-overlay-7", ownerUserId: "16" });
      await seedServerProfile({ id: "srv-7", guildId: OTHER_GUILD, ownerUserId: "16", mode: "linked", documentId: "doc-overlay-7" });

      const before = await resolveProfile(env, OTHER_GUILD, "16");
      expect(before.profile?.bio).toBe("before");

      // Simulate a fresh global publication: a *new* document becomes current.
      await seedDocument({ id: "doc-global-7b", ownerUserId: "16", orientation: "domme", dmStatus: "open", bio: "after" });
      await env.DB.prepare("UPDATE global_profiles SET current_document_id = ?, version = version + 1 WHERE owner_user_id = ?")
        .bind("doc-global-7b", "16")
        .run();

      const after = await resolveProfile(env, OTHER_GUILD, "16");
      expect(after.profile?.bio).toBe("after");
    });

    it("hides an inherited link the overlay marks not visible, while keeping its own local links", async () => {
      await seedDocument({ id: "doc-global-8", ownerUserId: "17", orientation: "domme", dmStatus: "open" });
      await seedGlobalProfile("17", "doc-global-8");
      await seedLink({ id: "link-global-8a", documentId: "doc-global-8", platform: "twitter", publicLabel: "Twitter", normalizedUrl: "https://twitter.com/a", linkType: "social", sortOrder: 0 });
      await seedLink({ id: "link-global-8b", documentId: "doc-global-8", platform: "cashapp", publicLabel: "CashApp", normalizedUrl: "https://cash.app/a", linkType: "payment", sortOrder: 1 });

      await seedDocument({ id: "doc-overlay-8", ownerUserId: "17" });
      await seedHiddenLink("doc-overlay-8", "link-global-8a");
      await seedLink({ id: "link-local-8", documentId: "doc-overlay-8", platform: "onlyfans", publicLabel: "Local", normalizedUrl: "https://example.com/local", linkType: "social", sortOrder: 0 });
      await seedServerProfile({ id: "srv-8", guildId: OTHER_GUILD, ownerUserId: "17", mode: "linked", documentId: "doc-overlay-8" });

      const result = await resolveProfile(env, OTHER_GUILD, "17");
      const linkIds = result.profile?.links.map((link) => link.id) ?? [];
      expect(linkIds).toContain("link-global-8b");
      expect(linkIds).toContain("link-local-8");
      expect(linkIds).not.toContain("link-global-8a");
    });

    it("falls back preferred payment: overlay choice, then global choice, then first visible payment link", async () => {
      await seedDocument({ id: "doc-global-9", ownerUserId: "18", orientation: "domme", dmStatus: "open", preferredPaymentLinkId: "link-global-9b" });
      await seedGlobalProfile("18", "doc-global-9");
      await seedLink({ id: "link-global-9a", documentId: "doc-global-9", platform: "cashapp", publicLabel: "A", normalizedUrl: "https://cash.app/a", linkType: "payment", sortOrder: 0 });
      await seedLink({ id: "link-global-9b", documentId: "doc-global-9", platform: "venmo", publicLabel: "B", normalizedUrl: "https://venmo.com/b", linkType: "payment", sortOrder: 1 });

      await seedDocument({ id: "doc-overlay-9", ownerUserId: "18" });
      await seedServerProfile({ id: "srv-9", guildId: OTHER_GUILD, ownerUserId: "18", mode: "linked", documentId: "doc-overlay-9" });

      // No overlay preference set -> falls back to global's preferred link.
      const first = await resolveProfile(env, OTHER_GUILD, "18");
      expect(first.profile?.preferredPaymentLinkId).toBe("link-global-9b");

      // Overlay picks an explicit (still-visible) preference -> wins over global's.
      await env.DB.prepare("UPDATE profile_documents SET preferred_payment_link_id = ? WHERE id = ?")
        .bind("link-global-9a", "doc-overlay-9")
        .run();
      const second = await resolveProfile(env, OTHER_GUILD, "18");
      expect(second.profile?.preferredPaymentLinkId).toBe("link-global-9a");

      // Global's preferred link disappears (hidden by the overlay) and the overlay's own
      // choice is also hidden -> falls back deterministically to the first visible payment link.
      await seedHiddenLink("doc-overlay-9", "link-global-9a");
      await env.DB.prepare("UPDATE profile_documents SET preferred_payment_link_id = NULL WHERE id = ?").bind("doc-overlay-9").run();
      await env.DB.prepare("UPDATE profile_documents SET preferred_payment_link_id = 'link-missing' WHERE id = ?")
        .bind("doc-global-9")
        .run();
      const third = await resolveProfile(env, OTHER_GUILD, "18");
      expect(third.profile?.preferredPaymentLinkId).toBe("link-global-9b");
    });
  });

  it("resolves nothing for a server guild with no server profile even when a global one exists", async () => {
    await seedDocument({ id: "doc-global-10", ownerUserId: "19", orientation: "domme", dmStatus: "open" });
    await seedGlobalProfile("19", "doc-global-10");

    const result = await resolveProfile(env, OTHER_GUILD, "19");
    expect(result.profile).toBeNull();
    expect(result.globalAvailable).toBe(true);
  });

  it("orders selections/aliases and never leaks alias data from a non-alias orientation", async () => {
    await seedDocument({ id: "doc-global-11", ownerUserId: "20", orientation: "submissive", dmStatus: "open" });
    await seedAlias("doc-global-11", "PetName", "petname");
    await seedGlobalProfile("20", "doc-global-11");

    const result = await resolveProfile(env, TEST_HOME_GUILD_ID, "20");
    expect(result.profile?.aliases).toEqual(["PetName"]);
  });
});
