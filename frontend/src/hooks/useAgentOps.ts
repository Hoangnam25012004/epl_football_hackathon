"use client";

// Agent Ops (docs/day7-design.md section 2.5): /ws/agents/{id} feeds the page; the statistics
// and the chaos are refreshed from GET /matches/{id}/agents every few seconds. A closed stream
// reconnects with a growing wait; an unknown match (4404) does not.
import { useCallback, useEffect, useReducer, useState } from "react";
import { api, wsUrl } from "@/lib/api";
import { EMPTY, reduce, withSnapshot, type AgentOpsState } from "@/lib/agent-ops";
import type { AgentOpsMessage, AgentOpsSnapshot } from "@/lib/contracts";

export type AgentOpsConnection = "connecting" | "open" | "reconnecting" | "unknown";
const REFRESH_MS = 3_000;
const UNKNOWN_MATCH = 4404;

type Action =
  { kind: "message"; message: AgentOpsMessage } | { kind: "snapshot"; snapshot: AgentOpsSnapshot };

function reducer(state: AgentOpsState, action: Action): AgentOpsState {
  return action.kind === "message"
    ? reduce(state, action.message)
    : withSnapshot(state, action.snapshot);
}

export function useAgentOps(matchId: string | null): {
  state: AgentOpsState;
  connection: AgentOpsConnection;
  refresh: () => Promise<void>;
} {
  const [state, dispatch] = useReducer(reducer, EMPTY);
  const [connection, setConnection] = useState<AgentOpsConnection>("connecting");

  const refresh = useCallback(async () => {
    if (!matchId) return;
    const result = await api.agents(matchId);
    if (result.ok) dispatch({ kind: "snapshot", snapshot: result.data });
  }, [matchId]);

  useEffect(() => {
    if (!matchId) return;
    let socket: WebSocket | null = null;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let alive = true;
    const connect = () => {
      socket = new WebSocket(wsUrl(`/ws/agents/${encodeURIComponent(matchId)}`));
      socket.onopen = () => {
        attempt = 0;
        setConnection("open");
      };
      socket.onmessage = (frame) => {
        try {
          dispatch({ kind: "message", message: JSON.parse(String(frame.data)) as AgentOpsMessage });
        } catch {
          // a frame that is not JSON is ignored: the next hello repairs the page
        }
      };
      socket.onclose = (closed) => {
        if (!alive) return;
        if (closed.code === UNKNOWN_MATCH) {
          setConnection("unknown");
          return;
        }
        setConnection("reconnecting");
        attempt += 1;
        timer = setTimeout(connect, Math.min(8_000, 500 * 2 ** attempt));
      };
    };
    connect();
    const poll = setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(poll);
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [matchId, refresh]);

  return { state, connection, refresh };
}
