import type { BrowserContext } from "@playwright/test";

export async function mockWorkspace(context: BrowserContext, authenticated: boolean) {
  await context.route("**/api/v1/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    let data: unknown;
    if (pathname === "/api/v1/public/bootstrap/") data = {
      session_authenticated: authenticated, product_name: "Nexus Community", authentication_mode: "on_demand", capabilities: [],
      authentication: { google: { enabled: false }, github: { enabled: false } },
    };
    else if (pathname === "/api/v1/auth/whoami/") data = {
      user_id: "owner", email: "owner@example.test", display_name: "Providers", is_superuser: false,
      current_tenant: "personal", tenant_id: "personal", roles: ["owner"],
    };
    else if (pathname === "/api/v1/personal/context/") data = {
      tenants: [{ id: "personal", name: "My workspace", slug: "personal", status: "active" }],
      projects: [],
    };
    else if (pathname === "/api/v1/account/me/") data = {
      user_id: "owner", email: "owner@example.test", username: "owner", display_name: "Providers",
      phone: "", company: "", status: "active", tenant_id: "personal", project_id: "",
      last_login_at: "2026-09-15T01:02:03Z",
    };
    else if (pathname.includes("summary")) data = { unread: 0, needs_action: 0, in_progress: 0, completed: 0, failed: 0 };
    else if (pathname.includes("stream")) { await route.fulfill({ status: 200, contentType: "text/event-stream", body: ": test\n\n" }); return; }
    else data = { items: [], results: [], next_cursor: null, has_more: false, total: 0 };
    await route.fulfill({ json: { ok: true, data, error: null, request_id: "localization-test" } });
  });
}
