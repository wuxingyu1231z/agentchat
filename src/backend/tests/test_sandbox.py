import asyncio
import json
from types import SimpleNamespace
import pytest
from tests.test_agent_runs import database, cleanup
from tests.test_context import task
from tests.sandbox_support import FakeDockerBackend
from agentchat.services.sandbox import SandboxService, SandboxUnavailable, DockerBackend, document_name, root_path, trusted_file
from agentchat.database.dao.sandbox import SandboxDao
from agentchat.database.dao.agent_run import RunDao, RunConflict
from agentchat.services.run_tools import RunContext


@pytest.mark.parametrize('name', ['../x.md', 'a/b.md', 'C:\\x.md', 'x.md:stream', '/x.md', 'CON.txt', 'a.exe', '.secret.md', 'x.md '])
def test_document_paths_are_not_host_paths(name):
    with pytest.raises(ValueError):
        document_name(name)


def test_write_edit_versions_scope_and_replay(database):
    async def check():
        try:
            run, conv, _ = await task()
            sandbox = RunContext(run.id, 'owner').get_sandbox()
            first = await sandbox.write('调研.md', 'Original content', 'write-one')
            assert first == await sandbox.write('调研.md', 'Original content', 'write-one')
            assert FakeDockerBackend.calls == 1
            assert (await sandbox.read('调研.md'))['content'] == 'Original content'
            changed = await sandbox.execute_tool('edit', {'name': '调研.md', 'old_text': 'Original',
                'new_text': 'Updated', 'expected_sha256': first['artifact']['sha256']}, 'edit-one')
            assert changed['artifact']['sha256'] != first['artifact']['sha256']
            with pytest.raises(ValueError, match='版本'):
                await sandbox.execute_tool('edit', {'name': '调研.md', 'old_text': 'Original', 'new_text': 'Old',
                    'expected_sha256': first['artifact']['sha256']}, 'stale-edit')
            with pytest.raises(RunConflict):
                await sandbox.write('调研.md', 'Unapproved overwrite', 'write-again')
            other, _, _ = await task('other-run')
            with pytest.raises(ValueError, match='没有'):
                await RunContext(other.id, 'owner').get_sandbox().read('调研.md')
            original_path = root_path() / first['artifact']['relative_path']
            assert original_path.read_text(encoding='utf-8') == 'Original content'
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_partial_output_is_not_published_and_can_resume(database, monkeypatch):
    class Interrupted(FakeDockerBackend):
        async def run(self, identity, inputs, outputs, policy):
            (outputs / 'document.md').write_text('partial')
            raise asyncio.CancelledError()
    async def check():
        try:
            run, conv, _ = await task()
            sandbox = RunContext(run.id, 'owner').get_sandbox()
            monkeypatch.setattr(SandboxService, 'backend_factory', Interrupted)
            with pytest.raises(asyncio.CancelledError):
                await sandbox.write('恢复.md', 'complete', 'step')
            assert await SandboxDao.document(run.user_id, run.conversation_id, '恢复.md') is None
            await RunDao.finish(run.id, 'interrupted')
            await RunDao.requeue(run.id)
            await RunDao.claim(run.id, 'new-owner')
            monkeypatch.setattr(SandboxService, 'backend_factory', FakeDockerBackend)
            result = await RunContext(run.id, 'new-owner').get_sandbox().write('恢复.md', 'complete', 'step')
            assert (root_path() / result['artifact']['relative_path']).read_text() == 'complete'
            with pytest.raises(RunConflict, match='租约'):
                await SandboxDao.finish(RunContext(run.id, 'owner'), result['job_id'], result, name='恢复.md')
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_missing_runtime_never_falls_back_to_host(database, monkeypatch):
    class Missing:
        async def run(self, *args):
            raise SandboxUnavailable('Docker is unavailable')
    async def check():
        try:
            run, _, _ = await task()
            monkeypatch.setattr(SandboxService, 'backend_factory', Missing)
            with pytest.raises(SandboxUnavailable):
                await RunContext(run.id, 'owner').get_sandbox().write('未生成.md', 'never published', 'step')
            assert await SandboxDao.document(run.user_id, run.conversation_id, '未生成.md') is None
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_output_traversal_and_symlink_are_rejected(database, monkeypatch):
    class Malicious:
        async def run(self, identity, inputs, outputs, policy):
            (outputs / 'result.json').write_text('{"output":"../request.json"}')
            return {}
    async def check():
        try:
            run, _, _ = await task()
            monkeypatch.setattr(SandboxService, 'backend_factory', Malicious)
            with pytest.raises(ValueError, match='不匹配'):
                await RunContext(run.id, 'owner').get_sandbox().write('report.md', 'x', 'bad')
            assert await SandboxDao.document(run.user_id, run.conversation_id, 'report.md') is None
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_docker_flags_and_no_credentials_are_passed(tmp_path, monkeypatch):
    from agentchat.services.sandbox import runtime as module
    calls = []
    async def command(args, **kwargs):
        calls.append(args)
        if args[1:3] == ['image', 'inspect']:
            return '[{"Id":"sha256:fixture"}]'
        if args[1] == 'inspect':
            return '[{"State":{"ExitCode":0,"OOMKilled":false,"Running":true}}]'
        if args[1] == 'exec' and kwargs.get('raw'):
            import io, tarfile
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w'):
                pass
            return output.getvalue()
        if args[1] == 'exec':
            return 'True'
        return ''
    monkeypatch.setattr(module, 'docker_binary', lambda: 'fake-docker')
    monkeypatch.setattr(module, 'command', command)
    asyncio.run(DockerBackend().run('a' * 64, tmp_path/'in', tmp_path/'out', {'image':'test:v1', 'timeout':60}))
    create = next(call for call in calls if call[1] == 'create')
    for flag in ('--network', '--read-only', '--user', '--cap-drop', '--security-opt', '--memory', '--cpus', '--pids-limit', '--pull'):
        assert flag in create
    assert create[create.index('--network') + 1] == 'none'
    assert '--privileged' not in create and '--env' not in create
    assert len([value for value in create if value.startswith('type=bind')]) == 1
    assert any(value.startswith('/outputs:') and 'size=32m' in value for value in create)
    assert calls[-1][1:3] == ['rm', '-f']


def test_pdf_runtime_is_mandatory_and_download_is_owner_scoped(database):
    async def check():
        try:
            from agentchat.api.v1.runs import sandbox_download
            from fastapi import HTTPException
            run, _, _ = await task()
            result = await RunContext(run.id, 'owner').get_sandbox().write('private.md', 'private', 'one')
            with pytest.raises(HTTPException) as error:
                await sandbox_download(run.id, result['job_id'], SimpleNamespace(user_id='other'))
            assert error.value.status_code == 404
            response = await sandbox_download(run.id, result['job_id'], SimpleNamespace(user_id='user'))
            assert response.filename == 'private.md'
        finally:
            await cleanup(database)
    asyncio.run(check())


@pytest.mark.parametrize('name,kind', [('../outside.md','file'), ('link.md','symlink'), ('ok.md','hardlink'), ('/absolute.md','file')])
def test_container_tar_cannot_escape_or_create_links(tmp_path, name, kind):
    import io, tarfile
    from agentchat.services.sandbox.runtime import unpack_outputs
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as bundle:
        entry = tarfile.TarInfo(name)
        if kind == 'symlink':
            entry.type, entry.linkname = tarfile.SYMTYPE, '/etc/passwd'
        elif kind == 'hardlink':
            entry.type, entry.linkname = tarfile.LNKTYPE, 'document.md'
        else:
            entry.size = 1
        bundle.addfile(entry, io.BytesIO(b'x') if entry.isfile() else None)
    with pytest.raises(ValueError):
        unpack_outputs(output.getvalue(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_write_tool_is_replayable_but_old_external_unknown_is_not(database):
    from langchain.tools.tool_node import ToolCallRequest
    from agentchat.services.run_tools import sandbox_replay
    async def check():
        try:
            run, _, _ = await task()
            context = RunContext(run.id, 'owner')
            write = next(tool for tool in context.get_sandbox().tools() if tool.name == 'write')
            request = ToolCallRequest(tool_call={'name':'write', 'id':'write-1', 'args':{
                'name':'tool.md', 'content':'Sandbox content'}, 'type':'tool_call'}, tool=write, state={}, runtime=None)
            async def forbidden_handler(request):
                raise AssertionError('Sandbox operation must not reach a host tool body')
            response = await context.execute_tool(request, forbidden_handler, lambda event: None, '写文档')
            assert response.status == 'success'
            row = await RunDao.tool(run.id, 'write-1')
            assert not row.read_only and sandbox_replay(row)
            assert not sandbox_replay(SimpleNamespace(name='write', arguments={}))
            assert not sandbox_replay(SimpleNamespace(name='send_email', arguments={'_execution_policy':'sandbox-v1'}))
            again = await context.execute_tool(request, forbidden_handler, lambda event: None, '写文档')
            assert again.content == response.content and FakeDockerBackend.calls == 1
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_upload_ownership_cannot_be_forged_by_request_url(database):
    async def check():
        try:
            await SandboxDao.register_upload('files/owned.pdf', 'owner-a', b'%PDF-original')
            assert (await SandboxDao.upload('files/owned.pdf','owner-a')).sha256
            with pytest.raises(ValueError, match='当前用户'):
                await SandboxDao.upload('files/owned.pdf','other')
            with pytest.raises(ValueError, match='重新上传'):
                await SandboxDao.upload('files/legacy.pdf','owner-a')
        finally:
            await cleanup(database)
    asyncio.run(check())
