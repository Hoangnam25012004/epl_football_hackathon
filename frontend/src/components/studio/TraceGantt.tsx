"use client";

// The agents at work (docs/day4-design.md section 5.9): one row per agent, one bar per span,
// colour by kind, a border for retries, fallbacks and rejections. Click a bar for its input and
// output. The visible proof of roles, handoffs and recovery (Best Multi-Agent System).
import { useMemo, useState } from "react";
import { EmptyState } from "@/components/common/ui";
import type { TraceSpan } from "@/lib/contracts";
import { useT } from "@/lib/i18n";

const KIND_COLOR: Record<TraceSpan["kind"], string> = {
  agent: "#64748b",
  tool: "#0ea5e9",
  llm: "#8b5cf6",
  guardrail: "#10b981",
  fallback: "#f59e0b",
};

const STATUS_RING: Record<TraceSpan["status"], string> = {
  ok: "",
  retry: "outline outline-2 outline-dashed outline-amber-400",
  fallback: "outline outline-2 outline-amber-500",
  rejected: "outline outline-2 outline-red-500",
  error: "outline outline-2 outline-red-700",
};

export function traceSummary(spans: readonly TraceSpan[]) {
  const end = spans.reduce((max, s) => Math.max(max, s.start_ms + s.duration_ms), 0);
  return {
    totalMs: end,
    llmCalls: spans.filter((s) => s.kind === "llm").length,
    rejected: spans.filter((s) => s.status === "rejected").length,
    fallback: spans.some((s) => s.kind === "fallback" || s.status === "fallback"),
  };
}

export function TraceGantt({ spans }: { spans: readonly TraceSpan[] }) {
  const { t } = useT();
  const [selected, setSelected] = useState<TraceSpan | null>(null);
  const summary = useMemo(() => traceSummary(spans), [spans]);
  const agents = useMemo(() => {
    const order: string[] = [];
    for (const span of spans) if (!order.includes(span.agent)) order.push(span.agent);
    return order;
  }, [spans]);
  if (spans.length === 0) return <EmptyState>{t("drawer.noTrace")}</EmptyState>;
  const total = Math.max(summary.totalMs, 1);

  return (
    <div className="flex flex-col gap-3 p-3" data-testid="trace-gantt">
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        <Stat label={t("drawer.total")} value={`${summary.totalMs.toFixed(0)} ms`} />
        <Stat label={t("drawer.llmCalls")} value={String(summary.llmCalls)} />
        <Stat label={t("drawer.rejected")} value={String(summary.rejected)} />
        <Stat
          label={t("drawer.fallback")}
          value={summary.fallback ? t("drawer.yes") : t("drawer.no")}
        />
      </dl>
      <div className="flex flex-col gap-1">
        {agents.map((agent) => (
          <div
            key={agent}
            className="grid grid-cols-[9rem_1fr] items-center gap-2"
            data-testid="trace-row"
          >
            <span className="truncate text-xs font-semibold" title={agent}>
              {agent}
            </span>
            <div className="relative h-6 rounded bg-panel-2">
              {spans
                .filter((span) => span.agent === agent)
                .map((span) => (
                  <button
                    key={span.span_id}
                    type="button"
                    data-testid="trace-span"
                    data-kind={span.kind}
                    data-status={span.status}
                    title={`${span.name} · ${span.kind} · ${span.status} · ${span.duration_ms.toFixed(1)} ms`}
                    onClick={() => setSelected(span)}
                    className={`absolute top-1 h-4 min-w-[3px] rounded-sm ${STATUS_RING[span.status]}`}
                    style={{
                      left: `${(span.start_ms / total) * 100}%`,
                      width: `${Math.max((span.duration_ms / total) * 100, 0.6)}%`,
                      backgroundColor: KIND_COLOR[span.kind],
                    }}
                  />
                ))}
            </div>
          </div>
        ))}
      </div>
      <ul className="flex flex-wrap gap-3 text-[11px] text-muted">
        {Object.entries(KIND_COLOR).map(([kind, color]) => (
          <li key={kind} className="flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: color }}
            />
            {kind}
          </li>
        ))}
      </ul>
      {selected ? (
        <section className="rounded border border-line p-2 text-xs" data-testid="trace-detail">
          <div className="mb-1 font-semibold">
            {selected.agent} · {selected.name} · {selected.status} · attempt {selected.attempt}
            {selected.model ? ` · ${selected.model}` : ""}
          </div>
          {selected.error ? <p className="mb-1 text-verdict-false">{selected.error}</p> : null}
          <div className="grid gap-2 md:grid-cols-2">
            <Json title={t("drawer.input")} value={selected.input} />
            <Json title={t("drawer.output")} value={selected.output} />
          </div>
        </section>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-line p-2">
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className="tabular text-base font-bold">{value}</dd>
    </div>
  );
}

function Json({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <div className="mb-0.5 font-semibold text-muted">{title}</div>
      <pre className="max-h-56 overflow-auto rounded bg-panel-2 p-2 text-[11px] leading-snug">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
