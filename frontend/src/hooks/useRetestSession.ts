import { useEffect, useRef, useState } from "react";

import { retestSocketUrl, type SessionEvent } from "../api/client";

export type SocketFactory = (url: string) => WebSocket;

/**
 * A live reasoning frame (issue #140) — the same socket as the transcript, but
 * not a transcript event: no `seq`, never persisted, superseded by the real
 * event once the turn lands.
 */
interface AgentDeltaFrame {
  kind: "agent_delta";
  payload: { text: string };
}

/** The session's terminal determination, once the agent has reached one. */
export interface Verdict {
  status: string;
  rationale: string;
}

// Defined once at module scope so its identity is stable across renders. The
// hook's effect depends on `makeSocket` (see below for why); if the default
// were an inline arrow function in the parameter list, a fresh instance would
// be created on every render, the effect would see a "changed" dependency
// each time, and the socket would be torn down and reopened in an infinite
// reconnect loop. A module-level const has no such problem, and factories
// callers inject (e.g. in tests) are expected to be created once too.
const defaultSocketFactory: SocketFactory = (url) => new WebSocket(url);

/**
 * Stream a retest session's transcript over its WebSocket.
 *
 * Opens a socket to `retestSocketUrl(id)` (or one built by an injected
 * `makeSocket`, e.g. a fake in tests), accumulates ordered `SessionEvent`s
 * deduped by `seq`, and derives the session's current lifecycle `status`
 * (from the latest `state_change` event's `payload.to`, defaulting to
 * `"working"`) and terminal `verdict` (the latest `verdict` event, unless a later
 * `verdict_cancelled` withdrew it).
 * The socket is closed on unmount and reopened whenever `id` changes.
 */
export function useRetestSession(id: number, makeSocket: SocketFactory = defaultSocketFactory) {
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [connected, setConnected] = useState(false);
  // The model's live reasoning for the turn in flight (issue #140). Transient by
  // design: it is not a transcript event, it has no `seq`, and it is cleared the
  // moment the turn lands so a finished turn is never described by the thinking
  // that produced it.
  const [thinking, setThinking] = useState("");
  const seen = useRef<Set<number>>(new Set());

  // Reset accumulated state when `id` changes, using React's documented
  // "adjust state while rendering" pattern rather than resetting inside the
  // effect below: calling setState here (guarded by comparing against the
  // last-seen id) lets React discard this render and immediately re-render
  // with the reset state before anything commits, so the new session starts
  // clean with no stray extra commit — and it sidesteps `set-state-in-effect`
  // (calling setState synchronously, i.e. not from a subscription callback,
  // in an effect body is a lint error here). Refs must not be touched during
  // render, so `seen` is reset in the effect instead, before the new socket's
  // handlers are attached — still ahead of any event the new socket can emit.
  const [sessionId, setSessionId] = useState(id);
  if (id !== sessionId) {
    setSessionId(id);
    setEvents([]);
    setConnected(false);
    setThinking("");
  }

  useEffect(() => {
    seen.current = new Set();
    const socket = makeSocket(retestSocketUrl(id));
    socket.onopen = () => {
      setConnected(true);
    };
    socket.onerror = () => {
      setConnected(false);
    };
    socket.onclose = () => {
      setConnected(false);
    };
    socket.onmessage = (e: MessageEvent<string>) => {
      const event = JSON.parse(e.data) as SessionEvent | AgentDeltaFrame;
      if (event.kind === "agent_delta") {
        setThinking((prev) => prev + (event as AgentDeltaFrame).payload.text);
        return;
      }
      const transcript = event as SessionEvent;
      if (seen.current.has(transcript.seq)) return;
      seen.current.add(transcript.seq);
      // A real event supersedes the reasoning that produced it.
      setThinking("");
      setEvents((prev) => [...prev, transcript]);
    };

    return () => {
      socket.close();
    };
  }, [id, makeSocket]);

  // Reopening a session (#214) does not erase its verdict: the append-only
  // transcript keeps the `verdict` event and gains a `verdict_cancelled`. So the
  // *latest* of the two decides whether a current verdict exists — otherwise a
  // reopened session keeps reporting the determination its operator withdrew.
  const verdictEvent = [...events]
    .reverse()
    .find((e) => e.kind === "verdict" || e.kind === "verdict_cancelled");
  const stateEvent = [...events].reverse().find((e) => e.kind === "state_change");

  return {
    events,
    connected,
    thinking,
    status: (stateEvent?.payload.to as string | undefined) ?? "working",
    verdict:
      verdictEvent?.kind === "verdict" ? (verdictEvent.payload as unknown as Verdict) : null,
  };
}
