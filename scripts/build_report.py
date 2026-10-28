"""Build an illustrated PDF from recorded evidence; never invent test outcomes."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, Flowable, KeepTogether

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/pdf'
OUT.mkdir(parents=True, exist_ok=True)
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='AtlasTitle', fontName='Helvetica-Bold', fontSize=30, leading=36, textColor=colors.HexColor('#263e70'), spaceAfter=18))
styles.add(ParagraphStyle(name='AtlasSub', fontSize=11, leading=17, textColor=colors.HexColor('#738099'), spaceAfter=13))
styles['BodyText'].leading = 15
styles['BodyText'].fontSize = 9
styles['BodyText'].spaceAfter = 9
styles['Heading1'].textColor = colors.HexColor('#3859a3')
styles['Heading1'].fontSize = 19
styles['Heading1'].leading = 25
styles['Heading2'].fontSize = 12
styles['Heading2'].leading = 17
story = []


def p(text, style='BodyText'):
    story.append(Paragraph(text, styles[style]))


def table(rows, widths):
    converted = [[Paragraph(str(cell), styles['BodyText']) for cell in row] for row in rows]
    t = Table(converted, colWidths=widths, repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf0fd')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.HexColor('#35518c')),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#e1e7f1')),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    story.append(t)
    story.append(Spacer(1,12))


class Flow(Flowable):
    def __init__(self, labels):
        Flowable.__init__(self)
        self.labels = labels
        self.width = 485
        self.height = len(labels) * 45
    def draw(self):
        c = self.canv
        for i, label in enumerate(self.labels):
            y = self.height - (i + 1) * 45 + 10
            c.setFillColor(colors.HexColor('#f1f5fd'))
            c.setStrokeColor(colors.HexColor('#cfdaef'))
            c.roundRect(30, y, 425, 31, 6, fill=1, stroke=1)
            c.setFillColor(colors.HexColor('#46618f'))
            c.setFont('Helvetica',9)
            c.drawCentredString(242.5, y + 12, label)
            if i < len(self.labels) - 1:
                c.setStrokeColor(colors.HexColor('#92a6cc'))
                c.line(242.5,y,242.5,y-13)
                c.line(242.5,y-13,239.5,y-8)
                c.line(242.5,y-13,245.5,y-8)


def screenshot(name, title, caption):
    file = ROOT / 'docs/screenshots' / name
    if not file.exists():
        return
    story.append(PageBreak())
    p(title,'Heading1')
    p(caption)
    width,height=ImageReader(str(file)).getSize()
    factor=min(485/width,575/height)
    story.append(Image(str(file),width=width*factor,height=height*factor))
    p('Recorded screenshot: docs/screenshots/' + name,'AtlasSub')


p('ATLAS','AtlasTitle')
p('Distributed Object-Tolerant Storage Framework','Heading1')
p('Distributed Systems | Universite Antonine | 28 October 2026','AtlasSub')
p('Implementation, demonstration guide, verification evidence and outstanding checks. This report separates working demonstrations from prepared bonus deployment files.')
table([
    ['Part','Implementation and evidence'],
    ['1. Environment','Python virtual environment, Git remote, Docker and Kubernetes clients configured.'],
    ['2. Storage nodes','Eight independent persistent storage nodes; SHA-256 checks and heartbeats.'],
    ['3. Metadata and placement','Fixed 4-MiB chunks, optional compression, versions, virtual-node consistent hashing and reference-counted deduplication.'],
    ['4. Redundancy and recovery','N=3,W=2,R=2 replication; per-bucket zfec 4+2 coding; repair and scrub workers. Small-workload repair measured. Final failure-suite status must be checked in recorded JSON.'],
    ['5. Data API','Streaming/multipart upload, byte ranges, metadata, search, API keys and shared rate limiting. Initial live API run: 10 passed; search was deferred during image download.'],
    ['6. React UI','Built and opened at localhost:8088. A real file upload was observed. Screenshot evidence follows.'],
    ['7-8. Bonus','Dockerfiles, worker queues, monitoring, Kubernetes manifests and GitHub workflows prepared. Complete remote deployment is not claimed.']
],[100,385])
p('Current status and exact commands','Heading2')
p('Read docs/RUN-AND-DEMONSTRATE.md for the current acceptance notes, credentials location, workload scope and demonstration commands. This report intentionally contains no private passwords.')
story.append(PageBreak())
p('Write path','Heading1')
story.append(Flow(['Authenticate API key and apply shared rate limit', 'Stream upload; read 4-MiB chunks; compute SHA-256',
    'Reuse content identity or optionally compress new content', 'Select distinct ring nodes for replication or 4+2 fragments',
    'Verify and persist fragments; require the write policy', 'Atomically commit version, ordered chunk list and reference counts',
    'Queue metadata indexing for Elasticsearch']))
p('A bucket selects replication or erasure coding. The content hash identifies original bytes; fragment hashes identify encoded stored bytes. Writes become visible only after their metadata transaction commits.')
p('Read path','Heading1')
story.append(Flow(['Authenticate and parse byte range', 'Read version and recorded chunk locations', 'Obtain read quorum or any k verified erasure fragments',
    'Decode, decompress and verify original SHA-256', 'Return verified bytes with 200/206, ETag and range headers']))
story.append(PageBreak())
p('Failure and repair path','Heading1')
story.append(Flow(['Missed heartbeats for 20 seconds mark a node failed', 'Persist repair job in etcd outbox; publish to RabbitMQ',
    'Worker obtains verified copies or k healthy fragments', 'Reconstruct and hash-check the original content',
    'Write replacement on a distinct healthy node', 'Commit the new location; track recovery reads and writes']))
p('Eight nodes provide spare capacity beyond the six distinct nodes used by each 4+2 stripe. Replacements are committed before obsolete physical data becomes eligible for collection.')
p('Background scrubber','Heading1')
story.append(Flow(['Periodic schedule or manual scrub job', 'Re-read referenced physical fragments and validate SHA-256',
    'Treat missing/corrupt bytes as unavailable fragments', 'Recover from healthy copies or k valid shares',
    'Atomically replace corrupt bytes and record scrub progress']))
story.append(PageBreak())
p('Measured comparison','Heading1')
p('The completed small-file measurement used identical seeded bytes under both policies: 24 logical objects, 22 unique chunks and one duplicate in every ten objects. One physical fragment per unique chunk was removed and rebuilt on the live cluster.')
table([
    ['Metric','Replication','Reed-Solomon 4+2'],
    ['Logical bytes','671,780','671,780'],
    ['Unique bytes','620,615','620,615'],
    ['Encoded stored bytes','1,861,845','930,966'],
    ['Coding overhead','3.0000x','1.5001x'],
    ['Effective overhead after deduplication','2.7715x','1.3858x'],
    ['Recovery source bytes','620,615','620,644'],
    ['Recovery target bytes','620,615','155,161'],
    ['Rebuilt fragments','22','22']
],[205,140,140])
p('Erasure coding halves coding storage overhead for this configuration. Repairing one erasure fragment reads approximately one original chunk and writes one quarter chunk. Padding explains small differences. Scrub scans and protocol headers are excluded from recovery traffic.')
p('Workload completion','Heading2')
p('The full generator supports 5,000 small files and ten 100-MiB files per mode. That complete benchmark did not finish before the presentation deadline. Two replicated 100-MiB objects were uploaded and checksum-verified; their repair measurement was interrupted by a concurrent failure test. Do not describe this as a completed full benchmark.')
benchmark = ROOT / 'docs/benchmark-results.json'
if benchmark.exists():
    data=json.loads(benchmark.read_text())
    p('Recorded benchmark JSON contains '+str(len(data.get('runs',[])))+' completed measurement rows. Consult that JSON for any later completed large-object measurements.')
p('API tests','Heading2')
xml=ROOT/'docs/api-results.xml'
if xml.exists():
    tree=ET.parse(xml)
    tests=tree.findall('.//testcase')
    passed=sum(not list(test) or all(child.tag not in ('failure','error','skipped') for child in test) for test in tests)
    p(f'Recorded JUnit evidence: {passed} passing test cases out of {len(tests)} recorded cases. Exact output: docs/api-results.xml.')
else:
    p('Initial recorded console run: 10 API tests passed; Elasticsearch search test was deselected while its image downloaded.')
p('Checks include streaming, cross-chunk and suffix byte ranges, invalid ranges, deduplication, versions, concurrent writes, compression, all 15 possible four-of-six erasure recoveries, authentication and shared rate limiting.')
screenshot('01-live-overview.jpg','Live cluster overview','Live browser screenshot showing the node-distribution interface and eight-node health summary during verification.')
screenshot('02-bucket-policies.jpg','Bucket policies','The UI offers independently configured replication and erasure-coding buckets. Some screenshots may record an intermediate restart state; use current live health and the acceptance notes when presenting.')
for name,title in [('03-objects.jpg','Object access'),('04-maintenance.jpg','Maintenance and repair progress'),('05-benchmarks.jpg','Benchmark comparison'),('06-api-docs.jpg','FastAPI documentation')]:
    screenshot(name,title,'Actual browser capture from the running application; no simulated dashboard values are used.')
story.append(PageBreak())
p('Demonstration checklist','Heading1')
for line in [
    'Start Docker Desktop and the required stack. Open localhost:8088 and use API_KEY from .env.',
    'Create one replicated bucket and one 4+2 erasure-coded bucket.',
    'Upload a file; inspect its SHA-256, version, tags and physical fragment locations.',
    'Download the object and compare its checksum. Demonstrate a byte-range request in Swagger or curl.',
    'Upload identical bytes under another key and inspect the shared chunk reference count.',
    'Demonstrate prefix/size/date/tag search after Elasticsearch is healthy.',
    'Queue a scrub and inspect Maintenance job progress and recovery byte counters.',
    'Run failure checks separately from benchmark fragment removal, then capture the successful final results.'
]: p('- '+line)
p('Source delivery','Heading2')
p('framework/: integrated services. ui/: React source. docker/: service Dockerfiles. compose.yaml: local topology. tests/: API and coding tests. scripts/: setup, generators, benchmarks and fault checks. deploy/: Kubernetes, Nginx and monitoring. .github/workflows/: CI and Kubernetes deployment. docs/: evidence and run guide.')
p('Practical limits','Heading2')
p('This lab uses an API key with workspace-wide access and internal service isolation. Transport TLS and multi-user authorization are not configured. The local machine experienced memory exhaustion during final integration, so bonus containers were deprioritized. Remote deployment requires a configured cluster/runner and credentials; GitHub alone does not host Kubernetes.')


def footer(canvas, doc):
    canvas.setStrokeColor(colors.HexColor('#e1e7f1'))
    canvas.line(54,40,541,40)
    canvas.setFont('Helvetica',8)
    canvas.setFillColor(colors.HexColor('#8995aa'))
    canvas.drawString(54,27,'ATLAS | Distributed Systems | Recorded evidence and demonstration guide')
    canvas.drawRightString(541,27,str(doc.page))


path=OUT/'Atlas-Storage-Report.pdf'
SimpleDocTemplate(str(path),pagesize=A4,rightMargin=54,leftMargin=54,topMargin=50,bottomMargin=54).build(story,onFirstPage=footer,onLaterPages=footer)
print(path)
