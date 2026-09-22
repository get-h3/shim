// Package main — generated H3 echo harness.
//
// This file was scaffolded by `hermes-h3 scaffold --lang go` from
// get-h3/shim/src/h3_shim/templates/go/main.go. It implements a minimal
// but H3-compliant harness: every user message is echoed back as text,
// session state is tracked per session_id, and the loop ends after two
// result callbacks (matching the protocol's typical 2-turn conversation).
//
// Session liveness + GC (DF5-H3-SHIM-2): a session is *live* only while the
// harness still owes it work — from the POST /v1/process that opened the
// turn until that turn is answered by POST /v1/result, is cancelled, or
// ends. Health().ActiveSessions counts live sessions only. A session that
// has been answered stays in the map for a bounded closing window (so the
// loop's END callback and GET /v1/sessions still resolve) and is then
// dropped by SweepIdle — an idle TTL (H3_SESSION_TTL_S, default 30s) plus a
// hard entry cap (H3_SESSION_MAX) — so the table can no longer grow with
// every finished conversation, and one-shot / error-path / cancelled
// sessions cannot leak.
//
// OWNERSHIP NOTE (measured live, DF5-H3-SHIM-2): the sdk-go HTTP server does
// NOT put this method's count on the wire — its health handler overwrites
// active_sessions with the size of its OWN session store (harness.go,
// GAP-050 supplier split), and that store drops an entry only on DELETE
// /v1/sessions/{id}. This harness owns a truthful, bounded count; the
// scaffold's /v1/health active_sessions stops growing only once the SDK's
// store grows an END purge / TTL of its own.
//
// To customise:
//  1. Replace OnProcess / OnResult with your own logic.
//  2. Update the package name if you wish.
//  3. Re-run `go build ./...` — that's it.
package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/get-h3/sdk-go/harness"
	"github.com/get-h3/sdk-go/protocol"
)

const (
	// DefaultSessionTTL is the idle window after which any session entry —
	// live or retained — is dropped. Override with H3_SESSION_TTL_S
	// (seconds); zero or less disables the TTL sweep.
	DefaultSessionTTL = 30 * time.Second
	// DefaultMaxSessions is the hard cap on tracked entries; beyond it the
	// least-recently-active entries are evicted. Override with
	// H3_SESSION_MAX. Backstop so the table stays bounded even with the TTL
	// disabled.
	DefaultMaxSessions = 1024
)

// EchoHarness echoes the user message back and tracks per-session state.
type EchoHarness struct {
	mu          sync.Mutex
	sessions    map[string]*sessionState
	sessionTTL  time.Duration
	maxSessions int
}

type sessionState struct {
	mu            sync.Mutex
	createdAt     time.Time
	lastActive    time.Time
	resultCount   int
	streamingMode bool
	// live is true while the harness still owes this session work. It goes
	// false once a non-streaming turn has been answered, on cancel, and on
	// END; only live sessions are counted by Health().ActiveSessions.
	live bool
}

// sessionLimits reads the GC knobs (H3_SESSION_TTL_S / H3_SESSION_MAX).
func sessionLimits() (time.Duration, int) {
	ttl := DefaultSessionTTL
	if raw := os.Getenv("H3_SESSION_TTL_S"); raw != "" {
		if secs, err := strconv.ParseFloat(raw, 64); err == nil {
			ttl = time.Duration(secs * float64(time.Second))
		}
	}
	limit := DefaultMaxSessions
	if raw := os.Getenv("H3_SESSION_MAX"); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	return ttl, limit
}

// NewEchoHarness constructs an EchoHarness with the per-session map initialised.
func NewEchoHarness() *EchoHarness {
	ttl, limit := sessionLimits()
	return &EchoHarness{
		sessions:    make(map[string]*sessionState),
		sessionTTL:  ttl,
		maxSessions: limit,
	}
}

func (h *EchoHarness) stateFor(sessionID string) *sessionState {
	h.mu.Lock()
	defer h.mu.Unlock()
	st, ok := h.sessions[sessionID]
	if !ok {
		now := time.Now()
		st = &sessionState{createdAt: now, lastActive: now, live: true}
		h.sessions[sessionID] = st
	}
	st.mu.Lock()
	st.lastActive = time.Now()
	st.mu.Unlock()
	return st
}

// SweepIdle drops entries that outlived the idle TTL and enforces the hard
// session cap, returning how many were dropped.
//
// DF5-H3-SHIM-2: every request path calls this, so a long-lived harness
// bounds its session table instead of accumulating one entry per
// conversation forever — the leak the on-END purge alone could not close
// (sessions that never receive a second result: one-shot, error-path,
// cancel tests and streaming turns).
func (h *EchoHarness) SweepIdle(now time.Time) int {
	h.mu.Lock()
	defer h.mu.Unlock()

	dropped := 0
	if h.sessionTTL > 0 {
		for id, st := range h.sessions {
			st.mu.Lock()
			idle := now.Sub(st.lastActive)
			st.mu.Unlock()
			if idle > h.sessionTTL {
				delete(h.sessions, id)
				dropped++
			}
		}
	}
	if overflow := len(h.sessions) - h.maxSessions; overflow > 0 {
		ids := make([]string, 0, len(h.sessions))
		lastActive := make(map[string]time.Time, len(h.sessions))
		for id, st := range h.sessions {
			st.mu.Lock()
			lastActive[id] = st.lastActive
			st.mu.Unlock()
			ids = append(ids, id)
		}
		sort.Slice(ids, func(i, j int) bool {
			return lastActive[ids[i]].Before(lastActive[ids[j]])
		})
		for _, id := range ids[:overflow] {
			delete(h.sessions, id)
			dropped++
		}
	}
	return dropped
}

// OnProcess echoes the user's message content. Messages containing
// "do not finish" trigger streaming mode (Finished: false).
func (h *EchoHarness) OnProcess(req *protocol.ProcessRequest) (*protocol.Decision, error) {
	h.SweepIdle(time.Now())
	st := h.stateFor(req.SessionID)

	content := fmt.Sprintf("Echo: %s", req.Message.Content)

	// Detect streaming mode for this session. A new user turn re-activates a
	// session that had gone quiet (DF5-H3-SHIM-2).
	st.mu.Lock()
	st.streamingMode = strings.Contains(req.Message.Content, "do not finish")
	st.live = true
	streaming := st.streamingMode
	st.mu.Unlock()

	finished := !streaming

	// Echo conversation history so callers can verify context is preserved.
	history := make([]protocol.HistoryEntry, len(req.Context.History))
	for i, entry := range req.Context.History {
		history[i] = protocol.HistoryEntry{Role: entry.Role, Content: entry.Content}
	}

	return &protocol.Decision{
		Decision:   protocol.DecisionText,
		DecisionID: "echo-process",
		Text:       &protocol.TextResp{Content: content, Finished: finished},
		History:    history,
	}, nil
}

// OnResult acknowledges the prior decision and ends the session after
// enough turns (skipping the end in streaming mode).
func (h *EchoHarness) OnResult(req *protocol.ResultRequest) (*protocol.Decision, error) {
	h.SweepIdle(time.Now())
	st := h.stateFor(req.SessionID)

	st.mu.Lock()
	st.resultCount++
	streaming := st.streamingMode
	count := st.resultCount
	if !streaming && count < 2 {
		// DF5-H3-SHIM-2: the harness has answered the only result of a
		// non-streaming turn, so nothing is outstanding any more — the
		// session stops being counted as live. The entry is retained for
		// the loop's closing callback (the END branch below) and for
		// GET /v1/sessions, and the idle sweep forgets it afterwards.
		st.live = false
	}
	st.mu.Unlock()

	// End after 2 results in normal mode; stay alive in streaming mode.
	if !streaming && count >= 2 {
		// Session GC (DF4-H3-SHIM-2): an END decision terminates the
		// conversation — drop the per-session state here so the map and
		// Health()'s active_sessions stay live-only, mirroring the py
		// template's pop-on-END semantics.
		h.mu.Lock()
		delete(h.sessions, req.SessionID)
		h.mu.Unlock()

		return &protocol.Decision{
			Decision:   protocol.DecisionEnd,
			DecisionID: "echo-end",
			End: &protocol.End{
				Reason:  protocol.EndTaskComplete,
				Summary: "Echo conversation complete",
			},
		}, nil
	}

	content := fmt.Sprintf("Result received: %s", req.DecisionID)
	finished := !streaming
	return &protocol.Decision{
		Decision:   protocol.DecisionText,
		DecisionID: "echo-result",
		Text:       &protocol.TextResp{Content: content, Finished: finished},
	}, nil
}

// OnCancel marks an interrupted conversation as no longer live: it owes no
// more work, so it stops counting towards Health().ActiveSessions and the
// idle sweep forgets it (DF5-H3-SHIM-2).
func (h *EchoHarness) OnCancel(req *protocol.CancelRequest) error {
	h.SweepIdle(time.Now())
	h.mu.Lock()
	st, ok := h.sessions[req.SessionID]
	h.mu.Unlock()
	if !ok {
		return nil
	}
	st.mu.Lock()
	st.live = false
	st.mu.Unlock()
	return nil
}

// OnSessionTerminate drops session state.
func (h *EchoHarness) OnSessionTerminate(sessionID string) error {
	h.mu.Lock()
	delete(h.sessions, sessionID)
	h.mu.Unlock()
	return nil
}

// Health reports the harness is healthy and advertises the DecisionText capability.
// ActiveSessions is this harness's own count of LIVE sessions; see the
// package-level OWNERSHIP NOTE — the sdk-go server overwrites the field on
// the wire with its own store's size.
func (h *EchoHarness) Health() *protocol.HealthResponse {
	h.SweepIdle(time.Now())

	h.mu.Lock()
	active := 0
	for _, st := range h.sessions {
		st.mu.Lock()
		if st.live {
			active++
		}
		st.mu.Unlock()
	}
	h.mu.Unlock()

	return &protocol.HealthResponse{
		Status:          protocol.HealthOK,
		Version:         "1.0.0",
		Transport:       "rest",
		ProtocolVersion: "1.0",
		Capabilities:    []protocol.DecisionType{protocol.DecisionText},
		ActiveSessions:  active,
	}
}

func main() {
	h := harness.NewHTTPServer(NewEchoHarness())
	port := os.Getenv("PORT")
	if port == "" {
		port = "9191"
	}
	addr := ":" + port
	log.Printf("h3-harness (go) listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, h))
}
