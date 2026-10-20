"""Headless Edge smoke test of the real browser code and local endpoints.

AI and Turnstile are mocked; no live email, paid model call or City request is made.
"""
import os
import base64
import json
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app
import coordination
from project_workflow import build_brief, routing_facts, update_project

BROWSER = os.environ.get("BROWSER_EXECUTABLE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


class DevTools:
    """Small local WebSocket client for viewport emulation and browser test results."""
    def __init__(self,url):
        address=urllib.parse.urlsplit(url)
        self.socket=socket.create_connection((address.hostname,address.port),timeout=20)
        key=base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall((f'GET {address.path} HTTP/1.1\r\nHost: {address.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        response=b''
        while not response.endswith(b'\r\n\r\n'):response+=self.socket.recv(1)
        if b' 101 ' not in response:raise RuntimeError('Browser debug connection failed.')
        self.sequence=0

    def read(self,count):
        data=b''
        while len(data)<count:
            part=self.socket.recv(count-len(data))
            if not part:raise RuntimeError('Browser debug connection closed.')
            data+=part
        return data

    def send(self,data,opcode=1):
        mask=os.urandom(4)
        header=bytes([0x80|opcode])
        length=len(data)
        header+=bytes([0x80|length]) if length<126 else (bytes([0x80|126])+struct.pack('!H',length) if length<65536 else bytes([0x80|127])+struct.pack('!Q',length))
        self.socket.sendall(header+mask+bytes(value^mask[index%4] for index,value in enumerate(data)))

    def call(self,method,params=None):
        self.sequence+=1
        self.send(json.dumps({'id':self.sequence,'method':method,'params':params or {}}).encode())
        message=b''
        while True:
            first,second=self.read(2)
            length=second&127
            if length==126:length=struct.unpack('!H',self.read(2))[0]
            elif length==127:length=struct.unpack('!Q',self.read(8))[0]
            mask=self.read(4) if second&128 else None
            data=self.read(length)
            if mask:data=bytes(value^mask[index%4] for index,value in enumerate(data))
            opcode=first&15
            if opcode==9:self.send(data,10);continue
            if opcode==8:raise RuntimeError('Browser debug connection closed.')
            message+=data
            if not first&128:continue
            result=json.loads(message);message=b''
            if result.get('id')==self.sequence:
                if 'error' in result:raise RuntimeError(result['error'])
                return result.get('result',{})

    def evaluate(self,expression):
        result=self.call('Runtime.evaluate',{'expression':expression,'returnByValue':True})
        if 'exceptionDetails' in result:raise RuntimeError('Browser evaluation failed.')
        return result.get('result',{}).get('value')

    def close(self):self.socket.close()
HARNESS = r'''<script>
sessionStorage.clear();
window.TURNSTILE_SITE_KEY='test';
window.turnstile={render:()=>0,getResponse:()=> 'test',reset:()=>{}};
window.addEventListener('error',e=>document.body.dataset.scriptError=e.message);
window.addEventListener('load',async()=>{
  const check=(ok,message)=>{if(!ok)throw Error(message);};
  try {
    const composer=document.querySelector('#form'), reply=document.querySelector('#message');
    reply.value='I am converting an office to a restaurant.';
    await composer.onsubmit({preventDefault(){}});
    check(document.querySelectorAll('.bubble.assistant').length===2,'Follow-up must be one message');
    check(!document.querySelector('.brief'),'First turn should ask a question');
    check(document.querySelector('.topic.active strong').textContent==='Building & plumbing','Highlight the current question topic');
    check(document.querySelectorAll('.live-step').length > 0,'Build the sidebar before the final brief');
    check(document.querySelector('#progress-label').textContent.startsWith('2 of'),'Count supported topics');
    reply.value='Interior alterations, with no site work.';
    await composer.onsubmit({preventDefault(){}});
    check(document.querySelectorAll('.brief').length===1,'One plan should appear');
    check(document.querySelector('.open-topics').textContent.includes('Still to confirm'),'Explain incomplete topics alongside the preliminary report');
    check(document.querySelectorAll('.topic.deferred').length > 0,'Unfinished topics become explicit City confirmations');
    check(!document.querySelector('.topic.pending'),'A ready plan should not leave unexplained pending topics');
    check(document.querySelector('#intake-state').textContent.includes('still to confirm'),'Explain why a plan is ready with incomplete coverage');
    check(document.querySelector('.brief').textContent.includes('office'),'Preserve scope in summary');
    check(getComputedStyle(composer).position==='relative','Composer should not cover the chat');
    const rows=[...document.querySelectorAll('.project-checklist > li')];
    check(rows.length > 0,'Checklist must render as numbered rows');
    rows.forEach((row,index)=>{
      const bounds=row.getBoundingClientRect();
      check(bounds.height < 240,'Checklist row stretched to the height of its parent');
      if(index) check(bounds.top >= rows[index-1].getBoundingClientRect().bottom-1,'Checklist rows overlap');
    });
    const detail=rows[0].querySelector('details'); detail.open=true;
    check(rows[1].getBoundingClientRect().top >= rows[0].getBoundingClientRect().bottom-1,'Expanded checklist detail overlaps the next row');
    detail.open=false;
    const email=document.querySelector('.coordination-form');
    email.elements.applicantName.value='Test Applicant';
    email.elements.applicantEmail.value='applicant@example.com';
    await email.onsubmit({preventDefault(){}});
    check(document.querySelector('.email-preview').textContent.includes('Jax Project Advisor'),'Preview must include relevant suggestions');
    check(!document.querySelector('.email-open').hidden,'Reviewed draft must offer an email action');
    check(!document.querySelector('.email-preview').textContent.toLowerCase().includes('checklist'),'Do not dump the checklist');
    reply.value='Actually, we need a new driveway.';
    await composer.onsubmit({preventDefault(){}});
    check(document.querySelectorAll('.brief').length===1,'Correction must replace the plan');
    check(document.querySelector('.brief').textContent.includes('new driveway'),'Correction must update scope');
    check(document.querySelector('.brief').textContent.includes('Updated project details'),'Show correction history');
    check(document.querySelector('#live-checklist').textContent.includes('new driveway'),'Sidebar follows corrections');
    const originalView=workspace.dataset.view;
    document.querySelector('.mobile-switch [data-view="plan"]').click();
    check(workspace.dataset.view==='plan','Switch to the mobile plan');
    check(document.querySelector('.mobile-switch [data-view="plan"]').getAttribute('aria-pressed')==='true','Expose the selected view accessibly');
    check(document.documentElement.scrollWidth <= window.innerWidth+1,'Plan layout overflow');
    setView(originalView);
    check(document.documentElement.scrollWidth <= window.innerWidth+1,'Mobile layout overflow');
    if (persistentMode) {
      document.querySelector('#chat').textContent='';
      await resumeProject();
      check(document.querySelectorAll('.bubble.user').length===3,'Resume saved conversation');
      check(document.querySelector('.brief').textContent.includes('new driveway'),'Resume saved plan');
      check(document.querySelector('#live-checklist').textContent.includes('new driveway'),'Resume live checklist');
    } else {
      check(!sessionStorage.getItem('jax-project-id'),'Demo must not store a project reference');
      check(demoState.length > 0,'Demo must preserve context only in memory');
    }
    const savedFetch=window.fetch, savedSidebar=document.querySelector('#live-checklist').textContent;
    const savedCoverage=document.querySelector('#intake-progress').value;
    window.fetch=async()=>{throw Error('Test connection interruption');};
    try {
      reply.value='Please keep this reply for a retry.';
      await composer.onsubmit({preventDefault(){}});
      check(reply.value==='Please keep this reply for a retry.','Failed requests preserve the reply');
      check(document.querySelector('#live-checklist').textContent===savedSidebar,'Failed requests preserve the supported plan');
      check(document.querySelector('#intake-progress').value===savedCoverage,'Failed requests preserve coverage');
      check(!document.querySelector('#send').disabled,'Failed requests allow retry');
    } finally {window.fetch=savedFetch;}
    resetProject();
    check(!sessionStorage.getItem('jax-project-id'),'Restart must clear the browser project reference');
    check(!document.querySelector('.brief'),'Restart clears prior plan');
    check(document.querySelectorAll('.live-step').length===0,'Restart clears live checklist');
    check(document.querySelector('#intake-progress').value===0,'Restart clears topic coverage');
    currentProject=window.__preview.project;
    renderWorkspace(window.__preview);
    renderBrief(window.__preview.brief);
    document.querySelector('.brief').scrollIntoView();
    document.body.dataset.browserTest='passed';
  } catch(error) { document.body.dataset.browserTest='failed: '+error.message; }
});
</script>'''


def fake_model(history, project):
    message = history[-1]["text"]
    if message.startswith("I am"):
        values={"existing_use":"office", "proposed_use":"restaurant", "project_description":"convert an office to a restaurant", "business_use":"yes"}
        answer="That sounds like a useful transformation. What work are you planning inside the space?"
        ready=False
    elif message.startswith("Interior"):
        values={"interior_work":"yes", "alterations":"interior alterations", "site_work":"no"}
        answer="Thanks — I can prepare your preliminary plan from that scope."
        ready=True
    else:
        values={"site_work":"yes", "site_changes":"a new driveway"}
        answer="Thanks for the correction — I have updated the site scope."
        ready=True
    updates={key:{"value":value,"evidence":message,"correction":message.startswith("Actually")} for key,value in values.items()}
    updated=update_project(project,updates,message)
    return app.workspace_result({"stage":"final" if ready else "follow_up", "message":answer, "project":updated,
            "question_field":"interior_work" if not ready else "", "question_reason":"The proposed work helps identify the building review route." if not ready else "",
            "facts":routing_facts(updated), "brief":build_brief(updated) if ready else None})


@unittest.skipUnless(Path(BROWSER).exists(), "Set BROWSER_EXECUTABLE to a Chromium browser to run the UI smoke test.")
class BrowserTests(unittest.TestCase):
    def test_chat_plan_email_correction_resume_and_mobile_layout(self):
        self.run_flow(390,844)

    def test_desktop_checklist_layout(self):
        self.run_flow(1440,1100)

    def test_demo_chat_and_email_without_storage(self):
        self.run_flow(390,844,'demo')

    def run_flow(self,width,height,mode='persistent'):
        class Handler(app.Handler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path == "/":
                    from project_workflow import empty_project
                    first=fake_model([{'text':'I am converting an office to a restaurant.'}],empty_project())
                    second=fake_model([{'text':'Interior alterations, with no site work.'}],first['project'])
                    preview=fake_model([{'text':'Actually, we need a new driveway.'}],second['project'])
                    body=app.page_html().replace('</body>','<script>window.__preview='+json.dumps(preview)+'</script>'+HARNESS+'</body>').encode()
                    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
                    self.send_header('Content-Length',str(len(body)));app.add_security_headers(self)
                    self.end_headers();self.wfile.write(body)
                else:super().do_GET()
        with tempfile.TemporaryDirectory() as temporary:
            environment={"PROJECT_STORAGE_MODE":mode,"PROJECT_DB_PATH":str(Path(temporary)/'projects.sqlite3'), "VERCEL":"", "UPSTASH_REDIS_REST_URL":"", "UPSTASH_REDIS_REST_TOKEN":"", "KV_REST_API_URL":"", "KV_REST_API_TOKEN":""}
            with patch.dict(os.environ,environment), patch.object(app,'OPENAI_API_KEY','test'), patch.object(app,'TURNSTILE_SITE_KEY',''), patch.object(app,'openai_chat',side_effect=fake_model), patch.object(coordination,'verify_turnstile',return_value=True):
                server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    profile=Path(temporary)/'browser'
                    browser=subprocess.Popen([BROWSER,'--headless','--disable-gpu','--no-first-run','--disable-extensions','--disable-smooth-scrolling',
                        '--user-data-dir='+str(profile),'--remote-debugging-port=0','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    client=None
                    try:
                        deadline=time.monotonic()+15
                        active_port=profile/'DevToolsActivePort'
                        while not active_port.exists() and time.monotonic()<deadline:time.sleep(.1)
                        port=int(active_port.read_text().splitlines()[0])
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as response:pages=json.load(response)
                        client=DevTools(next(page['webSocketDebuggerUrl'] for page in pages if page['type']=='page'))
                        client.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':width<640})
                        client.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/'})
                        deadline=time.monotonic()+30
                        marker=''
                        while not marker and time.monotonic()<deadline:
                            marker=client.evaluate("document.body?.dataset.browserTest || ''")
                            if not marker:time.sleep(.1)
                        self.assertEqual(marker,'passed')
                        self.assertEqual(client.evaluate('window.innerWidth'),width,'Browser must use the actual requested viewport')
                        if os.environ.get('UI_SCREENSHOT_DIR'):
                            target=Path(os.environ['UI_SCREENSHOT_DIR']).resolve()
                            target.mkdir(parents=True,exist_ok=True)
                            client.evaluate("window.scrollTo({top:document.querySelector('.project-checklist').getBoundingClientRect().top+window.scrollY-90,behavior:'instant'})")
                            screenshot=client.call('Page.captureScreenshot',{'format':'png'})
                            (target/f'checklist-{width}.png').write_bytes(base64.b64decode(screenshot['data']))
                            client.evaluate("setView('plan')")
                            screenshot=client.call('Page.captureScreenshot',{'format':'png'})
                            (target/f'project-plan-{width}.png').write_bytes(base64.b64decode(screenshot['data']))
                    finally:
                        if client:client.close()
                        browser.terminate();browser.wait(timeout=10)
                finally:
                    server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
