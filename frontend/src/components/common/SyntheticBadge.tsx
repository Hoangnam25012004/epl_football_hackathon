"use client";

import { useT } from "@/lib/i18n";

/** Required on every screen (Official Rules; docs/day4-design.md principle U5). */
export function SyntheticBadge({ compact = false }: { compact?: boolean }) {
  const { t } = useT();
  return (
    <span
      data-testid="synthetic-badge"
      title={t("common.syntheticNote")}
      className={`inline-flex items-center gap-1 rounded border border-amber-500/60 bg-amber-500/15 font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300 ${
        compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-xs"
      }`}
    >
      <span aria-hidden>◆</span>
      {t("common.synthetic")}
    </span>
  );
}

/** Static, bilingual: shown before the page code has loaded. */
export function SyntheticFallback() {
  return (
    <div className="flex min-h-dvh items-center justify-center p-6">
      <span
        data-testid="synthetic-badge"
        className="rounded border border-amber-500/60 bg-amber-500/15 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300"
      >
        ◆ Synthetic data · Dữ liệu tổng hợp
      </span>
    </div>
  );
}
