import { t, useLocale } from "../localization";
export function CursorPageControls({ page, hasMore, busy, onPrevious, onNext }: {
  page: number; hasMore: boolean; busy: boolean; onPrevious: () => void; onNext: () => void;
}) {
  useLocale();
  return <nav aria-label={t("Catalog pages")} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
    <span className="text-muted" aria-live="polite">{t("Page")}{" "}{page}</span>
    <div className="flex gap-2">
      <button className="btn min-h-11" disabled={busy || page === 1} onClick={onPrevious}>{t("Previous page")}</button>
      <button className="btn min-h-11" disabled={busy || !hasMore} onClick={onNext}>{t("Next page")}</button>
    </div>
  </nav>;
}
