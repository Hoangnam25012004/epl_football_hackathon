"use client";

// One live connection to a match: socket → frame batcher → store (docs/day4-design.md 4.4–4.5).
// A new match id or profile query closes the old socket and starts from a fresh hello.
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { MatchSocket, type SocketState, type SocketStats } from "@/lib/socket";
import type { AnyWsMessage } from "@/lib/ws-types";
import { createMatchStore, type MatchStore } from "@/store/match-store";
import { FrameBatcher } from "@/store/frame-batcher";

type DebugEntry = { name: string; stats: () => SocketStats; latencies: () => number[] };

declare global {
  interface Window {
    punditDebug?: { connections: Record<string, DebugEntry> };
  }
}

function register(name: string, entry: DebugEntry): () => void {
  if (typeof window === "undefined") return () => {};
  window.punditDebug ??= { connections: {} };
  window.punditDebug.connections[name] = entry;
  return () => {
    if (window.punditDebug?.connections[name] === entry)
      delete window.punditDebug.connections[name];
  };
}

export type MatchConnection = {
  store: StoreApi<MatchStore>;
  socketState: SocketState;
};

export function useMatchConnection(
  matchId: string | null,
  query: string,
  {
    name = "main",
    unknownMatch = "fatal" as "fatal" | "wait",
    onLive = undefined as ((message: AnyWsMessage) => void) | undefined,
  } = {},
): MatchConnection {
  const store = useMemo(() => createMatchStore(), []);
  const [socketState, setSocketState] = useState<SocketState>({ status: "idle" });
  const socketRef = useRef<MatchSocket | null>(null);
  // Day 5: every accepted message, in order, before frame batching (the voice queue)
  const live = useRef(onLive);
  useEffect(() => {
    live.current = onLive;
  }, [onLive]);

  useEffect(() => {
    if (!matchId) return;
    store.getState().reset();
    const batcher = new FrameBatcher<AnyWsMessage>((messages) => store.getState().apply(messages));
    const socket = new MatchSocket({
      matchId,
      query,
      unknownMatch,
      onMessage: (message) => {
        live.current?.(message);
        batcher.push(message);
      },
      onState: setSocketState,
    });
    socketRef.current = socket;
    const unregister = register(name, {
      name,
      stats: () => ({ ...socket.stats }),
      latencies: () => [...batcher.latencies],
    });
    socket.start();
    return () => {
      unregister();
      socket.stop();
      batcher.clear();
    };
  }, [matchId, query, name, unknownMatch, store]);

  return { store, socketState };
}

/** Read a slice of a connection's state (re-renders only when the slice changes). */
export function useMatch<T>(connection: MatchConnection, selector: (state: MatchStore) => T): T {
  return useStore(connection.store, selector);
}
