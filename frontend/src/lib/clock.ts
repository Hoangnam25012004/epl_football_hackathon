// Football minutes, a copy of `pundit.domain.matchtime.format_minute` (docs/day4-design.md 4.6).
// A golden test checks it against every `display_minute` the server sent in the fixtures.
const MS_PER_MINUTE = 60_000;

export function formatMinute(period: number, clockMs: number): string {
  const offset = period === 1 ? 0 : 45;
  const regularEnd = period === 1 ? 45 : 90;
  const shown = offset + Math.floor(clockMs / MS_PER_MINUTE) + 1;
  return shown <= regularEnd ? `${shown}'` : `${regularEnd}+${shown - regularEnd}'`;
}
