"use client";

import Link from "next/link";
import { useT } from "@/lib/i18n";
import { SyntheticBadge } from "./SyntheticBadge";
import { EmptyState } from "./ui";

/** A page opened without `?match=`: says so, links back and still carries the label (U5). */
export function NoMatch() {
  const { t } = useT();
  return (
    <main className="flex flex-col items-start gap-3 p-6">
      <SyntheticBadge />
      <EmptyState>
        {t("common.noMatch")}{" "}
        <Link className="text-accent underline" href="/">
          {t("common.back")}
        </Link>
      </EmptyState>
    </main>
  );
}
