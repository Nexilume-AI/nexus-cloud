import { Bot } from "lucide-react";
import { MemoryRouter } from "react-router-dom";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ShellNavigation } from "./ShellNavigation";
import { coreNavigationDomains, type NavigationDomain } from "./navigation";

describe("shared navigation decoration slot", () => {
  it("retains standard icons and capability stages when no decoration is configured", () => {
    const html = renderToString(<MemoryRouter><ShellNavigation domains={coreNavigationDomains} currentPath="/agents" expandedDomain="build"
      compact={false} onToggleDomain={() => {}} onNavigate={() => {}} onToggleCompact={() => {}} /></MemoryRouter>);
    expect(html).toContain('nexilume-shell-link__icon');
    expect(html).toContain('nexilume-shell-link__stage');
    expect(html).toContain('aria-current="page"');
    expect(html).toContain('href="/routers"');
  });

  it("renders a build-owned decoration without taking over the link's accessibility or navigation", () => {
    const onNavigate = vi.fn();
    const domains: NavigationDomain[] = [{ id: "build", label: "Build", verb: "Create", description: "Build resources", lanes: [{
      id: "extension", variant: "extension", items: [{ to: "/test-resource", label: "Test resource", description: "Build-owned destination", icon: Bot,
        decoration: <span data-testid="custom-symbol" aria-hidden="true">Symbol</span> }],
    }] }];
    const html = renderToString(<MemoryRouter initialEntries={["/test-resource/detail"]}><ShellNavigation domains={domains} currentPath="/test-resource/detail"
      expandedDomain="build" compact={false} onToggleDomain={() => {}} onNavigate={onNavigate} onToggleCompact={() => {}} /></MemoryRouter>);
    expect(html).toContain('data-testid="custom-symbol"');
    expect(html).toContain('href="/test-resource"');
    expect(html).toContain('aria-current="page"');
    expect(html).toContain('nexilume-shell-link--extension is-active');
    expect(html).toContain('Test resource');
    expect(onNavigate).not.toHaveBeenCalled();
  });
});
