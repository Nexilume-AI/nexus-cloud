import type { ComponentType } from "react";
import type { Project, ResourceAccess, ResourceOwnership, ResourceOwnershipInput } from "../lib/types";

export type ResourceOwnershipPickerProps = {
  projects: Project[];
  value: ResourceOwnershipInput;
  onChange: (value: ResourceOwnershipInput) => void;
  organizationAvailable?: boolean;
  organizationUnavailableReason?: string;
};
export type ResourceOwnershipExtension = {
  newPoolVisibility: "project" | "private";
  Picker: ComponentType<ResourceOwnershipPickerProps>;
  Badge: ComponentType<{ ownership?: ResourceOwnership | null }>;
  AccessState: ComponentType<{ access?: ResourceAccess | null }>;
};
