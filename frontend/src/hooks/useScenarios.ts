"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ScenarioInfo, TeamCard } from "@/lib/contracts";

let cache: Promise<ScenarioInfo[]> | null = null;

/** The scenarios (titles, team colours, example claims), fetched once per page load. */
export function loadScenarios(): Promise<ScenarioInfo[]> {
  cache ??= api.scenarios().then((result) => {
    if (!result.ok) {
      cache = null; // try again next time
      return [];
    }
    return result.data;
  });
  return cache;
}

export function useScenarios(): ScenarioInfo[] | null {
  const [scenarios, setScenarios] = useState<ScenarioInfo[] | null>(null);
  useEffect(() => {
    let alive = true;
    void loadScenarios().then((found) => {
      if (alive) setScenarios(found);
    });
    return () => {
      alive = false;
    };
  }, []);
  return scenarios;
}

/** The scenario of a match id such as "dominant_loser-42". */
export function scenarioOf(
  scenarios: ScenarioInfo[] | null,
  matchId: string | null,
): ScenarioInfo | null {
  if (!scenarios || !matchId) return null;
  return scenarios.find((s) => matchId.startsWith(`${s.key}-`)) ?? null;
}

export function teamCard(
  scenario: ScenarioInfo | null,
  teamId: string | null | undefined,
): TeamCard | null {
  if (!scenario || !teamId) return null;
  if (scenario.home.id === teamId) return scenario.home;
  if (scenario.away.id === teamId) return scenario.away;
  return null;
}
