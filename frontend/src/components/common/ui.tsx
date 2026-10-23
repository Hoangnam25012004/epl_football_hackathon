"use client";

// Small building blocks shared by every page.
import { useEffect, useId, useRef, type ReactNode } from "react";
import { textOn, safeColor } from "@/lib/color";
import { useT, type UiLang } from "@/lib/i18n";
import { ErrorBoundary } from "./ErrorBoundary";

export function Panel({
  title,
  actions,
  children,
  className = "",
  testId,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  const { t } = useT();
  const heading = useId();
  return (
    <section
      data-testid={testId}
      className={`flex min-h-0 flex-col rounded-lg border border-line bg-panel ${className}`}
    >
      <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <h2 id={heading} className="text-sm font-semibold uppercase tracking-wide text-muted">
          {title}
        </h2>
        {actions ? <div className="flex items-center gap-1">{actions}</div> : null}
      </header>
      {/* focusable, so a panel whose content overflows can be scrolled from the keyboard */}
      <div
        tabIndex={0}
        role="region"
        aria-labelledby={heading}
        className="min-h-0 flex-1 overflow-auto"
      >
        <ErrorBoundary
          name={typeof title === "string" ? title : testId}
          fallback={<p className="p-3 text-sm text-verdict-false">{t("common.panelError")}</p>}
        >
          {children}
        </ErrorBoundary>
      </div>
    </section>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="p-4 text-center text-sm text-muted">{children}</p>;
}

/** A panel whose data did not come (Day 6 states, docs/day6-design.md 8.4): say so, offer a
 * retry. A finished match sends no more snapshots, so nothing else would load it again. */
export function LoadFailed({ onRetry }: { onRetry: () => void }) {
  const { t } = useT();
  return (
    <div data-testid="load-failed" className="flex flex-col items-center gap-2 p-4 text-sm">
      <p className="text-muted">{t("common.loadFailed")}</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded border border-line px-3 py-1 hover:bg-panel-2"
      >
        {t("common.retry")}
      </button>
    </div>
  );
}

export function TeamChip({
  name,
  color,
  small = false,
}: {
  name: string;
  color: string | null | undefined;
  small?: boolean;
}) {
  const background = safeColor(color);
  return (
    <span
      className={`inline-flex items-center rounded font-bold ${small ? "px-1.5 text-xs" : "px-2 py-0.5 text-sm"}`}
      style={{ backgroundColor: background, color: textOn(background) }}
    >
      {name}
    </span>
  );
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
  disabled = false,
}: {
  value: T;
  options: readonly { value: T; label: ReactNode }[];
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-line">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={String(option.value)}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={`px-2.5 py-1 text-xs font-semibold first:rounded-l-md last:rounded-r-md disabled:opacity-50 ${
              active ? "bg-accent-fill text-white" : "text-ink hover:bg-panel-2"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function LangToggle() {
  const { lang, setLang, t } = useT();
  return (
    <Segmented<UiLang>
      label={t("common.uiLanguage")}
      value={lang}
      onChange={setLang}
      options={[
        { value: "en", label: "EN" },
        { value: "vi", label: "VI" },
      ]}
    />
  );
}

export function Tabs<T extends string>({
  value,
  tabs,
  onChange,
  label,
}: {
  value: T;
  tabs: readonly { value: T; label: ReactNode; testId?: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 border-b border-line px-2">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          data-testid={tab.testId}
          aria-selected={tab.value === value}
          onClick={() => onChange(tab.value)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
            tab.value === value
              ? "border-accent text-ink"
              : "border-transparent text-muted hover:text-ink"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/** A side sheet: keeps focus inside while open, closes on Escape (section 4.10). */
export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
}) {
  const { t } = useT();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && panel.current) {
        const focusable = panel.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        data-testid="drawer"
        onClick={(event) => event.stopPropagation()}
        className="flex h-full w-full max-w-3xl flex-col border-l border-line bg-panel shadow-2xl outline-none lg:w-[60vw]"
      >
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 text-sm text-muted hover:bg-panel-2"
          >
            {t("common.close")} ✕
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
      </div>
    </div>
  );
}

export function Chip({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "warn";
  title?: string;
}) {
  const tones = {
    neutral: "border-line text-muted",
    accent: "border-accent/50 text-accent",
    warn: "border-amber-500/50 text-amber-800 dark:text-amber-300",
  } as const;
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
