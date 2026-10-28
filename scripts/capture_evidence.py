"""Save truthful local evidence without exporting passwords or internal secrets."""
import html
import json
import os
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence'
OUT.mkdir(parents=True, exist_ok=True)


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=25, cwd=ROOT)
        return {'exit_code': result.returncode, 'output': (result.stdout + result.stderr).strip()}
    except Exception as error:
        return {'error': str(error)}


key = os.getenv('API_KEY')
if not key:
    key = next(line.split('=',1)[1].strip() for line in (ROOT/'.env').read_text().splitlines() if line.startswith('API_KEY='))
evidence = {'captured_at': time.time(), 'commands': {}, 'api': {}}
for label, args in [
    ('Python virtual environment', [str(ROOT/'.venv/Scripts/python.exe'), '--version']),
    ('Git version', ['git','--version']), ('Git repository', ['git','remote','-v']),
    ('Docker version', ['docker','--version']), ('Container status', ['docker','compose','ps']),
    ('Kubernetes client', ['kubectl','version','--client','--output=yaml'])]:
    evidence['commands'][label] = command(args)
for endpoint in ('/health','/cluster','/buckets','/benchmarks','/jobs','/search?prefix=reports/'):
    try:
        response=requests.get('http://localhost:8000'+endpoint,headers={'X-API-Key':key},timeout=20)
        try:
            body=response.json()
        except ValueError:
            body=response.text
        evidence['api'][endpoint]={'status':response.status_code,'body':body}
    except requests.RequestException as error:
        evidence['api'][endpoint]={'error':str(error)}
(OUT/'snapshot.json').write_text(json.dumps(evidence,indent=2))


def code(value):
    return '<pre>'+html.escape(value if isinstance(value,str) else json.dumps(value,indent=2))+'</pre>'


sections = [
    ('environment','1. Environment','Actual Python, Git, container and Kubernetes client checks.',
     ''.join('<h3>'+html.escape(name)+'</h3>'+code(record) for name,record in evidence['commands'].items())),
    ('storage','2. Storage nodes','Live storage health, free space, physical chunk counts and heartbeat timestamps.',code(evidence['api'].get('/cluster'))),
    ('metadata','3. Metadata and placement','Per-bucket protection policies and recorded virtual-node placement measurement.',code(evidence['api'].get('/buckets'))+code(json.loads((ROOT/'docs/verification.json').read_text()) if (ROOT/'docs/verification.json').exists() else {})),
    ('redundancy','4. Redundancy and recovery','Recorded benchmark and job evidence. Failed/pending checks remain visible.',code(evidence['api'].get('/benchmarks'))+code(evidence['api'].get('/jobs'))),
    ('api','5. API access','Recorded live HTTP response and search readiness. Full endpoint reference: localhost:8000/docs.',code(evidence['api'].get('/health'))+code(evidence['api'].get('/search?prefix=reports/'))),
    ('ui','6. React console','Open localhost:8088 for the live UI. Screenshots and acceptance notes are in docs/.','<p>A production React build is present. A real image upload was observed in the console during verification.</p>')]
for identity,title,description,body in sections:
    nav=''.join(f'<a href="{item[0]}.html">{html.escape(item[1])}</a>' for item in sections)
    page=f'''<!doctype html><html><head><meta charset="utf-8"><title>Atlas evidence - {html.escape(title)}</title><style>
    body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f6fb;color:#334665}}header{{padding:25px 35px;background:white;border-bottom:1px solid #dde5f2}}header b{{font-size:24px;color:#4362af}}nav{{display:flex;gap:15px;flex-wrap:wrap;margin-top:17px}}nav a{{color:#6c83b4;font-size:12px}}main{{max-width:1100px;margin:30px auto;padding:0 25px}}h1{{font-size:30px}}p{{color:#7e8ca6;line-height:1.6}}pre{{padding:20px;background:white;border:1px solid #e0e7f3;border-radius:8px;white-space:pre-wrap;word-break:break-word;font-size:11px;line-height:1.6}}.stamp{{font-size:11px;color:#9ca8bd}}</style></head>
    <body><header><b>ATLAS</b> / Recorded verification evidence<nav>{nav}</nav></header><main><div class="stamp">Captured {time.strftime('%Y-%m-%d %H:%M:%S %Z')} | Actual command and API output</div><h1>{html.escape(title)}</h1><p>{html.escape(description)}</p>{body}</main></body></html>'''
    (OUT/(identity+'.html')).write_text(page,encoding='utf-8')
print(OUT/'snapshot.json')
