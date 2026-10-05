import { forwardRef, useId, useRef } from "react";
import { Upload } from "lucide-react";
import { t, useLocale } from "../localization";

type FilePickerProps = {
  label: string;
  hint?: string;
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  files: readonly File[];
  onFilesChange: (files: File[]) => void;
  className?: string;
};

/** Keep browser-localized native file labels out of the application's UI. */
export const FilePicker = forwardRef<HTMLButtonElement, FilePickerProps>(function FilePicker(
  { label, hint, accept, multiple = false, disabled = false, files, onFilesChange, className = "" },
  ref,
) {
  useLocale();
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const selection = files.length === 0
    ? (multiple ? t("No files selected") : t("No file selected"))
    : files.length === 1 ? files[0].name : t("{{count}} files selected", { count: files.length });

  return <div className={`grid min-w-0 gap-2 ${className}`}>
    <span id={`${id}-label`} className="text-sm font-medium text-ink">{label}</span>
    {hint && <span id={`${id}-hint`} className="text-xs text-muted">{hint}</span>}
    <input
      ref={input}
      type="file"
      hidden
      className="hidden"
      aria-label={label}
      accept={accept}
      multiple={multiple}
      disabled={disabled}
      onChange={(event) => {
        const selected = Array.from(event.currentTarget.files ?? []);
        // The parent owns the selection. Re-selecting the same file must still
        // trigger validation/retry; dismissing the picker keeps existing files.
        event.currentTarget.value = "";
        if (selected.length) onFilesChange(selected);
      }}
    />
    <div className="flex min-w-0 flex-wrap items-center gap-3">
      <button
        ref={ref}
        type="button"
        className="btn btn-secondary min-h-11 shrink-0"
        disabled={disabled}
        aria-describedby={`${id}-label ${id}-selection${hint ? ` ${id}-hint` : ""}`}
        onClick={() => input.current?.click()}
      >
        <Upload size={16} aria-hidden="true" />
        <span>{multiple ? t("Choose files") : t("Choose file")}</span>
      </button>
      <span id={`${id}-selection`} role="status" className="min-w-0 flex-1 break-all text-sm text-muted" title={files.map(file => file.name).join(", ")}>
        {selection}
      </span>
    </div>
  </div>;
});
