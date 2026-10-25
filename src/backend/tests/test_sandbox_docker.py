"""Real Docker integration. Explicit opt-in; never silently replaced by FakeDockerBackend."""
import asyncio
import json
import os
import pytest
from tests.test_agent_runs import database, cleanup
from tests.test_context import task
from agentchat.services.sandbox import SandboxService, DockerBackend, root_path
from agentchat.services.run_tools import RunContext
from agentchat.database.dao.sandbox import SandboxDao
from agentchat.database.dao.agent_run import RunDao
from agentchat.services.sandbox import runtime as sandbox_runtime

pytestmark = pytest.mark.skipif(os.getenv('AGENTCHAT_TEST_DOCKER') != '1', reason='Requires real Linux Docker and built sandbox image; set AGENTCHAT_TEST_DOCKER=1')


def test_real_container_documents_pdf_and_replay(database, monkeypatch, record_property):
    monkeypatch.setattr(SandboxService, 'backend_factory', DockerBackend)
    async def check():
        try:
            run, _, _ = await task()
            sandbox = SandboxService(RunContext(run.id, 'owner'))
            first = await sandbox.write('真实容器.md', '# Research\nOriginal evidence.', 'real-write')
            assert (root_path() / first['artifact']['relative_path']).read_text(encoding='utf-8') == '# Research\nOriginal evidence.'
            assert first == await sandbox.write('真实容器.md', '# Research\nOriginal evidence.', 'real-write')
            edited = await sandbox.execute_tool('edit', {'name': '真实容器.md', 'old_text': 'Original',
                'new_text': 'Verified', 'expected_sha256': first['artifact']['sha256']}, 'real-edit')
            assert 'Verified' in (root_path() / edited['artifact']['relative_path']).read_text(encoding='utf-8')
            docx = await sandbox.write('真实容器.docx', 'Title\nEvidence line', 'real-docx')
            pdf = await sandbox.run('docx_to_pdf', 'real-convert', data=(root_path() / docx['artifact']['relative_path']).read_bytes())
            parsed = await sandbox.run('parse_pdf', 'real-parse', data=(root_path() / pdf['artifact']['relative_path']).read_bytes())
            assert any('Evidence line' in p['text'] for p in parsed['parsed']['pages'])
            restored = await sandbox.run('pdf_to_docx', 'real-reverse-convert', data=(root_path() / pdf['artifact']['relative_path']).read_bytes())
            from docx import Document
            assert any('Evidence line' in p.text for p in Document(root_path() / restored['artifact']['relative_path']).paragraphs)
            direct_pdf = await sandbox.write('直接生成.pdf', 'Direct PDF evidence\n中文研究结论', 'real-pdf-write')
            direct_parsed = await sandbox.run('parse_pdf', 'real-direct-parse', data=(root_path() / direct_pdf['artifact']['relative_path']).read_bytes())
            assert any('Direct PDF evidence' in p['text'] for p in direct_parsed['parsed']['pages'])
            assert any('中文研究结论' in ''.join(p['text'].split()) for p in direct_parsed['parsed']['pages'])
            assert first['runtime']['image_id'].startswith('sha256:')
            record_property('sandbox_results', json.dumps([
                {k: result[k] for k in ('job_id', 'runtime', 'elapsed_seconds')}
                for result in (first, edited, docx, pdf, parsed, restored, direct_pdf, direct_parsed)]))
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_real_container_isolation(tmp_path, record_property):
    inputs, outputs = tmp_path / 'inputs', tmp_path / 'outputs'
    inputs.mkdir()
    outputs.mkdir()
    (inputs / 'request.json').write_text(json.dumps({'operation':'probe_isolation', 'payload':{}}))
    asyncio.run(DockerBackend().run('f' * 64, inputs, outputs, {
        'image': os.getenv('AGENTCHAT_SANDBOX_IMAGE','agentchat-sandbox:v1'), 'timeout':30}))
    result = json.loads((outputs/'result.json').read_text())
    assert result and all(result.values()), result
    record_property('isolation', json.dumps(result))


def test_real_container_interruption_and_new_owner_recovery(database, monkeypatch, record_property):
    """Inject cancellation after a real docker start, then recover with a new run owner."""
    monkeypatch.setattr(SandboxService, 'backend_factory', DockerBackend)
    original_command = sandbox_runtime.command
    interrupted_container = None

    async def cancel_after_start(args, **kwargs):
        nonlocal interrupted_container
        output = await original_command(args, **kwargs)
        if args[1] == 'start' and interrupted_container is None:
            interrupted_container = args[2]
            raise asyncio.CancelledError()
        return output

    async def check():
        try:
            run, _, _ = await task()
            monkeypatch.setattr(sandbox_runtime, 'command', cancel_after_start)
            with pytest.raises(asyncio.CancelledError):
                await SandboxService(RunContext(run.id, 'owner')).write('中断恢复.md', 'Recovered evidence', 'recover-step')
            assert interrupted_container is not None
            assert await SandboxDao.document(run.user_id, run.conversation_id, '中断恢复.md') is None
            jobs = await SandboxDao.jobs(run.user_id, run.conversation_id)
            assert len(jobs) == 1 and jobs[0].status == 'interrupted'
            assert not await original_command([sandbox_runtime.docker_binary(), 'ps', '-aq', '--filter',
                'name=^/' + interrupted_container + '$'])
            await RunDao.finish(run.id, 'interrupted')
            await RunDao.requeue(run.id)
            await RunDao.claim(run.id, 'recovery-owner')
            monkeypatch.setattr(sandbox_runtime, 'command', original_command)
            recovered = await SandboxService(RunContext(run.id, 'recovery-owner')).write(
                '中断恢复.md', 'Recovered evidence', 'recover-step')
            assert recovered['job_id'] == jobs[0].id
            assert (root_path() / recovered['artifact']['relative_path']).read_text(encoding='utf-8') == 'Recovered evidence'
            row = await SandboxDao.job(recovered['job_id'], run.user_id, run.conversation_id)
            assert row.status == 'succeeded' and row.owner == 'recovery-owner'
            record_property('recovered_job', json.dumps({k: recovered[k] for k in ('job_id', 'runtime', 'elapsed_seconds')}))
        finally:
            await cleanup(database)
    asyncio.run(check())
