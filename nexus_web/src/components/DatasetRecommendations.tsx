import { t } from "../localization";
import { FileArchive, Upload } from "lucide-react";
import type { Dataset } from "../lib/types";
import type { DatasetRecommendedAction, DatasetPublicationPolicy } from "../app/resourcePublishing";

/** No response field can activate a distribution's optional management rules. */
export function datasetManagementPresentation(dataset: Dataset | undefined, policy?: DatasetPublicationPolicy) {
  const management = policy?.management;
  return {
    settingsTitle: management?.settingsTitle ?? "Collection settings",
    settingsDescription: management?.settingsDescription ?? "Choose a name that helps you find this collection. Technical identifiers remain system managed.",
    deletionSummary: management?.deletionSummary ?? "Deleting removes this collection and its release records.",
    deletionBlockReason: dataset ? management?.deletionBlockReason(dataset) ?? null : null,
  };
}

export function datasetNextAction(dataset: Dataset | undefined, policy?: DatasetPublicationPolicy): DatasetRecommendedAction {
  if (!dataset || dataset.file_count === 0) {
    return {
      key: "import",
      label: t("Import assets"),
      description:
        t("Choose an Agent and add an approved trace, memory selection, or output file."),
      icon: <Upload size={16} />,
    };
  }
  if (!dataset.current_version) {
    return {
      key: "release",
      label: t("Create first release"),
      description:
        t("Review policy checks and freeze the current assets into an immutable release."),
      icon: <FileArchive size={16} />,
    };
  }
  return policy?.recommend(dataset) ?? { key: "release", label: t("Review release"), description: t("View the current immutable release and its files."), icon: <FileArchive size={16} /> };
}
