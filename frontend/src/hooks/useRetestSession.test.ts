import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRetestSession } from "./useRetestSession";

class FakeSocket {
  onmessage: ((e: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
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
});
