import type { ComponentType } from "react";

export type DataLibraryNavigationProps = { collectionTotal?: number; acquiredTotal?: number };
/** Build-selected library entry; its URL selector cannot install an extension. */
export type DataLibraryExtension = {
  Workspace: ComponentType;
  Navigation: ComponentType<DataLibraryNavigationProps>;
  matchesLocation: (search: string) => boolean;
};
