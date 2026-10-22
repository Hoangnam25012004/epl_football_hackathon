"use client";

// The fan's commentary feed, the player-focus tab and the recaps (docs/day4-design.md 7.2–7.4).
import { useEffect, useRef, useState } from "react";
import { EmptyState, Segmented } from "@/components/common/ui";
import type { CommentaryView, RecapView, ScenarioInfo } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import { CommentaryCard } from "./CommentaryCard";

/** Newest on top; a reader who scrolled down is not moved, a "N new" button brings them back. */
export function CommentaryFeed({
  items,
  scenario,
  ownTeam,
  waiting,
}: {
  items: CommentaryView[];
  scenario: ScenarioInfo | null;
  ownTeam: string | null;
  waiting: boolean;
}) {
  const { t } = useT();
  const list = useRef<HTMLDivElement>(null);
  // the number of items when the reader scrolled away from the top (null: at the top)
  const [seenWhenLeft, setSeenWhenLeft] = useState<number | null>(null);
  const newest = [...items].reverse();
  const count = newest.length;
  const unseen = seenWhenLeft === null ? 0 : Math.max(0, count - seenWhenLeft);

  useEffect(() => {
    const element = list.current;
    if (!element) return;
    const onScroll = () => {
      const away = element.scrollTop > 40;
      // the browser keeps the reader's place when items arrive on top (scroll anchoring)
      setSeenWhenLeft((current) => (away ? (current ?? element.childElementCount) : null));
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => element.removeEventListener("scroll", onScroll);
  }, []);

  if (count === 0) {
    return <EmptyState>{waiting ? t("fan.waitingStart") : t("fan.noCommentary")}</EmptyState>;
  }
  return (
    <div className="relative h-full">
      {unseen > 0 ? (
        <button
          type="button"
          data-testid="new-items"
          onClick={() => list.current?.scrollTo({ top: 0, behavior: "smooth" })}
          className="absolute left-1/2 top-2 z-10 -translate-x-1/2 rounded-full bg-accent px-3 py-1 text-xs font-bold text-white shadow"
        >
          ↑ {t("fan.newItems", { n: unseen })}
        </button>
      ) : null}
      <div
        ref={list}
        aria-live="polite"
        data-testid="commentary-feed"
        className="flex h-full flex-col gap-2 overflow-auto p-3"
      >
        {newest.map((item) => (
          <CommentaryCard key={item.id} item={item} scenario={scenario} ownTeam={ownTeam} />
        ))}
      </div>
    </div>
  );
}

export function PlayerTab({
  items,
  playerName,
}: {
  items: CommentaryView[];
  playerName: string | null;
}) {
  const { t } = useT();
  if (!playerName) return <EmptyState>{t("fan.noPlayer")}</EmptyState>;
  const tracker = [...items]
    .reverse()
    .find((item) => item.player_tracker && item.player_tracker.length);
  const mentions = [...items].reverse().filter((item) => item.emphasis === "focus_player");
  return (
    <div className="flex flex-col gap-3 p-3" data-testid="player-tab">
      <h2 className="text-lg font-bold">{playerName}</h2>
      <section className="rounded-lg border border-line bg-panel p-3">
        <h3 className="mb-2 text-sm font-semibold text-muted">{t("fan.tracker")}</h3>
        {tracker?.player_tracker?.length ? (
          <dl className="grid grid-cols-2 gap-2">
            {tracker.player_tracker.map((fact) => (
              <div key={fact.label} className="rounded bg-panel-2 p-2">
                <dt className="text-xs text-muted">{fact.label}</dt>
                <dd className="tabular text-lg font-bold">{fact.display}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <EmptyState>{t("fan.waitingStart")}</EmptyState>
        )}
      </section>
      {mentions.map((item) => (
        <div key={item.id} className="rounded border border-amber-500/40 p-2 text-sm">
          <span className="tabular mr-2 font-bold">{item.display_minute}</span>
          {item.text}
        </div>
      ))}
    </div>
  );
}

export function RecapPanel({ recaps }: { recaps: { H1?: RecapView; FULL?: RecapView } }) {
  const { t } = useT();
  const [scope, setScope] = useState<"H1" | "FULL">(recaps.FULL ? "FULL" : "H1");
  const recap = recaps[scope];
  return (
    <div className="flex flex-col gap-3 p-3" data-testid="recap-panel">
      <Segmented<"H1" | "FULL">
        label={t("fan.tabRecap")}
        value={scope}
        onChange={setScope}
        options={[
          { value: "H1", label: t("fan.recapH1") },
          { value: "FULL", label: t("fan.recapFull") },
        ]}
      />
      {!recap ? (
        <EmptyState>
          {scope === "H1" ? t("fan.recapNotReadyH1") : t("fan.recapNotReadyFull")}
        </EmptyState>
      ) : (
        <article className="item-enter flex flex-col gap-2 rounded-lg border border-line bg-panel p-4">
          <h2 className="text-lg font-bold">
            {recap.title}
            {recap.revision > 1 ? (
              <span className="ml-2 rounded bg-accent/20 px-1.5 py-0.5 text-xs font-semibold text-accent">
                {t("fan.recapUpdated")}
              </span>
            ) : null}
          </h2>
          {recap.paragraphs.map((paragraph) => (
            <p key={paragraph} className="leading-relaxed">
              {paragraph}
            </p>
          ))}
        </article>
      )}
    </div>
  );
}
