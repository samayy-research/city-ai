"""Single-file Jacksonville project-readiness chat application.

Run with:  python app.py
Set OPENAI_API_KEY before starting the server. No Python packages are needed.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
from html import escape
from pathlib import Path
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_local_env() -> None:
    """Load an uncommitted local .env file without overriding real environment values."""
    env_file = Path(__file__).with_name(".env")
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key.replace("_", "").isalnum() and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


load_local_env()

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
# Set when using Pen AI or another OpenAI-compatible provider.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
OPENAI_TIMEOUT_SECONDS = 45
MAX_REQUEST_BYTES = 100_000
MAX_HISTORY_TURNS = 16
MAX_TURN_CHARS = 6_000

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """An upstream AI provider failure that is safe to show to visitors."""


def add_security_headers(handler: BaseHTTPRequestHandler) -> None:
    """Add baseline browser protections to every first-party response."""
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
    handler.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self' mailto:; connect-src 'self' https://challenges.cloudflare.com; "
        "frame-src https://challenges.cloudflare.com; img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com; "
        "style-src 'self' 'unsafe-inline'",
    )

SYSTEM_PROMPT = """You are Jax Project Advisor, a warm, precise City of Jacksonville
project-readiness assistant. You help a prospective applicant understand likely preliminary
permit routing and the best City conversation to have BEFORE formal submission. You are not
a City employee, cannot approve permits, and must never state that a permit is confirmed.

Read the full conversation. Extract only routing facts the visitor has actually provided;
do not infer a fact because it is common. You also guide the conversation naturally. When
one more detail would materially improve preliminary routing, acknowledge what the visitor
said and ask exactly one short, context-specific question in the same message. Choose the
most useful unanswered detail rather than following a fixed interview script. Do not ask
about a topic already answered, do not repeat a question, and do not turn the response into
a checklist. If the visitor mentions a location, acknowledge it warmly without implying any
City determination. Address or parcel information is optional.

Set ready_for_brief to false only when you ask that one follow-up question. Set it to true
when the shared scope is sufficient for a useful preliminary brief, even if some facts remain
unknown. When ready_for_brief is true, write a brief warm acknowledgement with no question
and no routing advice; the application creates the routing material from the facts.

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

Use the screening rules only to identify the facts above and to decide whether another detail
would be useful. Do not give routing advice, recommend City contacts, or create a checklist;
the application creates those from the structured facts. Do not invent named individuals,
appointments, email addresses, fees, deadlines, or integrations.

Return ONLY valid JSON, with this exact shape:
{
  "message":"brief, friendly response; include one natural question only when ready_for_brief is false",
  "ready_for_brief":true|false,
  "facts":{
    "business_use":"yes"|"no"|"not_sure"|"unknown",
    "occupancy_change":"yes"|"no"|"not_sure"|"unknown",
    "interior_work":"yes"|"no"|"not_sure"|"unknown",
    "plumbing":"yes"|"no"|"not_sure"|"unknown",
    "site_work":"yes"|"no"|"not_sure"|"unknown",
    "right_of_way":"yes"|"no"|"not_sure"|"unknown",
    "specialized_review":"yes"|"no"|"not_sure"|"unknown"
  }
}
Use "not_sure" only when the visitor explicitly says they do not know; use "unknown" when
the topic has not been addressed. Never request sensitive personal data.
"""

FACT_VALUES = {"yes", "no", "not_sure", "unknown"}
FACT_KEYS = (
    "business_use", "occupancy_change", "interior_work", "plumbing", "site_work",
    "right_of_way", "specialized_review",
)


def normalize_facts(value: object) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {key: raw.get(key) if raw.get(key) in FACT_VALUES else "unknown" for key in FACT_KEYS}


def checklist_item(item: str, why: str, category: str = "Permit routing") -> dict:
    return {"item": item, "category": category, "why": why,
            "source": "Based on your description and preliminary screening rules"}


def build_brief(facts: dict[str, str]) -> dict:
    routes, reviews, conversations, notes = [], [], [], []
    if facts["business_use"] == "yes":
        routes.append(checklist_item("Include Certificate of Use (COU) in the preliminary permit matrix", "A business-use change, expansion, relocation, or additional use was identified."))
        conversations.append({"group": "Zoning / Certificate of Use (COU)", "why": "Confirm the proposed use and COU route before other permit discussions.", "bring": "A short description of the current and proposed use."})
    if facts["occupancy_change"] == "yes":
        routes.append(checklist_item("Include Change of Use Building Permit in the preliminary permit matrix", "An occupancy-classification change was identified."))
    if facts["interior_work"] == "yes":
        routes.append(checklist_item("Include Building Permit in the preliminary permit matrix", "Interior construction or alterations were identified."))
    if facts["plumbing"] == "yes":
        routes.append(checklist_item("Include Plumbing Permit in the preliminary permit matrix", "New or modified plumbing was identified."))
    if facts["site_work"] == "yes":
        routes.append(checklist_item("Include Civil review / Site-Work Permit in the preliminary permit matrix", "Exterior or site work was identified."))
    if facts["right_of_way"] == "yes":
        routes.append(checklist_item("Include Right-of-Way Permit in the preliminary permit matrix", "Work in the public right-of-way was identified."))
    if facts["interior_work"] == "yes" or facts["occupancy_change"] == "yes":
        conversations.append({"group": "Building Inspection Division / Permit Coordinator", "why": "Confirm the preliminary building and trade-permit route.", "bring": "Your scope of work and any available plans or existing-condition information."})
    if facts["site_work"] == "yes" or facts["right_of_way"] == "yes":
        conversations.append({"group": "Development Services (site and civil review)", "why": "Confirm site, access, drainage, utility, and right-of-way review needs.", "bring": "A site sketch and known access or utility changes."})
    if facts["specialized_review"] == "yes":
        reviews.append(checklist_item("Confirm specialized mechanical, electrical, fire-prevention, or related review with City staff", "Specialized work was identified and its exact review path needs City confirmation.", "City confirmation"))
        conversations.append({"group": "Fire Prevention / applicable specialty review", "why": "Confirm life-safety or specialty-review requirements before submission.", "bring": "The proposed specialty-work scope and any equipment details."})
    uncertain = [key.replace("_", " ") for key, value in facts.items() if value in {"unknown", "not_sure"}]
    if uncertain:
        notes.append("City staff should confirm remaining scope details: " + ", ".join(uncertain) + ".")
    preparation = [
        checklist_item("Prepare a concise project description and intended use", "A clear scope supports the preliminary City-routing conversation.", "Project preparation"),
        checklist_item("Collect available site, suite, or existing-condition information", "Existing conditions and location details help City staff confirm the route.", "Project preparation"),
    ]
    groups = []
    if routes:
        groups.append({"outcome": "preliminary-route", "title": "Potential permit routes", "description": "These are preliminary routing flags, subject to City confirmation.", "items": routes})
    if reviews:
        groups.append({"outcome": "review-flag", "title": "City confirmation needed", "description": "These conditions need confirmation with the relevant City team.", "items": reviews})
    groups.append({"outcome": "preparation", "title": "Prepare for the conversation", "description": "Bring these basics to make the City conversation more productive.", "items": preparation})
    return {"project_summary": "A preliminary route based on the project details shared in this conversation.",
            "likely_route": "Review the preliminary flags with City staff before submitting through JaxEPICS.",
            "city_conversations": conversations or [{"group": "Building Inspection Division / Permit Coordinator", "why": "Confirm the preliminary route for the described project.", "bring": "A concise project description and any available location or existing-condition information."}],
            "checklist_groups": groups, "notes": notes}

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jax Project Advisor</title><style>
:root{--ink:#102d45;--blue:#0878ae;--pale:#eaf5f8;--line:#cbd9e1;--muted:#526671}*{box-sizing:border-box}body{margin:0;background:#f5f8fa;color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:940px;margin:auto;padding:32px 20px 70px}header{padding:12px 0 26px}.eyebrow{font-size:.76rem;font-weight:800;color:var(--blue);letter-spacing:.11em}h1{font-size:clamp(2rem,5vw,3.3rem);margin:5px 0}header p{color:var(--muted);max-width:680px}.notice{background:#fff7df;border:1px solid #edcf80;padding:12px 16px;border-radius:10px;margin:0 0 20px}.chat{display:grid;gap:16px}.bubble{max-width:800px;padding:16px 18px;border-radius:14px;background:white;border:1px solid var(--line);white-space:pre-wrap}.bubble.user{margin-left:auto;background:var(--ink);color:#fff;border:0}.bubble.assistant{border-left:5px solid var(--blue)}.composer{position:sticky;bottom:10px;background:white;border:1px solid var(--line);border-radius:14px;padding:12px;box-shadow:0 6px 25px #17364d18}.composer textarea{border:0;outline:0;resize:vertical;min-height:76px;width:100%;font:inherit;color:var(--ink)}.controls{display:flex;justify-content:space-between;align-items:center;gap:12px;color:var(--muted);font-size:.85rem}button{border:0;border-radius:8px;background:var(--blue);color:white;padding:10px 16px;font:inherit;font-weight:700;cursor:pointer}button:disabled{opacity:.6;cursor:wait}.brief{margin-top:8px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px}.brief h2{margin:0 0 8px}.brief h3{font-size:1rem;margin:22px 0 6px}.route{background:var(--pale);border-radius:9px;padding:12px}.cards{display:grid;gap:9px}.card{border:1px solid var(--line);border-radius:9px;padding:11px 13px}.card strong{display:block}.tag{font-size:.72rem;font-weight:800;text-transform:uppercase;color:var(--blue)}.check-group{margin-top:19px;border:1px solid var(--line);border-radius:11px;overflow:hidden}.check-group h3{margin:0;padding:14px 16px 2px}.check-group>p{margin:0;padding:0 16px 13px;color:var(--muted);font-size:.9rem}.check-group.preliminary-route h3{border-left:5px solid #1782b6}.check-group.review-flag h3{border-left:5px solid #c17a00}.check-group.preparation h3{border-left:5px solid #39814c}.check-group.specialized h3{border-left:5px solid #7748a7}.check-item{display:flex;gap:12px;padding:13px 16px;border-top:1px solid var(--line);cursor:pointer}.check-item input{width:18px;height:18px;margin-top:3px;accent-color:var(--blue)}.check-item strong{display:block}.why,.source{display:block;font-size:.88rem;margin-top:3px;color:var(--muted)}.source{font-size:.78rem}.check-item input:checked+span strong{text-decoration:line-through;opacity:.7}ul{padding-left:20px;margin:7px 0}.disclaimer{font-size:.84rem;color:var(--muted);margin-top:18px;border-top:1px solid var(--line);padding-top:12px}.error{color:#9f1d1d;background:#fff0f0;border-color:#e9bbbb}@media print{.composer,.notice{display:none}.wrap{max-width:none;padding:0}.bubble{display:none}}
</style></head><body><main class="wrap"><header><div class="eyebrow">CITY OF JACKSONVILLE · PROJECT INTAKE</div><h1>Jax Project Advisor</h1><p>Describe your project in your own words. We’ll ask only the questions needed to prepare a preliminary permit-routing checklist and City coordination plan.</p></header><div class="notice">This is preliminary project-readiness guidance, not a City determination. Final requirements are confirmed by City staff.</div><section class="chat" id="chat" aria-live="polite"></section><form class="composer" id="form"><textarea id="message" required placeholder="Describe your project in a few words…"></textarea><div class="scope-hints" aria-label="Optional project details"><span>Helpful details, if you know them:</span><button type="button" data-detail="The business use is changing.">Business use</button><button type="button" data-detail="I expect interior changes.">Interior changes</button><button type="button" data-detail="I expect exterior or site changes.">Exterior or site changes</button><button type="button" data-detail="I am not sure yet.">Not sure yet</button></div><div class="controls"><span>Do not include sensitive information.</span><button id="send" type="submit">Send</button></div></form></main><script>
const chat=document.querySelector('#chat'),form=document.querySelector('#form'),input=document.querySelector('#message'),send=document.querySelector('#send');let history=[];
function bubble(text,kind='assistant'){const el=document.createElement('div');el.className='bubble '+kind;el.textContent=text;chat.append(el);el.scrollIntoView({behavior:'smooth',block:'end'});return el}
function cityContact(group){const name=(group||'').toLowerCase();if(name.includes('fire'))return{label:'Contact Fire Prevention',email:'JohnsonC@coj.net',phone:'904-255-8560',url:'https://www.jacksonville.gov/departments/public-works/building-inspection-division/fire-and-rescue-plan-review-procedures'};if(name.includes('zoning'))return{label:'Contact Zoning',email:'Zoning@coj.net',phone:'904-255-8300',url:'https://www.jacksonville.gov/departments/public-works/development-services-division/zoning-section'};if(name.includes('building')||name.includes('permit coordinator'))return{label:'Contact Building Inspection',email:'BIDDocuments@coj.net',phone:'904-255-8500',url:'https://www.jacksonville.gov/departments/public-works/building-inspection-division/contact-us'};if(name.includes('right-of-way')||name.includes('development services')||name.includes('site'))return{label:'Contact Development Services',email:'ReviewGrp@coj.net',phone:'904-255-8310',url:'https://www.jacksonville.gov/departments/public-works/development-services-division'};if(name.includes('planning'))return{label:'Contact Current Planning',email:'CurrentPlanning@coj.net',phone:'904-255-7800',url:'https://www.jacksonville.gov/departments/planning-department/current-planning-division'};return null}
function item(tag,title,why,contact){const x=document.createElement('div');x.className='card';const label=document.createElement('span');label.className='tag';label.textContent=tag||'';const heading=document.createElement('strong');heading.textContent=title||'';const detail=document.createElement('span');detail.textContent=why||'';x.append(label,heading,detail);if(contact){const action=document.createElement('a');action.className='contact-action';action.href='mailto:'+contact.email;action.textContent=contact.label+' · '+contact.email+' · '+contact.phone;const source=document.createElement('a');source.className='contact-source';source.href=contact.url;source.target='_blank';source.rel='noopener noreferrer';source.textContent='Official City contact details ↗';x.append(action,source)}return x}
function checklistGroup(group){const section=document.createElement('section');section.className='check-group '+(group.outcome||'');const title=document.createElement('h3');title.textContent=group.title||'Checklist';section.append(title);if(group.description){const desc=document.createElement('p');desc.textContent=group.description;section.append(desc)};(group.items||[]).forEach((row,index)=>{const entry=document.createElement('div');entry.className='check-item';const number=document.createElement('span');number.className='step-number';number.textContent=String(index+1);const copy=document.createElement('span');const heading=document.createElement('strong');heading.textContent=row.item||'';const why=document.createElement('span');why.className='why';why.textContent='Why this appears: '+(row.why||'');const source=document.createElement('span');source.className='source';source.textContent='Source: '+(row.source||'Based on your description and preliminary screening rules');copy.append(heading,why,source);entry.append(number,copy);section.append(entry)});return section}
function conversationRank(group){const name=(group||'').toLowerCase();if(name.includes('certificate of use')||name.includes('cou'))return 0;if(name.includes('zoning'))return 1;if(name.includes('building'))return 2;if(name.includes('fire'))return 3;if(name.includes('development')||name.includes('right-of-way')||name.includes('site'))return 4;return 5}
function outputActions(){const actions=document.createElement('div');actions.className='output-actions';const print=document.createElement('button');print.type='button';print.className='secondary-action';print.textContent='Print or save as PDF';print.addEventListener('click',()=>window.print());const restart=document.createElement('button');restart.type='button';restart.className='secondary-action';restart.textContent='Start another project';restart.addEventListener('click',()=>{history=[];chat.textContent='';form.hidden=false;form.classList.remove('has-context');input.value='';input.placeholder='Describe your project in a few words…';bubble('Hi — tell me about the project you are considering. A simple description is enough to start.');window.scrollTo({top:0,behavior:'smooth'});input.focus()});actions.append(print,restart);return actions}
function brief(data){const b=data.brief;if(!b)return;form.hidden=true;const el=document.createElement('article');el.className='brief';const h=document.createElement('h2');h.textContent='Your preliminary action plan';el.append(h);const summary=document.createElement('p');summary.textContent=b.project_summary;el.append(summary);const route=document.createElement('div');route.className='route';route.textContent=b.likely_route;el.append(route);const ordered=[...(b.city_conversations||[])].sort((a,z)=>conversationRank(a.group)-conversationRank(z.group));if(ordered.length){const first=ordered[0];const firstTitle=document.createElement('h3');firstTitle.textContent='Start here';el.append(firstTitle);const firstStep=item('Recommended first conversation',first.group,first.why+' Bring: '+first.bring,cityContact(first.group));firstStep.classList.add('first-step');el.append(firstStep);if(ordered.length>1){const meetingTitle=document.createElement('h3');meetingTitle.textContent='Then coordinate with';el.append(meetingTitle);const meetings=document.createElement('div');meetings.className='cards';ordered.slice(1).forEach(x=>meetings.append(item('Next conversation',x.group,x.why+' Bring: '+x.bring,cityContact(x.group))));el.append(meetings)}}const checkTitle=document.createElement('h3');checkTitle.textContent='What to prepare';el.append(checkTitle);const groups=[...(b.checklist_groups||[])].sort((a,z)=>conversationRank([a.title,a.description,...(a.items||[]).map(x=>x.item)].join(' '))-conversationRank([z.title,z.description,...(z.items||[]).map(x=>x.item)].join(' ')));groups.forEach(group=>el.append(checklistGroup(group)));if((b.notes||[]).length){const nt=document.createElement('h3');nt.textContent='Important notes';el.append(nt);const ul=document.createElement('ul');b.notes.forEach(x=>{const li=document.createElement('li');li.textContent=x;ul.append(li)});el.append(ul)}const d=document.createElement('p');d.className='disclaimer';d.textContent='Not a City determination. Confirm final routing, documents, fees, and review requirements with the City before submitting through JaxEPICS.';el.append(d);el.append(outputActions());coordinationForm(el,b);chat.append(el);el.scrollIntoView({behavior:'smooth',block:'start'})}
function checklistText(groups){return(groups||[]).flatMap(group=>(group.items||[]).map(row=>'- '+(row.item||'Checklist item')+(row.why?' — '+row.why:''))).join('\n')}
function departmentChecklist(brief,team){const key=(team||'').toLowerCase();const terms=key.includes('fire')?['fire','sprinkler','alarm','hood','cooking']:key.includes('zoning')?['zoning','use','parking','sign','landscape']:key.includes('building')?['building','occupancy','structural','plumbing','mechanical','electrical','hvac']:key.includes('right-of-way')||key.includes('development')?['site','drainage','right-of-way','sidewalk','driveway','utility','access']:[''];const matches=(brief.checklist_groups||[]).filter(group=>{const words=[group.title,group.description,...(group.items||[]).flatMap(row=>[row.item,row.category,row.why])].join(' ').toLowerCase();return terms.some(term=>words.includes(term))});return checklistText(matches.length?matches:brief.checklist_groups)}
function coordinationForm(container,brief){if(!window.TURNSTILE_SITE_KEY)return;const section=document.createElement('section');section.className='brief';section.innerHTML='<h2>Prepare City coordination emails</h2><p>The overall email includes the complete preliminary checklist. Each department email includes the checklist items most relevant to that department.</p><form class="coordination-form"><label>Name<input name="name" required autocomplete="name"></label><label>Email<input name="email" type="email" required autocomplete="email"></label><label>Project address or location<input name="projectAddress" required autocomplete="street-address"></label><label>Message<textarea name="message" required rows="5"></textarea></label><div class="email-actions"></div><div class="turnstile-widget"></div><p class="coordination-notice" aria-live="polite"></p><button type="submit">Prepare overall City email</button></form>';const form=section.querySelector('form'),notice=section.querySelector('.coordination-notice'),widget=section.querySelector('.turnstile-widget'),actions=section.querySelector('.email-actions');form.message.value=(brief.project_summary||'')+'\n\nPreliminary route: '+(brief.likely_route||'');const prepare=async(team,recipient,checklist,button)=>{const token=widgetId!==undefined&&window.turnstile?window.turnstile.getResponse(widgetId):'';if(!token){notice.textContent='Complete the security verification before preparing the email.';return}button.disabled=true;const original=button.textContent;button.textContent='Verifying…';notice.textContent='';try{const response=await fetch('/api/coordination-request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:form.name.value,email:form.email.value,projectAddress:form.projectAddress.value,message:form.message.value,route:brief.likely_route||'Natural-language project intake',team,recipient,checklist,turnstileToken:token})});const result=await response.json();if(!response.ok)throw Error(result.error||'Unable to prepare the City email.');window.location.href=result.mailtoUrl;notice.textContent='Your email application should now open with the request prepared.'}catch(err){notice.textContent=err.message}finally{button.disabled=false;button.textContent=original;if(widgetId!==undefined&&window.turnstile)window.turnstile.reset(widgetId)}};(brief.city_conversations||[]).forEach(group=>{const contact=cityContact(group.group);if(!contact)return;const button=document.createElement('button');button.type='button';button.className='department-email';button.textContent='Prepare email for '+group.group;button.addEventListener('click',()=>prepare(group.group,contact.email,departmentChecklist(brief,group.group),button));actions.append(button)});let widgetId,attempts=0;const render=()=>{if(!window.turnstile){if(++attempts<40){notice.textContent='Loading security verification…';window.setTimeout(render,250);return}notice.textContent='Security verification could not load. Disable content blockers, check your connection, then refresh the page.';return}try{widgetId=window.turnstile.render(widget,{sitekey:window.TURNSTILE_SITE_KEY,theme:'light',callback:()=>{notice.textContent=''},'expired-callback':()=>{notice.textContent='Verification expired. Please complete it again.'},'error-callback':()=>{notice.textContent='Verification could not load. Please refresh and try again.'}})}catch(err){console.error('Turnstile failed to render.',err);notice.textContent='Security verification could not start. Check the Turnstile hostname settings, then refresh the page.'}};render();form.addEventListener('submit',event=>{event.preventDefault();prepare('Overall City coordination','',checklistText(brief.checklist_groups),form.querySelector('button[type="submit"]'))});container.append(section)}
function error(t){const el=bubble(t);el.classList.add('error')}
document.querySelectorAll('[data-detail]').forEach(button=>button.addEventListener('click',()=>{const detail=button.dataset.detail;input.value=input.value.trim()?(input.value.trim()+' '+detail):detail;input.focus()}));
form.addEventListener('submit',async e=>{e.preventDefault();const text=input.value.trim();if(!text)return;bubble(text,'user');history.push({role:'user',text});form.classList.add('has-context');input.placeholder='Reply to the advisor…';input.value='';send.disabled=true;send.textContent='Thinking…';try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({history})});const d=await r.json();if(!r.ok)throw Error(d.error||'The advisor could not respond.');if(d.message)bubble(d.message);if(d.stage==='final')brief(d);history.push({role:'model',text:d.message||''})}catch(err){error(err.message)}finally{send.disabled=false;send.textContent='Send';input.focus()}});bubble('Hi — tell me about the project you are considering. A simple description is enough to start.');
</script></body></html>'''


PAGE_STYLES = r'''<style>
:root{--navy:#092c4c;--navy-deep:#061e35;--blue:#0071bc;--blue-dark:#005a96;--gold:#cf9e37;--ink:#142c3d;--muted:#526674;--focus:#f0b429}html{background:#f1f5f7}body{min-height:100vh;background:linear-gradient(115deg,#e9f0f3 0%,#f6f8f7 48%,#e6eef1 100%);color:var(--ink);font:16px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:.002em}body:before{content:"";position:fixed;inset:0;z-index:-1;opacity:.42;background:repeating-linear-gradient(90deg,transparent 0,transparent 39px,rgba(9,44,76,.018) 40px),linear-gradient(180deg,rgba(255,255,255,.7),transparent 36%)}.wrap{max-width:1080px;padding:36px 24px 92px}header{position:relative;overflow:hidden;min-height:286px;margin:0 0 20px;padding:47px 48px 42px 154px;color:#fff;border:1px solid rgba(255,255,255,.13);border-radius:18px;background:radial-gradient(circle at 92% 12%,rgba(68,151,193,.46),transparent 27%),linear-gradient(130deg,var(--navy-deep),var(--navy) 63%,#06436c);box-shadow:0 18px 42px rgba(9,44,76,.18)}header:before{content:"JAX";display:grid;place-items:center;position:absolute;left:48px;top:49px;width:72px;height:72px;border:1px solid rgba(255,255,255,.7);border-radius:50%;color:#fff;font-family:Georgia,serif;font-size:1rem;font-weight:700;letter-spacing:.12em;box-shadow:inset 0 0 0 7px rgba(255,255,255,.08)}header:after{content:"PRELIMINARY PROJECT READINESS";position:absolute;right:46px;bottom:42px;padding-top:11px;border-top:1px solid rgba(255,255,255,.36);color:#b9dceb;font-size:.66rem;font-weight:800;letter-spacing:.16em}.eyebrow{margin-bottom:9px;color:#9bd4ed;font-size:.72rem;font-weight:800;letter-spacing:.16em}h1,header h1{max-width:620px;margin:0;color:#fff;font-family:Georgia,"Times New Roman",serif;font-size:clamp(2.35rem,5vw,4rem);font-weight:500;line-height:1.03;letter-spacing:-.035em}header p{max-width:660px;margin:18px 0 0;color:#e0edf2;font-size:1.05rem;line-height:1.65}.notice{display:flex;align-items:flex-start;gap:12px;margin:0 0 22px;padding:15px 18px;border:1px solid #d5b66d;border-radius:10px;background:#fffaf0;color:#4f3b12;box-shadow:0 5px 14px rgba(86,68,28,.06)}.notice:before{content:"i";flex:0 0 auto;display:grid;place-items:center;width:20px;height:20px;margin-top:2px;border:1px solid #8c6a22;border-radius:50%;font-family:Georgia,serif;font-weight:700}.chat{gap:18px}.bubble{position:relative;max-width:77%;padding:18px 21px;border:1px solid #d9e2e7;border-radius:4px 16px 16px 16px;background:rgba(255,255,255,.92);box-shadow:0 6px 18px rgba(19,49,68,.055);line-height:1.62}.bubble.assistant{padding-left:25px;border-left:4px solid var(--blue)}.bubble.user{border-radius:16px 4px 16px 16px;background:var(--navy);box-shadow:0 8px 18px rgba(9,44,76,.16)}.composer{z-index:2;margin-top:24px;padding:16px 17px 13px;border:1px solid #b9c9d1;border-radius:14px;background:rgba(255,255,255,.97);box-shadow:0 16px 36px rgba(9,44,76,.16)}.composer:focus-within{border-color:var(--blue);box-shadow:0 0 0 4px rgba(0,113,188,.14),0 16px 36px rgba(9,44,76,.16)}.composer textarea{min-height:104px;padding:4px 2px;background:transparent;color:var(--ink);font-size:1rem;line-height:1.55}.composer textarea::placeholder{color:#71838e}.controls{padding-top:10px;border-top:1px solid #e3eaed}.controls span{font-size:.78rem}.controls span:before{content:"Privacy reminder · ";font-weight:800;color:var(--navy)}button{border:1px solid transparent;border-radius:7px;background:var(--blue);box-shadow:0 2px 0 var(--blue-dark);padding:10px 18px;letter-spacing:.01em;transition:background .18s ease,transform .18s ease,box-shadow .18s ease}button:hover{background:var(--blue-dark)}button:active{transform:translateY(1px);box-shadow:none}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid var(--focus);outline-offset:3px}.brief{margin-top:10px;padding:31px;border:1px solid #d3dfe4;border-radius:14px;background:rgba(255,255,255,.96);box-shadow:0 12px 28px rgba(18,47,66,.08)}.brief h2{margin:0 0 13px;color:var(--navy);font-family:Georgia,"Times New Roman",serif;font-size:1.8rem;font-weight:500;letter-spacing:-.02em}.brief h3{color:var(--navy);font-size:1.05rem}.route{margin:20px 0;padding:16px 18px;border-left:5px solid var(--gold);border-radius:2px 8px 8px 2px;background:#f7f2e6;color:#3c3423;font-weight:600}.cards{grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.card{height:100%;padding:15px;border-radius:8px;border-color:#d7e1e6;background:#fbfcfc}.card strong{margin:3px 0 5px;color:var(--navy)}.tag{color:var(--blue-dark)}.check-group{border-color:#d3dfe4;border-radius:9px}.check-group h3{padding:17px 19px 4px}.check-group>p{padding:0 19px 15px}.check-item{padding:15px 19px;transition:background .14s ease}.check-item:hover{background:#f4f8fa}.check-item input{flex:0 0 auto;accent-color:var(--blue)}.why{color:#405765}.source{color:#647883}.disclaimer{color:#526674}.coordination-form{display:grid;gap:15px;margin-top:20px}.coordination-form label{display:grid;gap:6px;color:var(--navy);font-size:.86rem;font-weight:750}.coordination-form input,.coordination-form textarea{width:100%;padding:11px 12px;border:1px solid #adbec8;border-radius:6px;background:#fff;color:var(--ink);font:inherit}.coordination-form textarea{resize:vertical}.turnstile-widget{min-height:65px}.coordination-notice{min-height:1.5em;margin:0;color:var(--muted);font-size:.9rem}@media (max-width:680px){.wrap{padding:16px 14px 78px}header{min-height:0;padding:31px 24px 75px}header:before{display:none}header:after{left:24px;right:24px;bottom:25px;font-size:.57rem}.eyebrow{font-size:.64rem}header p{font-size:.96rem}.bubble{max-width:92%}.brief{padding:22px 18px}.controls{align-items:flex-end}.controls span{max-width:52%}}@media (prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition:none!important}}@media print{body{background:#fff}.wrap{padding:0}header{min-height:0;padding:25px;background:#fff;color:#000;box-shadow:none;border:1px solid #777}header:before,header:after{display:none}header h1,header p,.eyebrow{color:#000}.brief{box-shadow:none}.coordination-form{display:none}}
</style>'''

CHAT_UI_STYLES = r'''<style>
.chat{position:relative;padding:8px 0 4px}.chat:before{content:"PROJECT CONVERSATION";display:block;margin:0 0 13px 4px;color:#58707e;font-size:.68rem;font-weight:800;letter-spacing:.14em}.bubble{display:grid;gap:8px;max-width:84%;padding:16px 19px 18px}.bubble.assistant:not(.error):before{content:"JAX PROJECT ADVISOR";color:#00649f;font-size:.65rem;font-weight:850;letter-spacing:.12em}.bubble.user:before{content:"YOUR PROJECT DESCRIPTION";color:#b9dceb;font-size:.65rem;font-weight:800;letter-spacing:.1em}.bubble.error:before{content:"NOTICE";font-size:.68rem;font-weight:850;letter-spacing:.1em}.composer{position:sticky;bottom:16px;z-index:4;display:grid;gap:0;max-width:760px;margin:22px auto 0;padding:0;overflow:hidden;border:1px solid #aebfc9;border-radius:18px;background:#fff;box-shadow:0 10px 28px rgba(9,44,76,.16)}.composer:before{content:"DESCRIBE YOUR PROJECT";padding:11px 16px 0;color:#58707e;font-size:.65rem;font-weight:850;letter-spacing:.12em}.composer.has-context:before{content:"REPLY TO THE ADVISOR"}.composer textarea{min-height:58px;max-height:120px;padding:8px 16px 9px;border:0;outline:0;font-size:.96rem;line-height:1.45}.scope-hints{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:0 16px 10px;color:#647883;font-size:.74rem}.scope-hints span{width:100%;font-weight:700}.scope-hints button{padding:4px 8px;border:1px solid #c4d5de;border-radius:999px;background:#fff;color:#28546c;box-shadow:none;font-size:.72rem;font-weight:650}.scope-hints button:hover{border-color:var(--blue);background:#eaf5f8}.composer.has-context .scope-hints{display:none}.controls{min-height:46px;justify-content:flex-end;padding:6px 7px 6px 14px;background:#f5f8f9;border-top:1px solid #e1e8eb}.controls span{margin-right:auto;align-self:center;font-size:.72rem}.controls button{display:grid;place-items:center;min-width:78px;min-height:32px;border-radius:11px;font-size:.86rem}.controls button:after{content:" →";font-size:1.08em}.controls button:disabled:after{content:""}.output-actions{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0 0}.secondary-action{border:1px solid #9ab3c1;background:#fff;color:var(--navy);box-shadow:none}.secondary-action:hover{border-color:var(--blue);background:#edf6fa}.first-step{align-self:start;height:auto;max-width:540px;padding:14px 16px;border:2px solid #197caf;background:#f0f8fb;box-shadow:none}.first-step .contact-action{margin-top:7px}.first-step .contact-source{display:none}.step-number{display:grid;place-items:center;flex:0 0 auto;width:25px;height:25px;margin-top:2px;border-radius:50%;background:#e4f2f8;color:#00649f;font-size:.79rem;font-weight:850}.check-item{cursor:default}.check-item:hover{background:#f7fafb}@media (max-width:680px){.chat:before{margin-left:2px}.bubble{max-width:94%}.composer{bottom:9px;margin-top:16px}.composer textarea{min-height:54px}.controls span{max-width:58%;font-size:.67rem}.controls button{min-width:72px;padding:8px 10px}.scope-hints{padding-bottom:10px}.secondary-action{width:100%}.first-step{max-width:none}}@media print{.output-actions{display:none}.check-item{break-inside:avoid}}
</style>'''

FORM_UI_STYLES = r'''<style>
/* Keep the response area calm and readable: the model supplies the next question in-context. */
.composer{border-radius:14px}.composer textarea{min-height:72px;padding-top:11px}.composer textarea:focus{background:#fbfdfe}.scope-hints{padding-top:2px}.scope-hints span{color:#486272}.scope-hints button{min-height:30px;padding:5px 10px}.scope-hints button:focus-visible{outline-offset:1px}.controls{min-height:52px}.controls button{min-width:96px;min-height:38px}.controls button[disabled]{cursor:progress}.composer.has-context textarea{min-height:66px}.coordination-form{max-width:760px;padding:20px;border:1px solid #d9e4e8;border-radius:12px;background:#f8fbfc}.coordination-form label{gap:7px}.coordination-form input,.coordination-form textarea{border-radius:8px;transition:border-color .16s ease,box-shadow .16s ease}.coordination-form input:focus,.coordination-form textarea:focus{border-color:var(--blue);outline:0;box-shadow:0 0 0 3px rgba(0,113,188,.13)}.email-actions{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:9px}.department-email{min-height:44px;text-align:left;background:#fff;color:var(--blue-dark);border-color:#9bbac9;box-shadow:none}.department-email:hover{background:#edf7fb}.turnstile-widget{padding:8px 0}.coordination-notice{padding:2px 0}.coordination-form>button[type="submit"]{justify-self:start;min-height:44px}@media (max-width:680px){.composer textarea{min-height:68px}.controls{min-height:50px}.controls button{min-width:82px}.coordination-form{padding:16px}.email-actions{grid-template-columns:1fr}.coordination-form>button[type="submit"]{width:100%}}
</style>'''


def page_html() -> str:
    """Add the production presentation layer and optional Turnstile configuration."""
    turnstile = ""
    if TURNSTILE_SITE_KEY:
        turnstile = (
            '<script>window.TURNSTILE_SITE_KEY="'
            + escape(TURNSTILE_SITE_KEY, quote=True)
            + '";</script><script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" defer></script>'
        )
    page = HTML.replace(
        "</head>",
        PAGE_STYLES
        + CHAT_UI_STYLES
        + FORM_UI_STYLES
        + '<style>.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.contact-action,.contact-source{display:block;width:max-content;max-width:100%;margin-top:10px;color:var(--blue-dark);font-size:.84rem;font-weight:750;overflow-wrap:anywhere}.contact-source{margin-top:5px;font-size:.78rem;font-weight:650}.contact-action:hover,.contact-source:hover{color:var(--blue);text-decoration-thickness:2px}</style>'
        + turnstile
        + "</head>",
    )
    return page.replace(
        '<form class="composer" id="form">',
        '<form class="composer" id="form"><label class="sr-only" for="message">Describe your project</label>',
    )


def openai_chat(history: list[dict]) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("The server is missing OPENAI_API_KEY. Add it to the environment, then restart the server.")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-MAX_HISTORY_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = "assistant" if turn.get("role") == "model" else "user"
        text = turn.get("text", "")
        if not isinstance(text, str):
            continue
        text = text.strip()[:MAX_TURN_CHARS]
        if text:
            messages.append({"role": role, "content": text})
    payload = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    url = f"{OPENAI_BASE_URL}/chat/completions"
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT_SECONDS) as response:
            raw = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        logger.warning("AI provider returned HTTP %s", exc.code)
        raise ProviderError("The advisor is temporarily unavailable. Please try again shortly.") from exc
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        logger.warning("AI provider could not be reached: %s", type(exc).__name__)
        raise ProviderError("The advisor is temporarily unavailable. Please try again shortly.") from exc
    try:
        text = raw["choices"][0]["message"]["content"]
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The AI provider returned an unreadable response. Please try again.") from exc
    if not isinstance(result.get("message"), str):
        raise RuntimeError("The AI provider returned an unexpected response. Please try again.")
    facts = normalize_facts(result.get("facts"))
    message = result["message"].strip()[:600]
    if not message:
        raise RuntimeError("The AI provider returned an empty response. Please try again.")
    ready_for_brief = result.get("ready_for_brief") is True
    return {
        "stage": "final" if ready_for_brief else "follow_up",
        "message": message,
        "facts": facts,
        "brief": build_brief(facts) if ready_for_brief else None,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        add_security_headers(self)
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path != "/": self.send_error(404); return
        body = page_html().encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); add_security_headers(self); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/chat": self.send_error(404); return
        try:
            if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                raise ValueError("Content-Type must be application/json")
            size = int(self.headers.get("Content-Length", "0"))
            if not 2 <= size <= MAX_REQUEST_BYTES: raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size).decode())
            history = data.get("history")
            if not isinstance(history, list) or not history: raise ValueError("A project description is required")
            self.send_json(200, openai_chat(history))
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except ProviderError as exc:
            self.send_json(502, {"error": str(exc)})
        except Exception:
            # Keep the browser response safe, but make the full traceback available
            # in the local server console and in the deployment's function logs.
            logger.exception("Unexpected error while processing /api/chat")
            self.send_json(500, {"error": "Unexpected server error."})


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(f"Jax Project Advisor running at http://{HOST}:{PORT}")
    print("Server errors will include a traceback in this console; request content is not logged.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
