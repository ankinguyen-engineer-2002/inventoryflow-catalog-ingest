/**
 * Multi-tenant plugin (ADR-011).
 *
 * Extracts the dealer_id from request headers/JWT and attaches it to the
 * Fastify request. Downstream route handlers MUST use `req.dealerId` rather
 * than reading the header directly — this enforces the contract that every
 * authenticated path sets the tenant context.
 *
 * For the submission we accept the dealer_id from a custom header
 * `x-dealer-id`. Production should swap for JWT-based extraction inside
 * the same plugin (no consumer changes required).
 */
import fastifyPlugin from "fastify-plugin";

declare module "fastify" {
  interface FastifyRequest {
    /** Tenant context. Always set after multitenantPlugin runs. */
    dealerId: string | null;
  }
}

export const multitenantPlugin = fastifyPlugin(
  async (app) => {
    app.addHook("onRequest", (req, _reply, done) => {
      // Skip tenant requirement for health probes.
      if (req.url.startsWith("/healthz") || req.url.startsWith("/readyz") || req.url.startsWith("/metrics")) {
        req.dealerId = null;
        return done();
      }

      const headerValue = req.headers["x-dealer-id"];
      const dealerId = Array.isArray(headerValue) ? headerValue[0] : headerValue;
      req.dealerId = dealerId ?? null;
      done();
    });
  },
  { name: "multitenant" },
);
