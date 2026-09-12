import { useMutation, useQuery } from "@tanstack/react-query";
import { AtSign, FileAudio, FileText, Gauge, Loader2, Mic, MicOff, Monitor, Paperclip, Plus, Slash, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { api, type AgentFileTransfer } from "../lib/api";
import type { AgentInteractionTool, PrivateAgentRunDisplay } from "../lib/types";

export type ExecutionChoice = { profileId: string; reasoningEffort: string };

export const PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS = ["new", "cancel", "model", "help"] as const;
const RESERVED_COMMANDS = new Set<string>(PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS);
const BUILT_INS = [
  ["new", "Start a new Run"], ["cancel", "Cancel the active Run"],
  ["model", "Choose execution profile"], ["help", "Show available commands"],
] as const;

export function PrivateDisplayComposerTools({
  agentId, value, onValueChange, tools, activeTool, disabled, running, usage,
  uploadedFiles, onImportedFilesChange, onAudioChange, execution, onExecutionChange,
  onBuiltIn, onToolChange, commandMode = "full", cancelPending = false,
  showProfiles, onShowProfilesChange, selectionLabel = "Next Run",
  onDraftComputerFiles, onDraftAudioFiles, onMediaBusy,
  toolbarTarget, onChooseAttachments, attachmentDisabled = false, attachmentActionsLocked = false,
}: {
  agentId: string;
  value: string;
  onValueChange: (value: string) => void;
  tools: AgentInteractionTool[];
  activeTool: AgentInteractionTool | null;
  disabled: boolean;
  running: boolean;
  usage?: PrivateAgentRunDisplay["usage"];
  uploadedFiles: AgentFileTransfer[];
  onImportedFilesChange: (files: AgentFileTransfer[]) => void;
  onAudioChange: (ids: string[]) => void;
  execution: ExecutionChoice;
  onExecutionChange: (value: ExecutionChoice) => void;
  onBuiltIn: (command: string) => void;
  onToolChange: (toolName: string) => void;
  commandMode?: "full" | "interaction";
  cancelPending?: boolean;
  showProfiles: boolean;
  onShowProfilesChange: (value: boolean) => void;
  selectionLabel?: string;
  onDraftComputerFiles?: (files: AgentFileTransfer[]) => void;
  onDraftAudioFiles?: (files: AgentFileTransfer[]) => void;
  onMediaBusy?: (busy: boolean) => void;
  toolbarTarget?: HTMLDivElement | null;
  onChooseAttachments?: () => void;
  attachmentDisabled?: boolean;
  attachmentActionsLocked?: boolean;
}) {
  const { apiContext } = useAuth();
  const [computerFiles, setComputerFiles] = useState<AgentFileTransfer[]>([]);
  const [audio, setAudio] = useState<AgentFileTransfer[]>([]);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [dismissedSlash, setDismissedSlash] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [computerPickerOpen, setComputerPickerOpen] = useState(false);
  const [computerSearch, setComputerSearch] = useState("");
  const addButton = useRef<HTMLButtonElement>(null);
  const addMenu = useRef<HTMLDivElement>(null);
  const computerSearchInput = useRef<HTMLInputElement>(null);
  const media = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const startedAt = useRef(0);
  const audioPreviews = useRef(new Map<string, string>());
  const commandList = useRef<HTMLDivElement | null>(null);
  const overlayAnchor = useRef<HTMLDivElement | null>(null);
  const composerInput = useRef<HTMLTextAreaElement | null>(null);
  const profileSelect = useRef<HTMLSelectElement | null>(null);
  const active = useRef(true);
  const [uploadingAudio, setUploadingAudio] = useState(false);
  const audioDrafts = useRef<AgentFileTransfer[]>([]);

  useEffect(() => onImportedFilesChange(computerFiles), [computerFiles, onImportedFilesChange]);
  useEffect(() => onDraftComputerFiles?.(computerFiles), [computerFiles, onDraftComputerFiles]);
  useEffect(() => onAudioChange(audio.map(item => item.file_id)), [audio, onAudioChange]);
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt.current) / 1000);
      setElapsed(seconds);
      if (seconds >= 300) media.current?.stop();
    }, 250);
    return () => window.clearInterval(timer);
  }, [recording]);
  useEffect(() => { active.current = true; return () => {
    active.current = false;
    const recorder = media.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    recorder?.stream.getTracks().forEach(track => track.stop());
    audioPreviews.current.forEach(url => URL.revokeObjectURL(url));
    audioPreviews.current.clear();
  }; }, []);
  useEffect(() => {
    if (dismissedSlash && dismissedSlash !== value) setDismissedSlash("");
  }, [dismissedSlash, value]);
  useEffect(() => {
    if (!showProfiles) return;
    const frame = window.requestAnimationFrame(() => profileSelect.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [showProfiles]);

  const slashMatch = value.match(/^\/([a-z0-9-]*)$/i);
  const atMatch = value.match(/(?:^|\s)@([^\s@]*)$/u);
  const fileQuery = computerPickerOpen ? computerSearch : atMatch?.[1] || "";
  const computer = useQuery({
    queryKey: ["agent-computer-files", apiContext, agentId, fileQuery],
    queryFn: () => api.agentComputerFiles(apiContext, agentId, fileQuery),
    enabled: Boolean(agentId && (atMatch || computerPickerOpen) && !running && activeTool?.accepts_files),
    staleTime: 5_000,
  });
  const importFile = useMutation({
    mutationFn: (item: { path: string; size_bytes: number }) => api.importAgentComputerFile(apiContext, agentId, item.path, item.size_bytes),
    onSuccess: (file) => {
      if (!active.current) return;
      setComputerFiles(current => {
        if (current.some(item => item.file_id === file.file_id)) return current;
        if (current.length + uploadedFiles.length + audio.length >= 8) {
          toast.error("A Run can include at most eight files and recordings.");
          return current;
        }
        return [...current, file];
      });
      if (!computerPickerOpen) {
        const prefix = value.slice(0, value.length - (atMatch?.[1]?.length || 0) - 1);
        onValueChange(`${prefix}@${file.name} `);
      }
      setComputerPickerOpen(false);
      overlayAnchor.current?.closest("form")?.querySelector("textarea")?.focus();
    },
    onError: () => toast.error("Computer file could not be imported. Refresh its folder and retry."),
  });
  useEffect(() => onMediaBusy?.(recording || uploadingAudio || importFile.isPending), [recording, uploadingAudio, importFile.isPending, onMediaBusy]);

  const profiles = activeTool?.execution_profiles || [];
  const defaultProfile = profiles.find(item => item.is_default) || profiles[0];
  const selectedProfile = execution.profileId
    ? profiles.find(item => item.id === execution.profileId)
    : defaultProfile;
  const followsPublisherDefault = Boolean(profiles.length && !execution.profileId);
  const profileLabel = !profiles.length
    ? "Managed by Agent"
    : followsPublisherDefault
      ? `Publisher default · ${defaultProfile?.label || "Unavailable"}`
      : selectedProfile?.label || "Profile unavailable";
  const reasoningEfforts = selectedProfile?.reasoning_efforts || [];
  const profileDefaultEffort = selectedProfile?.default_reasoning_effort || reasoningEfforts[0] || "";

  async function uploadAudio(file: File) {
    if (!active.current) return;
    setUploadingAudio(true);
    try {
    if (file.size > 25 * 1024 * 1024) throw new Error("Recording exceeds 25 MiB.");
    let state = await api.createAgentFile(apiContext, agentId, file, "audio");
    function remember() {
      if (!active.current) return;
      audioDrafts.current = [...audioDrafts.current.filter(row => row.file_id !== state.file_id), state];
      onDraftAudioFiles?.(audioDrafts.current);
    }
    remember();
    while (state.received_bytes < file.size) {
      if (!active.current) return;
      const offset = state.received_bytes;
      const chunk = file.slice(offset, offset + state.chunk_bytes);
      const digest = await crypto.subtle.digest("SHA-256", await chunk.arrayBuffer());
      const sha = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
      state = await api.putAgentFileChunk(apiContext, state.file_id, offset, chunk, sha);
      remember();
    }
    if (!active.current) return;
    state = await api.completeAgentFile(apiContext, state.file_id);
    remember();
    while (["queued", "processing"].includes(state.state)) {
      await new Promise(resolve => window.setTimeout(resolve, 600));
      if (!active.current) return;
      state = await api.agentFileStatus(apiContext, state.file_id);
      remember();
    }
    if (!active.current) return;
    if (state.state !== "ready") throw new Error("Audio verification failed.");
    const previewUrl = URL.createObjectURL(file);
    audioPreviews.current.set(state.file_id, previewUrl);
    setAudio(current => {
      if (current.length + uploadedFiles.length + computerFiles.length >= 8) {
        URL.revokeObjectURL(previewUrl);
        audioPreviews.current.delete(state.file_id);
        toast.error("A Run can include at most eight files and recordings.");
        return current;
      }
      return [...current, state];
    });
    } finally { if (active.current) setUploadingAudio(false); }
  }

  async function toggleRecording() {
    if (recording) { media.current?.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!active.current) { stream.getTracks().forEach(track => track.stop()); return; }
      const recorder = new MediaRecorder(stream);
      chunks.current = [];
      recorder.ondataavailable = event => { if (event.data.size) chunks.current.push(event.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach(track => track.stop());
        if (!active.current) return;
        setRecording(false);
        const mediaType = (recorder.mimeType || "audio/webm").split(";", 1)[0].toLowerCase();
        const blob = new Blob(chunks.current, { type: mediaType });
        const extension = blob.type.includes("ogg") ? "ogg" : "webm";
        void uploadAudio(new File([blob], `voice-${Date.now()}.${extension}`, { type: blob.type })).catch(error => toast.error(error instanceof Error ? error.message : "Audio upload failed"));
      };
      recorder.start(500);
      media.current = recorder;
      startedAt.current = Date.now();
      setElapsed(0);
      setRecording(true);
    } catch { toast.error("Microphone permission is unavailable."); }
  }

  const slashItems = useMemo(() => {
    const query = slashMatch?.[1]?.toLowerCase() || "";
    const builtIns = commandMode === "interaction" || running
      ? BUILT_INS.filter(([name]) => name === "cancel" || name === "help")
      : BUILT_INS.filter(([name]) => name !== "cancel" && (name !== "model" || profiles.length > 0));
    return [
      ...builtIns.map(([name, description]) => ({ name, description, tool: "", disabled: name === "cancel" && cancelPending })),
      ...(commandMode === "full" && !running ? tools.filter(tool => tool.slash_command && !RESERVED_COMMANDS.has(tool.slash_command)).map(tool => ({
        name: tool.slash_command!, description: tool.slash_description || tool.description, tool: tool.name,
        disabled: !tool.availability.can_invoke,
      })) : []),
    ].filter(item => item.name.startsWith(query));
  }, [cancelPending, commandMode, profiles.length, slashMatch, tools, running]);

  function chooseCommand(item: { name: string; tool: string; disabled?: boolean }) {
    if (item.disabled) return;
    if (item.tool) {
      onToolChange(item.tool);
      onValueChange("");
    } else {
      onBuiltIn(item.name);
      onValueChange("");
    }
  }
  const slashMenuOpen = Boolean(slashMatch && dismissedSlash !== value);
  useEffect(() => {
    if (!slashMenuOpen) return;
    function handleComposerKeys(event: globalThis.KeyboardEvent) {
      if (!(event.target instanceof HTMLTextAreaElement)) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setDismissedSlash(value);
        return;
      }
      if (event.key === "ArrowDown") {
        const first = commandList.current?.querySelector<HTMLButtonElement>('[role="option"]:not([disabled])');
        if (!first) return;
        composerInput.current = event.target;
        event.preventDefault();
        event.stopPropagation();
        first.focus();
        return;
      }
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
      const query = slashMatch?.[1]?.toLowerCase() || "";
      const enabled = slashItems.filter(item => !item.disabled);
      const match = enabled.find(item => item.name === query) || (enabled.length === 1 ? enabled[0] : null);
      if (!match) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      chooseCommand(match);
    }
    document.addEventListener("keydown", handleComposerKeys, true);
    return () => document.removeEventListener("keydown", handleComposerKeys, true);
  }, [slashItems, slashMatch, slashMenuOpen, value]);
  function handleCommandListKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setDismissedSlash(value);
      window.requestAnimationFrame(() => composerInput.current?.focus());
      return;
    }
    moveListboxFocus(event);
  }

  useEffect(() => {
    if (!addOpen) return;
    const frame = requestAnimationFrame(() => addMenu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus());
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !addMenu.current?.contains(event.target) && !addButton.current?.contains(event.target)) setAddOpen(false);
    };
    const dismiss = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setAddOpen(false); addButton.current?.focus(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", dismiss);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", dismiss); };
  }, [addOpen]);
  useEffect(() => {
    if (!computerPickerOpen) return;
    const frame = requestAnimationFrame(() => computerSearchInput.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [computerPickerOpen]);
  useEffect(() => {
    if (attachmentActionsLocked) { setComputerPickerOpen(false); setAddOpen(false); }
  }, [attachmentActionsLocked]);
  const acceptsAttachments = Boolean(activeTool?.accepts_files || activeTool?.input_modalities?.includes("image"));
  const toolbarControls = <div className="flex min-h-11 min-w-0 items-center gap-1">
    {onChooseAttachments && acceptsAttachments ? <button ref={addButton} type="button" aria-label="Attach" title="Add files or images" aria-haspopup="menu" aria-expanded={addOpen}
      disabled={attachmentDisabled && disabled} onClick={() => setAddOpen(open => !open)}
      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-[#535350] hover:bg-black/5 disabled:opacity-40"><Plus size={20} /></button> : null}
    {activeTool?.input_modalities?.includes("audio") ? <button type="button" disabled={disabled && !recording} onClick={() => void toggleRecording()} className={`inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 text-xs ${recording ? "bg-red-50 text-red-800" : "text-[#535350] hover:bg-black/5"}`}>{recording ? <MicOff size={13} /> : <Mic size={13} />}{recording ? `Stop · ${formatDuration(elapsed)}` : "Voice"}</button> : null}
    {profiles.length ? <button type="button" onClick={() => onShowProfilesChange(!showProfiles)} className="inline-flex min-h-11 min-w-0 max-w-44 items-center gap-1.5 rounded-md px-2 text-xs text-[#535350] hover:bg-black/5" aria-expanded={showProfiles} aria-label={`${selectionLabel} execution · ${profileLabel}`} title={profileLabel}><Gauge size={13} className="shrink-0" /><span className="truncate">{profileLabel}</span></button> : <span className="inline-flex min-h-11 min-w-0 items-center gap-1.5 px-2 text-xs text-[#6f6e69]" title="This Agent does not expose caller-selectable execution profiles."><Gauge size={13} className="shrink-0" /><span className="truncate">Managed by Agent</span></span>}
    <span className="composer-context-usage ml-auto truncate text-xs text-[#858481]" title="Context usage">Context usage · {usage?.reported && usage.current ? `${Math.min(100, Math.round(usage.current.input_tokens / usage.current.context_window * 100))}%` : "Not reported"}</span>
  </div>;

  return <div ref={overlayAnchor} className="relative grid gap-2">
    {addOpen ? <ComposerOverlay anchor={overlayAnchor}><div ref={addMenu} role="menu" aria-label="Add attachments" className="max-h-[inherit] overflow-auto rounded-xl border border-black/15 bg-white p-2 shadow-lg" onBlur={event => { if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== addButton.current) setAddOpen(false); }} onKeyDown={event => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]:not(:disabled)'));
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }}>
      <button type="button" role="menuitem" disabled={attachmentDisabled} onClick={() => { setAddOpen(false); onChooseAttachments?.(); addButton.current?.focus(); }} className="flex min-h-11 w-full items-center gap-3 rounded-md px-2 text-left text-sm hover:bg-black/5 disabled:opacity-40"><Paperclip size={16} />{activeTool?.accepts_files ? activeTool.input_modalities?.includes("image") ? "Upload files and images" : "Upload files" : "Upload images"}</button>
      {activeTool?.accepts_files ? <button type="button" role="menuitem" disabled={disabled} onClick={() => { setAddOpen(false); setComputerSearch(""); setComputerPickerOpen(true); }} className="flex min-h-11 w-full items-center gap-3 rounded-md px-2 text-left text-sm hover:bg-black/5 disabled:opacity-40"><Monitor size={16} />Import from Computer</button> : null}
      <p className="px-2 py-2 text-xs text-[#6f6e69]">You can also paste or drop files here. Attachments stay private to this Run.</p>
      {activeTool?.input_modalities?.includes("image") ? <p className="px-2 pb-2 text-xs text-[#6f6e69]">Up to 4 images · PNG, JPEG, WebP · over 2 MiB optimized locally (20 MiB source limit).</p> : null}
      {disabled && activeTool?.accepts_files ? <p className="px-2 pb-2 text-xs text-[#6f6e69]">Computer import is available when the Agent is ready and the Computer is authorized.</p> : null}
    </div></ComposerOverlay> : null}
    {slashMenuOpen ? <ComposerOverlay anchor={overlayAnchor}><div ref={commandList} role="listbox" aria-label="Slash commands" onKeyDown={handleCommandListKeys} className="max-h-[inherit] overflow-auto rounded-lg border border-black/10 bg-white p-2 shadow-xl">
      <div className="px-2 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Commands</div>
      {slashItems.map(item => <button key={`${item.tool}:${item.name}`} type="button" role="option" aria-selected="false" disabled={item.disabled} onClick={() => chooseCommand(item)} className="flex min-h-11 w-full items-center gap-3 rounded-md px-2 text-left hover:bg-[#f3f3f1] disabled:cursor-not-allowed disabled:opacity-45"><Slash size={14} /><span className="w-24 font-mono text-xs">/{item.name}</span><span className="min-w-0 flex-1 truncate text-xs text-[#6f6e69]">{item.description}</span></button>)}
      {!slashItems.length ? <div role="status" className="px-2 py-3 text-xs text-[#6f6e69]">No matching command. Use /help to review available commands.</div> : null}
    </div></ComposerOverlay> : null}
    {commandMode === "full" ? <>
    {(atMatch || computerPickerOpen) && activeTool?.accepts_files ? <ComposerOverlay anchor={overlayAnchor}><div className="max-h-[inherit] overflow-auto rounded-lg border border-black/10 bg-white p-2 shadow-xl" onKeyDown={event => { if (event.key === "Escape" && computerPickerOpen) { event.preventDefault(); setComputerPickerOpen(false); addButton.current?.focus(); } }}>
      {computerPickerOpen ? <div className="mb-2 flex items-center gap-2"><input ref={computerSearchInput} aria-label="Search Computer files" placeholder="Search Computer files" value={computerSearch} onChange={event => setComputerSearch(event.target.value)} onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); }} className="min-h-11 min-w-0 flex-1 rounded-md border border-black/15 px-2 text-sm" /><button type="button" aria-label="Close Computer files" className="h-11 w-11 shrink-0 rounded-md hover:bg-black/5" onClick={() => { setComputerPickerOpen(false); addButton.current?.focus(); }}><X size={16} className="mx-auto" /></button></div> : null}
      <div role="listbox" aria-label="File mentions" onKeyDown={moveListboxFocus}>
      <div className="px-2 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Uploaded</div>
      {[...uploadedFiles, ...computerFiles].map(file => <button key={file.file_id} type="button" role="option" aria-selected="false" onClick={() => { if (atMatch && !computerPickerOpen) { const prefix = value.slice(0, value.length - (atMatch[1]?.length || 0) - 1); onValueChange(`${prefix}@${file.name} `); } setComputerPickerOpen(false); }} className="flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-left hover:bg-[#f3f3f1]"><FileText size={14} /><span className="truncate text-sm">{file.name}</span></button>)}
      <div className="mt-1 border-t border-black/10 px-2 py-2 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Computer</div>
      {computer.isLoading ? <div className="flex min-h-11 items-center gap-2 px-2 text-xs"><Loader2 className="animate-spin" size={14} /> Searching</div> : null}
      {computer.data?.items.filter(item => item.type === "file").map(item => <button key={item.path} type="button" role="option" aria-selected="false" disabled={disabled || importFile.isPending} onClick={() => importFile.mutate(item)} className="flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-left hover:bg-[#f3f3f1] disabled:opacity-50"><AtSign size={14} /><span className="min-w-0 flex-1 truncate text-sm">{item.name}</span><span className="text-xs text-[#858481]">{formatBytes(item.size_bytes)}</span></button>)}
      {computer.isError ? <div className="px-2 py-3 text-xs text-[#9b2c2c]">Computer files unavailable or not authorized.</div> : null}
      {computer.data && !computer.data.items.some(item => item.type === "file") ? <p className="px-2 py-3 text-xs text-[#6f6e69]">No matching Computer files. Try another search.</p> : null}
      </div>
    </div></ComposerOverlay> : null}
    {[...computerFiles, ...audio].length ? <div className="flex flex-wrap gap-2">
      {computerFiles.map(file => <FileChip key={file.file_id} icon={<AtSign size={12} />} file={file} onRemove={() => setComputerFiles(current => current.filter(item => item.file_id !== file.file_id))} />)}
      {audio.map(file => <div key={file.file_id} className="flex max-w-full items-center gap-2"><FileChip icon={<FileAudio size={12} />} file={file} onRemove={() => {
        const previewUrl = audioPreviews.current.get(file.file_id);
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        audioPreviews.current.delete(file.file_id);
        audioDrafts.current = audioDrafts.current.filter(item => item.file_id !== file.file_id);
        onDraftAudioFiles?.(audioDrafts.current);
        setAudio(current => current.filter(item => item.file_id !== file.file_id));
      }} />{audioPreviews.current.get(file.file_id) ? <audio controls preload="metadata" src={audioPreviews.current.get(file.file_id)} className="h-9 max-w-48" aria-label={`Preview ${file.name}`} /> : null}</div>)}
    </div> : null}
    {toolbarTarget ? createPortal(toolbarControls, toolbarTarget) : toolbarTarget === undefined ? toolbarControls : null}
    {showProfiles && profiles.length ? <div className="grid gap-3 rounded-md border border-black/10 bg-white p-3 sm:grid-cols-2">
      <div className="sm:col-span-2 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">{selectionLabel} execution</div>
      <label className="grid gap-1 text-xs font-semibold">Execution profile<select ref={profileSelect} value={execution.profileId} disabled={running} onChange={event => { const profile = profiles.find(item => item.id === event.target.value); onExecutionChange(profile ? { profileId: profile.id, reasoningEffort: "" } : { profileId: "", reasoningEffort: "" }); }} className="min-h-10 rounded-md border border-black/10 px-2 font-normal"><option value="">Publisher default · {defaultProfile?.label} · {defaultProfile?.model}</option>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.label} · {profile.model}{profile.is_default ? " · default" : ""}</option>)}</select></label>
      <label className="grid gap-1 text-xs font-semibold">Reasoning<select aria-label="Reasoning" value={execution.reasoningEffort} disabled={!execution.profileId || !reasoningEfforts.length || running} onChange={event => onExecutionChange({ profileId: selectedProfile?.id || "", reasoningEffort: event.target.value })} className="min-h-10 rounded-md border border-black/10 px-2 font-normal"><option value="">{execution.profileId ? `Profile default · ${profileDefaultEffort || "managed by model"}` : `Publisher default · ${profileDefaultEffort || "managed by model"}`}</option>{reasoningEfforts.map(effort => <option key={effort} value={effort}>{effort}</option>)}</select></label>
      {usage?.reported && usage.current ? <div className="sm:col-span-2 border-t border-black/10 pt-3 text-xs text-[#535350]"><div className="mb-2 flex flex-wrap items-center justify-between gap-2"><span className="font-semibold text-[#34322d]">Observed usage · {usage.current.model}{usage.current.profile_id ? ` · ${usage.current.profile_id}` : ""}</span><span className="font-mono text-[10px] uppercase tracking-[0.06em] text-[#6f6e69]">{usage.current.source === "gateway" ? "Gateway verified" : "Agent reported"}</span></div><div className="grid grid-cols-2 gap-2"><span>Input {usage.current.input_tokens.toLocaleString()}</span><span>Output {usage.current.output_tokens.toLocaleString()}</span><span>Cached {usage.current.cached_input_tokens.toLocaleString()}</span><span>Reasoning {usage.current.reasoning_tokens.toLocaleString()}</span><span>Run input total {usage.totals.input_tokens.toLocaleString()}</span><span>Run output total {usage.totals.output_tokens.toLocaleString()}</span></div></div> : <p className="sm:col-span-2 text-xs text-[#858481]">No model or token usage has been reported by the Agent or Nexus Gateway.</p>}
    </div> : null}
    </> : <div className="flex items-center gap-2 px-1"><span className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-black/10 bg-white px-2 text-xs text-[#535350]"><Slash size={13} /> commands</span></div>}
  </div>;
}

// Menus must escape the bounded composer scroll region without resizing it.
export function ComposerOverlay({ anchor, children }: { anchor: RefObject<HTMLDivElement | null>; children: ReactNode }) {
  const [position, setPosition] = useState({ left: 0, bottom: 0, width: 0, maxHeight: 0 });
  useLayoutEffect(() => {
    const element = anchor.current;
    if (!element) return;
    const boundary = element.closest("form") || element;
    function update() {
      const rect = boundary.getBoundingClientRect();
      setPosition({ left: Math.max(8, rect.left), bottom: window.innerHeight - rect.top + 8, width: rect.height ? Math.min(rect.width, window.innerWidth - 16) : 0, maxHeight: Math.max(44, Math.min(288, rect.top - 16)) });
    }
    update();
    const observer = new ResizeObserver(update);
    observer.observe(boundary);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); };
  }, [anchor]);
  return position.width > 0 ? createPortal(<div className="fixed z-40" style={position}>{children}</div>, document.body) : null;
}

function FileChip({ file, icon, onRemove }: { file: AgentFileTransfer; icon: ReactNode; onRemove: () => void }) {
  return <span className="inline-flex min-h-9 max-w-full items-center gap-2 rounded-md border border-black/10 bg-white px-2 text-xs">{icon}<span className="truncate">{file.name}</span><span className="text-[#858481]">{file.source_kind}</span><button type="button" onClick={onRemove} className="inline-flex h-7 w-7 items-center justify-center rounded" aria-label={`Remove ${file.name}`}><X size={12} /></button></span>;
}

function formatBytes(value: number) { return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KiB` : `${(value / 1024 / 1024).toFixed(1)} MiB`; }
function formatDuration(value: number) { return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`; }

function moveListboxFocus(event: KeyboardEvent<HTMLDivElement>) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const options = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="option"]:not([disabled])'));
  if (!options.length) return;
  event.preventDefault();
  const current = options.indexOf(document.activeElement as HTMLElement);
  const index = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : event.key === "ArrowDown" ? Math.min(options.length - 1, current + 1) : Math.max(0, current < 0 ? options.length - 1 : current - 1);
  options[index]?.focus();
}
