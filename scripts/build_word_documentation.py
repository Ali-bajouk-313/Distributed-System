from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import json, textwrap
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/Atlas-Documentation-Parts-1-to-8.docx'
PLACE=ROOT/'docs/screenshot-placeholders'; PLACE.mkdir(parents=True,exist_ok=True)
SHOTS=[
('01-environment','Part 1 Environment','Maximize PowerShell. Show the virtual environment Python version, Git version and remote, Docker version, and kubectl client version. Keep the command and its output visible. Do not show .env contents.','Run .\\.venv\\Scripts\\python.exe --version; git --version; git remote -v; docker --version; kubectl version --client.'),
('02-services','Part 2 Storage nodes','Show docker compose ps with at least four healthy storage nodes, plus metadata, gateway and worker services. Take a second screenshot of docker network inspect distributed-net if the container list and shared network do not fit together.','Run docker compose ps, then docker network inspect distributed-net. Save an additional image as 02-network.png if needed.'),
('03-chunks','Part 3 Metadata and deduplication','In the UI upload identical bytes under two different keys. Show the object metadata with SHA256, version, ordered chunk identifiers and locations. Show shared chunk identifiers or reference counts as evidence of deduplication.','Open http://localhost:8088, select Objects and inspect the object. Uploading the same filename alone may create another version; use two different keys for the demonstration.'),
('04-placement','Part 3 Consistent hashing','Show the placement verification output, including sample size and the percentages of chunks moved on adding and removing a node. Clearly identify historical output if you do not rerun it.','Open docs/verification.json. Archived results: 20000 sample chunks, 17.77 percent moved on addition and 24.70 percent on removal.'),
('05-policies','Part 4 Redundancy policies','Show the Buckets page with one replication bucket using N3 W2 R2 and one erasure bucket using k4 m2. Ensure the policy values can be read.','Open Buckets in the UI. Replication requires W plus R greater than N. A 4 plus 2 stripe needs six distinct nodes.'),
('06-node-failure','Part 4 Failure detection','Capture the Overview before failure, then after a disposable test node misses heartbeats. Show its failed state and the repair job. Keep both before and after screenshots.','Run scripts/verify_cluster.py --required-only only after benchmarks finish. It restores the stopped node automatically. Save the before image as 06-node-before.png and the failed state as 06-node-failure.png.'),
('07-repair','Part 4 Repair and scrubbing','Show completed repair and scrub jobs and a successful checksum verification. A queued or running job is not proof of successful repair.','Open Maintenance and docs/fault-verification.json. The required failure, rebuild and bit rot checks passed on 4 November 2026.'),
('08-benchmarks','Part 4 Storage and repair comparison','Show completed measurement rows for replication and erasure coding on both small and large workloads. Include logical bytes, physical bytes, and repair read and write bytes.','Open Benchmarks in the UI or the completed benchmark JSON. Do not present partial measurements as the complete two workload comparison.'),
('09-swagger','Part 5 API endpoints','Show the Swagger endpoint list with bucket, upload, download, metadata, listing, deletion, search and cluster routes. Use additional screenshots if needed for readability.','Open http://localhost:8000/docs. Keep API keys and authorization values hidden.'),
('10-api-tests','Part 5 API test results','Show the full pytest command and the final test summary. The screenshot must show any failures or deselected tests honestly.','Run docker compose exec -T gateway1 python -m pytest tests/test_api_v2.py -q. Current core verification: 10 passed with the Elasticsearch test excluded. The search check remains pending.'),
('11-range-auth','Part 5 Byte ranges and authentication','Show a byte range request returning HTTP 206 and Content-Range, an unauthenticated request returning 401, and the rate limiting test result. Multiple full desktop images are acceptable.','Use Swagger or curl for the range request. The API tests also cover these cases. Avoid showing authentication secrets in commands or headers.'),
('12-ui-objects','Part 6 Upload download and browsing','Show a completed upload in Objects, the object list, metadata, and the downloaded file with a matching checksum. Use separate screenshots for the upload and checksum if needed.','Open http://localhost:8088. For checksums use Get-FileHash on the original and downloaded files.'),
('13-ui-cluster','Part 6 Cluster state','Show healthy nodes, chunk distribution and repair progress in Overview and Maintenance. Wait until fault tests and benchmark fragment removal have finished before taking a final healthy cluster screenshot.','Open Overview and Maintenance. Eight storage nodes were healthy in the current verification.'),
('14-ui-search','Part 6 Elasticsearch search','Show real search results with a key prefix, a size or date filter, and a user defined tag. Include the search criteria and matching results.','This screenshot is pending until Elasticsearch starts and indexing catches up. A failed search or empty unverified result must not be labeled successful.'),
('15-bonus-monitoring','Part 7 Bonus fault tolerance and monitoring','Show both gateway instances, completed RabbitMQ maintenance jobs, Prometheus targets and the Grafana dashboard. Kubernetes evidence should also show storage pods, persistent volumes and probes.','These bonus demonstrations are pending. Do not use a configuration file alone as proof of a running cluster or successful failover.'),
('16-bonus-deployment','Part 8 Bonus Kubernetes deployment','Show a successful GitHub Actions deployment, kubectl get pods,svc,hpa,pvc in namespace atlas, and measured chunk movement after adding a storage node.','Docker Desktop Kubernetes is being reinstalled. A local deployment and a cloud deployment must be identified separately; neither is proven by a workflow file alone.')]
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',36)
small=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',27)
for number,(name,title,_,_) in enumerate(SHOTS,1):
 im=Image.new('RGB',(1200,675),'#f6f7f9');d=ImageDraw.Draw(im)
 d.rectangle((3,3,1196,671),outline='#98a2b3',width=3)
 d.text((600,160),f'SCREENSHOT PLACEHOLDER {number:02d}',font=font,fill='#344054',anchor='mm')
 for i,line in enumerate(textwrap.wrap(title,45)):
  d.text((600,260+i*48),line,font=font,fill='black',anchor='mm')
 d.text((600,410),'Replace this image with your own full desktop capture',font=small,fill='#344054',anchor='mm')
 d.text((600,460),'This is a placeholder and is not evidence of a test result',font=small,fill='#344054',anchor='mm')
 d.rectangle((4,575,1195,670),fill='#e4e7ec')
 d.text((600,622),'Keep the Windows taskbar and clock visible',font=small,fill='black',anchor='mm')
 im.save(PLACE/(name+'.png'))
doc=Document();sec=doc.sections[0];sec.page_width=Inches(8.5);sec.page_height=Inches(11);sec.top_margin=sec.bottom_margin=Inches(.75)
sec.left_margin=sec.right_margin=Inches(.8)
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Caption']:
 st=doc.styles[name];st.font.name='Calibri';st.font.color.rgb=RGBColor(0,0,0)
 for border in list(st.element.iter(qn('w:pBdr'))):border.getparent().remove(border)
doc.styles['Normal'].font.size=Pt(11)
doc.styles['Normal'].paragraph_format.space_after=Pt(7)
doc.styles['Caption'].font.size=Pt(10)
footer=sec.footer.paragraphs[0];footer.alignment=2
footer.add_run('Distributed Storage Lab  |  ')
fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');footer._p.append(fld)
def p(text): return doc.add_paragraph(text)
def code(text):
 r=p(text).runs[0];r.font.name='Consolas';r.font.size=Pt(9)
def h(text):doc.add_heading(text,level=1)
doc.add_paragraph('Distributed Object Storage Lab Report','Title')
p('Explanation and screenshot guide for parts 1 to 8')
p('Prepared 4 November 2026. This report explains the implementation, how to demonstrate it, and what evidence to capture. The appendix contains clearly marked placeholder images. Replace each placeholder with your own complete desktop screenshot, including the Windows taskbar and clock.')
p('Verified in the current session: eight healthy storage nodes; HTTP 200 from the UI and its live cluster API proxy; 10 API tests passed with the Elasticsearch test excluded; and successful missed heartbeat recovery and bit rot scrubbing. Elasticsearch search, the full workload comparison, Kubernetes deployment and full desktop screenshots are still pending final evidence. Configuration files are not presented as proof of deployment.')
h('Part 1 Set up the environment')
p('The environment uses Python for storage services, the gateway and maintenance workers. A virtual environment isolates host side tooling. Git tracks the project and the configured remote is the Distributed System GitHub repository. Docker Compose runs the local services on the distributed-net network. Kubernetes manifests provide a separate deployment option.')
p('The source and Git history were restored from Distributed.zip after the original working folder was missing. Docker Desktop was started and the archived Compose syntax error was corrected by regenerating compose.yaml. The virtual environment was recreated and requests installed for the host recovery test.')
code('powershell -ExecutionPolicy Bypass -File scripts/start-required.ps1')
p('Use scripts/setup.ps1 to build on a new computer. Existing images can be started with start-required.ps1. The UI is at http://localhost:8088 and Swagger is at http://localhost:8000/docs. Read credentials locally from .env and keep them out of screenshots and the submitted report. Evidence to replace: image 01.')
h('Part 2 Develop the storage node module')
p('Each storage node stores chunks independently in its own persistent volume and exposes PUT, GET and DELETE chunk operations. SHA256 identifies the stored content. Uploads use an atomic replacement and synchronize bytes to disk. Reads verify the hash and reject corrupted content instead of returning it as valid data.')
p('Heartbeats report node identity, free disk space, stored bytes and chunk count. The integrated topology contains eight storage nodes, exceeding the minimum of four and leaving spare nodes for a six fragment erasure stripe to recover after a failure. The metadata control plane tracks the last heartbeat. Evidence to replace: image 02 and an additional network screenshot if needed.')
h('Part 3 Implement metadata and placement')
p('Objects are split into 4 MiB chunks. Metadata records bucket, key, version, creation time, size, content type, tags, object checksum and the ordered chunk identifiers. The original task stage used SQLite; the integrated framework uses a three member etcd cluster shared by the gateways and workers.')
p('A consistent hashing ring with virtual nodes maps each chunk identity to storage owners. Adding or removing a node changes ownership for a fraction of chunks. Archived verification used 20000 sample chunks: adding a fifth node moved 17.77 percent and removing a fourth node moved 24.70 percent. These are historical results, saved in docs/verification.json.')
p('Chunk identities combine the storage policy and content hash, allowing identical bytes using the same policy to share stored chunks. Reference counts retain chunks while object versions still refer to them. Garbage collection removes unreferenced physical data. Optional compression is applied before redundancy encoding. Evidence to replace: images 03 and 04.')
h('Part 4 Implement redundancy and recovery')
p('Replication defaults to N equals 3, W equals 2 and R equals 2. Writes need two acknowledgements and reads require the configured read quorum. W plus R is 4, which exceeds N. The implementation can reject operations when too few healthy replicas remain. These settings are configurable per bucket.')
p('Reed Solomon coding uses the zfec library. The example policy has four data and two parity fragments, placed on six distinct nodes. Any four valid fragments reconstruct the data; all fifteen four of six decoding combinations are covered by the API test suite. Bucket policies select replication or erasure coding.')
p('After missed heartbeats exceed the configured timeout, the control plane marks a node failed and enqueues a repair. Workers reconstruct affected chunks using healthy sources, write replacement fragments on remaining nodes, verify writes and update placement. The background scrubber rereads stored content, checks SHA256 and repairs corrupted or missing fragments. Recovery counters distinguish source bytes from replacement bytes.')
p('The current required recovery test passed missed heartbeat detection, rebuilding on remaining nodes, deliberate bit rot repair through scrubbing, and verification of the original bytes. Results are in docs/fault-verification.json. Two issues discovered during testing were corrected: lock leases now renew while waiting, and maintenance releases the shared metadata lock between chunks so downloads remain available during repairs.')
code('.\\.venv\\Scripts\\python.exe scripts/verify_cluster.py --required-only')
p('Run failure injection separately from benchmark fragment removal. The benchmark generator supports 5000 small files of 1 to 50 KiB and ten large files of 100 MiB for each policy. A full comparison is running and must finish before its results are claimed complete. Evidence to replace: images 05 to 08.')
p('Archived small workload measurements used 24 files, 671780 logical bytes and 620615 unique bytes. Replication stored 1861845 bytes, read 620615 repair bytes and wrote 620615 replacement bytes. Erasure coding stored 930966 bytes, read 620644 repair bytes and wrote 155161 replacement bytes. Replication has approximately 3 times coding overhead and 4 plus 2 erasure coding approximately 1.5 times; effective overhead after deduplication differs. These archived numbers do not replace the complete two workload comparison.')
bench=ROOT/'docs/benchmark-full-results.json'
if bench.exists():
 for run in json.loads(bench.read_text()).get('runs',[]):
  p(f"Completed full measurement: {run['workload']} with {run['mode']}, {run['file_count']} files, {run['logical_bytes']} logical bytes, {run['physical_bytes']} physical bytes, {run['repair_read_bytes']} repair read bytes and {run['repair_write_bytes']} repair write bytes.")
h('Part 5 Develop the API for data access')
p('FastAPI gateways handle authenticated user requests behind Nginx. Object uploads support streamed bodies and multipart form data. Downloads validate data and support byte ranges with HTTP 206 and Content-Range. Invalid or unsatisfiable ranges are handled explicitly. The API also exposes object versions, metadata, search, cluster state and maintenance jobs.')
routes=[('/buckets','POST and GET','Create and list buckets'),('/objects/{bucket}/{key}','PUT POST GET DELETE','Upload download or delete an object'),('/metadata/{bucket}/{key}','GET','Object metadata and chunk placements'),('/buckets/{bucket}/objects','GET','List objects and versions'),('/search','GET','Prefix size date and tag search'),('/cluster','GET','Node health free space and degraded objects'),('/jobs','GET','Maintenance progress'),('/jobs/{kind}','POST','Repair scrub rebalance garbage collection and indexing')]
table=doc.add_table(rows=1, cols=3);table.style='Table Grid'
for c,t in zip(table.rows[0].cells,['Route','Methods','Purpose']):c.text=t
for row in routes:
 for c,t in zip(table.add_row().cells,row):c.text=t
for row in table.rows:
 for cell in row.cells:
  for paragraph in cell.paragraphs:
   for run in paragraph.runs:run.font.size=Pt(9)
for row in table.rows:
 for cell in row.cells:
  borders=OxmlElement('w:tcBorders')
  for edge in ['top','left','bottom','right']:
   border=OxmlElement('w:'+edge);border.set(qn('w:val'),'single');border.set(qn('w:sz'),'4');border.set(qn('w:color'),'D9D9D9');borders.append(border)
  cell._tc.get_or_add_tcPr().append(borders)
for cell in table.rows[0].cells:
 for paragraph in cell.paragraphs:
  for run in paragraph.runs:run.bold=True
 shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'E7EEF5');cell._tc.get_or_add_tcPr().append(shade)
p('Authentication uses X-API-Key. A shared metadata counter enforces rate limits across gateways. Swagger exposes the interactive API documentation. Search uses Elasticsearch over the latest object metadata, with filters for key prefix, size, dates and user tags. Search remains unavailable until the replacement Elasticsearch image starts and indexing succeeds.')
p('The current core test run passed all 10 tests with Elasticsearch excluded. Coverage includes authentication, validation, upload and download integrity, ranges, deduplication and versions, multipart and empty files, concurrent versions, erasure coding and compression, load balancing and shared rate limits. The full suite must pass before claiming search verified. A load balancer fix prevents an application level search error from excluding healthy gateways. Evidence to replace: images 09 to 11.')
code('docker compose exec -T gateway1 python -m pytest tests/test_api_v2.py -q')
h('Part 6 Build a simple web interface')
p('The React console provides Overview, Objects, Buckets, Maintenance and Benchmarks pages. Users connect with the API key, select a bucket and upload a file. Object metadata shows checksums, versions, tags and placement. Objects can be browsed, downloaded and deleted. Search controls map to Elasticsearch filters.')
p('Overview shows node health, chunk distribution and cluster capacity. Maintenance shows repair, scrub, rebalance and garbage collection progress. The current UI page and the UI API proxy both returned HTTP 200 and reported eight healthy nodes. A browser interaction demonstration and the search demonstration still need screenshots. Evidence to replace: images 12 to 14.')
p('For the upload demonstration, keep the original file, upload it, download it again, and compare both SHA256 values with Get-FileHash. Matching filenames alone are not an integrity check. For the final cluster screenshot, wait until deliberate failure tests and benchmark recovery measurements have ended.')
h('Part 7 Implement load balancing and fault tolerance')
p('The prepared deployment includes two FastAPI gateways behind Nginx, RabbitMQ maintenance queues and multiple workers. Metadata is stored in a three member etcd cluster. Storage StatefulSets use persistent volumes, stable identities and readiness and liveness probes. Prometheus and Grafana configurations cover request throughput, latency, per node disk usage and chunks, degraded objects and repair traffic.')
p('These bonus configurations are included, but a successful Kubernetes application rollout, metadata failover, gateway failover and dashboard demonstration are not yet claimed. They require runtime evidence. Evidence to replace: image 15 and additional monitoring images as needed.')
h('Part 8 Deploy the system')
p('Dockerfiles are included for storage, metadata, gateway, worker and UI roles. Docker Compose supplies the local topology. Portable Kubernetes manifests define services, workloads, persistent storage and a gateway horizontal autoscaler. GitHub Actions workflows build and publish images and deploy to a configured Kubernetes cluster.')
p('GitHub Actions automates deployment; it does not provide a permanent Kubernetes cluster. Docker Desktop Kubernetes is being reinstalled and no context was configured at the last check. A local rollout must be described as local. No cloud rollout or successful GitHub deployment is claimed. To complete the demonstration, show a successful rollout, healthy pods and volumes, autoscaling metrics, and actual chunk redistribution after adding a storage node. Evidence to replace: image 16.')
doc.add_page_break();h('System workflow diagrams')
for title,name,caption in [('Write path','write','The gateway stores verified redundant fragments before committing the object metadata and scheduling search indexing.'),('Read path','read','The gateway resolves metadata, verifies enough healthy fragments, reconstructs the data and returns the requested bytes.'),('Failure and repair path','failure','Missed heartbeats trigger reconstruction on remaining nodes; placement changes only after replacement data is written.'),('Scrubbing path','scrub','Periodic validation detects damaged content and restores it from healthy sources, recording repair traffic.')]:
 doc.add_heading(title,level=2);para=p(caption);para.paragraph_format.keep_with_next=True
 doc.add_picture(str(ROOT/'docs/diagrams'/(name+'.png')),width=Inches(5.1))
doc.add_page_break();h('How to replace the screenshot images')
p('Each following page contains a placeholder, the exact screen to capture, and a preparation step. A placeholder is not a screenshot and does not prove that a test passed. Keep any unfinished status visible in the report until you have the corresponding evidence.')
p('Maximize the application without entering F11 full screen mode. Keep the Windows taskbar, clock and date visible. Use Print Screen or Snipping Tool in Full screen mode, save the whole image, and do not crop it. Hide secrets before capture.')
p('In Word, right click the placeholder image and choose Change Picture, then This Device. Select your screenshot. Preserve its aspect ratio and check that the taskbar and clock are still readable. Update the caption to state what actually happened and when. Use extra pages if the results need more than one screenshot.')
p('Alternatively save the PNG files using the names below in docs/screenshots/fullscreen and rerun scripts/build_word_documentation.py with the bundled document runtime. Matching screenshots are inserted automatically in place of the placeholder images. Keep original captures as separate files for submission.')
for number,(name,title,desc,prep) in enumerate(SHOTS,1):
 doc.add_page_break();h(f'Screenshot {number:02d} {title}')
 actual=ROOT/'docs/screenshots/fullscreen'/(name+'.png')
 doc.add_picture(str(actual if actual.exists() else PLACE/(name+'.png')),width=Inches(6.65))
 doc.add_paragraph(('Captured evidence' if actual.exists() else 'Placeholder image to replace')+' — '+name+'.png',style='Caption')
 doc.add_heading('What to screenshot',level=2);p(desc)
 doc.add_heading('How to prepare',level=2);p(prep)
 p('Required framing: complete desktop with Windows taskbar and clock visible. Replace the placeholder only with your own actual capture.')
OUT.parent.mkdir(exist_ok=True);doc.save(OUT)
check=['# Screenshot replacement checklist','','Open output/Atlas-Documentation-Parts-1-to-8.docx. Replace each numbered placeholder with your own full desktop capture. Keep Windows taskbar and clock visible. Hide credentials. Save original PNG files under docs/screenshots/fullscreen.','']
for n,(name,title,desc,prep) in enumerate(SHOTS,1):check += [f'## {n:02d} {title}',f'Filename: {name}.png',desc,prep,'']
(ROOT/'docs/SCREENSHOT-CHECKLIST-PARTS-1-6.md').write_text('\n'.join(check),encoding='utf-8')
print(OUT)
