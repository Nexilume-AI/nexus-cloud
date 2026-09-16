import { t } from "../localization";
import type { DisplayStreamEvent } from "./types";

export type AgentDisplayMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: "streaming" | "completed";
};

export type AgentDisplayShellLine = {
  id: string;
  seq: number;
  stream: string;
  text: string;
  createdAt: string;
};

export type AgentDisplayTerminalEvent = {
  seq: number;
  kind: string;
  command_id?: string;
  data: string;
  exit_code: number | null;
  created_at?: string;
};

export type AgentDisplayShellSegment = {
  id: string;
  stream: string;
  text: string;
};

export type AgentDisplayShellTranscript = {
  segments: AgentDisplayShellSegment[];
  text: string;
};

export type AgentDisplayShellCommand = {
  id: string;
  command: string;
  commandId: string;
  startedAt: string;
  endedAt: string;
  exitCode: number | null;
  transcript: AgentDisplayShellTranscript;
};

export type AgentDisplayBrowserFrame = {
  path: string;
  title: string;
  url: string;
  text: string;
  width: number | null;
  height: number | null;
  observationId: string;
  revision: number | null;
  action: string;
  actionStatus: string;
  domNodeCount: number | null;
  source: string;
  computerName: string;
  profile: string;
  createdAt: string;
};

export function agentDisplayEventValue(event: DisplayStreamEvent): Record<string, unknown> {
  if (event.content && typeof event.content === "object" && !Array.isArray(event.content)) return event.content;
  if (event.value && typeof event.value === "object" && !Array.isArray(event.value)) return event.value as Record<string, unknown>;
  if (event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)) return event.payload;
  return {};
}

export function deriveAgentDisplayMessages(events: DisplayStreamEvent[]): AgentDisplayMessage[] {
  const messages = new Map<string, AgentDisplayMessage>();
  for (const event of events) {
    if (event.type === "MESSAGES_SNAPSHOT") {
      messages.clear();
      for (const item of event.messages ?? []) {
        const value = asRecord(item);
        const id = stringValue(value.id || value.messageId) || `message-${messages.size + 1}`;
        messages.set(id, {
          id,
          role: normalizeRole(value.role),
          content: stringValue(value.content || value.text),
          status: "completed",
        });
      }
      continue;
    }
    if (!event.type.startsWith("TEXT_MESSAGE_")) continue;
    const id = event.messageId || `message-${event.seq}`;
    const current = messages.get(id) ?? {
      id,
      role: normalizeRole(event.role),
      content: "",
      status: "streaming" as const,
    };
    if (event.type === "TEXT_MESSAGE_START") {
      messages.set(id, { ...current, role: normalizeRole(event.role || current.role), status: "streaming" });
    } else if (event.type === "TEXT_MESSAGE_CONTENT" || event.type === "TEXT_MESSAGE_CHUNK") {
      messages.set(id, { ...current, content: `${current.content}${event.delta ?? ""}`, status: "streaming" });
    } else if (event.type === "TEXT_MESSAGE_END") {
      messages.set(id, { ...current, status: "completed" });
    }
  }
  return [...messages.values()];
}

export function deriveAgentDisplayShell(events: DisplayStreamEvent[]): AgentDisplayShellLine[] {
  return events
    .filter((event) => event.type === "CUSTOM" && event.name === "nexus.computer.log")
    .map((event) => {
      const value = agentDisplayEventValue(event);
      return {
        id: `sdk-${event.id}`,
        seq: event.seq,
        stream: stringValue(value.stream || value.level) || "stdout",
        text: stringValue(value.message) || compactJson(value),
        createdAt: event.created_at,
      };
    });
}

/**
 * Builds the read-only Shell transcript from one authoritative source.
 *
 * Terminal events are preferred once they exist because SDK computer log events can mirror
 * the same command output. Terminal chunks are a byte-stream boundary, not a visual line
 * boundary, so they are concatenated without wrapping every event in its own block element.
 */
export function buildAgentDisplayShellTranscript(
  terminalEvents: AgentDisplayTerminalEvent[],
  sdkLines: AgentDisplayShellLine[],
): AgentDisplayShellTranscript {
  const segments: AgentDisplayShellSegment[] = [];
  const append = (id: string, stream: string, rawText: string, record = false) => {
    let text = normalizeShellNewlines(cleanShellText(rawText));
    if (record) text = text.replace(/^\n+|\n+$/g, "");
    if (!text) return;

    const previous = segments.at(-1);
    if (record && previous && !previous.text.endsWith("\n")) text = `\n${text}`;
    if (!record && previous?.text.endsWith("\n") && text.startsWith("\n")) text = text.slice(1);
    if (!text) return;
    if (!record && previous?.stream === stream) {
      previous.text += text;
      return;
    }
    segments.push({ id, stream, text });
  };

  if (terminalEvents.length) {
    for (const event of [...terminalEvents].sort((left, right) => left.seq - right.seq)) {
      if (event.kind === "exit") {
        append(`terminal-${event.seq}`, "system", `Process exited with code ${event.exit_code ?? "?"}`, true);
        continue;
      }
      if (event.kind === "command") {
        append(`terminal-${event.seq}`, "command", `$ ${normalizeShellNewlines(event.data).trim().replace(/^\$\s*/, "")}`, true);
        const command = segments.at(-1);
        if (command) command.text += "\n";
        continue;
      }
      append(`terminal-${event.seq}`, event.kind, event.data);
    }
  } else {
    for (const line of [...sdkLines].sort((left, right) => left.seq - right.seq)) {
      append(line.id, line.stream, line.text, true);
    }
  }

  const joined = segments.map((segment) => segment.text).join("");
  const text = joined.replace(/\n+$/g, "");
  if (segments.length && text !== joined) {
    const last = segments.at(-1)!;
    last.text = last.text.replace(/\n+$/g, "");
    if (!last.text) segments.pop();
  }
  return { segments, text };
}

/** Groups persisted Terminal records without changing their stream order. */
export function buildAgentDisplayShellCommands(
  terminalEvents: AgentDisplayTerminalEvent[],
  sdkLines: AgentDisplayShellLine[],
): AgentDisplayShellCommand[] {
  if (!terminalEvents.length) {
    const transcript = buildAgentDisplayShellTranscript([], sdkLines);
    return transcript.segments.length ? [{
      id: "sdk-shell",
      command: t("Agent Shell output"),
      commandId: "",
      startedAt: sdkLines[0]?.createdAt || "",
      endedAt: sdkLines.at(-1)?.createdAt || "",
      exitCode: null,
      transcript,
    }] : [];
  }

  const groups: Array<{ id: string; command: string; commandId: string; startedAt: string; endedAt: string; exitCode: number | null; events: AgentDisplayTerminalEvent[] }> = [];
  const byCommand = new Map<string, (typeof groups)[number]>();
  for (const event of [...terminalEvents].sort((left, right) => left.seq - right.seq)) {
    const commandId = event.command_id || "ungrouped";
    let group = byCommand.get(commandId);
    if (!group) {
      group = {
        id: commandId,
        command: event.kind === "command" ? normalizeShellNewlines(event.data).trim().replace(/^\$\s*/, "") : t("Shell output"),
        commandId: event.command_id || "",
        startedAt: event.created_at || "",
        endedAt: event.created_at || "",
        exitCode: null,
        events: [],
      };
      byCommand.set(commandId, group);
      groups.push(group);
    }
    if (event.kind === "command") group.command = normalizeShellNewlines(event.data).trim().replace(/^\$\s*/, "") || group.command;
    if (event.kind === "exit") group.exitCode = event.exit_code;
    group.endedAt = event.created_at || group.endedAt;
    group.events.push(event);
  }
  return groups.map(({ events, ...group }) => ({ ...group, transcript: buildAgentDisplayShellTranscript(events, []) }));
}

function normalizeShellNewlines(value: string) {
  return String(value || "").replace(/\r\n?/g, "\n");
}

function cleanShellText(value: string) {
  const text = String(value || "").replace(/\u001B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, "");
  if (!text.includes("#< CLIXML")) return text;
  const errors = [...text.matchAll(/<S S="Error">([\s\S]*?)<\/S>/g)].map((match) => decodePowerShellXml(match[1])).filter(Boolean);
  return errors.join("\n");
}

function decodePowerShellXml(value: string) {
  return value
    .replace(/_x([0-9a-fA-F]{4})_/g, (_, code: string) => String.fromCharCode(Number.parseInt(code, 16)))
    .replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("&amp;", "&").replaceAll("&quot;", '"').replaceAll("&apos;", "'")
    .trim();
}

export function latestAgentDisplayBrowserFrame(events: DisplayStreamEvent[]): AgentDisplayBrowserFrame | null {
  return agentDisplayBrowserFrames(events).at(-1) ?? null;
}

export function agentDisplayBrowserFrames(events: DisplayStreamEvent[]): AgentDisplayBrowserFrame[] {
  return events.filter((item) => item.type === "CUSTOM" && item.name === "nexus.computer.frame").flatMap((event) => {
    const value = agentDisplayEventValue(event);
    const path = stringValue(value.screenshot_url || value.screenshotUrl || value.image_url);
    if (!path) return [];
    return [{
      path,
      title: stringValue(value.title),
      url: stringValue(value.url),
      text: stringValue(value.text),
      width: numberValue(value.width),
      height: numberValue(value.height),
      observationId: stringValue(value.observation_id),
      revision: numberValue(value.revision),
      action: stringValue(value.action),
      actionStatus: stringValue(value.action_status),
      domNodeCount: numberValue(value.dom_node_count),
      source: stringValue(value.source),
      computerName: stringValue(value.computer_name),
      profile: stringValue(value.profile),
      createdAt: event.created_at,
    }];
  });
}

function normalizeRole(value: unknown): "user" | "assistant" {
  return stringValue(value).toLowerCase() === "user" ? "user" : "assistant";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function numberValue(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function compactJson(value: Record<string, unknown>) {
  try {
    return JSON.stringify(value);
  } catch {
    return t("Agent event");
  }
}
