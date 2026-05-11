/**
 * Application logger — Pino, structured JSON.
 *
 * Every log line carries `run_id` (when available) for correlation.
 * In dev we pretty-print; in prod we emit JSON for ingest by Loki/Datadog.
 */
import pino, { type LoggerOptions } from "pino";
import { env } from "./env.js";

const isDev = env.NODE_ENV === "development";

const options: LoggerOptions = {
  level: env.LOG_LEVEL,
  base: {
    service: env.OTEL_SERVICE_NAME,
    env: env.NODE_ENV,
  },
  redact: {
    // Never log secrets even by accident.
    paths: [
      "*.password",
      "*.token",
      "*.apiKey",
      "*.ANTHROPIC_API_KEY",
      "*.GEMINI_API_KEY",
      "*.S3_SECRET_KEY",
    ],
    censor: "[REDACTED]",
  },
  formatters: {
    level: (label) => ({ level: label }),
  },
  timestamp: pino.stdTimeFunctions.isoTime,
  ...(isDev
    ? {
        transport: {
          target: "pino-pretty",
          options: {
            colorize: true,
            singleLine: false,
            translateTime: "HH:MM:ss.l",
            ignore: "pid,hostname,service,env",
          },
        },
      }
    : {}),
};

export const logger = pino(options);

/**
 * Create a child logger pre-bound to a run_id / dealer_id.
 * Use at the start of any work scope to ensure every line carries context.
 */
export function withRun(runId: string, dealerId?: string) {
  return logger.child({ run_id: runId, ...(dealerId ? { dealer_id: dealerId } : {}) });
}
