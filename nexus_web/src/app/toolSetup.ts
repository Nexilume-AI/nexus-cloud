import type { ApiContext } from "../lib/api";
import type { WorkspaceToolConfig, WorkspaceToolConfigChange, WorkspaceToolConfigOptions, WorkspaceToolConfigPreview } from "../lib/types";

/** The host supplies transport; the shared workbench never imports a commercial client. */
export interface ToolSetupClient {
  workspaceToolConfig(context: ApiContext, session: string, tool: string): Promise<WorkspaceToolConfig>;
  workspaceToolConfigOptions(context: ApiContext, session: string, tool: string): Promise<WorkspaceToolConfigOptions>;
  previewWorkspaceToolConfig(context: ApiContext, session: string, change: WorkspaceToolConfigChange): Promise<WorkspaceToolConfigPreview>;
  applyWorkspaceToolConfigV2(context: ApiContext, session: string, change: WorkspaceToolConfigChange): Promise<WorkspaceToolConfig>;
  recoverWorkspaceToolConfig(context: ApiContext, session: string, change: {
    operation_id: string; expected_revision: string; action: "recover" | "restore_previous" | "keep_local";
  }): Promise<WorkspaceToolConfig>;
}
