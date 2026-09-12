import type { ComponentType } from "react";

/** Resource identity only; the installed extension owns its permission policy. */
export type ShareResourceTarget = {
  resourceType: string;
  resourceKind: string;
  resourceId: string;
  resourceName: string;
};

export type ShareResourceModalProps = { target: ShareResourceTarget | null; onClose: () => void };
export type ResourceSharingExtension = { Dialog: ComponentType<ShareResourceModalProps> };
