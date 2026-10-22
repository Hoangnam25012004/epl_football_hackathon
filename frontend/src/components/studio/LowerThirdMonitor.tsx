"use client";

// What is on air and the editor's controls (docs/day4-design.md section 5.6). The monitor runs
// the same scheduler as the overlay page, so it shows what OBS shows.
import { useState } from "react";
import { useStore } from "zustand";
import { useToast } from "@/components/common/Toast";
import { Chip, EmptyState, Segmented } from "@/components/common/ui";
import { Stage } from "@/components/overlay/Stage";
import { api } from "@/lib/api";
import type { OnAirCommand } from "@/lib/contracts";
import { useT } from "@/lib/i18n";
import { items } from "@/store/match-store";
import { useMatchConnection, type MatchConnection } from "@/hooks/useMatchConnection";
import { useOverlaySchedule } from "@/hooks/useOverlaySchedule";

export function LowerThirdMonitor({
  matchId,
  main,
  uiLang,
}: {
  matchId: string;
  main: MatchConnection;
  uiLang: "en" | "vi";
}) {
  const { t } = useT();
  const toast = useToast();
  const [preview, setPreview] = useState<"en" | "vi">(uiLang);
  // a second connection only when the preview language differs from the studio's
  const other = useMatchConnection(
    preview !== uiLang ? matchId : null,
    `mode=analyst&lang=${preview}`,
    {
      name: "studio-preview",
    },
  );
  const source = preview === uiLang ? main : other;
  const schedule = useOverlaySchedule(source, matchId, preview);
  const lowerThirds = useStore(source.store, (state) => state.lowerThirds);
  const superseded = useStore(source.store, (state) => state.superseded);
  const onAir = schedule.onAir;
  const recent = items(lowerThirds).reverse().slice(0, 10);

  const command = async (body: OnAirCommand) => {
    const result = await api.commandOnAir(matchId, body);
    if (!result.ok) toast.show(`${t(`err.${result.error.code}`)} ${result.error.message}`, "error");
  };

  const mode = onAir?.hold ? "HOLD" : onAir?.mode === "manual" ? "MANUAL" : "AUTO";
  return (
    <div className="flex flex-col gap-2 p-2" data-testid="lower-third-monitor">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip tone={mode === "AUTO" ? "neutral" : "warn"}>
          <span data-testid="on-air-mode">
            {t(
              mode === "HOLD"
                ? "studio.modeHold"
                : mode === "MANUAL"
                  ? "studio.modeManual"
                  : "studio.modeAuto",
            )}
          </span>
        </Chip>
        <Segmented<"en" | "vi">
          label={t("studio.preview")}
          value={preview}
          onChange={setPreview}
          options={[
            { value: "en", label: "EN" },
            { value: "vi", label: "VI" },
          ]}
        />
        <a
          className="ml-auto text-xs text-accent underline"
          href={`/overlay/?match=${encodeURIComponent(matchId)}&lang=${preview}`}
          target="_blank"
          rel="noreferrer"
        >
          {t("studio.openOverlay")} ↗
        </a>
      </div>
      <div
        className="relative aspect-video w-full overflow-hidden rounded border border-line"
        style={{
          background: "repeating-conic-gradient(#8882 0% 25%, transparent 0% 50%) 50% / 24px 24px",
        }}
      >
        <Stage current={schedule.current?.item ?? null} />
        {!schedule.current ? (
          <span className="absolute inset-0 flex items-center justify-center text-xs text-muted">
            {t("studio.nothingOnAir")}
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-1">
        <ControlButton testId="on-air-clear" onClick={() => void command({ action: "clear" })}>
          {t("studio.clear")}
        </ControlButton>
        {onAir?.hold ? (
          <ControlButton
            testId="on-air-release"
            onClick={() => void command({ action: "release" })}
          >
            {t("studio.release")}
          </ControlButton>
        ) : (
          <ControlButton testId="on-air-hold" onClick={() => void command({ action: "hold" })}>
            {t("studio.hold")}
          </ControlButton>
        )}
        <ControlButton testId="on-air-auto" onClick={() => void command({ action: "auto" })}>
          {t("studio.auto")}
        </ControlButton>
      </div>
      <h3 className="text-xs font-semibold uppercase text-muted">{t("studio.queue")}</h3>
      {recent.length === 0 ? <EmptyState>{t("studio.nothingOnAir")}</EmptyState> : null}
      <ul className="flex flex-col gap-1" data-testid="lower-third-queue">
        {recent.map((item) => (
          <li
            key={item.id}
            data-testid="lower-third-item"
            data-id={item.id}
            className={`flex items-center gap-2 rounded border border-line px-2 py-1 text-xs ${
              superseded.includes(item.id) ? "opacity-40 line-through" : ""
            } ${schedule.current?.item.id === item.id ? "border-red-500" : ""}`}
          >
            <span className="tabular font-bold">{item.match_clock}</span>
            <span className="min-w-0 flex-1 truncate">
              {item.title} · {item.subtitle}
            </span>
            <button
              type="button"
              data-testid="on-air-take"
              disabled={superseded.includes(item.id)}
              onClick={() => void command({ action: "take", item_id: item.id })}
              className="rounded bg-red-600 px-2 py-0.5 font-bold text-white disabled:opacity-40"
            >
              {t("studio.take")}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ControlButton({
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
      className="rounded border border-line px-2.5 py-1 text-xs font-semibold hover:bg-panel-2"
    >
      {children}
    </button>
  );
}
