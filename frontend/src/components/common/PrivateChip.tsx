"use client";

// A private session's mark in the Studio and Fan headers (docs/day6-design.md section 6.5):
// whoever has the link sees and controls it; nobody watching for 15 minutes removes it.
import { isPrivate } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Chip } from "./ui";

export function PrivateChip({ matchId }: { matchId: string | null }) {
  const { t } = useT();
  if (!matchId || !isPrivate(matchId)) return null;
  return (
    <span data-testid="private-chip">
      <Chip tone="accent" title={t("private.hint")}>
        🔒 {t("private.chip")}
      </Chip>
    </span>
  );
}
