"""Single-file Jacksonville project-readiness chat application.

Run with:  python app.py
Set GEMINI_API_KEY before starting the server.  No Python packages are needed.
"""
from __future__ import annotations

import json
import os
from html import escape
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")

SYSTEM_PROMPT = """You are Jax Project Advisor, a warm, precise City of Jacksonville
project-readiness assistant. You help a prospective applicant understand likely permit
routing and the best City conversation to have BEFORE formal submission. You are not a
City employee, cannot approve permits, and must never state that a permit is confirmed.

Have a natural conversation. Read the full conversation. If key routing facts are missing,
ask ONE short, friendly, high-value follow-up question. Do not ask questions already
answered. Once you have enough information, produce the final project-readiness brief.

Use these screening rules, which are preliminary and require City confirmation:
- Business use change, expansion, relocation, or an additional use: flag Certificate of
  Use (COU) for the preliminary matrix.
- Occupancy classification change: flag a Change of Use Building Permit.
- Interior construction/alterations: flag Building Permit.
- New or modified plumbing: flag Plumbing Permit.
- Parking, access, drainage, utilities, landscaping, sidewalks, or driveways: flag Civil
  review / Site-Work Permit.
- Work in public right-of-way: flag Right-of-Way Permit.
- Mechanical/HVAC, electrical, fire protection, commercial cooking, hazardous materials,
  special occupancy, zoning, historic/environmental/flood/parking concerns: tell the user
  to confirm the appropriate review with City staff; do not present these as confirmed permits.

For a final answer, recommend the most relevant coordination groups based on scope, such as
Building Inspection Division / Permit Coordinator, Zoning, Fire Prevention, Development
Services (site, drainage, concurrency), or Public Works/right-of-way. Do not invent named
individuals, appointments, email addresses, fees, deadlines, or integrations. State that
the applicant should submit through JaxEPICS only after the City confirms the route.

Return ONLY valid JSON, with this exact shape:
{
  "stage":"follow_up"|"final",
  "message":"friendly conversational response",
  "question":"one question when stage is follow_up, otherwise empty string",
  "brief":{"project_summary":"","likely_route":"","city_conversations":[{"group":"","why":"","bring":""}],"checklist_groups":[{"outcome":"preliminary-route"|"review-flag"|"preparation"|"specialized","title":"","description":"","items":[{"item":"","category":"","why":"","source":"Based on your description and preliminary screening rules"}]}],"notes":[""]}
}
For follow_up, brief may be null. For final, include a concise complete brief with at least
one checklist item. Use all applicable groups: preliminary-route for potential permit-matrix
items, review-flag for City confirmation items, preparation for documents/records to collect,
and specialized only when a separate workflow is clearly relevant. Every checklist item must
say why it appears and give a truthful source. Never claim a source document was consulted if
it was not supplied. Never request sensitive personal data; address or parcel is optional.
"""

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jax Project Advisor</title><style>
:root{--ink:#102d45;--blue:#0878ae;--pale:#eaf5f8;--line:#cbd9e1;--muted:#526671}*{box-sizing:border-box}body{margin:0;background:#f5f8fa;color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:940px;margin:auto;padding:32px 20px 70px}header{padding:12px 0 26px}.eyebrow{font-size:.76rem;font-weight:800;color:var(--blue);letter-spacing:.11em}h1{font-size:clamp(2rem,5vw,3.3rem);margin:5px 0}header p{color:var(--muted);max-width:680px}.notice{background:#fff7df;border:1px solid #edcf80;padding:12px 16px;border-radius:10px;margin:0 0 20px}.chat{display:grid;gap:16px}.bubble{max-width:800px;padding:16px 18px;border-radius:14px;background:white;border:1px solid var(--line);white-space:pre-wrap}.bubble.user{margin-left:auto;background:var(--ink);color:#fff;border:0}.bubble.assistant{border-left:5px solid var(--blue)}.composer{position:sticky;bottom:10px;background:white;border:1px solid var(--line);border-radius:14px;padding:12px;box-shadow:0 6px 25px #17364d18}.composer textarea{border:0;outline:0;resize:vertical;min-height:76px;width:100%;font:inherit;color:var(--ink)}.controls{display:flex;justify-content:space-between;align-items:center;gap:12px;color:var(--muted);font-size:.85rem}button{border:0;border-radius:8px;background:var(--blue);color:white;padding:10px 16px;font:inherit;font-weight:700;cursor:pointer}button:disabled{opacity:.6;cursor:wait}.brief{margin-top:8px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px}.brief h2{margin:0 0 8px}.brief h3{font-size:1rem;margin:22px 0 6px}.route{background:var(--pale);border-radius:9px;padding:12px}.cards{display:grid;gap:9px}.card{border:1px solid var(--line);border-radius:9px;padding:11px 13px}.card strong{display:block}.tag{font-size:.72rem;font-weight:800;text-transform:uppercase;color:var(--blue)}.check-group{margin-top:19px;border:1px solid var(--line);border-radius:11px;overflow:hidden}.check-group h3{margin:0;padding:14px 16px 2px}.check-group>p{margin:0;padding:0 16px 13px;color:var(--muted);font-size:.9rem}.check-group.preliminary-route h3{border-left:5px solid #1782b6}.check-group.review-flag h3{border-left:5px solid #c17a00}.check-group.preparation h3{border-left:5px solid #39814c}.check-group.specialized h3{border-left:5px solid #7748a7}.check-item{display:flex;gap:12px;padding:13px 16px;border-top:1px solid var(--line);cursor:pointer}.check-item input{width:18px;height:18px;margin-top:3px;accent-color:var(--blue)}.check-item strong{display:block}.why,.source{display:block;font-size:.88rem;margin-top:3px;color:var(--muted)}.source{font-size:.78rem}.check-item input:checked+span strong{text-decoration:line-through;opacity:.7}ul{padding-left:20px;margin:7px 0}.disclaimer{font-size:.84rem;color:var(--muted);margin-top:18px;border-top:1px solid var(--line);padding-top:12px}.error{color:#9f1d1d;background:#fff0f0;border-color:#e9bbbb}@media print{.composer,.notice{display:none}.wrap{max-width:none;padding:0}.bubble{display:none}}
</style></head><body><main class="wrap"><header><div class="eyebrow">CITY OF JACKSONVILLE · PROJECT INTAKE</div><h1>Jax Project Advisor</h1><p>Describe your project in your own words. We’ll ask only the questions needed to prepare a preliminary permit-routing checklist and City coordination plan.</p></header><div class="notice">This is preliminary project-readiness guidance, not a City determination. Final requirements are confirmed by City staff.</div><section class="chat" id="chat" aria-live="polite"></section><form class="composer" id="form"><textarea id="message" required placeholder="For example: I’m converting a retail suite into a small restaurant and changing the HVAC, plumbing, and signage."></textarea><div class="controls"><span>Do not include sensitive information.</span><button id="send" type="submit">Send</button></div></form></main><script>
const chat=document.querySelector('#chat'),form=document.querySelector('#form'),input=document.querySelector('#message'),send=document.querySelector('#send');let history=[];
function bubble(text,kind='assistant'){const el=document.createElement('div');el.className='bubble '+kind;el.textContent=text;chat.append(el);el.scrollIntoView({behavior:'smooth',block:'end'});return el}
function item(tag,title,why){const x=document.createElement('div');x.className='card';x.innerHTML=`<span class="tag"></span><strong></strong><span></span>`;x.querySelector('.tag').textContent=tag||'';x.querySelector('strong').textContent=title||'';x.querySelector('span:last-child').textContent=why||'';return x}
function checklistGroup(group){const section=document.createElement('section');section.className='check-group '+(group.outcome||'');const title=document.createElement('h3');title.textContent=group.title||'Checklist';section.append(title);if(group.description){const desc=document.createElement('p');desc.textContent=group.description;section.append(desc)};(group.items||[]).forEach(row=>{const label=document.createElement('label');label.className='check-item';const box=document.createElement('input');box.type='checkbox';const copy=document.createElement('span');const heading=document.createElement('strong');heading.textContent=row.item||'';const why=document.createElement('span');why.className='why';why.textContent='Why this appears: '+(row.why||'');const source=document.createElement('span');source.className='source';source.textContent='Source: '+(row.source||'Based on your description and preliminary screening rules');copy.append(heading,why,source);label.append(box,copy);section.append(label)});return section}
function brief(data){const b=data.brief;if(!b)return;const el=document.createElement('article');el.className='brief';const h=document.createElement('h2');h.textContent='Preliminary permit & document checklist';el.append(h);const summary=document.createElement('p');summary.textContent=b.project_summary;el.append(summary);const route=document.createElement('div');route.className='route';route.textContent=b.likely_route;el.append(route);const meetingTitle=document.createElement('h3');meetingTitle.textContent='Your next City conversations';el.append(meetingTitle);const meetings=document.createElement('div');meetings.className='cards';(b.city_conversations||[]).forEach(x=>meetings.append(item(x.group,x.why,'Bring: '+x.bring)));el.append(meetings);const checkTitle=document.createElement('h3');checkTitle.textContent='Detailed preliminary checklist';el.append(checkTitle);(b.checklist_groups||[]).forEach(group=>el.append(checklistGroup(group)));if((b.notes||[]).length){const nt=document.createElement('h3');nt.textContent='Follow-up notes';el.append(nt);const ul=document.createElement('ul');b.notes.forEach(x=>{const li=document.createElement('li');li.textContent=x;ul.append(li)});el.append(ul)}const d=document.createElement('p');d.className='disclaimer';d.textContent='Not a City determination. Confirm final routing, documents, fees, and review requirements with the City before submitting through JaxEPICS.';el.append(d);coordinationForm(el,b);chat.append(el);el.scrollIntoView({behavior:'smooth',block:'start'})}
function coordinationForm(container,brief){if(!window.TURNSTILE_SITE_KEY)return;const group=(brief.city_conversations||[])[0]||{};const section=document.createElement('section');section.className='brief';section.innerHTML='<h2>Request City coordination</h2><p>Send this preliminary project summary to the appropriate City contact after completing the security check.</p><form class="coordination-form"><label>Name<input name="name" required autocomplete="name"></label><label>Email<input name="email" type="email" required autocomplete="email"></label><label>Project address or location<input name="projectAddress" required autocomplete="street-address"></label><label>Message<textarea name="message" required rows="5"></textarea></label><div class="turnstile-widget"></div><p class="coordination-notice" aria-live="polite"></p><button type="submit">Prepare City email</button></form>';const form=section.querySelector('form'),notice=section.querySelector('.coordination-notice'),widget=section.querySelector('.turnstile-widget');form.message.value=(brief.project_summary||'')+'\n\nPreliminary route: '+(brief.likely_route||'');let widgetId;const render=()=>{if(!window.turnstile){notice.textContent='Security verification is still loading. Please wait a moment.';return}widgetId=window.turnstile.render(widget,{sitekey:window.TURNSTILE_SITE_KEY,theme:'light',callback:()=>{notice.textContent=''},'expired-callback':()=>{notice.textContent='Verification expired. Please complete it again.'},'error-callback':()=>{notice.textContent='Verification could not load. Please refresh and try again.'}})};if(window.turnstile)render();else window.addEventListener('load',render,{once:true});form.addEventListener('submit',async event=>{event.preventDefault();const token=widgetId!==undefined&&window.turnstile?window.turnstile.getResponse(widgetId):'';if(!token){notice.textContent='Complete the security verification before preparing the email.';return}const button=form.querySelector('button');button.disabled=true;button.textContent='Verifying…';notice.textContent='';try{const response=await fetch('/api/coordination-request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:form.name.value,email:form.email.value,projectAddress:form.projectAddress.value,message:form.message.value,route:brief.likely_route||'Natural-language project intake',team:group.group||'Appropriate City review staff',turnstileToken:token})});const result=await response.json();if(!response.ok)throw Error(result.error||'Unable to prepare the City email.');window.location.href=result.mailtoUrl;notice.textContent='Your email application should now open with the request prepared.'}catch(err){notice.textContent=err.message}finally{button.disabled=false;button.textContent='Prepare City email';if(widgetId!==undefined&&window.turnstile)window.turnstile.reset(widgetId)}});container.append(section)}
function error(t){const el=bubble(t);el.classList.add('error')}
form.addEventListener('submit',async e=>{e.preventDefault();const text=input.value.trim();if(!text)return;bubble(text,'user');history.push({role:'user',text});input.value='';send.disabled=true;send.textContent='Thinking…';try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({history})});const d=await r.json();if(!r.ok)throw Error(d.error||'The advisor could not respond.');bubble(d.message);if(d.stage==='follow_up'&&d.question)bubble(d.question);if(d.stage==='final')brief(d);history.push({role:'model',text:d.message+(d.question?'\n'+d.question:'')})}catch(err){error(err.message)}finally{send.disabled=false;send.textContent='Send';input.focus()}});bubble('Hi — tell me about the project you are considering. A simple description is enough to start.');
</script></body></html>'''


def page_html() -> str:
    """Add the public Turnstile configuration only when coordination is enabled."""
    if not TURNSTILE_SITE_KEY:
        return HTML
    turnstile = (
        '<script>window.TURNSTILE_SITE_KEY="'
        + escape(TURNSTILE_SITE_KEY, quote=True)
        + '";</script><script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" defer></script>'
    )
    return HTML.replace("</head>", turnstile + "</head>")


def gemini_chat(history: list[dict]) -> dict:
    if not API_KEY:
        raise RuntimeError("The server is missing GEMINI_API_KEY. Add it to the environment, then restart the server.")
    contents = []
    for turn in history[-16:]:
        role = "model" if turn.get("role") == "model" else "user"
        text = str(turn.get("text", "")).strip()[:6000]
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    payload = {"system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]}, "contents": contents,
               "generationConfig": {"temperature": 0.35, "responseMimeType": "application/json"}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Gemini request failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Could not reach Gemini. Check the network and API configuration.") from exc
    try:
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Gemini returned an unreadable response. Please try again.") from exc
    if result.get("stage") not in ("follow_up", "final") or not isinstance(result.get("message"), str):
        raise RuntimeError("Gemini returned an unexpected response. Please try again.")
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path != "/": self.send_error(404); return
        body = HTML.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/chat": self.send_error(404); return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 2 <= size <= 100000: raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size).decode())
            history = data.get("history")
            if not isinstance(history, list) or not history: raise ValueError("A project description is required")
            self.send_json(200, gemini_chat(history))
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send_json(502, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Unexpected server error."})


if __name__ == "__main__":
    print(f"Jax Project Advisor running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
