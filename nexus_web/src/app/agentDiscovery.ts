/** Optional, build-selected discovery destinations and onboarding copy. */
export type AgentDiscoveryExtension = {
  href: string;
  primaryLabel: string;
  secondaryLabel: string;
  restricted: { status: string; title: string; description: string };
  emptyDescription: string;
  guide: { title: string; description: string; accessDescription: string };
};

export function validateAgentDiscovery(value: AgentDiscoveryExtension) {
  const text = [value.href, value.primaryLabel, value.secondaryLabel, value.restricted?.status,
    value.restricted?.title, value.restricted?.description, value.emptyDescription,
    value.guide?.title, value.guide?.description, value.guide?.accessDescription];
  if (!text.every(item => typeof item === 'string' && item.trim())
      || !/^\/(?!\/)[^\s\\?#]+$/.test(value.href)) {
    throw new Error('Agent discovery extension is incomplete.');
  }
}
