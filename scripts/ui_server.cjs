// Zero-dependency local preview, serving the actual production React build.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../ui/dist');
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};
http.createServer((req,res)=>{
  if(req.url.startsWith('/evidence/')){
    const evidenceRoot=path.resolve(__dirname,'../docs/evidence');
    const target=path.resolve(evidenceRoot,req.url.slice('/evidence/'.length).split('?')[0]||'environment.html');
    if(!target.startsWith(evidenceRoot+path.sep)||!fs.existsSync(target)){res.writeHead(404);res.end();return}
    res.writeHead(200,{'Content-Type':mime[path.extname(target)]||'application/json'});
    fs.createReadStream(target).pipe(res);return;
  }
  if(req.url.startsWith('/api/')){
    const upstream=http.request({hostname:'127.0.0.1',port:8000,path:req.url.slice(4),method:req.method,headers:req.headers},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res)});
    upstream.on('error',()=>{res.writeHead(503,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'Gateway is starting; refresh in a moment.'}))});
    req.pipe(upstream);return;
  }
  const target=path.resolve(root,'.'+decodeURIComponent(req.url.split('?')[0]));
  if(target!==root&&!target.startsWith(root+path.sep)){res.writeHead(403);res.end();return}
  const file=fs.existsSync(target)&&fs.statSync(target).isFile()?target:path.join(root,'index.html');
  res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream'});
  fs.createReadStream(file).pipe(res);
}).listen(Number(process.env.PORT||8088),'127.0.0.1',()=>console.log('Atlas UI: http://localhost:'+(process.env.PORT||8088)));
