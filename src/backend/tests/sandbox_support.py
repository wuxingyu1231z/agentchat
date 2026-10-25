"""Explicitly simulated container. Never used by production; not proof of Docker isolation."""
import json


class FakeDockerBackend:
    calls = 0

    async def run(self, identity, inputs, outputs, policy):
        type(self).calls += 1
        request = json.loads((inputs / 'request.json').read_text(encoding='utf-8'))
        operation, payload = request['operation'], request['payload']
        if operation == 'parse_pdf':
            from agentchat.services.research_sources import parse_pdf
            result = parse_pdf((inputs / 'input.bin').read_bytes())
        else:
            suffix = payload.get('suffix', '.pdf' if operation == 'docx_to_pdf' else '.docx')
            content = payload.get('content', '')
            if operation == 'edit':
                content = (inputs / 'input.bin').read_text(encoding='utf-8')
                if content.count(payload['old_text']) != 1:
                    raise ValueError('edit does not match')
                content = content.replace(payload['old_text'], payload['new_text'])
            (outputs / ('document' + suffix)).write_text(content, encoding='utf-8')
            result = {'output': 'document' + suffix}
        (outputs / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        return {'image_id': 'fake-unit-test-image', 'container_name': 'fake-' + identity[:12], 'exit_code': 0}
