"""Local Docker execution; no host execution fallback and no model-selected commands/mounts."""
import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import io
import tarfile
from time import monotonic
from uuid import uuid4
from contextvars import ContextVar
from langchain_core.tools import tool
from agentchat.database.dao.sandbox import SandboxDao

CURRENT_RUN = ContextVar('sandbox_run', default=None)
POLICY_VERSION = 'docker-offline-v1'
SANDBOX_TOOLS = {'write', 'edit', 'read_document', 'docx_to_pdf', 'pdf_to_docx'}
FORMATS = {'.md', '.txt', '.json', '.csv', '.yaml', '.yml', '.docx', '.pdf'}
CONCURRENCY = asyncio.Semaphore(2)


class SandboxUnavailable(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


def root_path():
    return Path(os.getenv('AGENTCHAT_SANDBOX_ROOT', str(Path(__file__).resolve().parents[3] / 'data' / 'sandbox'))).resolve()


def document_name(name):
    # One logical filename, not a host path. Reject NTFS streams, reserved names and device paths too.
    if not name or len(name) > 160 or name != name.strip() or any(c in name for c in '\\/:\x00'):
        raise ValueError('文档名称只能是文件名，不能包含目录、绝对路径或设备路径')
    if not re.fullmatch(r'[\w\- .\u3400-\u9fff]+', name) or name.startswith('.') or name.endswith(('.', ' ')):
        raise ValueError('文档名称无效')
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('文档名称属于系统保留名称')
    if Path(name).suffix.lower() not in FORMATS:
        raise ValueError('仅支持 md/txt/json/csv/yaml/docx/pdf 文档')
    return name


def trusted_file(path, root, max_bytes):
    root = root.resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError('沙箱产物路径或类型无效')
    stat = path.stat()
    if stat.st_nlink != 1 or stat.st_size > max_bytes:
        raise ValueError('沙箱产物超过限制或不是独立普通文件')
    return path


def docker_binary():
    found = shutil.which('docker')
    if found:
        return found
    for base in (Path(os.getenv('ProgramFiles', 'C:/Program Files')) / 'Docker/Docker',
                 Path(os.getenv('LOCALAPPDATA', '.')) / 'Programs/DockerDesktop'):
        path = base / 'resources/bin/docker.exe'
        if path.is_file():
            return str(path)
    raise SandboxUnavailable('未安装 Docker：此操作必须在沙箱中执行，不会在宿主机降级运行')


async def command(args, timeout=30, limit=65536, raw=False):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)
    async def collect():
        result = bytearray()
        while block := await process.stdout.read(8192):
            result.extend(block)
            if len(result) > limit:
                raise ValueError('容器控制输出超过限制')
        await process.wait()
        return bytes(result) if raw else bytes(result).decode('utf-8', errors='replace')
    try:
        async with asyncio.timeout(timeout):
            output = await collect()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        # Do not expose Docker host configuration or arbitrary program output to the model.
        raise SandboxUnavailable(f'Docker 操作失败（退出码 {process.returncode}），请检查运行环境和沙箱镜像')
    return output if raw else output.strip()


def unpack_outputs(archive, outputs):
    """Never use extractall on container-controlled archives."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as bundle:
        count, total = 0, 0
        for entry in bundle:
            name = PurePosixPath(entry.name)
            if entry.isdir() and str(name) == '.':
                continue
            if not entry.isfile() or len(name.parts) != 1 or name.name not in {
                'result.json', *(f'document{suffix}' for suffix in FORMATS)}:
                raise ValueError('容器输出包含非法路径或链接')
            count += 1
            total += entry.size
            if count > 4 or total > 32_000_000 or entry.size > (8_000_000 if name.name == 'result.json' else 24_000_000):
                raise ValueError('容器输出超过容量限制')
            target = outputs / name.name
            if target.exists():
                raise ValueError('容器输出存在重复文件')
            with bundle.extractfile(entry) as source, target.open('xb') as destination:
                shutil.copyfileobj(source, destination, 65536)


class DockerBackend:
    async def run(self, identity, inputs, outputs, policy):
        binary = docker_binary()
        name = 'agentchat-sbx-' + identity[:24] + '-' + outputs.parent.name[:16]
        if any(',' in str(path) for path in (inputs, outputs)):
            raise ValueError('沙箱根目录不能含逗号')
        # Reclaim a previous crashed worker's container. No arbitrary container IDs are accepted.
        stale = await command([binary, 'ps', '-aq', '--filter', 'label=agentchat.sandbox.job=' + identity])
        for container in stale.splitlines()[:16]:
            if not re.fullmatch('[a-f0-9]{12,64}', container):
                raise ValueError('容器标识无效')
            await command([binary, 'rm', '-f', container])
        image = policy['image']
        inspected = json.loads(await command([binary, 'image', 'inspect', image]))
        image_id = inspected[0]['Id']
        args = [binary, 'create', '--name', name, '--pull', 'never', '--network', 'none',
            '--label', 'agentchat.sandbox.job=' + identity,
            '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--memory', '1g', '--memory-swap', '1g', '--cpus', '1', '--pids-limit', '64',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
            '--tmpfs', '/outputs:rw,nosuid,nodev,size=32m,uid=1000,gid=1000,mode=0700', '--log-driver', 'local',
            '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1', '--log-opt', 'compress=false',
            '--mount', f'type=bind,source={inputs},target=/inputs,readonly', image_id, '--hold']
        created = False
        try:
            await command(args)
            created = True
            async with asyncio.timeout(policy['timeout']):
                await command([binary, 'start', name])
                while True:
                    info = json.loads(await command([binary, 'inspect', name]))[0]
                    if not info['State']['Running'] or info['State'].get('OOMKilled'):
                        raise ValueError('沙箱任务失败或超过资源限制，未发布产物')
                    ready = await command([binary, 'exec', name, 'python', '-I', '-c',
                        "from pathlib import Path; print(Path('/outputs/result.json').is_file())"])
                    if ready == 'True':
                        break
                    await asyncio.sleep(0.25)
                # docker cp cannot read the live tmpfs mount. Pack inside the running
                # container with fixed code, then validate the untrusted tar on the host.
                archive = await command([binary, 'exec', name, 'python', '-I', '-c',
                    "import sys, tarfile\nwith tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as bundle:\n    bundle.add('/outputs', arcname='.')\n"],
                    limit=33_000_000, raw=True)
                unpack_outputs(archive, outputs)
            return {'image_id': image_id, 'container_name': name, 'exit_code': 0}
        finally:
            if created:
                await asyncio.shield(command([binary, 'rm', '-f', name]))


class SandboxService:
    backend_factory = DockerBackend

    def __init__(self, context):
        self.context = context

    async def run(self, operation, key, payload=None, data=b'', *, name=None, expected_sha=None):
        if operation not in {'write', 'edit', 'parse_pdf', 'docx_to_pdf', 'pdf_to_docx'}:
            raise ValueError('未授权的沙箱操作')
        run = await self.context.active_run()
        if len(data) > 12_000_000:
            raise ValueError('沙箱输入超过 12 MB')
        payload = payload or {}
        encoded = json.dumps({'operation': operation, 'payload': payload}, ensure_ascii=False).encode()
        if len(encoded) > 2_000_000:
            raise ValueError('文档输入超过 2 MB')
        if name:
            document_name(name)
        image = os.getenv('AGENTCHAT_SANDBOX_IMAGE', 'agentchat-sandbox:v1')
        if not re.fullmatch(r'[a-zA-Z0-9._/@:\-]+', image):
            raise ValueError('沙箱镜像配置无效')
        policy = {'version': POLICY_VERSION, 'image': image, 'timeout': 120, 'network': 'none',
            'memory_mb': 1024, 'cpus': 1, 'pids': 64, 'output_tmpfs_mb': 32, 'temp_tmpfs_mb': 256,
            'log_max_mb': 1, 'log_files': 1, 'log_compress': False, 'output_transport': 'exec-tar-v1'}
        identity = digest(json.dumps([run.id, operation, key, digest(encoded), digest(data), policy, name, expected_sha], sort_keys=True))
        cached = await SandboxDao.begin(self.context, identity, operation, policy)
        if cached is not None:
            return cached
        base = root_path()
        attempt = base / 'attempts' / identity[:32] / uuid4().hex[:16]
        inputs, outputs = attempt / 'inputs', attempt / 'outputs'
        start = monotonic()
        try:
            inputs.mkdir(parents=True, mode=0o700)
            outputs.mkdir(mode=0o700)
            (inputs / 'request.json').write_bytes(encoded)
            if data:
                (inputs / ('document.docx' if operation == 'docx_to_pdf' else 'input.bin')).write_bytes(data)
            async with CONCURRENCY:
                runtime = await self.backend_factory().run(identity, inputs.resolve(), outputs.resolve(), policy)
            children = list(outputs.iterdir())
            if len(children) > 4:
                raise ValueError('沙箱输出文件数量超过限制')
            for child in children:
                trusted_file(child, outputs, 24_000_000)
            metadata = json.loads(trusted_file(outputs / 'result.json', outputs, 8_000_000).read_text(encoding='utf-8'))
            result = {'job_id': identity, 'runtime': runtime, 'elapsed_seconds': round(monotonic() - start, 3)}
            if operation == 'parse_pdf':
                from agentchat.schemas.sandbox import PdfResult
                result['parsed'] = PdfResult.model_validate(metadata).model_dump()
            else:
                filename = metadata.get('output', '')
                suffix = payload.get('suffix') if operation in {'write', 'edit'} else '.pdf' if operation == 'docx_to_pdf' else '.docx'
                if filename != 'document' + suffix:
                    raise ValueError('沙箱输出清单与操作不匹配')
                source = trusted_file(outputs / filename, outputs, 24_000_000)
                content_hash = digest(source.read_bytes())
                relative = Path('artifacts') / digest(run.user_id + run.conversation_id + identity + content_hash) / ('document' + suffix)
                target = base / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    trusted_file(target, base, 24_000_000)
                    if digest(target.read_bytes()) != content_hash:
                        raise ValueError('已存在产物与重放结果不一致')
                else:
                    # Bytes were generated by the sandbox. Host only publishes a validated immutable artifact.
                    staging = target.with_name(uuid4().hex + '.part')
                    shutil.copyfile(source, staging)
                    staging.replace(target)
                result['artifact'] = {'job_id': identity, 'name': name or 'document' + suffix,
                    'relative_path': relative.as_posix(), 'sha256': content_hash, 'bytes': target.stat().st_size,
                    'download_url': f'/api/v1/runs/{run.id}/sandbox/{identity}/download'}
            await self.context.active_run()
            await SandboxDao.finish(self.context, identity, result, name=name, expected_sha=expected_sha)
            await self.context.get_context().trace('sandbox', job_id=identity, operation=operation,
                input_sha256=digest(data or encoded), policy=policy, **runtime, elapsed_seconds=result['elapsed_seconds'])
            return result
        except BaseException:
            await asyncio.shield(SandboxDao.fail(self.context, identity))
            raise

    async def write(self, name, content, key, expected_sha=None):
        name = document_name(name)
        return await self.run('write', key, {'suffix': Path(name).suffix.lower(), 'content': content},
            name=name, expected_sha=expected_sha)

    async def read(self, name):
        run = await self.context.active_run()
        head = await SandboxDao.document(run.user_id, run.conversation_id, document_name(name))
        if not head:
            raise ValueError('当前会话中没有该文档')
        path = trusted_file(root_path() / head.artifact['relative_path'], root_path(), 24_000_000)
        if digest(path.read_bytes()) != head.sha256:
            raise ValueError('文档产物校验失败')
        result = {'name': name, 'sha256': head.sha256, 'artifact': head.artifact}
        if path.suffix not in {'.pdf', '.docx'}:
            result['content'] = path.read_text(encoding='utf-8')[:24000]
            result['truncated'] = path.stat().st_size > len(result['content'].encode())
        return result

    async def execute_tool(self, name, args, key):
        if name == 'write':
            return await self.write(args['name'], args['content'], key, args.get('expected_sha256'))
        if name == 'read_document':
            return await self.read(args['name'])
        if name == 'edit':
            current = await self.read(args['name'])
            if current['sha256'] != args['expected_sha256'] or Path(args['name']).suffix.lower() in {'.pdf', '.docx'}:
                raise ValueError('版本不匹配或该文档格式不支持文本编辑')
            data = trusted_file(root_path() / current['artifact']['relative_path'], root_path(), 2_000_000).read_bytes()
            return await self.run('edit', key, {'suffix': Path(args['name']).suffix.lower(),
                'old_text': args['old_text'], 'new_text': args['new_text']}, data,
                name=args['name'], expected_sha=args['expected_sha256'])
        if name in {'docx_to_pdf', 'pdf_to_docx'}:
            run = await self.context.active_run()
            # Only the current authorized attachment; arbitrary storage URLs are not accepted.
            if args.get('file_url') != run.request.get('file_url') or not run.request.get('file_url'):
                raise ValueError('文档转换只能使用本次任务的附件')
            data = await attachment_bytes(run)
            return await self.run(name, key, data=data)
        raise ValueError('未注册的沙箱工具')

    def tools(self):
        @tool
        async def write(name: str, content: str, expected_sha256: str | None = None) -> str:
            """在隔离沙箱中创建本会话文档，支持 md/txt/json/csv/yaml/docx/pdf。name 只能是文件名；覆盖需先读取并提供原 sha256。"""
            raise RuntimeError('write 必须经持久化任务沙箱执行')

        @tool
        async def edit(name: str, old_text: str, new_text: str, expected_sha256: str) -> str:
            """在隔离沙箱中修改本会话文本类文档。需原文 sha256，old_text 必须恰好匹配一次。"""
            raise RuntimeError('edit 必须经持久化任务沙箱执行')

        @tool
        async def read_document(name: str) -> str:
            """读取本会话已发布文档及 sha256；不能读取任意宿主机路径。"""
            raise RuntimeError('read_document 必须经持久化任务沙箱执行')
        return [write, edit, read_document]


async def attachment_bytes(run):
    from agentchat.settings import app_settings
    if app_settings.storage.mode != 'local':
        raise ValueError('沙箱附件第一版仅支持已授权的本地上传文件')
    from agentchat.services.storage.local import LocalStorageClient
    storage = LocalStorageClient()
    path = trusted_file(storage._path(run.request['file_url']), storage.root, 12_000_000)
    ownership = await SandboxDao.upload(path.relative_to(storage.root).as_posix(), run.user_id)
    data = await asyncio.to_thread(path.read_bytes)
    if digest(data) != ownership.sha256:
        raise ValueError('附件内容与上传时不一致，请重新上传')
    return data
