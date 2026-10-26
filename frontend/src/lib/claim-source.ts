// Where a claim's answer comes from, in words (docs/day7-design.md sections 2.4 and 2.9): the
// rules, a template, the live model; a progressive claim's stage (queued, running, rev.2) and
// why the model did not add to it. Pure: the card renders what this returns.
import type { ClaimResult } from "@/lib/contracts";
import type { Key } from "@/lib/i18n";

export type Line = { key: Key; vars?: Record<string, string | number>; tone?: "warn" | "accent" };

export type ClaimView = {
  /** The chips under the explanation, in order. */
  chips: Line[];
  /** A progress strip while the model pass waits or runs. */
  progress: Line | null;
  /** A sentence under the verdict: the model's reading, or a changed verdict. */
  notes: Line[];
};

function seconds(ms: number | undefined): string {
  return ((ms ?? 0) / 1000).toFixed(1);
}

export function claimView(claim: ClaimResult): ClaimView {
  const chips: Line[] = [];
  const notes: Line[] = [];
  let progress: Line | null = null;
  const stage = claim.model_pass ?? null;
  const modelText = claim.explanation_source === "llm";
  if (stage === null) {
    // one pass (the claim of Days 2 to 6)
    chips.push(
      modelText
        ? { key: "claim.modelOnePass", vars: { model: claim.model }, tone: "accent" }
        : { key: "claim.rulesTemplate" },
    );
    chips.push({ key: claim.analyst_source === "llm" ? "claim.readModel" : "claim.readRules" });
    return { chips, progress, notes };
  }
  if ((claim.revision ?? 1) === 1) {
    chips.push({ key: "claim.rules", vars: { s: seconds(claim.timings_ms.total) } });
    if (stage === "queued") progress = { key: "claim.queued" };
    if (stage === "running") progress = { key: "claim.running" };
  } else if (stage === "done") {
    chips.push(
      modelText
        ? {
            key: "claim.model",
            vars: { model: claim.model, s: seconds(claim.timings_ms.model_pass) },
            tone: "accent",
          }
        : { key: "claim.rulesTemplate" },
    );
  } else {
    chips.push({ key: "claim.rules", vars: { s: seconds(claim.timings_ms.total) } });
  }
  if (stage === "skipped" && claim.skip_reason)
    chips.push({ key: `claim.skip.${claim.skip_reason}` as Key, tone: "warn" });
  if (stage === "done") {
    if (claim.degraded.includes("analyst_disagreement")) notes.push({ key: "claim.disagree" });
    else if (claim.analyst_source === "llm") notes.push({ key: "claim.agree" });
  }
  if (claim.rev1_verdict && claim.rev1_verdict !== claim.verdict)
    notes.push({
      key: "claim.updated",
      vars: { from: claim.rev1_verdict, to: claim.verdict },
    });
  return { chips, progress, notes };
}
