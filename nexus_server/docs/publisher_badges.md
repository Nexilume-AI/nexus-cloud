# Automatically generated publication badges

Each publicly listed Agent has a deterministic product badge. Its UUID selects the
color and geometric details. A currently public, normalized raster logo is embedded
when available; otherwise the generator supplies a geometric mark. Renaming an Agent
does not change its visual identity. No per-product configuration or image generation
service is required.

Publisher identity follows the Marketplace publishing workspace/organization, not
the currently signed-in viewer or an arbitrary team member. The existing masked
publisher name is preserved. Publisher badges count only currently eligible public
catalog listings. They recognize publication, not certification, quality or uptime.

## Hosted Marketplace

Open an Agent's Marketplace detail page and select **Share your badge**. Choose
Product or Publisher, Compact or Honor card, then copy Markdown or HTML. A downloaded
SVG is a snapshot; embedding its URL keeps it updated.

- `GET /api/v1/marketplace/badges/agents/{agent_id}.svg`
- `GET /api/v1/marketplace/badges/publishers/{publisher_id}.svg`
- Append `?style=card` for the larger honor card; compact is the default.
- `GET /api/v1/marketplace/badges/agents/{agent_id}/` returns public image and target URLs.
- `GET /api/v1/marketplace/publishers/{publisher_id}/` displays public Agent cards, 24 per page.

Images do not require authentication or API keys. SVGs have no scripts, external
resources or arbitrary user markup. Publication eligibility is rechecked before
conditional ETag responses. Private, suspended, removed and no-longer-listed Agents
return 404; a publisher without public listings does too. Images cache for five
minutes. GitHub image proxies and other third-party caches can retain older images
longer; downloaded copies cannot be revoked remotely.

## Open-source building blocks

`apps.common.publisher_badges.render_badge` is a pure SVG generator with no Django
or Marketplace dependency. `PublisherBadgePanel` is the reusable React embed panel.
The hosted Marketplace catalog adapter lives in the existing Enterprise distribution;
the Personal distribution does not install or advertise hosted catalog routes.

The panel expects the usual `{ok: true, data: {agent, publisher}}` envelope, each
entry containing `name`, `image_url`, and `target_url`. Custom hosts must enforce their
own publication visibility before calling the renderer, supply a trusted public
origin, and never include private names, credentials or external image URLs.
