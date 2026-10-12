const chat = document.querySelector('#chat');
const form = document.querySelector('#form');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const persistentMode = window.PROJECT_STORAGE_MODE === 'persistent';
let projectId = persistentMode ? sessionStorage.getItem('jax-project-id') || '' : '';
let demoState = '';
if (!persistentMode) sessionStorage.removeItem('jax-project-id');
let currentBrief = null;
let currentProject = null;
let pending = false;

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function bubble(text, kind = 'assistant', citations = []) {
  const el = node('div', text, 'bubble ' + kind);
  if (citations.length) {
    const sources = node('div', '', 'message-sources');
    citations.forEach(source => sources.append(link(source.title, source.url)));
    el.append(sources);
  }
  chat.append(el);
  return el;
}
function contact(group) {
  if (group.startsWith('Concurrency')) return {email:'CMMSO@coj.net',url:'https://www.jacksonville.gov/departments/planning-department/transportation-planning/concurrency-and-mobility-management-system-office'};
  if (group.startsWith('Zoning')) return {email:'Zoning@coj.net', url:'https://www.jacksonville.gov/departments/public-works/development-services-division/zoning-section'};
  if (group.startsWith('Development')) return {email:'ReviewGrp@coj.net', url:'https://www.jacksonville.gov/departments/public-works/development-services-division'};
  return {email:'BIDDocuments@coj.net', url:'https://www.jacksonville.gov/departments/public-works/building-inspection-division/contact-us'};
}
function link(text, url) {
  const el = node('a', text, 'contact-source');
  // URLs are from the server rule base, never markup from the model.
  if (url.startsWith('https://www.jacksonville.gov/') || url.startsWith('https://paopropertysearch.coj.net/Basic/Detail.aspx?RE=')) {
    el.href = url; el.target = '_blank'; el.rel = 'noopener noreferrer';
  }
  return el;
}
function renderBrief(brief) {
  chat.querySelector('.brief')?.remove();
  currentBrief = brief;
  const article = node('article', '', 'brief');
  article.append(node('h2', 'Your preliminary coordination plan'), node('p', brief.project_summary, 'project-summary'));
  const steps = brief.action_plan.filter(step => step.status !== 'not_applicable');
  const ready = steps.filter(step => step.prerequisites.length === 0);
  article.append(node('h3', ready.length > 1 ? 'Conversations you can start now' : 'Start here'));
  if (!steps.length) article.append(node('p', 'No routes were flagged by the scope you shared. This does not establish that no permit is needed; confirm the described scope with City staff.'));
  const conversations = node('div', '', 'conversation-cards');
  for (const group of brief.city_conversations) {
    const relevant = ready.filter(step => group.step_ids.includes(step.id));
    if (!relevant.length) continue;
    const card = node('div', '', 'card first-step');
    card.append(node('strong', group.group), node('p', relevant[0].request));
    const c = contact(group.group);
    card.append(link('Official department information', c.url));
    conversations.append(card);
  }
  article.append(conversations);
  const checklistHeading = node('div', '', 'checklist-heading');
  checklistHeading.append(node('h3', 'Project checklist'), node('span', steps.length + ' actions to coordinate', 'checklist-count'));
  article.append(checklistHeading);
  const checklist = node('ol', '', 'project-checklist');
  steps.forEach((step, index) => {
    const row = node('li', '', 'plan-step');
    const number = node('span', String(index + 1), 'step-number'); number.setAttribute('aria-hidden', 'true');
    const copy = node('div', '', 'step-copy');
    const heading = node('div', '', 'step-heading');
    const title = step.title.replace(/^Resolve whether to confirm /, 'Confirm ');
    heading.append(node('strong', title), node('span', step.status === 'unresolved' ? 'Needs confirmation' : 'Preliminary route', 'status-pill ' + step.status));
    copy.append(heading, node('p', step.department, 'step-department'));
    if (step.prerequisites.length) {
      const titles = step.prerequisites.map(id => brief.action_plan.find(s => s.id === id)?.title.replace(/^Resolve whether to confirm /, 'Confirm ') || id);
      copy.append(node('p', 'First: ' + titles.join('; '), 'dependency'));
    }
    const details = node('details');
    details.append(node('summary', 'What to confirm and why'));
    details.append(node('p', 'Needed outcome: ' + step.outcome));
    if (step.exception_note) details.append(node('p', step.exception_note));
    details.append(node('p', step.evidence.length ? 'You shared: ' + step.evidence.join(' / ') : 'This detail has not been established.'));
    details.append(node('p', step.guidance), node('p', step.exceptions), link(step.source_title, step.source_url));
    (step.fees || []).forEach(fee => details.append(node('p',fee.label + ': ' + fee.amount + '. ' + fee.qualification),link('City fee schedule',fee.source_url)));
    copy.append(details); row.append(number, copy); checklist.append(row);
  });
  article.append(checklist);
  const omitted = brief.action_plan.filter(step => step.status === 'not_applicable');
  if (omitted.length) {
    const details = node('details'); details.append(node('summary', 'Scope reported as not included'));
    omitted.forEach(step => details.append(node('p', step.title + ': ' + (step.exception_note || step.evidence.join(' / ')))));
    article.append(details);
  }
  const corrections = currentProject?.corrections || [];
  if (corrections.length) {
    const details = node('details'); details.append(node('summary', 'Updated project details'));
    corrections.forEach(change => details.append(node('p', change.field.replaceAll('_', ' ') + ': ' + change.previous + ' → ' + change.value)));
    article.append(details);
  }
  const ruleDetails = node('details'); ruleDetails.append(node('summary', 'About this preliminary guidance'));
  ruleDetails.append(node('p', 'Rule version ' + brief.rules.version + ' · ' + brief.rules.review_status, 'source'));
  article.append(ruleDetails);
  article.append(node('p', 'Preliminary guidance only. City staff must confirm the requirements. Add or correct details below to update this plan.', 'disclaimer'));
  const actions = node('div', '', 'output-actions');
  const print = node('button', 'Print or save as PDF', 'secondary-action'); print.type='button'; print.onclick=()=>window.print();
  const restart = node('button', 'Start another project', 'secondary-action'); restart.type='button'; restart.onclick=resetProject;
  actions.append(print,restart); article.append(actions);
  if (steps.length) coordinationForm(article, brief);
  chat.append(article);
}
function coordinationForm(container, brief) {
  const section = node('section', '', 'email-section');
  section.append(node('h3', 'Prepare a short City email'), node('p', 'A brief project description, relevant advisor suggestions and one request for confirmation. Review the draft before opening your email app.'));
  const emailForm = node('form', '', 'coordination-form');
  function field(title, name, type='text', required=false) {
    const label=node('label', title);
    const el=node(type === 'textarea' ? 'textarea' : 'input'); el.name=name;
    if(type !== 'textarea') el.type=type;
    el.required=required;
    if(type === 'textarea') {el.rows=3; el.maxLength=500;}
    label.append(el); emailForm.append(label); return el;
  }
  const name=field('Your name','applicantName','text',true); name.autocomplete='name';
  const email=field('Your email','applicantEmail','email',true); email.autocomplete='email';
  const address=field('Project location (optional)','projectAddress'); address.maxLength=300;
  address.value=currentProject?.fields.location.value === 'unknown' ? '' : currentProject?.fields.location.value || '';
  const label=node('label','Who should review this request?'); const select=node('select');
  for(const group of brief.city_conversations) {const option=node('option',group.group); option.value=group.group; select.append(option);}
  const overall=node('option','Overall City coordination'); overall.value='Overall City coordination'; select.append(overall);
  label.append(select); emailForm.append(label);
  const note=field('Additional context (optional)','note','textarea');
  const preview=node('div','','email-preview');
  const previewStatus=node('p','','coordination-notice'); previewStatus.setAttribute('role','status');
  const widget=node('div','','turnstile-widget');
  const button=node('button','Review email draft'); button.type='submit';
  const open=node('a','Open draft in email app','email-open'); open.hidden=true;
  emailForm.append(widget,previewStatus,preview,button,open); section.append(emailForm); container.append(section);
  let widgetId;
  if(window.TURNSTILE_SITE_KEY) {
    let attempts=0;
    const render=()=>{
      if(!window.turnstile) {if(++attempts<40) return setTimeout(render,250); previewStatus.textContent='Security verification could not load. Refresh to retry.'; return;}
      try {widgetId=window.turnstile.render(widget,{sitekey:window.TURNSTILE_SITE_KEY,theme:'light',
        'expired-callback':()=>{previewStatus.textContent='Verification expired. Please complete it again.';},
        'error-callback':()=>{previewStatus.textContent='Security verification could not load. Refresh to retry.';}});}
      catch {previewStatus.textContent='Security verification could not start. Refresh to retry.';}
    }; render();
  } else {button.disabled=true; previewStatus.textContent='Email preparation is not configured on this deployment.';}
  emailForm.addEventListener('input',()=>{open.hidden=true; preview.textContent='';});
  emailForm.onsubmit=async event=>{
    event.preventDefault();
    if(!emailForm.reportValidity())return;
    const token=widgetId !== undefined && window.turnstile ? window.turnstile.getResponse(widgetId) : '';
    if(!token){previewStatus.textContent='Complete the security verification first.';return;}
    button.disabled=true; button.textContent='Preparing draft…'; open.hidden=true;
    try {
      const response=await fetch('/api/coordination-request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...(persistentMode?{projectId}:{state:demoState}),name:name.value,email:email.value,projectAddress:address.value,note:note.value,team:select.value,turnstileToken:token})});
      const result=await response.json(); if(!response.ok)throw Error(result.error || 'Unable to prepare the email.');
      preview.textContent=''; preview.append(node('strong',result.subject),node('pre',result.body));
      open.href=result.mailtoUrl; open.hidden=false; previewStatus.textContent='Review the draft, then open it in your email app. Nothing has been sent.';
    }catch(err){previewStatus.textContent=err.message;}
    finally{button.disabled=false;button.textContent='Review email draft';if(widgetId !== undefined)window.turnstile.reset(widgetId);}
  };
}
function resetProject() {
  if(pending)return;
  projectId=''; demoState=''; currentProject=null; currentBrief=null;
  if (persistentMode) sessionStorage.removeItem('jax-project-id');
  chat.textContent=''; form.classList.remove('has-context'); input.value=''; input.placeholder='Tell me what you are planning…';
  bubble('Hi — tell me about the project you are considering. A simple description is enough to start.'); input.focus();
}
form.onsubmit=async event=>{
  event.preventDefault(); const message=input.value.trim(); if(!message || pending)return;
  pending=true; send.disabled=true; input.disabled=true; send.textContent='Thinking…';
  const user=bubble(message,'user'); const loading=bubble('Thinking through your project…'); loading.classList.add('pending');
  try {
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...(persistentMode?{projectId}:{state:demoState}),message})});
    const result=await response.json(); if(!response.ok)throw Error(result.error || 'The advisor could not respond.');
    loading.remove();
    if (persistentMode) {projectId=result.projectId; sessionStorage.setItem('jax-project-id',projectId);}
    else demoState=result.state || '';
    currentProject=result.project;
    input.value=''; form.classList.add('has-context'); input.placeholder='Reply, add details, or correct the project…';
    bubble(result.message, 'assistant', result.citations || []); chat.querySelector('.brief')?.remove(); currentBrief=null;
    if(result.brief)renderBrief(result.brief);
    user.scrollIntoView({behavior:'smooth',block:'start'});
  }catch(err){loading.remove();user.remove();bubble(err.message + ' Your reply is still below so you can retry.','error');}
  finally{pending=false;send.disabled=false;input.disabled=false;send.textContent='Send';input.focus();}
};
input.maxLength=6000;
input.addEventListener('keydown',event=>{if((event.ctrlKey || event.metaKey) && event.key==='Enter'){event.preventDefault();form.requestSubmit();}});
async function resumeProject() {
  if (!persistentMode) { resetProject(); return; }
  if(!projectId){resetProject();return;}
  pending=true;send.disabled=true;input.disabled=true;
  try {
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({projectId,resume:true})});
    const result=await response.json();if(!response.ok)throw Error(result.error || 'Unable to restore this project.');
    currentProject=result.project;result.history.forEach(turn=>bubble(turn.text,turn.role==='user'?'user':'assistant',turn.citations || []));
    form.classList.add('has-context');input.placeholder='Reply, add details, or correct the project…';if(result.brief)renderBrief(result.brief);
  }catch(err){projectId='';sessionStorage.removeItem('jax-project-id');bubble(err.message,'error');}
  finally{pending=false;send.disabled=false;input.disabled=false;}
}
resumeProject();
if (!persistentMode) {
  document.querySelector('.controls span').textContent='Demo: this tab only. Refreshing resets the conversation.';
}

let printDetails = [];
window.addEventListener('beforeprint', () => {
  printDetails = [...document.querySelectorAll('.project-checklist details')].filter(detail => !detail.open);
  printDetails.forEach(detail => { detail.open = true; });
});
window.addEventListener('afterprint', () => {
  printDetails.forEach(detail => { detail.open = false; });
  printDetails = [];
});
