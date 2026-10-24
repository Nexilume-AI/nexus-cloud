# Connect-first Agent Marketplace

This release includes a portable `AgentMarketplaceLayout` component and an exact integration patch for Organization-enabled Marketplace hosts. Personal deployments do not include commercial Marketplace routes; this change does not add those routes or their backend.

The layout exposes a connection card independently of Overview, Tools and Details. On wide detail panes the card sits alongside the content; narrow panes place it above the tabs. The host supplies pricing, device requirements, tab content and a connection handler. It performs no authentication or credential creation itself.

The hosted integration moves Connect Agent into the title area, opens the existing MCP client configuration in a drawer, keeps Open Chat secondary and limited to chat-capable Agents, moves device setup into the connection card, and places badge sharing beside the publisher. Only required or optional devices are shown. Price, permissions and health facts remain accessible under Details. Preparing configuration does not claim a verified client connection.

Apply the companion patch to a compatible Organization-enabled source tree after installing the shared component and localization. Hosted deployment was built with TypeScript/Vite and checked in a real browser.
