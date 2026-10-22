"use client";

// The editor's queue of talking points: approve / pin / dismiss (docs/day4-design.md section 5.5).
// A click shows "sending" until the server's `talking_point_status` arrives; on a REST error the
// card goes back to its old state. The last decision of the server wins in every tab.
import { useMemo, useState } from "react";
import { useToast } from "@/components/common/Toast";
import { Chip, EmptyState, Segmented, Tabs } from "@/components/common/ui";
import { api } from "@/lib/api";
import type { TalkingPoint } from "@/lib/contracts";
import { kindLabel, useT, type Key } from "@/lib/i18n";
import type { StoreApi } from "zustand/vanilla";
import type { MatchStore, TalkingPointStatus } from "@/store/match-store";
import { FactChips } from "./ClaimBox";

type Tab = "new" | "pinned" | "approved" | "dismissed";
type Show = "en" | "vi" | "both";

export function TalkingPointQueue({
  matchId,
  points,
  pending,
  store,
  onTrace,
}: {
  matchId: string;
  points: TalkingPoint[];
  pending: Record<string, TalkingPointStatus>;
  store: StoreApi<MatchStore>;
  onTrace: (itemId: string) => void;
}) {
  const { t, lang } = useT();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("new");
  const [show, setShow] = useState<Show>(lang === "vi" ? "vi" : "en");

  const statusOf = (point: TalkingPoint): TalkingPointStatus =>
    pending[point.id] ?? point.status ?? "new";

  const groups = useMemo(() => {
    const newest = [...points].reverse();
    const of = (status: TalkingPointStatus) => newest.filter((p) => statusOf(p) === status);
    return {
      new: [...of("pinned"), ...of("new")], // pinned stay on top of the queue
      pinned: of("pinned"),
      approved: of("approved"),
      dismissed: of("dismissed"),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, pending]);

  const decide = async (point: TalkingPoint, status: TalkingPointStatus) => {
    store.getState().setPending(point.id, status);
    const result = await api.talkingPointStatus(matchId, point.id, status);
    if (!result.ok) {
      store.getState().setPending(point.id, null);
      toast.show(`${t(`err.${result.error.code}`)} ${result.error.message}`, "error");
    }
  };

  const shown = groups[tab];
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 pr-2">
        <Tabs<Tab>
          label={t("studio.talkingPoints")}
          value={tab}
          onChange={setTab}
          tabs={(["new", "pinned", "approved", "dismissed"] as const).map((value) => ({
            value,
            testId: `tp-tab-${value}`,
            label: `${t(`studio.tp${value[0]!.toUpperCase()}${value.slice(1)}` as Key)} (${groups[value].length})`,
          }))}
        />
        <Segmented<Show>
          label={t("studio.tpShow")}
          value={show}
          onChange={setShow}
          options={[
            { value: "en", label: "EN" },
            { value: "vi", label: "VI" },
            { value: "both", label: t("studio.tpBoth") },
          ]}
        />
      </div>
      <ul className="flex-1 overflow-auto p-2" data-testid="talking-points">
        {shown.length === 0 ? <EmptyState>{t("studio.tpEmpty")}</EmptyState> : null}
        {shown.map((point) => {
          const status = statusOf(point);
          const sending = point.id in pending;
          return (
            <li
              key={point.id}
              data-testid="talking-point"
              data-id={point.id}
              data-status={status}
              className="item-enter mb-2 rounded-md border border-line bg-panel-2/60 p-2"
            >
              <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs">
                <span className="tabular font-bold">{point.display_minute}</span>
                <Chip>{kindLabel(lang, point.trigger_kind)}</Chip>
                <span aria-label={`importance ${point.importance}`}>
                  {"●".repeat(point.importance)}
                </span>
                {status === "pinned" ? <Chip tone="accent">📌 {t("studio.tpPinned")}</Chip> : null}
                {sending ? <Chip tone="warn">{t("common.sending")}</Chip> : null}
              </div>
              {(show === "en" || show === "both") && (
                <Lines lines={point.en} source={point.source_en} lang="EN" />
              )}
              {(show === "vi" || show === "both") && (
                <Lines lines={point.vi} source={point.source_vi} lang="VI" />
              )}
              <div className="mt-1">
                <FactChips facts={point.facts} max={4} />
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {status !== "approved" ? (
                  <Action onClick={() => void decide(point, "approved")} testId="tp-approve">
                    ✓ {t("studio.tpApprove")}
                  </Action>
                ) : null}
                {status !== "pinned" ? (
                  <Action onClick={() => void decide(point, "pinned")} testId="tp-pin">
                    📌 {t("studio.tpPin")}
                  </Action>
                ) : null}
                {status !== "dismissed" ? (
                  <Action onClick={() => void decide(point, "dismissed")} testId="tp-dismiss">
                    ✕ {t("studio.tpDismiss")}
                  </Action>
                ) : null}
                {status !== "new" ? (
                  <Action onClick={() => void decide(point, "new")} testId="tp-restore">
                    ↺ {t("studio.tpRestore")}
                  </Action>
                ) : null}
                <Action onClick={() => onTrace(point.id)} testId="tp-trace">
                  {t("studio.trace")}
                </Action>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Lines({
  lines,
  source,
  lang,
}: {
  lines: TalkingPoint["en"];
  source: string;
  lang: string;
}) {
  return (
    <div className="mb-1 flex gap-2">
      <span className="mt-0.5 shrink-0 text-[10px] font-bold text-muted">
        {lang}
        <br />
        <span className="font-normal">{source}</span>
      </span>
      <ul className="flex flex-col gap-0.5 text-sm">
        {lines.map((line) => (
          <li key={line.text}>• {line.text}</li>
        ))}
      </ul>
    </div>
  );
}

function Action({
  onClick,
  children,
  testId,
}: {
  onClick: () => void;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="rounded border border-line px-2 py-0.5 text-xs font-semibold hover:bg-panel"
    >
      {children}
    </button>
  );
}
