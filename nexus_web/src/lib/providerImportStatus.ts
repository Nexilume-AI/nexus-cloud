import { t } from "../localization";

// Translate known presentation states without changing wire values or unknown states.
export function providerImportStatusLabel(status: string): string {
  switch (status) {
    case "preview": return t("Import preview");
    case "processing": return t("Import processing");
    case "partial": return t("Import partially completed");
    case "complete": return t("Import completed");
    case "expired": return t("Import expired");
    case "discarded": return t("Import discarded");
    default: return status;
  }
}
