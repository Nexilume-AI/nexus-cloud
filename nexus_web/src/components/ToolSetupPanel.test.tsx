import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import ts from "typescript";
import { ToolSetupPanel } from "./ToolSetupPanel";
import type { ToolSetupClient } from "../app/toolSetup";

vi.mock("../lib/api", () => { throw new Error("Shared Tool Setup must not load the host API client"); });

describe("shared Computer Tool Setup boundary", () => {
  it("renders without a commercial client or a terminal session", () => {
    const unavailable = vi.fn(() => Promise.reject(new Error("Not connected")));
    const client: ToolSetupClient = {
      workspaceToolConfig: unavailable, workspaceToolConfigOptions: unavailable,
      previewWorkspaceToolConfig: unavailable, applyWorkspaceToolConfigV2: unavailable,
      recoverWorkspaceToolConfig: unavailable,
    };
    const html = renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>
      <ToolSetupPanel client={client} apiContext={{}} isContextReady={false}
        form={{ selectedTool: "codex", selectedRuntimeId: "", selectedAgentId: "" }}
        selectedConnection={undefined} activeSession={null} isDetecting={false} isOpeningSession={false}
        onChange={() => {}} onClose={() => {}} onDetectTools={() => {}} onOpenSession={() => {}} />
    </QueryClientProvider>);
    expect(html).toContain("Open a terminal session first");
    expect(html).toContain("Agent setup");
    expect(unavailable).not.toHaveBeenCalled();
  });

  it("has only type-level host dependencies and does not include the legacy credential generator", () => {
    for (const file of ["ToolSetupPanel.tsx", "ToolSetupPendingChange.tsx"]) {
      const source = readFileSync(new URL(file, import.meta.url), "utf8");
      const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      for (const statement of tree.statements.filter(ts.isImportDeclaration)) {
        const path = (statement.moduleSpecifier as ts.StringLiteral).text;
        expect(path).not.toContain("enterprise");
        if (path.endsWith("/lib/api")) expect(statement.importClause?.isTypeOnly).toBe(true);
      }
      expect(source).not.toContain("LegacyToolSetupPanel");
      expect(source).not.toContain("downloadConfig");
      expect(source).not.toContain("full_profile");
    }
    const host = readFileSync(new URL("../pages/PlaygroundPage.tsx", import.meta.url), "utf8");
    expect(host).toContain('from "../components/ToolSetupPanel"');
    expect(host).toContain("client={api}");
    expect(host).not.toContain("function ToolSetupPanel(");
  });
});
