/**
 * Generated H3 echo harness (TypeScript / Hono).
 *
 * This file was scaffolded by `hermes-h3 scaffold --lang ts` from
 * `get-h3/shim/src/h3_shim/templates/ts/index.ts`. It implements a minimal
 * but H3-compliant harness: every user message is echoed back as text,
 * session state is tracked per `session_id`, and the loop ends after two
 * result callbacks.
 *
 * Session liveness + GC (DF5-H3-SHIM-2): a session is *live* only while the
 * harness still owes it work — from the `/v1/process` that opened the turn
 * until that turn is answered by `/v1/result`, is cancelled, or ends.
 * `health().active_sessions` counts live sessions only. An answered session
 * stays in the maps for a bounded closing window (the loop's END callback
 * and `GET /v1/sessions/:id` still resolve) and is then dropped by
 * `sweepIdle` — an idle TTL (`H3_SESSION_TTL_S`, default 30s) plus a hard
 * entry cap (`H3_SESSION_MAX`) — so one-shot / error-path / cancelled
 * sessions cannot leak and the table cannot grow with every conversation.
 *
 * To run:
 *
 *   npm install
 *   npm run dev          # tsx watch
 *   # or
 *   npm run build && npm start
 *
 * The harness listens on http://localhost:9191 by default. Verify with:
 *
 *   h3-test --endpoint http://localhost:9191
 *
 * To customise:
 *
 *   1. Replace `onProcess` / `onResult` with your own logic.
 *   2. Re-run `npm run dev` — that's it.
 */

import { serve } from '@hono/node-server';
import { randomUUID } from 'node:crypto';
import { Hono } from 'hono';
import {
  createH3Router,
  type Decision,
  type Harness,
  type HealthResponse,
  type ProcessRequest,
  type ResultRequest,
} from '@get-h3/h3-harness-sdk';

const VERSION = '1.0.0';
const PROTOCOL_VERSION = '1.0';

// Session GC (DF5-H3-SHIM-2): idle TTL + hard cap on tracked sessions.
// H3_SESSION_TTL_S is in seconds; zero or less disables the TTL sweep.
const SESSION_TTL_MS = Number(process.env.H3_SESSION_TTL_S ?? '30') * 1000;
const MAX_SESSIONS = Number(process.env.H3_SESSION_MAX ?? '1024');

const DECISION_TEXT = 'text' as const;
const DECISION_END = 'end' as const;
const END_REASON_TASK_COMPLETE = 'task_complete' as const;

interface SessionRecord {
  session_id: string;
  started_at: string;
  last_active: string;
  turn_count: number;
  status: 'active' | 'completed';
  current_decision: string;
  current_decision_type: string;
}

interface SessionState {
  resultCount: number;
  streamingMode: boolean;
  /** Epoch ms of the last request that touched this session (idle sweep). */
  lastActive: number;
  /**
   * True while the harness still owes this session work (DF5-H3-SHIM-2).
   * It goes false once a non-streaming turn has been answered, so a
   * one-shot / error-path / cancelled conversation stops counting in
   * `health().active_sessions`.
   */
  live: boolean;
}

class EchoHarness implements Harness {
  private readonly sessions = new Map<string, SessionState>();
  private readonly records = new Map<string, SessionRecord>();
  private readonly startedAt = Date.now();

  private stateFor(sessionId: string): SessionState {
    let st = this.sessions.get(sessionId);
    if (!st) {
      st = {
        resultCount: 0,
        streamingMode: false,
        lastActive: Date.now(),
        live: true,
      };
      this.sessions.set(sessionId, st);
    }
    st.lastActive = Date.now();
    return st;
  }

  /**
   * Session GC (DF5-H3-SHIM-2): drop entries that outlived the idle TTL and
   * evict the least-recently-active beyond the hard cap, returning how many
   * session entries were dropped.
   *
   * Every request path calls this, so a long-lived harness bounds its table
   * instead of accumulating one entry per conversation forever — the leak
   * the on-END purge alone could not close, because one-shot, error-path,
   * cancel and streaming sessions never receive a second result and so
   * never reached END.
   */
  private sweepIdle(now = Date.now()): number {
    let dropped = 0;
    if (SESSION_TTL_MS > 0) {
      for (const [sid, st] of this.sessions) {
        if (now - st.lastActive > SESSION_TTL_MS) {
          this.sessions.delete(sid);
          dropped += 1;
        }
      }
      for (const [sid, rec] of this.records) {
        const age = now - Date.parse(rec.last_active);
        if (Number.isFinite(age) && age > SESSION_TTL_MS) {
          this.records.delete(sid);
        }
      }
    }
    const overflow = this.sessions.size - MAX_SESSIONS;
    if (overflow > 0) {
      const oldest = [...this.sessions.entries()].sort(
        (a, b) => a[1].lastActive - b[1].lastActive,
      );
      for (const [sid] of oldest.slice(0, overflow)) {
        this.sessions.delete(sid);
        this.records.delete(sid);
        dropped += 1;
      }
    }
    return dropped;
  }

  /** Live sessions only: entries that still owe the loop work. */
  private activeCount(): number {
    let count = 0;
    for (const st of this.sessions.values()) {
      if (st.live) count += 1;
    }
    return count;
  }

  /**
   * Live sessions — the count `health().active_sessions` reports. The metrics
   * gauge is SET from this, so it measures the same session set /v1/health
   * measures instead of a second, drifting recount.
   */
  activeSessions(): number {
    this.sweepIdle();
    return this.activeCount();
  }

  /**
   * Per-session record for GET /v1/sessions/:id. Mirrors the H3
   * SessionResponse wire shape and tracks the lifecycle locally so the
   * scaffolded harness can emit `status: "completed"` once the loop has
   * ended — the SDK router (as of h3-harness-sdk via github:get-h3/
   * sdk-typescript) records sessions but leaves status pinned to "active",
   * which fails the battery's session_status_completed assertion (GAP-045).
   */
  private recordFor(sessionId: string, decision: Pick<Decision, 'decision' | 'decision_id'>): SessionRecord {
    const now = new Date().toISOString();
    const existing = this.records.get(sessionId);
    if (existing) {
      existing.last_active = now;
      existing.turn_count += 1;
      existing.current_decision = decision.decision_id;
      existing.current_decision_type = decision.decision;
      if (decision.decision === DECISION_END) {
        existing.status = 'completed';
      }
      return existing;
    }
    const record: SessionRecord = {
      session_id: sessionId,
      started_at: now,
      last_active: now,
      turn_count: 1,
      status: decision.decision === DECISION_END ? 'completed' : 'active',
      current_decision: decision.decision_id,
      current_decision_type: decision.decision,
    };
    this.records.set(sessionId, record);
    return record;
  }

  /** Public read of the per-session record (undefined when unknown). */
  sessionRecord(sessionId: string): SessionRecord | undefined {
    return this.records.get(sessionId);
  }

  health(): HealthResponse {
    return {
      status: 'ok',
      version: VERSION,
      transport: 'rest',
      protocol_version: PROTOCOL_VERSION,
      uptime_seconds: Math.floor((Date.now() - this.startedAt) / 1000),
      active_sessions: this.activeSessions(),
      // Capabilities must match what this harness actually emits: declaring
      // fewer capabilities than you emit lies to capability-routing consumers.
      // This harness emits text (onProcess, and the non-ending onResult
      // branch) and end (onResult once the second result arrives).
      capabilities: [DECISION_TEXT, DECISION_END],
    };
  }

  async onProcess(req: ProcessRequest) {
    this.sweepIdle();
    const st = this.stateFor(req.session_id);
    st.streamingMode = req.message.content.includes('do not finish');
    // A new user turn re-activates a session that had gone quiet
    // (DF5-H3-SHIM-2).
    st.live = true;

    const history = (req.context?.history ?? []).map((m: { role: string; content: string }) => ({
      role: m.role,
      content: m.content,
    }));

    const decision = {
      decision: DECISION_TEXT,
      decision_id: randomUUID(),
      history,
      text: {
        content: `Echo: ${req.message.content}`,
        finished: !st.streamingMode,
      },
    };
    this.recordFor(req.session_id, decision);
    return decision;
  }

  async onResult(req: ResultRequest) {
    this.sweepIdle();
    const st = this.stateFor(req.session_id);
    st.resultCount += 1;

    let decision: Decision;
    if (!st.streamingMode && st.resultCount >= 2) {
      decision = {
        decision: DECISION_END,
        decision_id: randomUUID(),
        history: [],
        end: {
          reason: END_REASON_TASK_COMPLETE,
          summary: 'Echo conversation complete',
        },
      };
    } else {
      // DF5-H3-SHIM-2: the harness has answered the only result of a
      // non-streaming turn with its final text, so nothing is outstanding
      // any more — the session stops being counted as live. The entry is
      // retained for the loop's closing result callback (answered with END
      // above) and for GET /v1/sessions/:id, and the idle sweep forgets it
      // afterwards, so one-shot conversations cannot accumulate.
      if (!st.streamingMode) {
        st.live = false;
      }
      decision = {
        decision: DECISION_TEXT,
        decision_id: randomUUID(),
        history: [],
        text: {
          content: `Result received: ${req.decision_id}`,
          finished: !st.streamingMode,
        },
      };
    }
    this.recordFor(req.session_id, decision);
    // Session GC (DF4-H3-SHIM-2): an END decision terminates the
    // conversation — drop both per-session maps here (mirroring the py
    // template's pop-on-END) so health() counts only live sessions.
    // The per-session lookup then 404s per the battery's ended-session
    // contract; a DELETE still tears down an in-flight session.
    if (decision.decision === DECISION_END) {
      this.sessions.delete(req.session_id);
      this.records.delete(req.session_id);
    }
    return decision;
  }

  async onCancel() {
    // No-op for the echo harness.
    return true;
  }

  async onSessionTerminate(sessionId: string) {
    this.sessions.delete(sessionId);
    this.records.delete(sessionId);
  }
}

// ── Metrics (H3 harness SDK contract, obs-spec §5.4) ─────────────────────────
// Every scaffolded harness serves the protocol's four harness metric families
// on GET /metrics, on the same listener as the H3 routes, in Prometheus text
// exposition format:
//
//   h3_harness_requests_total            counter   endpoint, decision, lang
//   h3_harness_request_duration_seconds  histogram endpoint, lang
//   h3_harness_errors_total              counter   endpoint, status_class
//   h3_harness_sessions_active           gauge     lang
//
// The registry is hand-rolled (two counters, one gauge, one fixed-bucket
// histogram) so the generated project stays dependency-light: four families do
// not justify a Prometheus client library in every scaffolded harness.
//
// Labels stay BOUNDED, which is what keeps the series count cheap to scrape:
// `lang` is fixed by this template, `endpoint` is always a route TEMPLATE
// ("process", "result", "health", "cancel", "sessions") and never a session id
// or a raw path, and `decision` is the H3 decision type the turn produced,
// "none" for an endpoint that answers no decision, or "error" for a 4xx/5xx.

const METRICS_LANG = 'ts';

/** Prometheus default bucket grid: resolves a turn's p50/p95 from 5 ms up. */
const DURATION_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10];

/** The bounded `endpoint` label set: one entry per H3 route template served. */
const ENDPOINT_TEMPLATES = ['cancel', 'health', 'process', 'result', 'sessions'];

/**
 * Route templates that answer without an H3 decision; their request series is
 * registered at zero under decision="none".
 */
const DECISIONLESS_ENDPOINTS = ['cancel', 'health', 'sessions'];

/** The bounded `status_class` label set for errors_total. */
const STATUS_CLASSES = ['4xx', '5xx'];

/** The bounded `decision` label set: the protocol's decision types. */
const DECISION_LABELS = new Set(['text', 'end', 'tool_call', 'llm_call', 'wait', 'delegate']);

/** Escape a label value for the text exposition format. */
function labelValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

/** The errors_total label for an HTTP status, or undefined when it is not an error. */
function statusClassOf(status: number): string | undefined {
  if (status >= 500) return '5xx';
  if (status >= 400) return '4xx';
  return undefined;
}

/**
 * Map a request path onto its route TEMPLATE.
 *
 * Path parameters collapse ("/v1/sessions/<id>" -> "sessions") so a session id
 * can never become a label; paths outside the documented H3 surface return
 * undefined and are not recorded.
 */
function endpointForPath(path: string): string | undefined {
  if (path === '/v1/process') return 'process';
  if (path === '/v1/result') return 'result';
  if (path === '/v1/health') return 'health';
  if (path === '/v1/cancel') return 'cancel';
  if (path === '/v1/sessions' || path.startsWith('/v1/sessions/')) return 'sessions';
  return undefined;
}

/** Fixed-bucket histogram; counts[i] covers (bucket i-1, bucket i]. */
class LatencyHistogram {
  readonly counts: number[] = new Array(DURATION_BUCKETS.length + 1).fill(0);
  sum = 0;
  observations = 0;

  observe(seconds: number): void {
    this.sum += seconds;
    this.observations += 1;
    for (let i = 0; i < DURATION_BUCKETS.length; i += 1) {
      if (seconds <= DURATION_BUCKETS[i]) {
        this.counts[i] += 1;
        return;
      }
    }
    this.counts[DURATION_BUCKETS.length] += 1;
  }
}

/**
 * Hand-rolled Prometheus registry backing GET /metrics.
 *
 * Single-threaded by construction (Node runs the JS loop on one thread), and
 * bounded: the label sets are fixed constants, so the series count cannot grow
 * with traffic.
 */
class MetricsRegistry {
  private readonly requests = new Map<string, number>();
  private readonly errors = new Map<string, number>();
  private readonly durations = new Map<string, LatencyHistogram>();
  private sessionsActive = 0;

  constructor(private readonly lang: string) {
    // Register the ZERO STATE of every route template this harness serves, so a
    // scrape arriving before any traffic still shows all four families (and
    // rate() over a freshly started harness starts at 0 instead of "no data").
    // Nothing is fabricated: the seeded series are exactly the routes this
    // harness answers, every value starts at 0, and values only ever move by
    // observation.
    for (const endpoint of ENDPOINT_TEMPLATES) {
      this.durations.set(endpoint, new LatencyHistogram());
      for (const statusClass of STATUS_CLASSES) {
        this.errors.set(`${endpoint}\u0000${statusClass}`, 0);
      }
    }
    for (const endpoint of DECISIONLESS_ENDPOINTS) {
      this.requests.set(`${endpoint}\u0000none`, 0);
    }
  }

  /** Record one finished request: request counter, latency histogram, errors. */
  observeRequest(endpoint: string, decision: string, status: number, seconds: number): void {
    const requestKey = `${endpoint}\u0000${decision}`;
    this.requests.set(requestKey, (this.requests.get(requestKey) ?? 0) + 1);
    this.durations.get(endpoint)?.observe(seconds);
    const statusClass = statusClassOf(status);
    if (statusClass !== undefined) {
      const errorKey = `${endpoint}\u0000${statusClass}`;
      this.errors.set(errorKey, (this.errors.get(errorKey) ?? 0) + 1);
    }
  }

  /**
   * Publish the harness's own live-session count. The caller passes the SAME
   * count /v1/health reports (`EchoHarness.activeSessions`), so the gauge
   * measures the session set the harness keeps — never a recount from thin air.
   */
  setSessionsActive(count: number): void {
    this.sessionsActive = count;
  }

  /** Render the whole registry in Prometheus text exposition format. */
  render(): string {
    const lang = labelValue(this.lang);
    const lines: string[] = [
      '# HELP h3_harness_requests_total Requests handled by this harness, by endpoint and decision.',
      '# TYPE h3_harness_requests_total counter',
    ];
    for (const key of [...this.requests.keys()].sort()) {
      const [endpoint, decision] = key.split('\u0000');
      lines.push(
        `h3_harness_requests_total{endpoint="${labelValue(endpoint)}",decision="${labelValue(
          decision,
        )}",lang="${lang}"} ${this.requests.get(key) ?? 0}`,
      );
    }

    lines.push(
      '# HELP h3_harness_request_duration_seconds Request latency in seconds, by endpoint.',
      '# TYPE h3_harness_request_duration_seconds histogram',
    );
    for (const endpoint of ENDPOINT_TEMPLATES) {
      const histogram = this.durations.get(endpoint) ?? new LatencyHistogram();
      const label = `endpoint="${labelValue(endpoint)}",lang="${lang}"`;
      let cumulative = 0;
      DURATION_BUCKETS.forEach((upper, index) => {
        cumulative += histogram.counts[index];
        lines.push(
          `h3_harness_request_duration_seconds_bucket{${label},le="${upper}"} ${cumulative}`,
        );
      });
      cumulative += histogram.counts[DURATION_BUCKETS.length];
      lines.push(
        `h3_harness_request_duration_seconds_bucket{${label},le="+Inf"} ${cumulative}`,
      );
      lines.push(`h3_harness_request_duration_seconds_sum{${label}} ${histogram.sum}`);
      lines.push(
        `h3_harness_request_duration_seconds_count{${label}} ${histogram.observations}`,
      );
    }

    lines.push(
      '# HELP h3_harness_errors_total Requests answered with a 4xx or 5xx, by endpoint.',
      '# TYPE h3_harness_errors_total counter',
    );
    for (const key of [...this.errors.keys()].sort()) {
      const [endpoint, statusClass] = key.split('\u0000');
      lines.push(
        `h3_harness_errors_total{endpoint="${labelValue(endpoint)}",status_class="${labelValue(
          statusClass,
        )}"} ${this.errors.get(key) ?? 0}`,
      );
    }

    lines.push(
      '# HELP h3_harness_sessions_active Sessions this harness still owes work, by lang.',
      '# TYPE h3_harness_sessions_active gauge',
      `h3_harness_sessions_active{lang="${lang}"} ${this.sessionsActive}`,
    );
    return `${lines.join('\n')}\n`;
  }
}

const metrics = new MetricsRegistry(METRICS_LANG);

/** HTTP status of a thrown error, when the framework carries one. */
function statusOfThrown(err: unknown): number {
  const carried = (err as { status?: unknown; statusCode?: unknown } | null) ?? {};
  const status =
    typeof carried.status === 'number' ? carried.status : carried.statusCode;
  return typeof status === 'number' && status >= 400 ? status : 500;
}

/**
 * The H3 decision type a decision response carries, read off the JSON the SDK
 * wrote. The response is cloned, so the bytes the client receives are untouched;
 * anything unreadable or outside the bounded label set reads as "none".
 */
async function sniffDecision(res: Response): Promise<string> {
  try {
    const body = (await res.clone().json()) as { decision?: unknown };
    const value = body?.decision;
    return typeof value === 'string' && DECISION_LABELS.has(value) ? value : 'none';
  } catch {
    return 'none';
  }
}

const harness = new EchoHarness();
const app = new Hono();

// Instrument every /v1 route and serve /metrics. Registered BEFORE the routes so
// it wraps them; the H3 response for each route is passed through byte-for-byte
// (only the status code and a clone of the body are read).
app.use('/v1/*', async (c, next) => {
  const endpoint = endpointForPath(c.req.path);
  if (endpoint === undefined) {
    await next();
    return;
  }
  const started = performance.now();
  try {
    await next();
  } catch (err) {
    metrics.observeRequest(endpoint, 'error', statusOfThrown(err), (performance.now() - started) / 1000);
    throw err;
  }
  const status = c.res.status;
  const decision = status >= 400 ? 'error' : await sniffDecision(c.res);
  metrics.observeRequest(endpoint, decision, status, (performance.now() - started) / 1000);
});

app.get('/metrics', (c) => {
  // The gauge is SET from the harness's own live-session count at scrape time —
  // the same set /v1/health reports.
  metrics.setSessionsActive(harness.activeSessions());
  return c.body(metrics.render(), 200, {
    'Content-Type': 'text/plain; version=0.0.4; charset=utf-8',
  });
});

// Shadow GET /v1/sessions/:session_id BEFORE mounting the SDK router so
// the scaffolded harness emits its own lifecycle status (active →
// completed once the loop ends). The SDK router's session GET leaves
// status pinned to "active" for ended sessions, which the 48-test battery
// rejects (session_status_completed). Unknown sessions still 404 with the
// SESSION_NOT_FOUND error shape.
app.get('/v1/sessions/:session_id', (c) => {
  const sessionId = c.req.param('session_id');
  const record = harness.sessionRecord(sessionId);
  if (!record) {
    return c.json(
      {
        error: {
          code: 'SESSION_NOT_FOUND',
          message: `Session ${sessionId} not found`,
        },
      },
      404,
    );
  }
  return c.json(record);
});

app.route('/', createH3Router(harness));

const port = Number(process.env.PORT ?? 9191);
console.log(`h3-harness (ts) listening on :${port}`);

serve({ fetch: app.fetch, port });
