"use client";

// Play / pause / speed / restart from a minute (docs/day4-design.md section 5.2). Buttons wait for
// the server; the displayed state only changes with the `status` message (principle U2).
import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/common/Toast";
import { Segmented } from "@/components/common/ui";
import { api, parseMatchId, type Result } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { SessionStatus } from "@/lib/ws-types";

const SPEEDS = [1, 5, 20] as const;
const RESTART_MINUTES = [0, 45, 60, 75] as const;

export function ReplayControls({
  matchId,
  status,
  speed,
}: {
  matchId: string;
  status: SessionStatus | null;
  speed: number | null;
}) {
  const { t } = useT();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (action: () => Promise<Result<unknown>>) => {
      setBusy(true);
      const result = await action();
      setBusy(false);
      if (!result.ok)
        toast.show(t(`err.${result.error.code}`) + ` ${result.error.message}`, "error");
    },
    [t, toast],
  );

  const running = status === "running";
  const canPlay = status === "created" || status === "paused" || status === "stopped";
  const toggle = useCallback(() => {
    if (running) void run(() => api.pause(matchId));
    else if (status === "created") void run(() => api.start(matchId));
    else if (canPlay) void run(() => api.resume(matchId));
  }, [running, status, canPlay, run, matchId]);

  // Space: play / pause, unless the user is typing
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        event.code !== "Space" ||
        target?.closest("input, textarea, select, button, [contenteditable]")
      )
        return;
      event.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  const restart = (minute: number) => {
    const parsed = parseMatchId(matchId);
    if (!parsed) return;
    if (!window.confirm(t("studio.restartConfirm", { minute }))) return;
    void run(() =>
      api.createMatch({
        scenario: parsed.scenario,
        seed: parsed.seed,
        speed: speed ?? 20,
        from_minute: minute,
        restart: true,
        autostart: true,
      }),
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="replay-controls">
      <button
        type="button"
        data-testid="play-pause"
        disabled={busy || !(running || canPlay)}
        onClick={toggle}
        className="min-w-24 rounded bg-accent px-3 py-1.5 text-sm font-bold text-white disabled:opacity-50"
      >
        {running ? `⏸ ${t("studio.pause")}` : `▶ ${t("studio.play")}`}
      </button>
      <Segmented<number>
        label={t("studio.speed")}
        value={speed ?? 20}
        disabled={busy}
        onChange={(value) => void run(() => api.speed(matchId, value))}
        options={SPEEDS.map((value) => ({ value, label: `${value}x` }))}
      />
      <label className="flex items-center gap-1 text-xs text-muted">
        ↺ {t("studio.restartFrom")}
        <select
          data-testid="restart-from"
          disabled={busy}
          value=""
          onChange={(event) => {
            if (event.target.value !== "") restart(Number(event.target.value));
          }}
          className="rounded border border-line bg-panel px-1 py-1 text-xs text-ink"
        >
          <option value="">—</option>
          {RESTART_MINUTES.map((minute) => (
            <option key={minute} value={minute}>
              {minute}&apos;
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
