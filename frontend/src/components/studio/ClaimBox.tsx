"use client";

// Check a claim and show the verdict with its numbers (docs/day4-design.md section 5.4). Every
// number is a fact's `display`; the verdict has a colour, an icon and words (section 4.9).
import { useState, type FormEvent } from "react";
import { useToast } from "@/components/common/Toast";
import { Chip, EmptyState } from "@/components/common/ui";
import { api } from "@/lib/api";
import type { ClaimResult, Fact } from "@/lib/contracts";
import { useT, type Key } from "@/lib/i18n";
import { VERDICT_STYLE } from "@/lib/verdict";

const MAX_CHARS = 300;

export function FactChips({ facts, max = 6 }: { facts: readonly Fact[]; max?: number }) {
  const { lang } = useT();
  return (
    <ul className="flex flex-wrap gap-1.5">
      {facts.slice(0, max).map((fact) => (
        <li
          key={fact.key}
          data-testid="fact"
          className="rounded border border-line bg-panel-2 px-1.5 py-0.5 text-xs"
          title={lang === "vi" ? fact.label_vi : fact.label_en}
        >
          <span className="text-muted">{lang === "vi" ? fact.label_vi : fact.label_en}: </span>
          <span className="tabular font-semibold">{fact.display}</span>
        </li>
      ))}
    </ul>
  );
}

export function VerdictPill({ verdict }: { verdict: ClaimResult["verdict"] }) {
  const { t } = useT();
  const style = VERDICT_STYLE[verdict];
  return (
    <span
      data-testid="verdict"
      data-verdict={verdict}
      className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-sm font-bold text-white"
      style={{ backgroundColor: style.color }}
    >
      <span aria-hidden>{style.icon}</span>
      {t(`verdict.${verdict}`)}
    </span>
  );
}

export function VerdictCard({
  claim,
  onDetails,
  highlight = false,
}: {
  claim: ClaimResult;
  onDetails: (claimId: string) => void;
  highlight?: boolean;
}) {
  const { t } = useT();
  return (
    <article
      data-testid="verdict-card"
      className={`item-enter flex flex-col gap-2 rounded-md border p-3 ${highlight ? "border-accent" : "border-line"}`}
      style={{ borderLeft: `4px solid ${VERDICT_STYLE[claim.verdict].color}` }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <VerdictPill verdict={claim.verdict} />
        <span className="text-xs text-muted">
          {t("studio.claimAsOf", { minute: claim.as_of_display })}
        </span>
      </div>
      <p className="font-semibold">“{claim.text}”</p>
      {claim.headline.length ? <FactChips facts={claim.headline} /> : null}
      <p className="text-sm">{claim.explanation}</p>
      <div className="flex flex-wrap items-center gap-1">
        <Chip title={t("studio.source")}>{claim.explanation_source}</Chip>
        <Chip>{claim.analyst_source === "llm" ? "LLM" : "rules"}</Chip>
        {claim.degraded.map((flag) => (
          <Chip key={flag} tone="warn">
            {t(`degraded.${flag}` as Key)}
          </Chip>
        ))}
        <button
          type="button"
          data-testid="claim-details"
          onClick={() => onDetails(claim.claim_id)}
          className="ml-auto rounded border border-line px-2 py-0.5 text-xs font-semibold hover:bg-panel-2"
        >
          {t("studio.details")}
        </button>
      </div>
    </article>
  );
}

export function ClaimBox({
  matchId,
  examples,
  history,
  onDetails,
}: {
  matchId: string;
  examples: readonly string[];
  history: ClaimResult[];
  onDetails: (claimId: string) => void;
}) {
  const { t } = useT();
  const toast = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [latest, setLatest] = useState<string | null>(null);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const claim = text.trim();
    if (claim.length < 3 || busy) return;
    setBusy(true);
    setError(null);
    const result = await api.verifyClaim(matchId, claim);
    setBusy(false);
    if (result.ok) {
      setLatest(result.data.claim_id);
      return;
    }
    if (result.error.code === "invalid") setError(result.error.message);
    else toast.show(`${t(`err.${result.error.code}`)} ${result.error.message}`, "error");
  };

  const newest = [...history].reverse();
  return (
    <div className="flex flex-col gap-3 p-3">
      <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-2">
        <div className="flex gap-2">
          <input
            data-testid="claim-input"
            value={text}
            maxLength={MAX_CHARS}
            onChange={(event) => setText(event.target.value)}
            placeholder={t("studio.claimPlaceholder")}
            aria-label={t("studio.claim")}
            className="min-w-0 flex-1 rounded border border-line bg-bg px-3 py-2 text-sm"
          />
          <button
            type="submit"
            data-testid="claim-submit"
            disabled={busy || text.trim().length < 3}
            className="rounded bg-accent px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
          >
            {busy ? t("studio.claimVerifying") : t("studio.claimVerify")}
          </button>
        </div>
        {error ? (
          <p role="alert" className="text-sm text-verdict-false">
            {error}
          </p>
        ) : null}
        {examples.length ? (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted">{t("studio.claimExamples")}:</span>
            {examples.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setText(example)}
                className="rounded-full border border-line px-2 py-0.5 hover:bg-panel-2"
              >
                {example}
              </button>
            ))}
          </div>
        ) : null}
      </form>
      <div aria-live="polite" className="flex flex-col gap-2" data-testid="claim-history">
        {newest.length === 0 ? <EmptyState>{t("studio.noClaims")}</EmptyState> : null}
        {newest.map((claim) => (
          <VerdictCard
            key={claim.claim_id}
            claim={claim}
            onDetails={onDetails}
            highlight={claim.claim_id === latest}
          />
        ))}
      </div>
    </div>
  );
}
