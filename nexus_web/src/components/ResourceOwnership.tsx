import { t, useLocale } from "../localization";
import { useContext, useEffect } from "react";
import { ApplicationDistributionContext } from "../app/distribution";
import type { ResourceOwnershipPickerProps } from "../app/resourceOwnership";
import type { ResourceAccess, ResourceOwnership } from "../lib/types";

/** Personal uses the server-owned context; organization/share UI is an extension. */
export function ResourceOwnershipPicker(props: ResourceOwnershipPickerProps) {
  useLocale();
  const Picker = useContext(ApplicationDistributionContext)?.resourceOwnership?.Picker;
  return Picker ? <Picker {...props} /> : <PersonalOwnership {...props} />;
}

function PersonalOwnership({ projects, value, onChange }: ResourceOwnershipPickerProps) {
  useLocale();
  const projectId = projects.length === 1 ? projects[0].id : null;
  useEffect(() => {
    if (projectId && (value.scope !== "project" || value.project_id !== projectId)) {
      onChange({ scope: "project", project_id: projectId });
    }
  // A parent Dialog may reset to a new default object in the same effect pass.
  // Observe that replacement too, even if its primitive fields equal the old
  // default; otherwise it can overwrite this fixed-owner correction unnoticed.
  }, [projectId, value, onChange]);
  return <p className="text-sm text-muted" role="status">{projectId
    ? t("Personal installation · Only you can access this resource.")
    : t("Personal context unavailable. Reload before creating a resource.")}</p>;
}

export function ResourceOwnershipBadge(props: { ownership?: ResourceOwnership | null }) {
  useLocale();
  const Badge = useContext(ApplicationDistributionContext)?.resourceOwnership?.Badge;
  if (Badge) return <Badge {...props} />;
  return props.ownership ? <span className="inline-flex rounded-full border border-line px-2 py-1 text-xs text-muted">{t("Personal")}</span> : null;
}

export function ResourceAccessState(props: { access?: ResourceAccess | null }) {
  useLocale();
  const State = useContext(ApplicationDistributionContext)?.resourceOwnership?.AccessState;
  if (State) return <State {...props} />;
  return props.access && !props.access.can_read ? <span role="status">{t("Resource unavailable. Reload the personal installation.")}</span> : null;
}
