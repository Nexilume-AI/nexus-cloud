export type NotificationFilter = "attention" | "unread" | "all";
export type NotificationCounts = { unread: number; needs_attention: number; total: number };
export type PersonalNotification = {
  id: string; kind: string; title: string; message: string; agent_name: string;
  project_id: string | null; project_name: string | null; created_at: string;
  is_read: boolean; needs_attention: boolean; resolved: boolean; action_label: string;
};
export type NotificationPage = {
  results: PersonalNotification[]; next_cursor: string | null; counts: NotificationCounts;
  read_snapshot: string; read_batch_count: number; retention_days: number;
};
