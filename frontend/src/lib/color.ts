// Text on a team colour: black or white, whichever reads better (WCAG relative luminance).
function channel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string): number {
  const found = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!found) return 0;
  const n = parseInt(found[1] ?? "000000", 16);
  return (
    0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)
  );
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

export function textOn(background: string): "#000000" | "#ffffff" {
  return contrastRatio(background, "#000000") >= contrastRatio(background, "#ffffff")
    ? "#000000"
    : "#ffffff";
}

export const NEUTRAL_TEAM = "#64748b";

export function safeColor(hex: string | null | undefined): string {
  return hex && /^#[0-9a-f]{6}$/i.test(hex) ? hex : NEUTRAL_TEAM;
}
