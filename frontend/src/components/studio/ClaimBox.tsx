"use client";

// Check a claim and show the verdict with its numbers (docs/day4-design.md section 5.4). Every
// number is a fact's `display`; the verdict has a colour, an icon and words (section 4.9).
import { useRef, useState, type FormEvent } from "react";
import { useToast } from "@/components/common/Toast";
import { Chip, EmptyState } from "@/components/common/ui";
import { MicButton } from "@/components/studio/MicButton";
import { api } from "@/lib/api";
import { claimView, type Line } from "@/lib/claim-source";
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
      style={{ backgroundColor: style.fill }}
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
  const view = claimView(claim);
  const say = (line: Line) =>
    line.key === "claim.updated" && line.vars
      ? t(line.key, {
          from: t(`verdict.${line.vars.from}` as Key),
          to: t(`verdict.${line.vars.to}` as Key),
        })
      : t(line.key, line.vars);
  return (
    <article
      data-testid="verdict-card"
      data-revision={claim.revision ?? 1}
      data-model-pass={claim.model_pass ?? "none"}
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
      <p className="text-sm" data-testid="claim-explanation">
        {claim.explanation}
      </p>
      {view.notes.map((note) => (
        <p key={note.key} data-testid="claim-note" className="text-xs font-semibold text-muted">
          {say(note)}
        </p>
      ))}
      {view.progress ? (
        <div role="status" data-testid="claim-progress" className="flex flex-col gap-1 text-xs">
          <span>{say(view.progress)}</span>
          <span className="progress-strip h-1 overflow-hidden rounded bg-panel-2" aria-hidden>
            <span className="block h-full w-1/3 bg-accent" />
          </span>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-1">
        {view.chips.map((chip) => (
          <span key={chip.key} data-testid="claim-source">
            <Chip tone={chip.tone ?? "neutral"} title={t("studio.source")}>
              {say(chip)}
            </Chip>
          </span>
        ))}
        {claim.degraded
          // a progressive claim says it in a note: "the model read it differently"
          .filter((flag) => !(claim.model_pass && flag === "analyst_disagreement"))
          .map((flag) => (
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
  const { t, lang } = useT();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
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
            ref={input}
            data-testid="claim-input"
            value={text}
            maxLength={MAX_CHARS}
            onChange={(event) => setText(event.target.value)}
            placeholder={t("studio.claimPlaceholder")}
            aria-label={t("studio.claim")}
            className="min-w-0 flex-1 rounded border border-line bg-bg px-3 py-2 text-sm"
          />
          <MicButton
            language={lang}
            onText={(spoken) => {
              // filled, never sent: the commentator checks it and presses Enter
              setText(spoken.slice(0, MAX_CHARS));
              setError(null);
              input.current?.focus();
            }}
          />
          <button
            type="submit"
            data-testid="claim-submit"
            disabled={busy || text.trim().length < 3}
            className="rounded bg-accent-fill px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
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
