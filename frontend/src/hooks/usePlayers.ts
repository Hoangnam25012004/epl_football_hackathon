"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PlayerBrief } from "@/lib/contracts";

const cache = new Map<string, Promise<PlayerBrief[]>>();

/** Both squads of a match (fictional names), fetched once per match. */
export function usePlayers(matchId: string | null): Map<string, PlayerBrief> {
  const [players, setPlayers] = useState<Map<string, PlayerBrief>>(new Map());
  useEffect(() => {
    if (!matchId) return;
    let alive = true;
    let pending = cache.get(matchId);
    if (!pending) {
      pending = api.players(matchId).then((result) => {
        if (!result.ok) {
          cache.delete(matchId);
          return [];
        }
        return result.data;
      });
      cache.set(matchId, pending);
    }
    void pending.then((list) => {
      if (alive) setPlayers(new Map(list.map((player) => [player.id, player])));
    });
    return () => {
      alive = false;
    };
  }, [matchId]);
  return players;
}

export function playerName(
  players: Map<string, PlayerBrief>,
  id: string | null | undefined,
): string {
  if (!id) return "";
  return players.get(id)?.short_name ?? id;
}
