import { Languages } from "lucide-react";
import { setLocale } from "./locale";
import { isLocale } from "./model";
import { useLocale } from "./useLocale";
import "./language.css";

export function LanguageSelect() {
  const locale = useLocale();
  return (
    <label className="nexus-language-select">
      <Languages size={16} aria-hidden="true" />
      <select aria-label="语言 / Language" value={locale} onChange={event => {
        if (isLocale(event.target.value)) setLocale(event.target.value);
      }}>
        <option value="zh-CN">简体中文</option>
        <option value="en-US">English</option>
      </select>
    </label>
  );
}
