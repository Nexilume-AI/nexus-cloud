import { t, useLocale, getLocale } from "../localization";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Copy,
  Download,
  Eye,
  KeyRound,
  Loader2,
  MonitorUp,
  MoreHorizontal,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Server,
  Settings2,
  ShieldCheck,
  Square,
  Terminal,
  Trash2,
  Wifi,
  WifiOff,
  X
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { useApplicationDistribution } from '../app/distribution';
import { personalComputerPairing } from '../app/computerPairing';
import { api, type ApiContext, workspaceTerminalWebSocketUrl } from "../lib/api";
import { formatTerminalStatusLine, TerminalOutputNormalizer } from "../lib/terminal";
import type {
  Agent,
  ComputerRuntimePairing,
  ProviderRuntimeAccount,
  ProviderRuntimeCredentials,
  WorkspaceConnection,
  WorkspaceConnectionTestResult,
  WorkspaceTerminalSession
} from "../lib/types";
import { Field } from "../components/Form";
import { ToolSetupPanel, getToolStatus, formatComputerConnection, type ToolSetupForm } from "../components/ToolSetupPanel";



export function PlaygroundPage() {
  useLocale();
  const { apiContext, isContextReady } = useAuth();
  const queryClient = useQueryClient();

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-brand">{t("Workspace operations")}</div>
          <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-ink">{t("Computer")}</h1>
          <p className="mt-1 text-sm leading-6 text-muted">{t("Pair this user's Computer Runtime, resume a terminal session, and configure agent tooling without exposing an inbound SSH service.")}</p>
        </div>
      </div>

      <RemoteWorkspacePanel apiContext={apiContext} isContextReady={isContextReady} queryClient={queryClient} />
    </div>
  );
}

function RemoteWorkspacePanel({
  apiContext,
  isContextReady,
  queryClient
}: {
  apiContext: ApiContext;
  isContextReady: boolean;
  queryClient: ReturnType<typeof useQueryClient>;
}) {
  useLocale();
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [activeSession, setActiveSession] = useState<WorkspaceTerminalSession | null>(null);
  const [lastConnectionTest, setLastConnectionTest] = useState<WorkspaceConnectionTestResult | null>(null);
  const [isTargetModalOpen, setIsTargetModalOpen] = useState(false);
  const [pairingName, setPairingName] = useState("My Computer");
  const [pairingWorkspaceRoot, setPairingWorkspaceRoot] = useState("~/.nexus");
  const [computerPairing, setComputerPairing] = useState<ComputerRuntimePairing | null>(null);
  const [renameConnectionId, setRenameConnectionId] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [detailsConnectionId, setDetailsConnectionId] = useState("");
  const [isToolSetupOpen, setIsToolSetupOpen] = useState(false);
  const [targetSearch, setTargetSearch] = useState("");
  const [targetFilter, setTargetFilter] = useState<"all" | "attention">("all");
  const autoTestedConnectionsRef = useRef<Set<string>>(new Set());
  const silentTestConnectionsRef = useRef<Set<string>>(new Set());
  const [toolSetup, setToolSetup] = useState<ToolSetupForm>({
    selectedTool: "codex",
    selectedRuntimeId: "",
    selectedAgentId: ""
  });

  const connections = useQuery({
    queryKey: ["workspace-connections", apiContext],
    queryFn: () => api.workspaceConnections(apiContext),
    enabled: isContextReady
  });
  const sessions = useQuery({
    queryKey: ["workspace-terminal-sessions", apiContext],
    queryFn: () => api.workspaceTerminalSessions(apiContext),
    enabled: isContextReady
  });

  const connectionData = connections.data ?? [];
  const sessionData = sessions.data ?? [];
  const latestResumableSession = sessionData.find((session) =>
    ["created", "active"].includes(session.status.toLowerCase())
  );
  const selectedConnection =
    connectionData.find((item) => item.id === selectedConnectionId) ??
    connectionData.find((item) => item.id === latestResumableSession?.connection_id) ??
    connectionData[0];
  const detailsConnection = connectionData.find((item) => item.id === detailsConnectionId);
  const selectedSessions = useMemo(
    () => (selectedConnection ? sessionData.filter((item) => item.connection_id === selectedConnection.id) : []),
    [selectedConnection, sessionData]
  );
  const resumableSession = useMemo(
    () => selectedSessions.find((session) => ["created", "active"].includes(session.status.toLowerCase())),
    [selectedSessions]
  );

  useEffect(() => {
    const activeSessionMatchesSelection = Boolean(
      activeSession &&
      selectedConnection &&
      activeSession.connection_id === selectedConnection.id &&
      ["created", "active"].includes(activeSession.status.toLowerCase())
    );
    if (!activeSessionMatchesSelection && activeSession?.id !== resumableSession?.id) {
      setActiveSession(resumableSession ?? null);
    }
  }, [activeSession, resumableSession, selectedConnection?.id]);

  async function refreshWorkspace() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["workspace-connections"] }),
      queryClient.invalidateQueries({ queryKey: ["workspace-terminal-sessions"] })
    ]);
  }

  function closeTargetModal() {
    setIsTargetModalOpen(false);
    setComputerPairing(null);
  }

  function openAddTarget() {
    setPairingName("My Computer");
    setPairingWorkspaceRoot("~/.nexus");
    setComputerPairing(null);
    setIsTargetModalOpen(true);
  }

  function openPairReplacement(connection: WorkspaceConnection) {
    setPairingName(connection.name);
    setPairingWorkspaceRoot(connection.workspace_root || "~/.nexus");
    setComputerPairing(null);
    setIsTargetModalOpen(true);
  }

  const createComputerPairing = useMutation({
    mutationFn: () =>
      api.computerRuntimePairingCode(apiContext, {
        name: pairingName.trim() || "My Computer",
        project_id: apiContext.projectId || null,
        workspace_root: pairingWorkspaceRoot.trim() || "~/.nexus",
      }),
    onSuccess: async (pairing) => {
      setComputerPairing(pairing);
      await queryClient.invalidateQueries({ queryKey: ["workspace-connections"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Pairing link could not be created")),
  });

  useEffect(() => {
    if (!isTargetModalOpen || !computerPairing) return;
    const interval = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: ["workspace-connections"] });
    }, 2000);
    return () => window.clearInterval(interval);
  }, [computerPairing, isTargetModalOpen, queryClient]);

  const deleteConnection = useMutation({
    mutationFn: (connectionId: string) => api.deleteWorkspaceConnection(apiContext, connectionId),
    onSuccess: async () => {
      setActiveSession(null);
      setSelectedConnectionId("");
      toast.success(t("Target deleted"));
      await refreshWorkspace();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to delete target"))
  });

  const revokeComputer = useMutation({
    mutationFn: (connectionId: string) => api.revokeComputerRuntime(apiContext, connectionId),
    onSuccess: async () => {
      toast.success(t("Computer Runtime revoked"));
      await refreshWorkspace();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Computer Runtime could not be revoked")),
  });

  const renameComputer = useMutation({
    mutationFn: ({ connectionId, name }: { connectionId: string; name: string }) =>
      api.updateWorkspaceConnection(apiContext, connectionId, { name }),
    onSuccess: async () => {
      setRenameConnectionId("");
      setRenameValue("");
      toast.success(t("Computer renamed"));
      await refreshWorkspace();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Computer could not be renamed")),
  });

  const testConnection = useMutation({
    mutationFn: (connectionId: string) => api.testWorkspaceConnection(apiContext, connectionId),
    onSuccess: async (result, connectionId) => {
      const silent = silentTestConnectionsRef.current.delete(connectionId);
      setLastConnectionTest(result);
      if (!silent) toast.success(result.status === "succeeded" ? t("Connection test passed") : t("Connection test failed"));
      await refreshWorkspace();
    },
    onError: (error, connectionId) => {
      const silent = silentTestConnectionsRef.current.delete(connectionId);
      setLastConnectionTest(null);
      if (!silent) toast.error(error instanceof Error ? error.message : t("Connection test failed"));
    }
  });

  useEffect(() => {
    if (!selectedConnection || !shouldRefreshTargetHealth(selectedConnection)) return;
    if (autoTestedConnectionsRef.current.has(selectedConnection.id) || testConnection.isPending) return;
    autoTestedConnectionsRef.current.add(selectedConnection.id);
    silentTestConnectionsRef.current.add(selectedConnection.id);
    testConnection.mutate(selectedConnection.id);
  }, [selectedConnection?.id, selectedConnection?.last_test_at]);

  const createSession = useMutation({
    mutationFn: (connectionId: string) =>
      api.createWorkspaceTerminalSession(apiContext, {
        connection_id: connectionId,
        shell: selectedConnection ? inferSessionShell(selectedConnection) : "auto",
        cols: 120,
        rows: 34
      }),
    onSuccess: async (session) => {
      setActiveSession(session);
      toast.success(t("Terminal ready"));
      await refreshWorkspace();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to open terminal"))
  });

  const closeSession = useMutation({
    mutationFn: (sessionId: string) => api.closeWorkspaceTerminalSession(apiContext, sessionId),
    onSuccess: async (closedSession) => {
      queryClient.setQueryData<WorkspaceTerminalSession[]>(
        ["workspace-terminal-sessions", apiContext],
        (current) => current?.map((session) => (session.id === closedSession.id ? closedSession : session)) ?? []
      );
      setActiveSession(null);
      toast.success(t("Terminal closed"));
      await refreshWorkspace();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to close terminal"))
  });

  const detectTools = useMutation({
    mutationFn: (sessionId: string) => api.detectWorkspaceTerminalTools(apiContext, sessionId),
    onSuccess: async (session) => {
      setActiveSession(session);
      toast.success(t("Tool status refreshed"));
      await refreshWorkspace();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to detect tools"))
  });

  const workspaceHasError = connections.isError || sessions.isError;
  const sessionIsLive = Boolean(activeSession && ["created", "active"].includes(activeSession.status.toLowerCase()));
  const selectedActiveSessionCount = selectedSessions.filter((session) => ["created", "active"].includes(session.status.toLowerCase())).length;

  return (
    <div className="grid gap-4">
      {workspaceHasError && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900" role="alert">
          <div>
            <div className="font-semibold">{t("Computer data could not be refreshed.")}</div>
            <div className="mt-0.5 text-rose-700">{t("Check the selected workspace and try again. Existing terminal sessions are not stopped.")}</div>
          </div>
          <button className="btn border-rose-200 bg-white text-rose-800" type="button" onClick={() => void refreshWorkspace()}>
            <RefreshCw size={16} />{t("Retry")}</button>
        </div>
      )}

      <section className="overflow-hidden rounded-xl border border-line bg-white shadow-panel" aria-label={t("Computer workbench")}>
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line bg-slate-50/70 px-4 py-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-3">
            <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${sessionIsLive ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-line bg-white text-slate-600"}`}>
              {sessionIsLive ? <Wifi size={18} /> : <WifiOff size={18} />}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate text-sm font-semibold text-ink">{selectedConnection?.name || t("No Computer selected")}</h2>
                <span className="text-xs font-medium text-muted">{t("Session")}</span>
                <WorkspaceStatusBadge status={activeSession?.status || "not connected"} />
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                <span>
                  {selectedConnection ? formatComputerConnection(selectedConnection) : t("Pair a Computer Runtime to begin.")}
                </span>
                {selectedConnection && (
                  <span className="inline-flex items-center gap-1">{t("Target health:")}<span className="font-medium text-slate-700">{formatTargetHealth(selectedConnection)}</span>
                    {selectedConnection.last_test_at ? ` · ${formatRelativeTime(selectedConnection.last_test_at)}` : ""}
                  </span>
                )}
                {selectedConnection && (
                  <span>{selectedActiveSessionCount}{" "}{t("active session")}{getLocale() === "zh-CN" ? "" : selectedActiveSessionCount === 1 ? "" : "s"}</span>
                )}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn"
              type="button"
              disabled={!selectedConnection?.availability?.available}
              onClick={() => setIsToolSetupOpen(true)}
            >
              <Settings2 size={16} />{t("Configure tools")}</button>
            <button className="btn" type="button" onClick={() => void refreshWorkspace()} disabled={connections.isFetching || sessions.isFetching}>
              {connections.isFetching || sessions.isFetching ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}{t("Sync")}</button>
            {activeSession ? (
              <button
                className="btn"
                type="button"
                onClick={() => closeSession.mutate(activeSession.id)}
                disabled={closeSession.isPending}
              >
                {closeSession.isPending ? <Loader2 size={16} className="animate-spin" /> : <Square size={16} />}{t("End session")}</button>
            ) : (
              <button
                className="btn btn-primary"
                type="button"
                disabled={!selectedConnection?.availability?.available || createSession.isPending}
                onClick={() => {
                  if (selectedConnection) createSession.mutate(selectedConnection.id);
                }}
              >
                {createSession.isPending ? <Loader2 size={16} className="animate-spin" /> : <Terminal size={16} />}{t("Open terminal")}</button>
            )}
          </div>
        </header>

        <div className="grid min-w-0 xl:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="min-w-0 border-b border-line bg-slate-50/40 xl:border-b-0 xl:border-r" aria-label={t("Computers")}>
            <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold text-ink">{t("Computers")}</h2>
                <p className="mt-0.5 text-xs text-muted">{connectionData.length}{" "}{t("configured")}</p>
              </div>
              <button className="btn btn-primary" type="button" onClick={openAddTarget}>
                <Plus size={16} />{t("Pair Computer")}</button>
            </div>
            {connectionData.length > 5 && (
              <div className="grid gap-2 border-b border-line bg-white px-3 py-3">
                <label className="relative">
                  <span className="sr-only">{t("Search Computers")}</span>
                  <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" size={15} />
                  <input
                    className="input h-9 pl-9 text-sm"
                    placeholder={t("Search targets")}
                    value={targetSearch}
                    onChange={(event) => setTargetSearch(event.target.value)}
                  />
                </label>
                <div className="grid grid-cols-2 rounded-lg bg-slate-100 p-1" aria-label={t("Target health filter")}>
                  {(["all", "attention"] as const).map((filter) => (
                    <button
                      key={filter}
                      className={`min-h-8 rounded-md px-2 text-xs font-semibold ${targetFilter === filter ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink"}`}
                      type="button"
                      onClick={() => setTargetFilter(filter)}
                    >
                      {filter === "all" ? t("All") : t("Needs attention")}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="max-h-[360px] overflow-y-auto xl:max-h-[720px]">
          <TargetsList
            connections={connectionData}
            sessions={sessionData}
            selectedConnection={selectedConnection}
            isLoading={connections.isLoading}
            isTesting={testConnection.isPending}
            testingConnectionId={testConnection.variables || ""}
            search={targetSearch}
            filter={targetFilter}
            onSelect={(connection) => {
              if (connection.id === selectedConnection?.id) return;
              const nextSession = sessionData.find(
                (session) =>
                  session.connection_id === connection.id &&
                  ["created", "active"].includes(session.status.toLowerCase())
              );
              setActiveSession(nextSession ?? null);
              setSelectedConnectionId(connection.id);
              setLastConnectionTest(null);
            }}
            onTest={(connection) => testConnection.mutate(connection.id)}
            onViewDetails={(connection) => setDetailsConnectionId(connection.id)}
            onRevoke={(connection) => {
              if (window.confirm(`Revoke "${connection.name}"? Its outbound Runtime will disconnect and existing bindings will become unavailable.`)) {
                revokeComputer.mutate(connection.id);
              }
            }}
            onDelete={(connection) => {
              if (window.confirm(`Delete Computer "${connection.name}"? Existing active sessions must be closed first.`)) {
                deleteConnection.mutate(connection.id);
              }
            }}
            onAdd={openAddTarget}
          />
            </div>
          </aside>

          <section className="min-w-0 bg-white" aria-labelledby="remote-terminal-title">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
              <div>
                <h2 id="remote-terminal-title" className="text-sm font-semibold text-ink">{t("Terminal")}</h2>
                <p className="mt-0.5 text-xs text-muted">{t("This shell and its recent output stay attached until you end the session.")}</p>
              </div>
              {activeSession && <WorkspaceStatusBadge status={activeSession.status} />}
            </div>
            <div className="p-3 sm:p-4">
              <TerminalWorkspace
                apiContext={apiContext}
                selectedConnection={selectedConnection}
                activeSession={activeSession}
                onConnected={() => {
                  setActiveSession((current) =>
                    current && current.status !== "active" ? { ...current, status: "active" } : current
                  );
                }}
                onDisconnected={() => void refreshWorkspace()}
              />
            </div>
          </section>

        </div>
      </section>

      {isTargetModalOpen && (
        <PairComputerModal
          name={pairingName}
          workspaceRoot={pairingWorkspaceRoot}
          pairing={computerPairing}
          connection={connectionData.find((item) => item.id === computerPairing?.connection_id)}
          isCreating={createComputerPairing.isPending}
          onNameChange={setPairingName}
          onWorkspaceRootChange={setPairingWorkspaceRoot}
          onClose={closeTargetModal}
          onCreate={() => createComputerPairing.mutate()}
        />
      )}

      {detailsConnection && (
        <TargetDetailsDrawer
          connection={detailsConnection}
          sessions={sessionData.filter((session) => session.connection_id === detailsConnection.id)}
          isTesting={testConnection.isPending && testConnection.variables === detailsConnection.id}
          onClose={() => setDetailsConnectionId("")}
          onRename={() => {
            setRenameConnectionId(detailsConnection.id);
            setRenameValue(detailsConnection.name);
          }}
          onPairAgain={() => {
            setDetailsConnectionId("");
            openPairReplacement(detailsConnection);
          }}
          onTest={() => testConnection.mutate(detailsConnection.id)}
          onRevoke={() => {
            if (window.confirm(`Revoke "${detailsConnection.name}"? Its outbound Runtime will disconnect.`)) {
              revokeComputer.mutate(detailsConnection.id);
            }
          }}
          onDelete={() => {
            if (window.confirm(`Delete Computer "${detailsConnection.name}"? Existing active sessions must be closed first.`)) {
              setDetailsConnectionId("");
              deleteConnection.mutate(detailsConnection.id);
            }
          }}
        />
      )}

      {renameConnectionId && (
        <RenameComputerDialog
          name={renameValue}
          isSaving={renameComputer.isPending}
          onNameChange={setRenameValue}
          onClose={() => {
            if (!renameComputer.isPending) {
              setRenameConnectionId("");
              setRenameValue("");
            }
          }}
          onSave={() => renameComputer.mutate({ connectionId: renameConnectionId, name: renameValue.trim() })}
        />
      )}

      {isToolSetupOpen && (
        <ToolSetupPanel
          client={api}
          key={`${apiContext.tenantId}:${apiContext.projectId}:${activeSession?.id || selectedConnection?.id}`}
          apiContext={apiContext}
          isContextReady={isContextReady}
          form={toolSetup}
          selectedConnection={selectedConnection}
          activeSession={activeSession}
          isDetecting={detectTools.isPending}
          isOpeningSession={createSession.isPending}
          onChange={setToolSetup}
          onClose={() => setIsToolSetupOpen(false)}
          onDetectTools={() => {
            if (activeSession) detectTools.mutate(activeSession.id);
          }}
          onOpenSession={() => {
            if (selectedConnection) createSession.mutate(selectedConnection.id);
          }}
        />
      )}
    </div>
  );
}

function TerminalWorkspace({
  apiContext,
  selectedConnection,
  activeSession,
  onConnected,
  onDisconnected
}: {
  apiContext: ApiContext;
  selectedConnection: WorkspaceConnection | undefined;
  activeSession: WorkspaceTerminalSession | null;
  onConnected: () => void;
  onDisconnected: () => void;
}) {
  useLocale();
  const terminalRef = useRef<HTMLDivElement | null>(null);
  const xtermRef = useRef<XTerm | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const onConnectedRef = useRef(onConnected);
  const onDisconnectedRef = useRef(onDisconnected);
  const reconnectAttemptsRef = useRef(0);
  const isCompactViewport = useCompactViewport();
  const activeSessionId = activeSession?.id;
  const [socketStatus, setSocketStatus] = useState<"idle" | "connecting" | "connected" | "disconnected" | "error">("idle");
  const [reconnectKey, setReconnectKey] = useState(0);

  useEffect(() => {
    onConnectedRef.current = onConnected;
  }, [onConnected]);

  useEffect(() => {
    onDisconnectedRef.current = onDisconnected;
  }, [onDisconnected]);

  useEffect(() => {
    if (!activeSessionId || !terminalRef.current || isCompactViewport) {
      setSocketStatus("idle");
      return;
    }
    let disposed = false;
    let reconnectTimer: number | undefined;
    setSocketStatus("connecting");
    const terminal = new XTerm({
      cursorBlink: true,
      convertEol: false,
      scrollback: 5000,
      fontFamily: "JetBrains Mono, Consolas, Menlo, monospace",
      fontSize: 13,
      theme: {
        background: "#0f172a",
        foreground: "#e2e8f0",
        cursor: "#38bdf8"
      }
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalRef.current);
    xtermRef.current = terminal;
    fitAddon.fit();
    terminal.writeln("Connecting to Nexus terminal gateway...");
    const outputNormalizer = new TerminalOutputNormalizer();

    const writeOutput = (data: string) => {
      const normalized = outputNormalizer.push(data);
      if (normalized) terminal.write(normalized);
    };

    const writeStatusLine = (message: string) => {
      const pending = outputNormalizer.flush();
      if (pending) terminal.write(pending);
      terminal.write(formatTerminalStatusLine(message, terminal.buffer.active.cursorX));
    };

    let socket: WebSocket | null = null;
    const scheduleReconnect = (message: string) => {
      if (disposed) return;
      writeStatusLine(message);
      const reconnectAttempt = reconnectAttemptsRef.current;
      if (reconnectAttempt < 4) {
        reconnectAttemptsRef.current = reconnectAttempt + 1;
        const reconnectDelay = 1000 * 2 ** reconnectAttempt;
        writeStatusLine(`reconnecting in ${reconnectDelay / 1000}s`);
        reconnectTimer = window.setTimeout(() => {
          if (!disposed) setReconnectKey((value) => value + 1);
        }, reconnectDelay);
      } else {
        onDisconnectedRef.current();
      }
    };

    const connect = async () => {
      try {
        const { ticket } = await api.workspaceTerminalTicket(apiContext, activeSessionId);
        if (disposed) return;
        socket = new WebSocket(
          workspaceTerminalWebSocketUrl(apiContext, activeSessionId),
          ["nexus-terminal-v1", `nexus-terminal-ticket.${ticket}`],
        );
        socketRef.current = socket;
        socket.onopen = () => {
          if (!disposed) setSocketStatus("connected");
          reconnectAttemptsRef.current = 0;
          onConnectedRef.current();
          const dimensions = terminal.rows && terminal.cols ? { rows: terminal.rows, cols: terminal.cols } : { rows: 34, cols: 120 };
          socket?.send(JSON.stringify({ type: "resize", ...dimensions }));
        };
        socket.onmessage = (event) => {
          try {
            const message = JSON.parse(String(event.data)) as { type?: string; data?: string; status?: string };
            if (message.type === "output") writeOutput(message.data ?? "");
            if (message.type === "status") writeStatusLine(message.status ?? "status");
          } catch {
            writeOutput(String(event.data));
          }
        };
        socket.onclose = () => {
          if (disposed) return;
          socketRef.current = null;
          setSocketStatus("disconnected");
          scheduleReconnect("terminal disconnected");
        };
        socket.onerror = () => {
          if (!disposed) setSocketStatus("error");
          writeStatusLine("terminal websocket error");
        };
      } catch (error) {
        if (disposed) return;
        setSocketStatus("error");
        const message = error instanceof Error ? error.message : "terminal authorization failed";
        scheduleReconnect(`terminal connection failed: ${message}`);
      }
    };
    void connect();

    const inputDisposable = terminal.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data }));
      }
    });
    terminal.attachCustomKeyEventHandler((event) => {
      if (event.type === "keydown" && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c" && terminal.hasSelection()) {
        void copyWorkspaceTerminalText(terminal.getSelection(), "Terminal selection copied");
        return false;
      }
      return true;
    });
    const resizeDisposable = terminal.onResize((size) => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
      }
    });
    const resizeHandler = () => fitAddon.fit();
    const commandHandler = (event: Event) => {
      const command = (event as CustomEvent<string>).detail;
      if (command && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data: command }));
      }
    };
    window.addEventListener("resize", resizeHandler);
    window.addEventListener("nexus-terminal-command", commandHandler);

    return () => {
      disposed = true;
      window.removeEventListener("resize", resizeHandler);
      window.removeEventListener("nexus-terminal-command", commandHandler);
      inputDisposable.dispose();
      resizeDisposable.dispose();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close();
      const pending = outputNormalizer.flush();
      if (pending) terminal.write(pending);
      terminal.dispose();
      if (xtermRef.current === terminal) xtermRef.current = null;
      socketRef.current = null;
    };
  }, [activeSessionId, apiContext, isCompactViewport, reconnectKey]);

  if (!selectedConnection) {
    return <div className="rounded-md border border-dashed border-line bg-slate-50 p-6 text-sm text-muted">{t("Select or pair a Computer Runtime first.")}</div>;
  }
  if (!activeSession) {
    return (
      <div className="grid min-h-[320px] place-items-center rounded-lg border border-dashed border-line bg-slate-50 p-6 text-center sm:min-h-[480px] xl:min-h-[640px]">
        <div>
          <Terminal className="mx-auto text-muted" size={34} />
          <div className="mt-3 text-sm font-semibold text-ink">{t("No terminal session")}</div>
          <div className="mt-1 text-sm text-muted">{t("Open a terminal to configure CLI tools and MCP servers on this target.")}</div>
        </div>
      </div>
    );
  }
  if (isCompactViewport) {
    return (
      <div className="grid min-h-[260px] place-items-center rounded-lg border border-line bg-slate-50 p-6 text-center">
        <div className="max-w-sm">
          <MonitorUp className="mx-auto text-slate-600" size={34} />
          <div className="mt-3 text-base font-semibold text-ink">{t("Session is")}{" "}{formatWorkspaceStatus(activeSession.status)}</div>
          <div className="mt-2 text-sm leading-6 text-muted">{t("Session status and controls remain available on mobile. Open Nexus on a tablet or desktop to interact with the live terminal.")}</div>
        </div>
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950">
      <div className="flex min-h-9 items-center justify-between gap-3 border-b border-slate-800 px-3 text-xs text-slate-300" aria-live="polite">
        <span className="inline-flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${
              socketStatus === "connected"
                ? "bg-emerald-400"
                : socketStatus === "connecting"
                  ? "animate-pulse bg-amber-300"
                  : "bg-rose-400"
            }`}
            aria-hidden="true"
          />
          {formatWorkspaceStatus(socketStatus)}
        </span>
        <div className="flex shrink-0 items-center gap-2">
          <button
            className="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-slate-700 px-2.5 font-semibold text-slate-200 hover:bg-slate-800"
            type="button"
            onClick={() => {
              const terminal = xtermRef.current;
              const transcript = terminal ? terminalBufferText(terminal) : "";
              if (transcript) void copyWorkspaceTerminalText(transcript, "Terminal output copied");
              else toast.info("No terminal output to copy yet");
            }}
            aria-label={t("Copy terminal output")}
            title={t("Copy the complete terminal transcript")}
          >
            <Copy size={13} />{t("Copy output")}</button>
        {["disconnected", "error"].includes(socketStatus) && (
          <button
            className="font-semibold text-white underline underline-offset-2"
            type="button"
            onClick={() => {
              reconnectAttemptsRef.current = 0;
              setReconnectKey((value) => value + 1);
            }}
          >{t("Reconnect")}</button>
        )}
        </div>
      </div>
      <div ref={terminalRef} className="h-[444px] overflow-hidden xl:h-[604px]" />
    </div>
  );
}

function terminalBufferText(terminal: XTerm) {
  const lines: string[] = [];
  const buffer = terminal.buffer.active;
  for (let index = 0; index < buffer.length; index += 1) {
    const line = buffer.getLine(index);
    if (!line) continue;
    const text = line.translateToString(true);
    if (line.isWrapped && lines.length) lines[lines.length - 1] += text;
    else lines.push(text);
  }
  while (lines.length && !lines.at(-1)) lines.pop();
  return lines.join("\n");
}

async function copyWorkspaceTerminalText(value: string, successMessage: string) {
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
      toast.error(t("Unable to copy terminal output"));
      return;
    }
  }
  toast.success(successMessage);
}





function LegacyToolSetupPanel({
  apiContext,
  isContextReady,
  form,
  selectedConnection,
  activeSession,
  isDetecting,
  isOpeningSession,
  onChange,
  onClose,
  onDetectTools,
  onOpenSession
}: {
  apiContext: ApiContext;
  isContextReady: boolean;
  form: ToolSetupForm;
  selectedConnection: WorkspaceConnection | undefined;
  activeSession: WorkspaceTerminalSession | null;
  isDetecting: boolean;
  isOpeningSession: boolean;
  onChange: (form: ToolSetupForm) => void;
  onClose: () => void;
  onDetectTools: () => void;
  onOpenSession: () => void;
}) {
  useLocale();
  const toolStatus = getToolStatus(activeSession);
  const queryClient = useQueryClient();
  const [isReviewing, setIsReviewing] = useState(false);
  const [lastAppliedAt, setLastAppliedAt] = useState("");
  const providerRuntimes = useQuery({
    queryKey: ["provider-runtimes", apiContext],
    queryFn: () => api.providerRuntimes(apiContext),
    enabled: isContextReady
  });
  const agents = useQuery({
    queryKey: ["agents", apiContext],
    queryFn: () => api.agents(apiContext),
    enabled: isContextReady
  });
  const runtimeData = providerRuntimes.data ?? [];
  const agentData = agents.data ?? [];
  const selectedRuntime = runtimeData.find((runtime) => runtime.id === form.selectedRuntimeId);
  const selectedAgent = agentData.find((agent) => agent.id === form.selectedAgentId);
  const toolConfig = useQuery({
    queryKey: ["workspace-tool-config", apiContext, activeSession?.id, form.selectedTool],
    queryFn: () => api.workspaceToolConfig(apiContext, activeSession?.id || "", form.selectedTool),
    enabled: isContextReady && !!activeSession && form.selectedTool === "codex"
  });

  useEffect(() => {
    if (!form.selectedRuntimeId && runtimeData[0]) {
      onChange({ ...form, selectedRuntimeId: runtimeData[0].id });
    }
  }, [form, onChange, runtimeData]);

  const applyToolConfig = useMutation({
    mutationFn: (body: Parameters<typeof api.applyWorkspaceToolConfig>[2]) => {
      if (!activeSession) throw new Error("Open a terminal first.");
      return api.applyWorkspaceToolConfig(apiContext, activeSession.id, body);
    },
    onSuccess: async (result, variables) => {
      queryClient.setQueryData(
        ["workspace-tool-config", apiContext, activeSession?.id, "codex"],
        result
      );
      setIsReviewing(false);
      setLastAppliedAt(new Date().toISOString());
      toast.success(toolOperationLabel(variables.operation));
      await queryClient.invalidateQueries({ queryKey: ["workspace-tool-config"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to apply tool config"))
  });
  const rollbackToolConfig = useMutation({
    mutationFn: () => {
      if (!activeSession) throw new Error("Open a terminal first.");
      return api.rollbackWorkspaceToolConfig(apiContext, activeSession.id, "codex");
    },
    onSuccess: async (result) => {
      queryClient.setQueryData(
        ["workspace-tool-config", apiContext, activeSession?.id, "codex"],
        result
      );
      toast.success(t("Rolled back Codex config"));
      await queryClient.invalidateQueries({ queryKey: ["workspace-tool-config"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to rollback tool config"))
  });
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !applyToolConfig.isPending && !rollbackToolConfig.isPending) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [applyToolConfig.isPending, onClose, rollbackToolConfig.isPending]);

  const remoteConfig = toolConfig.data;
  const toolItem = toolStatus?.tools?.[form.selectedTool];
  const selectedRuntimeModel = selectedRuntime ? runtimeCanonicalModel(selectedRuntime) : "";
  const primaryOperation = selectedAgent ? "full_profile" : "api_only";
  const configStatus =
    !activeSession
      ? { label: t("Session required"), detail: t("Open a terminal session before reading or changing remote configuration."), tone: "slate" }
      : toolStatus?.error
        ? { label: t("Needs attention"), detail: toolStatus.error, tone: "rose" }
        : toolItem && !toolItem.installed
          ? { label: t("Tool missing"), detail: t("{{0}} was not detected on this target.", { 0: toolLabel(form.selectedTool) }), tone: "amber" }
          : toolConfig.isLoading
            ? { label: t("Reading configuration"), detail: t("Checking the managed Codex profile on this target."), tone: "slate" }
            : remoteConfig && !remoteConfig.exists
              ? { label: t("Setup required"), detail: t("The Nexus-managed Codex profile has not been created yet."), tone: "amber" }
              : remoteConfig && selectedRuntimeModel && remoteConfig.model !== selectedRuntimeModel
                ? { label: t("Changes available"), detail: t("The remote profile currently uses {{0}}.", { 0: remoteConfig.model || "no model" }), tone: "amber" }
                : remoteConfig?.api_configured
                  ? { label: t("Configured"), detail: t("The managed Codex profile is connected to a Nexus provider runtime."), tone: "emerald" }
                  : { label: t("Setup required"), detail: t("Choose a provider runtime to connect Codex to Nexus."), tone: "amber" };
  const configTone =
    configStatus.tone === "emerald"
      ? "border-emerald-200 bg-emerald-50 text-emerald-900"
      : configStatus.tone === "rose"
        ? "border-rose-200 bg-rose-50 text-rose-900"
        : configStatus.tone === "amber"
          ? "border-amber-200 bg-amber-50 text-amber-900"
          : "border-line bg-slate-50 text-slate-800";

  function sendCommand(command: string) {
    window.dispatchEvent(new CustomEvent("nexus-terminal-command", { detail: command }));
    navigator.clipboard?.writeText(command).catch(() => undefined);
    toast.info(activeSession ? t("Command copied; paste it into the terminal if it was not inserted automatically.") : t("Open a terminal first. Command copied."));
  }

  function downloadGeneratedConfig() {
    if (!remoteConfig?.content) return;
    downloadTextFile("nexus.config.toml", remoteConfig.content, "text/plain");
    toast.success(t("Config file downloaded"));
  }

  function applyOperation(operation: "api_only" | "mcp_add" | "mcp_remove" | "mcp_replace" | "mcp_clear" | "full_profile", extra: Record<string, string> = {}) {
    applyToolConfig.mutate({
      tool: "codex",
      operation,
      provider_runtime_id: form.selectedRuntimeId || undefined,
      agent_id: form.selectedAgentId || undefined,
      ...extra
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-950/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tool-setup-title"
      onMouseDown={() => {
        if (!applyToolConfig.isPending && !rollbackToolConfig.isPending) onClose();
      }}
    >
      <section
        className="flex h-full w-full max-w-xl flex-col overflow-hidden border-l border-line bg-white shadow-overlay"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-line px-4 py-4 sm:px-6">
          <div className="min-w-0">
            <h2 id="tool-setup-title" className="text-lg font-semibold tracking-[-0.02em] text-ink">{t("Tool setup")}</h2>
            <p className="mt-1 truncate text-sm text-muted">
              {selectedConnection ? `${selectedConnection.name} · ${formatComputerConnection(selectedConnection)}` : t("No Computer selected")}
            </p>
          </div>
          <button
            className="btn h-10 w-10 shrink-0 p-0"
            type="button"
            aria-label={t("Close tool setup")}
            disabled={applyToolConfig.isPending || rollbackToolConfig.isPending}
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">
          <section className="border-b border-line px-4 py-5 sm:px-6" aria-labelledby="available-tools-title">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 id="available-tools-title" className="text-sm font-semibold text-ink">{t("Tools on this target")}</h3>
                <p className="mt-1 text-xs leading-5 text-muted">
                  {toolStatus?.checked_at ? t("Checked {{0}}", { 0: new Date(toolStatus.checked_at).toLocaleString(getLocale()) }) : t("Tool detection begins when a terminal session is open.")}
                </p>
              </div>
              <button className="btn h-9 shrink-0 px-3 text-xs" type="button" disabled={!activeSession || isDetecting} onClick={onDetectTools}>
                {isDetecting ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}{t("Refresh status")}</button>
            </div>
            <div className="mt-4 grid overflow-hidden rounded-xl border border-line">
              <ToolStatusRow
                label={t("Codex")}
                item={toolStatus?.tools?.codex}
                selected={form.selectedTool === "codex"}
                onSelect={() => {
                  setIsReviewing(false);
                  onChange({ ...form, selectedTool: "codex" });
                }}
              />
              <ToolStatusRow
                label={t("Claude Code")}
                item={toolStatus?.tools?.claude_code}
                selected={form.selectedTool === "claude_code"}
                onSelect={() => {
                  setIsReviewing(false);
                  onChange({ ...form, selectedTool: "claude_code" });
                }}
              />
            </div>
          </section>

          {!activeSession ? (
            <section className="px-4 py-6 sm:px-6">
              <div className="rounded-xl border border-dashed border-line bg-slate-50 p-5">
                <Terminal size={24} className="text-slate-600" />
                <h3 className="mt-3 text-sm font-semibold text-ink">{t("Open a terminal session first")}</h3>
                <p className="mt-1 text-sm leading-6 text-muted">{t("Nexus reads and applies tool configuration through the selected Computer Runtime session.")}</p>
                <button className="btn btn-primary mt-4" type="button" disabled={!selectedConnection || isOpeningSession} onClick={onOpenSession}>
                  {isOpeningSession ? <Loader2 size={16} className="animate-spin" /> : <Terminal size={16} />}{t("Open terminal")}</button>
              </div>
            </section>
          ) : form.selectedTool === "claude_code" ? (
            <section className="px-4 py-6 sm:px-6">
              <div className={`rounded-xl border p-4 ${toolItem?.installed ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
                <div className="flex items-start gap-3">
                  {toolItem?.installed ? <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-700" /> : <AlertTriangle size={20} className="mt-0.5 shrink-0 text-amber-700" />}
                  <div>
                    <h3 className="text-sm font-semibold text-ink">{toolItem?.installed ? t("Claude Code detected") : t("Claude Code is not installed")}</h3>
                    <p className="mt-1 text-sm leading-6 text-muted">
                      {toolItem?.installed
                        ? t("Nexus can verify this CLI, but managed remote configuration is currently available for Codex only.")
                        : t("Installation is managed outside Nexus. Install Claude Code on this target, then refresh tool status.")}
                    </p>
                  </div>
                </div>
              </div>
              <button className="btn mt-4" type="button" onClick={() => sendCommand("claude --version\n")}>
                <Send size={16} />{t("Run verification command")}</button>
            </section>
          ) : (
            <>
              <section className="border-b border-line px-4 py-5 sm:px-6" aria-labelledby="configuration-status-title">
                <h3 id="configuration-status-title" className="text-sm font-semibold text-ink">{t("Configuration status")}</h3>
                <div className={`mt-3 rounded-xl border p-4 ${configTone}`} aria-live="polite">
                  <div className="flex items-start gap-3">
                    {configStatus.tone === "emerald" ? <CheckCircle2 size={19} className="mt-0.5 shrink-0" /> : configStatus.tone === "slate" ? <Loader2 size={19} className={`mt-0.5 shrink-0 ${toolConfig.isLoading ? "animate-spin" : ""}`} /> : <AlertTriangle size={19} className="mt-0.5 shrink-0" />}
                    <div>
                      <div className="text-sm font-semibold">{configStatus.label}</div>
                      <div className="mt-1 text-xs leading-5 opacity-80">{configStatus.detail}</div>
                    </div>
                  </div>
                </div>
                {toolConfig.isError && (
                  <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800" role="alert">
                    {toolConfig.error instanceof Error ? toolConfig.error.message : t("Remote configuration could not be read.")}
                  </div>
                )}
              </section>

              <section className="border-b border-line px-4 py-5 sm:px-6" aria-labelledby="configure-codex-title">
                <div>
                  <h3 id="configure-codex-title" className="text-sm font-semibold text-ink">{t("Configure Codex")}</h3>
                  <p className="mt-1 text-xs leading-5 text-muted">{t("Choose the Nexus runtime Codex should use. Connecting an Agent is optional.")}</p>
                </div>
                <div className="mt-4 grid gap-4">
                  <Field label={t("Provider runtime")}>
                    <select
                      className="select"
                      value={form.selectedRuntimeId}
                      onChange={(event) => {
                        setIsReviewing(false);
                        onChange({ ...form, selectedRuntimeId: event.target.value });
                      }}
                    >
                      {runtimeData.length === 0 && <option value="">{t("No provider runtime available")}</option>}
                      {runtimeData.map((runtime) => (
                        <option key={runtime.id} value={runtime.id}>
                          {runtime.name} / {runtimeCanonicalModel(runtime)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={t("Agent MCP (optional)")}>
                    <select
                      className="select"
                      value={form.selectedAgentId}
                      onChange={(event) => {
                        setIsReviewing(false);
                        onChange({ ...form, selectedAgentId: event.target.value });
                      }}
                    >
                      <option value="">{t("Keep existing MCP servers")}</option>
                      {agentData.map((agent) => (
                        <option key={agent.id} value={agent.id}>
                          {agent.name} / {agent.current_version || t("draft")}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                {(providerRuntimes.isError || agents.isError) && (
                  <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900">{t("Some setup options could not be loaded. Refresh the page before applying changes.")}</div>
                )}

                {isReviewing && (
                  <div className="mt-4 rounded-xl border border-slate-300 bg-slate-50 p-4" aria-label={t("Configuration change preview")}>
                    <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">{t("Review changes")}</div>
                    <div className="mt-3 grid gap-3 text-sm">
                      <Detail label={t("Target")} value={selectedConnection?.name || "Selected target"} />
                      <Detail label={t("Provider API")} value={`${selectedRuntime?.name || "Not selected"} / ${selectedRuntimeModel || "-"}`} />
                      <Detail
                        label={t("Agent MCP")}
                        value={selectedAgent ? `Replace current servers with ${selectedAgent.name}` : "Keep all current MCP servers"}
                      />
                      <Detail label={t("Safety")} value={remoteConfig?.exists ? "Create a timestamped backup before writing" : "Create a new managed profile"} />
                    </div>
                  </div>
                )}
                {lastAppliedAt && <div className="mt-3 text-xs text-emerald-700">{t("Last applied")}{" "}{new Date(lastAppliedAt).toLocaleString(getLocale())}.</div>}
              </section>

              <section className="border-b border-line px-4 py-5 sm:px-6" aria-labelledby="mcp-servers-title">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 id="mcp-servers-title" className="text-sm font-semibold text-ink">{t("Current MCP servers")}</h3>
                    <p className="mt-1 text-xs text-muted">{remoteConfig?.mcp_servers.length || 0}{" "}{t("connected")}</p>
                  </div>
                  <button className="btn h-9 px-3 text-xs" type="button" disabled={toolConfig.isFetching} onClick={() => void toolConfig.refetch()}>
                    {toolConfig.isFetching ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}{t("Reload config")}</button>
                </div>
                {remoteConfig?.mcp_servers.length ? (
                  <div className="mt-3 divide-y divide-line overflow-hidden rounded-xl border border-line">
                    {remoteConfig.mcp_servers.map((server) => (
                      <div key={server.name} className="flex items-center justify-between gap-3 px-3 py-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium text-ink">{server.name}</div>
                          <div className="mt-0.5 truncate text-xs text-muted">{server.url || server.type || t("Managed server")}</div>
                        </div>
                        <button
                          className="btn h-8 px-2 text-xs"
                          type="button"
                          disabled={applyToolConfig.isPending}
                          onClick={() => applyOperation("mcp_remove", { mcp_server_name: server.name })}
                        >
                          <Trash2 size={14} />{t("Remove")}</button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-3 rounded-lg border border-dashed border-line bg-slate-50 p-3 text-sm text-muted">{t("No MCP servers are configured. Provider API settings are unaffected.")}</div>
                )}
              </section>

              <section className="px-4 py-5 sm:px-6">
                <details className="group rounded-xl border border-line bg-white">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-ink">{t("Advanced settings")}<ChevronRight size={16} className="transition-transform group-open:rotate-90" />
                  </summary>
                  <div className="border-t border-line px-4 py-4">
                    <div className="grid gap-3 text-sm">
                      <Detail label={t("Config path")} value={remoteConfig?.path || "~/.codex/nexus.config.toml"} />
                      <Detail label={t("Profile")} value={remoteConfig?.profile || "nexus"} />
                      <Detail label={t("Model")} value={remoteConfig?.model || "Not configured"} />
                      <Detail label={t("Provider")} value={remoteConfig?.model_provider || "Not configured"} />
                      <Detail label={t("Base URL")} value={remoteConfig?.base_url || "Not configured"} />
                      <Detail label={t("Wire API")} value={remoteConfig?.wire_api || "Not configured"} />
                    </div>
                    <div className="mt-4 grid gap-2 sm:grid-cols-2">
                      <button className="btn justify-start" type="button" disabled={!remoteConfig?.content} onClick={downloadGeneratedConfig}>
                        <Download size={16} />{t("Download config")}</button>
                      <button className="btn justify-start" type="button" onClick={() => sendCommand(`codex --profile ${remoteConfig?.profile || "nexus"}\n`)}>
                        <Send size={16} />{t("Launch Codex")}</button>
                      <button
                        className="btn justify-start"
                        type="button"
                        disabled={!selectedRuntime || applyToolConfig.isPending}
                        onClick={() => applyOperation("api_only")}
                      >
                        <Save size={16} />{t("Apply API only")}</button>
                      <button
                        className="btn justify-start"
                        type="button"
                        disabled={!selectedAgent || applyToolConfig.isPending}
                        onClick={() => applyOperation("mcp_add")}
                      >
                        <Plus size={16} />{t("Add selected MCP")}</button>
                      <button
                        className="btn justify-start"
                        type="button"
                        disabled={!selectedAgent || applyToolConfig.isPending}
                        onClick={() => {
                          if (window.confirm(`Replace all MCP servers on "${selectedConnection?.name || "this target"}" with "${selectedAgent?.name}"?`)) {
                            applyOperation("mcp_replace");
                          }
                        }}
                      >
                        <Save size={16} />{t("Replace all MCP")}</button>
                      <button
                        className="btn justify-start border-rose-200 text-rose-700 hover:bg-rose-50"
                        type="button"
                        disabled={applyToolConfig.isPending || !remoteConfig?.mcp_servers.length}
                        onClick={() => {
                          if (window.confirm(`Clear all MCP servers on "${selectedConnection?.name || "this target"}"? Provider API settings will be preserved.`)) {
                            applyOperation("mcp_clear");
                          }
                        }}
                      >
                        <Trash2 size={16} />{t("Clear all MCP")}</button>
                    </div>

                    <div className="mt-5 border-t border-line pt-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">{t("Recovery")}</div>
                      <p className="mt-1 text-xs leading-5 text-muted">
                        {remoteConfig?.rollback_available
                          ? t("Latest backup: {{0}}", { 0: remoteConfig.backup_created_at ? new Date(remoteConfig.backup_created_at).toLocaleString(getLocale()) : "available" })
                          : t("No backup is available for this session yet.")}
                      </p>
                      <button
                        className="btn mt-3 justify-start"
                        type="button"
                        disabled={rollbackToolConfig.isPending || !remoteConfig?.rollback_available}
                        onClick={() => {
                          const backupTime = remoteConfig?.backup_created_at ? new Date(remoteConfig.backup_created_at).toLocaleString(getLocale()) : "the latest backup";
                          if (window.confirm(`Roll back Codex configuration on "${selectedConnection?.name || "this target"}" to ${backupTime}?`)) {
                            rollbackToolConfig.mutate();
                          }
                        }}
                      >
                        {rollbackToolConfig.isPending ? <Loader2 size={16} className="animate-spin" /> : <RotateCcw size={16} />}{t("Roll back latest backup")}</button>
                    </div>
                  </div>
                </details>
              </section>
            </>
          )}
        </div>
        {activeSession && form.selectedTool === "codex" && (
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-line bg-white px-4 py-3 shadow-[0_-8px_24px_rgba(15,23,42,0.06)] sm:px-6">
            {isReviewing ? (
              <>
                <button className="btn" type="button" disabled={applyToolConfig.isPending} onClick={() => setIsReviewing(false)}>{t("Cancel")}</button>
                <button
                  className="btn btn-primary"
                  type="button"
                  disabled={!selectedRuntime || applyToolConfig.isPending}
                  onClick={() => applyOperation(primaryOperation)}
                >
                  {applyToolConfig.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                  {applyToolConfig.isPending ? t("Applying…") : t("Apply changes")}
                </button>
              </>
            ) : (
              <button
                className="btn btn-primary w-full justify-center"
                type="button"
                disabled={!selectedRuntime || applyToolConfig.isPending || providerRuntimes.isError}
                onClick={() => setIsReviewing(true)}
              >{t("Review changes")}<ChevronRight size={16} />
              </button>
            )}
          </footer>
        )}
      </section>
    </div>
  );
}

function buildConfigCommands({
  shell,
  tool,
  credentials,
  mcpExport,
  selectedAgent,
  selectedRuntime
}: {
  shell: "bash" | "powershell";
  tool: ToolSetupForm["selectedTool"];
  credentials: ProviderRuntimeCredentials | null;
  mcpExport: Record<string, unknown> | null;
  selectedAgent?: Agent;
  selectedRuntime?: ProviderRuntimeAccount;
}) {
  const applyDisabled = !credentials;
  const config = buildToolConfig({ tool, credentials, mcpExport, selectedAgent, selectedRuntime });
  if (shell === "powershell") {
    return [
      {
        title: tool === "codex" ? t("Generate Codex profile {{0}}", { 0: config.profileName }) : t("Apply {{0}} config with backup", { 0: toolLabel(tool) }),
        kind: "apply",
        disabled: applyDisabled,
        command: buildPowerShellApplyCommand(tool, config)
      },
      ...(tool === "codex"
        ? [
            {
              title: t("Launch codex --profile {{0}}", { 0: config.profileName }),
              kind: "launch",
              disabled: applyDisabled,
              command: buildPowerShellLaunchCommand(config)
            }
          ]
        : []),
      {
        title: t("Rollback {{0}} latest backup", { 0: toolLabel(tool) }),
        kind: "rollback",
        disabled: false,
        command: buildPowerShellRollbackCommand(tool, config)
      }
    ];
  }
  return [
    {
      title: tool === "codex" ? t("Generate Codex profile {{0}}", { 0: config.profileName }) : t("Apply {{0}} config with backup", { 0: toolLabel(tool) }),
      kind: "apply",
      disabled: applyDisabled,
      command: buildBashApplyCommand(tool, config)
    },
    ...(tool === "codex"
      ? [
          {
            title: t("Launch codex --profile {{0}}", { 0: config.profileName }),
            kind: "launch",
            disabled: applyDisabled,
            command: buildBashLaunchCommand(config)
          }
        ]
      : []),
    {
      title: t("Rollback {{0}} latest backup", { 0: toolLabel(tool) }),
      kind: "rollback",
      disabled: false,
      command: buildBashRollbackCommand(tool, config)
    }
  ];
}

function configDownloadName(tool: ToolSetupForm["selectedTool"], config: ReturnType<typeof buildToolConfig>) {
  return tool === "codex" ? `${config.profileName}.config.toml` : "settings.json";
}

function toolOperationLabel(operation: string) {
  const labels: Record<string, string> = {
    api_only: "Applied API provider",
    full_profile: "Rebuilt Codex profile",
    mcp_add: "Added MCP server",
    mcp_replace: "Replaced MCP servers",
    mcp_remove: "Removed MCP server",
    mcp_clear: "Cleared MCP servers"
  };
  return labels[operation] || t("Applied tool config");
}

function envDownloadName(tool: ToolSetupForm["selectedTool"], config: ReturnType<typeof buildToolConfig>, shell: "bash" | "powershell") {
  if (tool === "codex") {
    return shell === "powershell" ? `${config.profileName}.ps1` : `${config.profileName}.env`;
  }
  return shell === "powershell" ? "nexus-provider.ps1" : "nexus-provider.env";
}

function configDownloadMime(tool: ToolSetupForm["selectedTool"]) {
  return tool === "codex" ? "text/plain" : "application/json";
}

function downloadTextFile(fileName: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function buildToolConfig({
  tool,
  credentials,
  mcpExport,
  selectedAgent,
  selectedRuntime
}: {
  tool: ToolSetupForm["selectedTool"];
  credentials: ProviderRuntimeCredentials | null;
  mcpExport: Record<string, unknown> | null;
  selectedAgent?: Agent;
  selectedRuntime?: ProviderRuntimeAccount;
}) {
  const baseUrl = credentials?.gateway_api_base_url || credentials?.runtime_api_base_url || "";
  const apiKey = credentials?.gateway_api_key || credentials?.runtime_api_key || "";
  const model = credentials?.recommended_model || credentials?.canonical_model || (selectedRuntime ? runtimeCanonicalModel(selectedRuntime) : "");
  if (tool === "codex") {
    const profileName = codexProfileName({ selectedRuntime, selectedAgent });
    return {
      profileName,
      targetPath: `$HOME/.codex/${profileName}.config.toml`,
      windowsTargetPath: `$env:USERPROFILE\\.codex\\${profileName}.config.toml`,
      content: buildCodexToml({ baseUrl, apiKey, model, mcpExport, selectedAgent }),
      envPath: "",
      windowsEnvPath: "",
      envContent: "",
      windowsEnvContent: ""
    };
  }
  return {
    profileName: "",
    targetPath: "$HOME/.claude/settings.json",
    windowsTargetPath: "$env:USERPROFILE\\.claude\\settings.json",
    content: buildClaudeSettingsJson({ baseUrl, apiKey, mcpExport }),
    envPath: "$HOME/.claude/nexus-provider.env",
    windowsEnvPath: "$env:USERPROFILE\\.claude\\nexus-provider.ps1",
    envContent: `export ANTHROPIC_AUTH_TOKEN=${shQuote(apiKey)}\nexport ANTHROPIC_BASE_URL=${shQuote(baseUrl)}\n`,
    windowsEnvContent: `$env:ANTHROPIC_AUTH_TOKEN=${psQuote(apiKey)}\n$env:ANTHROPIC_BASE_URL=${psQuote(baseUrl)}\n`
  };
}

function buildCodexToml({
  baseUrl,
  apiKey,
  model,
  mcpExport,
  selectedAgent
}: {
  baseUrl: string;
  apiKey: string;
  model: string;
  mcpExport: Record<string, unknown> | null;
  selectedAgent?: Agent;
}) {
  const server = firstMcpServer(mcpExport);
  const lines = [
    `model = "${tomlEscape(model)}"`,
    `model_provider = "nexus"`,
    "",
    `[model_providers.nexus]`,
    `name = "Nexus"`,
    `base_url = "${tomlEscape(baseUrl)}"`,
    `wire_api = "responses"`,
    `experimental_bearer_token = "${tomlEscape(apiKey)}"`,
    ""
  ];
  if (server?.url) {
    const serverName = tomlKey(selectedAgent?.name || server.name || "nexus_agent");
    lines.push(`[mcp_servers.${serverName}]`, `url = "${tomlEscape(server.url)}"`);
    Object.entries(server.headers || {}).forEach(([key, value]) => {
      lines.push(`headers.${tomlKey(key)} = "${tomlEscape(String(value))}"`);
    });
    lines.push("");
  }
  return lines.join("\n");
}

function buildClaudeSettingsJson({
  baseUrl,
  apiKey,
  mcpExport
}: {
  baseUrl: string;
  apiKey: string;
  mcpExport: Record<string, unknown> | null;
}) {
  return JSON.stringify(
    {
      env: {
        ANTHROPIC_BASE_URL: baseUrl,
        ANTHROPIC_AUTH_TOKEN: apiKey
      },
      mcpServers: extractMcpServers(mcpExport)
    },
    null,
    2
  );
}

function buildBashApplyCommand(
  tool: ToolSetupForm["selectedTool"],
  config: { profileName: string; targetPath: string; content: string; envPath: string; envContent: string }
) {
  const dir = tool === "codex" ? "$HOME/.codex" : "$HOME/.claude";
  const fileName = tool === "codex" ? `${config.profileName}.config.toml` : "claude-settings.json";
  const envWrite = config.envPath
    ? `cat > "${config.envPath}" <<'EOF'
${config.envContent}
EOF
`
    : "";
  return `set -e
stamp="$(date +%Y%m%d%H%M%S)"
backup="$HOME/.nexus/config-backups/$stamp"
mkdir -p "$backup" "${dir}"
if [ -f "${config.targetPath}" ]; then cp "${config.targetPath}" "$backup/${fileName}"; fi
cat > "${config.targetPath}" <<'EOF'
${config.content}
EOF
${envWrite}
echo "Backup: $backup"
${tool === "codex" ? `echo "Run: codex --profile ${config.profileName}"` : `echo "Rollback command is available from Nexus Tool Setup."`}
`;
}

function buildBashRollbackCommand(tool: ToolSetupForm["selectedTool"], config: { profileName: string; targetPath: string }) {
  const target = tool === "codex" ? config.targetPath : "$HOME/.claude/settings.json";
  const fileName = tool === "codex" ? `${config.profileName}.config.toml` : "claude-settings.json";
  return `set -e
latest="$(ls -td "$HOME/.nexus/config-backups/"* 2>/dev/null | head -n 1 || true)"
if [ -z "$latest" ]; then echo "No backup found for ${toolLabel(tool)}."; exit 1; fi
backup_file="$latest/${fileName}"
if [ ! -f "$backup_file" ]; then echo "No backup file found for ${toolLabel(tool)}."; exit 1; fi
cp "$backup_file" "${target}"
echo "Restored ${target} from $latest"
`;
}

function buildPowerShellApplyCommand(
  tool: ToolSetupForm["selectedTool"],
  config: { profileName: string; windowsTargetPath: string; content: string; windowsEnvPath: string; windowsEnvContent: string }
) {
  const dir = tool === "codex" ? "$env:USERPROFILE\\.codex" : "$env:USERPROFILE\\.claude";
  const fileName = tool === "codex" ? `${config.profileName}.config.toml` : "claude-settings.json";
  const envWrite = config.windowsEnvPath
    ? `@'
${config.windowsEnvContent}
'@ | Set-Content -Encoding UTF8 ${psQuoteLiteral(config.windowsEnvPath)}
`
    : "";
  return `$ErrorActionPreference = 'Stop'
$Stamp = Get-Date -Format 'yyyyMMddHHmmss'
$Backup = Join-Path $env:USERPROFILE ".nexus\\config-backups\\$Stamp"
New-Item -ItemType Directory -Force -Path $Backup, ${psQuoteLiteral(dir)} | Out-Null
if (Test-Path ${psQuoteLiteral(config.windowsTargetPath)}) { Copy-Item ${psQuoteLiteral(config.windowsTargetPath)} (Join-Path $Backup ${psQuote(fileName)}) -Force }
@'
${config.content}
'@ | Set-Content -Encoding UTF8 ${psQuoteLiteral(config.windowsTargetPath)}
${envWrite}
Write-Host "Backup: $Backup"
${tool === "codex" ? `Write-Host "Run: codex --profile ${config.profileName}"` : `Write-Host "Rollback command is available from Nexus Tool Setup."`}
`;
}

function buildPowerShellRollbackCommand(tool: ToolSetupForm["selectedTool"], config: { profileName: string; windowsTargetPath: string }) {
  const target = tool === "codex" ? config.windowsTargetPath : "$env:USERPROFILE\\.claude\\settings.json";
  const fileName = tool === "codex" ? `${config.profileName}.config.toml` : "claude-settings.json";
  return `$ErrorActionPreference = 'Stop'
$Root = Join-Path $env:USERPROFILE ".nexus\\config-backups"
$Latest = Get-ChildItem $Root -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
if ($null -eq $Latest -or -not (Test-Path (Join-Path $Latest.FullName ${psQuote(fileName)}))) { throw "No backup found for ${toolLabel(tool)}." }
Copy-Item (Join-Path $Latest.FullName ${psQuote(fileName)}) ${psQuoteLiteral(target)} -Force
Write-Host "Restored ${target} from $($Latest.FullName)"
`;
}

function buildBashLaunchCommand(config: { profileName: string; envPath: string }) {
  return config.envPath ? `source "${config.envPath}"
codex --profile ${shQuote(config.profileName)}
` : `codex --profile ${shQuote(config.profileName)}
`;
}

function buildPowerShellLaunchCommand(config: { profileName: string; windowsEnvPath: string }) {
  return config.windowsEnvPath ? `. ${psQuoteLiteral(config.windowsEnvPath)}
codex --profile ${psQuoteLiteral(config.profileName)}
` : `codex --profile ${psQuoteLiteral(config.profileName)}
`;
}

function ToolStatusRow({
  label,
  item,
  selected,
  onSelect
}: {
  label: string;
  item?: { installed?: boolean; command?: string; path?: string; version?: string };
  selected: boolean;
  onSelect: () => void;
}) {
  useLocale();
  const status = item ? (item.installed ? "installed" : "missing") : "unknown";
  return (
    <button
      className={`flex min-h-16 w-full items-center gap-3 border-b border-line px-3 py-3 text-left last:border-b-0 ${
        selected ? "bg-blue-50/70" : "bg-white hover:bg-slate-50"
      }`}
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
    >
      <span
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
          status === "installed"
            ? "bg-emerald-100 text-emerald-700"
            : status === "missing"
              ? "bg-amber-100 text-amber-700"
              : "bg-slate-100 text-slate-500"
        }`}
      >
        {status === "installed" ? <CheckCircle2 size={18} /> : <Terminal size={18} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="font-medium text-ink">{label}</span>
          <span
            className={
              status === "installed"
                ? "text-xs font-semibold text-emerald-700"
                : status === "missing"
                  ? "text-xs font-semibold text-amber-700"
                  : "text-xs font-semibold text-muted"
            }
          >
            {status === "installed" ? t("Installed") : status === "missing" ? t("Not installed") : t("Not checked")}
          </span>
        </span>
        <span className="mt-1 block truncate text-xs text-muted">
          {item?.installed ? item.version || item.path || item.command || t("Ready") : item?.command ? t("Command: {{0}}", { 0: item.command }) : t("Open a session to check")}
        </span>
      </span>
      <ChevronRight size={16} className={selected ? "text-brand" : "text-slate-400"} />
    </button>
  );
}


function inferSessionShell(connection: WorkspaceConnection) {
  const shell = inferConfigShell(connection, null);
  return shell === "powershell" ? "powershell" : "auto";
}

function inferConfigShell(connection: WorkspaceConnection | undefined, session: WorkspaceTerminalSession | null): "bash" | "powershell" {
  if (session?.shell === "powershell") return "powershell";
  const facts = connection?.metadata?.last_facts;
  if (facts && typeof facts === "object") {
    const osName = String((facts as Record<string, unknown>).os ?? "").toLowerCase();
    const shell = String((facts as Record<string, unknown>).shell ?? "").toLowerCase();
    if (["powershell", "cmd"].includes(shell) || ["windows", "mingw", "msys", "cygwin", "win32"].some((marker) => osName.includes(marker))) {
      return "powershell";
    }
  }
  return "bash";
}

function toolLabel(tool: ToolSetupForm["selectedTool"]) {
  return tool === "codex" ? t("Codex") : t("Claude Code");
}

function codexProfileName(_context: {
  selectedRuntime?: ProviderRuntimeAccount;
  selectedAgent?: Agent;
}) {
  return "nexus";
}

function extractMcpServers(mcpExport: Record<string, unknown> | null): Record<string, unknown> {
  const servers = mcpExport?.mcpServers;
  return servers && typeof servers === "object" && !Array.isArray(servers) ? (servers as Record<string, unknown>) : {};
}

function firstMcpServer(mcpExport: Record<string, unknown> | null): { name?: string; url?: string; headers?: Record<string, unknown> } | null {
  const servers = extractMcpServers(mcpExport);
  const [name, value] = Object.entries(servers)[0] ?? [];
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const server = value as Record<string, unknown>;
  const headers = server.headers && typeof server.headers === "object" && !Array.isArray(server.headers) ? (server.headers as Record<string, unknown>) : {};
  return {
    name,
    url: String(server.url ?? ""),
    headers
  };
}

function TargetsList({
  connections,
  sessions,
  selectedConnection,
  isLoading,
  isTesting,
  testingConnectionId,
  search,
  filter,
  onSelect,
  onTest,
  onViewDetails,
  onRevoke,
  onDelete,
  onAdd
}: {
  connections: WorkspaceConnection[];
  sessions: WorkspaceTerminalSession[];
  selectedConnection: WorkspaceConnection | undefined;
  isLoading: boolean;
  isTesting: boolean;
  testingConnectionId: string;
  search: string;
  filter: "all" | "attention";
  onSelect: (connection: WorkspaceConnection) => void;
  onTest: (connection: WorkspaceConnection) => void;
  onViewDetails: (connection: WorkspaceConnection) => void;
  onRevoke: (connection: WorkspaceConnection) => void;
  onDelete: (connection: WorkspaceConnection) => void;
  onAdd: () => void;
}) {
  useLocale();
  if (isLoading) {
    return (
      <div className="grid gap-1 p-2" aria-label={t("Loading Computers")}>
        {[0, 1, 2].map((item) => (
          <div key={item} className="animate-pulse rounded-lg px-3 py-3">
            <div className="h-4 w-2/3 rounded bg-slate-200" />
            <div className="mt-2 h-3 w-full rounded bg-slate-100" />
            <div className="mt-3 h-3 w-4/5 rounded bg-slate-100" />
          </div>
        ))}
      </div>
    );
  }
  if (connections.length === 0) {
    return (
      <div className="m-3 rounded-lg border border-dashed border-line bg-white p-5 text-center">
        <Server className="mx-auto text-muted" size={24} />
        <div className="mt-3 text-sm font-semibold text-ink">{t("No Computers paired")}</div>
        <p className="mt-1 text-xs leading-5 text-muted">{t("Pair Nexus Computer Runtime to open terminals and configure agent tools without inbound SSH.")}</p>
        <button className="btn btn-primary mt-4" type="button" onClick={onAdd}>
          <Plus size={15} />{t("Pair Computer")}</button>
      </div>
    );
  }

  const normalizedSearch = search.trim().toLowerCase();
  const visibleConnections = connections
    .filter((connection) => {
      const matchesSearch =
        !normalizedSearch ||
        [connection.name, connection.runtime?.platform, connection.runtime?.browser_name]
          .join(" ")
          .toLowerCase()
          .includes(normalizedSearch);
      const matchesFilter = filter === "all" || targetNeedsAttention(connection);
      return matchesSearch && matchesFilter;
    })
    .sort((left, right) => {
      if (left.id === selectedConnection?.id) return -1;
      if (right.id === selectedConnection?.id) return 1;
      const healthOrder = Number(targetNeedsAttention(left)) - Number(targetNeedsAttention(right));
      if (healthOrder !== 0) return healthOrder;
      return left.name.localeCompare(right.name);
    });

  if (visibleConnections.length === 0) {
    return (
      <div className="m-3 rounded-lg border border-dashed border-line bg-white p-5 text-center">
        <Search className="mx-auto text-muted" size={22} />
        <div className="mt-3 text-sm font-semibold text-ink">{t("No matching targets")}</div>
        <p className="mt-1 text-xs text-muted">{t("Clear the search or switch the health filter.")}</p>
      </div>
    );
  }

  return (
    <div className="grid p-2">
      {visibleConnections.map((connection) => {
        const selected = selectedConnection?.id === connection.id;
        const activeSessions = sessions.filter(
          (session) => session.connection_id === connection.id && ["created", "active"].includes(session.status.toLowerCase())
        ).length;
        return (
          <div
            key={connection.id}
            className={`group relative grid grid-cols-[minmax(0,1fr)_auto] items-start rounded-lg border px-3 py-3 transition-colors ${
              selected ? "border-sky-300 bg-sky-50" : "border-transparent hover:border-line hover:bg-white"
            }`}
          >
            <button className="min-w-0 pr-1 text-left" type="button" onClick={() => onSelect(connection)}>
              <div className="flex min-w-0 items-center gap-2">
                <Server size={16} className={`shrink-0 ${selected ? "text-sky-700" : "text-muted"}`} />
                <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{connection.name}</span>
              </div>
              <div className="mt-1 truncate text-xs text-muted">
                {formatComputerConnection(connection)}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted">
                <TargetHealthBadge connection={connection} />
                <span>{connection.connection_type === "runtime" ? t("Outbound Runtime") : t("Legacy SSH · Disabled")}</span>
                <span aria-hidden="true">·</span>
                <span>{connection.last_test_at ? formatRelativeTime(connection.last_test_at) : t("Never checked")}</span>
                <span aria-hidden="true">·</span>
                <span>{activeSessions}{" "}{t("active session")}{getLocale() === "zh-CN" ? "" : activeSessions === 1 ? "" : "s"}</span>
              </div>
              {targetNeedsAttention(connection) && connection.last_test_error && (
                <div className="mt-2 line-clamp-2 text-xs leading-5 text-rose-700">{connection.last_test_error}</div>
              )}
            </button>

            <div className="ml-1 flex items-center">
              <details className="relative">
                <summary
                  className="inline-flex h-10 w-10 cursor-pointer list-none items-center justify-center rounded-lg text-muted hover:bg-white hover:text-ink [&::-webkit-details-marker]:hidden"
                  aria-label={t("Actions for {{0}}", { 0: connection.name })}
                  title={t("Target actions")}
                >
                  <MoreHorizontal size={18} />
                </summary>
                <div className="absolute right-0 top-11 z-20 w-48 rounded-lg border border-line bg-white p-1.5 shadow-overlay">
                  <button className="flex min-h-10 w-full items-center gap-2 rounded-md px-3 text-left text-sm text-ink hover:bg-slate-50" type="button" onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    onViewDetails(connection);
                  }}>
                    <Eye size={15} />{t("View details")}</button>
                  <button className="flex min-h-10 w-full items-center gap-2 rounded-md px-3 text-left text-sm text-ink hover:bg-slate-50" type="button" onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    onTest(connection);
                  }} disabled={isTesting}>
                    {isTesting && testingConnectionId === connection.id ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}{t("Refresh status")}</button>
                  {connection.connection_type === "runtime" && !connection.runtime?.revoked && <button className="flex min-h-10 w-full items-center gap-2 rounded-md px-3 text-left text-sm text-amber-800 hover:bg-amber-50" type="button" onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    onRevoke(connection);
                  }}>
                    <ShieldCheck size={15} />{t("Revoke Runtime")}</button>}
                  <div className="my-1 border-t border-line" />
                  <button className="flex min-h-10 w-full items-center gap-2 rounded-md px-3 text-left text-sm text-rose-700 hover:bg-rose-50" type="button" onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    onDelete(connection);
                  }}>
                    <Trash2 size={15} />{t("Delete Computer")}</button>
                </div>
              </details>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TargetDetailsDrawer({
  connection,
  sessions,
  isTesting,
  onClose,
  onRename,
  onPairAgain,
  onRevoke,
  onTest,
  onDelete
}: {
  connection: WorkspaceConnection;
  sessions: WorkspaceTerminalSession[];
  isTesting: boolean;
  onClose: () => void;
  onRename: () => void;
  onPairAgain: () => void;
  onRevoke: () => void;
  onTest: () => void;
  onDelete: () => void;
}) {
  useLocale();
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const facts = connection.metadata?.last_facts;
  const factRecord = facts && typeof facts === "object" ? (facts as Record<string, unknown>) : {};
  const historyValue = connection.metadata?.health_history;
  const history = Array.isArray(historyValue) ? (historyValue as Array<Record<string, unknown>>) : [];
  const activeSessions = sessions.filter((session) => ["created", "active"].includes(session.status.toLowerCase()));

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/35" role="dialog" aria-modal="true" aria-labelledby="target-details-title" onMouseDown={onClose}>
      <div className="flex h-full w-full max-w-lg flex-col border-l border-line bg-white shadow-overlay" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="target-details-title" className="truncate text-lg font-semibold text-ink">{connection.name}</h2>
              <TargetHealthBadge connection={connection} />
            </div>
            <div className="mt-1 truncate text-sm text-muted">{formatComputerConnection(connection)}</div>
          </div>
          <button className="btn h-10 w-10 p-0" type="button" onClick={onClose} aria-label={t("Close target details")}>
            <X size={17} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto bg-slate-50/50 p-4 sm:p-5">
          {connection.last_test_error && (
            <div className="mb-4 flex gap-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900" role="alert">
              <AlertTriangle className="mt-0.5 shrink-0" size={17} />
              <div>
                <div className="font-semibold">{t("Connection needs attention")}</div>
                <div className="mt-1 leading-5 text-rose-700">{connection.last_test_error}</div>
              </div>
            </div>
          )}

          <section className="rounded-xl border border-line bg-white p-4">
            <h3 className="text-sm font-semibold text-ink">{t("Connection")}</h3>
            <div className="mt-4 grid grid-cols-2 gap-4">
              <Detail label={t("Connection")} value={connection.connection_type === "runtime" ? "Outbound WSS" : "Legacy SSH · Disabled"} />
              <Detail label={t("Active sessions")} value={String(activeSessions.length)} />
              <Detail label={t("Operating system")} value={String(connection.runtime?.platform || factRecord.os || "Not detected")} />
              <Detail label={t("Shell")} value={String(factRecord.shell || "Not detected")} />
            </div>
            {connection.connection_type === "runtime" && <div className="mt-4 border-t border-line pt-4 text-xs leading-5 text-muted">{t("The device identity is held by the current operating-system user. Nexus Cloud stores only its public key.")}</div>}
          </section>

          {connection.connection_type === "runtime" && (
            <section className="mt-4 rounded-xl border border-line bg-white p-4">
              <h3 className="text-sm font-semibold text-ink">{t("Runtime controls")}</h3>
              <p className="mt-1 text-xs leading-5 text-muted">{t("Run these commands as the paired operating-system user.")}</p>
              <div className="mt-3 grid gap-2 font-mono text-xs">
                <code className="rounded-lg bg-slate-950 px-3 py-2.5 text-slate-100">nexus-computer status</code>
                <code className="rounded-lg bg-slate-950 px-3 py-2.5 text-slate-100">nexus-computer restart</code>
                <code className="rounded-lg bg-slate-950 px-3 py-2.5 text-slate-100">nexus-computer logs</code>
              </div>
            </section>
          )}

          <section className="mt-4 rounded-xl border border-line bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-ink">{t("Health history")}</h3>
              <span className="text-xs text-muted">{t("Last")}{" "}{Math.min(history.length, 10)}{" "}{t("checks")}</span>
            </div>
            <div className="mt-3 grid">
              {history.length === 0 && <div className="rounded-lg bg-slate-50 p-3 text-sm text-muted">{t("No recorded checks yet. Recheck this target to establish a baseline.")}</div>}
              {history.slice(0, 10).map((entry, index) => (
                <div key={`${String(entry.checked_at)}-${index}`} className="flex items-start gap-3 border-b border-line py-3 last:border-b-0">
                  <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${entry.status === "succeeded" ? "bg-emerald-500" : "bg-rose-500"}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span className="font-medium text-ink">{entry.status === "succeeded" ? t("Reachable") : t("Needs attention")}</span>
                      <span className="text-xs text-muted">{formatWorkspaceTime(String(entry.checked_at || ""))}</span>
                    </div>
                    {Boolean(entry.error) && <div className="mt-1 line-clamp-2 text-xs leading-5 text-rose-700">{String(entry.error)}</div>}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="mt-4 rounded-xl border border-line bg-white p-4">
            <h3 className="text-sm font-semibold text-ink">{t("Recent sessions")}</h3>
            <div className="mt-3 grid gap-2">
              {sessions.length === 0 && <div className="rounded-lg bg-slate-50 p-3 text-sm text-muted">{t("No sessions have been opened on this target.")}</div>}
              {sessions.slice(0, 5).map((session) => (
                <div key={session.id} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-ink">{session.shell}</div>
                    <div className="mt-0.5 text-xs text-muted">{formatWorkspaceTime(session.updated_at)}</div>
                  </div>
                  <WorkspaceStatusBadge status={session.status} />
                </div>
              ))}
            </div>
          </section>
        </div>

        <footer className="flex flex-wrap items-center gap-2 border-t border-line bg-white px-4 py-3 sm:px-5">
          <button className="btn" type="button" onClick={onRename}>{t("Rename")}</button>
          {(connection.runtime?.revoked || !connection.runtime) && <button className="btn" type="button" onClick={onPairAgain}>{t("Pair again")}</button>}
          {connection.connection_type === "runtime" && !connection.runtime?.revoked && <button className="btn border-amber-200 text-amber-800 hover:bg-amber-50" type="button" onClick={onRevoke}>
            <ShieldCheck size={15} />{t("Revoke Runtime")}</button>}
          <button className="btn" type="button" onClick={onTest} disabled={isTesting}>
            {isTesting ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}{t("Refresh status")}</button>
          <button className="btn ml-auto border-rose-200 text-rose-700 hover:bg-rose-50" type="button" onClick={onDelete}>
            <Trash2 size={15} />{t("Delete Computer")}</button>
        </footer>
      </div>
    </div>
  );
}

function RenameComputerDialog({
  name,
  isSaving,
  onNameChange,
  onClose,
  onSave,
}: {
  name: string;
  isSaving: boolean;
  onNameChange: (name: string) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  useLocale();
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSaving) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSaving, onClose]);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-labelledby="rename-computer-title" onMouseDown={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-line bg-white shadow-overlay" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <h2 id="rename-computer-title" className="text-base font-semibold text-ink">{t("Rename Computer")}</h2>
            <p className="mt-1 text-sm text-muted">{t("This changes the Nexus display name, not the operating-system device name.")}</p>
          </div>
          <button className="btn h-10 w-10 p-0" type="button" onClick={onClose} disabled={isSaving} aria-label={t("Close rename dialog")}><X size={16} /></button>
        </header>
        <div className="p-5">
          <Field label={t("Computer name")}>
            <input className="input" autoFocus maxLength={128} value={name} onChange={(event) => onNameChange(event.target.value)} />
          </Field>
        </div>
        <footer className="flex justify-end gap-2 border-t border-line bg-slate-50/60 px-5 py-4">
          <button className="btn" type="button" onClick={onClose} disabled={isSaving}>{t("Cancel")}</button>
          <button className="btn btn-primary min-w-28" type="button" onClick={onSave} disabled={isSaving || !name.trim()}>{isSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}{isSaving ? t("Saving…") : t("Save")}</button>
        </footer>
      </div>
    </div>
  );
}

function PairComputerModal({
  name,
  workspaceRoot,
  pairing,
  connection,
  isCreating,
  onNameChange,
  onWorkspaceRootChange,
  onClose,
  onCreate,
}: {
  name: string;
  workspaceRoot: string;
  pairing: ComputerRuntimePairing | null;
  connection: WorkspaceConnection | undefined;
  isCreating: boolean;
  onNameChange: (value: string) => void;
  onWorkspaceRootChange: (value: string) => void;
  onClose: () => void;
  onCreate: () => void;
}) {
  useLocale();
  const online = Boolean(connection?.runtime?.online);
  const copy = useApplicationDistribution().computerPairing ?? personalComputerPairing;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-3 sm:p-4" role="dialog" aria-modal="true" aria-labelledby="pair-computer-title" onMouseDown={onClose}>
      <div className="flex max-h-[94vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/50 bg-white shadow-overlay" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div>
            <h2 id="pair-computer-title" className="text-base font-semibold text-ink">{t("Pair Computer")}</h2>
            <p className="mt-1 text-sm leading-6 text-muted">{copy.introduction}</p>
          </div>
          <button className="btn h-10 w-10 p-0" type="button" onClick={onClose} aria-label={t("Close Pair Computer dialog")}><X size={16} /></button>
        </header>
        <div className="flex-1 overflow-y-auto p-5">
          {!pairing ? (
            <div className="grid gap-4">
              <Field label={t("Computer name")}>
                <input className="input" value={name} onChange={(event) => onNameChange(event.target.value)} placeholder={t("My Computer")} />
              </Field>
              <Field label={t("Workspace root")}>
                <input className="input font-mono text-sm" value={workspaceRoot} onChange={(event) => onWorkspaceRootChange(event.target.value)} placeholder={t("~/.nexus")} />
                <p className="mt-1 text-xs leading-5 text-muted">{copy.workspaceDescription}</p>
              </Field>
              <div className="rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm leading-6 text-sky-950">{t("Install")}{" "}<code className="rounded bg-white px-1.5 py-0.5 font-mono text-xs">nexus-agent-sdk[computer,browser]</code>{" "}{t("first. Pairing creates a user-only Ed25519 identity and installs a user-level background service.")}</div>
            </div>
          ) : (
            <div className="grid gap-4">
              <div className={`rounded-xl border p-4 ${online ? "border-emerald-200 bg-emerald-50 text-emerald-950" : "border-amber-200 bg-amber-50 text-amber-950"}`} aria-live="polite">
                <div className="flex items-start gap-3">
                  {online ? <CheckCircle2 className="mt-0.5 shrink-0" size={20} /> : <Loader2 className="mt-0.5 shrink-0 animate-spin" size={20} />}
                  <div>
                    <div className="font-semibold">{online ? t("Computer Runtime connected") : t("Waiting for this Computer")}</div>
                    <p className="mt-1 text-sm leading-6">{online ? copy.connectedDescription(connection?.name || name) : t("Run the command below on the Computer before the pairing link expires.")}</p>
                  </div>
                </div>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">{t("Setup command")}</div>
                <div className="mt-2 flex items-start gap-2 rounded-xl border border-line bg-slate-950 p-3 text-slate-100">
                  <code className="min-w-0 flex-1 break-all font-mono text-xs leading-6">{pairing.setup_command}</code>
                  <button className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/20 hover:bg-white/10" type="button" aria-label={t("Copy setup command")} onClick={async () => {
                    await navigator.clipboard.writeText(pairing.setup_command);
                    toast.success("Setup command copied");
                  }}><Copy size={16} /></button>
                </div>
                <p className="mt-2 text-xs text-muted">{t("Pairing expires")}{" "}{new Date(pairing.expires_at).toLocaleString(getLocale())}{" "}{t("and can only be used once.")}</p>
              </div>
            </div>
          )}
        </div>
        <footer className="flex justify-end gap-2 border-t border-line bg-slate-50/60 px-5 py-4">
          <button className="btn" type="button" onClick={onClose}>{online ? t("Done") : t("Cancel")}</button>
          {!pairing && <button className="btn btn-primary min-w-40" type="button" onClick={onCreate} disabled={isCreating || !name.trim()}>{isCreating ? <Loader2 size={16} className="animate-spin" /> : <MonitorUp size={16} />}{isCreating ? t("Creating link…") : t("Create pairing link")}</button>}
        </footer>
      </div>
    </div>
  );
}

/* Legacy SSH form retained temporarily in source history only. It is excluded
 * from the product bundle and can no longer collect credentials. */
/*
function TargetModal({
  mode,
  existingAuthMode,
  form,
  showPassword,
  validation,
  isValidating,
  isSaving,
  onChange,
  onShowPasswordChange,
  onClose,
  onValidate,
  onSubmit
}: {
  mode: "create" | "edit";
  existingAuthMode?: string;
  form: TargetForm;
  showPassword: boolean;
  validation: WorkspaceConnectionTestResult | null;
  isValidating: boolean;
  isSaving: boolean;
  onChange: (form: TargetForm) => void;
  onShowPasswordChange: (show: boolean) => void;
  onClose: () => void;
  onValidate: () => void;
  onSubmit: () => void;
}) {
  const [fingerprintConfirmed, setFingerprintConfirmed] = useState(false);
  const validationSectionRef = useRef<HTMLElement | null>(null);
  const validationPassed = validation?.status === "succeeded";
  const fingerprint = String(validation?.facts?.host_key_fingerprint || "");
  const formComplete = targetFormComplete(form, mode, existingAuthMode);

  useEffect(() => {
    setFingerprintConfirmed(false);
    if (validation) {
      window.requestAnimationFrame(() => validationSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  }, [validation]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSaving && !isValidating) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSaving, isValidating, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-3 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ssh-target-dialog-title"
      onMouseDown={() => {
        if (!isSaving && !isValidating) onClose();
      }}
    >
      <div className="flex max-h-[94vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/50 bg-white shadow-overlay" onMouseDown={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
          <div>
            <div id="ssh-target-dialog-title" className="text-base font-semibold text-ink">{mode === "create" ? "Add remote target" : "Edit remote target"}</div>
            <div className="mt-1 text-sm text-muted">Verify the SSH connection and host identity before saving.</div>
          </div>
          <button className="btn h-10 w-10 p-0" type="button" onClick={onClose} aria-label="Close remote target dialog" disabled={isSaving || isValidating}>
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          <section aria-labelledby="target-connection-fields">
            <div id="target-connection-fields" className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">Connection</div>
            <div className="mt-3 grid gap-4">
              <Field label="Target name">
                <input
                  className="input"
                  placeholder="Production runner"
                  value={form.name}
                  onChange={(event) => onChange({ ...form, name: event.target.value })}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-[1fr_112px]">
                <Field label="Host">
                  <input
                    className="input"
                    placeholder="runner.example.com"
                    value={form.ssh_host}
                    onChange={(event) => {
                      const sshHost = event.target.value;
                      const priorSuggestion = suggestTargetName(form.ssh_host);
                      const name = mode === "create" && (!form.name.trim() || form.name === priorSuggestion) ? suggestTargetName(sshHost) : form.name;
                      onChange({ ...form, ssh_host: sshHost, name });
                    }}
                  />
                </Field>
                <Field label="Port">
                  <input className="input" type="number" min="1" max="65535" value={form.ssh_port} onChange={(event) => onChange({ ...form, ssh_port: event.target.value })} />
                </Field>
              </div>
              <Field label="SSH user">
                <input className="input" placeholder="ubuntu" value={form.ssh_user} onChange={(event) => onChange({ ...form, ssh_user: event.target.value })} />
              </Field>
            </div>
          </section>

          <section className="mt-5 border-t border-line pt-5" aria-labelledby="target-authentication-fields">
            <div className="flex items-center gap-2">
              <KeyRound size={15} className="text-muted" />
              <div id="target-authentication-fields" className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">Authentication</div>
            </div>
            <div className="mt-3 grid gap-4">
              <Field label="Authentication method">
                <select className="select" value={form.auth_mode} onChange={(event) => onChange({ ...form, auth_mode: event.target.value, private_key: "", password: "" })}>
                  <option value="private_key">SSH key</option>
                  <option value="password">Password</option>
                </select>
              </Field>
              {form.auth_mode === "private_key" ? (
                <Field label={mode === "edit" ? "Replacement SSH private key (optional)" : "SSH private key"}>
                  <textarea
                    className="textarea min-h-32 font-mono text-xs"
                    placeholder={mode === "edit" ? "Leave blank to keep the stored key" : "Paste an OpenSSH or PEM private key"}
                    value={form.private_key}
                    onChange={(event) => onChange({ ...form, private_key: event.target.value })}
                  />
                  <label className="btn mt-2 inline-flex cursor-pointer">
                    <Download size={15} />
                    Load key file
                    <input
                      className="sr-only"
                      type="file"
                      accept=".pem,.key,.ppk,text/plain"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (!file) return;
                        const reader = new FileReader();
                        reader.onload = () => onChange({ ...form, private_key: String(reader.result || "") });
                        reader.readAsText(file);
                        event.currentTarget.value = "";
                      }}
                    />
                  </label>
                </Field>
              ) : (
                <Field label={mode === "edit" ? "Replacement password (optional)" : "Password"}>
                  <div className="relative">
                    <input
                      className="input pr-11"
                      type={showPassword ? "text" : "password"}
                      placeholder={mode === "edit" ? "Leave blank to keep the stored password" : ""}
                      value={form.password}
                      onChange={(event) => onChange({ ...form, password: event.target.value })}
                    />
                    <button
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      className="absolute right-2 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-muted hover:bg-slate-100 hover:text-ink"
                      onClick={() => onShowPasswordChange(!showPassword)}
                      title={showPassword ? "Hide password" : "Show password"}
                      type="button"
                    >
                      {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </Field>
              )}
              <p className="text-xs leading-5 text-muted">Credentials are encrypted at rest. They are never returned by the API or displayed after this dialog closes.</p>
            </div>
          </section>

          {validation && (
            <section ref={validationSectionRef} className={`mt-5 rounded-xl border p-4 ${validationPassed ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50"}`} aria-live="polite">
              <div className="flex items-start gap-3">
                {validationPassed ? <CheckCircle2 className="mt-0.5 shrink-0 text-emerald-700" size={19} /> : <AlertTriangle className="mt-0.5 shrink-0 text-rose-700" size={19} />}
                <div className="min-w-0 flex-1">
                  <div className={`text-sm font-semibold ${validationPassed ? "text-emerald-900" : "text-rose-900"}`}>
                    {validationPassed ? "Connection verified" : "Connection could not be verified"}
                  </div>
                  {validationPassed ? (
                    <>
                      <p className="mt-1 text-xs leading-5 text-emerald-800">Confirm that this fingerprint belongs to the server you intend to connect.</p>
                      <div className="mt-3 rounded-lg bg-white/80 p-3">
                        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted">
                          <Fingerprint size={14} />
                          SSH host fingerprint
                        </div>
                        <div className="mt-2 break-all font-mono text-xs leading-5 text-ink">{fingerprint || "Fingerprint unavailable"}</div>
                      </div>
                      <label className="mt-3 flex cursor-pointer items-start gap-3 text-sm text-emerald-950">
                        <input
                          className="mt-1 h-4 w-4 rounded border-emerald-300 text-accent focus:ring-accent"
                          type="checkbox"
                          checked={fingerprintConfirmed}
                          onChange={(event) => setFingerprintConfirmed(event.target.checked)}
                        />
                        <span>I verified this host fingerprint with the server owner.</span>
                      </label>
                    </>
                  ) : (
                    <>
                      <p className="mt-1 text-xs leading-5 text-rose-800">{validation.error || "Review the host, credentials, and network access, then try again."}</p>
                      <div className="mt-3 grid gap-2">
                        {validation.checks.map((check) => (
                          <div key={check.name} className="flex items-start justify-between gap-3 rounded-lg bg-white/70 px-3 py-2 text-xs">
                            <span className="font-medium text-ink">{check.name}</span>
                            <span className={check.ok ? "text-emerald-700" : "text-rose-700"}>{check.detail}</span>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              </div>
            </section>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-slate-50/60 px-5 py-4">
          <button className="btn" type="button" onClick={onClose} disabled={isSaving || isValidating}>
            Cancel
          </button>
          {!validationPassed ? (
            <button className="btn btn-primary" type="button" onClick={onValidate} disabled={!formComplete || isValidating || isSaving}>
              {isValidating ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
              {isValidating ? "Testing connection…" : validation ? "Test again" : "Test connection"}
            </button>
          ) : (
            <button className="btn btn-primary" type="button" onClick={onSubmit} disabled={!fingerprintConfirmed || isSaving || isValidating}>
              {isSaving ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
              {isSaving ? "Saving…" : mode === "create" ? "Confirm & add target" : "Confirm & save changes"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
*/

function useCompactViewport() {
  const [isCompact, setIsCompact] = useState(() => window.matchMedia("(max-width: 767px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const onChange = () => setIsCompact(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return isCompact;
}

const TARGET_HEALTH_STALE_MS = 15 * 60 * 1000;

function shouldRefreshTargetHealth(connection: WorkspaceConnection) {
  if (connection.connection_type === "runtime") return false;
  if (!connection.last_test_at) return true;
  const checkedAt = new Date(connection.last_test_at).getTime();
  return Number.isNaN(checkedAt) || Date.now() - checkedAt > TARGET_HEALTH_STALE_MS;
}

function targetNeedsAttention(connection: WorkspaceConnection) {
  if (connection.connection_type === "runtime") return !connection.availability?.available;
  if (connection.connection_type === "ssh") return true;
  const status = String(connection.last_test_status || "").toLowerCase();
  return !["succeeded", "success", "passed"].includes(status) || shouldRefreshTargetHealth(connection);
}

function formatTargetHealth(connection: WorkspaceConnection) {
  if (connection.connection_type === "runtime") {
    if (connection.runtime?.revoked) return "Revoked";
    if (connection.runtime?.online) return "Online";
    return connection.runtime ? "Offline" : "Pairing";
  }
  if (connection.connection_type === "ssh") return "Legacy disabled";
  const status = String(connection.last_test_status || "").toLowerCase();
  if (["failed", "error"].includes(status)) return "Needs attention";
  if (!connection.last_test_at || !status || status === "untested") return "Not checked";
  if (shouldRefreshTargetHealth(connection)) return "Check expired";
  if (["succeeded", "success", "passed"].includes(status)) return "Reachable";
  return "Unknown";
}


function TargetHealthBadge({ connection }: { connection: WorkspaceConnection }) {
  useLocale();
  const label = formatTargetHealth(connection);
  const tone =
    ["Reachable", "Online"].includes(label)
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : ["Needs attention", "Offline", "Revoked", "Legacy disabled"].includes(label)
        ? "border-rose-200 bg-rose-50 text-rose-700"
        : label === "Check expired"
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-line bg-slate-50 text-slate-600";
  return <span className={`inline-flex min-h-6 shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${tone}`}>{label}</span>;
}

function formatRelativeTime(value: string) {
  const parsed = new Date(value);
  const timestamp = parsed.getTime();
  if (Number.isNaN(timestamp)) return "Check time unavailable";
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (elapsedSeconds < 45) return "Checked just now";
  const minutes = Math.round(elapsedSeconds / 60);
  if (minutes < 60) return `Checked ${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Checked ${hours} hr${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `Checked ${days} day${days === 1 ? "" : "s"} ago`;
}

function formatWorkspaceStatus(status: string) {
  const normalized = String(status || "").trim().toLowerCase().replace(/[_-]+/g, " ");
  const labels: Record<string, string> = {
    active: "Connected",
    created: "Preparing",
    succeeded: "Ready",
    success: "Ready",
    passed: "Ready",
    untested: "Not tested",
    failed: "Needs attention",
    closed: "Ended",
    disconnected: "Disconnected",
    "not connected": "Not connected"
  };
  return labels[normalized] || normalized.replace(/\b\w/g, (letter) => letter.toUpperCase()) || t("Unknown");
}

function WorkspaceStatusBadge({ status }: { status: string }) {
  useLocale();
  const normalized = String(status || "").toLowerCase();
  const tone =
    ["active", "succeeded", "success", "passed"].includes(normalized)
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : ["created", "pending", "connecting"].includes(normalized)
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : ["failed", "error", "disconnected"].includes(normalized)
          ? "border-rose-200 bg-rose-50 text-rose-700"
          : "border-line bg-slate-50 text-slate-600";

  return (
    <span className={`inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${tone}`}>
      {formatWorkspaceStatus(status)}
    </span>
  );
}

function formatWorkspaceTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(getLocale(), {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function Detail({ label, value }: { label: string; value: string }) {
  useLocale();
  return (
    <div className="min-w-0">
      <div className="label">{label}</div>
      <div className="mt-1 break-all text-sm font-medium text-ink">{value || "-"}</div>
    </div>
  );
}

function shQuote(value: string) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

function psQuote(value: string) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function psQuoteLiteral(value: string) {
  return `"${String(value).replace(/`/g, "``").replace(/"/g, '`"')}"`;
}

function tomlEscape(value: string) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function tomlKey(value: string) {
  const normalized = String(value || "nexus").replace(/[^A-Za-z0-9_-]/g, "_");
  return normalized || "nexus";
}

function runtimeCanonicalModel(runtime: ProviderRuntimeAccount) {
  return runtime.model_offers.find((offer) => offer.status === "confirmed")?.canonical_model_key || runtime.runtime_type;
}
