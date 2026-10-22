// Verdicts are shown with a colour, an icon and words, never with colour alone (section 4.9).
import type { ClaimResult } from "./contracts";

export type Verdict = ClaimResult["verdict"];

export const VERDICT_STYLE: Record<Verdict, { icon: string; color: string; tone: string }> = {
  TRUE: { icon: "✓", color: "var(--true)", tone: "true" },
  PARTLY_TRUE: { icon: "◐", color: "var(--partly)", tone: "partly" },
  FALSE: { icon: "✗", color: "var(--false)", tone: "false" },
  INSUFFICIENT_DATA: { icon: "?", color: "var(--unknown)", tone: "unknown" },
};

export const VERDICTS: readonly Verdict[] = ["TRUE", "PARTLY_TRUE", "FALSE", "INSUFFICIENT_DATA"];
