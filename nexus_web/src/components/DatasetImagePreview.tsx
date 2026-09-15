import { t, useLocale } from "../localization";
import { useContext, useEffect, useState } from "react";
import { ApplicationDistributionContext } from "../app/distribution";
import { Image as ImageIcon, Loader2 } from "lucide-react";
import { api, type ApiContext } from "../lib/api";
import { NexilumeDialog } from "./NexilumeControls";

export function DatasetImagePreview({
  context,
  file,
}: {
  context: ApiContext;
  file: { file_name: string; content_type?: string; download_url: string };
}) {
  useLocale();
  const loader = useContext(ApplicationDistributionContext)?.datasetImagePreview ?? api.previewDatasetImage;
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [preview, setPreview] = useState<{
    key: string;
    url: string;
    error: string;
  } | null>(null);
  const key = `${context.tenantId}|${context.projectId}|${context.token}|${file.download_url}|${attempt}`;
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    let url = "";
    setPreview(null);
    void loader(context, file.download_url)
      .then((blob) => {
        if (disposed) return;
        url = URL.createObjectURL(blob);
        setPreview({ key, url, error: "" });
      })
      .catch((reason) => {
        if (!disposed)
          setPreview({
            key,
            url: "",
            error:
              reason instanceof Error
                ? reason.message
                : "Image preview unavailable.",
          });
      });
    return () => {
      disposed = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [open, key, loader]);
  if (
    !["image/png", "image/jpeg", "image/webp"].includes(file.content_type || "")
  )
    return null;
  const current = preview?.key === key ? preview : null;
  return (
    <>
      <button
        type="button"
        className="btn min-h-11"
        aria-label={t("Preview {{0}}", { 0: file.file_name })}
        title={t("Preview {{0}}", { 0: file.file_name })}
        onClick={() => setOpen(true)}
      >
        <ImageIcon size={16} />
      </button>
      <NexilumeDialog
        open={open}
        onClose={() => setOpen(false)}
        title={t("Image preview")}
        description={file.file_name}
        size="large"
      >
        {!current ? (
          <p role="status" className="flex items-center gap-2">
            <Loader2 size={16} className="animate-spin" />{t("Loading protected preview…")}</p>
        ) : current.error ? (
          <div>
            <p role="alert" className="break-words text-sm text-red-700">
              {current.error}
            </p>
            <button
              className="btn mt-3 min-h-11"
              onClick={() => setAttempt((value) => value + 1)}
            >{t("Retry preview")}</button>
          </div>
        ) : (
          <img
            src={current.url}
            alt={file.file_name}
            className="max-h-[65vh] w-full object-contain"
            onError={() =>
              setPreview({
                key,
                url: "",
                error: "Image preview could not be displayed.",
              })
            }
          />
        )}
        <p className="mt-4 text-xs text-muted">{t("Protected preview · Metadata removed · Download retains the original file.")}</p>
      </NexilumeDialog>
    </>
  );
}
