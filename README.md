<div align='center'>
    <img src="docs/media/cover.jpg" alt="agentchat：连接模型、知识与工具" width="100%">
</div>

<div align="center">

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue.svg?style=for-the-badge&logo=python&logoColor=white" alt="Python Version" />
  <img src="https://img.shields.io/badge/vue-3.4+-4FC08D.svg?style=for-the-badge&logo=vue.js&logoColor=white" alt="Vue Version" />
  <img src="https://img.shields.io/badge/fastapi-0.121+-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge" alt="License" />
</p>

<p align="center">
  <b>连接模型、知识与工具的智能体应用平台</b>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> •
  <a href="#研究-agent">研究 Agent</a> •
  <a href="#本地-docker-沙箱">Docker 沙箱</a> •
  <a href="#高级配置">部署指南</a> •
  <a href="https://shy2593666979.github.io/agentchat-docs/">在线文档</a> •
  <a href="https://agentchat.cloud">在线体验</a> •
  <a href="https://github.com/Shy2593666979/agentchat-docs/blob/main/images/README.md">加入微信交流群🌟</a>
</p>
</div>

---

[English](README_EN.md) · [更新日志](docs/CHANGELOG.md)

## 项目简介

agentchat 是一个前后端分离的智能体应用平台。你可以连接模型服务，为智能体绑定工具、Skill、MCP 和知识库，在会话中使用这些能力，也可以通过 Mars 发起搜索、研究和报告任务，查看执行过程与结果。论文调研助手通过独立状态图执行检索、阅读、证据核验和报告生成；本地 Docker 沙箱承担文档写入、编辑、转换与论文 PDF 解析。

首次运行可以使用 SQLite、内存缓存、本地文件和 Chroma，无需额外部署 MySQL、Redis、MinIO 或 Milvus。需要外部数据库、缓存或对象存储时，再切换对应配置。

## 核心能力

![agentchat 技术架构](docs/media/architecture.png)

| 能力 | 使用方式 |
| --- | --- |
| 灵活会话 | 在同一会话中切换智能体；根据首条问题异步生成 2–10 字话题标题 |
| 智能体 | 选择模型，配置提示词，组合工具、MCP、Skill 与知识库 |
| 可靠任务运行 | 正式聊天使用 `/runs`，持久化任务状态、执行事件、工具账本与图检查点，支持暂停、恢复和结果复用 |
| 论文调研 | 选择 `paper-research`，拆解主题、检索 arXiv / Crossref、阅读公开 PDF、比较并核验来源，导出 Markdown / JSON |
| 按需检索子 Agent | 符合独立工作条件时最多并行运行 2 个只读检索子 Agent，由主 Agent 统一阅读与核验 |
| 本地 Docker 沙箱 | 在固定操作、无网络容器中处理文档与 PDF，返回带 SHA256 的会话产物及鉴权下载入口 |
| 上下文管理 | 保存原始材料、版本化工作记忆与检索片段，按容量组装模型输入，支持原文回读 |
| Mars 任务 | 从探索页发起任务，查看工具执行过程与 Markdown 报告 |
| 工具与 MCP | 使用内置工具、导入 OpenAPI，或连接 SSE / Streamable HTTP MCP 服务；支持对话式创建 MCP 与人工确认 |
| Skill | 启动时加载研究报告、数据分析、产品文档、代码审查、邮件写作、知识整理六类技能模板 |
| 知识库 | 上传资料、向量化并绑定到智能体，检索后重排；可按需启用全文检索 |
| 用户资源隔离 | 注册时获得独立的默认智能体、工具、Skill 和 MCP 副本，编辑不会影响其他用户 |
| 用量与日志 | 记录模型调用和会话 Token 用量，提供数据看板、请求追踪与工具耗时日志 |
| 流式协议 | 通过 AG-UI 适配层统一消息、工具执行和运行状态事件 |

模型服务和外部工具各自有配置要求。基础聊天可先配置一个对话模型；知识库需要可用的向量模型，部分工具还需要单独的 API Key。

聊天默认使用通用助手，保留专用助手切换；支持工具失败重试、任务暂停和检查点恢复，详见 [任务执行与恢复说明](docs/agent-runtime.md)。

选择 [论文调研助手](docs/paper-research.md) 可执行研究主题拆解、论文检索、PDF 阅读、结构化比较与引用核验，并支持调整研究方向及导出带来源的报告。

论文调研助手位于聊天入口，探索页 Mars 是另一个任务入口。基础聊天不要求 Docker；涉及文档写入、转换、论文 PDF 解析及研究报告落盘时必须准备沙箱镜像，Docker 不可用时不会回退到宿主机处理。

既有界面、会话、资源隔离、AG-UI、Token 统计和日志变化见 [更新日志](docs/CHANGELOG.md)；新增能力详见 [任务运行](docs/agent-runtime.md)、[论文调研](docs/paper-research.md)、[上下文机制](docs/context-management.md) 和 [本地沙箱](docs/local-sandbox.md)。

## 目录

- [功能展示](#功能展示)
- [项目结构与运行入口](#项目结构与运行入口)
- [快速开始](#快速开始)
- [首次使用](#首次使用)
- [研究 Agent](#研究-agent)
- [本地 Docker 沙箱](#本地-docker-沙箱)
- [最小示例](#最小示例)
- [高级配置](#高级配置)
- [开发与测试](#开发与测试)
- [当前已知限制与待补充](#当前已知限制与待补充)
- [文档导航](#文档导航)
- [许可证与支持](#许可证与支持)

## 功能展示

### 探索任务演示

在探索页输入一个目标，查看任务执行过程与完整报告：

> 中国四大古都的影响力对比，给我做一份这四个城市的旅游攻略。

https://github.com/user-attachments/assets/640650f3-4134-452a-9d17-6f850dabe95f

从探索页发起任务，查看深度搜索、资料整理与报告生成的完整流程。演示视频经过剪辑，仅展示应用内容。

### 当前界面

#### 数据看板

查看模型调用、Token 用量、七日趋势与最近活动。

![数据看板 · Mingguang 的七日演示数据](docs/media/dashboard.jpg)

#### 模型服务

以六家厂商各一款模型展示名称、厂商图标及接口配置；`show_model` 与实际请求的模型标识分别管理。

![模型服务 · 六家厂商的展示配置](docs/media/models.jpg)

#### 智能体与能力管理

![智能体 · 我的助手](docs/media/agents.jpg)

<table>
<tr>
<td width="50%"><b>工具</b><br>管理内置工具与自定义能力。<br><a href="docs/media/tools.jpg"><img src="docs/media/tools.jpg" alt="工具管理页面" width="100%"></a></td>
<td width="50%"><b>Skill</b><br>将可复用的操作知识组织成技能。<br><a href="docs/media/skills.jpg"><img src="docs/media/skills.jpg" alt="Skill 管理页面" width="100%"></a></td>
</tr>
<tr>
<td width="50%"><b>MCP</b><br>连接外部工具与数据服务。<br><a href="docs/media/mcp.jpg"><img src="docs/media/mcp.jpg" alt="MCP 服务管理页面" width="100%"></a></td>
<td width="50%"><b>知识库</b><br>按主题管理知识空间。<br><a href="docs/media/knowledge.jpg"><img src="docs/media/knowledge.jpg" alt="知识库管理页面" width="100%"></a></td>
</tr>
</table>

<details>
<summary>查看登录页</summary>

![agentchat 登录页](docs/media/login.jpg)

</details>

## 项目结构与运行入口

以下仅列出运行和学习本次新增功能所需的关键路径：

```text
agentchat/
├── src/
│   ├── backend/
│   │   ├── agentchat/
│   │   │   ├── main.py                 # FastAPI 应用、初始化与任务服务生命周期
│   │   │   ├── settings.py             # YAML 配置选择、简易模式默认值
│   │   │   ├── config.simple.yaml      # 轻量本地配置模板
│   │   │   ├── api/v1/runs.py          # 创建、订阅、暂停、恢复、导出任务
│   │   │   ├── core/agents/            # 通用助手、论文调研图、检索子 Agent 等
│   │   │   ├── services/               # 任务调度、上下文、来源读取与 sandbox/
│   │   │   ├── database/               # 业务模型、DAO、初始化与默认资源
│   │   │   └── tools/                  # 搜索、文档转换等工具
│   │   ├── tests/                     # 机制回归、可选真实 Docker 测试
│   │   ├── pyproject.toml              # Python 依赖声明与开发依赖
│   │   └── uv.lock                     # Python 锁定依赖
│   └── frontend/
│       ├── src/pages/conversation/     # 正式聊天与任务状态界面
│       ├── src/apis/runs.ts            # 任务 API、SSE 订阅和产物下载
│       ├── vite.config.ts             # 开发服务器与 /api 代理
│       └── package.json               # 前端依赖与启动、检查、构建命令
├── docker/
│   ├── sandbox/                       # 独立文档沙箱 Dockerfile 与固定 worker
│   └── docker-compose.yml             # 既有整套应用容器部署配置
├── scripts/build-sandbox.ps1           # Windows 沙箱镜像构建脚本
├── docs/                              # 配置、运行、研究、上下文及沙箱说明
└── .github/workflows/ci.yml            # 后端测试及前端检查、构建
```

| 入口 | 职责 |
| --- | --- |
| `agentchat.main:app` | 后端正式启动入口，默认示例监听 `127.0.0.1:7860` |
| `src/frontend` 中的 `npm run dev` | 启动 Vue 3 / Vite 前端，示例端口 `8090` |
| [`core/agents/general_agent.py`](src/backend/agentchat/core/agents/general_agent.py) | 通用助手，内置工具与沙箱工具接入 |
| [`core/agents/paper_research_agent.py`](src/backend/agentchat/core/agents/paper_research_agent.py) | 论文研究状态图；[`research_subagent.py`](src/backend/agentchat/core/agents/research_subagent.py) 决定检索委派与子图执行 |
| [`services/agent_runs.py`](src/backend/agentchat/services/agent_runs.py) / [`run_tools.py`](src/backend/agentchat/services/run_tools.py) | 后台任务生命周期、检查点、工具调用与重试 |
| [`services/research_sources.py`](src/backend/agentchat/services/research_sources.py) / [`context.py`](src/backend/agentchat/services/context.py) | 受控来源读取、证据存储、工作记忆与模型输入容量管理 |
| [`services/sandbox/runtime.py`](src/backend/agentchat/services/sandbox/runtime.py) / [`docker/sandbox/worker.py`](docker/sandbox/worker.py) | 宿主控制器与容器固定操作的边界 |

`scripts/start.py` 也会启动前后端，但其依赖安装逻辑只查找根目录 `request*.txt`，没有使用后端 `uv.lock`。复现请使用下面分步骤的锁定依赖安装方式。

## 快速开始

### 1. 准备环境

- Git、Python 3.12、Node.js 22（与 CI 保持一致）。
- [uv](https://docs.astral.sh/uv/getting-started/installation/)：用于创建 Python 虚拟环境及安装锁定依赖。
- 一个可用的模型 API Key、接口地址和模型标识。以下沿用仓库简易模板的通义兼容接口与 `qwen-plus`，不代表本次已验证服务可用。通用助手需要模型支持工具调用；论文调研代码使用 JSON mode 结构化输出，所选服务也需支持。
- 使用文档工具或完整论文调研流程时，需要 Docker CLI、正在运行的 Linux Docker 引擎与预先构建的沙箱镜像。Windows 可使用 Docker Desktop 的 WSL2 / Linux containers 后端，见 [本地 Docker 沙箱](#本地-docker-沙箱)。

在开始之前，确认终端中 `uv --version` 和 `node --version` 可执行。Python 依赖由 `pyproject.toml` / `uv.lock` 管理，前端依赖由 `package.json` / `package-lock.json` 管理；核心包括 FastAPI、LangChain / LangGraph、SQLModel、Vue 3 和 Vite。

### 2. 获取代码并安装后端依赖

```bash
git clone https://github.com/wuxingyu1231z/agentchat.git
cd agentchat
git checkout 924d66d881955fc874c789ca646d03219da8251a
cd src/backend
uv sync --locked --python 3.12
```

以上固定到本次核对的功能提交，便于复现；需要跟随主分支开发时省略 `git checkout`。

### 3. 创建自己的配置

Windows PowerShell：

```powershell
Copy-Item agentchat/config.simple.yaml agentchat/config.local.simple.yaml
```

Linux / macOS：

```bash
cp agentchat/config.simple.yaml agentchat/config.local.simple.yaml
```

编辑 `src/backend/agentchat/config.local.simple.yaml` 中的对话模型：

```yaml
multi_models:
  chat_model:
    api_key: "填写你的模型 API Key"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_name: "qwen-plus"
```

这里展示的是需要编辑的片段，文件中的其他设置保留。更换模型服务时同时修改 Key、地址与模型标识。`config.local*.yaml` 已被 Git 忽略。简易模式下工具调用和推理默认继承对话模型；知识库的向量和重排有各自接口要求，更换服务后需另行核对，见 [模型配置说明](docs/simple-config.md#模型继承与能力边界)。

论文助手可直接检索 arXiv / Crossref，无需先创建知识库或配置向量模型；这些站点及来源 PDF 必须网络可达。需要补充网页搜索时，可在同一个本地配置文件增加：

```yaml
tools:
  tavily:
    api_key: "填写你的 Tavily 搜索 Key"
```

也可配置 `tools.bocha.api_key` 与 `tools.bocha.endpoint`，具体见 [搜索配置](docs/simple-config.md)。论文助手优先使用 Tavily，其次 Bocha；两者均未配置时仅继续学术检索，并记录网页搜索缺失。搜索 Key 与模型 Key 分别申请、配置，修改后重启后端。

### 4. 启动后端

仍在 `src/backend` 目录执行，并保持终端运行。

Windows PowerShell：

```powershell
$env:AGENTCHAT_CONFIG = "agentchat/config.local.simple.yaml"
uv run --locked uvicorn agentchat.main:app --host 127.0.0.1 --port 7860
```

Linux / macOS：

```bash
export AGENTCHAT_CONFIG=agentchat/config.local.simple.yaml
uv run --locked uvicorn agentchat.main:app --host 127.0.0.1 --port 7860
```

打开 [健康检查](http://127.0.0.1:7860/health)，应返回 `{"status":"OK"}`；接口文档位于 [Swagger UI](http://127.0.0.1:7860/docs)。这表示服务已启动，模型是否可调用还需要发送消息验证。

### 5. 启动前端

另开一个终端，从项目根目录执行：

```bash
cd src/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 8090 --strictPort
```

访问 [http://127.0.0.1:8090](http://127.0.0.1:8090)。前端开发服务器通过 `/api` 代理访问本机 `7860` 端口的后端。

后端地址不同时，在 `src/frontend/.env.local` 中设置 `VITE_API_BASE_URL=http://后端地址:端口`，然后重启前端。后端端口、启动参数与本地文件的访问地址也应保持一致，详见 [简易配置指南](docs/simple-config.md)。

## 首次使用

1. **注册并登录**：新账号会获得自己的工具、Skill 与 MCP 等默认资源；聊天默认选择通用助手，保留论文调研助手和自建智能体入口。知识库按需创建。
2. **确认模型**：配置文件中的 `chat_model` 用于系统默认模型；在模型服务页添加的模型可供智能体选择。展示名称与实际调用标识分别填写，真实调用以服务商要求的标识为准。
3. **开始会话**：选择一个智能体发送问题。同一会话可以切换智能体，话题标题会在后台生成，也可以手动修改。
4. **添加能力**：在智能体编辑页绑定需要的工具、MCP、Skill 或知识库。MCP 出现在列表中不等于已绑定到当前智能体；调用第三方服务前确认其地址和凭据可用。
5. **使用资料与任务**：需要检索资料时先上传到知识库并等待处理完成；需要搜索研究时可从探索页发起 Mars 任务。知识库依赖向量与重排模型，任务依赖实际选用的工具配置。
6. **查看用量**：数据看板展示调用与 Token 统计；统计依据模型返回的 usage，服务商未返回用量时可能不完整。

## 研究 Agent

在智能体列表或聊天输入框旁选择 **论文调研助手**（`paper-research`），输入研究主题与约束。其实现为独立 LangGraph 状态图：

```text
规划 → 按需委派/检索 → 筛选来源 → 阅读 → 结构化比较 → 证据核验 → 报告
```

| 阶段 | 当前实现 |
| --- | --- |
| 规划与检索 | 拆为 2–4 个子问题及英文检索式，检索 arXiv、Crossref 和已配置的网页搜索服务 |
| 按需委派 | 至少 3 个子问题，且至少 2 项为无前置依赖、检索范围不过度重合的实质工作时，最多启用 2 个只读检索子 Agent；主 Agent 汇总去重与核验 |
| 筛选与阅读 | 最多选读 6 篇论文、2 个补充网页；复查论文元数据，尝试读取公开 PDF，并在首页匹配标题或 DOI |
| 比较与核验 | 提取方法、数据集、实验结论、局限性及原文页码；先检查引文与页码，再单独调用模型判断陈述是否得到原文支持 |
| 报告与导出 | 仅使用通过核验的陈述确定性生成比较表和结论，列出来源、获取时间、内容哈希和阅读限制；支持 Markdown 报告、研究 JSON、执行事件 JSONL |

“单独调用模型核验”指独立的一次模型请求，当前实现没有要求使用不同厂商或另一个模型。PDF 身份不匹配、全文获取失败或证据不足会保留记录；无已核实证据时报告明确说明材料不足。

正式聊天任务由后端运行，刷新、关闭页面或切换会话不会取消任务。暂停在当前模型或工具操作返回后的步骤边界生效；暂停、中断或失败后可继续，也可填写新研究方向再恢复，每个任务最多修改方向 10 次。已保存的步骤结果会复用，调整方向后重新规划、检索，符合条件的阅读记录可以复用。

恢复需要同时保留业务数据库和 LangGraph 检查点文件，JSONL 导出本身不能恢复任务。同一会话只能有一个未完成任务；外部邮件、MCP / OpenAPI 等副作用仍可能需要人工核实。详见 [任务运行与恢复](docs/agent-runtime.md) 和 [论文调研流程](docs/paper-research.md)。

## 本地 Docker 沙箱

应用部署容器与文档沙箱分别由 `docker/docker-compose.yml` 和 `docker/sandbox/` 定义。下面采用**宿主机启动后端 + 本地 Docker 执行文档操作**的方式。后端负责联网检索、数据库、模型调用与检查点；容器负责固定文档操作，不接收任意 Shell 命令，也不挂载项目、数据库、模型密钥或 Docker socket。

### 1. 构建镜像

启动 Linux Docker 引擎，在仓库根目录执行：

Windows PowerShell：

```powershell
docker info --format '{{.OSType}}'
powershell -ExecutionPolicy Bypass -File scripts/build-sandbox.ps1
```

Linux / macOS 可直接按同一 Dockerfile 构建：

```bash
docker info --format '{{.OSType}}'
docker build --tag agentchat-sandbox:v1 docker/sandbox
```

`docker info` 的操作系统类型应为 `linux`。构建上下文仅为 `docker/sandbox/`；默认基础镜像为 `python:3.12-slim-bookworm`，镜像内安装 LibreOffice Writer、中文字体、PyMuPDF 1.26.7、python-docx 1.2.0 和 pdf2docx 0.5.8。构建需要网络；执行时使用 `--pull never`，不会自动下载缺失镜像。上述跨平台命令依据 Dockerfile 整理，本次未在 Linux / macOS 实测。

默认镜像名为 `agentchat-sandbox:v1`。使用自定义镜像或 digest 时，在启动后端的终端设置 `AGENTCHAT_SANDBOX_IMAGE`；沙箱数据根目录可通过 `AGENTCHAT_SANDBOX_ROOT` 设置。基础镜像网络问题与 Windows WSL2 排查见 [沙箱说明](docs/local-sandbox.md)。

### 2. 在会话中使用

- 通用助手提供 `write`、`edit`、`read_document`，支持 md / txt / json / csv / yaml / yml / docx / pdf。名称是当前会话中的逻辑文件名，不能是宿主路径。`read_document` 读取已发布产物及哈希，文本预览有长度限制。
- 新建文件遇到同名会报错；覆盖或编辑需先读取最新 `sha256`，再作为 `expected_sha256` 传入。`edit` 只支持文本格式，待替换原文需恰好匹配一次；DOCX / PDF 可携带原哈希重新生成。
- DOCX ↔ PDF 转换工具只接受**本次 `/runs` 请求中已登记所有权和内容哈希的本地上传附件**。不能传入任意存储 URL；旧附件缺少登记时需重新上传。
- 论文 PDF 在后端受控下载后交给容器解析，研究报告也通过沙箱写出。产物显示在任务状态区，可携带登录凭据下载。

### 3. 资源与隔离边界

| 项目 | 代码中的默认策略 |
| --- | --- |
| 权限与网络 | UID/GID `1000:1000`，只读根文件系统，`--network none`，移除 capabilities，禁止提权 |
| 资源 | 每容器 1 CPU、1 GB 内存、64 个进程、120 秒操作超时；每个后端进程最多并发 2 个沙箱作业 |
| 输入与输出 | 仅只读挂载本次输入；`/outputs` 为 32 MB tmpfs，`/tmp` 为 256 MB tmpfs，无宿主可写挂载 |
| 发布 | 控制器校验输出 tar 的路径、类型、数量和大小后发布不可变版本，记录内容 SHA256 与实际镜像 ID |
| 保存位置 | 默认 `src/backend/data/sandbox/artifacts/` 保存产物，`attempts/` 保存各次输入与输出，下载 API 校验登录与所属关系 |

Docker 不可用、镜像缺失或沙箱失败时，相应操作失败，不会切换为宿主文档处理。成功作业可复用；中断作业重新执行固定步骤，不恢复容器内进程位置。既有 Pyodide 代码 Agent 使用另一种执行机制，不能把它视为该 Docker 沙箱的任意代码执行入口。

## 最小示例

以下是手动验证用例，实际回答和来源取决于模型、网络及可访问材料，本草稿不预设成功结果。

### 1. 基础聊天

完成前后端启动、注册登录后，选择 **通用助手**，发送：

> 用两句话解释什么是工具调用，不要调用外部工具。

检查能否收到回答及任务最终状态。`/health` 返回成功仅能说明服务启动，不能替代模型调用验证。

### 2. 沙箱文档

先构建沙箱镜像，再在通用助手会话中发送：

> 请调用 write 创建名为 hello.md 的文档，内容为“# 沙箱测试”和下一行“这是第一个会话文档”。创建后用 read_document 读取它，返回 sha256。

检查任务区是否出现 `hello.md` 下载入口，并核对文件内容。随后可发送：

> 请先读取 hello.md 的最新 sha256，再调用 edit，把“这是第一个会话文档”替换为“已完成编辑验证”，使用刚读取的哈希。编辑后再次读取确认。

文件只写入该会话的沙箱产物目录，不会修改仓库 README。工具是否实际执行需查看任务事件和产物；若模型没有调用工具，不能仅凭其文字回答判定沙箱已验证。

### 3. 论文调研

准备好 Docker、模型与外网连接，选择 **论文调研助手**，发送：

> 调研强化学习 Agent 的工具失败恢复方法，重点比较方法、评测数据集和局限性。仅使用能够核验的来源，缺少全文或证据时明确说明，不要跨不同实验设置排名。

在研究进度中检查规划、检索、阅读和核验记录；完成后下载 Markdown 与研究 JSON。JSON 用于复查检索元数据、阅读范围、候选陈述和核验结果。可在步骤执行中点击暂停，等待暂停后填写“只关注工具失败后的恢复与重试，排除纯提示词优化”再继续，核对是否产生新的方向版本。子 Agent 由规划后的规则决定，简单任务没有并行分工也属于预期行为。

<details>
<summary>查看对应任务接口与最小请求体</summary>

先在前端创建属于当前账号的会话；以下 JSON 是提交到 `POST /api/v1/runs` 的请求体，将会话 ID 和请求 ID 替换为实际值。接口需要登录凭据，可在 [Swagger UI](http://127.0.0.1:7860/docs) 查看完整 schema。

```json
{
  "conversation_id": "替换为当前账号的会话ID",
  "request_id": "readme-research-001",
  "agent_id": "paper-research",
  "user_input": "调研强化学习 Agent 的工具失败恢复方法，比较方法、评测数据集和局限性。"
}
```

同一请求重试时复用 `request_id`，新任务使用新的值。创建响应中的 `data.id` 为任务 ID；后续接口中的 `{id}` 替换为该值。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/runs/{id}` | 查询任务状态与当前回答 |
| GET | `/api/v1/runs/{id}/events` | SSE 订阅或重放执行事件 |
| POST | `/api/v1/runs/{id}/pause` | 请求在步骤边界暂停 |
| POST | `/api/v1/runs/{id}/resume` | 恢复；请求体可为 `{}`，或带 `research_direction` 调整范围 |
| GET | `/api/v1/runs/{id}/research/export?format=markdown` | 下载已生成报告；尚无报告时返回 409 |
| GET | `/api/v1/runs/{id}/research/export?format=json` | 下载研究工作区记录 |
| GET | `/api/v1/runs/{id}/export` | 下载执行事件 JSONL |
| GET | `/api/v1/runs/{id}/sandbox` | 查看沙箱作业记录 |

</details>

## 高级配置

简易配置沿用完整 YAML 结构，省略的参数使用默认值。详细说明见 [简易配置](docs/simple-config.md) 和 [后端文档](src/backend/README.md)。

| 需求 | 配置入口 |
| --- | --- |
| 切换 SQLite / MySQL | `database.url`，同步与异步驱动自动派生 |
| 使用 Redis | `redis.endpoint`；本地单进程可留空使用内存缓存 |
| 使用 MinIO / OSS | `storage.mode` 以及对应存储配置 |
| 分开设置模型 | `multi_models` 下的 `chat_model`、`tool_call_model`、`reasoning_model`、`embedding`、`rerank` 等 |
| 更换向量库或启用全文检索 | `rag.vector_db`、`rag.enable_elasticsearch` 及对应连接设置 |
| 添加默认 MCP / Skill 模板 | `agentchat/config/mcp_server.json`、`agentchat/config/default_skills.json` 与 `config/skills/` |
| 调整日志 | `log_level`；日志目录可通过 `AGENTCHAT_LOG_DIR` 指定 |
| 配置网页搜索 | `tools.tavily.api_key` 或 `tools.bocha.api_key` / `endpoint` |
| 指定检查点文件 | `AGENTCHAT_CHECKPOINT_DB`，默认使用本地 `agentchat-checkpoints.sqlite` |
| 指定沙箱镜像 / 数据目录 | `AGENTCHAT_SANDBOX_IMAGE` / `AGENTCHAT_SANDBOX_ROOT` |
| 调整上下文容量 | `AGENTCHAT_CONTEXT_TOKENS`（默认 32000）、`AGENTCHAT_CONTEXT_OUTPUT_TOKENS`（默认 4096）、`AGENTCHAT_MODEL_CONTEXT_LIMITS`，见 [上下文机制](docs/context-management.md) |

配置加载优先级为：代码显式传入路径 → `AGENTCHAT_CONFIG` → `agentchat/config.local.yaml` → `agentchat/config.yaml`。`config.local.simple.yaml` 需要通过环境变量指定，修改后重启后端。

默认从 `src/backend` 启动时，业务数据库为 `data/agentchat.db`，本地上传为 `data/uploads/`；检查点默认在业务 SQLite 文件旁。备份需同时包含业务数据库、检查点与仍需使用的上传、沙箱产物和向量数据。原始研究材料和执行记录按私有数据管理，不提交到公共仓库。

已有的 [Docker 应用部署文件](docker/) 使用 MySQL、Redis、MinIO 和前端开发服务器的组合，与上述轻量本地方案不同。现有 Compose 没有为后端提供 Docker CLI / 引擎连接，后端 Dockerfile 也未安装 Docker CLI，不能仅执行 `docker compose up` 就认定已接通新增文档沙箱。容器部署还需核对地址、凭据与持久化目录；本次文档更新未完成该方案的运行验证。

## 开发与测试

后端，在 `src/backend` 执行：

```bash
uv sync --locked --group dev
uv run --locked pytest tests -q
```

前端，在 `src/frontend` 执行：

```bash
npm ci
npm run lint
npm run build
node --test tests/mars-task.test.cjs
```

安装前端依赖后，后端测试还会校验 Python AG-UI 事件与前端解析器的配合。需要外部服务的手动脚本放在 `src/backend/tests/manual`，默认不收集。

[GitHub CI](.github/workflows/ci.yml) 在推送 `main`、提交 PR 或手动触发时运行后端测试及前端类型检查、构建。它使用临时数据库和测试配置，无需真实模型密钥。`npm run build` 生成前端静态文件，不会自动部署应用。

本次新增能力的重点机制测试，在 `src/backend` 执行：

```bash
uv run --locked pytest tests/test_agent_runs.py tests/test_paper_research.py tests/test_research_subagents.py tests/test_context.py tests/test_sandbox.py -q
```

这些测试使用模拟模型、来源或容器，验证状态与边界，不能证明真实联网调研质量或 Docker 隔离。构建好镜像后，可显式运行真实容器测试：

Windows PowerShell：

```powershell
$env:AGENTCHAT_TEST_DOCKER = "1"
uv run --locked pytest tests/test_sandbox_docker.py -q
```

Linux / macOS：

```bash
AGENTCHAT_TEST_DOCKER=1 uv run --locked pytest tests/test_sandbox_docker.py -q
```

真实 Docker 测试覆盖文档创建、编辑、结果复用、DOCX / PDF 转换与解析、取消后的容器清理和恢复，并有隔离探针。未设置 `AGENTCHAT_TEST_DOCKER=1` 时会跳过；当前 CI 没有构建沙箱镜像或开启此开关，因此普通 CI 通过不能代替真实容器验证。本次 README 修改未执行上述应用测试。

## 当前已知限制与待补充

### 已从代码确认的边界

| 范围 | 当前限制 |
| --- | --- |
| 研究覆盖 | 每轮规划 2–4 个子问题，最多选读 6 篇论文和 2 个补充网页，属于有限预算辅助调研，不能宣称穷尽文献或完成系统综述 |
| PDF 阅读 | 单文件最多 12,000,000 字节，最多提取 300 页或 200 万字符；不提供 OCR、解密或付费墙绕过。提取文本不等于逐段分析全文，分析按方向选取片段 |
| 证据质量 | 原文、页码和模型语义核验仍可能有遗漏或误判；不能代替专家判断、人工核对与独立复现实验，跨数据集数值不能直接排名 |
| 模型与网络 | Key、模型能力、站点可达性、API 限流和 PDF 可访问性会影响结果；没有 Tavily / Bocha 时不做补充网页搜索 |
| 上下文 | token 使用估算且有容量上限；历史检索以关键词与重排为主，同义改写召回有限；有效要求仍放不下时会明确报容量错误 |
| 任务恢复 | 当前适合单机；依赖业务数据库和本地检查点，不支持多主机各自使用独立检查点文件。暂停需要等待当前操作返回，不能撤销已发生的外部操作 |
| 工具副作用 | 外部邮件、MCP / OpenAPI 等异常后可能已生效，需要人工核实后决定跳过或重试；不保证外部服务严格一次执行。旧 `/completion` 不提供 `/runs` 的恢复能力 |
| 文档操作 | DOCX / PDF 生成按逐行段落处理，不是完整 Markdown 排版引擎；转换只接受本次任务的本地授权附件，二进制格式不支持直接文本编辑 |
| 沙箱存储 | 尝试目录用于诊断与恢复，当前无自动清理策略；删除会话会清理数据库授权记录，物理孤儿文件暂留，需要部署者维护磁盘空间 |

## 文档导航

- [任务执行、暂停与恢复](docs/agent-runtime.md)
- [论文调研助手与按需多 Agent](docs/paper-research.md)
- [本地 Docker 文档沙箱](docs/local-sandbox.md)
- [上下文、工作记忆与证据回读](docs/context-management.md)
- [后端架构、接口与故障排查](src/backend/README.md)
- [简易配置与本地数据目录](docs/simple-config.md)
- [灵活会话与异步标题](docs/flexible-conversations.md)
- [用户资源隔离](docs/private-resources.md)
- [默认 Skill](docs/default-skills.md)
- [默认 MCP](docs/mcp-defaults.md) · [Parallel Search MCP](docs/reference/parallel_search_mcp.md)
- [AG-UI 流式协议](docs/ag-ui.md)
- [会话 Token 统计](docs/conversation-token-usage.md)
- [日志格式与追踪](docs/logging.md)
- [测试说明](src/backend/tests/README.md)
- [更新日志](docs/CHANGELOG.md)

## 许可证与支持

本项目采用 [MIT License](LICENSE)。
