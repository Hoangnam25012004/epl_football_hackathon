// Verdicts are shown with a colour, an icon and words, never with colour alone (section 4.9).
// `color` follows the theme (card edges, text); `fill` is the label background under white text,
// dark enough for 4.5:1 in every theme.
import type { ClaimResult } from "./contracts";

export type Verdict = ClaimResult["verdict"];

export const VERDICT_STYLE: Record<
  Verdict,
  { icon: string; color: string; fill: string; tone: string }
> = {
  TRUE: { icon: "✓", color: "var(--true)", fill: "var(--true-fill)", tone: "true" },
  PARTLY_TRUE: { icon: "◐", color: "var(--partly)", fill: "var(--partly-fill)", tone: "partly" },
  FALSE: { icon: "✗", color: "var(--false)", fill: "var(--false-fill)", tone: "false" },
  INSUFFICIENT_DATA: {
    icon: "?",
    color: "var(--unknown)",
    fill: "var(--unknown-fill)",
    tone: "unknown",
  },
};

export const VERDICTS: readonly Verdict[] = ["TRUE", "PARTLY_TRUE", "FALSE", "INSUFFICIENT_DATA"];
