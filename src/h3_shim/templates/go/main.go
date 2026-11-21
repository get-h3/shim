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
	"bytes"
	"fmt"
	"io"
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

// ActiveSessions reports how many tracked sessions still owe the loop work —
// this harness's own count of LIVE sessions, which is what Health() puts in the
// active_sessions field (see the package-level OWNERSHIP NOTE: the sdk-go
// server overwrites that field on the wire with the size of its own store). It
// sweeps expired entries first, so the /metrics gauge and /v1/health measure the
// same session set instead of two different recounts.
func (h *EchoHarness) ActiveSessions() int {
	h.SweepIdle(time.Now())

	h.mu.Lock()
	defer h.mu.Unlock()
	active := 0
	for _, st := range h.sessions {
		st.mu.Lock()
		if st.live {
			active++
		}
		st.mu.Unlock()
	}
	return active
}

// Health reports the harness is healthy. Capabilities must list EVERY decision
// type this harness emits — declaring fewer capabilities than you emit lies to
// capability-routing consumers (DF4-H3-SHIM-4). This harness emits text
// (OnProcess, and the non-ending OnResult branch) and end (OnResult once the
// second result of a non-streaming turn arrives).
func (h *EchoHarness) Health() *protocol.HealthResponse {
	active := h.ActiveSessions()

	return &protocol.HealthResponse{
		Status:          protocol.HealthOK,
		Version:         "1.0.0",
		Transport:       "rest",
		ProtocolVersion: "1.0",
		// Capabilities must match what this harness actually emits:
		// declaring fewer capabilities than you emit lies to
		// capability-routing consumers.
		Capabilities:   []protocol.DecisionType{protocol.DecisionText, protocol.DecisionEnd},
		ActiveSessions: active,
	}
}

// ── Metrics (H3 harness SDK contract, obs-spec §5.4) ────────────────────────
//
// Every scaffolded harness serves the protocol's four harness metric families
// on GET /metrics, on the same listener as the H3 routes, in Prometheus text
// exposition format:
//
//	h3_harness_requests_total            counter   endpoint, decision, lang
//	h3_harness_request_duration_seconds  histogram endpoint, lang
//	h3_harness_errors_total              counter   endpoint, status_class
//	h3_harness_sessions_active           gauge     lang
//
// The registry is hand-rolled (two counters, one gauge, one fixed-bucket
// histogram) so the generated project stays dependency-light: four families do
// not justify a Prometheus client library in every scaffolded harness.
//
// Labels stay BOUNDED, which is what keeps the series count cheap to scrape:
// `lang` is fixed by this template; `endpoint` is always a route TEMPLATE
// ("process", "result", "health", "cancel", "sessions") and never a session id
// or a raw path; `decision` is the H3 decision type the turn produced, "none"
// for an endpoint that answers no decision, or "error" for a 4xx/5xx.
const metricsLang = "go"

// durationBuckets is the fixed bucket grid for request_duration_seconds — the
// Prometheus default set, which resolves a decision turn's p50/p95 from 5 ms up.
var durationBuckets = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}

// endpointTemplates is the bounded `endpoint` label set: one entry per H3 route
// template this harness serves.
var endpointTemplates = []string{"cancel", "health", "process", "result", "sessions"}

// decisionlessEndpoints are the route templates that answer without an H3
// decision; their request series is registered at zero under decision="none".
var decisionlessEndpoints = []string{"cancel", "health", "sessions"}

// statusClasses is the bounded `status_class` label set for errors_total.
var statusClasses = []string{"4xx", "5xx"}

// decisionLabels bounds the `decision` label to the protocol's decision types,
// so a malformed or hostile response body can never widen it.
var decisionLabels = map[string]bool{
	"delegate": true,
	"end":      true,
	"llm_call": true,
	"text":     true,
	"tool_call": true,
	"wait":     true,
}

// latencyHistogram is a fixed-bucket histogram. counts[i] holds observations in
// (bucket i-1, bucket i]; the last slot is +Inf. The exposition renders the
// cumulative counts Prometheus scrapes.
type latencyHistogram struct {
	counts       []uint64
	sum          float64
	observations uint64
}

func newLatencyHistogram() *latencyHistogram {
	return &latencyHistogram{counts: make([]uint64, len(durationBuckets)+1)}
}

func (h *latencyHistogram) observe(v float64) {
	h.sum += v
	h.observations++
	for i, ub := range durationBuckets {
		if v <= ub {
			h.counts[i]++
			return
		}
	}
	h.counts[len(durationBuckets)]++
}

// requestKey / errorKey are the label tuples of the two counters.
type requestKey struct{ endpoint, decision string }

type errorKey struct{ endpoint, statusClass string }

// metricsRegistry is the hand-rolled Prometheus registry backing /metrics.
type metricsRegistry struct {
	lang string

	mu        sync.Mutex
	requests  map[requestKey]uint64
	errors    map[errorKey]uint64
	durations map[string]*latencyHistogram
	sessions  int
}

// newMetricsRegistry builds a registry for one harness language.
//
// It registers the ZERO STATE of every route template the harness serves, so a
// scrape arriving before any traffic still shows all four families (and a
// rate() over a freshly started harness starts at 0 instead of "no data").
// Nothing is fabricated: the seeded series are exactly the routes this harness
// answers, every value starts at 0, and values only ever move by observation.
func newMetricsRegistry(lang string) *metricsRegistry {
	m := &metricsRegistry{
		lang:      lang,
		requests:  make(map[requestKey]uint64),
		errors:    make(map[errorKey]uint64),
		durations: make(map[string]*latencyHistogram),
	}
	for _, endpoint := range endpointTemplates {
		m.durations[endpoint] = newLatencyHistogram()
		for _, class := range statusClasses {
			m.errors[errorKey{endpoint: endpoint, statusClass: class}] = 0
		}
	}
	for _, endpoint := range decisionlessEndpoints {
		m.requests[requestKey{endpoint: endpoint, decision: "none"}] = 0
	}
	return m
}

// observeRequest records one finished request: the requests counter, the
// per-endpoint latency histogram, and — for a 4xx/5xx — the error counter.
func (m *metricsRegistry) observeRequest(endpoint, decision string, status int, seconds float64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.requests[requestKey{endpoint: endpoint, decision: decision}]++
	if hist, ok := m.durations[endpoint]; ok {
		hist.observe(seconds)
	}
	if class, ok := statusClassOf(status); ok {
		m.errors[errorKey{endpoint: endpoint, statusClass: class}]++
	}
}

func statusClassOf(status int) (string, bool) {
	switch {
	case status >= 500:
		return "5xx", true
	case status >= 400:
		return "4xx", true
	}
	return "", false
}

// setSessionsActive publishes the harness's own live-session count. The caller
// passes the SAME count /v1/health reports (EchoHarness.ActiveSessions), so the
// gauge measures the session set the harness keeps — it is never a recount.
func (m *metricsRegistry) setSessionsActive(n int) {
	m.mu.Lock()
	m.sessions = n
	m.mu.Unlock()
}

// render writes the whole registry in Prometheus text exposition format
// (version 0.0.4).
func (m *metricsRegistry) render() string {
	m.mu.Lock()
	defer m.mu.Unlock()

	var b strings.Builder

	b.WriteString("# HELP h3_harness_requests_total Requests handled by this harness, by endpoint and decision.\n")
	b.WriteString("# TYPE h3_harness_requests_total counter\n")
	for _, k := range sortedRequestKeys(m.requests) {
		fmt.Fprintf(
			&b, "h3_harness_requests_total{endpoint=%q,decision=%q,lang=%q} %d\n",
			k.endpoint, k.decision, m.lang, m.requests[k],
		)
	}

	b.WriteString("# HELP h3_harness_request_duration_seconds Request latency in seconds, by endpoint.\n")
	b.WriteString("# TYPE h3_harness_request_duration_seconds histogram\n")
	for _, endpoint := range endpointTemplates {
		hist := m.durations[endpoint]
		cumulative := uint64(0)
		for i, ub := range durationBuckets {
			cumulative += hist.counts[i]
			fmt.Fprintf(
				&b, "h3_harness_request_duration_seconds_bucket{endpoint=%q,lang=%q,le=%q} %d\n",
				endpoint, m.lang, strconv.FormatFloat(ub, 'g', -1, 64), cumulative,
			)
		}
		cumulative += hist.counts[len(durationBuckets)]
		fmt.Fprintf(
			&b, "h3_harness_request_duration_seconds_bucket{endpoint=%q,lang=%q,le=\"+Inf\"} %d\n",
			endpoint, m.lang, cumulative,
		)
		fmt.Fprintf(
			&b, "h3_harness_request_duration_seconds_sum{endpoint=%q,lang=%q} %s\n",
			endpoint, m.lang, strconv.FormatFloat(hist.sum, 'g', -1, 64),
		)
		fmt.Fprintf(
			&b, "h3_harness_request_duration_seconds_count{endpoint=%q,lang=%q} %d\n",
			endpoint, m.lang, hist.observations,
		)
	}

	b.WriteString("# HELP h3_harness_errors_total Requests answered with a 4xx or 5xx, by endpoint.\n")
	b.WriteString("# TYPE h3_harness_errors_total counter\n")
	for _, k := range sortedErrorKeys(m.errors) {
		fmt.Fprintf(
			&b, "h3_harness_errors_total{endpoint=%q,status_class=%q} %d\n",
			k.endpoint, k.statusClass, m.errors[k],
		)
	}

	b.WriteString("# HELP h3_harness_sessions_active Sessions this harness still owes work, by lang.\n")
	b.WriteString("# TYPE h3_harness_sessions_active gauge\n")
	fmt.Fprintf(&b, "h3_harness_sessions_active{lang=%q} %d\n", m.lang, m.sessions)

	return b.String()
}

func sortedRequestKeys(counts map[requestKey]uint64) []requestKey {
	keys := make([]requestKey, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].endpoint != keys[j].endpoint {
			return keys[i].endpoint < keys[j].endpoint
		}
		return keys[i].decision < keys[j].decision
	})
	return keys
}

func sortedErrorKeys(counts map[errorKey]uint64) []errorKey {
	keys := make([]errorKey, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].endpoint != keys[j].endpoint {
			return keys[i].endpoint < keys[j].endpoint
		}
		return keys[i].statusClass < keys[j].statusClass
	})
	return keys
}

// serveMetrics answers GET /metrics with the text exposition body.
func (m *metricsRegistry) serveMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body := m.render()
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	if _, err := io.WriteString(w, body); err != nil {
		log.Printf("metrics: write response: %v", err)
	}
}

// maxSniffBytes bounds how much of a response body is kept to read the
// `decision` label off the JSON the SDK writes.
const maxSniffBytes = 64 << 10

// metricsWriter records the status code and a bounded copy of the response body
// for the metrics layer. It forwards every write untouched — no buffering, no
// header rewriting, no extra flush — so the H3 wire contract is unchanged.
type metricsWriter struct {
	http.ResponseWriter
	status int
	sniff  bytes.Buffer
}

func (w *metricsWriter) WriteHeader(code int) {
	if w.status == 0 {
		w.status = code
	}
	w.ResponseWriter.WriteHeader(code)
}

func (w *metricsWriter) Write(p []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	if room := maxSniffBytes - w.sniff.Len(); room > 0 {
		if room > len(p) {
			room = len(p)
		}
		w.sniff.Write(p[:room])
	}
	return w.ResponseWriter.Write(p)
}

// Unwrap lets http.ResponseController reach the underlying writer.
func (w *metricsWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

// endpointForPath maps a request path to its route TEMPLATE. Path parameters are
// collapsed ("/v1/sessions/<id>" -> "sessions"), so a session id can never
// become a label; paths outside the documented H3 surface are not recorded.
func endpointForPath(path string) (string, bool) {
	switch path {
	case "/v1/process":
		return "process", true
	case "/v1/result":
		return "result", true
	case "/v1/health":
		return "health", true
	case "/v1/cancel":
		return "cancel", true
	case "/v1/sessions":
		return "sessions", true
	}
	if strings.HasPrefix(path, "/v1/sessions/") {
		return "sessions", true
	}
	return "", false
}

// sniffDecision reads the H3 decision type out of a JSON decision response and
// returns "none" when the body carries no decision this contract knows.
func sniffDecision(body []byte) string {
	const key = `"decision"`
	idx := bytes.Index(body, []byte(key))
	if idx < 0 {
		return "none"
	}
	rest := body[idx+len(key):]
	open := bytes.IndexByte(rest, '"')
	if open < 0 {
		return "none"
	}
	rest = rest[open+1:]
	close := bytes.IndexByte(rest, '"')
	if close < 0 {
		return "none"
	}
	value := string(rest[:close])
	if !decisionLabels[value] {
		return "none"
	}
	return value
}

// withRequestMetrics serves /metrics and instruments every H3 route with the
// four harness metric families. /metrics itself and any path outside the H3
// surface (404s for unknown routes) pass through unrecorded.
func withRequestMetrics(next http.Handler, m *metricsRegistry, activeSessions func() int) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/metrics" {
			// The gauge is SET from the harness's own live-session count at
			// scrape time — the same set /v1/health reports.
			m.setSessionsActive(activeSessions())
			m.serveMetrics(w, r)
			return
		}

		endpoint, ok := endpointForPath(r.URL.Path)
		if !ok {
			next.ServeHTTP(w, r)
			return
		}

		started := time.Now()
		mw := &metricsWriter{ResponseWriter: w}
		next.ServeHTTP(mw, r)

		status := mw.status
		if status == 0 {
			status = http.StatusOK
		}
		decision := "none"
		if status >= 400 {
			decision = "error"
		} else {
			decision = sniffDecision(mw.sniff.Bytes())
		}
		m.observeRequest(endpoint, decision, status, time.Since(started).Seconds())
	})
}

func main() {
	echo := NewEchoHarness()
	h := harness.NewHTTPServer(echo)
	metrics := newMetricsRegistry(metricsLang)
	handler := withRequestMetrics(h, metrics, echo.ActiveSessions)

	port := os.Getenv("PORT")
	if port == "" {
		port = "9191"
	}
	addr := ":" + port
	log.Printf("h3-harness (go) listening on %s (metrics: %s/metrics)", addr, addr)
	log.Fatal(http.ListenAndServe(addr, handler))
}
