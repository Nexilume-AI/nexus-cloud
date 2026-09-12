type EntryLocation = { pathname: string; search: string; hash?: string };
export type AgentDisplayNavigation = (agentId: string) => { fallback: string; launchPaths: readonly string[] };

/** Keep the launch page's filters, but never persist a consumed attach intent. */
export function agentDisplayEntryUrl(location: EntryLocation): string {
  const params = new URLSearchParams(location.search);
  params.delete("attach");
  params.delete("return");
  const query = params.toString();
  return `${location.pathname}${query ? `?${query}` : ""}${location.hash || ""}`;
}

export function privateDisplayReturnTo(agentId: string, state: unknown, navigation?: AgentDisplayNavigation): string {
  const id = encodeURIComponent(agentId);
  const defaults = { fallback: agentId ? `/agents/${id}/publish` : "/agents", launchPaths: [`/agents/${id}/publish`] };
  const { fallback, launchPaths } = navigation ? navigation(agentId) : defaults;
  if (!Array.isArray(launchPaths) || ![fallback, ...launchPaths].every(path => {
    if (typeof path !== 'string' || !/^\/(?!\/)[^\\\s?#]+$/.test(path)) return false;
    const url = new URL(path, 'https://nexus.invalid');
    return url.origin === 'https://nexus.invalid' && url.pathname === path;
  })) throw new Error('Agent display navigation is incomplete.');
  const value = state && typeof state === "object" && "returnTo" in state ? state.returnTo : null;
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || /[\\\x00-\x1f\x7f]/.test(value)) return fallback;
  try {
    const url = new URL(value, "https://nexus.invalid");
    // Only the same Agent's real launch surfaces may receive device intents.
    // The build-selected distribution owns fallback and launch destinations.
    // A URL, response or saved state cannot add another launch surface.
    if (url.origin !== "https://nexus.invalid" || !launchPaths.includes(url.pathname)) return fallback;
    return agentDisplayEntryUrl(url);
  } catch {
    return fallback;
  }
}

export function privateDisplayAttachmentUrl(agentId: string, state: unknown, device: "computer" | "mobile", navigation?: AgentDisplayNavigation): string {
  const url = new URL(privateDisplayReturnTo(agentId, state, navigation), "https://nexus.invalid");
  url.searchParams.set("attach", device);
  url.searchParams.set("return", "private-display");
  return `${url.pathname}${url.search}${url.hash}`;
}
