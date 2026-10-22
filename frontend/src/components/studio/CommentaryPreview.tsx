"use client";

// What viewers are hearing, in the studio's language (docs/day4-design.md section 5.7).
import { Chip, EmptyState } from "@/components/common/ui";
import type { CommentaryView } from "@/lib/contracts";
import { kindLabel, useT } from "@/lib/i18n";

export function CommentaryPreview({
  items,
  onDetails,
}: {
  items: CommentaryView[];
  onDetails: (itemId: string) => void;
}) {
  const { t, lang } = useT();
  const newest = [...items].reverse().slice(0, 60);
  if (newest.length === 0) return <EmptyState>{t("studio.noCommentary")}</EmptyState>;
  return (
    <ol className="flex flex-col" data-testid="commentary-preview">
      {newest.map((item) => (
        <li key={item.id} className="item-enter border-b border-line/60 px-3 py-2 text-sm">
          <div className="mb-0.5 flex items-center gap-1.5 text-xs text-muted">
            <span className="tabular font-bold text-ink">{item.display_minute}</span>
            <span>{kindLabel(lang, item.kind)}</span>
            <Chip>{item.source}</Chip>
            <button
              type="button"
              onClick={() => onDetails(item.id)}
              className="ml-auto underline hover:text-ink"
            >
              {t("studio.trace")}
            </button>
          </div>
          <p className={item.importance >= 3 ? "font-semibold" : ""}>{item.text}</p>
        </li>
      ))}
    </ol>
  );
}
