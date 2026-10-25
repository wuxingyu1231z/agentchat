"""Fixed operations only. Runs inside the container; never imported by the application."""
import json
import subprocess
import sys
import time
from pathlib import Path

INPUT, OUTPUT = Path('/inputs'), Path('/outputs')


def parse_pdf(data):
    import fitz
    if not data.lstrip().startswith(b'%PDF-'):
        raise ValueError('Source is not a PDF')
    with fitz.open(stream=data, filetype='pdf') as doc:
        if doc.needs_pass:
            raise ValueError('Encrypted PDF is not supported')
        pages, remaining, truncated = [], 2_000_000, len(doc) > 300
        for index in range(min(len(doc), 300)):
            original = doc[index].get_text()
            retained = original[:remaining]
            pages.append({'page': index + 1, 'text': retained})
            remaining -= len(retained)
            if len(retained) < len(original) or remaining == 0:
                truncated = True
                break
        return {'pages': pages, 'total_pages': len(doc), 'truncated': truncated}


def main():
    req = json.loads((INPUT / 'request.json').read_text(encoding='utf-8'))
    operation, payload = req['operation'], req['payload']
    result = {}
    if operation == 'probe_isolation':
        import os
        import socket
        result = {'non_root': os.getuid() != 0}
        for label, path in [('inputs_readonly', INPUT / 'must-not-write'), ('root_readonly', Path('/app/must-not-write'))]:
            try:
                path.write_text('denial probe')
                result[label] = False
            except OSError:
                result[label] = True
        try:
            socket.create_connection(('1.1.1.1', 443), timeout=0.5).close()
            result['network_denied'] = False
        except OSError:
            result['network_denied'] = True
        result['credentials_absent'] = not any(os.getenv(key) for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LANGSMITH_API_KEY'))
        space = os.statvfs(OUTPUT)
        result['output_quota'] = space.f_blocks * space.f_frsize <= 32 * 1024 * 1024
    elif operation == 'parse_pdf':
        result = parse_pdf((INPUT / 'input.bin').read_bytes())
    elif operation in ('write', 'edit'):
        suffix = payload['suffix']
        if suffix not in ('.md', '.txt', '.json', '.csv', '.yaml', '.yml', '.docx', '.pdf'):
            raise ValueError('Unsupported document format')
        text = payload.get('content', '')
        if operation == 'edit':
            text = (INPUT / 'input.bin').read_text(encoding='utf-8')
            if text.count(payload['old_text']) != 1:
                raise ValueError('Edit must match exactly one occurrence')
            text = text.replace(payload['old_text'], payload['new_text'], 1)
        if suffix == '.json':
            json.loads(text)
        if suffix in ('.docx', '.pdf'):
            from docx import Document
            doc = Document()
            for line in text.splitlines():
                doc.add_paragraph(line)
            target = OUTPUT / 'document.docx'
            doc.save(target)
            if suffix == '.pdf':
                subprocess.run(['libreoffice', '-env:UserInstallation=file:///tmp/lo-profile', '--headless',
                    '--convert-to', 'pdf', '--outdir', str(OUTPUT), str(target)], check=True, timeout=90,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            (OUTPUT / ('document' + suffix)).write_text(text, encoding='utf-8')
        result = {'output': 'document' + suffix}
    elif operation in ('docx_to_pdf', 'pdf_to_docx'):
        if operation == 'docx_to_pdf':
            subprocess.run(['libreoffice', '-env:UserInstallation=file:///tmp/lo-profile', '--headless',
                '--convert-to', 'pdf', '--outdir', str(OUTPUT), str(INPUT / 'document.docx')],
                check=True, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = {'output': 'document.pdf'}
        else:
            from pdf2docx import Converter
            converter = Converter(str(INPUT / 'input.bin'))
            try:
                converter.convert(str(OUTPUT / 'document.docx'), start=0, end=None, multi_processing=False)
            finally:
                converter.close()
            result = {'output': 'document.docx'}
    else:
        raise ValueError('Unsupported sandbox operation')
    # A bounded file protocol avoids mixing library logs with result JSON.
    pending = OUTPUT / 'result.part'
    pending.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    pending.replace(OUTPUT / 'result.json')


if __name__ == '__main__':
    main()
    if '--hold' in sys.argv:
        # Keep tmpfs available until the trusted controller has collected outputs and removes this container.
        while True:
            time.sleep(1)
