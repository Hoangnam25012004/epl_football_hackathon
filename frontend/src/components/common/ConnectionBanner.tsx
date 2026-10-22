"use client";

import { useEffect, useState } from "react";
import { useT } from "@/lib/i18n";
import type { SocketState } from "@/lib/socket";

/** Seconds until the next attempt: exact at first (`retryInMs`), then ticking towards `retryAt`. */
function useCountdown(retryAt: number | undefined, retryInMs: number | undefined): number {
  const [tick, setTick] = useState<{ at: number; target?: number }>({ at: 0 });
  useEffect(() => {
    if (retryAt === undefined) return;
    const timer = setInterval(() => setTick({ at: Date.now(), target: retryAt }), 250);
    return () => clearInterval(timer);
  }, [retryAt]);
  if (retryAt === undefined) return 0;
  const left = tick.target === retryAt ? retryAt - tick.at : (retryInMs ?? 0);
  return Math.max(0, Math.ceil(left / 1000));
}

/** What the connection is doing, in words; nothing when it is live. */
export function ConnectionBanner({
  state,
  onCreate,
}: {
  state: SocketState;
  onCreate?: () => void;
}) {
  const { t } = useT();
  const secondsLeft = useCountdown(state.retryAt, state.retryInMs);

  if (state.status === "open" || state.status === "idle") return null;
  let text: string;
  let tone = "border-amber-500/50 bg-amber-500/10 text-amber-800 dark:text-amber-200";
  switch (state.status) {
    case "connecting":
      text = t("conn.connecting");
      break;
    case "reconnecting":
      text = state.code === 4429 ? t("conn.tooMany") : t("conn.reconnecting", { s: secondsLeft });
      break;
    case "waiting":
      text = t("conn.waiting");
      break;
    case "fatal":
      text = state.code === 4422 ? t("conn.fatal4422") : t("conn.fatal404");
      tone = "border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-200";
      break;
    default:
      text = t("conn.closed");
  }
  return (
    <div
      role="status"
      data-testid="connection-banner"
      data-status={state.status}
      className={`flex items-center justify-between gap-3 border px-3 py-2 text-sm ${tone}`}
    >
      <span>{text}</span>
      {state.status === "fatal" && state.code === 4404 && onCreate ? (
        <button
          type="button"
          onClick={onCreate}
          className="rounded bg-accent-fill px-3 py-1 text-sm font-semibold text-white"
        >
          {t("conn.createMatch")}
        </button>
      ) : null}
    </div>
  );
}
