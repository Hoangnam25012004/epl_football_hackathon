"use client";

// The four lower-third templates (docs/day4-design.md section 6.2). Every number is the server's
// `display` string; the leading side is the server's `highlight` (principle U1). Text lengths are
// bounded by the server; the layout still truncates so nothing can leave the frame.
import type { GraphicStatView, LowerThirdView, TeamBadge } from "@/lib/contracts";
import { safeColor, textOn } from "@/lib/color";

export type Phase = "enter" | "exit" | "static";

function teamOfPlayer(item: LowerThirdView): TeamBadge {
  if (item.player && item.player.startsWith(`${item.away.id}-`)) return item.away;
  return item.home;
}

function Badge({ team }: { team: TeamBadge }) {
  const color = safeColor(team.primary_color);
  return (
    <span
      className="inline-flex h-12 min-w-24 items-center justify-center px-4 text-3xl font-extrabold tracking-wide"
      style={{ backgroundColor: color, color: textOn(color) }}
    >
      {team.short_name}
    </span>
  );
}

function SyntheticTag({ language }: { language: string }) {
  return (
    <span
      data-testid="lt-synthetic"
      className="rounded-sm bg-amber-400 px-2 py-0.5 text-sm font-bold uppercase tracking-wider text-black"
    >
      {language === "vi" ? "Dữ liệu tổng hợp" : "Synthetic data"}
    </span>
  );
}

function StatRow({ stat, item }: { stat: GraphicStatView; item: LowerThirdView }) {
  if (stat.value !== null && stat.value !== undefined) {
    return (
      <div className="flex items-baseline justify-between gap-6 px-6 py-2">
        <span className="truncate text-2xl text-slate-200">{stat.label}</span>
        <span className="tabular text-3xl font-bold text-white">{stat.value}</span>
      </div>
    );
  }
  const side = (team: TeamBadge, display: string | null | undefined, lead: boolean) => {
    const color = safeColor(team.primary_color);
    return (
      <span
        className={`tabular min-w-20 rounded-sm px-2 text-center text-3xl ${lead ? "font-extrabold" : "font-semibold opacity-80"}`}
        style={lead ? { backgroundColor: color, color: textOn(color) } : { color: "#fff" }}
      >
        {display ?? "—"}
      </span>
    );
  };
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-6 py-2">
      <div className="flex justify-start">
        {side(item.home, stat.home, stat.highlight === "home")}
      </div>
      <span className="truncate text-center text-2xl text-slate-200">{stat.label}</span>
      <div className="flex justify-end">
        {side(item.away, stat.away, stat.highlight === "away")}
      </div>
    </div>
  );
}

export function LowerThird({ item, phase = "static" }: { item: LowerThirdView; phase?: Phase }) {
  const animation = phase === "enter" ? "lt-enter" : phase === "exit" ? "lt-exit" : "";
  const isScore = item.template === "score_update";
  const score = isScore ? item.stats.find((s) => s.home != null && s.away != null) : undefined;
  const rest = score ? item.stats.filter((s) => s !== score) : item.stats;
  const accent = item.template === "player_card" ? teamOfPlayer(item) : null;
  return (
    <div
      data-testid="lower-third"
      data-template={item.template}
      data-id={item.id}
      className={`${animation} w-[1040px] overflow-hidden rounded-md bg-slate-950/90 text-white shadow-2xl ring-1 ring-white/10`}
    >
      <div className="flex h-2">
        <span className="flex-1" style={{ backgroundColor: safeColor(item.home.primary_color) }} />
        <span className="flex-1" style={{ backgroundColor: safeColor(item.away.primary_color) }} />
      </div>
      <div className="flex items-center justify-between gap-4 px-6 pb-2 pt-4">
        <div className="min-w-0">
          <div className="truncate text-[38px] font-extrabold uppercase leading-tight tracking-wide">
            {item.title}
          </div>
          <div className="truncate text-2xl text-slate-300">{item.subtitle}</div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <span className="tabular rounded-sm bg-white/10 px-2 py-0.5 text-2xl font-bold">
            {item.match_clock}
          </span>
          <SyntheticTag language={item.language} />
        </div>
      </div>
      {score ? (
        <div className="flex items-center justify-center gap-4 px-6 py-3">
          <Badge team={item.home} />
          <span className="tabular text-6xl font-extrabold">
            {score.home}–{score.away}
          </span>
          <Badge team={item.away} />
        </div>
      ) : null}
      {accent ? (
        <div className="px-6 pb-1">
          <span
            className="inline-block h-1.5 w-24 rounded-full"
            style={{ backgroundColor: safeColor(accent.primary_color) }}
          />
        </div>
      ) : null}
      <div className="pb-3">
        {rest.map((stat) => (
          <StatRow key={stat.label} stat={stat} item={item} />
        ))}
      </div>
    </div>
  );
}
