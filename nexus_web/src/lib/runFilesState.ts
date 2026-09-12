import { useEffect, useState } from "react";

export type FilesState = { search: string; kind: string; turn: string; selected: string; inputCursors: string[]; outputCursors: string[]; pdfPage: number };
const empty: FilesState = { search: "", kind: "all", turn: "all", selected: "", inputCursors: [""], outputCursors: [""], pdfPage: 1 };
const storage = "nexus.files-view.v1";
export function readFilesState(key: string): FilesState {
  try {
    const entry = JSON.parse(sessionStorage.getItem(storage) || "{}")[key];
    if (!entry || Date.now() - entry.at > 86400_000) return empty;
    const value = entry.value;
    return { ...empty, search: typeof value.search === "string" ? value.search.slice(0, 160) : "",
      kind: ["all", "input", "output"].includes(value.kind) ? value.kind : "all",
      turn: /^(all|unknown|[1-9][0-9]{0,8})$/.test(value.turn) ? value.turn : "all",
      selected: typeof value.selected === "string" ? value.selected.slice(0, 160) : "",
      pdfPage: Number.isSafeInteger(value.pdfPage) && value.pdfPage > 0 ? value.pdfPage : 1,
      inputCursors: validCursors(value.inputCursors), outputCursors: validCursors(value.outputCursors) };
  } catch { return empty; }
}
function validCursors(value: unknown): string[] {
  return Array.isArray(value) && value.length && value.length <= 100 && value.every(v => typeof v === "string" && v.length <= 2048) ? value : [""];
}
export function useFilesState(key: string) {
  const [state, setState] = useState(() => readFilesState(key));
  useEffect(() => {
    try {
      const rows = JSON.parse(sessionStorage.getItem(storage) || "{}");
      rows[key] = { at: Date.now(), value: state };
      sessionStorage.setItem(storage, JSON.stringify(Object.fromEntries(Object.entries(rows)
        .sort((a, b) => (b[1] as { at: number }).at - (a[1] as { at: number }).at).slice(0, 50))));
    } catch { /* Optional view preference; no credentials or file content. */ }
  }, [key, state]);
  return [state, setState] as const;
}
