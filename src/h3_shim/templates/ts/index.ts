/**
 * Generated H3 echo harness (TypeScript / Hono).
 *
 * This file was scaffolded by `hermes-h3 scaffold --lang ts` from
 * `get-h3/shim/src/h3_shim/templates/ts/index.ts`. It implements a minimal
 * but H3-compliant harness: every user message is echoed back as text,
 * session state is tracked per `session_id`, and the loop ends after two
 * result callbacks.
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
import { Hono } from 'hono';
import {
  createH3Router,
  type Harness,
  type HealthResponse,
  type ProcessRequest,
  type ResultRequest,
} from '@get-h3/h3-harness-sdk';

const VERSION = '1.0.0';
const PROTOCOL_VERSION = '1.0';

const DECISION_TEXT = 'text' as const;
const DECISION_END = 'end' as const;
const END_REASON_TASK_COMPLETE = 'task_complete' as const;

interface SessionState {
  resultCount: number;
  streamingMode: boolean;
}

class EchoHarness implements Harness {
  private readonly sessions = new Map<string, SessionState>();
  private readonly startedAt = Date.now();

  private stateFor(sessionId: string): SessionState {
    let st = this.sessions.get(sessionId);
    if (!st) {
      st = { resultCount: 0, streamingMode: false };
      this.sessions.set(sessionId, st);
    }
    return st;
  }

  health(): HealthResponse {
    return {
      status: 'ok',
      version: VERSION,
      transport: 'rest',
      protocol_version: PROTOCOL_VERSION,
      uptime_seconds: Math.floor((Date.now() - this.startedAt) / 1000),
      active_sessions: this.sessions.size,
      capabilities: [DECISION_TEXT, DECISION_END],
    };
  }

  async onProcess(req: ProcessRequest) {
    const st = this.stateFor(req.session_id);
    st.streamingMode = req.message.content.includes('do not finish');

    const history = (req.context?.history ?? []).map((m: { role: string; content: string }) => ({
      role: m.role,
      content: m.content,
    }));

    return {
      decision: DECISION_TEXT,
      decision_id: 'echo-process',
      history,
      text: {
        content: `Echo: ${req.message.content}`,
        finished: !st.streamingMode,
      },
    };
  }

  async onResult(req: ResultRequest) {
    const st = this.stateFor(req.session_id);
    st.resultCount += 1;

    if (!st.streamingMode && st.resultCount >= 2) {
      return {
        decision: DECISION_END,
        decision_id: 'echo-end',
        end: {
          reason: END_REASON_TASK_COMPLETE,
          summary: 'Echo conversation complete',
        },
      };
    }

    return {
      decision: DECISION_TEXT,
      decision_id: 'echo-result',
      text: {
        content: `Result received: ${req.decision_id}`,
        finished: !st.streamingMode,
      },
    };
  }

  async onCancel() {
    // No-op for the echo harness.
    return true;
  }

  async onSessionTerminate(sessionId: string) {
    this.sessions.delete(sessionId);
  }
}

const app = new Hono();
app.route('/', createH3Router(new EchoHarness()));

const port = Number(process.env.PORT ?? 9191);
console.log(`h3-harness (ts) listening on :${port}`);

serve({ fetch: app.fetch, port });