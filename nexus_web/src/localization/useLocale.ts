import { useSyncExternalStore } from "react";
import { getLocale, subscribeLocale } from "./locale";
import { defaultLocale } from "./model";

/** Subscribing updates copy without remounting forms, chats or terminal sessions. */
export function useLocale() {
  return useSyncExternalStore(subscribeLocale, getLocale, () => defaultLocale);
}
