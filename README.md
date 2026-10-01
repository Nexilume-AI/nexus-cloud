<div align="center">

# Nexus Cloud Community

**Your Agents. Your models. Your devices.**

[![License: Nexus Community](https://img.shields.io/badge/License-Nexus_Community-17251d.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/Read-the_docs-b8ef73.svg)](nexus_server/nexus_personal/WORKFLOWS.md)
[![Cite the technical report](https://img.shields.io/badge/Cite-technical_report-e8e9e4.svg)](#citation)

`Self-hosted` · `Docker Compose` · `Single owner`

**English** · [Chinese](README_zh.md)

[Highlights](#highlights) · [Quick start](#quick-start) · [Documentation](#documentation) · [Ecosystem](#ecosystem) · [Contributing](#contributing) · [Citation](#citation)

</div>

A self-hosted, single-owner workspace for running Agents, routing model requests, managing Data Assets and connecting authorized devices.

![Nexus Cloud Community: illustrated workflow](docs/media/overview.svg)

*Workflow illustration, not a product screenshot. Connections require the setup and authorization described below.*

## See it in action

 An isolated demo account runs a
small Python Agent in a real Docker container. The example is deterministic: no
paid model, personal Computer or Mobile is used. Enterprise menus and commercial
features shown here.

### Ask, confirm, inspect

![Private Display asks the caller to choose the checklist audience](docs/media/enterprise-inline-question.jpg)

*An SDK `chat.ask()` question stays inline with the conversation. The caller
chooses the audience before the Agent continues.*

<details>
<summary>See the live plan, completed conversation and Markdown output</summary>

![The SDK plan tracks the three steps of the private Run](docs/media/enterprise-run-plan.jpg)

*Plan updates come from the running Agent, not a presentation mockup.*

![The private Run completed and returned its checklist](docs/media/enterprise-run-completed.jpg)

![The generated Markdown checklist opens in Files preview](docs/media/enterprise-file-preview.jpg)

*Files distinguishes outputs and provides a preview and download controls.*

![The approved output archived in a private Data Assets collection](docs/media/enterprise-data-assets.jpg)

*The scanned file was archived to the demo Project's collection. No public release
or Marketplace publication was created.*

</details>

**Try the same example:** upload [readme_launch_agent.py](examples/readme_launch_agent.py)
through **Agents → Runtime → Upload Python**, build, deploy, then choose **Test Agent
privately**. Ask for a launch checklist and answer the audience question. A supported
Python build profile, execution worker and private file storage must be configured.
This installation requires **Data Assets → Scan output** approval before file preview;
the linked capture guide includes that step.

[Capture details and reproduction steps](docs/media/capture-notes.md). These are
original screenshots, not a video or a claim that every integration was tested.

## Highlights

| Bring your own | What you can do | First guide |
| --- | --- | --- |
| **Models** | Connect Providers, organize Sources and Pools, expose routing endpoints | [Workflows](nexus_server/nexus_personal/WORKFLOWS.md) |
| **Agents** | Manage versions and deployments; interact through Private Display | [Execution setup](nexus_server/nexus_personal/HOST.md) |
| **Data** | Manage files and Data Assets for supported Agent workflows | [Workflows](nexus_server/nexus_personal/WORKFLOWS.md) |
| **Devices** | Attach separately installed Computer, Mobile or OpenWrt runtimes | [Operator guide](nexus_server/nexus_personal/HOST.md) |

## Quick start

**Before you start:** Git, Docker with Linux containers, Compose v2 and at least 4 GiB available memory on the recommended x86_64 host. Repository access is required.

```sh
git clone https://github.com/Nexilume-AI/nexus-cloud-community.git
cd nexus-cloud-community
docker compose -f deploy/community/compose.yaml up -d --build --wait
```

Open **http://127.0.0.1:18090**. Sign in as **owner@example.local** using the generated password retrieved locally:

```sh
docker compose -f deploy/community/compose.yaml run --rm --no-deps --entrypoint cat initialize /var/lib/nexus-bootstrap/owner-password
```

Change that password in Settings. First startup builds images and initializes storage; subsequent starts reuse persisted data.

> [!IMPORTANT]
> This starts Cloud, not a ready-made Agent execution fleet. Configure execution controllers separately. Remote Computer/Mobile pairing requires working HTTPS/WSS. Follow the [Docker deployment guide](deploy/community/README.md) before exposing the installation. Upstream model usage may incur charges.

## Your first workflow

1. Connect a Provider and verify its models. Direct API needs only your existing
   endpoint. **Codex Proxy** / CLIProxyAPI require the explicit
   [Provider execution setup](deploy/community/PROVIDERS.md), available for
   native and Compose installs. Docker Engine is a host prerequisite, not a pip
   dependency; the default Cloud stack does not receive the Docker socket.
2. Create a Pool and Router for model access when your workflow needs one.
3. Configure execution, deploy an Agent and check runtime health.
4. Open Private Display, send a message and inspect the result.
5. Pair and attach a Computer or Mobile only if the Agent needs it.

**Success looks like:** a healthy deployed runtime, a completed Run and its returned result or outputs. A paired device must also be online and authorized; pairing alone does not grant access.

## Community or Enterprise?

Choose the edition for your deployment:

| | Community | Enterprise |
| --- | --- | --- |
| Intended use | Authenticated, single-owner self-hosting | Organization and commercial operations |
| Agents, Private Display, model routing, Data Assets and device integrations | Shared product capabilities; setup required | Shared product capabilities; setup required |
| Access administration | Not included | Organization / Project roles and machine identities |
| Billing and TokenBank | Not included | Commercial billing and accounting capabilities |
| Agent, model and data Marketplaces | Not included | Marketplace workflows |
| Source distribution | Source-available community source in this repository | Separately distributed proprietary implementation |

Enterprise can be featured here alongside Community. Screenshots and recordings must identify the edition used; an Enterprise demonstration is not proof that its menus or commercial features ship in Community. Publishing Enterprise product media does not publish its source code.

[Compare editions in detail](README_GUIDE.md#community-or-enterprise). Do not point Community and Enterprise at the same database.

## Documentation

| Goal | Guide |
| --- | --- |
| Docker deployment, persistence and backups | [Docker guide](deploy/community/README.md) |
| Native Linux installation | [Linux guide](nexus_server/nexus_personal/LINUX.md) |
| Native Windows, execution and HTTPS/WSS | [Operator guide](nexus_server/nexus_personal/HOST.md) |
| Complete a model/Agent/device workflow | [Workflows](nexus_server/nexus_personal/WORKFLOWS.md) |
| Configure router-facing Relay | [Relay setup](README_GUIDE.md#included-relay-server) |
| Review compatibility before upgrading | [Release notes](RELEASE_NOTES.md) |
| Source layout, provenance and detailed setup | [Installation reference](README_GUIDE.md) |

The reference retains the previous setup and troubleshooting material. Historical comparisons and test statements there are dated evidence, not live status.

## Ecosystem

| Project | Role | Install separately? |
| --- | --- | --- |
| [Cloud Community](https://github.com/Nexilume-AI/nexus-cloud-community) | Server, Web Console and bundled Cloud Relay | Main workspace |
| [Python SDK](https://github.com/Nexilume-AI/nexus-agent-sdk-python) | Agent applications and outbound Computer Runtime | Yes |
| [OpenWrt](https://github.com/Nexilume-AI/nexus-openwrt) | Edge registration and capability routing | Optional |
| [Mobile](https://github.com/Nexilume-AI/nexus-mobile) | Authorized Android device integration | Optional |
| [Documentation](https://github.com/Nexilume-AI/nexus-docs) | User guides and reference | Read online or build locally |

Repository access, release availability and compatibility determine which integrations you can install. Cloud installation does not install device runtimes.

## Contributing

Small reproducible fixes, clearer tutorials, translations and sanitized examples are welcome. Before submitting a change, follow the setup and checks for the component you touch. Use [Issues](https://github.com/Nexilume-AI/nexus-cloud-community/issues) for reproducible bugs; include versions and redacted diagnostics, never credentials or private files.

Report sensitive security issues privately to the repository maintainers. Release checks and CI are not a guarantee of production readiness on every platform.

## Citation

If Nexus supports your research or engineering work, please cite the technical report below, rather than the software repository. [CITATION.cff](CITATION.cff) provides the same report metadata through `preferred-citation`.

Nexilume Research. *Nexus: Operating AI Agents Beyond the Cloud*. Technical Report NX-SYS-2026-001, v0.56-E3, September 2026. Research Draft.

```bibtex
@techreport{nexilume2026nexus,
  author      = {{Nexilume Research}},
  title       = {{Nexus}: Operating {AI} Agents Beyond the Cloud},
  institution = {Nexilume Research},
  type        = {Technical Report},
  number      = {NX-SYS-2026-001},
  year        = {2026},
  month       = sep,
  note        = {Version v0.56-E3; Research Draft}
}
```

## License

Nexus-authored source is distributed under [Nexus Community License 1.0](LICENSE). Third-party components retain their own licenses and notices. Documentation does not grant rights to separately distributed Enterprise implementation.

### Licensing conditions

Source-available, not unmodified Apache-2.0 or OSI-approved open source. Multi-tenant service operation and removal of existing Nexus UI branding require prior written authorization. Earlier Apache-2.0 grants and third-party licenses remain unchanged. Contributions require explicit agreement permitting commercial use and future relicensing. See [LICENSING.md](LICENSING.md). Authorization contact: **cary.nexilume@outlook.com**.
