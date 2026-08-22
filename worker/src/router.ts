import type { Env } from "./env.js";

export interface RouteContext {
  request: Request;
  env: Env;
  executionCtx: ExecutionContext;
  params: Readonly<Record<string, string>>;
}

export type RouteHandler = (ctx: RouteContext) => Promise<Response> | Response;

interface Route {
  method: string;
  segments: readonly string[];
  handler: RouteHandler;
}

function splitPath(path: string): string[] {
  return path.split("/").filter((segment) => segment.length > 0);
}

/**
 * A minimal explicit router: routes are registered as method + literal path
 * segments, with `:name` segments captured as params. No framework, no
 * pattern-matching magic beyond exact segment counts.
 */
export class Router {
  private readonly routes: Route[] = [];

  add(method: string, path: string, handler: RouteHandler): void {
    this.routes.push({ method: method.toUpperCase(), segments: splitPath(path), handler });
  }

  get(path: string, handler: RouteHandler): void {
    this.add("GET", path, handler);
  }

  put(path: string, handler: RouteHandler): void {
    this.add("PUT", path, handler);
  }

  post(path: string, handler: RouteHandler): void {
    this.add("POST", path, handler);
  }

  /** Returns the matched handler/params, or null with a flag for "path matched, method didn't". */
  match(method: string, pathname: string): { handler: RouteHandler; params: Record<string, string> } | "method_not_allowed" | null {
    const segments = splitPath(pathname);
    let pathMatchedOtherMethod = false;
    for (const route of this.routes) {
      if (route.segments.length !== segments.length) continue;
      const params: Record<string, string> = {};
      let matched = true;
      for (let i = 0; i < segments.length; i++) {
        const routeSegment = route.segments[i] as string;
        const actual = segments[i] as string;
        if (routeSegment.startsWith(":")) {
          params[routeSegment.slice(1)] = decodeURIComponent(actual);
        } else if (routeSegment !== actual) {
          matched = false;
          break;
        }
      }
      if (!matched) continue;
      if (route.method !== method.toUpperCase()) {
        pathMatchedOtherMethod = true;
        continue;
      }
      return { handler: route.handler, params };
    }
    return pathMatchedOtherMethod ? "method_not_allowed" : null;
  }
}
