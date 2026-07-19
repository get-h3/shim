// Package main — generated H3 echo harness.
//
// This file was scaffolded by `hermes-h3 scaffold --lang go` from
// get-h3/shim/src/h3_shim/templates/go/main.go. It implements a minimal
// but H3-compliant harness: every user message is echoed back as text,
// session state is tracked per session_id, and the loop ends after two
// result callbacks (matching the protocol's typical 2-turn conversation).
//
// To customise:
//   1. Replace OnProcess / OnResult with your own logic.
//   2. Update the package name if you wish.
//   3. Re-run `go build ./...` — that's it.
package main

import (
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/get-h3/sdk-go/harness"
	"github.com/get-h3/sdk-go/protocol"
)

// EchoHarness echoes the user message back and tracks per-session state.
type EchoHarness struct {
	mu       sync.Mutex
	sessions map[string]*sessionState
}

type sessionState struct {
	mu            sync.Mutex
	createdAt     time.Time
	resultCount   int
	streamingMode bool
}

// NewEchoHarness constructs an EchoHarness with the per-session map initialised.
func NewEchoHarness() *EchoHarness {
	return &EchoHarness{
		sessions: make(map[string]*sessionState),
	}
}

func (h *EchoHarness) stateFor(sessionID string) *sessionState {
	h.mu.Lock()
	defer h.mu.Unlock()
	st, ok := h.sessions[sessionID]
	if !ok {
		st = &sessionState{createdAt: time.Now()}
		h.sessions[sessionID] = st
	}
	return st
}

// OnProcess echoes the user's message content. Messages containing
// "do not finish" trigger streaming mode (Finished: false).
func (h *EchoHarness) OnProcess(req *protocol.ProcessRequest) (*protocol.Decision, error) {
	st := h.stateFor(req.SessionID)

	content := fmt.Sprintf("Echo: %s", req.Message.Content)

	// Detect streaming mode for this session.
	st.mu.Lock()
	st.streamingMode = strings.Contains(req.Message.Content, "do not finish")
	st.mu.Unlock()

	finished := !st.streamingMode

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
	st := h.stateFor(req.SessionID)

	st.mu.Lock()
	st.resultCount++
	streaming := st.streamingMode
	count := st.resultCount
	st.mu.Unlock()

	// End after 2 results in normal mode; stay alive in streaming mode.
	if !streaming && count >= 2 {
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

// OnCancel is a no-op for the echo harness.
func (h *EchoHarness) OnCancel(req *protocol.CancelRequest) error {
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
func (h *EchoHarness) Health() *protocol.HealthResponse {
	h.mu.Lock()
	active := len(h.sessions)
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
	addr := ":9191"
	log.Printf("h3-harness (go) listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, h))
}