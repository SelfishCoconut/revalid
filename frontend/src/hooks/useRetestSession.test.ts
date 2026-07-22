import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRetestSession } from "./useRetestSession";

class FakeSocket {
  onmessage: ((e: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  close = () => {};
  emit(event: unknown) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

describe("useRetestSession", () => {
  it("accumulates events and surfaces the verdict", async () => {
    const socket = new FakeSocket();
    // Factory created once, outside the render — this is what a caller does
    // in practice (a ref/module-level fn), and it lets us assert below that
    // the effect only invokes it a single time (no reconnect loop).
    let callCount = 0;
    const makeSocket = () => {
      callCount += 1;
      return socket as unknown as WebSocket;
    };

    const { result } = renderHook(() => useRetestSession(1, makeSocket));

    act(() => {
      socket.onopen?.();
    });
    act(() => {
      socket.emit({ seq: 1, kind: "command_proposed", payload: { command: "id" } });
    });
    act(() => {
      socket.emit({ seq: 2, kind: "verdict", payload: { status: "still_open", rationale: "x" } });
    });

    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.verdict?.status).toBe("still_open");
    expect(result.current.connected).toBe(true);
    expect(callCount).toBe(1);
  });

  it("marks connected false when the server closes the socket, not just on error", async () => {
    const socket = new FakeSocket();
    const makeSocket = () => socket as unknown as WebSocket;

    const { result } = renderHook(() => useRetestSession(1, makeSocket));

    act(() => {
      socket.onopen?.();
    });
    expect(result.current.connected).toBe(true);

    act(() => {
      socket.onclose?.();
    });
    expect(result.current.connected).toBe(false);
  });

  it("derives status from the latest state_change event, defaulting to starting", async () => {
    const socket = new FakeSocket();
    const makeSocket = () => socket as unknown as WebSocket;
    const { result } = renderHook(() => useRetestSession(1, makeSocket));

    expect(result.current.status).toBe("starting");

    act(() => {
      socket.emit({ seq: 1, kind: "state_change", payload: { to: "awaiting_approval" } });
    });
    await waitFor(() => expect(result.current.status).toBe("awaiting_approval"));

    act(() => {
      socket.emit({ seq: 2, kind: "state_change", payload: { to: "running" } });
    });
    await waitFor(() => expect(result.current.status).toBe("running"));
  });

  it("dedupes events by seq", async () => {
    const socket = new FakeSocket();
    const makeSocket = () => socket as unknown as WebSocket;
    const { result } = renderHook(() => useRetestSession(1, makeSocket));

    act(() => {
      socket.emit({ seq: 1, kind: "command_proposed", payload: {} });
    });
    act(() => {
      socket.emit({ seq: 1, kind: "command_proposed", payload: {} });
    });

    await waitFor(() => expect(result.current.events).toHaveLength(1));
  });

  it("does not reconnect when re-rendered with the same id and factory", async () => {
    const socket = new FakeSocket();
    let callCount = 0;
    const makeSocket = () => {
      callCount += 1;
      return socket as unknown as WebSocket;
    };

    const { rerender } = renderHook(() => useRetestSession(1, makeSocket));
    expect(callCount).toBe(1);

    rerender();
    rerender();

    expect(callCount).toBe(1);
  });

  it("closes the old socket and reconnects when id changes", async () => {
    let closeCalls = 0;
    const makeSocket = () => {
      const s = new FakeSocket();
      s.close = () => {
        closeCalls += 1;
      };
      return s as unknown as WebSocket;
    };

    const { rerender } = renderHook(({ id }) => useRetestSession(id, makeSocket), {
      initialProps: { id: 1 },
    });

    rerender({ id: 2 });

    expect(closeCalls).toBe(1);
  });

  it("resets events/status/verdict before the new id's socket can emit anything", async () => {
    const socketA = new FakeSocket();
    const socketB = new FakeSocket();
    // Stable factory reference (created once, outside any render) that hands
    // out a different fake socket per session id — mirrors how a real caller
    // would key sockets by the URL retestSocketUrl(id) produces, without
    // recreating the factory itself on every render (which would reopen the
    // reconnect-loop bug this hook's effect dependency guards against).
    const sockets = new Map([
      [1, socketA],
      [2, socketB],
    ]);
    const makeSocket = (url: string) => {
      const id = Number(/retest-sessions\/(\d+)\/stream/.exec(url)?.[1]);
      const socket = sockets.get(id);
      if (!socket) throw new Error(`no fake socket registered for id ${id}`);
      return socket as unknown as WebSocket;
    };

    const { result, rerender } = renderHook(({ id }) => useRetestSession(id, makeSocket), {
      initialProps: { id: 1 },
    });

    act(() => {
      socketA.emit({ seq: 1, kind: "command_proposed", payload: { command: "id" } });
    });
    act(() => {
      socketA.emit({
        seq: 2,
        kind: "verdict",
        payload: { status: "still_open", rationale: "x" },
      });
    });

    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.verdict?.status).toBe("still_open");

    rerender({ id: 2 });

    // Reset must be visible immediately on the re-render that picks up the
    // new id — before socket B has emitted anything — so no stale events
    // from session 1 leak into session 2's view.
    expect(result.current.events).toEqual([]);
    expect(result.current.status).toBe("starting");
    expect(result.current.verdict).toBeNull();

    act(() => {
      socketB.emit({ seq: 1, kind: "command_proposed", payload: { command: "ls" } });
    });

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0]?.payload).toEqual({ command: "ls" });
  });

  it("accumulates live reasoning deltas and clears them when the turn lands", async () => {
    // Issue #140: `agent_delta` frames carry no `seq`, are never persisted, and
    // must be superseded by the real transcript event they were leading up to.
    const socket = new FakeSocket();
    const makeSocket = () => socket as unknown as WebSocket;
    const { result } = renderHook(() => useRetestSession(1, makeSocket));

    act(() => {
      socket.emit({ kind: "agent_delta", payload: { text: "I should " } });
    });
    act(() => {
      socket.emit({ kind: "agent_delta", payload: { text: "probe the login endpoint" } });
    });

    await waitFor(() => {
      expect(result.current.thinking).toBe("I should probe the login endpoint");
    });
    // Deltas are not transcript events.
    expect(result.current.events).toHaveLength(0);

    act(() => {
      socket.emit({ seq: 1, kind: "command_proposed", payload: { command: "curl -s /login" } });
    });

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    // The landed turn replaces the reasoning that produced it.
    expect(result.current.thinking).toBe("");
  });
});
