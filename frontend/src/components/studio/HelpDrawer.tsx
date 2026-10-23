"use client";

// Studio help (docs/day6-design.md section 8.5, P1): what each panel shows, how to read a
// verdict, the keyboard shortcuts. "?" opens it, unless the user is typing.
import { useEffect, useState } from "react";
import { Drawer } from "@/components/common/ui";
import { VerdictPill } from "@/components/studio/ClaimBox";
import { useT } from "@/lib/i18n";
import { VERDICTS } from "@/lib/verdict";

const PANELS = [
  ["studio.events", "help.panelEvents"],
  ["studio.claim", "help.panelClaim"],
  ["studio.talkingPoints", "help.panelTalkingPoints"],
  ["studio.commentary", "help.panelCommentary"],
  ["studio.lowerThirds", "help.panelLowerThirds"],
  ["studio.momentum", "help.panelMomentum"],
  ["studio.stats", "help.panelStats"],
] as const;
const KEYS = [
  ["Space", "help.keySpace"],
  ["Enter", "help.keyEnter"],
  ["Esc", "help.keyEsc"],
  ["?", "help.keyHelp"],
] as const;

export function HelpButton() {
  const { t } = useT();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing =
        event.target instanceof Element &&
        event.target.closest("input, textarea, select, [contenteditable]");
      if (event.key !== "?" || typing) return;
      event.preventDefault();
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <button
        type="button"
        data-testid="help-open"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        className="rounded border border-line px-2 py-0.5 hover:bg-panel-2"
      >
        ? {t("help.open")}
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} title={t("help.title")}>
        <div data-testid="help" className="flex flex-col gap-6 p-4 text-sm">
          <section className="flex flex-col gap-2">
            <h3 className="font-semibold">{t("help.panels")}</h3>
            <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-[max-content_1fr]">
              {PANELS.map(([panel, text]) => (
                <div key={panel} className="contents">
                  <dt className="font-medium">{t(panel)}</dt>
                  <dd className="text-muted">{t(text)}</dd>
                </div>
              ))}
            </dl>
          </section>
          <section className="flex flex-col gap-2">
            <h3 className="font-semibold">{t("help.verdicts")}</h3>
            <ul className="flex flex-col gap-2">
              {VERDICTS.map((verdict) => (
                <li key={verdict} className="flex flex-wrap items-center gap-2">
                  <VerdictPill verdict={verdict} />
                  <span className="text-muted">{t(`help.verdict${verdict}`)}</span>
                </li>
              ))}
            </ul>
            <p>{t("help.trust")}</p>
          </section>
          <section className="flex flex-col gap-2">
            <h3 className="font-semibold">{t("help.shortcuts")}</h3>
            <ul className="flex flex-col gap-1">
              {KEYS.map(([key, text]) => (
                <li key={key} className="flex items-center gap-3">
                  <kbd className="min-w-14 rounded border border-line bg-bg px-2 py-0.5 text-center font-mono text-xs">
                    {key}
                  </kbd>
                  <span className="text-muted">{t(text)}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </Drawer>
    </>
  );
}
