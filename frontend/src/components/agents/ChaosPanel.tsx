"use client";

// Chaos (docs/day7-design.md section 2.6): break the model of your own match, one fault at a
// time, and watch the lanes recover. A shared match is locked: start a demo match of your own.
import { useState } from "react";
import { useToast } from "@/components/common/Toast";
import { Chip } from "@/components/common/ui";
import { api } from "@/lib/api";
import type { ChaosCommand, ChaosStatus } from "@/lib/contracts";
import { useT, type Key } from "@/lib/i18n";

export const FAULTS = [
  "model_down",
  "model_slow",
  "model_garbage",
  "rate_limited",
  "content_filter",
  "model_lies",
] as const satisfies readonly NonNullable<ChaosCommand["fault"]>[];

export function ChaosPanel({
  matchId,
  chaos,
  studioTemplates,
  onChanged,
  onStartDemo,
}: {
  matchId: string;
  chaos: ChaosStatus | null;
  studioTemplates: boolean;
  onChanged: () => void;
  onStartDemo: () => void;
}) {
  const { t } = useT();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (chaos && !chaos.available)
    return (
      <div className="flex flex-col gap-2 p-3 text-sm" data-testid="chaos-locked">
        <p>{t("chaos.locked")}</p>
        <button
          type="button"
          onClick={onStartDemo}
          className="self-start rounded bg-accent-fill px-3 py-1.5 font-bold text-white"
        >
          ▶ {t("chaos.start")}
        </button>
      </div>
    );

  const send = async (fault: ChaosCommand["fault"]) => {
    setBusy(true);
    const result = await api.setChaos(matchId, { fault, duration_s: 300 });
    setBusy(false);
    if (!result.ok) toast.show(t("chaos.failed", { message: result.error.message }), "error");
    onChanged();
  };

  const active = chaos?.fault ?? null;
  return (
    <div className="flex flex-col gap-3 p-3 text-sm" data-testid="chaos-panel">
      <p className="text-muted">{t("chaos.intro")}</p>
      <p role="status" data-testid="chaos-state" className="font-semibold">
        {active
          ? t("chaos.active", {
              fault: t(`chaos.fault.${active}` as Key),
              s: Math.ceil(chaos?.seconds_left ?? 0),
            })
          : t("chaos.none")}
      </p>
      <ul className="flex flex-col gap-2">
        {FAULTS.map((fault) => (
          <li key={fault} className="flex flex-col gap-0.5">
            <button
              type="button"
              data-testid={`chaos-${fault}`}
              aria-pressed={active === fault}
              disabled={busy}
              onClick={() => void send(fault)}
              className={`self-start rounded border px-2 py-1 font-semibold disabled:opacity-50 ${active === fault ? "border-accent bg-panel-2 text-accent" : "border-line hover:bg-panel-2"}`}
            >
              {t(`chaos.fault.${fault}` as Key)}
            </button>
            <span className="text-xs text-muted">{t(`chaos.expect.${fault}` as Key)}</span>
          </li>
        ))}
      </ul>
      <button
        type="button"
        data-testid="chaos-reset"
        disabled={busy}
        onClick={() => void send(null)}
        className="self-start rounded border border-line px-3 py-1 font-semibold hover:bg-panel-2 disabled:opacity-50"
      >
        {t("chaos.reset")}
      </button>
      <p data-testid="chaos-lies">
        {t("chaos.lies", { n: chaos?.lies_published ?? 0, told: chaos?.lies_told ?? 0 })}
      </p>
      <p className="text-xs text-muted">{t("chaos.calls", { n: chaos?.calls ?? 0 })}</p>
      <div className="flex flex-wrap gap-1">
        {chaos?.claim_breaker ? (
          <Chip tone={chaos.claim_breaker === "closed" ? "neutral" : "warn"}>
            claim · {t(`agents.breaker.${chaos.claim_breaker}` as Key)}
          </Chip>
        ) : null}
        {chaos?.studio_breaker ? (
          <Chip tone={chaos.studio_breaker === "closed" ? "neutral" : "warn"}>
            studio · {t(`agents.breaker.${chaos.studio_breaker}` as Key)}
          </Chip>
        ) : null}
      </div>
      {studioTemplates ? <p className="text-xs text-muted">{t("chaos.studioNote")}</p> : null}
      <p className="text-xs">{t("chaos.tryClaim")}</p>
    </div>
  );
}
