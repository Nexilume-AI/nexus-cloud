/** Optional discovery copy and destination, selected by the application build. */
export type SourceDiscoveryExtension = {
  href: string;
  label: string;
  emptyDescription: string;
  sourceDescription: string;
};

export function validateSourceDiscovery(value: SourceDiscoveryExtension) {
  if (![value.href,value.label,value.emptyDescription,value.sourceDescription].every(text => typeof text==='string' && text.trim())
      || !/^\/(?!\/)[^\s\\?#]+$/.test(value.href)) {
    throw new Error('Source discovery extension is incomplete.');
  }
}
