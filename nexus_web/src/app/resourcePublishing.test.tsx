import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ApplicationDistributionContext, validateApplicationDistribution, type ApplicationDistribution } from "./distribution";
import { DatasetPublicationScope, DatasetPublicationPanel, AgentPublicationWorkspace, ProviderModelSettingsDialog, ProviderPublicationDialog, ResourceListingEditor } from "../components/ResourcePublishing";
import type { Agent, Dataset, ProviderConnection, ProviderRuntimeModelOffer } from "../lib/types";

const base: ApplicationDistribution = { id: "test", navigation: [], workspaceRoutes: [], standaloneRoutes: [], guestOverview: null };
const policy = { canPublish: () => false, needsPublication: () => false };
const extension = { ListingEditor: () => null, ProviderDialog: () => null, ProviderSummary: () => null, ProviderModelDialog: () => null, AgentPanel: () => null, agentPolicy: { sectionLabel: "Publish", recommend: () => null, status: () => ({ label: "Publication", value: "Unpublished", tone: "muted" as const }) }, AgentSummary: () => null, DatasetSummary: () => null, AgentSettingsPanel: () => null, providerPolicy: policy, DatasetScope: ({ children }: { children: React.ReactNode }) => <>{children}</>, DatasetPanel: () => null, datasetPolicy: { recommend: () => ({ key: "release", label: "Release", description: "", icon: null }) } };

describe("optional publisher slots", () => {
  it("keeps collection and capacity controls when no Data publisher is installed", () => {
    const html = renderToString(<ApplicationDistributionContext.Provider value={base}>
      <DatasetPublicationScope dataset={undefined} apiContext={{}} onSaved={async () => {}} selectionRevision={0} releaseSizeBytes={0}>
        <button>Import files</button>
        <DatasetPublicationPanel variant="settings" capacityControl={<button>Save capacity</button>} onShare={() => {}} />
      </DatasetPublicationScope>
    </ApplicationDistributionContext.Provider>);
    expect(html).toContain("Import files");
    expect(html).toContain("Save capacity");
    expect(html).not.toMatch(/pricing|Marketplace|Publish/);
  });

  it("forwards Data context, lifecycle reset and host controls through explicit composition", () => {
    const dataset = { id: "d" } as Dataset;
    const onSaved = vi.fn(async () => {}), onShare = vi.fn();
    const Scope = vi.fn(({ children }: { children: React.ReactNode }) => <>{children}</>);
    const Panel = vi.fn(({ capacityControl }: { capacityControl: React.ReactNode }) => <section>Data publisher{capacityControl}</section>);
    renderToString(<ApplicationDistributionContext.Provider value={{ ...base, resourcePublishing: { ...extension, DatasetScope: Scope, DatasetPanel: Panel } }}>
      <DatasetPublicationScope dataset={dataset} apiContext={{ tenantId: "t" }} onSaved={onSaved} selectionRevision={3} releaseSizeBytes={12}>
        <DatasetPublicationPanel variant="workspace" capacityControl={<button>Save capacity</button>} onShare={onShare} />
      </DatasetPublicationScope>
    </ApplicationDistributionContext.Provider>);
    expect(Scope).toHaveBeenCalledWith(expect.objectContaining({ dataset, onSaved, selectionRevision: 3, releaseSizeBytes: 12 }), undefined);
    expect(Panel).toHaveBeenCalledWith(expect.objectContaining({ variant: "workspace", onShare }), undefined);
    for (const patch of [{ DatasetScope: undefined }, { DatasetPanel: undefined }, { datasetPolicy: {} }]) {
      expect(() => validateApplicationDistribution({ ...base, resourcePublishing: { ...extension, ...patch } } as unknown as ApplicationDistribution)).toThrow(/incomplete/);
    }
  });

  it("keeps personal test actions without mounting any commercial component", () => {
    const html = renderToString(<ApplicationDistributionContext.Provider value={base}>
      <AgentPublicationWorkspace agent={{ id: "a" } as Agent} apiContext={{ token: null, tenantId: "t", projectId: null }} onSaved={async () => {}}
        testActions={<><a href="/agents/a/private-display">Private Display (test)</a><button>Attach Computer</button><button>Attach Mobile</button></>} />
    </ApplicationDistributionContext.Provider>);
    expect(html).toContain("Test your Agent");
    expect(html).toContain("Private Display (test)");
    expect(html).toContain("Attach Computer");
    expect(html).toContain("Attach Mobile");
    expect(html).not.toMatch(/Marketplace|Pricing|Publish Agent|Manage grants/);
  });

  it("forwards the Agent and test action slot without remaking caller controls", () => {
    const onSaved = vi.fn(async () => {});
    const AgentPanel = vi.fn(({ testActions }: { testActions: React.ReactNode }) => <section>Commercial panel{testActions}</section>);
    const agent = { id: "a" } as Agent;
    const apiContext = { token: null, tenantId: "t", projectId: null };
    const testActions = <button>Attach Computer</button>;
    const html = renderToString(<ApplicationDistributionContext.Provider value={{ ...base, resourcePublishing: { ...extension, AgentPanel } }}>
      <AgentPublicationWorkspace agent={agent} apiContext={apiContext} onSaved={onSaved} testActions={testActions} />
    </ApplicationDistributionContext.Provider>);
    expect(html).toContain("Commercial panel");
    expect(html.match(/Attach Computer/g)).toHaveLength(1);
    expect(AgentPanel).toHaveBeenCalledWith(expect.objectContaining({ agent, apiContext, onSaved, testActions }), undefined);
    expect(() => validateApplicationDistribution({ ...base, resourcePublishing: { ...extension, AgentPanel: undefined } } as unknown as ApplicationDistribution)).toThrow(/incomplete/);
  });

  it("mounts no publisher UI when the distribution has no extension", () => {
    const html = renderToString(<ApplicationDistributionContext.Provider value={base}>
      <ResourceListingEditor kind="agents" resourceId="a" resourceName="Agent" />
      <ProviderPublicationDialog provider={{ id: "p" } as ProviderConnection} onClose={() => {}} />
      <ProviderModelSettingsDialog provider={{ id: "p" } as ProviderConnection} offer={{ id: "m" } as ProviderRuntimeModelOffer} onClose={() => {}} />
    </ApplicationDistributionContext.Provider>);
    expect(html).toBe("");
  });

  it("forwards identity and callbacks to the injected components", () => {
    const saved = vi.fn(), dirty = vi.fn(), close = vi.fn();
    const editor = vi.fn(() => <span>Listing extension</span>);
    const dialog = vi.fn(() => <span>Provider extension</span>);
    const provider = { id: "p" } as ProviderConnection;
    const html = renderToString(<ApplicationDistributionContext.Provider value={{ ...base, resourcePublishing: { ...extension, ListingEditor: editor, ProviderDialog: dialog } }}>
      <ResourceListingEditor kind="data-assets" resourceId="d" resourceName="Dataset" onSaved={saved} onDirtyChange={dirty} />
      <ProviderPublicationDialog provider={provider} onClose={close} />
    </ApplicationDistributionContext.Provider>);
    expect(html).toContain("Listing extension");
    expect(html).toContain("Provider extension");
    expect(editor).toHaveBeenCalledWith(expect.objectContaining({ kind: "data-assets", resourceId: "d", onSaved: saved, onDirtyChange: dirty }), undefined);
    expect(dialog).toHaveBeenCalledWith(expect.objectContaining({ provider, onClose: close }), undefined);
  });

  it("does not mount a closed Provider dialog", () => {
    const dialog = vi.fn(() => null);
    renderToString(<ApplicationDistributionContext.Provider value={{ ...base, resourcePublishing: { ...extension, ProviderDialog: dialog, ProviderModelDialog: dialog } }}>
      <ProviderPublicationDialog provider={null} onClose={() => {}} />
      <ProviderModelSettingsDialog provider={{ id: "p" } as ProviderConnection} offer={null} onClose={() => {}} />
    </ApplicationDistributionContext.Provider>);
    expect(dialog).not.toHaveBeenCalled();
  });

  it("rejects missing composition and incomplete publishing registration", () => {
    expect(() => renderToString(<ResourceListingEditor kind="agents" resourceId="a" resourceName="Agent" />)).toThrow(/not configured/);
    expect(() => validateApplicationDistribution({ ...base, resourcePublishing: {} } as ApplicationDistribution)).toThrow(/incomplete/);
    expect(() => validateApplicationDistribution({ ...base, resourcePublishing: { ...extension, providerPolicy: {} } } as unknown as ApplicationDistribution)).toThrow(/incomplete/);
  });

  it("forwards the selected model and close callback to the private editor", () => {
    const editor = vi.fn(() => <span>Model settings</span>);
    const onClose = vi.fn();
    const provider = { id: "p" } as ProviderConnection;
    const offer = { id: "m" } as ProviderRuntimeModelOffer;
    const html = renderToString(<ApplicationDistributionContext.Provider value={{ ...base, resourcePublishing: { ...extension, ProviderModelDialog: editor } }}>
      <ProviderModelSettingsDialog provider={provider} offer={offer} onClose={onClose} />
    </ApplicationDistributionContext.Provider>);
    expect(html).toContain("Model settings");
    expect(editor).toHaveBeenCalledWith(expect.objectContaining({ provider, offer, onClose }), undefined);
  });
});
