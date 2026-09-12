import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, BookOpenText, Bot, CheckCircle2, ChevronLeft, ChevronRight, Copy, Download, FileText, Folder, FolderOpen, History, ListTodo, Loader2, LockKeyhole, Maximize2, MessageSquare, Minus, Monitor, Plus, Search, Terminal, WrapText, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { useApplicationDistribution } from '../app/distribution';
import { RunPresentationSummary, useRunHistoryRemovalDescription } from "../components/RunPresentation";
import { ChatCopyButton } from "../components/ChatCopyButton";
import { ComposerDeviceMenu } from "../components/ComposerDeviceMenu";
import { chatMessageText } from "../lib/chatMessageText";
import { clearSubmittedDraft, quotedReply, readRunDraft, runDraftKey, useRunDraft, writeRunDraft } from "../lib/runDrafts";
import { clearSubmittedAssets, useComposerDraft, readComposerDraft, writeComposerDraft } from "../lib/composerDrafts";
import { RestoredComposerAttachments, useRestoredAttachments } from "../components/RestoredComposerAttachments";
import { useRunChatScroll } from "../lib/useRunChatScroll";
import { contextRevision, runContextKey, useContextView, useContextUpdates, useContextScroll, type ContextPanel } from "../lib/runContextState";
import { AgentChatMessageContent } from "../components/AgentChatMessageContent";
import { AgentComputerAttachDialog } from "../components/AgentComputerAttachDialog";
import { AgentDisplayComposer, AgentDisplayComputerFrame, AgentDisplayHeaderFrame, AgentDisplayMobileTabs, AgentDisplayModeTabs, AgentDisplayPanel, AgentDisplaySurface, AgentDisplayWorkspace } from "../components/AgentDisplayFrame";
import { NexilumeDialog } from "../components/NexilumeControls";
import { ContentMarkdown } from "../components/ContentMarkdown";
import { PrivateAgentRunRail } from "../components/PrivateAgentRunRail";
import { FOLLOW_UP_MAX_CHARACTERS, RunFollowUps, RunFollowUpMode, useRunFollowUps } from "../components/RunFollowUps";
import type { FollowUpAttachmentControl } from "../components/RunFollowUps";
import { isConcurrentRunQuota, RunCapacityRecovery } from "../components/RunCapacityRecovery";
import { currentRunFailure, RunFailureNotice } from "../components/RunFailureNotice";
import { type RunImageAttachment } from "../components/RunImageAttachments";
import { RunFilesWorkspace } from "../components/RunFilesWorkspace";
import { fileReference, type RunFile } from "../lib/runFiles";
import { RunAttachmentPicker, type RunAttachmentIntake } from "../components/RunAttachmentPicker";
import { PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS, PrivateDisplayComposerTools, type ExecutionChoice } from "../components/PrivateDisplayComposerTools";
import { api, ApiError } from "../lib/api";
import { runDisplayPolling } from "../lib/runDisplayPolling";
import { privateDisplayAttachmentUrl, privateDisplayReturnTo } from "../lib/agentDisplayNavigation";
import { agentDisplayBrowserFrames, buildAgentDisplayShellCommands, buildAgentDisplayShellTranscript, deriveAgentDisplayMessages, deriveAgentDisplayShell } from "../lib/agentDisplayEvents";
import type { AgentDisplayBrowserFrame, AgentDisplayShellLine } from "../lib/agentDisplayEvents";
import { usePrivateAgentTerminal, type PrivateAgentTerminalConnection } from "../lib/usePrivateAgentTerminal";
import type { RunHistoryFilter, AgentInteractionTool, AgentChatContentBlock, AgentOutputArtifact, AgentPrivateRun, AgentRunInteraction, AgentRunMessage, AgentRunTerminal, DisplayStreamEvent, PrivateAgentRunDisplay } from "../lib/types";

type WorkspaceMode = "chat" | "shell" | "browser";
type MobilePanel = "history" | "live" | "context";
type PlanStep = { id: string; label: string; state: string; detail?: string };
type RunSubmission = { content?: string; arguments?: Record<string, unknown>; attachments?: Array<{ asset_id: string }>; files?: string[]; audio?: string[]; execution_profile_id?: string; reasoning_effort?: string };

const MOBILE_PANELS = [
  { id: "history", label: "Runs", icon: History },
  { id: "live", label: "Live", icon: Monitor },
  { id: "context", label: "Context", icon: ListTodo },
] as const;

export function PrivateAgentRunDisplayPage() {
  const { agentDisplayNavigation } = useApplicationDistribution();
  const historyRemovalDescription = useRunHistoryRemovalDescription();
  const { agentId: routeAgentId = "", runId: routeRunId = "" } = useParams();
  const { apiContext, isContextReady, user, projects, setProjectId } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const urlRunId = searchParams.get("run") || routeRunId;
  // BrowserRouter commits navigation in a React transition. Switch the editor
  // scope urgently on a history click, before the old textarea can accept input
  // for an already-changed URL and then be detached by the deferred commit.
  const [runSelection, setRunSelection] = useState<{ id: string; agent: string } | null>(null);
  const selectedRunId = runSelection?.id ?? urlRunId;
  useEffect(() => { if (runSelection?.id === urlRunId) setRunSelection(null); }, [urlRunId, runSelection]);
  const search = searchParams.get("q") || "";
  const historyFilter = (["all", "running", "input_required", "completed_unread", "failed"].includes(searchParams.get("history") || "") ? searchParams.get("history") : "all") as RunHistoryFilter;
  function setSearch(value: string) { setSearchParams(current => { const next = new URLSearchParams(current); if (value) next.set("q", value); else next.delete("q"); return next; }, { replace: true, state: location.state }); }
  function setHistoryFilter(value: RunHistoryFilter) { setSearchParams(current => { const next = new URLSearchParams(current); if (value !== "all") next.set("history", value); else next.delete("history"); return next; }, { replace: true, state: location.state }); }
  const attachmentIntake = useRef<RunAttachmentIntake>(null);
  const fileReferenceHandler = useRef<((text: string, checkOnly?: boolean) => boolean) | null>(null);
  const [imageAttachments, setImageAttachments] = useState<RunImageAttachment[]>([]);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [fileIds, setFileIds] = useState<string[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<import("../lib/api").AgentFileTransfer[]>([]);
  const [computerFiles, setComputerFiles] = useState<import("../lib/api").AgentFileTransfer[]>([]);
  const [audioIds, setAudioIds] = useState<string[]>([]);
  const [mediaBusy, setMediaBusy] = useState(false);
  const [uploadingFiles, setUploadingFiles] = useState(false);
  const [fileEpoch, setFileEpoch] = useState(0);
  const [selectedTool, setSelectedTool] = useState("");
  const [showProfiles, setShowProfiles] = useState(false);
  const [slashHelpOpen, setSlashHelpOpen] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("chat");
  const contextKey = runContextKey(user?.user_id, apiContext.tenantId, apiContext.projectId, selectedRunId || `agent:${routeAgentId}`);
  const { panel: contextPanel, setPanel: setContextPanel, mobile: mobilePanel, setMobile: setMobilePanel } = useContextView(contextKey);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 1024px)").matches);
  const [liveUnread, setLiveUnread] = useState(0);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => setDesktop(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [optimisticMessage, setOptimisticMessage] = useState("");
  const [optimisticReply, setOptimisticReply] = useState("");
  const optimisticBase = useRef(0);
  const [createdConversation, setCreatedConversation] = useState<{ scope: string; runId: string; key: string; content: string } | null>(null);
  const [streamEvents, setStreamEvents] = useState<DisplayStreamEvent[]>([]);
  const eventCursor = useRef(0);
  const [historyCursor, setHistoryCursor] = useState<number | null>(null);
  const terminalCursor = useRef(0);
  const terminalEpoch = useRef("");
  const [terminalEvents, setTerminalEvents] = useState<Awaited<ReturnType<typeof api.privateAgentRunTerminal>>["events"]>([]);
  const [terminalInfo, setTerminalInfo] = useState<AgentRunTerminal | null>(null);
  const [renameTarget, setRenameTarget] = useState<AgentPrivateRun | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<AgentPrivateRun | null>(null);
  const [computerDialogOpen, setComputerDialogOpen] = useState(false);
  const [runDetailsOpen, setRunDetailsOpen] = useState(false);
  const [projectConsentOpen, setProjectConsentOpen] = useState(false);
  const [quotaIssue, setQuotaIssue] = useState<{ key: string; message: string } | null>(null);

  const token = useQuery({
    queryKey: ["agent-run-display-token", apiContext, selectedRunId],
    queryFn: () => api.issueAgentRunDisplayToken(apiContext, selectedRunId),
    enabled: Boolean(isContextReady && selectedRunId),
    staleTime: 25 * 60 * 1000,
    refetchInterval: 30 * 60 * 1000,
    refetchOnWindowFocus: true,
  });
  const displayToken = token.data?.display_token ?? "";
  const run = useQuery({
    queryKey: ["private-agent-run", apiContext, selectedRunId, displayToken],
    queryFn: () => api.privateAgentRunDisplay(apiContext, selectedRunId, displayToken),
    enabled: Boolean(displayToken),
    // Token renewal is not a different conversation. Never carry data across
    // an account/Organization/Project/Run boundary, even during loading.
    placeholderData: (previous, query) => JSON.stringify(query?.queryKey.slice(0, 3)) === JSON.stringify(["private-agent-run", apiContext, selectedRunId]) ? previous : undefined,
    refetchInterval: (query) => runDisplayPolling({ status: query.state.data?.status, attached: Boolean(query.state.data?.computer.attached), shell: false, files: false }).run,
  });
  const agentId = routeAgentId || runSelection?.agent || run.data?.agent_id || "";
  const pendingInteraction = run.data?.interactions.find((item) => item.status === "pending") ?? null;
  const conversationKey = runDraftKey(user?.user_id, apiContext.tenantId, apiContext.projectId, agentId, selectedRunId);
  const currentAttachmentConversation = useRef(conversationKey); currentAttachmentConversation.current = conversationKey;
  const conversationScope = runDraftKey(user?.user_id, apiContext.tenantId, apiContext.projectId, agentId, "");
  const assetDraft = useComposerDraft(conversationKey);
  const adoptingCreatedRun = createdConversation?.scope === conversationScope && createdConversation.runId === selectedRunId;
  const liveConversationKey = adoptingCreatedRun ? createdConversation.key : conversationKey;
  const draft = useRunDraft(runDraftKey(user?.user_id, apiContext.tenantId, apiContext.projectId, agentId, selectedRunId, pendingInteraction ? `interaction:${pendingInteraction.id}` : "chat"));
  const { text: message, setText: setMessage } = draft;
  const computerRevision = run.data?.computer.revision ?? 0;
  const interactor = useQuery({
    queryKey: ["agent-interactor", apiContext, agentId],
    queryFn: () => api.agentInteractor(apiContext, agentId),
    select: (data) => {
      const computer = run.data?.computer;
      if (!selectedRunId || computer?.online === undefined) return data;
      const ready = !data.computer.missing_scopes.length && (computer.attached
        ? computer.online && (!data.computer.declared_scopes.includes("browser.control") || computer.browser_available !== false)
        : data.computer.requirement !== "required");
      return { ...data, computer: { ...data.computer, attached: computer.attached, ready,
        device_name: computer.name, platform: computer.platform || "", browser_name: "",
        browser_available: computer.browser_available ?? null,
        message: ready ? "" : data.computer.missing_scopes.length ? data.computer.message : `${computer.name || "This Run's Computer"} is offline or unavailable. Change Computer for this Run before continuing.` } };
    },
    enabled: Boolean(isContextReady && agentId),
    refetchInterval: (query) => runDisplayPolling({ status: run.data?.status, attached: false, shell: false, files: false, ready: query.state.data?.tools.some(tool => tool.availability.can_invoke) }).readiness,
    refetchOnWindowFocus: "always",
  });
  const runs = useInfiniteQuery({
    queryKey: ["agent-private-runs", apiContext, agentId, search, historyFilter],
    initialPageParam: "",
    queryFn: ({ pageParam }) => api.privateAgentRuns(apiContext, agentId, search, pageParam, historyFilter),
    getNextPageParam: page => page.next_cursor || undefined,
    enabled: Boolean(isContextReady && agentId),
    refetchInterval: query => {
      const counts = query.state.data?.pages[0]?.counts;
      return counts && counts.running + counts.input_required === 0 ? 15_000 : 5_000;
    },
  });
  const historyRuns = useMemo(() => [...new Map((runs.data?.pages.flatMap(page => page.results) || []).map(item => [item.id, item])).values()], [runs.data]);
  const historyCounts = runs.data?.pages[0]?.counts;
  const readAttempts = useRef(new Set<string>());
  const markRead = useMutation({
    mutationFn: (input: { id: string; completedAt: string; token: string }) => api.markPrivateRunRead(apiContext, input.id, input.token, input.completedAt),
    onSuccess: (_response, input) => {
      queryClient.setQueriesData<PrivateAgentRunDisplay>({ queryKey: ["private-agent-run", apiContext, input.id] }, current => current?.completed_at === input.completedAt ? { ...current, completion_unread: false } : current);
      void queryClient.invalidateQueries({ queryKey: ["agent-private-runs", apiContext, agentId] });
      void queryClient.invalidateQueries({ queryKey: ["private", "work-inbox", user?.user_id, apiContext.tenantId] });
    },
  });
  const markReadMutate = markRead.mutate;
  const onCompletionViewed = useCallback((id: string, completedAt: string) => {
    if (!displayToken) return;
    const key = JSON.stringify([user?.user_id, apiContext.tenantId, apiContext.projectId, id, completedAt]);
    if (readAttempts.current.has(key)) return;
    if (readAttempts.current.size >= 500) readAttempts.current.delete(readAttempts.current.values().next().value!);
    readAttempts.current.add(key);
    markReadMutate({ id, completedAt, token: displayToken });
  }, [user?.user_id, apiContext.tenantId, apiContext.projectId, displayToken, markReadMutate]);
  const events = useQuery({
    queryKey: ["private-agent-run-events", apiContext, selectedRunId, displayToken, historyCursor],
    queryFn: async () => {
      const cursor = historyCursor ?? eventCursor.current;
      return { cursor, runId: selectedRunId, rows: await api.privateAgentRunEvents(apiContext, selectedRunId, displayToken, cursor) };
    },
    enabled: Boolean(displayToken),
    refetchInterval: (query) => historyCursor !== null ? false : query.state.data?.rows.length === 100 ? 100 :
      (run.data && ["completed", "failed"].includes(run.data.status) && query.state.data?.rows.length === 0 ? false : 2_000),
  });
  const appendTerminalEvent = useCallback((event: AgentRunTerminal["events"][number]) => {
    if (!event.seq) return;
    setTerminalEvents((current) => current.some((item) => item.seq === event.seq)
      ? current
      : [...current, event].sort((left, right) => left.seq - right.seq));
    terminalCursor.current = Math.max(terminalCursor.current, event.seq);
  }, []);
  const terminalDone = ["completed", "failed", "cancelled", "expired"].includes(run.data?.status || "");
  const terminalConnection = usePrivateAgentTerminal({
    context: apiContext,
    runId: selectedRunId,
    displayToken,
    enabled: Boolean(displayToken && run.data?.computer.attached && workspaceMode === "shell" && !terminalDone),
    onEvent: appendTerminalEvent,
    onStatus: (status) => setTerminalInfo((current) => current ? { ...current, status } : current),
  });
  const terminal = useQuery({
    queryKey: ["private-agent-run-terminal", apiContext, selectedRunId, displayToken, computerRevision],
    queryFn: async () => {
      const epoch = `${selectedRunId}:${computerRevision}`;
      if (terminalEpoch.current !== epoch) { terminalEpoch.current = epoch; terminalCursor.current = 0; }
      const cursor = terminalCursor.current;
      return { cursor, runId: selectedRunId, revision: computerRevision, payload: await api.privateAgentRunTerminal(apiContext, selectedRunId, displayToken, cursor) };
    },
    enabled: Boolean(displayToken),
    refetchInterval: (query) => query.state.data?.payload.has_more ? 100 : terminalConnection === "live" ? false : runDisplayPolling({ status: run.data?.status, attached: Boolean(run.data?.computer.attached), shell: workspaceMode === "shell", files: false }).terminal,
  });

  // Final outputs/transcripts may arrive after the preceding poll. Refresh on
  // transitions and when opening a panel, even once regular polling has stopped.
  useEffect(() => {
    if (!displayToken) return;
    void queryClient.invalidateQueries({ queryKey: ["private-run-file-outputs", apiContext, selectedRunId] });
    void queryClient.invalidateQueries({ queryKey: ["private-agent-run-terminal", apiContext, selectedRunId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-private-runs", apiContext, agentId] });
    if (!["completed", "failed", "cancelled", "expired"].includes(run.data?.status || "")) return;
    const drains = [400, 1_200, 3_000].map((delay) => window.setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: ["private-agent-run-terminal", apiContext, selectedRunId] });
      void queryClient.invalidateQueries({ queryKey: ["private-run-file-outputs", apiContext, selectedRunId] });
    }, delay));
    return () => drains.forEach((timer) => window.clearTimeout(timer));
  }, [run.data?.status, selectedRunId, displayToken, queryClient, apiContext, agentId]);
  useEffect(() => {
    if (!displayToken) return;
    if (workspaceMode === "shell") void queryClient.invalidateQueries({ queryKey: ["private-agent-run-terminal", apiContext, selectedRunId] });
    if (contextPanel === "files") void queryClient.invalidateQueries({ queryKey: ["private-run-file-outputs", apiContext, selectedRunId] });
  }, [workspaceMode, contextPanel, displayToken, apiContext, selectedRunId, queryClient]);

  const chatTools = useMemo(() => interactor.data?.tools.filter((tool) => tool.policy.chat) ?? [], [interactor.data?.tools]);
  const composerTools = useMemo(() => interactor.data?.tools.filter((tool) => tool.policy.chat || Boolean(tool.slash_command)) ?? [], [interactor.data?.tools]);
  const privateDisplayAvailable = interactor.data?.private_display?.available ?? chatTools.length === 1;
  const privateDisplayToolName = interactor.data?.private_display?.tool_name || (chatTools.length === 1 ? chatTools[0].name : "");
  useEffect(() => {
    if (run.data?.tool_name) setSelectedTool(run.data.tool_name);
    else if (!selectedTool && assetDraft.snapshot.tool && composerTools.some(tool => tool.name === assetDraft.snapshot.tool)) setSelectedTool(assetDraft.snapshot.tool);
    else if (!selectedTool && privateDisplayAvailable) setSelectedTool(privateDisplayToolName);
    else if (selectedTool && !composerTools.some((tool) => tool.name === selectedTool)) setSelectedTool(privateDisplayAvailable ? privateDisplayToolName : "");
  }, [composerTools, privateDisplayAvailable, privateDisplayToolName, run.data?.tool_name, selectedTool, assetDraft.snapshot.tool]);
  const activeTool = useMemo(
    () => composerTools.find((tool) => tool.name === (run.data?.tool_name || selectedTool)) ?? null,
    [composerTools, run.data?.tool_name, selectedTool],
  );
  const restoredAssets = useRestoredAttachments(conversationKey, assetDraft.restored, activeTool, { id: selectedRunId, token: displayToken });
  const execution: ExecutionChoice = assetDraft.snapshot.tool === activeTool?.name ? assetDraft.snapshot : { profileId: "", reasoningEffort: "" };
  const selectedProfile = activeTool?.execution_profiles?.find(profile => profile.id === execution.profileId);
  const executionInvalid = Boolean(execution.profileId && (!selectedProfile || (execution.reasoningEffort && !selectedProfile.reasoning_efforts.includes(execution.reasoningEffort))));
  const setExecution = (choice: ExecutionChoice) => assetDraft.choose(activeTool?.name || "", choice);
  const updateImages = useCallback((images: RunImageAttachment[]) => {
    setImageAttachments(images);
    assetDraft.replace("image", images.map(item => ({ id: item.asset_id, kind: "image", name: item.file.name, size: item.file.size, contentType: item.file.type })));
  }, [assetDraft.replace]);
  useEffect(() => { setImageAttachments([]); setUploadingImage(false); setFileIds([]); setUploadedFiles([]); setComputerFiles([]); setAudioIds([]); setUploadingFiles(false); setMediaBusy(false); setShowProfiles(false); }, [conversationKey]);
  useEffect(() => { setTerminalEvents([]); setTerminalInfo(null); }, [computerRevision]);
  useEffect(() => { setComputerDialogOpen(false); }, [selectedRunId]);
  useEffect(() => {
    eventCursor.current = 0;
    setStreamEvents([]);
    setOptimisticMessage("");
    setOptimisticReply("");
    setHistoryCursor(null);
    terminalCursor.current = 0;
    setTerminalEvents([]);
    setTerminalInfo(null);
  }, [selectedRunId]);
  useEffect(() => {
    const batch = events.data;
    if (!batch?.rows.length || batch.runId !== selectedRunId || batch.cursor !== (historyCursor ?? eventCursor.current)) return;
    setStreamEvents((current) => {
      if (historyCursor !== null) return batch.rows;
      const seen = new Set(current.map((event) => event.id));
      const additions = batch.rows.filter((event) => !seen.has(event.id));
      return additions.length ? [...current, ...additions].sort((left, right) => left.seq - right.seq).slice(-1000) : current;
    });
    if (historyCursor === null) eventCursor.current = Math.max(eventCursor.current, ...batch.rows.map((event) => event.seq));
  }, [events.data, historyCursor, selectedRunId]);
  useEffect(() => {
    const batch = terminal.data;
    if (!batch || batch.runId !== selectedRunId || batch.revision !== computerRevision) return;
    setTerminalInfo(batch.payload);
    if (!batch.payload.events.length) return;
    setTerminalEvents((current) => {
      const seen = new Set(current.map((event) => event.seq));
      return [...current, ...batch.payload.events.filter((event) => !seen.has(event.seq))].sort((left, right) => left.seq - right.seq);
    });
    terminalCursor.current = Math.max(terminalCursor.current, ...batch.payload.events.map((event) => event.seq));
  }, [terminal.data, selectedRunId, computerRevision]);

  const createRun = useMutation({
    mutationFn: (input: RunSubmission) => api.createPrivateAgentRun(apiContext, agentId, input, selectedTool),
    onMutate: (input) => { optimisticBase.current = 0; setOptimisticMessage(input.content || ""); return { ...draft.capture(), assetTicket: assetDraft.capture() }; },
    onSuccess: async (response, _input, ticket) => {
      clearSubmittedDraft(ticket);
      clearSubmittedAssets(ticket?.assetTicket);
      if (!draft.isCurrent(ticket)) return;
      // A caller may write the next draft before the first POST is confirmed.
      // Carry it into the newly created Run, without overwriting another draft.
      if (ticket) {
        const remaining = readRunDraft(ticket.key);
        const nextKey = runDraftKey(user?.user_id, apiContext.tenantId, apiContext.projectId, agentId, response.run_id);
        if (remaining && !readRunDraft(nextKey)) { writeRunDraft(nextKey, remaining); writeRunDraft(ticket.key, ""); }
      }
      setQuotaIssue(null);
      setCreatedConversation({ scope: conversationScope, runId: response.run_id, key: conversationKey, content: _input.content || "" });
      setOptimisticMessage("");
      setImageAttachments([]);
      navigate(response.display_url, { replace: true, state: location.state });
      setFileIds([]); setUploadedFiles([]); setComputerFiles([]); setAudioIds([]); setUploadingFiles(false); setFileEpoch(value => value + 1);
      await queryClient.invalidateQueries({ queryKey: ["agent-private-runs", apiContext, agentId] });
    },
    onError: (error, _input, ticket) => { if (!draft.isCurrent(ticket)) return; setOptimisticMessage(""); if (isConcurrentRunQuota(error)) setQuotaIssue({ key: conversationKey, message: errorMessage(error) }); else toast.error(errorMessage(error) || "Unable to start Agent Run"); },
  });
  const resumeRun = useMutation({
    mutationFn: (input: RunSubmission) => api.resumePrivateAgentRun(apiContext, selectedRunId, displayToken, input),
    onMutate: (input) => { optimisticBase.current = run.data?.messages.length || 0; setOptimisticMessage(input.content || ""); return { ...draft.capture(), assetTicket: assetDraft.capture() }; },
    onSuccess: async (_response, _input, ticket) => {
      clearSubmittedDraft(ticket);
      clearSubmittedAssets(ticket?.assetTicket);
      if (!draft.isCurrent(ticket)) return;
      setQuotaIssue(null);
      setImageAttachments([]);
      setFileIds([]); setUploadedFiles([]); setComputerFiles([]); setAudioIds([]); setUploadingFiles(false); setFileEpoch(value => value + 1);
      void queryClient.invalidateQueries({ queryKey: ["private-run-input-files"] });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["private-agent-run"] }),
        queryClient.invalidateQueries({ queryKey: ["private-agent-run-events"] }),
        queryClient.invalidateQueries({ queryKey: ["agent-private-runs", apiContext, agentId] }),
      ]);
    },
    onError: (error, _input, ticket) => {
      if (!draft.isCurrent(ticket)) return;
      setOptimisticMessage("");
      void queryClient.invalidateQueries({ queryKey: ["agent-interactor", apiContext, agentId] });
      if (isConcurrentRunQuota(error)) setQuotaIssue({ key: conversationKey, message: errorMessage(error) });
      else toast.error(errorMessage(error) || "Unable to continue Agent Run");
    },
  });
  const answer = useMutation({
    mutationFn: ({ interaction, value }: { interaction: AgentRunInteraction; value: string }) => api.answerPrivateAgentRunInteraction(apiContext, selectedRunId, interaction.id, displayToken, { text: interaction.choices.find((item) => item.value === value)?.label ?? value, value }),
    onMutate: ({ interaction, value }) => {
      optimisticBase.current = run.data?.messages.length || 0;
      setOptimisticReply(interaction.choices.find((item) => item.value === value)?.label ?? value);
      return draft.capture();
    },
    onSuccess: async (_response, _input, ticket) => {
      clearSubmittedDraft(ticket);
      if (!draft.isCurrent(ticket)) return;
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["private-agent-run"] }), queryClient.invalidateQueries({ queryKey: ["private-agent-run-events"] })]);
    },
    onError: (error, _input, ticket) => {
      if (!draft.isCurrent(ticket)) return;
      setOptimisticReply("");
      toast.error(errorMessage(error) || "Unable to send reply");
    },
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelPrivateAgentRun(apiContext, selectedRunId, displayToken),
    onSuccess: async () => {
      toast.success("Cancellation requested");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["private-agent-run"] }),
        queryClient.invalidateQueries({ queryKey: ["agent-private-runs", apiContext, agentId] }),
      ]);
    },
    onError: (error) => toast.error(errorMessage(error) || "Unable to cancel Agent Run"),
  });
  const recoveryDecision = useMutation({
    mutationFn: (action: "check_status" | "retry" | "cancel") => api.decidePrivateAgentRecovery(apiContext, selectedRunId, displayToken, action),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["private-agent-run"] }),
        queryClient.invalidateQueries({ queryKey: ["private-agent-run-events"] }),
      ]);
    },
    onError: (error) => toast.error(errorMessage(error) || "Unable to apply the recovery decision"),
  });
  const authorizeProjectContext = useMutation({
    mutationFn: () => api.authorizeAgentProjectContext(apiContext, agentId),
    onSuccess: async () => {
      setProjectConsentOpen(false);
      toast.success("Project instructions allowed for this Agent");
      await queryClient.invalidateQueries({ queryKey: ["agent-interactor", apiContext, agentId] });
    },
    onError: (error) => toast.error(errorMessage(error) || "Unable to share Project instructions"),
  });
  const revokeProjectContext = useMutation({
    mutationFn: () => api.revokeAgentProjectContext(apiContext, agentId),
    onSuccess: async () => {
      toast.success("Project instruction access revoked for future Turns");
      await queryClient.invalidateQueries({ queryKey: ["agent-interactor", apiContext, agentId] });
    },
    onError: (error) => toast.error(errorMessage(error) || "Unable to revoke Project instruction access"),
  });
  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.renamePrivateAgentRun(apiContext, id, title, id === selectedRunId ? displayToken : ""),
    onSuccess: async () => { setRenameTarget(null); await Promise.all([queryClient.invalidateQueries({ queryKey: ["agent-private-runs", apiContext, agentId] }), queryClient.invalidateQueries({ queryKey: ["private-agent-run"] })]); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deletePrivateAgentRun(apiContext, id, id === selectedRunId ? displayToken : ""),
    onError: (error) => toast.error(errorMessage(error) || "Unable to remove Run"),
    onSuccess: async (_, id) => { setDeleteTarget(null); if (id === selectedRunId) navigate(`/agents/${agentId}/private-display`, { replace: true, state: location.state }); await queryClient.invalidateQueries({ queryKey: ["agent-private-runs", apiContext, agentId] }); },
  });

  const running = Boolean(run.data && ["starting", "running", "input_required"].includes(run.data.status));
  const plan = useMemo(() => derivePlan(streamEvents), [streamEvents]);
  const pendingMessage = optimisticMessage || (adoptingCreatedRun && !run.data ? createdConversation.content : "");
  const runMessages = useMemo(() => mergeRunMessages(run.data?.messages ?? [], streamEvents, pendingMessage, optimisticReply, optimisticBase.current), [run.data?.messages, streamEvents, pendingMessage, optimisticReply]);
  useEffect(() => {
    // Keep accepted input visible until the authoritative message arrives. An
    // answered question changes the draft slot, not the identity of its reply.
    const received = run.data?.messages.slice(optimisticBase.current) || [];
    if (optimisticMessage && received.some(item => item.role === "user" && item.content === optimisticMessage)) setOptimisticMessage("");
    if (optimisticReply && received.some(item => item.role === "user" && item.content === optimisticReply)) setOptimisticReply("");
  }, [run.data?.messages, optimisticMessage, optimisticReply]);

  function selectRun(id: string) { setRunSelection({ id, agent: agentId }); const next = new URLSearchParams(searchParams); next.set("run", id); navigate(`/agents/${agentId}/private-display?${next}`, { replace: true, state: location.state }); }
  function newRun() {
    if (running) {
      toast.info("Cancel the active Run or answer its request before starting a new Run.");
      return;
    }
    if (!selectedRunId) clearSubmittedAssets(assetDraft.capture());
    navigate(`/agents/${agentId}/private-display`, { replace: true, state: location.state });
    setSelectedTool(privateDisplayToolName);
    setOptimisticMessage("");
    setOptimisticReply("");
    setImageAttachments([]);
    setFileIds([]);
    setUploadedFiles([]);
    setComputerFiles([]);
    setAudioIds([]);
    setUploadingImage(false);
    setUploadingFiles(false);
    setFileEpoch(value => value + 1);
    setShowProfiles(false);
    setSlashHelpOpen(false);
    setWorkspaceMode("chat");
  }
  function switchProject(nextProjectId: string) {
    if ((apiContext.projectId || "") === nextProjectId || !agentId) return;
    setProjectId(nextProjectId || null);
    navigate(`/agents/${agentId}/private-display`, { replace: true, state: location.state });
    setSelectedTool("");
    setOptimisticMessage("");
    setOptimisticReply("");
    setImageAttachments([]);
    setFileIds([]);
    setUploadedFiles([]);
    setComputerFiles([]);
    setAudioIds([]);
    setUploadingImage(false);
    setUploadingFiles(false);
    setFileEpoch(value => value + 1);
    setWorkspaceMode("chat");
    setMobilePanel("history");
  }
  function chooseSlashTool(toolName: string) {
    if (selectedRunId) navigate(`/agents/${agentId}/private-display`, { replace: true, state: location.state });
    setSelectedTool(toolName);
    assetDraft.choose(toolName, { profileId: "", reasoningEffort: "" });
    assetDraft.restore();
    setImageAttachments([]); setFileIds([]); setUploadedFiles([]); setComputerFiles([]); setAudioIds([]); setUploadingImage(false); setUploadingFiles(false); setMediaBusy(false); setFileEpoch(value => value + 1);
  }
  function leavePrivateDisplay() {
    navigate(privateDisplayReturnTo(agentId, location.state, agentDisplayNavigation));
  }
  function manageDevice(device: "computer" | "mobile") {
    if (device === "computer" && selectedRunId) {
      if (running) { toast.info("Stop this Run and wait for completion before changing its Computer."); return; }
      setComputerDialogOpen(true);
      return;
    }
    navigate(privateDisplayAttachmentUrl(agentId, location.state, device, agentDisplayNavigation));
  }
  function submit(value: string) {
    if (!pendingInteraction && (uploadingImage || uploadingFiles || mediaBusy || restoredAssets.blocked || executionInvalid)) return;
    const slash = value.trim().match(/^\/([a-z0-9-]+)$/i);
    if (slash) {
      const command = slash[1].toLowerCase();
      if ((PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS as readonly string[]).includes(command)) {
        runBuiltInCommand(command);
        setMessage("");
        return;
      }
      const tool = composerTools.find(item => item.slash_command === command && !(PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS as readonly string[]).includes(command));
      if (tool && !running && !pendingInteraction) {
        if (!tool.availability.can_invoke) {
          toast.error(tool.availability.message || `/${command} is currently unavailable.`);
          return;
        }
        chooseSlashTool(tool.name);
        setMessage("");
        toast.info(`/${command} selected. Add the task details, then send.`);
        return;
      }
      toast.error(`Unknown command /${command}`);
      return;
    }
    const recovered = restoredAssets.rows.filter(row => row.ready).map(row => row.asset);
    const attachments = activeTool?.input_modalities?.includes("image") ? [...new Set([...imageAttachments.map(item => item.asset_id), ...recovered.filter(item => (item.kind === "image" || item.kind === "run_image")).map(item => item.id)])].map(asset_id => ({ asset_id })) : [];
    const files = activeTool?.accepts_files ? [...new Set([...fileIds, ...computerFiles.map(file => file.file_id), ...recovered.filter(item => item.kind === "upload" || item.kind === "computer" || item.kind === "run_file").map(item => item.id)])] : [];
    const audio = [...new Set([...audioIds, ...recovered.filter(item => item.kind === "audio").map(item => item.id)])];
    if (attachments.length > 4) { toast.error("Attach at most four images per message."); return; }
    if (files.length + audio.length > 8) {
      toast.error("A Run can include at most eight files and recordings.");
      return;
    }
    const selectedExecution = activeTool?.execution_profiles?.length ? {
      execution_profile_id: execution.profileId,
      reasoning_effort: execution.reasoningEffort,
    } : {};
    if (pendingInteraction) answer.mutate({ interaction: pendingInteraction, value });
    else if (selectedRunId && run.data && !running && activeTool?.name === run.data.tool_name) resumeRun.mutate({ content: value, attachments, files, audio, ...selectedExecution });
    else createRun.mutate({ content: value, attachments, files, audio, ...selectedExecution });
  }

  function runBuiltInCommand(command: string) {
    if (command === "new") newRun();
    else if (command === "cancel") {
      if (!selectedRunId || !running) toast.info("There is no active Run to cancel.");
      else if (!cancel.isPending) cancel.mutate();
    }
    else if (command === "model") {
      if (running) toast.info("The execution profile is fixed for the active Run. Choose it before the next Run.");
      else if (!activeTool?.execution_profiles?.length) toast.info("This Agent does not expose execution profiles.");
      else setShowProfiles(true);
    }
    else if (command === "help") setSlashHelpOpen(true);
  }
  function handleRunSlashCommand(command: string) {
    if (!(PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS as readonly string[]).includes(command)) return false;
    runBuiltInCommand(command);
    return true;
  }
  const projectContextSetupRequired = Boolean(interactor.data?.project_context.requires_authorization);
  const ready = Boolean(agentId && !interactor.isError && !run.isError && !token.isError && privateDisplayAvailable && activeTool?.availability.can_invoke && interactor.data?.computer.ready && interactor.data?.mobile.ready && !projectContextSetupRequired);
  const unavailableToolMessage = interactor.data?.tools.find((tool) => !tool.availability.can_invoke)?.availability.message || "";
  const blockedMessage = !privateDisplayAvailable ? interactor.data?.private_display?.message || unavailableToolMessage || "This Agent does not support Private Display." : !activeTool?.availability.can_invoke ? activeTool?.availability.message || interactor.data?.private_display?.message || "This Agent is temporarily unavailable." : projectContextSetupRequired ? `Review and allow ${interactor.data?.project_context.project_name || "Project"} instructions before starting this external Agent.` : !interactor.data?.computer.ready ? interactor.data?.computer.message || "Connect the required Computer." : !interactor.data?.mobile.ready ? interactor.data?.mobile.message || "Connect the required Mobile device." : "";
  const runUnavailable = Boolean(selectedRunId && [token.error, run.error].some(error => error instanceof ApiError && [401, 403, 404].includes(error.status)));
  const interactorAccessDenied = interactor.error instanceof ApiError && [401, 403, 404].includes(interactor.error.status);
  const cloudUnavailable = !runUnavailable && !interactorAccessDenied && (token.isError || run.isError || interactor.isError);
  const checkingStatus = token.isFetching || run.isFetching || interactor.isFetching;
  async function checkStatus() {
    // Refresh observation only: never resume a task or replay an exchange token.
    if (token.isError && selectedRunId) await token.refetch();
    await Promise.all([selectedRunId && displayToken ? run.refetch() : Promise.resolve(), interactor.refetch()]);
  }
  const recoveredFollowUp = restoredAssets.rows.filter(row => row.ready).map(row => row.asset);
  const followUpAttachments: FollowUpAttachmentControl = {
    value: {
      attachments: [...new Set([...imageAttachments.map(item => item.asset_id), ...recoveredFollowUp.filter(item => (item.kind === "image" || item.kind === "run_image")).map(item => item.id)])].map(asset_id => ({ asset_id })),
      files: [...new Set([...fileIds, ...computerFiles.map(item => item.file_id), ...recoveredFollowUp.filter(item => item.kind === "upload" || item.kind === "computer" || item.kind === "run_file").map(item => item.id)])],
    },
    blocked: uploadingImage || uploadingFiles || mediaBusy ? "Wait for attachments to finish uploading, or remove them."
      : restoredAssets.blocked ? "Check or remove saved attachments before sending this message."
      : audioIds.length || recoveredFollowUp.some(item => item.kind === "audio") ? "Recordings cannot be sent while the Agent is working. Keep them for a later turn or remove them." : "",
    onDelivered: input => {
      const ids = new Set([...(input.attachments || []).map(item => item.asset_id), ...(input.files || [])]);
      if (!ids.size) return;
      const ticket = assetDraft.capture();
      clearSubmittedAssets({ key: conversationKey, assets: ticket.assets.filter(item => ids.has(item.id)) });
      if (currentAttachmentConversation.current !== conversationKey) return;
      setImageAttachments([]); setFileIds([]); setUploadedFiles([]); setComputerFiles([]); setAudioIds([]);
      setUploadingImage(false); setUploadingFiles(false); setMediaBusy(false);
      assetDraft.restore(); setFileEpoch(value => value + 1);
    },
  };
  const attachmentDisabled = (running && run.data?.follow_up?.attachment_protocol?.queue !== 1) || Boolean(pendingInteraction) || createRun.isPending || resumeRun.isPending || !ready;
  async function referenceFile(file: RunFile): Promise<boolean> {
    if (!fileReferenceHandler.current?.("", true)) return false;
    const image = file.key.startsWith("image:");
    if (image ? !activeTool?.input_modalities?.includes("image") : !activeTool?.accepts_files) {
      toast.error(image ? "This tool does not accept images." : "This tool does not accept file attachments.");
      return false;
    }
    const current = readComposerDraft(conversationKey);
    const count = current.assets.filter(asset => image ? ["image", "run_image"].includes(asset.kind) : !["image", "run_image"].includes(asset.kind)).length;
    if (count >= (image ? 4 : 8)) { toast.error("Remove an attachment before adding another reference."); return false; }
    const scope = conversationKey;
    try {
      const asset = await api.runFileReference(apiContext, selectedRunId, displayToken, image ? "image" : file.kind, file.id);
      if (currentAttachmentConversation.current !== scope || !fileReferenceHandler.current?.("", true)) return false;
      if (!fileReferenceHandler.current?.(fileReference(file))) return false;
      const draft = readComposerDraft(scope);
      writeComposerDraft(scope, { ...draft, assets: [...draft.assets.filter(row => row.kind !== asset.kind || row.id !== asset.id), { ...asset, name: file.name }] });
      assetDraft.restore();
      setMobilePanel("live");
      return true;
    } catch (error) { if (currentAttachmentConversation.current === scope) toast.error(errorMessage(error) || "File reference unavailable. Your draft has not changed."); return false; }
  }
  const attachmentControls = (toolbarTarget: HTMLDivElement | null, attachmentActionsLocked: boolean) => <div className="min-w-0">
    <RestoredComposerAttachments state={restoredAssets} onRemove={assetDraft.remove} disabled={createRun.isPending || resumeRun.isPending} />
    {!assetDraft.persisted ? <p role="status" className="text-xs text-amber-800">Attachment references are kept only on this page because browser storage is unavailable.</p> : null}
    {executionInvalid ? <p role="alert" className="text-sm text-amber-800">Saved execution settings are no longer available. <button type="button" className="min-h-11 underline" onClick={() => setExecution({ profileId: "", reasoningEffort: "" })}>Use publisher default</button></p> : null}
    <PrivateDisplayComposerTools key={`composer-tools:${conversationKey}:${fileEpoch}`} toolbarTarget={toolbarTarget} attachmentActionsLocked={attachmentActionsLocked} onChooseAttachments={() => attachmentIntake.current?.choose()} attachmentDisabled={attachmentDisabled || attachmentActionsLocked} agentId={agentId} value={running && !pendingInteraction ? "" : message} onValueChange={setMessage} tools={composerTools} activeTool={activeTool} disabled={attachmentActionsLocked || running || Boolean(pendingInteraction) || createRun.isPending || resumeRun.isPending || cancel.isPending || !ready || mediaBusy} running={running} usage={run.data?.usage} uploadedFiles={uploadedFiles} onImportedFilesChange={setComputerFiles} onAudioChange={setAudioIds} onDraftComputerFiles={assetDraft.computer} onDraftAudioFiles={assetDraft.audio} onMediaBusy={setMediaBusy} execution={execution} onExecutionChange={setExecution} onBuiltIn={runBuiltInCommand} onToolChange={chooseSlashTool} commandMode="full" cancelPending={cancel.isPending} showProfiles={showProfiles} onShowProfilesChange={setShowProfiles} selectionLabel={run.data ? "Next turn" : "New Run"} />
    <RunAttachmentPicker key={`${conversationKey}:${fileEpoch}`} ref={attachmentIntake}
      disabled={attachmentDisabled}
      images={activeTool?.input_modalities?.includes("image") ? { value: imageAttachments, onChange: updateImages, onBusyChange: setUploadingImage, reserved: restoredAssets.rows.filter(row => row.asset.kind === "image").length, disabled: attachmentDisabled } : undefined}
      files={activeTool?.accepts_files ? { agentId, onChange: setFileIds, onTransfersChange: setUploadedFiles, onDraftTransfersChange: assetDraft.uploads, onBusyChange: setUploadingFiles, reserved: computerFiles.length + audioIds.length + restoredAssets.rows.filter(row => row.asset.kind !== "image").length, disabled: attachmentDisabled } : undefined} />
  </div>;

  if (!agentId && !selectedRunId) return <PrivateDisplayState />;

  return (
    <AgentDisplaySurface standalone>
      <AgentDisplayHeaderFrame>
        <div className="flex min-h-[58px] items-center gap-3 px-3 py-2 xl:px-5">
          <button type="button" onClick={leavePrivateDisplay} className="inline-flex h-10 w-10 items-center justify-center rounded-md text-[#535350] hover:bg-black/5" aria-label="Return to previous page"><ArrowLeft size={18} /></button>
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[#1a1a19] text-[#bdfc73]"><LockKeyhole size={16} /></div>
          <div className="min-w-0 flex-1"><h1 className="truncate text-[15px] font-semibold text-[#1a1a19]">{run.data?.display_title || run.data?.title || run.data?.agent_name || interactor.data?.agent.name || "Private Display"}</h1><div className="flex min-w-0 items-center gap-1 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]"><span className="truncate">{run.data ? `Run · Turn ${run.data.turn_index || 1} · ${run.data.status.replaceAll("_", " ")}` : "New Run"}</span>{run.data ? <><span aria-hidden="true">·</span><button type="button" onClick={() => setRunDetailsOpen(true)} className="min-h-6 max-w-[45%] truncate rounded px-1 text-left font-sans font-semibold normal-case tracking-normal text-[#535350] hover:bg-black/5 hover:text-[#1a1a19]" title={`Open Run details · ${run.data.project_context.project_name || "Organization shared"}`}>{run.data.project_context.project_name || "Organization shared"}</button></> : null}</div></div>
          {run.data ? <div className="hidden border-l border-black/10 pl-3 text-right text-[11px] leading-4 text-[#535350] lg:block" title="Resolved execution snapshot for the latest turn"><div className="font-mono text-[9px] uppercase tracking-[0.08em] text-[#858481]">Current Run</div><div className="font-semibold text-[#34322d]">{run.data.execution?.model || "Managed by Agent"}{run.data.execution?.reasoning_effort ? ` · ${run.data.execution.reasoning_effort}` : ""}</div></div> : null}
          <RunPresentationSummary run={run.data} />
          {running ? <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending || run.data?.execution_task?.status === "cancel_requested"} className="inline-flex min-h-11 items-center gap-2 rounded-md border border-black/10 px-3 text-sm text-[#535350]"><X size={14} /> {run.data?.execution_task?.status === "cancel_requested" ? "Stopping · awaiting Agent" : "Cancel"}</button> : null}
        </div>
        {run.data?.execution_task?.execution_state === "queued" ? <p role="status" className="px-4 pb-2 text-sm">Queued · waiting for an Agent worker.</p> : null}
        {streamEvents.length === 1000 || historyCursor !== null ? <div className="flex flex-wrap items-center gap-3 px-4 pb-2 text-sm"><span role="status">{historyCursor === null ? "Live view: latest 1,000 events." : "Event history · live updates paused."}</span><button className="min-h-11 underline" disabled={!streamEvents[0] || streamEvents[0].seq <= 1} onClick={() => setHistoryCursor(Math.max(0, (streamEvents[0]?.seq ?? 1) - 101))}>Earlier events</button>{historyCursor !== null ? <button className="min-h-11 underline" onClick={() => { eventCursor.current = Math.max(0, eventCursor.current - 1000); setStreamEvents([]); setHistoryCursor(null); }}>Return to live</button> : null}</div> : null}
        {run.data?.execution_task?.error_code === "CANCEL_UNCONFIRMED" ? <p role="alert" className="px-4 pb-2 text-sm">Cancellation was not acknowledged. Nexus access is revoked, but external work may still be running.</p> : null}
        {run.data?.execution_task?.error_code === "WORKER_LOST_OUTCOME_UNKNOWN" ? <p role="alert" className="px-4 pb-2 text-sm">The worker disconnected. The external outcome is unknown; this call was not automatically replayed.</p> : null}
        {cloudUnavailable ? <div role="alert" className="mx-3 mb-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"><span>Cloud connection unavailable. Display updates could not be loaded; the Agent may still be running. Loaded history is preserved.</span><button type="button" className="min-h-11 rounded-md border border-black/10 bg-white px-3" disabled={checkingStatus} onClick={() => void checkStatus()}>{checkingStatus ? "Checking" : "Reconnect display"}</button></div> : null}
        {interactorAccessDenied && !runUnavailable ? <div role="alert" className="mx-3 mb-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm"><span>Agent access unavailable. Check your sign-in and Organization/Project access before continuing.</span><button type="button" className="min-h-11 underline" onClick={leavePrivateDisplay}>Return to entry</button></div> : null}
        {quotaIssue?.key === conversationKey ? <RunCapacityRecovery key={conversationKey} context={apiContext} agentId={agentId} runId={selectedRunId} message={quotaIssue.message} onChanged={() => { void queryClient.invalidateQueries({ queryKey: ["agent-private-runs"] }); void queryClient.invalidateQueries({ queryKey: ["private-agent-run"] }); }} /> : null}
      </AgentDisplayHeaderFrame>
      {run.data ? <AgentComputerAttachDialog open={computerDialogOpen} agentId={agentId} agentName={run.data.agent_name}
        apiContext={apiContext} declaredScopes={interactor.data?.computer.declared_scopes ?? []} returnTo={location.pathname + location.search}
        targetRun={{ id: selectedRunId, displayToken, revision: computerRevision, name: run.data.computer.name || "Current Computer" }}
        onClose={() => setComputerDialogOpen(false)} onAttached={() => setComputerDialogOpen(false)} /> : null}
      <AgentDisplayMobileTabs panels={MOBILE_PANELS.map(panel => ({ ...panel, label: panel.id === "live" && liveUnread ? `Live · ${liveUnread} new` : panel.id === "history" && historyCounts && (historyCounts.input_required + historyCounts.completed_unread) > 0 ? `Runs · ${historyCounts.input_required + historyCounts.completed_unread}` : panel.label, attention: panel.id === "live" ? liveUnread > 0 : panel.id === "history" && Boolean(historyCounts && (historyCounts.input_required + historyCounts.completed_unread) > 0) }))} active={mobilePanel} onChange={(value) => setMobilePanel(value as MobilePanel)} />
      <AgentDisplayWorkspace
        activeMobilePanel={mobilePanel === "history" ? "plan" : mobilePanel === "context" ? "files" : "live"}
        plan={<PrivateAgentRunRail projects={projects} projectId={apiContext.projectId || ""} onProjectChange={switchProject} projectChangeDisabled={createRun.isPending || resumeRun.isPending || answer.isPending} runs={historyRuns} filter={historyFilter} counts={historyCounts} onFilterChange={setHistoryFilter} hasMore={runs.hasNextPage} loadingMore={runs.isFetchingNextPage} onLoadMore={() => void runs.fetchNextPage()} error={runs.isError} onRetry={() => void runs.refetch()} activeRunId={selectedRunId} loading={runs.isLoading} deletingId={remove.variables || ""} query={search} onQueryChange={setSearch} onNew={newRun} onSelect={selectRun} onRename={(item) => { setRenameTarget(item); setRenameValue(item.title); }} onDelete={setDeleteTarget} />}
        live={runUnavailable ? <div role="alert" className="flex h-full items-center justify-center rounded-md border border-black/10 bg-white"><EmptyWorkspace icon={LockKeyhole} title="Run unavailable" description="This Run does not exist or belongs to another caller." /></div> : <RunLivePanel fileReferenceHandler={fileReferenceHandler} key={liveConversationKey} onCompletionViewed={onCompletionViewed} readError={markRead.isError && markRead.variables?.id === selectedRunId && markRead.variables?.completedAt === run.data?.completed_at} onRetryRead={() => markRead.variables && markRead.mutate({ ...markRead.variables, token: displayToken })} visible={desktop || mobilePanel === "live"} onUnreadChange={setLiveUnread} draftSaved={draft.saved} onCheckStatus={() => void checkStatus()} onRecoveryAction={(action) => recoveryDecision.mutate(action)} checkingStatus={checkingStatus || recoveryDecision.isPending} run={run.data} loading={Boolean(selectedRunId && (token.isLoading || run.isLoading))} mode={workspaceMode} onModeChange={setWorkspaceMode} events={streamEvents} terminalEvents={terminalEvents} terminalInfo={terminalInfo} terminalConnection={terminalConnection} messages={runMessages} interaction={pendingInteraction} value={message} onValueChange={setMessage} onSubmit={submit} onSlashCommand={handleRunSlashCommand} submitting={createRun.isPending || resumeRun.isPending || answer.isPending || cancel.isPending} ready={ready && (Boolean(pendingInteraction) || (!uploadingImage && !uploadingFiles && !mediaBusy && !restoredAssets.blocked && !executionInvalid))} displayToken={displayToken} attachmentControls={attachmentControls} followUpAttachments={followUpAttachments} allowAttachmentOnly={Boolean(imageAttachments.length || fileIds.length || computerFiles.length || audioIds.length || restoredAssets.rows.some(row => row.ready))} onAttachFiles={files => attachmentIntake.current?.add(files)} blockedMessage={blockedMessage} running={running} activeTool={activeTool} onNewRun={newRun} computerSummary={interactor.data?.computer} onManageComputer={() => manageDevice("computer")} mobileSummary={interactor.data?.mobile} onManageMobile={() => manageDevice("mobile")} projectContextSetupRequired={projectContextSetupRequired} onReviewProjectContext={() => setProjectConsentOpen(true)} />}
        context={<RunContextPanel key={contextKey} contextKey={contextKey} events={streamEvents} run={run.data} displayToken={displayToken} active={contextPanel} onChange={setContextPanel} plan={plan} onReference={referenceFile} computerRequested={interactor.data?.computer.requirement !== "disabled"} />}
      />
      <NexilumeDialog open={runDetailsOpen} onClose={() => setRunDetailsOpen(false)} title="Run details" eyebrow="Run context" description="Identity, Project snapshot and attached devices for this Run.">
        {run.data ? <div className="grid gap-4">
          <div className="grid gap-3"><ContextFact label="Run ID" value={run.data.id} /><ContextFact label="Agent" value={run.data.agent_name} /><ContextFact label="Project scope" value={run.data.project_context.project_name || "Organization shared"} /><ContextFact label="Snapshot revision" value={run.data.project_context.project_id ? String(run.data.project_context.revision) : "No Project snapshot"} /><ContextFact label="Captured" value={run.data.project_context.captured_at ? formatDateTime(run.data.project_context.captured_at) : "Not captured"} /></div>
          <p className="text-sm leading-6 text-[#535350]">Later Project edits do not change this Run.</p>
          {run.data.project_context.instructions_markdown ? <section aria-labelledby="run-project-instructions"><h3 id="run-project-instructions" className="mb-2 text-sm font-semibold text-[#34322d]">Project instructions snapshot</h3><div className="max-h-[38vh] overflow-y-auto border-y border-black/10 bg-[#fbfbfa] px-4 py-3"><ContentMarkdown value={run.data.project_context.instructions_markdown} /></div></section> : <p className="text-sm text-[#858481]">This Run has no Project instructions snapshot.</p>}
          <section aria-labelledby="run-device-context"><h3 id="run-device-context" className="mb-2 text-sm font-semibold text-[#34322d]">Attached devices</h3><div className="grid gap-4 sm:grid-cols-2">
            <div><div className="mb-2 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Computer</div><ContextFact label="Binding" value={run.data.computer.attached ? run.data.computer.name || "Attached" : "Not attached"} /><ContextFact label="Status" value={run.data.computer.online === true ? "Online" : run.data.computer.online === false ? "Offline" : "Not reported"} /><ContextFact label="Platform" value={run.data.computer.platform || "Not reported"} /><ContextFact label="Workspace" value={run.data.computer.attached ? formatWorkspacePath(run.data.computer.workspace_cwd) : "Not available"} /><ContextFact label="Browser" value={run.data.computer.browser_available === true ? "Available" : run.data.computer.browser_available === false ? "Unavailable" : "Not reported"} /></div>
            <div><div className="mb-2 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Mobile</div><ContextFact label="Binding" value={run.data.mobile.attached ? run.data.mobile.name || "Attached" : "Not attached"} /><ContextFact label="Status" value={run.data.mobile.status ? run.data.mobile.status.replaceAll("_", " ") : "Not reported"} /><ContextFact label="Capabilities" value={run.data.mobile.capabilities.length ? run.data.mobile.capabilities.map(scope => scope.replace("mobile.", "")).join(", ") : "None"} /><ContextFact label="Commands" value={String(run.data.mobile.commands.length)} /></div>
          </div></section>
          {interactor.data?.project_context.external_agent && interactor.data.project_context.authorized && interactor.data.project_context.available ? <button type="button" onClick={() => revokeProjectContext.mutate()} disabled={revokeProjectContext.isPending} className="min-h-11 justify-self-start rounded-md border border-black/10 px-3 text-sm font-semibold text-[#535350] hover:bg-black/5">Stop sharing with this Agent for future Turns</button> : null}
        </div> : <p className="text-sm text-[#858481]">No Run selected.</p>}
      </NexilumeDialog>
      <NexilumeDialog open={slashHelpOpen} onClose={() => setSlashHelpOpen(false)} title="Private Display commands" eyebrow="Composer shortcuts" description="Commands act on this private Run and are never sent to the Agent as prompt text." footer={<button type="button" className="btn btn-primary" onClick={() => setSlashHelpOpen(false)}>Close</button>}>
        <div className="grid gap-4">
          <div className="divide-y divide-black/10 border-y border-black/10">
            <CommandHelpRow command="/new" description={running ? "Available after the active Run is completed or cancelled." : "Clear the draft and attachments, then prepare an independent Run."} />
            <CommandHelpRow command="/cancel" description={running ? "Request cancellation of the active Run." : "Available only while a Run can be cancelled."} />
            {activeTool?.execution_profiles?.length ? <CommandHelpRow command="/model" description="Open and focus the execution profile selector." /> : null}
            <CommandHelpRow command="/help" description="Open this command reference." />
          </div>
          {composerTools.some(tool => tool.slash_command && !(PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS as readonly string[]).includes(tool.slash_command)) ? <div>
            <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Agent commands</div>
            <div className="divide-y divide-black/10 border-y border-black/10">
              {composerTools.filter(tool => tool.slash_command && !(PRIVATE_DISPLAY_BUILT_IN_SLASH_COMMANDS as readonly string[]).includes(tool.slash_command)).map(tool => <CommandHelpRow key={tool.name} command={`/${tool.slash_command}`} description={tool.availability.can_invoke ? tool.slash_description || tool.description : tool.availability.message || "Currently unavailable."} />)}
            </div>
          </div> : null}
          <p className="text-sm leading-6 text-[#6f6e69]">Type a command by itself. Use <span className="font-mono text-xs">@</span> to attach an uploaded or Computer file.</p>
        </div>
      </NexilumeDialog>

      <NexilumeDialog open={Boolean(renameTarget)} onClose={() => setRenameTarget(null)} title="Rename run" description="Choose a private title visible only in your Run History.">
        <form onSubmit={(event) => { event.preventDefault(); if (renameTarget && renameValue.trim()) rename.mutate({ id: renameTarget.id, title: renameValue.trim() }); }} className="grid gap-4">
          <input autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} maxLength={80} className="min-h-11 rounded-md border border-black/15 px-3 text-sm outline-none focus:border-[#6fa43f]" />
          <div className="flex justify-end gap-2"><button type="button" onClick={() => setRenameTarget(null)} className="min-h-10 rounded-md border border-black/10 px-4 text-sm">Cancel</button><button type="submit" disabled={!renameValue.trim() || rename.isPending} className="min-h-10 rounded-md bg-[#1a1a19] px-4 text-sm font-semibold text-white">Save title</button></div>
        </form>
      </NexilumeDialog>
      <NexilumeDialog open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)} title="Remove run from history" description={historyRemovalDescription}>
        {deleteTarget && ["running", "starting", "input_required"].includes(deleteTarget.status) ? <>
          <p className="mb-3 text-sm text-[#535350]">This Run is still active. Stop it before removing it from history; hiding it would not release capacity.</p>
          <button type="button" onClick={() => { selectRun(deleteTarget.id); setDeleteTarget(null); }} className="min-h-11 rounded-md border border-black/15 px-4 text-sm">Open Run to stop</button>
        </> : <div className="flex justify-end gap-2"><button type="button" onClick={() => setDeleteTarget(null)} className="min-h-11 rounded-md border border-black/10 px-4 text-sm">Cancel</button><button type="button" onClick={() => deleteTarget && remove.mutate(deleteTarget.id)} disabled={remove.isPending} className="min-h-11 rounded-md bg-[#9b2c2c] px-4 text-sm font-semibold text-white">Remove run</button></div>}
      </NexilumeDialog>
      <NexilumeDialog open={projectConsentOpen} onClose={() => setProjectConsentOpen(false)} title="Share Project instructions" description={`Allow this external Agent to read revision ${interactor.data?.project_context.revision || 0} of ${interactor.data?.project_context.project_name || "the selected Project"}.`}>
        <div className="max-h-[50vh] overflow-y-auto border-y border-black/10 bg-[#fbfbfa] px-4 py-3">
          <ContentMarkdown value={interactor.data?.project_context.instructions_markdown || ""} empty="This Project has no instructions." />
        </div>
        <p className="mt-3 text-xs leading-5 text-[#6f6e69]">The exact revision is snapshotted into each Run. It is not exposed in developer Observability and Nexus does not automatically make it a model system prompt.</p>
        <div className="mt-4 flex justify-end gap-2"><button type="button" onClick={() => setProjectConsentOpen(false)} className="min-h-11 rounded-md border border-black/10 px-4 text-sm">Not now</button><button type="button" onClick={() => authorizeProjectContext.mutate()} disabled={authorizeProjectContext.isPending} className="min-h-11 rounded-md bg-[#1a1a19] px-4 text-sm font-semibold text-white">{authorizeProjectContext.isPending ? "Allowing" : "Allow instructions"}</button></div>
      </NexilumeDialog>
    </AgentDisplaySurface>
  );
}

function RunLivePanel({ fileReferenceHandler, onCompletionViewed, readError, onRetryRead, visible, onUnreadChange, draftSaved, onCheckStatus, onRecoveryAction, checkingStatus, run, loading, mode, onModeChange, events, terminalEvents, terminalInfo, terminalConnection, messages, interaction, value, onValueChange, onSubmit, onSlashCommand, submitting, ready, blockedMessage, running, activeTool, onNewRun, computerSummary, onManageComputer, mobileSummary, onManageMobile, projectContextSetupRequired, onReviewProjectContext, displayToken, attachmentControls, allowAttachmentOnly, onAttachFiles, followUpAttachments }: {
  fileReferenceHandler: React.RefObject<((text: string, checkOnly?: boolean) => boolean) | null>;
  onCompletionViewed: (runId: string, completedAt: string) => void; readError: boolean; onRetryRead: () => void;
  visible: boolean; onUnreadChange: (count: number) => void;
  draftSaved: boolean; onCheckStatus: () => void; onRecoveryAction: (action: "check_status" | "retry" | "cancel") => void; checkingStatus: boolean;
  displayToken: string;
  attachmentControls?: (toolbar: HTMLDivElement | null, locked: boolean) => ReactNode;
  followUpAttachments: FollowUpAttachmentControl;
  allowAttachmentOnly: boolean;
  onAttachFiles: (files: File[]) => void;
  run?: PrivateAgentRunDisplay; loading: boolean; mode: WorkspaceMode; onModeChange: (value: WorkspaceMode) => void; events: DisplayStreamEvent[];
  terminalEvents: Array<{ seq: number; kind: string; command_id: string; data: string; exit_code: number | null; created_at: string }>;
  terminalInfo: AgentRunTerminal | null;
  terminalConnection: PrivateAgentTerminalConnection;
  messages: AgentRunMessage[]; interaction: AgentRunInteraction | null; value: string; onValueChange: (value: string) => void; onSubmit: (value: string) => void; onSlashCommand: (command: string) => boolean; submitting: boolean; ready: boolean; blockedMessage: string; running: boolean; activeTool: AgentInteractionTool | null; onNewRun: () => void; computerSummary?: { requirement: string; attached: boolean; ready: boolean; device_name?: string; platform: string; browser_available: boolean | null; browser_name: string }; onManageComputer: () => void; mobileSummary?: { requirement: string; attached: boolean; ready: boolean; device_name: string; device_status: string }; onManageMobile: () => void;
  projectContextSetupRequired: boolean; onReviewProjectContext: () => void;
}) {
  const { apiContext } = useAuth();
  const chatScroll = useRunChatScroll(messages, visible && mode === "chat" && !loading);
  async function downloadMessageFile(block: Extract<AgentChatContentBlock, { type: "file" }>) {
    if (!run || !displayToken || !block.url.startsWith(`/api/v1/agent-runs/${run.id}/files/`)) {
      toast.error("This file reference is not available to the current Run."); return;
    }
    try {
      const value = await api.prepareAgentDownload(apiContext, block.url, displayToken);
      const anchor = document.createElement("a"); anchor.href = value.url; anchor.download = value.file_name;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
    } catch { toast.error("Download unavailable. Retry to renew access."); }
  }
  useEffect(() => { onUnreadChange(chatScroll.unread); }, [chatScroll.unread, onUnreadChange]);
  useEffect(() => {
    function seen() {
      if (visible && mode === "chat" && !loading && chatScroll.atLatest && document.visibilityState === "visible" && run?.status === "completed" && run.completion_unread && run.completed_at) onCompletionViewed(run.id, run.completed_at);
    }
    seen();
    document.addEventListener("visibilitychange", seen);
    return () => document.removeEventListener("visibilitychange", seen);
  }, [visible, mode, loading, chatScroll.atLatest, run?.id, run?.status, run?.completion_unread, run?.completed_at, onCompletionViewed]);
  const followUp = useRunFollowUps(run, onSlashCommand, followUpAttachments);
  const acceptingFollowUp = Boolean(!interaction && running && run?.execution_task?.status !== "cancel_requested" && run?.follow_up && run.follow_up.mode !== "none");
  const deviceEvents = useMemo(() => events.filter(event => event.seq > (run?.computer.event_cursor ?? 0)), [events, run?.computer.event_cursor]);
  const shell = useMemo(() => deriveAgentDisplayShell(deviceEvents), [deviceEvents]);
  const shellTranscript = useMemo(() => buildAgentDisplayShellTranscript(terminalEvents, shell), [terminalEvents, shell]);
  const latestShellSeq = terminalEvents.at(-1)?.seq ?? shell.at(-1)?.seq ?? 0;
  const [seenShellSeq, setSeenShellSeq] = useState(0);
  useEffect(() => {
    if (visible && mode === "shell") setSeenShellSeq(latestShellSeq);
  }, [latestShellSeq, mode, visible]);
  const frames = agentDisplayBrowserFrames(deviceEvents);
  const choices = interaction?.choices.length ? interaction.choices : interaction?.kind === "confirm" ? [{ value: "true", label: "Confirm" }, { value: "false", label: "Decline" }] : [];
  const failure = currentRunFailure(run);
  const executionState = run?.execution_task?.status || run?.execution_task?.execution_state || "";
  const recoveryMessage = executionState === "waiting_for_runtime"
    ? "Waiting for Agent. This Run and its completed operations are preserved."
    : executionState === "recovering"
      ? `Recovering the same Run and Turn${run?.execution_task?.recovery?.attempt ? ` · attempt ${run.execution_task.recovery.attempt}` : ""}.`
      : executionState === "recovery_required"
        ? "A recovery decision is required before this Turn can continue."
        : run?.execution_task && !run.execution_task.recovery?.managed
          ? "Limited recovery · upgrade and redeploy this Agent to enable platform-managed operation recovery."
          : "";
  const composerRegion = useRef<HTMLDivElement>(null);
  const canResume = Boolean(run?.execution_task?.continuable ?? run?.tool_policy.continuable);
  const quoteUnavailable = submitting ? "Wait for your message to send." : interaction ? "Answer the current question before quoting another message." : running && (!run?.follow_up || run.follow_up.mode === "none") ? "This Agent cannot receive messages while working." : run && !running && !canResume ? "Start a new Run to reply." : "";
  useEffect(() => {
    fileReferenceHandler.current = (text: string, checkOnly = false) => {
      if (quoteUnavailable || followUp.draft.pending) {
        toast.error(quoteUnavailable || "Confirm the previous submission before changing this draft."); return false;
      }
      if (checkOnly) return true;
      if (running) followUp.setText(followUp.text ? `${followUp.text}\n\n${text}\n\n` : `${text}\n\n`);
      else onValueChange(value ? `${value}\n\n${text}\n\n` : `${text}\n\n`);
      onModeChange("chat");
      requestAnimationFrame(() => composerRegion.current?.querySelector<HTMLTextAreaElement>('textarea:not(:disabled)')?.focus());
      return true;
    };
    return () => { fileReferenceHandler.current = null; };
  }, [fileReferenceHandler, quoteUnavailable, followUp.draft.pending, running, followUp.text, followUp.setText, value, onValueChange, onModeChange]);
  function quote(item: AgentRunMessage) {
    const text = chatMessageText(item.content_blocks, item.content);
    if (running && run) {
      followUp.setText(quotedReply(text, item.role, followUp.text));
    } else onValueChange(quotedReply(text, item.role, value));
    onModeChange("chat");
    requestAnimationFrame(() => composerRegion.current?.querySelector<HTMLTextAreaElement>('textarea:not(:disabled)')?.focus());
  }
  function messageActions(item: AgentRunMessage) {
    return <div className="mt-1 flex flex-wrap items-start gap-1">
      <ChatCopyButton text={chatMessageText(item.content_blocks, item.content)} label="Copy message" />
      <button type="button" aria-label="Quote reply" title={quoteUnavailable || "Quote this message in your draft"} disabled={Boolean(quoteUnavailable)} onClick={() => quote(item)} className="min-h-11 rounded-md px-2 text-xs text-[#535350] hover:bg-black/5 disabled:opacity-45 focus-visible:outline focus-visible:outline-2">Quote reply</button>
    </div>;
  }
  const disabled = running && !interaction;
  return (
    <AgentDisplayPanel>
      <div className="flex min-h-12 shrink-0 items-center justify-between gap-2 border-b border-black/10 px-3" aria-label="Run workspace toolbar"><AgentDisplayModeTabs touchTargets label="Run workspace modes" active={mode} onChange={(next) => onModeChange(next as WorkspaceMode)} modes={[{ id: "chat", label: "Chat", icon: MessageSquare, attention: Boolean(interaction) || chatScroll.unread > 0 }, { id: "shell", label: "Shell", icon: Terminal, attention: mode !== "shell" && latestShellSeq > seenShellSeq }, { id: "browser", label: "Browser", icon: Monitor }]} />{recoveryMessage ? <details className="relative shrink-0 text-xs"><summary className="min-h-11 cursor-pointer content-center rounded-md px-2 text-[#535350]">Recovery</summary><p role="status" className="absolute right-0 top-full z-20 w-64 max-w-[75vw] rounded-md border border-black/15 bg-white p-3 leading-5">{recoveryMessage}</p></details> : null}</div>
      {failure && run ? <RunFailureNotice key={`${run.id}:${failure.code}`} failure={failure} runId={run.id} ready={ready} canResume={canResume} checking={checkingStatus} onAction={action => {
        if (action === "check_status") onCheckStatus();
        else if (action === "retry_operation") onRecoveryAction("retry");
        else if (action === "cancel_turn") onRecoveryAction("cancel");
        else if (action === "manage_computer") onManageComputer();
        else if (action === "manage_mobile") onManageMobile();
        else if (action === "view_browser") onModeChange("browser");
        else if (action === "new_run") onNewRun();
        else if (action === "continue_chat") { onModeChange("chat"); requestAnimationFrame(() => composerRegion.current?.querySelector("textarea")?.focus()); }
      }} /> : null}
      {readError ? <p role="status" className="px-4 pt-2 text-xs text-amber-800">Read status could not sync. <button type="button" onClick={onRetryRead} className="min-h-11 underline">Retry read status</button></p> : null}
      <div className="relative min-h-0 flex-1">
        {chatScroll.unread > 0 ? <div className="pointer-events-none absolute inset-x-3 bottom-3 z-10 flex justify-center" aria-live="polite"><button type="button" className="pointer-events-auto min-h-11 rounded-full border border-black/15 bg-white px-4 text-sm shadow-sm" onClick={() => { chatScroll.toLatest(); onModeChange("chat"); }}>New messages ({chatScroll.unread}) ↓</button></div> : null}
        {loading ? <div role="status" className="pointer-events-none absolute right-6 top-5 z-10 flex items-center gap-2 rounded-md bg-white/95 px-2 py-1 text-xs text-[#858481]"><Loader2 className="animate-spin" size={16} /> Loading run</div> : null}
        {mode === "chat" ? <div ref={chatScroll.viewport} onScroll={chatScroll.onScroll} aria-label="Chat history" aria-busy={loading} style={{ overflowAnchor: "none", scrollbarGutter: "stable" }} className="h-full overflow-y-auto px-4 py-4"><div ref={chatScroll.content} className="mx-auto grid max-w-3xl gap-6">
          {!loading && !messages.length ? <EmptyWorkspace icon={Bot} title="What should this Agent do?" description="Your instruction starts a new caller-private Run. Follow-up questions from the Agent stay inside that Run." /> : null}
          {messages.map((item) => item.role === "user" ? <div key={item.id} data-message-id={item.id} className="ml-auto min-w-0 max-w-[80%] rounded-xl bg-[#ecece8] px-4 py-3 text-sm text-[#34322d]"><AgentChatMessageContent blocks={item.content_blocks} fallback={item.content} role="user" onDownloadFile={downloadMessageFile} />{messageActions(item)}</div> : <article key={item.id} data-message-id={item.id} className="grid grid-cols-[28px_minmax(0,1fr)] gap-3 text-sm text-[#34322d]"><div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#1a1a19] text-[#bdfc73]"><Bot size={14} /></div><div className="min-w-0 pt-0.5"><div className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[#858481]">Agent</div><AgentChatMessageContent blocks={item.content_blocks} fallback={item.content} role="assistant" />{messageActions(item)}</div></article>)}
          {submitting && !interaction ? <div className="flex items-center gap-3 text-sm text-[#858481]"><Loader2 className="animate-spin" size={15} /> {run ? "Continuing current run…" : "Starting independent run…"}</div> : null}
          <RunFollowUps controller={followUp} active={acceptingFollowUp}
            restoreDisabled={interaction ? "Answer the current question first." : loading || submitting ? "Wait for the current request to finish." : running ? "The Agent is still working." : !canResume ? "This tool cannot continue this Run." : ""}
            appendDraft={!interaction && Boolean(value.trim())}
            onRestoreDraft={text => {
              onValueChange(value.trim() ? `${value}\n\n${text}` : text);
              requestAnimationFrame(() => composerRegion.current?.querySelector<HTMLTextAreaElement>('textarea:not(:disabled)')?.focus());
            }} />
          {interaction ? <div role="group" aria-label="Agent question" className="rounded-md border border-black/15 bg-white p-3"><p className="text-sm font-medium">{interaction.prompt}</p>{choices.length ? <div className="mt-2 flex flex-wrap gap-2">{choices.map(choice => <button key={choice.value} type="button" disabled={submitting} onClick={() => onSubmit(choice.value)} className="min-h-11 rounded-md border border-black/20 px-3 text-sm">{choice.label}</button>)}</div> : null}</div> : null}
        </div></div> : null}
        {!loading && mode === "shell" ? <RunShellPanel runId={run?.id || ""} terminalEvents={terminalEvents} sdkLines={shell} terminal={terminalInfo} connection={terminalConnection} fallbackStatus={run?.computer.terminal_status || "not_started"} fallbackComputer={run?.computer.name || "Caller computer"} transcript={shellTranscript} /> : null}
        {!loading && mode === "browser" ? frames.length ? <BrowserFrameHistory frames={frames} runId={run?.id || ""} /> : <div className="h-full rounded-md border border-black/10 bg-[#f3f3f1]"><EmptyWorkspace icon={Monitor} title="No Browser frame" description="Frames explicitly published by the Agent will appear here." /></div> : null}
      </div>
      <div ref={composerRegion} onPaste={event => { const files = Array.from(event.clipboardData.files); if (files.length) { event.preventDefault(); if (!followUp.draft.pending) onAttachFiles(files); } }}
        onDragOver={event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); }}
        onDrop={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); if (!followUp.draft.pending) onAttachFiles(Array.from(event.dataTransfer.files)); } }} className="max-h-[55%] shrink-0 overflow-y-auto bg-white px-3 pb-3 pt-2" style={{ scrollbarGutter: "stable" }}>
        {!draftSaved ? <p role="status" className="mb-2 text-xs text-amber-800">Draft is kept in this page only. Browser storage is unavailable or the draft is too long; copy it before leaving.</p> : null}
        {projectContextSetupRequired && !interaction ? <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950"><span>{blockedMessage}</span><button type="button" className="btn min-h-10 bg-white" onClick={onReviewProjectContext}><BookOpenText size={15} /> Review instructions</button></div> : null}
        {interaction && mode !== "chat" ? <div role="group" aria-label="Agent question" className="mb-2 max-h-32 overflow-y-auto text-sm"><p>{interaction.prompt}</p>{choices.map(choice => <button key={choice.value} type="button" disabled={submitting} onClick={() => onSubmit(choice.value)} className="min-h-11 rounded-md border px-3">{choice.label}</button>)}</div> : null}
        {!interaction && run && !running && !canResume ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-black/10 bg-white px-4 py-3"><div><div className="text-sm font-semibold text-[#34322d]">This tool runs once per Run</div><div className="mt-1 text-xs text-[#858481]">It does not support follow-up turns. Start a new Run to invoke it again.</div></div><button type="button" onClick={onNewRun} className="min-h-10 rounded-md bg-[#1a1a19] px-4 text-sm font-semibold text-white">Start a new Run</button></div> : null}
        <AgentDisplayComposer
          deliveryActions={acceptingFollowUp ? <RunFollowUpMode controller={followUp} compact /> : undefined}
          deviceActions={<ComposerDeviceMenu computer={computerSummary} mobile={mobileSummary} onComputer={onManageComputer} onMobile={onManageMobile} />}
          attachments={toolbar => <fieldset disabled={Boolean(followUp.draft.pending)} className="min-w-0">{attachmentControls?.(toolbar, Boolean(followUp.draft.pending))}</fieldset>}
          attachmentActionsDisabled={Boolean(followUp.draft.pending)}
          value={acceptingFollowUp ? followUp.text : value}
          onChange={acceptingFollowUp ? followUp.setText : onValueChange}
          onSubmit={acceptingFollowUp ? followUp.submit : onSubmit}
          busy={acceptingFollowUp ? followUp.send.isPending : submitting}
          allowEmpty={!acceptingFollowUp && !interaction && allowAttachmentOnly}
          editable={interaction ? choices.length === 0 : Boolean(activeTool && (!run || canResume || acceptingFollowUp))}
          maxCharacters={acceptingFollowUp ? FOLLOW_UP_MAX_CHARACTERS : undefined}
          disabled={loading || (interaction ? choices.length > 0 : acceptingFollowUp ? Boolean(followUp.attachmentError) || !followUp.displayToken || Boolean(followUp.draft.pending) || followUp.check.isPending : disabled || !ready || Boolean(run && !canResume))}
          placeholder={interaction ? choices.length ? "Choose an answer in the conversation" : "Reply to this Run" : acceptingFollowUp ? followUp.selectedMode === "steer" ? "Add guidance for the current task" : "What should the Agent do next?" : running ? "Agent is working…" : run ? canResume ? `Continue this Run · Turn ${(run.turn_index || 1) + 1}` : "Start a new Run to use this tool again" : "Describe the task for this Agent"}
          error={acceptingFollowUp ? followUp.error : !interaction && !ready ? blockedMessage : undefined}
          label={interaction ? "Reply to current Run" : acceptingFollowUp ? "Follow-up message" : run ? canResume || running ? "Continue this Run" : "Run message" : "Start a new Agent Run"}
        />
      </div>
    </AgentDisplayPanel>
  );
}

function RunContextPanel({ contextKey, events, run, displayToken, active, onChange, plan, onReference, computerRequested }: { contextKey: string; events: DisplayStreamEvent[]; run?: PrivateAgentRunDisplay; displayToken: string; active: ContextPanel; onChange: (value: ContextPanel) => void; plan: PlanStep[]; onReference: (file: RunFile) => Promise<boolean>; computerRequested: boolean }) {
  const scroll = useContextScroll(contextKey, active);
  const hasWorkspace = Boolean(computerRequested && run?.computer.attached);
  useEffect(() => { if (run && active === "workspace" && !hasWorkspace) onChange("plan"); }, [active, hasWorkspace, onChange, run]);
  const revisions: Partial<Record<ContextPanel, string>> = run ? {
    ...(events.length ? { plan: contextRevision(plan) } : {}),
    files: contextRevision([run.messages.flatMap(message => message.content_blocks.filter(block => block.type === "file" || block.type === "image")), events.filter(event => /FILE|OUTPUT|ARTIFACT|IMAGE/.test(event.type + event.activityType)).map(event => event.seq)]),
    ...(hasWorkspace ? { workspace: contextRevision([run.computer.name, run.computer.workspace_cwd, run.computer.revision]) } : {}),
  } : {};
  const updates = useContextUpdates(contextKey, revisions);
  const modes = [{ id: "plan", label: "Plan", icon: ListTodo }, { id: "files", label: "Files", icon: FolderOpen }, ...(hasWorkspace ? [{ id: "workspace", label: "Workspace", icon: Folder }] : [])];
  return <AgentDisplayPanel as="aside"><div className="border-b border-black/10 px-3 py-3"><div className="font-mono text-[10px] uppercase tracking-[0.1em] text-[#858481]">Run context</div><div className="mt-2"><AgentDisplayModeTabs label="Run context pages" modes={modes.map(mode => ({ ...mode, attention: updates.unread(mode.id as ContextPanel), attentionLabel: "Updates available" }))} active={active} onChange={(value) => { updates.acknowledge(value as ContextPanel); onChange(value as ContextPanel); }} /></div></div><div ref={scroll} data-context-scroll-root data-context-scroll="panel" aria-label="Run context content" className="min-h-0 flex-1 overflow-y-auto p-4" style={{ overflowAnchor: "none" }}>
    {!run ? <EmptyWorkspace icon={ListTodo} title="No Run selected" description="Start or select a Run to inspect its Plan and attached resources." /> : null}
    {run && active === "plan" ? <PlanPage steps={plan} status={run.status} /> : null}
    {run && active === "files" ? <RunFilesWorkspace key={run.id} runId={run.id} displayToken={displayToken} messages={run.messages} computer={run.computer} status={run.status} onReference={onReference} /> : null}
    {run && active === "workspace" && run.computer.attached ? <WorkspaceContextPage run={run} displayToken={displayToken} /> : null}
  </div></AgentDisplayPanel>;
}

function WorkspaceContextPage({ run, displayToken }: { run: PrivateAgentRunDisplay; displayToken: string }) {
  const { apiContext } = useAuth();
  const queryClient = useQueryClient();
  const [browsePath, setBrowsePath] = useState(run.computer.workspace_cwd || ".");
  useEffect(() => setBrowsePath(run.computer.workspace_cwd || "."), [run.id, run.computer.workspace_cwd, run.computer.revision]);
  const directories = useQuery({
    queryKey: ["private-agent-run-computer", apiContext, run.id, displayToken, run.computer.revision, browsePath],
    queryFn: () => api.privateAgentRunComputerDirectories(apiContext, run.id, displayToken, browsePath),
    enabled: Boolean(run.computer.attached && displayToken),
  });
  const switchFolder = useMutation({
    mutationFn: (path: string) => api.updatePrivateAgentRunWorkspaceCwd(apiContext, run.id, displayToken, path),
    onSuccess: async (value) => {
      setBrowsePath(value.cwd);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["private-agent-run"] }),
        queryClient.invalidateQueries({ queryKey: ["private-agent-run-computer", apiContext, run.id] }),
      ]);
      toast.success("Run working folder updated");
    },
    onError: (error) => toast.error(errorMessage(error) || "Unable to change the Run working folder"),
  });
  const listing = directories.data;
  const crumbs = browsePath === "." ? [] : browsePath.split("/").filter(Boolean);
  return <div className="grid gap-3">
    <div className="border-b border-black/10 pb-3"><div className="font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Run workspace</div><div className="mt-1 text-sm font-semibold text-[#34322d]">{run.computer.name || "Attached Computer"}</div></div>
    {(run.computer.revision ?? 0) > 0 ? <p className="text-sm leading-6 text-[#535350]">Computer changed for the next turn. Earlier Trace and Outputs keep their original device; files and browser sessions were not moved.</p> : null}
    <section className="rounded-md border border-black/10 bg-[#fbfbfa] p-3" aria-labelledby="run-working-folder-title">
      <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div id="run-working-folder-title" className="font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">Working folder</div><div className="mt-1 truncate text-sm font-semibold text-[#34322d]">{formatWorkspacePath(run.computer.workspace_cwd)}</div></div><button type="button" onClick={() => switchFolder.mutate(browsePath)} disabled={switchFolder.isPending || browsePath === (run.computer.workspace_cwd || ".")} className="min-h-10 shrink-0 rounded-md bg-[#1a1a19] px-3 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">Use folder</button></div>
      <p className="mt-2 text-xs leading-5 text-[#858481]">Relative SDK file paths and terminal <code>cwd=&quot;.&quot;</code> use this folder immediately. A command already running remains in its original folder.</p>
      <div className="mt-3 flex min-h-10 flex-wrap items-center gap-1 border-y border-black/10 py-2 text-xs">
        <button type="button" onClick={() => setBrowsePath(".")} className="rounded px-1.5 py-1 font-semibold hover:bg-black/5">Workspace</button>
        {crumbs.map((crumb, index) => { const path = crumbs.slice(0, index + 1).join("/"); return <span key={path} className="contents"><ChevronRight size={12} className="text-[#aaa9a4]" /><button type="button" onClick={() => setBrowsePath(path)} className="rounded px-1.5 py-1 hover:bg-black/5">{crumb}</button></span>; })}
      </div>
      <div className="mt-2 grid gap-1">
        {listing?.parent !== null && listing?.parent !== undefined ? <button type="button" onClick={() => setBrowsePath(listing.parent || ".")} className="flex min-h-10 items-center gap-2 rounded-md px-2 text-left text-sm text-[#535350] hover:bg-black/5"><FolderOpen size={15} /> ..</button> : null}
        {listing?.directories.map((item) => <button type="button" key={item.path} onClick={() => setBrowsePath(item.path)} className="flex min-h-10 items-center gap-2 rounded-md px-2 text-left text-sm text-[#535350] hover:bg-black/5"><Folder size={15} className="text-[#6fa43f]" /><span className="truncate">{item.name}</span></button>)}
        {directories.isLoading ? <div className="flex min-h-10 items-center gap-2 px-2 text-xs text-[#858481]"><Loader2 className="animate-spin" size={14} /> Loading folders</div> : null}
        {directories.isError ? <button type="button" onClick={() => directories.refetch()} className="min-h-10 rounded-md border border-black/10 px-3 text-xs">Retry folder listing</button> : null}
        {!directories.isLoading && !directories.isError && !listing?.directories.length ? <div className="px-2 py-3 text-xs text-[#858481]">No child folders.</div> : null}
      </div>
    </section>
  </div>;
}

function formatWorkspacePath(value: string) { return value && value !== "." ? `Workspace / ${value}` : "Workspace /"; }
function formatDateTime(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "at Run creation" : parsed.toLocaleString(); }

function PlanPage({ steps, status }: { steps: PlanStep[]; status: string }) {
  const visible = steps.length ? steps : [{ id: "run", label: statusLabel(status), state: status }];
  return <ol className="grid gap-2">{visible.map((step, index) => <li key={step.id} className="grid grid-cols-[28px_minmax(0,1fr)] gap-3 rounded-md border border-black/10 p-3"><div className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${["done", "completed"].includes(step.state) ? "bg-[#efffdd] text-[#386a22]" : step.state === "failed" ? "bg-[#fff1ef] text-[#9b2c2c]" : "bg-[#f2f2ef] text-[#6f6e69]"}`}>{["done", "completed"].includes(step.state) ? <CheckCircle2 size={14} /> : index + 1}</div><div><div className="text-sm font-semibold">{step.label}</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.08em] text-[#858481]">{step.state}</div>{step.detail ? <p className="mt-2 text-xs leading-5 text-[#6f6e69]">{step.detail}</p> : null}</div></li>)}</ol>;
}

function BrowserFrameHistory({ frames, runId }: { frames: AgentDisplayBrowserFrame[]; runId: string }) {
  const [index, setIndex] = useState(frames.length - 1);
  useEffect(() => setIndex(frames.length - 1), [frames.length]);
  const frame = frames[Math.min(index, frames.length - 1)];
  return <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto]">
    <ProtectedFrame frame={frame} runId={runId} />
    <div className="border-t border-black/10 bg-white px-3 py-2 text-xs text-[#6f6e69]">
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => setIndex((value) => Math.max(value - 1, 0))} disabled={index <= 0} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-black/10 disabled:opacity-35" aria-label="Previous Browser observation"><ChevronLeft size={14} /></button>
        <button type="button" onClick={() => setIndex((value) => Math.min(value + 1, frames.length - 1))} disabled={index >= frames.length - 1} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-black/10 disabled:opacity-35" aria-label="Next Browser observation"><ChevronRight size={14} /></button>
        <span className="font-mono uppercase tracking-[0.06em]">Observation {frame.revision ?? index + 1} · {index + 1} of {frames.length}</span>
        {frame.action ? <span className="ml-auto truncate">{frame.action.replaceAll("_", " ")} · {frame.actionStatus || "captured"}{frame.domNodeCount !== null ? ` · ${frame.domNodeCount} DOM nodes` : ""}</span> : null}
      </div>
      <div className="mt-1 flex items-center gap-3 pl-[4.5rem] text-[11px] text-[#858481]">
        <span className="min-w-0 flex-1 truncate font-mono" title={frame.url}>{frame.url || "URL not reported"}</span>
        {frame.width && frame.height ? <span className="shrink-0 font-mono">{frame.width}×{frame.height}</span> : null}
      </div>
    </div>
  </div>;
}

function ProtectedFrame({ frame, runId }: { frame: AgentDisplayBrowserFrame; runId: string }) {
  const { apiContext } = useAuth();
  const token = useQuery({ queryKey: ["agent-run-display-token", apiContext, runId], queryFn: () => api.issueAgentRunDisplayToken(apiContext, runId), enabled: Boolean(runId), staleTime: 45 * 60 * 1000 });
  const [url, setUrl] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [zoom, setZoom] = useState<"fit" | number>("fit");
  useEffect(() => { let active = true; let objectUrl = ""; setUrl(""); if (frame.path && token.data?.display_token) api.privateAgentRunDisplayAsset(apiContext, frame.path, token.data.display_token).then((blob) => { if (!active) return; objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }).catch(() => { if (active) setUrl(""); }); return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); }; }, [apiContext, frame.path, token.data?.display_token]);
  useEffect(() => { setExpanded(false); setZoom("fit"); }, [frame.path]);
  const diagnosticPixel = Boolean(frame.width && frame.height && frame.width <= 2 && frame.height <= 2);
  const title = frame.source === "attached_computer"
    ? `Attached Computer · ${frame.computerName || "Computer"} · Isolated browser`
    : frame.title || "Protected Browser frame";
  const numericZoom = zoom === "fit" ? 100 : zoom;
  const setNumericZoom = (value: number) => setZoom(Math.min(Math.max(value, 25), 200));
  const expandedImageStyle = zoom === "fit"
    ? undefined
    : { width: frame.width ? `${Math.max(frame.width * numericZoom / 100, 1)}px` : `${numericZoom}%` };
  return <>
    <AgentDisplayComputerFrame title={title} status="active" tone="light">
      {url ? diagnosticPixel ? <div className="flex h-full items-center justify-center bg-[#f3f3f0] p-8 text-center"><div><Monitor className="mx-auto text-[#858481]" size={28} /><div className="mt-3 text-sm font-semibold text-[#535350]">Diagnostic frame received</div><p className="mt-1 max-w-sm text-xs leading-5 text-[#858481]">The Agent published a {frame.width}×{frame.height} pixel frame. Publish a real PNG, JPEG or WebP screenshot to display Browser content.</p></div></div> : <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] bg-[#f3f3f0]">
        <div className="group relative min-h-0 overflow-hidden">
          <img src={url} alt={frame.title || "Agent Browser frame"} className="h-full min-h-0 w-full object-contain" />
          <button type="button" onClick={() => setExpanded(true)} className="absolute right-3 top-3 inline-flex min-h-10 items-center gap-2 rounded-md border border-black/15 bg-white/95 px-3 text-xs font-semibold text-[#34322d] shadow-sm backdrop-blur hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#6fa43f]" aria-label="Open larger frame view">
            <Maximize2 size={14} /> Enlarge
          </button>
        </div>
        <div className="flex items-center gap-3 border-t border-black/10 bg-white px-4 py-2 text-xs text-[#6f6e69]"><span className="min-w-0 flex-1 truncate">{frame.text || frame.url || `${frame.width || "?"}×${frame.height || "?"} protected frame`}</span><button type="button" onClick={() => setExpanded(true)} className="min-h-10 shrink-0 rounded-md px-2 font-semibold text-[#535350] underline underline-offset-2">View larger</button></div>
      </div> : <div className="flex h-full items-center justify-center text-sm text-[#858481]"><Loader2 className="mr-2 animate-spin" size={15} /> Loading frame</div>}
    </AgentDisplayComputerFrame>
    <NexilumeDialog open={expanded} onClose={() => setExpanded(false)} size="large" title={frame.title || "Protected Browser frame"} eyebrow={title} description={frame.width && frame.height ? `${frame.width} × ${frame.height} protected image` : "Protected image from this Run"} footer={<div className="flex w-full flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-1"><button type="button" onClick={() => setZoom("fit")} className={`min-h-11 rounded-md border px-3 text-xs font-semibold ${zoom === "fit" ? "border-[#6fa43f] bg-[#efffdd] text-[#386a22]" : "border-black/15 bg-white text-[#535350]"}`}>Fit</button><button type="button" aria-label="Zoom out" onClick={() => setNumericZoom(numericZoom - 25)} className="inline-flex h-11 w-11 items-center justify-center rounded-md border border-black/15 bg-white"><Minus size={15} /></button><output aria-label="Zoom level" aria-live="polite" className="min-w-14 text-center font-mono text-xs">{zoom === "fit" ? "Fit" : `${zoom}%`}</output><button type="button" aria-label="Zoom in" onClick={() => setNumericZoom(numericZoom + 25)} className="inline-flex h-11 w-11 items-center justify-center rounded-md border border-black/15 bg-white"><Plus size={15} /></button><button type="button" onClick={() => setZoom(100)} className="min-h-11 rounded-md border border-black/15 bg-white px-3 text-xs font-semibold">100%</button></div><button type="button" onClick={() => setExpanded(false)} className="min-h-11 rounded-md bg-[#1a1a19] px-4 text-xs font-semibold text-white">Close</button></div>}>
      <div className="flex h-[min(72vh,760px)] min-h-[320px] items-center justify-center overflow-auto rounded-md border border-black/10 bg-[#e9e9e5] p-3">
        {url ? <img src={url} alt={`${frame.title || "Agent Browser frame"} enlarged`} className={zoom === "fit" ? "max-h-full max-w-full object-contain" : "max-w-none shrink-0"} style={expandedImageStyle} /> : <div className="inline-flex items-center gap-2 text-sm text-[#858481]"><Loader2 className="animate-spin" size={15} /> Loading frame</div>}
      </div>
    </NexilumeDialog>
  </>;
}

function RunShellPanel({ runId, terminalEvents, sdkLines, terminal, connection, fallbackStatus, fallbackComputer, transcript }: {
  runId: string;
  terminalEvents: AgentRunTerminal["events"];
  sdkLines: AgentDisplayShellLine[];
  terminal: AgentRunTerminal | null;
  connection: PrivateAgentTerminalConnection;
  fallbackStatus: string;
  fallbackComputer: string;
  transcript: ReturnType<typeof buildAgentDisplayShellTranscript>;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const latestSeq = terminalEvents.at(-1)?.seq ?? sdkLines.at(-1)?.seq ?? 0;
  const previousSeq = useRef(latestSeq);
  const following = useRef(true);
  const [newOutput, setNewOutput] = useState(0);
  const [query, setQuery] = useState("");
  const [wrap, setWrap] = useState(() => readShellSetting("nexus:private-shell:wrap") !== "off");
  const commands = useMemo(() => buildAgentDisplayShellCommands(terminalEvents, sdkLines), [terminalEvents, sdkLines]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return commands;
    return commands.filter((item) => `${item.command}\n${item.transcript.text}`.toLocaleLowerCase().includes(needle));
  }, [commands, query]);
  const status = terminal?.status || fallbackStatus;
  const computer = terminal?.computer_name || fallbackComputer;
  const storageKey = `nexus:private-shell:scroll:${runId}`;

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const restored = Number(readShellSetting(storageKey) || 0);
    requestAnimationFrame(() => {
      element.scrollTop = restored > 0 ? Math.min(restored, element.scrollHeight) : element.scrollHeight;
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
    });
  }, [storageKey]);
  useEffect(() => {
    if (latestSeq <= previousSeq.current) return;
    const additions = latestSeq - previousSeq.current;
    previousSeq.current = latestSeq;
    const element = viewport.current;
    if (element && following.current) requestAnimationFrame(() => { element.scrollTop = element.scrollHeight; });
    else setNewOutput((value) => value + Math.max(1, additions));
  }, [latestSeq]);

  const connectionLabel = connection === "live" ? "Live" : connection === "connecting" || connection === "reconnecting" ? "Reconnecting" : connection === "polling" ? "Polling fallback" : status.replaceAll("_", " ");
  return (
    <AgentDisplayComputerFrame
      title={`${computer} · ${terminal?.shell && terminal.shell !== "auto" ? terminal.shell : "Shell"} · read only`}
      status={status}
      tone="dark"
      action={<div className="flex items-center gap-1">
        <button type="button" onClick={() => downloadShellTranscript(runId, transcript.text)} disabled={!transcript.text} className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-semibold normal-case text-white/70 hover:bg-white/10 hover:text-white disabled:opacity-40" aria-label="Download Shell transcript"><Download size={13} /> <span className="hidden md:inline">Download</span></button>
        <button type="button" onClick={() => copyShellTranscript(transcript.text)} disabled={!transcript.text} title={transcript.text ? "Copy the complete Shell transcript" : "No Shell output to copy yet"} className="inline-flex min-h-11 items-center gap-1.5 rounded-md border border-white/15 px-2.5 text-[11px] font-semibold normal-case text-white/70 hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-40" aria-label="Copy Shell output"><Copy size={13} /> Copy</button>
      </div>}
    >
      <div className="flex min-h-11 shrink-0 flex-wrap items-center gap-2 border-b border-white/10 px-3 py-1.5 text-[11px] text-white/55">
        <label className="relative min-w-[150px] flex-1 sm:max-w-xs"><Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-white/35" size={13} /><span className="sr-only">Search Shell transcript</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search commands or output" className="min-h-9 w-full rounded-md border border-white/15 bg-white/5 pl-8 pr-3 text-xs text-white outline-none placeholder:text-white/30 focus:border-white/35" /></label>
        <button type="button" aria-pressed={wrap} onClick={() => { const next = !wrap; setWrap(next); writeShellSetting("nexus:private-shell:wrap", next ? "on" : "off"); }} className={`inline-flex min-h-11 items-center gap-1.5 rounded-md px-2.5 ${wrap ? "bg-white/10 text-white" : "text-white/55 hover:bg-white/5"}`}><WrapText size={13} /> Wrap</button>
        <span role="status" className="capitalize">{connectionLabel}</span>
        {terminal?.workspace_cwd ? <span className="max-w-full truncate font-mono" title={terminal.workspace_cwd}>cwd {terminal.workspace_cwd}</span> : null}
      </div>
      {terminal?.last_error ? <p role="alert" className="shrink-0 border-b border-red-400/20 bg-red-950/40 px-3 py-2 text-xs text-red-200">{terminal.last_error}</p> : null}
      {terminal?.truncated ? <p role="status" className="shrink-0 border-b border-amber-300/20 bg-amber-950/30 px-3 py-2 text-xs text-amber-100">Earlier Shell output exceeded the protected transcript limit and was truncated.</p> : null}
      <div ref={viewport} onScroll={(event) => {
        const element = event.currentTarget;
        following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
        if (following.current) setNewOutput(0);
        writeShellSetting(storageKey, String(Math.round(element.scrollTop)));
      }} aria-label="Read-only Shell transcript" tabIndex={0} className="relative min-h-0 flex-1 cursor-text select-text overflow-auto px-4 py-3 font-mono text-xs leading-6 text-white/80" style={{ scrollbarGutter: "stable" }}>
        {!commands.length ? <span className="text-white/40">Shell output will appear when the Agent executes a command.</span> : null}
        {commands.length && !filtered.length ? <div className="py-10 text-center font-sans text-sm text-white/45">No Shell output matches this search.</div> : null}
        <div className="grid gap-3">
          {filtered.map((command) => <section key={command.id} className="min-w-0 overflow-hidden rounded-md border border-white/10 bg-white/[0.025]">
            <header className="flex min-h-10 min-w-0 items-center gap-2 border-b border-white/10 px-3 font-sans text-[11px] text-white/50">
              <span className="min-w-0 flex-1 truncate font-mono text-[#bdfc73]" title={command.command}>$ {command.command}</span>
              {command.startedAt ? <time className="hidden shrink-0 sm:block" dateTime={command.startedAt}>{formatDateTime(command.startedAt)}</time> : null}
              {formatShellDuration(command.startedAt, command.endedAt) ? <span className="hidden shrink-0 md:block">{formatShellDuration(command.startedAt, command.endedAt)}</span> : null}
              {command.exitCode !== null ? <span className={command.exitCode === 0 ? "text-emerald-300" : "text-red-300"}>Exit {command.exitCode}</span> : <span>{status === "active" ? "Running" : "No exit code"}</span>}
              <button type="button" onClick={() => copyShellTranscript(command.transcript.text)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md hover:bg-white/10" aria-label={`Copy output for ${command.command}`}><Copy size={13} /></button>
            </header>
            <pre className={`min-w-0 px-3 py-2 ${wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"}`}>{command.transcript.segments.filter((item) => item.stream !== "command" && !item.text.startsWith("Process exited with code ")).map((item) => <span key={item.id} aria-label={item.stream === "stderr" ? "Standard error" : undefined} className={item.stream === "stderr" ? "text-red-300" : item.stream === "system" ? "text-white/45" : ""}>{item.text}</span>)}</pre>
          </section>)}
        </div>
        {newOutput > 0 ? <div className="sticky bottom-3 flex justify-center"><button type="button" onClick={() => { const element = viewport.current; if (element) element.scrollTop = element.scrollHeight; following.current = true; setNewOutput(0); }} className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 bg-[#262626] px-4 font-sans text-xs text-white shadow-md"><ArrowDown size={14} /> New Shell output</button></div> : null}
      </div>
    </AgentDisplayComputerFrame>
  );
}

function readShellSetting(key: string) {
  try { return window.sessionStorage.getItem(key) || window.localStorage.getItem(key) || ""; } catch { return ""; }
}

function writeShellSetting(key: string, value: string) {
  try {
    if (key.includes(":scroll:")) window.sessionStorage.setItem(key, value);
    else window.localStorage.setItem(key, value);
  } catch { /* Shell remains usable when browser storage is unavailable. */ }
}

function downloadShellTranscript(runId: string, value: string) {
  if (!value) return;
  const url = URL.createObjectURL(new Blob([value], { type: "text/plain;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `nexus-shell-${runId || "run"}.txt`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function formatShellDuration(start: string, end: string) {
  const startAt = Date.parse(start);
  const endAt = Date.parse(end);
  if (!Number.isFinite(startAt) || !Number.isFinite(endAt) || endAt <= startAt) return "";
  const milliseconds = endAt - startAt;
  return milliseconds < 1_000 ? `${milliseconds} ms` : `${(milliseconds / 1_000).toFixed(milliseconds < 10_000 ? 1 : 0)} s`;
}

async function copyShellTranscript(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) {
      toast.error("Unable to copy Shell output");
      return;
    }
  }
  toast.success("Shell output copied");
}

function mergeRunMessages(persisted: AgentRunMessage[], events: DisplayStreamEvent[], optimistic: string, optimisticReply: string, optimisticBase = 0): AgentRunMessage[] {
  const result = persisted.map((message) => ({ ...message, content_blocks: [...message.content_blocks] }));
  if (optimistic && !persisted.slice(optimisticBase).some(item => item.role === "user" && item.content === optimistic)) {
    result.push({ id: "optimistic-run", sequence: result.length + 1, turn_index: null, role: "user", content: optimistic, content_blocks: [{ type: "markdown", text: optimistic }], created_at: "" });
  }
  if (optimisticReply && !persisted.slice(optimisticBase).some(item => item.role === "user" && item.content === optimisticReply)) {
    result.push({
      id: "optimistic-interaction-reply",
      sequence: result.length + 1,
      turn_index: null,
      role: "user",
      content: optimisticReply,
      content_blocks: [{ type: "markdown", text: optimisticReply }],
      created_at: "",
    });
  }
  for (const streamed of deriveAgentDisplayMessages(events).filter((item) => item.content)) {
    const materialized = result.some((message) =>
      message.role === streamed.role && (
        message.content === streamed.content
        || message.content_blocks.some((block) => block.type === "markdown" && block.text === streamed.content)
      )
    );
    if (materialized) continue;
    const latest = result.at(-1);
    if (streamed.role === "assistant" && latest?.role === "assistant") {
      latest.content = `${latest.content}\n\n${streamed.content}`;
      latest.content_blocks = [...latest.content_blocks, { type: "markdown", text: streamed.content }];
      continue;
    }
    result.push({
      id: `event-${streamed.id}`,
      sequence: result.length + 1,
      turn_index: null,
      role: streamed.role,
      content: streamed.content,
      content_blocks: [{ type: "markdown", text: streamed.content }],
      created_at: "",
    });
  }
  return result;
}

function derivePlan(events: DisplayStreamEvent[]): PlanStep[] {
  let steps: PlanStep[] = [];
  for (const event of events) {
    if (event.type === "ACTIVITY_SNAPSHOT" && (event.activityType === "PLAN" || !event.activityType)) { const raw = event.content?.steps; if (Array.isArray(raw)) steps = raw.map((item, index) => { const value = record(item); return { id: text(value.id) || `step-${index}`, label: text(value.label || value.title), state: text(value.state || value.status) || "pending", detail: text(value.detail) }; }); }
    if (event.type === "ACTIVITY_DELTA") for (const operation of event.patch ?? []) { const match = text(operation.path).match(/^\/steps\/(\d+)\/(state|label|detail)$/); if (match && steps[Number(match[1])]) steps = steps.map((step, index) => index === Number(match[1]) ? { ...step, [match[2]]: text(operation.value) } : step); }
  }
  return steps;
}

function ContextFact({ label, value }: { label: string; value: string }) { return <div className="flex items-center justify-between gap-3 border-b border-black/10 pb-3"><span className="text-xs text-[#858481]">{label}</span><span className="text-sm font-medium capitalize">{value}</span></div>; }
function CommandHelpRow({ command, description }: { command: string; description: string }) { return <div className="grid gap-1 py-3 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4"><span className="font-mono text-xs font-semibold text-[#34322d]">{command}</span><span className="text-sm leading-5 text-[#6f6e69]">{description}</span></div>; }
function EmptyWorkspace({ icon: Icon, title, description }: { icon: typeof Monitor; title: string; description: string }) { return <div className="flex min-h-56 items-center justify-center px-6 text-center"><div><Icon className="mx-auto text-[#aaa9a5]" size={24} /><div className="mt-3 text-sm font-semibold text-[#535350]">{title}</div><p className="mx-auto mt-1 max-w-sm text-xs leading-5 text-[#858481]">{description}</p></div></div>; }
function PrivateDisplayState() { return <AgentDisplaySurface standalone><div className="flex h-full items-center justify-center"><EmptyWorkspace icon={LockKeyhole} title="Private Display unavailable" description="Open Private Display from an Agent you can invoke." /></div></AgentDisplaySurface>; }
function statusLabel(status: string) { if (status === "completed") return "Run completed"; if (status === "failed") return "Run failed"; if (status === "input_required") return "Waiting for input"; return "Agent is working"; }

function errorMessage(value: unknown) { return value instanceof Error ? value.message : ""; }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function text(value: unknown): string { return typeof value === "string" ? value : value == null ? "" : String(value); }
