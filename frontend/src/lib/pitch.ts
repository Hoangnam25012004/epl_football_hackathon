// Event coordinates on one pitch (docs/day4-design.md section 4.6). Every event's (x, y) is in
// metres on a 105 x 68 pitch, in the frame of the team that made it (it attacks towards x = 105,
// Day 1 section 3.1). The home team is drawn as is, the away team rotated by 180 degrees, so the
// two teams attack in opposite directions on the same drawing. Geometry only, never a statistic.
export const PITCH_LENGTH = 105;
export const PITCH_WIDTH = 68;

export type PitchPoint = { x: number; y: number };

export function toPitch(
  event: { x?: number | null; y?: number | null; team?: string | null },
  homeId: string,
): PitchPoint | null {
  if (event.x === null || event.x === undefined || event.y === null || event.y === undefined) {
    return null;
  }
  const x = Math.min(PITCH_LENGTH, Math.max(0, event.x));
  const y = Math.min(PITCH_WIDTH, Math.max(0, event.y));
  if (event.team && event.team !== homeId) return { x: PITCH_LENGTH - x, y: PITCH_WIDTH - y };
  return { x, y };
}
