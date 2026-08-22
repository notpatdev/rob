import type { Env } from "../src/env";
import type { D1Migration } from "cloudflare:test";

declare module "cloudflare:test" {
  // Tests need to swap THRONE_PUBLIC_KEY_PEM per-case to exercise signature
  // verification against freshly generated keypairs, so it must be mutable here
  // even though the production Env type marks it readonly.
  interface ProvidedEnv extends Omit<Env, "THRONE_PUBLIC_KEY_PEM"> {
    THRONE_PUBLIC_KEY_PEM: string;
    TEST_MIGRATIONS: D1Migration[];
  }
}
