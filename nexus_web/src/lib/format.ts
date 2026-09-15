import { t, getLocale } from "../localization";
export function formatMoney(value: string | number | null | undefined, currency = "USD") {
  const numeric = Number(value ?? 0);
  const normalizedCurrency = String(currency || "USD").trim();
  if (!Number.isFinite(numeric)) return `${normalizedCurrency} 0.00`;
  try {
    return new Intl.NumberFormat(getLocale(), {
      style: "currency",
      currency: normalizedCurrency.toUpperCase(),
      maximumFractionDigits: 4
    }).format(numeric);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return `${new Intl.NumberFormat(getLocale(), { maximumFractionDigits: 4 }).format(numeric)} ${formatUnitLabel(normalizedCurrency)}`;
  }
}

export function formatNumber(value: string | number | null | undefined) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "0";
  return new Intl.NumberFormat(getLocale()).format(numeric);
}

export function formatDate(value: string | null | undefined) {
  if (!value) return t("Never");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(getLocale(), {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

export function compactId(value: string | null | undefined) {
  if (!value) return "-";
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function formatUnitLabel(value: string) {
  const label = value.trim().replace(/[_-]+/g, " ").replace(/\s+/g, " ");
  return label || t("units");
}
