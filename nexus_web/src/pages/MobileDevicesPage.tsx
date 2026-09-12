import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Camera,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Copy,
  Eye,
  Keyboard,
  Loader2,
  MousePointerClick,
  Plus,
  RefreshCw,
  RotateCw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Smartphone,
  Trash2,
  Wifi,
  WifiOff,
  X
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import type { MobileCommand, MobileDevice, MobileDeviceWithPairingToken } from "../lib/types";
import { compactId, formatDate } from "../lib/format";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";

type CommandAction =
  | "observe"
  | "tap_text"
  | "tap_coordinates"
  | "type_text"
  | "swipe"
  | "press_back"
  | "open_app"
  | "wait_for_state";

type CommandForm = {
  action: CommandAction;
  text: string;
  packageName: string;
  x: string;
  y: string;
  endX: string;
  endY: string;
};

type DeviceView = "overview" | "control" | "activity" | "settings";
type ActivityFilter = "all" | "attention" | "running" | "completed";

const initialCommandForm: CommandForm = {
  action: "observe",
  text: "",
  packageName: "",
  x: "0.5",
  y: "0.8",
  endX: "0.5",
  endY: "0.2"
};

const deviceViews: Array<{ id: DeviceView; label: string; icon: typeof Activity }> = [
  { id: "overview", label: "Overview", icon: Smartphone },
  { id: "control", label: "Control", icon: MousePointerClick },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings }
];

const actionOptions: Array<{ value: CommandAction; label: string; description: string }> = [
  { value: "observe", label: "Refresh screen", description: "Request the latest device observation." },
  { value: "tap_text", label: "Tap visible text", description: "Tap the first matching label on screen." },
  { value: "type_text", label: "Type text", description: "Type into the currently focused field." },
  { value: "open_app", label: "Open Android app", description: "Open an installed app by package name." },
  { value: "press_back", label: "Go back", description: "Press the Android system back button." },
  { value: "wait_for_state", label: "Wait for text", description: "Wait until matching text appears." },
  { value: "tap_coordinates", label: "Advanced: tap coordinates", description: "Tap a normalized screen position." },
  { value: "swipe", label: "Advanced: swipe", description: "Swipe between normalized positions." }
];

export function MobileDevicesPage() {
  const { apiContext, isContextReady, user } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [selectedView, setSelectedView] = useState<DeviceView>("overview");
  const [pairDialogOpen, setPairDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deviceName, setDeviceName] = useState("android-phone");
  const [approvalMode, setApprovalMode] = useState<"manual" | "confirm_high_risk" | "auto">("confirm_high_risk");
  const [pairing, setPairing] = useState<MobileDeviceWithPairingToken | null>(null);
  const [pairingServerUrl, setPairingServerUrl] = useState("");
  const [pairingQrUrl, setPairingQrUrl] = useState("");
  const [showPairingAdvanced, setShowPairingAdvanced] = useState(false);
  const [showAdvancedActions, setShowAdvancedActions] = useState(false);
  const [showRawObservation, setShowRawObservation] = useState(false);
  const [commandForm, setCommandForm] = useState<CommandForm>(initialCommandForm);
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("all");
  const [deviceSearch, setDeviceSearch] = useState("");
  const [mcpExport, setMcpExport] = useState("");
  const [screenshotUrl, setScreenshotUrl] = useState("");
  const [screenshotLoading, setScreenshotLoading] = useState(false);
  const [screenshotError, setScreenshotError] = useState("");

  const canManageAutomaticApproval = Boolean(
    user?.is_superuser || user?.roles?.some((role) => ["owner", "admin", "mobile_admin"].includes(role.toLowerCase()))
  );

  const devices = useQuery({
    queryKey: ["mobile-devices", apiContext.token, apiContext.tenantId, apiContext.projectId],
    queryFn: () => api.mobileDevices(apiContext),
    enabled: isContextReady,
    refetchInterval: 5000
  });
  const aggregateStatus = useQuery({
    queryKey: ["mobile-aggregate-status", apiContext.token, apiContext.tenantId],
    queryFn: () => api.mobileAggregateStatus(apiContext),
    enabled: isContextReady && canManageAutomaticApproval,
    refetchInterval: 15000
  });

  const deviceData = devices.data ?? [];
  const selectedDevice = useMemo(
    () => deviceData.find((device) => device.id === selectedId),
    [deviceData, selectedId]
  );
  const filteredDevices = useMemo(() => {
    const query = deviceSearch.trim().toLowerCase();
    if (!query) return deviceData;
    return deviceData.filter((device) =>
      [device.name, device.current_package, lifecycleLabel(deviceLifecycle(device))]
        .join(" ")
        .toLowerCase()
        .includes(query)
    );
  }, [deviceData, deviceSearch]);

  const commands = useQuery({
    queryKey: ["mobile-commands", apiContext.token, apiContext.tenantId, apiContext.projectId, selectedDevice?.id],
    queryFn: () => api.mobileCommands(apiContext, selectedDevice!.id),
    enabled: Boolean(isContextReady && selectedDevice?.id),
    refetchInterval: 3000
  });

  const commandData = commands.data ?? [];
  const pendingCommands = commandData.filter((command) => command.status === "pending_approval");
  const onlineCount = deviceData.filter((device) => deviceLifecycle(device) === "online").length;
  const attentionCount = deviceData.filter((device) =>
    ["setup_required", "offline", "token_expired", "disabled"].includes(deviceLifecycle(device))
  ).length;
  const awaitingCount = deviceData.filter((device) => deviceLifecycle(device) === "awaiting_pairing").length;
  const runtime = useMemo(() => runtimeHints(), []);
  const pairingDeepLink = pairing ? buildPairingDeepLink(pairing, pairingServerUrl || defaultPairingBaseUrl(runtime)) : "";

  useEffect(() => {
    if (!pairingDeepLink) {
      setPairingQrUrl("");
      return;
    }
    let canceled = false;
    QRCode.toDataURL(pairingDeepLink, { width: 360, margin: 1, errorCorrectionLevel: "M" })
      .then((url) => {
        if (!canceled) setPairingQrUrl(url);
      })
      .catch(() => {
        if (!canceled) setPairingQrUrl("");
      });
    return () => {
      canceled = true;
    };
  }, [pairingDeepLink]);

  useEffect(() => {
    if (!selectedId || selectedDevice) return;
    setSelectedId("");
  }, [selectedDevice, selectedId]);

  useEffect(() => {
    let canceled = false;
    let objectUrl = "";
    setScreenshotUrl("");
    setScreenshotError("");
    if (!selectedDevice?.screenshot_available) {
      setScreenshotLoading(false);
      return;
    }
    setScreenshotLoading(true);
    api.mobileScreenshot(apiContext, selectedDevice.id)
      .then((blob) => {
        if (canceled) return;
        objectUrl = URL.createObjectURL(blob);
        setScreenshotUrl(objectUrl);
      })
      .catch((error) => {
        if (!canceled) {
          setScreenshotError(error instanceof Error ? error.message : "The latest screenshot could not be loaded.");
        }
      })
      .finally(() => {
        if (!canceled) setScreenshotLoading(false);
      });
    return () => {
      canceled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [
    apiContext.projectId,
    apiContext.tenantId,
    apiContext.token,
    selectedDevice?.id,
    selectedDevice?.screenshot_available,
    selectedDevice?.screenshot_captured_at
  ]);

  const createDevice = useMutation({
    mutationFn: () =>
      api.createMobileDevice(apiContext, {
        name: deviceName.trim(),
        platform: "android",
        approval_mode: approvalMode
      }),
    onSuccess: async (device) => {
      setPairing(device);
      setSelectedId(device.id);
      toast.success("Android device record created");
      await queryClient.invalidateQueries({ queryKey: ["mobile-devices"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to create mobile device")
  });

  const rotateToken = useMutation({
    mutationFn: (deviceId: string) => api.rotateMobileDeviceToken(apiContext, deviceId),
    onSuccess: async (device) => {
      setPairing(device);
      setPairingServerUrl("");
      setShowPairingAdvanced(false);
      toast.success("New pairing QR generated");
      await queryClient.invalidateQueries({ queryKey: ["mobile-devices"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to generate pairing QR")
  });

  const updateDevice = useMutation({
    mutationFn: ({ deviceId, body }: { deviceId: string; body: Parameters<typeof api.updateMobileDevice>[2] }) =>
      api.updateMobileDevice(apiContext, deviceId, body),
    onSuccess: async () => {
      toast.success("Device policy updated");
      await queryClient.invalidateQueries({ queryKey: ["mobile-devices"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to update device")
  });

  const deleteDevice = useMutation({
    mutationFn: (deviceId: string) => api.deleteMobileDevice(apiContext, deviceId),
    onSuccess: async () => {
      toast.success("Device deleted");
      setDeleteDialogOpen(false);
      setSelectedId("");
      setPairing(null);
      setMcpExport("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["mobile-devices"] }),
        queryClient.invalidateQueries({ queryKey: ["mobile-commands"] })
      ]);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to delete device")
  });

  const createCommand = useMutation({
    mutationFn: () => {
      if (!selectedDevice) throw new Error("Select a mobile device first.");
      return api.createMobileCommand(apiContext, selectedDevice.id, buildCommandBody(commandForm));
    },
    onSuccess: async (command) => {
      setSelectedView("activity");
      toast.success(command.status === "pending_approval" ? "Action sent for approval" : "Action queued");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["mobile-commands"] }),
        queryClient.invalidateQueries({ queryKey: ["mobile-devices"] })
      ]);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to run action")
  });

  const captureScreen = useMutation({
    mutationFn: (deviceId: string) =>
      api.createMobileCommand(apiContext, deviceId, {
        action: "capture_screen",
        arguments: {},
        ttl_seconds: 180
      }),
    onSuccess: async (command) => {
      toast.success(command.status === "pending_approval" ? "Screen capture sent for approval" : "Screen capture requested");
      await queryClient.invalidateQueries({ queryKey: ["mobile-commands"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to request screen capture")
  });

  const approveCommand = useMutation({
    mutationFn: (commandId: string) => api.approveMobileCommand(apiContext, commandId),
    onSuccess: async () => {
      toast.success("Action approved and queued");
      await queryClient.invalidateQueries({ queryKey: ["mobile-commands"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to approve action")
  });

  const rejectCommand = useMutation({
    mutationFn: (commandId: string) => api.rejectMobileCommand(apiContext, commandId),
    onSuccess: async () => {
      toast.success("Action rejected");
      await queryClient.invalidateQueries({ queryKey: ["mobile-commands"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to reject action")
  });

  const cancelCommand = useMutation({
    mutationFn: (commandId: string) => api.cancelMobileCommand(apiContext, commandId),
    onSuccess: async () => {
      toast.success("Action canceled");
      await queryClient.invalidateQueries({ queryKey: ["mobile-commands"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to cancel action")
  });

  const deleteCommand = useMutation({
    mutationFn: (commandId: string) => api.deleteMobileCommand(apiContext, commandId),
    onSuccess: async () => {
      toast.success("Activity entry removed");
      await queryClient.invalidateQueries({ queryKey: ["mobile-commands"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to remove activity")
  });

  const exportMcp = useMutation({
    mutationFn: (deviceId: string) => api.exportMobileMcp(apiContext, deviceId),
    onSuccess: (result) => {
      setMcpExport(JSON.stringify(result, null, 2));
      toast.success("MCP configuration ready");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Failed to export MCP config")
  });

  function openDevice(device: MobileDevice, preferredView?: DeviceView) {
    setSelectedId(device.id);
    setSelectedView(preferredView ?? "overview");
    setActivityFilter("all");
    setShowRawObservation(false);
    setMcpExport("");
  }

  function closeDevice() {
    setSelectedId("");
    setPairing(null);
    setMcpExport("");
  }

  function beginPairing() {
    setPairing(null);
    setDeviceName("android-phone");
    setApprovalMode("confirm_high_risk");
    setPairingServerUrl("");
    setShowPairingAdvanced(false);
    setPairDialogOpen(true);
  }

  const visibleCommands = commandData.filter((command) => commandMatchesFilter(command, activityFilter));
  const selectedLifecycle = selectedDevice ? deviceLifecycle(selectedDevice) : "awaiting_pairing";
  const actionRisk = inferActionRisk(commandForm);
  const actionDescription = actionOptions.find((option) => option.value === commandForm.action)?.description ?? "";
  const actionReady = commandFormReady(commandForm) && selectedLifecycle === "online";
  const captureInFlight = captureScreen.isPending || commandData.some(
    (command) => command.action === "capture_screen" && ["pending_approval", "queued", "running"].includes(command.status)
  );
  const screenshotSupported = selectedDevice?.capabilities?.screenshot === true;

  return (
    <div className="min-w-0 grid gap-6 overflow-x-hidden">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Mobile</h1>
          <p className="mt-1 text-sm text-muted">Pair Android devices, monitor connection health, and run protected actions.</p>
        </div>
        <button className="btn btn-primary min-h-11 w-fit" onClick={beginPairing}>
          <Plus size={16} />
          Pair Android device
        </button>
      </header>

      <section className="panel min-w-0 overflow-hidden" aria-labelledby="mobile-devices-heading">
        <div className="grid gap-4 border-b border-line px-4 py-4 sm:px-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div>
            <h2 id="mobile-devices-heading" className="text-base font-semibold text-ink">Devices</h2>
            <p className="mt-1 text-sm text-muted">Connection state and the next useful action are kept visible.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SummaryPill label="Total" value={deviceData.length} />
            <SummaryPill label="Online" value={onlineCount} tone={onlineCount > 0 ? "success" : "neutral"} />
            <SummaryPill label="Pairing" value={awaitingCount} />
            <SummaryPill label="Attention" value={attentionCount} tone={attentionCount > 0 ? "warn" : "neutral"} />
            {aggregateStatus.data ? (
              <SummaryPill
                label="Workspace fleet"
                value={`${aggregateStatus.data.online}/${aggregateStatus.data.total} online · ${aggregateStatus.data.busy} busy · ${aggregateStatus.data.failed} failed`}
                tone={aggregateStatus.data.failed > 0 ? "warn" : "neutral"}
              />
            ) : null}
          </div>
        </div>

        <div className="flex flex-col gap-3 border-b border-line bg-slate-50/60 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          {deviceData.length >= 5 ? (
            <label className="relative block w-full sm:max-w-xs">
              <span className="sr-only">Search devices</span>
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" size={16} />
              <input
                className="input min-h-11 pl-9"
                placeholder="Search devices"
                value={deviceSearch}
                onChange={(event) => setDeviceSearch(event.target.value)}
              />
            </label>
          ) : (
            <div className="flex min-h-10 items-center gap-2 text-sm text-muted">
              <RefreshCw size={15} className={devices.isFetching ? "animate-spin" : ""} />
              Live updates every 5 seconds
            </div>
          )}
          <button
            className="btn min-h-11 w-fit"
            onClick={() => void devices.refetch()}
            disabled={devices.isFetching}
            aria-label="Refresh device status"
          >
            <RefreshCw size={16} className={devices.isFetching ? "animate-spin" : ""} />
            Refresh now
          </button>
        </div>

        <div className="min-w-0 p-4 sm:p-5">
          {devices.isLoading ? (
            <DeviceListSkeleton />
          ) : devices.isError ? (
            <EmptyState
              title="Devices could not be loaded"
              description={devices.error instanceof Error ? devices.error.message : "Check your Mobile permissions and try again."}
              action={<button className="btn btn-primary" onClick={() => void devices.refetch()}>Try again</button>}
            />
          ) : filteredDevices.length === 0 && deviceData.length > 0 ? (
            <EmptyState title="No matching devices" description="Clear the search or try another device name." />
          ) : deviceData.length === 0 ? (
            <EmptyState
              title="No Android devices paired"
              description="Create a secure pairing QR, scan it in Nexus Mobile, and wait for the device to come online."
              action={
                <button className="btn btn-primary min-h-11" onClick={beginPairing}>
                  <Plus size={16} />
                  Pair Android device
                </button>
              }
            />
          ) : (
            <>
              <div className="grid gap-3 md:hidden" aria-label="Mobile devices">
                {filteredDevices.map((device) => (
                  <DeviceCard key={device.id} device={device} onOpen={() => openDevice(device)} />
                ))}
              </div>
              <div className="hidden min-w-0 overflow-x-auto rounded-lg border border-line md:block">
                <table className="w-full table-fixed text-left text-sm">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-muted">
                    <tr>
                      <th className="w-[27%] px-4 py-3">Device</th>
                      <th className="w-[19%] px-4 py-3">Connection</th>
                      <th className="w-[23%] px-4 py-3">Current app</th>
                      <th className="w-[18%] px-4 py-3">Last seen</th>
                      <th className="w-[13%] px-4 py-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {filteredDevices.map((device) => (
                      <tr key={device.id} className="bg-white hover:bg-slate-50/70">
                        <td className="px-4 py-3">
                          <button className="min-h-11 min-w-0 text-left" onClick={() => openDevice(device)}>
                            <span className="block truncate font-semibold text-ink hover:text-accent">{device.name}</span>
                            <span className="block truncate font-mono text-xs text-muted">{compactId(device.id)} · Android</span>
                          </button>
                        </td>
                        <td className="px-4 py-3"><LifecycleBadge device={device} /></td>
                        <td className="px-4 py-3">
                          <div className="truncate font-medium text-ink">{friendlyAppName(device.current_package) || "No app reported"}</div>
                          <div className="truncate text-xs text-muted">{device.current_activity || lifecycleDetail(device)}</div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-ink">{formatRelativeTime(device.last_seen_at)}</div>
                          <div className="text-xs text-muted">{device.last_seen_at ? formatDate(device.last_seen_at) : "Waiting for first connection"}</div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button className="btn min-h-10 px-3" onClick={() => openDevice(device)}>
                            {recommendedActionLabel(device)}
                            <ChevronRight size={15} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </section>

      {selectedDevice && (
        <AccessibleDrawer title={`${selectedDevice.name} device workspace`} onClose={closeDevice}>
          <div className="flex h-full min-h-0 flex-col">
            <header className="border-b border-line bg-white px-4 py-4 sm:px-6">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 id="mobile-device-drawer-title" className="truncate text-xl font-semibold text-ink">{selectedDevice.name}</h2>
                    <LifecycleBadge device={selectedDevice} />
                  </div>
                  <p className="mt-1 text-sm text-muted">{lifecycleDetail(selectedDevice)}</p>
                </div>
                <button className="btn h-11 w-11 shrink-0 p-0" onClick={closeDevice} aria-label="Close device workspace" title="Close">
                  <X size={18} />
                </button>
              </div>
              <nav className="mt-4 grid grid-cols-4 gap-1 rounded-lg bg-slate-100 p-1" aria-label="Device workspace sections">
                {deviceViews.map((view) => {
                  const Icon = view.icon;
                  const attention = view.id === "activity" && pendingCommands.length > 0;
                  return (
                    <button
                      key={view.id}
                      className={`relative flex min-h-11 items-center justify-center gap-2 rounded-md px-2 text-xs font-semibold transition-colors sm:text-sm ${
                        selectedView === view.id ? "bg-white text-ink shadow-sm" : "text-muted hover:bg-white/70 hover:text-ink"
                      }`}
                      onClick={() => setSelectedView(view.id)}
                      aria-current={selectedView === view.id ? "page" : undefined}
                    >
                      <Icon size={16} />
                      <span>{view.label}</span>
                      {attention && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-amber-500" aria-label={`${pendingCommands.length} actions need approval`} />}
                    </button>
                  );
                })}
              </nav>
            </header>

            <main className="min-h-0 flex-1 overflow-y-auto bg-canvas p-4 sm:p-6">
              {selectedView === "overview" && (
                <div className="grid gap-5">
                  {["awaiting_pairing", "token_expired"].includes(selectedLifecycle) && (
                    <PairingPanel
                      device={selectedDevice}
                      pairing={pairing?.id === selectedDevice.id ? pairing : null}
                      pairingQrUrl={pairing?.id === selectedDevice.id ? pairingQrUrl : ""}
                      pairingDeepLink={pairing?.id === selectedDevice.id ? pairingDeepLink : ""}
                      pairingServerUrl={pairingServerUrl}
                      runtime={runtime}
                      isRotating={rotateToken.isPending}
                      showAdvanced={showPairingAdvanced}
                      onShowAdvanced={() => setShowPairingAdvanced((current) => !current)}
                      onServerUrlChange={setPairingServerUrl}
                      onGenerate={() => rotateToken.mutate(selectedDevice.id)}
                    />
                  )}

                  <section className="rounded-xl border border-line bg-white" aria-labelledby="device-health-heading">
                    <div className="flex flex-col gap-3 border-b border-line px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <h3 id="device-health-heading" className="font-semibold text-ink">Connection health</h3>
                        <p className="mt-1 text-sm text-muted">The latest heartbeat and device context.</p>
                      </div>
                      <button className="btn min-h-11 w-fit" onClick={() => void devices.refetch()} disabled={devices.isFetching}>
                        <RefreshCw size={16} className={devices.isFetching ? "animate-spin" : ""} />
                        Recheck
                      </button>
                    </div>
                    <div className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-4">
                      <DeviceMetric label="Connection" value={lifecycleLabel(selectedLifecycle)} detail={lifecycleDetail(selectedDevice)} />
                      <DeviceMetric label="Last seen" value={formatRelativeTime(selectedDevice.last_seen_at)} detail={selectedDevice.last_seen_at ? formatDate(selectedDevice.last_seen_at) : "No heartbeat received"} />
                      <DeviceMetric label="Current app" value={friendlyAppName(selectedDevice.current_package) || "Not reported"} detail={selectedDevice.current_package || "The app reports this after pairing"} />
                      <DeviceMetric label="Protection" value={approvalModeLabel(selectedDevice.approval_mode)} detail={approvalModeDetail(selectedDevice.approval_mode)} />
                    </div>
                  </section>

                  {selectedLifecycle === "offline" && (
                    <section className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                      <div className="flex gap-3">
                        <WifiOff className="mt-0.5 shrink-0 text-amber-700" size={20} />
                        <div>
                          <h3 className="font-semibold text-amber-950">Device is offline</h3>
                          <p className="mt-1 text-sm leading-6 text-amber-900">Open Nexus Mobile, confirm network access and Accessibility permission, then return here. Status refreshes automatically.</p>
                          <div className="mt-3 flex flex-wrap gap-2">
                            <button className="btn min-h-11 bg-white" onClick={() => void devices.refetch()}>
                              <RefreshCw size={16} />
                              Check again
                            </button>
                            <button className="btn min-h-11 bg-white" onClick={() => setSelectedView("settings")}>
                              <RotateCw size={16} />
                              Re-pair device
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>
                  )}

                  {selectedLifecycle === "setup_required" && (
                    <section className="rounded-xl border border-amber-200 bg-amber-50 p-4" aria-labelledby="mobile-setup-required-heading">
                      <div className="flex gap-3">
                        <ShieldCheck className="mt-0.5 shrink-0 text-amber-700" size={20} />
                        <div>
                          <h3 id="mobile-setup-required-heading" className="font-semibold text-amber-950">Finish setup on Android</h3>
                          <p className="mt-1 text-sm leading-6 text-amber-900">
                            Pairing is complete. On the phone, open Nexus Mobile, enable Nexus Mobile Control in Accessibility settings, then return here.
                          </p>
                          <div className="mt-3 flex flex-wrap gap-2">
                            <button className="btn min-h-11 bg-white" onClick={() => void devices.refetch()} disabled={devices.isFetching}>
                              <RefreshCw size={16} className={devices.isFetching ? "animate-spin" : ""} />
                              Check permission
                            </button>
                            <button className="btn min-h-11 bg-white" onClick={() => setSelectedView("settings")}>
                              <Settings size={16} />
                              Pairing details
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>
                  )}

                  <section className="grid gap-5 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
                    <ObservationPreview
                      device={selectedDevice}
                      screenshotUrl={screenshotUrl}
                      screenshotLoading={screenshotLoading}
                      screenshotError={screenshotError}
                      capturePending={captureInFlight}
                      canCapture={selectedLifecycle === "online" && screenshotSupported}
                      onCapture={() => captureScreen.mutate(selectedDevice.id)}
                    />
                    <div className="rounded-xl border border-line bg-white p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <h3 className="font-semibold text-ink">Device details</h3>
                          <p className="mt-1 text-sm text-muted">Useful identity and capability information.</p>
                        </div>
                        <button className="btn min-h-11" onClick={() => setSelectedView("control")} disabled={selectedLifecycle !== "online"}>
                          <MousePointerClick size={16} />
                          Open control
                        </button>
                      </div>
                      <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                        <Fact label="Platform" value="Android" />
                        <Fact label="Current activity" value={selectedDevice.current_activity || "Not reported"} />
                        <Fact label="Paired" value={selectedDevice.paired_at ? formatDate(selectedDevice.paired_at) : "Not yet"} />
                        <Fact label="Capabilities" value={capabilitySummary(selectedDevice)} />
                      </dl>
                    </div>
                  </section>
                </div>
              )}

              {selectedView === "control" && (
                <div className="grid gap-5 xl:grid-cols-[minmax(280px,0.85fr)_minmax(0,1.15fr)]">
                  <ObservationPreview
                    device={selectedDevice}
                    prominent
                    screenshotUrl={screenshotUrl}
                    screenshotLoading={screenshotLoading}
                    screenshotError={screenshotError}
                    capturePending={captureInFlight}
                    canCapture={selectedLifecycle === "online" && screenshotSupported}
                    onCapture={() => captureScreen.mutate(selectedDevice.id)}
                  />
                  <section className="rounded-xl border border-line bg-white p-4 sm:p-5" aria-labelledby="mobile-action-heading">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 id="mobile-action-heading" className="font-semibold text-ink">Run an action</h3>
                        <p className="mt-1 text-sm text-muted">Review the target, risk, and approval path before dispatch.</p>
                      </div>
                      <LifecycleBadge device={selectedDevice} />
                    </div>

                    {selectedLifecycle !== "online" && (
                      <div className="mt-4 flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950" role="alert">
                        <WifiOff className="mt-0.5 shrink-0" size={17} />
                        <div>
                          <div className="font-semibold">Actions are paused while this device is offline.</div>
                          <div className="mt-1">Reconnect the Android device before sending a command.</div>
                        </div>
                      </div>
                    )}

                    <div className="mt-5 grid gap-4">
                      <Field label="Action">
                        <select
                          className="select min-h-11"
                          value={commandForm.action}
                          onChange={(event) => setCommandForm((current) => ({ ...initialCommandForm, action: event.target.value as CommandAction }))}
                        >
                          {actionOptions.filter((option) => showAdvancedActions || !option.label.startsWith("Advanced:")).map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                          ))}
                        </select>
                      </Field>
                      <p className="-mt-2 text-sm text-muted">{actionDescription}</p>

                      {actionNeedsText(commandForm.action) && (
                        <Field label={commandForm.action === "type_text" ? "Text to type" : "Text to find"}>
                          <input
                            className="input min-h-11"
                            value={commandForm.text}
                            onChange={(event) => setCommandForm((current) => ({ ...current, text: event.target.value }))}
                          />
                        </Field>
                      )}
                      {commandForm.action === "open_app" && (
                        <Field label="Android package name">
                          <input
                            className="input min-h-11"
                            value={commandForm.packageName}
                            onChange={(event) => setCommandForm((current) => ({ ...current, packageName: event.target.value }))}
                            placeholder="com.android.settings"
                          />
                        </Field>
                      )}
                      {actionNeedsCoordinates(commandForm.action) && (
                        <div className="grid gap-3 sm:grid-cols-2">
                          <Field label="Start X (0–1)">
                            <input className="input min-h-11" inputMode="decimal" value={commandForm.x} onChange={(event) => setCommandForm((current) => ({ ...current, x: event.target.value }))} />
                          </Field>
                          <Field label="Start Y (0–1)">
                            <input className="input min-h-11" inputMode="decimal" value={commandForm.y} onChange={(event) => setCommandForm((current) => ({ ...current, y: event.target.value }))} />
                          </Field>
                          {commandForm.action === "swipe" && (
                            <>
                              <Field label="End X (0–1)">
                                <input className="input min-h-11" inputMode="decimal" value={commandForm.endX} onChange={(event) => setCommandForm((current) => ({ ...current, endX: event.target.value }))} />
                              </Field>
                              <Field label="End Y (0–1)">
                                <input className="input min-h-11" inputMode="decimal" value={commandForm.endY} onChange={(event) => setCommandForm((current) => ({ ...current, endY: event.target.value }))} />
                              </Field>
                            </>
                          )}
                        </div>
                      )}

                      <button className="text-left text-sm font-semibold text-accent underline-offset-4 hover:underline" onClick={() => setShowAdvancedActions((current) => !current)}>
                        {showAdvancedActions ? "Hide coordinate actions" : "Show advanced coordinate actions"}
                      </button>
                    </div>

                    <div className="mt-5 rounded-lg border border-line bg-slate-50 p-4" aria-label="Execution preview">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-semibold text-ink">Execution preview</div>
                        <Badge tone={riskTone(actionRisk)}>{actionRisk} risk</Badge>
                      </div>
                      <dl className="mt-3 grid gap-2 text-sm">
                        <PreviewRow label="Target" value={selectedDevice.name} />
                        <PreviewRow label="Action" value={actionLabel(commandForm.action)} />
                        <PreviewRow label="Approval" value={approvalPreview(selectedDevice.approval_mode, actionRisk)} />
                        <PreviewRow label="Expires" value="3 minutes after submission" />
                      </dl>
                    </div>

                    <button
                      className="btn btn-primary mt-4 min-h-11 w-full sm:w-fit"
                      onClick={() => createCommand.mutate()}
                      disabled={!actionReady || createCommand.isPending}
                    >
                      {createCommand.isPending ? <Loader2 size={16} className="animate-spin" /> : commandIcon(commandForm.action)}
                      {actionRisk === "high" && selectedDevice.approval_mode !== "auto" ? "Send for approval" : "Run action"}
                    </button>
                  </section>
                </div>
              )}

              {selectedView === "activity" && (
                <div className="grid gap-5">
                  {pendingCommands.length > 0 && (
                    <section className="rounded-xl border border-amber-200 bg-amber-50 p-4" aria-labelledby="approval-queue-heading">
                      <div className="flex items-start gap-3">
                        <ClipboardCheck className="mt-0.5 shrink-0 text-amber-700" size={20} />
                        <div className="min-w-0 flex-1">
                          <h3 id="approval-queue-heading" className="font-semibold text-amber-950">{pendingCommands.length} action{pendingCommands.length === 1 ? "" : "s"} need approval</h3>
                          <p className="mt-1 text-sm text-amber-900">Confirm the target and arguments before allowing execution.</p>
                          <div className="mt-3 grid gap-3">
                            {pendingCommands.map((command) => (
                              <ApprovalCard
                                key={command.id}
                                command={command}
                                isBusy={approveCommand.isPending || rejectCommand.isPending}
                                onApprove={() => approveCommand.mutate(command.id)}
                                onReject={() => rejectCommand.mutate(command.id)}
                              />
                            ))}
                          </div>
                        </div>
                      </div>
                    </section>
                  )}

                  <section className="rounded-xl border border-line bg-white" aria-labelledby="activity-history-heading">
                    <div className="grid gap-3 border-b border-line px-4 py-4 sm:flex sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <h3 id="activity-history-heading" className="font-semibold text-ink">Action history</h3>
                        <p className="mt-1 text-sm text-muted">Approvals, execution progress, results, and failures.</p>
                      </div>
                      <div className="flex flex-wrap gap-1" role="group" aria-label="Filter activity">
                        {(["all", "attention", "running", "completed"] as ActivityFilter[]).map((filter) => (
                          <button
                            key={filter}
                            className={`btn min-h-10 px-3 ${activityFilter === filter ? "btn-primary" : ""}`}
                            onClick={() => setActivityFilter(filter)}
                          >
                            {activityFilterLabel(filter)}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="p-4">
                      {commands.isLoading ? (
                        <DeviceListSkeleton />
                      ) : visibleCommands.length === 0 ? (
                        <EmptyState
                          title={commandData.length === 0 ? "No actions yet" : "No actions match this filter"}
                          description={commandData.length === 0 ? "Open Control to run the first protected action." : "Choose another activity filter."}
                          action={commandData.length === 0 ? <button className="btn btn-primary" onClick={() => setSelectedView("control")}>Open Control</button> : undefined}
                        />
                      ) : (
                        <div className="divide-y divide-line">
                          {visibleCommands.map((command) => (
                            <CommandRow
                              key={command.id}
                              command={command}
                              onCancel={() => cancelCommand.mutate(command.id)}
                              onDelete={() => deleteCommand.mutate(command.id)}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </section>
                </div>
              )}

              {selectedView === "settings" && (
                <div className="grid gap-5">
                  <section className="rounded-xl border border-line bg-white p-4 sm:p-5">
                    <h3 className="font-semibold text-ink">Approval policy</h3>
                    <p className="mt-1 text-sm text-muted">The server calculates action risk. Users cannot lower required protection per action.</p>
                    <div className="mt-4 grid gap-3 lg:grid-cols-3">
                      <PolicyOption
                        title="Confirm high risk"
                        description="Low and medium risk actions run directly. High risk actions wait for approval."
                        selected={selectedDevice.approval_mode === "confirm_high_risk"}
                        onSelect={() => updateDevice.mutate({ deviceId: selectedDevice.id, body: { approval_mode: "confirm_high_risk" } })}
                      />
                      <PolicyOption
                        title="Approve every action"
                        description="Every action waits for an administrator. Best for shared or sensitive devices."
                        selected={selectedDevice.approval_mode === "manual"}
                        onSelect={() => updateDevice.mutate({ deviceId: selectedDevice.id, body: { approval_mode: "manual" } })}
                      />
                      {canManageAutomaticApproval ? (
                        <PolicyOption
                          title="Automatic approval"
                          description="Actions run without a manual checkpoint. Restrict this to trusted test devices."
                          selected={selectedDevice.approval_mode === "auto"}
                          danger
                          onSelect={() => updateDevice.mutate({ deviceId: selectedDevice.id, body: { approval_mode: "auto" } })}
                        />
                      ) : selectedDevice.approval_mode === "auto" ? (
                        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                          <div className="font-semibold">Automatic approval is active</div>
                          <div className="mt-1">A Mobile policy administrator must change this setting.</div>
                        </div>
                      ) : null}
                    </div>
                  </section>

                  <section className="rounded-xl border border-line bg-white p-4 sm:p-5">
                    <h3 className="font-semibold text-ink">Pairing and connection</h3>
                    <p className="mt-1 text-sm text-muted">Generating a new QR immediately invalidates the previous device token.</p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button className="btn min-h-11" onClick={() => rotateToken.mutate(selectedDevice.id)} disabled={rotateToken.isPending}>
                        {rotateToken.isPending ? <Loader2 size={16} className="animate-spin" /> : <RotateCw size={16} />}
                        Generate new pairing QR
                      </button>
                      <button className="btn min-h-11" onClick={() => setSelectedView("overview")}>
                        View connection health
                      </button>
                    </div>
                    {pairing?.id === selectedDevice.id && (
                      <div className="mt-5">
                        <PairingPanel
                          device={selectedDevice}
                          pairing={pairing}
                          pairingQrUrl={pairingQrUrl}
                          pairingDeepLink={pairingDeepLink}
                          pairingServerUrl={pairingServerUrl}
                          runtime={runtime}
                          isRotating={rotateToken.isPending}
                          showAdvanced={showPairingAdvanced}
                          onShowAdvanced={() => setShowPairingAdvanced((current) => !current)}
                          onServerUrlChange={setPairingServerUrl}
                          onGenerate={() => rotateToken.mutate(selectedDevice.id)}
                        />
                      </div>
                    )}
                  </section>

                  <details className="rounded-xl border border-line bg-white">
                    <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-3 px-4 font-semibold text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                      Developer integration
                      <ChevronRight size={17} />
                    </summary>
                    <div className="grid gap-4 border-t border-line p-4 xl:grid-cols-[0.9fr_1.1fr]">
                      <div className="grid content-start gap-3">
                        <PairingField label="Device ID" value={selectedDevice.id} />
                        <PairingField label="Project" value={selectedDevice.project_id || "Organization-wide"} />
                        <PairingField label="Token prefix" value={selectedDevice.token_prefix || "Not available"} />
                        <button className="btn min-h-11 w-fit" onClick={() => exportMcp.mutate(selectedDevice.id)}>
                          {exportMcp.isPending ? <Loader2 size={16} className="animate-spin" /> : <Copy size={16} />}
                          Prepare MCP configuration
                        </button>
                      </div>
                      <pre className="max-h-80 min-h-40 overflow-auto rounded-lg bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-100">
                        {mcpExport || "Prepare the MCP configuration to connect this device to a trusted agent."}
                      </pre>
                    </div>
                  </details>

                  <section className="rounded-xl border border-rose-200 bg-white p-4 sm:p-5">
                    <h3 className="font-semibold text-rose-900">Danger zone</h3>
                    <p className="mt-1 text-sm text-rose-700">Delete this device after active and queued actions have completed or been canceled.</p>
                    <button className="btn btn-danger mt-4 min-h-11" onClick={() => setDeleteDialogOpen(true)}>
                      <Trash2 size={16} />
                      Delete device
                    </button>
                  </section>
                </div>
              )}
            </main>
          </div>
        </AccessibleDrawer>
      )}

      {pairDialogOpen && (
        <AccessibleDialog title="Pair an Android device" description="Create a secure QR, scan it in Nexus Mobile, and confirm the first heartbeat." onClose={() => setPairDialogOpen(false)} maxWidth="max-w-3xl">
          <div className="grid gap-6 lg:grid-cols-[minmax(260px,0.75fr)_minmax(0,1.25fr)]">
            <div>
              <ol className="grid gap-2" aria-label="Pairing progress">
                <PairingStep number="1" title="Create device" detail={pairing ? "Complete" : "Name the Android device"} complete={Boolean(pairing)} />
                <PairingStep number="2" title="Scan QR" detail={pairing ? "QR ready" : "Available after creation"} complete={Boolean(pairing && selectedDevice?.paired_at)} active={Boolean(pairing && !selectedDevice?.paired_at)} />
                <PairingStep number="3" title="Grant permissions" detail="Accessibility and screen observation" complete={Boolean(selectedDevice?.capabilities?.accessibility)} active={Boolean(selectedDevice?.paired_at && !selectedDevice?.capabilities?.accessibility)} />
                <PairingStep number="4" title="Confirm online" detail={selectedDevice && deviceLifecycle(selectedDevice) === "online" ? "Device connected" : "Waiting for heartbeat"} complete={Boolean(selectedDevice && deviceLifecycle(selectedDevice) === "online")} />
              </ol>

              {!pairing && (
                <form
                  className="mt-5 grid gap-4"
                  onSubmit={(event) => {
                    event.preventDefault();
                    createDevice.mutate();
                  }}
                >
                  <Field label="Device name">
                    <input className="input min-h-11" value={deviceName} onChange={(event) => setDeviceName(event.target.value)} required />
                  </Field>
                  <Field label="Approval policy">
                    <select className="select min-h-11" value={approvalMode} onChange={(event) => setApprovalMode(event.target.value as typeof approvalMode)}>
                      <option value="confirm_high_risk">Confirm high-risk actions</option>
                      <option value="manual">Approve every action</option>
                      {canManageAutomaticApproval && <option value="auto">Automatic approval · trusted test devices only</option>}
                    </select>
                  </Field>
                  {approvalMode === "auto" && (
                    <RiskNotice text="Automatic approval removes the manual checkpoint. Use it only for isolated, trusted test devices." />
                  )}
                  <button className="btn btn-primary min-h-11 w-full sm:w-fit" disabled={createDevice.isPending || !deviceName.trim()}>
                    {createDevice.isPending ? <Loader2 size={16} className="animate-spin" /> : <Smartphone size={16} />}
                    Create pairing QR
                  </button>
                </form>
              )}
            </div>

            <div className="min-w-0">
              {pairing ? (
                <div className="grid gap-5">
                  {selectedDevice && deviceLifecycle(selectedDevice) === "online" ? (
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5">
                      <div className="flex items-start gap-3">
                        <CheckCircle2 className="mt-0.5 text-emerald-700" size={22} />
                        <div>
                          <h3 className="font-semibold text-emerald-950">Android device connected</h3>
                          <p className="mt-1 text-sm text-emerald-800">The first heartbeat was received. This device is ready for protected actions.</p>
                          <button
                            className="btn btn-primary mt-4 min-h-11"
                            onClick={() => {
                              setPairDialogOpen(false);
                              openDevice(selectedDevice, "overview");
                            }}
                          >
                            Open device
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : selectedDevice && deviceLifecycle(selectedDevice) === "setup_required" ? (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 p-5">
                      <div className="flex items-start gap-3">
                        <ShieldCheck className="mt-0.5 text-amber-700" size={22} />
                        <div>
                          <h3 className="font-semibold text-amber-950">Pairing confirmed — one permission remains</h3>
                          <p className="mt-1 text-sm leading-6 text-amber-900">
                            On Android, tap Enable control and allow Nexus Mobile Control in Accessibility settings. Commands remain blocked until this permission is active.
                          </p>
                          <button className="btn mt-4 min-h-11 bg-white" type="button" onClick={() => void devices.refetch()}>
                            <RefreshCw size={16} />
                            Check permission
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="grid gap-5 sm:grid-cols-[220px_minmax(0,1fr)]">
                      <div className="mx-auto flex h-[220px] w-[220px] items-center justify-center rounded-xl border border-line bg-white p-3">
                        {pairingQrUrl ? <img className="h-full w-full" src={pairingQrUrl} alt="Nexus Mobile Android pairing QR code" /> : <Loader2 size={22} className="animate-spin text-muted" />}
                      </div>
                      <div className="grid content-start gap-3">
                        <div>
                          <h3 className="font-semibold text-ink">Scan with Nexus Mobile</h3>
                          <p className="mt-1 text-sm leading-6 text-muted">Open Nexus Mobile on Android and scan this QR. Keep this screen open until the device reports online.</p>
                        </div>
                        <PairingExpiry value={pairing.pairing_expires_at} />
                        <div className="flex items-center gap-2 text-sm text-muted">
                          <Loader2 size={16} className="animate-spin" />
                          Waiting for the first heartbeat
                        </div>
                        <button className="btn min-h-11 w-fit" type="button" onClick={() => void devices.refetch()}>
                          <RefreshCw size={16} />
                          Check connection
                        </button>
                      </div>
                    </div>
                  )}
                  <button className="text-left text-sm font-semibold text-accent underline-offset-4 hover:underline" type="button" onClick={() => setShowPairingAdvanced((current) => !current)}>
                    {showPairingAdvanced ? "Hide connection details" : "Show connection details"}
                  </button>
                  {showPairingAdvanced && (
                    <div className="grid gap-3 rounded-xl border border-line bg-slate-50 p-4 sm:grid-cols-2">
                      <Field label="Server URL encoded in QR">
                        <input className="input min-h-11" value={pairingServerUrl} onChange={(event) => setPairingServerUrl(event.target.value)} placeholder={runtime.lanBaseUrl} />
                      </Field>
                      <PairingField label="Android emulator URL" value={runtime.emulatorBaseUrl} />
                      <PairingField label="Device ID" value={pairing.id} />
                      <PairingField label="Pairing token" value={pairing.pairing_token} secret />
                      <div className="sm:col-span-2"><PairingField label="Deep link" value={pairingDeepLink} secret /></div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex min-h-72 items-center justify-center rounded-xl border border-dashed border-line bg-slate-50 p-6">
                  <div className="max-w-sm text-center">
                    <ShieldCheck className="mx-auto text-muted" size={30} />
                    <h3 className="mt-3 font-semibold text-ink">QR appears after device creation</h3>
                    <p className="mt-1 text-sm leading-6 text-muted">The token is shown only once and expires if the device does not complete its first pairing in time.</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </AccessibleDialog>
      )}

      {deleteDialogOpen && selectedDevice && (
        <AccessibleDialog title="Delete Android device" description={selectedDevice.name} onClose={() => setDeleteDialogOpen(false)} maxWidth="max-w-lg" layer="z-[70]">
          <div className="grid gap-4">
            <RiskNotice text="Active, queued, and pending approval actions must be completed or canceled before deletion." />
            <div className="rounded-lg border border-line bg-slate-50 p-4">
              <div className="font-semibold text-ink">{selectedDevice.name}</div>
              <div className="mt-1 font-mono text-xs text-muted">{selectedDevice.id}</div>
            </div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button className="btn min-h-11" onClick={() => setDeleteDialogOpen(false)}>Keep device</button>
              <button className="btn btn-danger min-h-11" onClick={() => deleteDevice.mutate(selectedDevice.id)} disabled={deleteDevice.isPending}>
                {deleteDevice.isPending ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                Delete device
              </button>
            </div>
          </div>
        </AccessibleDialog>
      )}
    </div>
  );
}

function DeviceCard({ device, onOpen }: { device: MobileDevice; onOpen: () => void }) {
  return (
    <article className="rounded-xl border border-line bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-ink">{device.name}</h3>
          <p className="mt-0.5 truncate font-mono text-xs text-muted">{compactId(device.id)} · Android</p>
        </div>
        <LifecycleBadge device={device} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">Current app</div>
          <div className="mt-1 truncate font-medium text-ink">{friendlyAppName(device.current_package) || "Not reported"}</div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">Last seen</div>
          <div className="mt-1 font-medium text-ink">{formatRelativeTime(device.last_seen_at)}</div>
        </div>
      </div>
      <p className="mt-3 text-sm leading-6 text-muted">{lifecycleDetail(device)}</p>
      <button className="btn btn-primary mt-4 min-h-11 w-full" onClick={onOpen}>
        {recommendedActionLabel(device)}
        <ChevronRight size={16} />
      </button>
    </article>
  );
}

function LifecycleBadge({ device }: { device: MobileDevice }) {
  const lifecycle = deviceLifecycle(device);
  const config = {
    online: { icon: Wifi, className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
    offline: { icon: WifiOff, className: "border-amber-200 bg-amber-50 text-amber-800" },
    awaiting_pairing: { icon: Smartphone, className: "border-blue-200 bg-blue-50 text-blue-700" },
    setup_required: { icon: ShieldCheck, className: "border-amber-200 bg-amber-50 text-amber-800" },
    token_expired: { icon: AlertTriangle, className: "border-rose-200 bg-rose-50 text-rose-700" },
    disabled: { icon: WifiOff, className: "border-line bg-slate-50 text-slate-600" }
  }[lifecycle] ?? { icon: AlertTriangle, className: "border-line bg-slate-50 text-slate-600" };
  const Icon = config.icon;
  return (
    <span className={`inline-flex min-h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-xs font-semibold ${config.className}`}>
      <Icon size={13} />
      {lifecycleLabel(lifecycle)}
    </span>
  );
}

function PairingPanel({
  device,
  pairing,
  pairingQrUrl,
  pairingDeepLink,
  pairingServerUrl,
  runtime,
  isRotating,
  showAdvanced,
  onShowAdvanced,
  onServerUrlChange,
  onGenerate
}: {
  device: MobileDevice;
  pairing: MobileDeviceWithPairingToken | null;
  pairingQrUrl: string;
  pairingDeepLink: string;
  pairingServerUrl: string;
  runtime: ReturnType<typeof runtimeHints>;
  isRotating: boolean;
  showAdvanced: boolean;
  onShowAdvanced: () => void;
  onServerUrlChange: (value: string) => void;
  onGenerate: () => void;
}) {
  return (
    <section className="rounded-xl border border-blue-200 bg-blue-50/60 p-4 sm:p-5" aria-labelledby="pair-device-heading">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 id="pair-device-heading" className="font-semibold text-blue-950">
            {deviceLifecycle(device) === "token_expired" ? "Pairing QR expired" : "Finish pairing this Android device"}
          </h3>
          <p className="mt-1 text-sm leading-6 text-blue-900">
            {deviceLifecycle(device) === "token_expired" ? "Generate a new QR and scan it within the pairing window." : "Scan the QR in Nexus Mobile and wait for the first heartbeat."}
          </p>
        </div>
        {!pairing && (
          <button className="btn btn-primary min-h-11 shrink-0" onClick={onGenerate} disabled={isRotating}>
            {isRotating ? <Loader2 size={16} className="animate-spin" /> : <RotateCw size={16} />}
            Generate QR
          </button>
        )}
      </div>
      {pairing && (
        <div className="mt-5 grid gap-5 sm:grid-cols-[200px_minmax(0,1fr)]">
          <div className="mx-auto flex h-[200px] w-[200px] items-center justify-center rounded-xl border border-line bg-white p-3">
            {pairingQrUrl ? <img className="h-full w-full" src={pairingQrUrl} alt="Nexus Mobile Android pairing QR code" /> : <Loader2 size={20} className="animate-spin text-muted" />}
          </div>
          <div className="grid content-start gap-3">
            <PairingExpiry value={pairing.pairing_expires_at} />
            <div className="flex items-center gap-2 text-sm text-blue-900">
              <Loader2 size={15} className="animate-spin" />
              Waiting for Nexus Mobile
            </div>
            <button className="btn min-h-11 w-fit bg-white" type="button" onClick={onShowAdvanced}>
              {showAdvanced ? "Hide connection details" : "Connection details"}
            </button>
            {showAdvanced && (
              <div className="grid gap-3">
                <Field label="Server URL encoded in QR">
                  <input className="input min-h-11" value={pairingServerUrl} onChange={(event) => onServerUrlChange(event.target.value)} placeholder={runtime.lanBaseUrl} />
                </Field>
                <PairingField label="Device ID" value={pairing.id} />
                <PairingField label="Pairing token" value={pairing.pairing_token} secret />
                <PairingField label="Deep link" value={pairingDeepLink} secret />
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function ObservationPreview({
  device,
  prominent = false,
  screenshotUrl,
  screenshotLoading,
  screenshotError,
  capturePending,
  canCapture,
  onCapture
}: {
  device: MobileDevice;
  prominent?: boolean;
  screenshotUrl: string;
  screenshotLoading: boolean;
  screenshotError: string;
  capturePending: boolean;
  canCapture: boolean;
  onCapture: () => void;
}) {
  const observationText = observationTextLines(device.last_observation);
  const screenshotSupported = device.capabilities?.screenshot === true;
  return (
    <section className={`rounded-xl border border-line bg-white ${prominent ? "p-5" : "p-4"}`} aria-labelledby={`observation-${device.id}`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 id={`observation-${device.id}`} className="font-semibold text-ink">Latest screen</h3>
          <p className="mt-1 text-sm text-muted">
            {device.screenshot_captured_at
              ? `Image captured ${formatRelativeTime(device.screenshot_captured_at)}`
              : device.last_seen_at
                ? `UI observation received ${formatRelativeTime(device.last_seen_at)}`
                : "No screen data received"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={deviceLifecycle(device) === "online" ? "success" : "muted"}>{friendlyAppName(device.current_package) || "Android"}</Badge>
          <button
            type="button"
            className="btn min-h-10 bg-white"
            onClick={onCapture}
            disabled={!canCapture || capturePending}
            title={
              screenshotSupported
                ? deviceLifecycle(device) === "online"
                  ? "Capture the current screen once"
                  : "The device must be online"
                : "Requires Android 11+ and screenshot permission"
            }
          >
            {capturePending ? <Loader2 size={15} className="animate-spin" /> : <Camera size={15} />}
            {capturePending ? "Capturing" : "Capture screen"}
          </button>
        </div>
      </div>
      <div className={`mx-auto mt-4 overflow-hidden rounded-[1.5rem] border-[6px] border-slate-900 bg-slate-950 shadow-sm ${prominent ? "max-w-sm" : "max-w-xs"}`}>
        <div className="flex h-7 items-center justify-between bg-slate-900 px-4 text-[10px] text-slate-300">
          <span>{deviceLifecycle(device) === "online" ? "Connected" : "Last known state"}</span>
          <span>{friendlyAppName(device.current_package) || "Nexus Mobile"}</span>
        </div>
        <div className="min-h-72 bg-white">
          {screenshotLoading ? (
            <div className="flex min-h-72 items-center justify-center text-sm text-slate-500">
              <Loader2 className="mr-2 animate-spin" size={18} />
              Loading protected image
            </div>
          ) : screenshotUrl ? (
            <img
              src={screenshotUrl}
              alt="Latest Android screen capture"
              className="block h-auto min-h-72 w-full bg-slate-100 object-contain"
            />
          ) : observationText.length > 0 ? (
            <div className="grid gap-2">
              <div className="px-4 pt-4 text-[11px] font-semibold uppercase tracking-wide text-slate-500">UI observation fallback</div>
              <div className="grid gap-2 px-4 pb-4">
              {observationText.slice(0, 12).map((text, index) => (
                <div key={`${text}-${index}`} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800">{text}</div>
              ))}
              </div>
            </div>
          ) : (
            <div className="flex min-h-64 flex-col items-center justify-center text-center">
              <Eye className="text-slate-400" size={26} />
              <div className="mt-3 font-semibold text-slate-700">No screen image</div>
              <div className="mt-1 max-w-52 text-sm leading-5 text-slate-500">
                {screenshotSupported ? "Connect the device and capture the current screen." : "Screen capture requires Android 11 or newer."}
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="mt-3 text-xs leading-5 text-muted">
        {screenshotError
          ? `Image unavailable: ${screenshotError}`
          : "Screenshots are requested manually, blocked on sensitive screens, and removed after 5 minutes."}
      </div>
    </section>
  );
}

function ApprovalCard({ command, isBusy, onApprove, onReject }: { command: MobileCommand; isBusy: boolean; onApprove: () => void; onReject: () => void }) {
  return (
    <article className="rounded-lg border border-amber-200 bg-white p-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-ink">{actionLabel(command.action)}</span>
            <Badge tone={riskTone(command.risk_level)}>{command.risk_level} risk</Badge>
          </div>
          <p className="mt-1 break-words text-sm text-muted">{friendlyArguments(command)}</p>
        </div>
        <div className="flex gap-2">
          <button className="btn min-h-11 flex-1 sm:flex-none" onClick={onReject} disabled={isBusy}>
            <X size={15} />
            Reject
          </button>
          <button className="btn btn-primary min-h-11 flex-1 sm:flex-none" onClick={onApprove} disabled={isBusy}>
            <CheckCircle2 size={15} />
            Approve
          </button>
        </div>
      </div>
    </article>
  );
}

function CommandRow({ command, onCancel, onDelete }: { command: MobileCommand; onCancel: () => void; onDelete: () => void }) {
  const canCancel = ["pending_approval", "queued"].includes(command.status);
  const canDelete = ["succeeded", "failed", "rejected", "canceled"].includes(command.status);
  return (
    <article className="grid gap-3 py-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-ink">{actionLabel(command.action)}</span>
          <Badge tone={riskTone(command.risk_level)}>{command.risk_level} risk</Badge>
        </div>
        <p className="mt-1 break-words text-sm text-muted">{friendlyArguments(command)}</p>
        {command.error && <p className="mt-1 text-sm text-rose-700">{command.error}</p>}
      </div>
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-muted">Status</div>
        <div className="mt-1"><CommandStatus status={command.status} /></div>
      </div>
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-muted">Created</div>
        <div className="mt-1 text-sm text-ink">{formatDate(command.created_at)}</div>
      </div>
      {(canCancel || canDelete) && (
        <div className="flex justify-end">
          {canCancel ? (
            <button className="btn min-h-11" onClick={onCancel}>Cancel</button>
          ) : (
            <button className="btn h-11 w-11 p-0" onClick={onDelete} aria-label={`Remove ${actionLabel(command.action)} activity`} title="Remove activity">
              <Trash2 size={16} />
            </button>
          )}
        </div>
      )}
    </article>
  );
}

function CommandStatus({ status }: { status: string }) {
  const tone = status === "succeeded" ? "success" : status === "failed" || status === "rejected" ? "danger" : status === "pending_approval" || status === "queued" ? "warn" : "info";
  return <Badge tone={tone}>{commandStatusLabel(status)}</Badge>;
}

function PolicyOption({ title, description, selected, danger = false, onSelect }: { title: string; description: string; selected: boolean; danger?: boolean; onSelect: () => void }) {
  return (
    <button
      className={`min-h-28 rounded-lg border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        selected ? (danger ? "border-amber-300 bg-amber-50" : "border-accent bg-blue-50") : "border-line bg-white hover:bg-slate-50"
      }`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <span className="flex items-center gap-2 font-semibold text-ink">
        <span className={`h-4 w-4 rounded-full border-4 ${selected ? "border-accent bg-white" : "border-slate-300 bg-white"}`} />
        {title}
      </span>
      <span className="mt-2 block text-sm leading-5 text-muted">{description}</span>
    </button>
  );
}

function AccessibleDrawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialogRef = useDialogFocus(onClose);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/35" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="h-[100dvh] w-full overflow-hidden border-l border-line bg-white shadow-overlay sm:max-w-5xl"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        {children}
      </section>
    </div>
  );
}

function AccessibleDialog({ title, description, onClose, children, maxWidth, layer = "z-[60]" }: { title: string; description?: string; onClose: () => void; children: ReactNode; maxWidth: string; layer?: string }) {
  const dialogRef = useDialogFocus(onClose);
  const titleId = `dialog-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div className={`fixed inset-0 ${layer} flex items-stretch justify-center bg-slate-950/40 sm:items-center sm:p-4`} role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className={`flex h-[100dvh] w-full flex-col overflow-hidden bg-white shadow-overlay sm:h-auto sm:max-h-[94vh] sm:rounded-2xl sm:border sm:border-line ${maxWidth}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-line px-4 py-4 sm:px-5">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold text-ink">{title}</h2>
            {description && <p className="mt-1 text-sm text-muted">{description}</p>}
          </div>
          <button className="btn h-11 w-11 shrink-0 p-0" onClick={onClose} aria-label={`Close ${title}`} title="Close">
            <X size={18} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">{children}</div>
      </section>
    </div>
  );
}

function useDialogFocus(onClose: () => void) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = dialog?.querySelector<HTMLElement>("button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[href],[tabindex]:not([tabindex='-1'])");
    focusable?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const items = [...dialog.querySelectorAll<HTMLElement>("button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[href],[tabindex]:not([tabindex='-1'])")];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousBodyOverflow;
      previous?.focus();
    };
  }, []);
  return dialogRef;
}

function PairingField({ label, value, secret = false }: { label: string; value: string; secret?: boolean }) {
  return (
    <div className="grid min-w-0 gap-1.5">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</div>
      <div className="flex min-w-0 items-center gap-2 rounded-lg border border-line bg-white px-3 py-2">
        <code className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{secret ? maskToken(value) : value}</code>
        <button className="btn h-11 w-11 shrink-0 p-0" onClick={() => void copyText(value)} aria-label={`Copy ${label}`} title={`Copy ${label}`}>
          <Copy size={15} />
        </button>
      </div>
    </div>
  );
}

function PairingStep({ number, title, detail, active = false, complete = false }: { number: string; title: string; detail: string; active?: boolean; complete?: boolean }) {
  return (
    <li className={`grid grid-cols-[2.5rem_minmax(0,1fr)] gap-3 rounded-lg border px-3 py-3 ${active ? "border-blue-300 bg-blue-50" : complete ? "border-emerald-200 bg-emerald-50" : "border-line bg-white"}`}>
      <div className={`flex h-10 w-10 items-center justify-center rounded-full text-sm font-semibold ${complete ? "bg-emerald-700 text-white" : active ? "bg-accent text-white" : "bg-slate-100 text-slate-600"}`}>
        {complete ? <CheckCircle2 size={18} /> : number}
      </div>
      <div className="min-w-0">
        <div className="font-semibold text-ink">{title}</div>
        <div className="mt-0.5 text-sm text-muted">{detail}</div>
      </div>
    </li>
  );
}

function PairingExpiry({ value }: { value: string | null }) {
  if (!value) return <div className="text-sm text-muted">Keep this QR private and scan it promptly.</div>;
  return (
    <div className="rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-blue-950">
      Pairing window ends <span className="font-semibold">{formatDate(value)}</span>
    </div>
  );
}

function DeviceMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="min-w-0 bg-white p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 truncate font-semibold text-ink">{value}</div>
      <div className="mt-1 line-clamp-2 text-xs leading-5 text-muted">{detail}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-slate-50 p-3">
      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-medium text-ink">{value}</dd>
    </div>
  );
}

function PreviewRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right font-medium text-ink">{value}</dd>
    </div>
  );
}

function SummaryPill({ label, value, tone = "neutral" }: { label: string; value: ReactNode; tone?: "neutral" | "warn" | "success" }) {
  const toneClass = tone === "warn" ? "border-amber-200 bg-amber-50" : tone === "success" ? "border-emerald-200 bg-emerald-50" : "border-line bg-slate-50";
  return (
    <div className={`flex min-h-10 items-center rounded-lg border px-3 ${toneClass}`}>
      <span className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</span>
      <span className="ml-2 font-semibold tabular-nums text-ink">{value}</span>
    </div>
  );
}

function RiskNotice({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm leading-5 text-amber-950" role="alert">
      <AlertTriangle className="mt-0.5 shrink-0" size={16} />
      <span>{text}</span>
    </div>
  );
}

function DeviceListSkeleton() {
  return (
    <div className="grid gap-3" aria-label="Loading devices">
      {[0, 1].map((item) => <div key={item} className="h-20 animate-pulse rounded-xl bg-slate-100" />)}
    </div>
  );
}

function deviceLifecycle(device: MobileDevice): string {
  if (device.lifecycle_status) return device.lifecycle_status;
  if (device.status === "disabled") return "disabled";
  if (!device.last_seen_at) return "awaiting_pairing";
  return device.online_status === "online" ? "online" : "offline";
}

function lifecycleLabel(value: string) {
  if (value === "awaiting_pairing") return "Awaiting pairing";
  if (value === "setup_required") return "Setup required";
  if (value === "online") return "Online";
  if (value === "offline") return "Offline";
  if (value === "token_expired") return "QR expired";
  if (value === "disabled") return "Disabled";
  return "Unknown";
}

function lifecycleDetail(device: MobileDevice) {
  if (device.lifecycle_detail) return device.lifecycle_detail;
  const lifecycle = deviceLifecycle(device);
  if (lifecycle === "awaiting_pairing") return "Scan the pairing QR on the Android device.";
  if (lifecycle === "setup_required") return "Enable Nexus Mobile Accessibility control to finish setup.";
  if (lifecycle === "online") return "The device is connected and ready for actions.";
  if (lifecycle === "offline") return "Open Nexus Mobile and check its connection.";
  if (lifecycle === "token_expired") return "Generate a new pairing QR to continue.";
  return "Enable this device before using it.";
}

function recommendedActionLabel(device: MobileDevice) {
  const lifecycle = deviceLifecycle(device);
  if (lifecycle === "online") return "Open";
  if (lifecycle === "awaiting_pairing") return "Pair";
  if (lifecycle === "setup_required") return "Finish";
  if (lifecycle === "token_expired") return "New QR";
  if (lifecycle === "offline") return "Diagnose";
  return "Review";
}

function approvalModeLabel(value: string) {
  if (value === "confirm_high_risk") return "Confirm high risk";
  if (value === "manual") return "Approve every action";
  if (value === "auto") return "Automatic approval";
  return value || "Unknown";
}

function approvalModeDetail(value: string) {
  if (value === "manual") return "Every action waits for approval.";
  if (value === "auto") return "Trusted test device; no manual checkpoint.";
  return "High-risk actions wait for approval.";
}

function approvalPreview(mode: string, risk: "low" | "medium" | "high") {
  if (mode === "manual") return "Administrator approval required";
  if (mode === "auto") return "Runs automatically under device policy";
  return risk === "high" ? "Administrator approval required" : "Runs after submission";
}

function inferActionRisk(form: CommandForm): "low" | "medium" | "high" {
  if (form.action === "type_text") return "high";
  if (form.action === "tap_text" && /pay|send|delete|purchase|付款|支付|删除/i.test(form.text)) return "high";
  if (["tap_text", "tap_coordinates", "swipe", "open_app"].includes(form.action)) return "medium";
  return "low";
}

function commandFormReady(form: CommandForm) {
  if (actionNeedsText(form.action)) return Boolean(form.text.trim());
  if (form.action === "open_app") return Boolean(form.packageName.trim());
  if (actionNeedsCoordinates(form.action)) {
    const values = [form.x, form.y, ...(form.action === "swipe" ? [form.endX, form.endY] : [])].map(Number);
    return values.every((value) => Number.isFinite(value) && value >= 0 && value <= 1);
  }
  return true;
}

function buildCommandBody(form: CommandForm): Parameters<typeof api.createMobileCommand>[2] {
  const argumentsValue: Record<string, unknown> = {};
  if (actionNeedsText(form.action)) argumentsValue.text = form.text.trim();
  if (form.action === "open_app") argumentsValue.package = form.packageName.trim();
  if (form.action === "tap_coordinates") {
    argumentsValue.x = Number(form.x);
    argumentsValue.y = Number(form.y);
  }
  if (form.action === "swipe") {
    argumentsValue.start_x = Number(form.x);
    argumentsValue.start_y = Number(form.y);
    argumentsValue.end_x = Number(form.endX);
    argumentsValue.end_y = Number(form.endY);
    argumentsValue.duration_ms = 300;
  }
  return { action: form.action, arguments: argumentsValue, ttl_seconds: 180 };
}

function actionNeedsText(action: CommandAction) {
  return action === "tap_text" || action === "type_text" || action === "wait_for_state";
}

function actionNeedsCoordinates(action: CommandAction) {
  return action === "tap_coordinates" || action === "swipe";
}

function actionLabel(action: string) {
  return {
    observe: "Refresh screen",
    capture_screen: "Capture screen",
    tap_text: "Tap visible text",
    tap_coordinates: "Tap coordinates",
    type_text: "Type text",
    swipe: "Swipe",
    press_back: "Go back",
    open_app: "Open Android app",
    wait_for_state: "Wait for text"
  }[action] ?? action.replace(/_/g, " ");
}

function commandIcon(action: CommandAction) {
  if (action === "observe") return <Eye size={16} />;
  if (action === "press_back") return <ArrowLeft size={16} />;
  if (action === "tap_text" || action === "tap_coordinates") return <MousePointerClick size={16} />;
  if (action === "type_text") return <Keyboard size={16} />;
  return <Send size={16} />;
}

function commandStatusLabel(status: string) {
  return {
    pending_approval: "Needs approval",
    queued: "Queued",
    running: "Running",
    succeeded: "Completed",
    failed: "Failed",
    rejected: "Rejected",
    canceled: "Canceled"
  }[status] ?? status.replace(/_/g, " ");
}

function commandMatchesFilter(command: MobileCommand, filter: ActivityFilter) {
  if (filter === "attention") return ["pending_approval", "failed"].includes(command.status);
  if (filter === "running") return ["queued", "running"].includes(command.status);
  if (filter === "completed") return ["succeeded", "rejected", "canceled"].includes(command.status);
  return true;
}

function activityFilterLabel(filter: ActivityFilter) {
  return { all: "All", attention: "Needs attention", running: "In progress", completed: "Completed" }[filter];
}

function friendlyArguments(command: MobileCommand) {
  const args = command.arguments || {};
  if (command.action === "tap_text") return `Tap “${String(args.text || "")}”`;
  if (command.action === "type_text") return `Type “${String(args.text || "")}”`;
  if (command.action === "wait_for_state") return `Wait for “${String(args.text || "")}”`;
  if (command.action === "open_app") return `Open ${String(args.package || "Android app")}`;
  if (command.action === "tap_coordinates") return `Tap at ${args.x}, ${args.y}`;
  if (command.action === "swipe") return `Swipe from ${args.start_x}, ${args.start_y} to ${args.end_x}, ${args.end_y}`;
  if (command.action === "capture_screen") return "Capture the current screen once";
  return "No additional input";
}

function friendlyAppName(packageName: string) {
  if (!packageName) return "";
  const known: Record<string, string> = {
    "com.android.settings": "Settings",
    "com.android.chrome": "Chrome",
    "com.google.android.apps.nexuslauncher": "Home"
  };
  return known[packageName] || packageName.split(".").pop()?.replace(/[_-]/g, " ") || packageName;
}

function capabilitySummary(device: MobileDevice) {
  const entries = Object.entries(device.capabilities || {}).filter(([, value]) => Boolean(value));
  if (entries.length === 0) return "Not reported";
  return entries.map(([key]) => key.replace(/_/g, " ")).join(", ");
}

function observationTextLines(value: Record<string, unknown>) {
  const lines: string[] = [];
  const walk = (node: unknown) => {
    if (lines.length >= 20 || node == null) return;
    if (typeof node === "string") {
      const text = node.trim();
      if (text && text.length <= 160 && !lines.includes(text)) lines.push(text);
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (typeof node === "object") {
      const record = node as Record<string, unknown>;
      for (const key of ["text", "label", "contentDescription", "title"]) if (record[key]) walk(record[key]);
      for (const [key, nested] of Object.entries(record)) {
        if (!["text", "label", "contentDescription", "title", "bounds", "packageName"].includes(key)) walk(nested);
      }
    }
  };
  walk(value);
  return lines;
}

function formatRelativeTime(value: string | null) {
  if (!value) return "Never";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Unknown";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 45) return "Just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function riskTone(value: string): "success" | "warn" | "danger" | "muted" | "info" {
  if (value === "high") return "danger";
  if (value === "medium") return "warn";
  if (value === "low") return "success";
  return "muted";
}

function maskToken(value: string) {
  if (value.length <= 18) return value;
  return `${value.slice(0, 14)}…${value.slice(-6)}`;
}

function runtimeHints() {
  const host = window.location.hostname;
  const currentPort = window.location.port || (window.location.protocol === "https:" ? "443" : "80");
  const apiPort = currentPort === "5173" ? "8000" : currentPort;
  return {
    emulatorBaseUrl: `${window.location.protocol}//10.0.2.2:${apiPort}`,
    lanBaseUrl: `${window.location.protocol}//${host}:${apiPort}`
  };
}

function defaultPairingBaseUrl(runtime: ReturnType<typeof runtimeHints>) {
  const currentHost = window.location.hostname.toLowerCase();
  return currentHost === "localhost" || currentHost === "127.0.0.1" ? runtime.emulatorBaseUrl : runtime.lanBaseUrl;
}

function buildPairingDeepLink(pairing: MobileDeviceWithPairingToken, baseUrl: string) {
  const params = new URLSearchParams({
    base_url: baseUrl.trim().replace(/\/+$/, ""),
    device_id: pairing.id,
    token: pairing.pairing_token
  });
  if (pairing.pairing_expires_at) params.set("expires_at", pairing.pairing_expires_at);
  return `nexus-mobile://pair?${params.toString()}`;
}

async function copyText(value: string) {
  await navigator.clipboard.writeText(value);
  toast.success("Copied");
}
