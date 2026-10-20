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
let currentProgress = null;
let pending = false;
let previousSteps = new Map();
const workspace = document.querySelector('.workspace');
const topicLabels = ['Your project', 'Project location', 'Existing & proposed use', 'Building & plumbing', 'Fire & specialty work', 'Site & parking'];
const statusLabels = {covered:'Covered', excluded:'Not relevant to reported scope', active:'Discussing now', uncertain:'Needs clarification', partial:'Details started', pending:'Not yet covered', deferred:'To confirm with City'};

function setView(view) {
  workspace.dataset.view = view;
  document.querySelectorAll('.mobile-switch button').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.view === view));
  });
}
document.querySelectorAll('.mobile-switch button').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelector('#restart').addEventListener('click', resetProject);

function renderWorkspace(result = {}) {
  const historicResult = document.querySelector('#historic-result');
  const historicExpanded = historicResult.querySelector('details')?.open || false;
  const progress = result.progress || {covered:0, total:6, topics:topicLabels.map((title, index) => ({id:String(index), title, status:'pending'}))};
  currentProgress = progress;
  const deferred = progress.topics.filter(topic => topic.status === 'deferred');
  document.querySelector('#intake-state').textContent = result.stage === 'final' ? (deferred.length ? `Preliminary plan ready · ${deferred.length} ${deferred.length === 1 ? 'topic' : 'topics'} still to confirm` : 'Preliminary plan ready') : '';
  const label = `${progress.covered} of ${progress.total} topics covered`;
  document.querySelector('#progress-label').textContent = label;
  document.querySelector('#mobile-progress').textContent = `${progress.covered}/${progress.total} covered`;
  const meter = document.querySelector('#intake-progress');
  meter.max = progress.total; meter.value = progress.covered; meter.setAttribute('aria-valuetext', label);
  const topics = document.querySelector('#topic-list'); topics.replaceChildren();
  progress.topics.forEach(topic => {
    const row = node('li', '', 'topic ' + topic.status);
    row.dataset.topic = topic.id;
    if (topic.status === 'active') row.setAttribute('aria-current', 'step');
    const marker = node('span', topic.status === 'covered' ? '✓' : topic.status === 'excluded' ? '−' : ['uncertain', 'deferred'].includes(topic.status) ? '?' : '', 'topic-marker');
    marker.setAttribute('aria-hidden', 'true');
    const copy = node('div'); copy.append(node('strong', topic.title), node('span', topic.status_label || statusLabels[topic.status], 'topic-status'));
    if (topic.reason) copy.append(node('p', topic.reason, 'topic-reason'));
    if (topic.id === 'historic') copy.append(historicResult);
    row.append(marker, copy); topics.append(row);
  });
  if (!progress.topics.some(topic => topic.id === 'historic')) topics.after(historicResult);
  const fields = currentProject?.fields;
  historicResult.replaceChildren();
  const historic = historicCard(historicExpanded); if (historic && progress.topics.some(topic => topic.id === 'historic')) historicResult.append(historic);
  const location = fields?.location?.value;
  const description = fields?.project_description?.value;
  document.querySelector('#panel-summary').textContent = [description, location].filter(value => value && value !== 'unknown' && value !== 'not_sure').join(' · ') || 'A little context. A clearer path forward.';
  const list = document.querySelector('#live-checklist');
  const openIds = new Set([...list.querySelectorAll('details[open]')].map(detail => detail.dataset.step));
  list.replaceChildren();
  const steps = (result.live_plan?.action_plan || []).filter(step => step.status !== 'not_applicable' && (step.evidence.length || result.stage === 'final'));
  const nextSteps = new Map();
  let changed = 0;
  steps.forEach(step => {
    const fingerprint = JSON.stringify([step.status, step.evidence, step.exception_note, step.prerequisites, step.fees]);
    nextSteps.set(step.id, fingerprint);
    const updated = previousSteps.has(step.id) && previousSteps.get(step.id) !== fingerprint;
    const added = previousSteps.size > 0 && !previousSteps.has(step.id);
    if (updated || added) changed++;
    const detail = node('details', '', 'live-step' + (updated || added ? ' changed' : ''));
    detail.dataset.step = step.id; detail.open = openIds.has(step.id);
    const summary = node('summary'); summary.append(node('span', checklistTitle(step)));
    const meta = node('span', '', 'live-step-meta');
    meta.append(node('span', 'Confirm with the City', 'live-step-status'));
    if (updated || added) meta.append(node('span', updated ? 'Updated' : 'Added', 'update-tag'));
    summary.append(meta); detail.append(summary, node('p', step.department, 'live-department'));
    detail.append(node('p', step.request));
    if (step.evidence.length) detail.append(node('p', (step.evidence_label || 'You shared') + ': ' + step.evidence.join(' / ')));
    if (step.exception_note) detail.append(node('p', step.exception_note));
    if (step.prerequisites.length) detail.append(node('p', 'First: ' + step.prerequisites.map(id => checklistTitle(result.live_plan.action_plan.find(item => item.id === id) || {id,title:id})).join('; ')));
    detail.append(link(step.source_title, step.source_url)); list.append(detail);
  });
  if (!steps.length) list.append(node('p', result.live_plan ? 'No active actions flagged by the scope shared so far. Unresolved topics may still change your plan.' : 'Your first details will start shaping the checklist here.', 'empty-plan'));
  const removed = [...previousSteps.keys()].filter(id => !nextSteps.has(id)).length;
  document.querySelector('#plan-update').textContent = result.stage === 'final' && deferred.length ? 'Your plan includes open questions for City confirmation. Add details in chat to refine it.' : changed || removed ? 'Checklist updated to reflect your latest details.' : result.stage === 'final' ? 'Your preliminary plan is ready. You can still add or correct details.' : '';
  previousSteps = nextSteps;
}

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
  if (group.startsWith('Historic')) return {email:'HistoricPreservation@coj.net',url:'https://www.jacksonville.gov/departments/planning-department/community-planning-division/historic-preservation-section'};
  if (group.startsWith('Concurrency')) return {email:'CMMSO@coj.net',url:'https://www.jacksonville.gov/departments/planning-department/transportation-planning/concurrency-and-mobility-management-system-office'};
  if (group.startsWith('Zoning')) return {email:'Zoning@coj.net', url:'https://www.jacksonville.gov/departments/public-works/development-services-division/zoning-section'};
  if (group.startsWith('Development')) return {email:'ReviewGrp@coj.net', url:'https://www.jacksonville.gov/departments/public-works/development-services-division'};
  return {email:'BIDDocuments@coj.net', url:'https://www.jacksonville.gov/departments/public-works/building-inspection-division/contact-us'};
}
function link(text, url) {
  const el = node('a', text, 'contact-source');
  // URLs are from the server rule base, never markup from the model.
  if (url.startsWith('https://www.jacksonville.gov/') || url.startsWith('https://maps.coj.net/landdevelopmentreview/') || url.startsWith('https://paopropertysearch.coj.net/Basic/Detail.aspx?RE=')) {
    el.href = url; el.target = '_blank'; el.rel = 'noopener noreferrer';
  }
  return el;
}
function checklistTitle(step) {
  const titles = {use:'Zoning & Certificate of Use (COU)', occupancy:'Occupancy & converting-use review',
    building:'Building permit', plumbing:'Plumbing permit', site:'Site development & parking',
    row:'Right-of-way & access', specialty:'Specialty & agency review', historic:'Historic preservation',
    archaeology:'Archaeological review', mobility:'Mobility review & fees'};
  const title = step.title.replace(/^(?:Resolve whether to )?(?:confirm )?/i, '');
  return titles[step.id] || title.charAt(0).toUpperCase() + title.slice(1);
}
function historicCard(expanded = false) {
  const finding = currentProject?.research?.historic;
  if (!finding || finding.query !== currentProject?.fields?.location?.value) return null;
  const card = node('section', '', 'historic-card');
  card.append(node('h3', 'Historic & archaeological checks'));
  const historicFlag = finding.historic_flag || finding.local_status === 'designated' || finding.screening_checks?.some(check => check.status === 'identified') || finding.archaeology?.levels?.some(level => level.toLowerCase() === 'high');
  card.append(node('strong', historicFlag ? 'Historic review: Flagged' : finding.historic_status === 'not_identified' ? 'Historic review: No flags identified' : 'Historic review: Check details'));
  card.append(node('strong', {designated:'Local historic designation identified', not_identified:'No local historic designation identified'}[finding.local_status] || 'Historic status needs confirmation'));
  const archaeology = finding.archaeology;
  if (archaeology?.levels?.some(level => level.toLowerCase() === 'high')) card.append(node('strong', 'Archaeological sensitivity: High'));
  else if (archaeology?.status === 'requires_review') card.append(node('strong', 'Archaeological sensitivity check incomplete'));
  card.append(node('p', finding.reason));
  if (finding.screening_checks?.length) {
    const details = node('details'); details.open = expanded;
    details.append(node('summary', 'View screening checklist'));
    const list = node('ul', '', 'historic-screening-list');
    finding.screening_checks.forEach(check => {
      const row = node('li', '', 'screening-' + check.status);
      row.append(node('span', check.title), node('strong', check.status === 'identified' ? (check.complete === false ? 'Found; partial check' : 'Found') : check.status === 'not_identified' ? 'Not identified' : 'Check incomplete'));
      list.append(row);
    });
    details.append(list); card.append(details);
  }
  const matches = [...new Set((finding.parcels || []).flatMap(parcel => parcel.checks.flatMap(check => (check.matches || []).map(match => match.LANDMARK_N || match.RESNAME || match.SITENAME || match.NAME || '').filter(Boolean))))];
  if (matches.length) card.append(node('p', 'Records: ' + matches.join('; ')));
  if (finding.parcels?.length) card.append(node('p', 'Parcel(s): ' + finding.parcels.map(parcel => parcel.parcel).join(', ')));
  card.append(node('p', 'Checked ' + new Date(finding.checked_at).toLocaleString(), 'source'), link('View the City historic map', finding.source_url));
  return card;
}
function renderBrief(brief) {
  chat.querySelector('.brief')?.remove();
  currentBrief = brief;
  const article = node('article', '', 'brief');
  article.append(node('h2', 'Your preliminary coordination plan'), node('p', brief.project_summary, 'project-summary'));
  const openTopics = currentProgress?.topics.filter(topic => topic.status === 'deferred') || [];
  if (openTopics.length) {
    const section = node('section', '', 'open-topics');
    section.append(node('h3', 'Still to confirm'), node('p', 'This preliminary plan uses the details you shared. These topics remain open; add details in chat or confirm them with City staff.'));
    const list = node('ul');
    openTopics.forEach(topic => {
      const item = node('li'); item.append(node('strong', topic.title + ': '), document.createTextNode(topic.reason)); list.append(item);
    });
    section.append(list); article.append(section);
  }
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
    const title = checklistTitle(step);
    const confirmation = node('span', 'Confirm with the City', 'status-pill ' + step.status);
    confirmation.title = step.status === 'unresolved' ? 'Applicability remains unresolved; confirm with City staff.' : 'Preliminary route based on reported scope; confirm with City staff.';
    heading.append(node('strong', title), confirmation);
    copy.append(heading, node('p', step.department, 'step-department'));
    if (step.prerequisites.length) {
      const titles = step.prerequisites.map(id => checklistTitle(brief.action_plan.find(s => s.id === id) || {id,title:id}));
      copy.append(node('p', 'First: ' + titles.join('; '), 'dependency'));
    }
    const details = node('details');
    details.append(node('summary', 'What to confirm and why'));
    details.append(node('p', 'Needed outcome: ' + step.outcome));
    if (step.exception_note) details.append(node('p', step.exception_note));
    details.append(node('p', step.evidence.length ? (step.evidence_label || 'You shared') + ': ' + step.evidence.join(' / ') : 'This detail has not been established.'));
    details.append(node('p', step.guidance), node('p', step.exceptions), link(step.source_title, step.source_url));
    (step.fees || []).forEach(fee => details.append(node('p',fee.label + ': ' + fee.amount + '. ' + fee.qualification),link('City fee schedule',fee.source_url)));
    copy.append(details);
    if (step.id === 'historic') {
      const historic = historicCard(true);
      if (historic) {const siteDetails = node('details', '', 'historic-site-details'); siteDetails.append(node('summary', 'Historic site details'), historic); copy.append(siteDetails);}
    }
    row.append(number, copy); checklist.append(row);
  });
  article.append(checklist);
  if (!steps.some(step => step.id === 'historic')) {const historic = historicCard(true); if (historic) article.append(historic);}
  const omitted = brief.action_plan.filter(step => step.status === 'not_applicable');
  if (omitted.length) {
    const details = node('details'); details.append(node('summary', 'Scope reported as not included'));
    omitted.forEach(step => details.append(node('p', checklistTitle(step) + ': ' + (step.exception_note || step.evidence.join(' / ')))));
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
  previousSteps = new Map(); renderWorkspace(); setView('chat');
  if (persistentMode) sessionStorage.removeItem('jax-project-id');
  chat.textContent=''; form.classList.remove('has-context'); input.value=''; input.placeholder='Tell me what you are planning…';
  bubble('Hi — tell me about the project you are considering. A simple description is enough to start.'); input.focus();
}
form.onsubmit=async event=>{
  event.preventDefault(); const message=input.value.trim(); if(!message || pending)return;
  pending=true; send.disabled=true; input.disabled=true; send.textContent='Thinking…';
  document.querySelector('#restart').disabled=true;
  chat.setAttribute('aria-busy', 'true');
  const user=bubble(message,'user'); const loading=bubble('Thinking through your project…'); loading.classList.add('pending');
  loading.scrollIntoView({behavior:'smooth', block:'nearest'});
  try {
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...(persistentMode?{projectId}:{state:demoState}),message})});
    const result=await response.json(); if(!response.ok)throw Error(result.error || 'The advisor could not respond.');
    loading.remove();
    if (persistentMode) {projectId=result.projectId; sessionStorage.setItem('jax-project-id',projectId);}
    else demoState=result.state || '';
    currentProject=result.project;
    renderWorkspace(result);
    input.value=''; form.classList.add('has-context'); input.placeholder='Reply, add details, or correct the project…';
    bubble(result.message, 'assistant', result.citations || []); chat.querySelector('.brief')?.remove(); currentBrief=null;
    if(result.brief)renderBrief(result.brief);
    user.scrollIntoView({behavior:'smooth',block:'start'});
  }catch(err){loading.remove();user.remove();bubble(err.message + ' Your reply is still below so you can retry.','error');}
  finally{pending=false;send.disabled=false;input.disabled=false;send.textContent='Send';document.querySelector('#restart').disabled=false;chat.setAttribute('aria-busy','false');input.focus({preventScroll:true});}
};
input.maxLength=6000;
input.addEventListener('keydown',event=>{if((event.ctrlKey || event.metaKey) && event.key==='Enter'){event.preventDefault();form.requestSubmit();}});
async function resumeProject() {
  if (!persistentMode) { resetProject(); return; }
  if(!projectId){resetProject();return;}
  pending=true;send.disabled=true;input.disabled=true;document.querySelector('#restart').disabled=true;
  try {
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({projectId,resume:true})});
    const result=await response.json();if(!response.ok)throw Error(result.error || 'Unable to restore this project.');
    currentProject=result.project;result.history.forEach(turn=>bubble(turn.text,turn.role==='user'?'user':'assistant',turn.citations || []));
    renderWorkspace(result);
    form.classList.add('has-context');input.placeholder='Reply, add details, or correct the project…';if(result.brief)renderBrief(result.brief);
  }catch(err){projectId='';sessionStorage.removeItem('jax-project-id');bubble(err.message,'error');}
  finally{pending=false;send.disabled=false;input.disabled=false;document.querySelector('#restart').disabled=false;}
}
renderWorkspace();
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
