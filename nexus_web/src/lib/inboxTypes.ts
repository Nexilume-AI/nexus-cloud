export type InboxState = "needs_action" | "in_progress" | "failed" | "completed" | "resolved" | "canceled" | "snoozed";
export type InboxCategory = "agent" | "background" | "approval" | "operations";
export type InboxSummary = {
  unread: number; badge_count: number; needs_attention: number; in_progress: number; failed: number;
  completed_unread: number; total: number; revision: string; retention_days?: number;
  push_enabled?: boolean;
  push_status?: string;
  enabled_push_devices?: number;
};
export type InboxItem = {
  id: string; category: InboxCategory; kind: string; state: InboxState; priority: number;
  audience: "personal" | "role"; ownership: "personal" | "role";
  title: string; message: string; action_label: string; project_id: string | null;
  project_name: string | null; resource_name: string; occurred_at: string; due_at: string | null;
  resolved_at: string | null; updated_at: string; read_at: string | null; is_read: boolean;
  snoozed_until: string | null; can_archive: boolean;
};
export type InboxPage = { results: InboxItem[]; next_cursor: string | null; counts: InboxSummary | null };
export type InboxPreferences = {
  categories: Record<InboxCategory, boolean>;
  events: { action_required: boolean; failed: boolean; completed: boolean };
  timezone: string; dnd_enabled: boolean; dnd_start: string | null; dnd_end: string | null;
  urgent_bypass: boolean; push_enabled: boolean; push_status?: string; vapid_public_key: string;
};
export type PushDevice = { id: string; device_name: string; enabled: boolean; last_seen_at: string };
