import json,threading,time,uuid
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parent
class Handler(BaseHTTPRequestHandler):
    def handle_request(self):
        body=self.rfile.read(int(self.headers.get("Content-Length",0))) if self.command=="POST" else None
        if self.command=="POST" and self.path=="/api/v2/jobs":
            payload=json.loads(body)
            with (ROOT/"mocked_submissions.jsonl").open("a",encoding="utf-8") as f:f.write(json.dumps(payload)+"\n")
            data=json.dumps({"job_id":str(uuid.uuid4()),"kind":payload["kind"],"params":payload.get("params",{}),"status":"mocked-only"}).encode()
            self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(data);return
        if self.command=="POST" and self.path!="/api/v2/estimate":
            self.send_response(405);self.end_headers();return
        response=requests.request(self.command,"http://127.0.0.1:8100"+self.path,data=body,headers={"Content-Type":"application/json","Accept-Encoding":"identity"},timeout=20)
        self.send_response(response.status_code)
        for key in ["Content-Type","Cache-Control"]:
            if key in response.headers:self.send_header(key,response.headers[key])
        self.end_headers();self.wfile.write(response.content)
    do_GET=handle_request
    do_POST=handle_request
    def log_message(self,*args):pass
server=ThreadingHTTPServer(("127.0.0.1",8159),Handler)
timer=threading.Timer(300,server.shutdown);timer.daemon=True;timer.start()
print("Read-only/mock-submit QA proxy8159; lifetime300seconds",flush=True)
try:server.serve_forever()
finally:server.server_close()
