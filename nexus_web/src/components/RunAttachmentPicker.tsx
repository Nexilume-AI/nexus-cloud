import { t } from "../localization";
import { forwardRef, useImperativeHandle, useRef, useState, type ComponentProps } from "react";
import { RunImageAttachments, type AttachmentIntake } from "./RunImageAttachments";
import { RunFileAttachments } from "./RunFileAttachments";

/** One input surface, retaining the two existing private transfer protocols. */
export type RunAttachmentIntake = AttachmentIntake & { choose: () => void };
export const RunAttachmentPicker = forwardRef<RunAttachmentIntake, {
  disabled: boolean;
  images?: ComponentProps<typeof RunImageAttachments>;
  files?: ComponentProps<typeof RunFileAttachments>;
}>(function RunAttachmentPicker({ disabled, images, files }, ref) {
  const imageIntake = useRef<AttachmentIntake>(null);
  const fileIntake = useRef<AttachmentIntake>(null);
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  function add(selected: File[]) {
    if (!selected.length) return;
    if (disabled) { setError(t("Attachments cannot be added to the current turn. Keep the files and add them when the Agent is ready for your next message.")); return; }
    const imageFiles: File[] = [], otherFiles: File[] = [], rejected: string[] = [];
    for (const file of selected) {
      // Never silently turn an unsupported image into a generic file input.
      if (file.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|svg|heic|avif|bmp|tiff?)$/i.test(file.name)) {
        if (images) imageFiles.push(file);
        else rejected.push(`${file.name}: this Agent does not accept image inputs.`);
      } else if (files) otherFiles.push(file);
      else rejected.push(`${file.name}: this Agent does not accept file inputs.`);
    }
    setError(rejected.join("\n"));
    if (imageFiles.length) imageIntake.current?.add(imageFiles);
    if (otherFiles.length) fileIntake.current?.add(otherFiles);
  }
  useImperativeHandle(ref, () => ({ add, choose: () => { if (!disabled) input.current?.click(); } }));
  return <section aria-label={t("Message attachments")} className="min-w-0">
    {images || files ? <>
      <input ref={input} type="file" multiple aria-label={t("Choose attachments")} className="sr-only" disabled={disabled} accept={files ? undefined : "image/png,image/jpeg,image/webp"} onChange={event => { add(Array.from(event.target.files || [])); event.target.value = ""; }} />
      {images ? <RunImageAttachments {...images} ref={imageIntake} /> : null}
      {files ? <RunFileAttachments {...files} ref={fileIntake} /> : null}
    </> : null}
    {error ? <div role="alert" className="whitespace-pre-wrap text-sm text-red-700">{error}<button type="button" className="block min-h-11 underline" onClick={() => setError("")}>{t("Dismiss attachment notice")}</button></div> : null}
  </section>;
});
