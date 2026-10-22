"use client";

// One line of commentary for one viewer (docs/day4-design.md section 7.2): the server rendered it
// for this profile (language, register, club, beginner); the card only lays it out.
import { useState } from "react";
import { safeColor } from "@/lib/color";
import type { CommentaryView, ScenarioInfo } from "@/lib/contracts";
import { kindLabel, useT } from "@/lib/i18n";

const EXCITEMENT = [
  "text-base",
  "text-base",
  "text-lg font-semibold",
  "text-xl font-extrabold",
] as const;

// `lang` marks the server's words with their own language (the UI language may differ, U8)
export function RuleExplanation({
  rule,
  lang,
}: {
  rule: NonNullable<CommentaryView["explanation"]>;
  lang?: CommentaryView["language"];
}) {
  const { t } = useT();
  const [open, setOpen] = useState(rule.depth === "full");
  return (
    <aside
      data-testid="rule-explanation"
      data-rule={rule.rule}
      className="mt-2 rounded-md border border-sky-500/40 bg-sky-500/10 p-2 text-sm"
    >
      <div className="font-semibold text-sky-700 dark:text-sky-300">
        📘 {t("fan.rule")}: <span lang={lang}>{rule.title}</span>
      </div>
      {open ? (
        <p className="mt-0.5" lang={lang}>
          {rule.text}
        </p>
      ) : (
        <button type="button" onClick={() => setOpen(true)} className="mt-0.5 text-xs underline">
          {t("fan.more")}
        </button>
      )}
    </aside>
  );
}

export function CommentaryCard({
  item,
  scenario,
  ownTeam,
}: {
  item: CommentaryView;
  scenario: ScenarioInfo | null;
  ownTeam: string | null;
}) {
  const { lang } = useT();
  const emphasis = item.emphasis ?? "neutral";
  const own =
    scenario && ownTeam ? (scenario.home.id === ownTeam ? scenario.home : scenario.away) : null;
  const opponent =
    scenario && own ? (own.id === scenario.home.id ? scenario.away : scenario.home) : null;
  const edge =
    emphasis === "own_team"
      ? safeColor(own?.primary_color)
      : emphasis === "opponent"
        ? safeColor(opponent?.primary_color)
        : emphasis === "focus_player"
          ? "#f59e0b"
          : "transparent";
  // a quieter line is dashed and smaller, never faded: its text keeps 4.5:1 contrast
  const low = item.relevance === "low";
  return (
    <article
      data-testid="commentary-card"
      data-id={item.id}
      data-emphasis={emphasis}
      className={`item-enter rounded-lg border border-line bg-panel p-3 ${low ? "border-dashed" : ""}`}
      style={{ borderLeft: `4px solid ${edge}` }}
    >
      <div className="mb-1 flex items-center gap-2 text-xs text-muted">
        <span data-testid="commentary-minute" className="tabular font-bold text-ink">
          {item.display_minute}
        </span>
        <span>{kindLabel(lang, item.kind)}</span>
      </div>
      {item.framing ? (
        <p
          data-testid="commentary-framing"
          className="mb-1 text-sm italic text-muted"
          lang={item.language}
        >
          {item.framing}
        </p>
      ) : null}
      <p
        data-testid="commentary-text"
        lang={item.language}
        className={low ? "text-sm" : EXCITEMENT[Math.max(0, Math.min(3, item.excitement ?? 0))]}
      >
        {item.text}
      </p>
      {item.facts && item.facts.length ? (
        <ul className="mt-2 flex flex-wrap gap-1.5" lang={item.language}>
          {item.facts.map((fact) => (
            <li
              key={`${fact.label}:${fact.display}`}
              className="rounded bg-panel-2 px-1.5 py-0.5 text-xs"
            >
              <span className="text-muted">{fact.label}: </span>
              <span className="tabular font-semibold">{fact.display}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {item.explanation ? <RuleExplanation rule={item.explanation} lang={item.language} /> : null}
    </article>
  );
}
