<div align="center">

# Nexus Cloud

**Your Agents. Your models. Your devices.**

[![License: Apache-2.0 modified](https://img.shields.io/badge/License-Apache--2.0_modified-17251d.svg)](LICENSE)
[![在线体验](https://img.shields.io/badge/Try-Nexus_Cloud-b8ef73.svg)](https://cloud.nexilume.com/)
[![文档](https://img.shields.io/badge/Read-the_docs-b8ef73.svg)](nexus_server/nexus_personal/WORKFLOWS.md)
[![引用技术报告](https://img.shields.io/badge/Cite-technical_report-e8e9e4.svg)](#引用)


[English](README.md) · **简体中文**

[设计动机](#设计动机) · [看一次完整流程](#真实运行画面) · [核心功能](#核心功能) · [使用场景](#使用场景) · [快速开始](#快速开始) · [生态安装包](#项目生态) · [参与贡献](#参与贡献) · [引用](#引用)

</div>

> **[在线体验 Nexus Cloud](https://cloud.nexilume.com/)**：在浏览器中探索 Nexus Cloud，也可以自行部署，开始使用。


![Nexus Cloud 流程示意图](docs/media/overview.svg)

## 设计动机

| 产品 | 从用户视角看 |
| --- | --- |
| **Nexus** | **别人开发和部署 Agent，我选择自己的设备让它执行。** |
| OpenClaw | 我部署和配置一套助手，让它通过渠道和节点替我工作。 |
| Codex | 我选择工作环境，把任务交给 Codex，检查并迭代结果。 |



你的文件可能
在电脑上，下一项任务需要浏览器，另一个流程又需要手机。你不应该为了使用 Agent，
就把工作环境搬到开发者的机器上，或在自己的设备上逐个部署 Agent 及其依赖。

Nexus 让部署在别处的 Agent 使用你 Attach 并授权的设备。对你而言，这意味着：

- **少安装，少维护。** 使用云端或开发者托管的 Agent，不必先在自己的电脑上部署
  这个 Agent。设备 Runtime 配对一次后，即可按需授权不同 Agent 使用它。
- **在自己的工作环境中完成任务。** 让 Agent 处理授权工作目录里的文件，使用
  Computer 的终端或隔离浏览器，或操作已 Attach 的手机。设备由你选择，所需权限
  由你批准。
- **按任务选择设备。** 不同任务可以让同一个 Agent 使用不同的受支持设备，不必
  为每台设备重新部署 Agent；Agent 与设备也不必处于同一台机器或同一局域网。
- **在同一个地方跟进工作。** 通过 Private Display 对话、查看进展、回答问题、
  补充指令和检查结果，即使实际操作发生在另一台已连接的设备上，交互入口依然一致。

例如，一个托管在 Docker 中的 Agent，可以处理你授权的 Computer 工作目录中的
文件，再将结果返回 Private Display；你不需要先把这个 Agent 部署到该 Computer。

这就是**解耦的 Agent 执行 Fabric**：**将 Agent 的部署位置与它能够操作的用户
设备解耦，并通过统一的执行上下文 Run Context 连接起来。**

![Agent 与调用者通过 Nexus Run Context，连接调用者已绑定并授权的 Computer 和 Mobile](docs/media/execution-fabric.svg)

对开发者而言，这种分离也减少了设备连接、交互界面和执行状态管理的重复建设。
Nexus 提供 Python Agent 构建、部署、Run 授权和可观测能力；Enterprise 进一步
提供商业计费。

设备仍需安装兼容的 Runtime、保持网络可达并完成显式授权。配对不等于授予 Agent
访问权限；选择另一台设备，也不会自动迁移正在运行的终端或浏览器会话。

**Agent 可以部署在别处，任务可以在你选择的设备上完成。**

## 真实运行画面

**Agent 在 Docker 中，CSV 在你的电脑上，报告经你确认后生成。**

你想把电脑上的 `expenses.csv` 整理成一份简短报告。开发者已经部署好 Agent，
你无需在本机安装它的代码和依赖，只需配对一次 Nexus Computer Runtime。

### 1. 选好 Agent，再选择自己的电脑

在 **Marketplace** 找到助手，打开详情并点击 **Attach Computer**。
批准它请求的文件权限，再选择自己的在线设备。

![从 Marketplace 发现 Agent、批准文件权限并绑定 My demo Computer](docs/media/device-demo-marketplace.gif)

*Agent 已经托管运行。你提供的是获授权的执行环境，而不是再部署一份 Agent。*

### 2. 说出任务，关键步骤由你确认

打开 **Private Display**，输入：“把我的 expenses.csv 整理成简短报告，写入前先问我。”
Agent 通过 Run Context 读取文件、更新 Plan，并在**同一段对话中**请求确认。

![Private Display 中真实读取文件、内联确认并查看 SDK Plan](docs/media/device-demo-confirm.gif)

*样例包含 3 条合成费用，共 $128.50。只有点击 **Save my report** 后才会写入报告。*

### 3. 结果回到你需要的位置

Agent 在 **Attached Computer** 的 CSV 旁写入 `expense-report.md`，并提供私有副本，
可在 **Files** 中预览、下载。原始 CSV 未被修改；Computer 文件、Run 产物和浏览器
下载文件的 SHA-256 一致。

![Run 完成后在 Files 中预览生成的 Markdown 报告](docs/media/device-demo-result.gif)

**Agent 部署一次，设备由你选择，过程在一个界面里跟进。**

<details>
<summary>自己运行此例：源码、准备步骤与采集说明</summary>

1. 配置受支持的 Python 构建 Worker、Docker 执行环境和私有文件存储。在
   **Build → Agents → Runtime → Upload Python** 上传
   [readme_computer_agent.py](examples/readme_computer_agent.py)，构建并部署。
2. 为 Nexus Computer Runtime 配对一个隔离测试 Workspace，Attach 到 Agent，
   批准 `files.list`、`files.read`、`files.write`，不需要终端或浏览器权限。
3. 将 [expenses.csv](examples/expenses.csv) 放进 Computer 上该 Agent 的工作目录
   （本次为 `<paired-root>/agents/<agent-id>/workspace/`），打开 Private Display 发起任务。
4. 点击 **Save my report**。若安装环境要求产物扫描，须由有权限的操作员扫描并批准
   该私有产物，之后才能预览和下载。

此例采用确定性逻辑，不需要模型 API Key，演示的是设备执行链路，而不是模型推理能力。
文件内容会经过已授权的 Cloud 执行链路，不代表数据始终不离开电脑。

画面采集自现有 **Enterprise** 安装，使用真实 Docker Agent、Windows Computer Runtime
和独立演示空间。Marketplace 是企业版入口；自托管用户可从 **Build → Agents** 打开
该 Agent。GIF 由真实浏览器关键帧剪辑，缩短了等待，不是界面设计稿、连续视频或速度测试。

[验收结果、边界与复现记录](docs/media/device-demo-notes.md)
· [采集校验信息](docs/media/device-demo-manifest.json)

</details>

<a id="可以做什么"></a>

## 核心功能

- **Agent 部署与用户设备解耦。** Agent 可托管在 Docker、开发者电脑或通过
  OpenWrt 接入的边缘环境中；每位用户自行选择并授权实际执行任务的设备。
- **连接电脑与 Android 手机。** 通过 Attached Computer 和 Mobile 使用文件、
  终端、隔离浏览器及受支持的 Android 操作。Computer Runtime 与 Android 应用
  主动向 Cloud 建立出站连接，不要求 Cloud 通过入站 SSH 访问设备。
- **Python SDK 与 MCP 集成。** 将 Python 能力变成可调用的 Agent 服务，保留
  自己选择的模型和 Agent 框架，复用 Nexus 的服务接入、设备访问及用户交互能力。
- **统一的 Agent 交互界面。** Private Display 集中展示对话、进度、Plan、
  内联问题、浏览器画面与文件产物，减少为每个 Agent 单独开发交互界面的工作。
- **用户授权的执行环境。** 用户显式 Attach 设备并批准所需权限；Run 级委托将
  每次调用绑定到相应的调用者、设备和工作目录。
- **执行历史与可观测性。** 跟踪 Run、检查错误、查看终端输出并获取生成的产物。
  受支持的工作流可在后续轮次继续，并保留执行上下文。
- **模型 API 路由。** 接入 Provider，通过 Execution Router 和 Aggregation
  Router 组织模型访问，为应用提供统一的已配置模型 API。
- **文件与可复用数据资产。** 导入文件和图片、预览受支持的格式，并将 Agent
  生成的产物归档到 Data Assets；按安装配置执行安全扫描和访问控制。
- **Agent 分发与商业化。** 商业版支持通过 Marketplace 发布 Agent，并采用
  固定费用或 Agent 回报实际费用的计费方式。

Marketplace、Organization 权限管理与计费属于 Enterprise 能力，详见
[社区版与企业版](#社区版与企业版)。设备操作需要兼容的 Runtime、可用的网络连接
和显式授权。

## 使用场景

| 场景 | Nexus 如何帮助 |
| --- | --- |
| **为他人提供文件助手** | 报告 Agent 部署一次，用户 Attach 自己的电脑并授权工作目录，即可分析表格或生成文档，无需在本机安装该 Agent。 |
| **在用户电脑上自动操作浏览器** | 在 Attached Computer 的隔离浏览器中打开网站、填写表单和收集结果，并在 Private Display 查看截图与进展。 |
| **电脑与手机协同工作** | 将电脑上的文件处理与已授权的 Android 操作组合起来，例如在电脑上准备信息，再用于手机工作流。 |
| **开发与运维助手** | 检查获授权的工作目录、执行已批准的命令、分析日志并生成诊断报告，用户可以同步跟进过程。 |
| **边缘托管 Agent 服务** | Agent 保留在开发者电脑或 OpenWrt 接入的网络中，通过 Nexus 提供服务，不必将应用迁入 Cloud 容器。 |
| **面向多用户的 Agent 产品** | 在 Enterprise 中，让多个用户使用同一份已部署 Agent，每位调用者提供自己的授权设备、工作目录和私有交互会话。 |
| **统一模型访问入口** | 将已配置的模型 Provider 接入 Router API，让应用使用统一接口，并集中管理路由策略。 |

**Nexus 将 Agent 的运行位置，与用户需要完成工作的地方分开。**

## 快速开始

需要 Git、使用 Linux 容器的 Docker、Compose v2；推荐 x86_64 主机且至少有 4 GiB 可用内存，并具备仓库访问权限。

```sh
git clone https://github.com/Nexilume-AI/nexus-cloud.git
cd nexus-cloud
docker compose -f deploy/community/compose.yaml up -d --build --wait
```

打开 **http://127.0.0.1:18090**，账号为 **owner@example.local**。在本机读取初始随机密码：

```sh
docker compose -f deploy/community/compose.yaml run --rm --no-deps --entrypoint cat initialize /var/lib/nexus-bootstrap/owner-password
```

登录后修改密码。该命令启动 Cloud，不会自动安装设备 Runtime，也不会自动完成 Agent/Provider 执行控制器配置。远程设备需要可用的 HTTPS/WSS；模型供应商可能另外收费。

## 第一个完整流程

1. 连接 Provider 并确认模型可用。Direct API 使用已有接口；**Codex Proxy** / CLIProxyAPI 需额外配置 [Provider 执行环境](deploy/community/PROVIDERS.md)，支持原生安装命令与可选 Compose。Docker Engine 是宿主机前提，不由 pip 自动安装；默认 Cloud 不挂载 Docker socket。
2. 按需创建 Model Pool 与 Router。
3. 配置执行环境，部署 Agent 并检查健康状态。
4. 在 Private Display 发起任务，查看回复与文件产物。
5. 仅在任务需要时配对、Attach 并授权设备。

## 文档与边界

| 目标 | 入口 |
| --- | --- |
| Docker、持久化与备份 | [部署指南](deploy/community/README.md) |
| Linux 原生安装 | [Linux 指南](nexus_server/nexus_personal/LINUX.md) |
| Windows、执行环境、HTTPS/WSS | [运维指南](nexus_server/nexus_personal/HOST.md) |
| 模型、Agent、设备流程 | [工作流](nexus_server/nexus_personal/WORKFLOWS.md) |
| Relay 与详细安装参考 | [完整指南](README_GUIDE.md) |
| 升级注意事项 | [Release notes](RELEASE_NOTES.md) |

## 社区版与企业版

| | Community 社区版 | Enterprise 企业版 |
| --- | --- | --- |
| 适用场景 | 需要认证的单用户自托管 | 组织协作与商业运营 |
| Agent、Private Display、模型路由、Data Assets 与设备集成 | 共有产品能力，需要完成配置 | 共有产品能力，需要完成配置 |
| Access 管理 | 不包含 | Organization / Project 角色与机器身份 |
| Billing 与 TokenBank | 不包含 | 商业计费与账务能力 |
| Agent、模型与数据 Marketplace | 不包含 | Marketplace 工作流 |
| 源码发布 | 本仓库 源码可用的社区源码 | 独立分发的闭源实现 |




## 项目生态

按需安装生态组件。Cloud、SDK 与设备 Runtime 独立分发。

| 组件 | 用途 | 下载 / 安装 |
| --- | --- | --- |
| **[Nexus Cloud](https://github.com/Nexilume-AI/nexus-cloud)** | Server、Web Console 与配套 Cloud Relay | [快速开始](#快速开始) |
| **Python SDK** | 开发 Agent 应用 | [PyPI：nexilume](https://pypi.org/project/nexilume/) · [Wheel / 源码包](https://pypi.org/project/nexilume/#files) · [SDK 指南](https://github.com/Nexilume-AI/nexus-agent-sdk-python#quick-start) |
| **Computer Runtime** | 连接电脑，执行已授权的文件、终端与浏览器操作 | 包含在 SDK 中 · [安装指南](https://github.com/Nexilume-AI/nexus-agent-sdk-python#connect-your-computer) |
| **Android 应用** | 连接手机，参与已授权的 Agent 工作流 | [下载 APK · 0.1.2-beta.2](https://github.com/Nexilume-AI/nexus-mobile/releases/download/v0.1.2-beta.2/nexus-mobile-0.1.2-beta.2.apk) · [发行说明与校验值](https://github.com/Nexilume-AI/nexus-mobile/releases/tag/v0.1.2-beta.2) |
| **OpenWrt** | 边缘注册、发现与能力路由 | [下载 x86_64 安装包 · Beta](https://github.com/Nexilume-AI/nexus-openwrt/releases/tag/v0.1.0-beta.1) · [安装指南](https://github.com/Nexilume-AI/nexus-openwrt/blob/main/docs/package-install.md) |
| **Documentation** | 教程与参考 | [阅读文档](https://github.com/Nexilume-AI/nexus-docs) |

**安装 Python SDK：**

```sh
python -m pip install --upgrade nexilume
```

使用 MCP 集成时，改为安装 `"nexilume[fastmcp]"`。安装包名为 `nexilume`，Python 导入名仍为 `nexus_agent`。

**连接 Computer，包含可选浏览器支持：**

```sh
python -m pip install --upgrade "nexilume[computer,browser]"
nexus-computer setup "<pairing-url-from-your-cloud>"
```

建议使用虚拟环境；可选集成推荐 Python 3.12。浏览器控制还需要在该 Computer 上安装兼容的 Chromium 浏览器。

**Android：**需要 Android 8.0+，下载的 APK 为已签名的 **Beta** 版本。安装前请查看发行说明与校验值；也可浏览[全部 Android 发行版本](https://github.com/Nexilume-AI/nexus-mobile/releases)。

在自己的 Cloud Console 中生成 Computer 配对链接或 Mobile 配对二维码。配对后仍需 Attach 设备，并批准 Agent 所需的权限；安装 Cloud 不会自动安装这些组件或授予设备访问权限。

## 参与贡献

欢迎可复现的问题修复、教程、翻译与脱敏示例；提交前请执行所改组件的检查。

问题反馈请附组件版本与脱敏复现步骤，不要上传凭据、个人文件或真实设备配置。安全问题请私下联系仓库维护者。 CI 通过不等于所有平台均已完成生产验收。

## 引用

如果 Nexus 对你的研究或工程工作有帮助，请引用以下技术报告，而不是软件仓库。[CITATION.cff](CITATION.cff) 的 `preferred-citation` 提供同一报告的机器可读元数据。

Nexilume Research. *Nexus: An Execution Fabric for AI Agents Across Cloud, Edge, and Devices*. 技术报告 NX-SYS-2026-001，2026 年 9 月。

```bibtex
@techreport{nexilume2026nexus,
  author      = {{Nexilume Research}},
  title       = {{Nexus}: An Execution Fabric for {AI} Agents Across Cloud, Edge, and Devices},
  institution = {Nexilume Research},
  type        = {Technical Report},
  number      = {NX-SYS-2026-001},
  year        = {2026},
  month       = sep
}
```

## 许可证

Nexus 自有代码采用 [Apache License 2.0 (modified)](LICENSE)。第三方组件保留各自许可证与声明；公开文档不授予独立企业版实现的使用权。

### 许可条件

Nexus 采用 Apache License 2.0 的修改版，并附加以下条件。多租户服务运营及移除现有 Nexus 界面品牌标识须事先取得书面授权。此前的 Apache-2.0 授权和第三方许可证保持不变。贡献者须明确同意允许商业使用及未来重新许可的贡献协议。许可说明：[LICENSING.md](LICENSING.md)。授权联系：**cary.nexilume@outlook.com**。
