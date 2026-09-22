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
    this.sweepIdle();
    return {
      status: 'ok',
      version: VERSION,
      transport: 'rest',
      protocol_version: PROTOCOL_VERSION,
      uptime_seconds: Math.floor((Date.now() - this.startedAt) / 1000),
      active_sessions: this.activeCount(),
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

const harness = new EchoHarness();
const app = new Hono();

// Shadow GET /v1/sessions/:session_id BEFORE mounting the SDK router so
// the scaffolded harness emits its own lifecycle status (active →
// completed once the loop ends). The SDK router's session GET leaves
// status pinned to "active" for ended sessions, which the 46-test battery
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
