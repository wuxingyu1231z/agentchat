# 本地 Docker 工具沙箱

通用 agent 的 `write`、`edit`、文档转换和论文 agent 的 PDF 解析、Markdown 报告落盘统一使用 Docker。数据库、模型密钥和图检查点留在后端。不会在 Docker 不可用时改用宿主进程处理文档。

## 准备运行环境

Windows 使用 Docker Desktop 的 WSL2 / Linux containers 后端。新启用 WSL 与 VirtualMachinePlatform 后通常需要重启；此项目不自动重启系统。启动 Docker Desktop 后，在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-sandbox.ps1
```

只向 Docker 发送 `docker/sandbox/` 构建上下文，不发送项目配置、研究资料或学习手册。镜像名默认为 `agentchat-sandbox:v1`，运行时记录实际 image ID。部署可通过 `AGENTCHAT_SANDBOX_IMAGE` 配置已构建镜像或 digest；执行期间禁止自动拉取镜像。

如果 Docker Hub 因网络或域名解析不可达，可使用 [Docker 官方在 Amazon ECR 发布的 Python 镜像](https://gallery.ecr.aws/docker/library/python)，无需修改全局 DNS 或使用非官方镜像仓库：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-sandbox.ps1 -BaseImage public.ecr.aws/docker/library/python:3.12-slim-bookworm
```

`-BaseImage` 只由部署者设置，不向 agent 开放；建议部署时使用仓库返回的 `@sha256:...` 固定基础镜像，并保存构建日志。默认基础镜像仍是 `python:3.12-slim-bookworm`。构建阶段需要联网安装依赖，任务执行阶段仍使用 `--network none` 和 `--pull never`。

### Windows 已重启但引擎仍不可用

“功能已启用”和“WSL 2 已能运行”需要分别验证。若 Docker 提示 Virtual Machine Platform 未启用，可在管理员 PowerShell 检查：

```powershell
Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
Get-Service vmcompute,hns,WslService
wsl --status
```

如果功能显示 Enabled，但 `vmcompute` / `hns` 服务缺失，重复重启或重复安装 Python 依赖不能证明环境已恢复。先排查 Windows 组件完整性，参考[微软系统文件修复说明](https://support.microsoft.com/en-au/windows/experience/backup-recovery/using-system-file-checker-in-windows)，依次执行 `DISM /Online /Cleanup-Image /RestoreHealth` 与 `sfc /scannow`，保留退出码和诊断日志，再确认服务与 Linux 引擎状态。修复可能需要联网或重启；项目不自动重启、不注销已有 WSL 发行版。只有引擎响应正常、镜像构建成功、真实集成测试通过后，才可认为本机部署完成。

## 使用

- 通用 agent 自动获得 `write(name, content, expected_sha256?)`、`edit(name, old_text, new_text, expected_sha256)`、`read_document(name)`。
- name 仅为当前会话的逻辑文件名；不接受宿主绝对路径、目录、设备名或 NTFS 数据流。
- 支持 md/txt/json/csv/yaml/docx/pdf；DOCX/PDF 写入目前按逐行段落生成，不是完整 Markdown 排版引擎。
- 新建文件时同名已存在会报错；覆盖或编辑需要先读取最新 sha256。二进制格式不能直接文本编辑，可重新生成并提供原 sha256。
- 产物实际保存在后端 `data/sandbox/artifacts/` 下的独立版本目录，API 返回本地文档名、哈希及鉴权下载地址；这不是自动覆盖项目源文件的工具。
- 文档转换第一版只接受本次 `/runs` 请求里的本地上传附件，不接受模型自行指定的任意存储 URL。
- 上传接口登记当前用户和内容哈希；转换时再次校验所有权与内容。旧附件没有登记时需要重新上传，不根据 URL 猜测权限。
- 聊天任务状态区提供鉴权下载按钮，带登录凭据获取文件；不依赖公开静态文件链接。
- 论文 PDF 下载仍走受控公开地址读取，下载后的解析进入无网络容器；报告文件也必须经过沙箱写出。

默认容器：非 root、只读根文件系统、网络关闭、移除 capabilities、禁止提权、1 CPU、1 GB 内存、64 个进程、120 秒运行超时。只映射本次输入目录（只读）；输出放入独立的 32 MB tmpfs，临时区为 256 MB tmpfs，没有宿主可写挂载。控制器按容量上限取回输出 tar，逐项检查普通文件、路径和重复项后才发布，不使用 extractall；没有项目、数据库、密钥或 Docker socket 挂载。镜像内只有固定操作 worker，不给模型任意命令参数。

输出从运行中的容器通过固定 Python 打包命令流式取回，避免 `docker cp` 对 tmpfs 的限制；控制器仍把 tar 视为不可信输入。容器使用 `local` 日志驱动，设置 `max-size=1m`、`max-file=1` 和 `compress=false`，避免单日志文件与默认压缩的参数冲突。沙箱内 PyMuPDF 固定为 1.26.7，包含 1.26.5 缺失 `Rect.get_area` 的修复，PDF→DOCX 在真实容器中验证。

## 恢复与限制

产物先在沙箱生成，后端只做校验与字节发布。不可变文件保存完成后，再在同一数据库事务中更新 SandboxJob 结果与 SandboxDocument 当前版本；数据库所有权检查阻止旧 worker 发布。崩溃留下的未引用文件不会作为成功结果展示。

沙箱调用的账本仍记为写操作，但额外记录 `_execution_policy=sandbox-v1`，允许安全重放。已经成功的作业复用结果；中断作业重新执行固定步骤，不恢复任意进程指令位置。旧版本未标记的未知写入不会被自动升级为可重放。

邮件、MCP/OpenAPI 等外部副作用没有因沙箱而获得去重保证；结果不确定仍需查询或人工核实。旧 `/completion` 文档转换已禁用宿主执行，需使用 `/runs`。既有 Pyodide 代码 agent 保留，其隔离边界与本文 Docker 文档沙箱不同。

每次尝试保留输入与输出目录用于恢复诊断，因此应按保留策略维护磁盘空间；当前版本不自动递归删除资料。删除会话会清理数据库授权记录，物理孤儿文件暂留，不提供公共静态下载。

## 验证

机制测试显式使用模拟容器，不能证明真实隔离：

```powershell
cd src/backend
.\.venv\Scripts\python.exe -m pytest tests/test_sandbox.py -q
```

完成真实镜像构建后，显式开启容器集成测试：

```powershell
$env:AGENTCHAT_TEST_DOCKER = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_sandbox_docker.py -q
```

测试覆盖真实文档创建、编辑、结果复用、DOCX/PDF 双向转换、直接生成 PDF 和 PDF 解析；还在真实容器启动后注入取消，检查容器清理、未发布产物和新 owner 恢复。独立探针检查非 root、输入及根目录只读、禁网、密钥缺省和输出配额。使用 pytest 的 `--junitxml` 参数可保存 image ID、各步骤耗时和隔离断言结果。首次运行会启动多个容器。鉴权诊断：`GET /api/v1/runs/{run_id}/sandbox`；产物下载：`GET /api/v1/runs/{run_id}/sandbox/{job_id}/download`。
