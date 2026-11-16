# Jax Project Advisor

An independent Python application that turns a natural-language project conversation into preliminary Jacksonville permit-routing guidance, a coordination plan, and a checklist.

## Run locally

PowerShell:

```powershell
cd C:\city\jax-project-advisor
$env:OPENAI_API_KEY = "your OpenAI or Pen AI API key"
# Optional: choose any model offered by your provider
$env:OPENAI_MODEL = "gpt-4.1-mini"
# Optional: use an OpenAI-compatible provider endpoint instead of api.openai.com/v1
$env:OPENAI_BASE_URL = "https://your-provider.example/v1"
python app.py
```

Alternatively, place these values in an uncommitted `jax-project-advisor/.env` file;
the local server loads it automatically and does not override values already set in PowerShell.

Open http://127.0.0.1:8000. The key stays on the Python server and is never sent to the browser.

## Deploy to Vercel

In the Vercel import screen, select `jax-project-advisor` as the **Root Directory**. No build command or Python dependencies are required. The default demo mode needs no database or Redis integration.

Add these Environment Variables in Vercel before deploying:

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (optional; defaults to `gpt-4.1-mini`)
- `OPENAI_BASE_URL` (optional; defaults to `https://api.openai.com/v1`)
- `PROJECT_STORAGE_MODE` (optional; defaults to `demo`, with no project persistence)
- Only for `PROJECT_STORAGE_MODE=persistent`: `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`, or `KV_REST_API_URL` and `KV_REST_API_TOKEN` (one matching pair required on Vercel)
- `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` (required to enable the protected City coordination-email form)
- `CITY_COORDINATION_EMAIL` (required to enable the protected City coordination-email form)

Vercel routes `/` to the app page, serves `advisor.js` as a static asset, and exposes `/api/chat` and `/api/coordination-request`. The local Python server serves those same paths. The email workflow validates the Turnstile token and returns a short draft for review. The visitor then chooses to open their email app; no email is sent by this application.

### Optional persistent mode: connect Redis and redeploy

Skip this section for the demo. To enable saved project records, set `PROJECT_STORAGE_MODE=persistent` first.

1. Install the Upstash Redis integration from Vercel Marketplace, create or select a database, and connect it to this Vercel project for Production (and Preview if needed).
2. In Project Settings → Environment Variables, confirm that one complete REST URL/token pair is present. Use the read/write REST token, not a read-only token or the `redis://` connection string. Do not commit credentials to Git or paste them into chat.
3. Ensure `OPENAI_API_KEY` and, for a non-OpenAI provider, `OPENAI_BASE_URL` / `OPENAI_MODEL` are also set for Production. The local `.env` file is intentionally not uploaded.
4. Redeploy the latest commit after connecting the database or changing environment variables. No tables or manual data upload are needed; the app creates expiring Redis project keys automatically.
5. Visit `/api/health`. It reports whether model and storage settings are present without exposing their values. `ready: true` means configuration is present, not that upstream credentials have been authenticated; submit a test chat to verify the full flow.

Chat checks configuration before model calls. Missing settings return a specific HTTP 503/code instead of a generic 500. The Upstash names take precedence if supplied, and credentials are never mixed across two store configurations.

## What it does

1. The visitor describes a project in ordinary language.
2. The model receives the current project context and relevant versioned guidance. It asks one useful plain-language question when an answer could change routing, and returns field updates with exact applicant quotes.
3. The server preserves scope, reported approvals, explicit uncertainty, corrections and conflicts. Silence never deletes an earlier supported detail.
4. One authoritative action plan supplies the start-here conversations, prerequisites, applicable/unresolved/excluded routes, sources and department email requests.
5. Applicants can continue adding or correcting details after seeing a plan. The updated plan replaces the previous one. In demo mode, refreshing resets the project; persistent mode restores it in the same tab.

## Conversation workspace

Desktop uses a project sidebar beside an independently scrolling conversation, with the composer always visible. Mobile offers Chat and Project plan views. After each successful answer, the sidebar updates topic coverage, highlights the current question and shows an expandable developing checklist from the same rule-derived plan used by the final report. Corrections update existing actions; failed requests preserve the last supported plan and the unsent reply.

Coverage describes collected intake details, not permit approval or a fixed question count. The AI chooses relevant scope questions and natural wording from eligible topics; the server validates evidence, resolved topics and construction branches. Conversation state distinguishes unanswered, answered and explicitly deferred topics. Asking a question does not resolve it. Address replies, direct questions and research requests preserve the pending scope question. Research and question turns can return an answer without another intake question; a later scope reply or request to continue resumes the pending topic. Bare yes/no replies after a response-only turn do not automatically answer the suspended question.

One AI response integrates City research with the conversation; the server no longer prepends a separate GIS paragraph. Explicit research requests refresh the historic screening. The existing project record stores this conversation state alongside supported facts, in both demo envelopes and persistent sessions. An explicit request to finish can produce a preliminary plan with unknowns retained for City confirmation. Technical occupancy determinations stay with City staff. Optional historic review appears when relevant, and unknown scope is never counted as excluded. Print output keeps the final report and omits the workspace controls.

Scope replies such as “yes all of them,” “none,” and “grading and parking” resolve the pending decision while preserving narrative details. A narrow follow-up cannot negate previously recorded site work. Historic sessions with missed answers can recover those decisions from saved question metadata or the original scope-question wording. If generated prose fails validation twice, a response built from accepted project facts continues the conversation without dropping the answer. Commercial projects receive an early zoning/COU introduction once when the official COU guidance is available.

## Project storage

During the pilot, **Email pilot feedback** is available in the conversation header and beside the final checklist actions. The recipient defaults to **samay.shetty@haskell.com** and is editable; optional `PILOT_FEEDBACK_EMAIL` overrides that default. Reviewers add comments, then review an email with the fixed subject **Feedback City Pilot**, every message currently displayed in the conversation, source links, and the resulting checklist. **Open Outlook** opens Outlook on the web with the recipient and subject filled in. Short drafts also fill the message body. Long drafts use **Copy full feedback**, followed by opening Outlook, pasting into the message body and clicking Send. If clipboard permission is denied, the complete preview is selected for manual copy. There are no downloads. The reviewer sends the message in Outlook; automatic server delivery would require an authenticated email integration. Comments stay in the current page and are cleared by Start over. Preparing feedback does not call the model, require Turnstile, or send/store feedback on the app server. Restored persistent conversations include the history retained by the application (up to 200 turns); demo feedback includes all messages still displayed in the current tab even though model context retains only recent turns.

The default `PROJECT_STORAGE_MODE=demo` keeps project context only in the open browser tab's memory. It does not write project records to SQLite, Redis, local storage, or session storage. Context travels in HTTPS request bodies as a signed, compressed envelope, not in URLs; signatures prevent fabricated project records from being used for email drafts. The envelope is not encrypted and expires after six hours without an update. Refreshing, closing the tab, or switching devices starts over. Natural follow-ups, research, checklists, corrections, and configured email drafting remain available while the tab stays open. Applicant-address lookups are not cached across requests in this mode.

This describes application storage, not a guarantee of zero retention by the AI provider or hosting platform. Messages still go to the configured provider; its retention policy and hosting logs need separate review. Enabling demo mode does not delete previously saved records.

Optional `PROJECT_STORAGE_MODE=persistent` uses `.project-state.sqlite3` locally (ignored by Git). Override the location with `PROJECT_DB_PATH` if needed. On Vercel it uses the configured Upstash Redis REST endpoint with atomic revision checks, so concurrent requests cannot silently overwrite another update.

In persistent mode, project records expire seven days after their last update. The browser stores only a random project access reference in session storage; anyone with that reference can access that project. There is no authenticated project library or cross-device account history. Server state keeps the supported field record independently of the model's recent-message window, plus the last 200 dialogue turns. Email contact information is used to prepare the draft and is not added to the project record. Production authentication and a retention policy still require product decisions.

## Live research and conversational advice

Historic screening runs automatically when a street address or real estate number is supplied. `historic_gis.py` uses the City's public Land Development Review map session and proxy, including its required Referer header, to retrieve complete parcel polygons and query local historic districts, local landmarks, National Register records and Florida Master Site File structures. Queries use POST with polygon geometry; no browser, GIS credential or additional dependency is required. The layer names are checked before using their IDs. Only the address/parcel identifiers and bounded historic attributes enter project research.

The same lookup separately reads the map's **Ash Site** property details: `ASHSITE` on Address Point records (layer 4) and `ASH_NAME` on every matched parcel (layer 1). Address points are queried by exact official address; their RE field is empty in the current service and is not used to join parcels. Findings retain raw values, parcel/address identity, completeness and source information. A mapped designation adds an independent Solid Waste Division / Ash Program coordination action. Blank/null/N/A fields mean no designation shown in those records; they do not establish absence of contamination. Missing fields and failed queries remain unresolved. Ash findings do not set the Historic flag. The sidebar shows Ash status and underlying property-details records.

The address check can complete a missing street suffix from a unique official address and suggest a close spelling match with the same house number. Supplied street types and directions are preserved; ambiguous or incomplete searches stay unresolved. Spelling suggestions require confirmation before their parcel results can be relied on. The screened or suggested address is displayed with the finding. The supplied Jacksonville logo appears in the header on desktop and mobile.

The pre-review project updates and routing rules have been restored. Sessions created with the reverted review's scope schema are cleaned of its extra fields and narrative conflicts when loaded, preserving the original applicant fields and fact conflicts. This prevents a saved description conflict from continuing the reverted question loop.

Intake follows a server-owned checklist: previous use for a reported business project (zoning/COU context), interior work, plumbing, equipment/systems, site/parking, then street access. Location/scope, address confirmation, real conflicts and relevant historic clarification are handled before those scope questions. The model extracts supported answers and gives a brief explanation; it cannot choose, bundle or reorder questions. Every answer updates saved fields and the rule-derived plan. One reply can resolve several items, which are then skipped. Simple yes/no replies update only the pending question, even if the model proposes other updates. Unknown or deferred answers remain City-confirmation items rather than looping. The server produces the final checklist once the applicable questions are resolved or deferred. City contact and approval are not prerequisites. Unsolicited fee amounts are suppressed during intake; a general restaurant/systems answer never establishes a new hood.

Explicit affirmative replies with details also resolve the pending question. For example, adding sinks while using existing office restrooms records plumbing as yes and retains the original scope quote. This protects the decision if model extraction omits it, uses a non-boolean value or supplies a paraphrased evidence quote. Conditional or reversed answers are not forced to yes. On the next chat update, an existing session can recover a missed decision from a recorded checklist question followed by a clear applicant answer; recovery fills only unknown flags and preserves known answers, uncertainty and conflicts.

Construction type is established before previous-use and work questions when unclear. A new business alone does not establish a new building. Explicit new-building/ground-up scope selects a separate checklist branch that skips prior-tenant and interior-alteration questions and asks about planned fixtures, systems, site work and street connections. Existing-building projects retain the renovation questions. Known answers are skipped on either branch. New construction establishes the building-review screening flag without rewriting an applicant's interior-work answer; it does not assume plumbing or specific fire systems. The checklist and report distinguish new-building occupancy review from converting an existing space.

The sidebar and report show the finding, parcel IDs, source and check time. Local mapped designations trigger an exterior-work question. No local intersection is described as “not identified,” rather than a definitive non-historic determination; National Register and inventory records are distinguished from local COA applicability. Multiple parcels, failed queries and unmatched addresses retain their qualifications. A lookup failure never becomes a negative finding. Applicant-reported historic facts remain separate; conflicting applicant and GIS findings stay unresolved. Changing the address invalidates the old result. Same-project findings are reused for up to an hour, with unresolved local checks retried after five minutes; demo mode does not cache addresses across projects. The production backend therefore uses direct Python queries, while `C:\city\gis-proof` remains the standalone browser proof.

If exact and same-number address matching fail, historic screening searches within 100 street numbers on the same street. A unique closest street-number match is screened and displayed as a suggested nearby address, with confirmation required. This compares street numbers, not geographic distance. Ties, conflicting street suffixes/directions, and incomplete or oversized searches stay unresolved. Nearby results never establish historic status for the original project address.

Each turn first updates applicant-supported scope, then retrieves relevant official City guidance and renders a concise explanation with one useful follow-up. The chat is no longer limited to acknowledgements. Sources appear as clickable links under the reply. Public reads are bounded, run concurrently and cache for an hour; unavailable sources do not supply prices.

`city_research.py` searches the Duval Property Appraiser's public search form, ranks address matches (including small spelling errors), and reads the matching property-use records. It deliberately excludes owner names, mailing addresses and valuations. A parcel/tax classification is kept in `project.research`, separate from applicant-confirmed `existing_use` and occupancy. Multiple records at one address remain ambiguous. The address alone never establishes the suite use or permitted occupancy.

The City fee page is parsed into rows retaining amounts, categories, units, minimums and qualifiers. The model chooses a relevant fee ID, but the server inserts the exact fetched amount and qualification. Model-written dollar figures are rejected. COU fees, hood review fees and mobility calculation/review fees are distinct from total project costs. The actual mobility obligation is not calculated or guessed. Applicable fee references also appear inside the checklist disclosures.

Retrieved references include COU/converting-use guidance, BID's current code-edition statement, converting-use/design-professional FAQs, current forms, CMMSO guidance, and relevant site/LDPM overview pages. BID currently identifies the 2023 Florida Building Code, 8th Edition. This confirms the edition reference; it does **not** mean the full code, all LDPM PDF volumes or Municipal Code Chapters 652/654/655/656 have been ingested. Additional expert-reviewed documents can be incorporated when supplied. The example ChatGPT transcript is a style/evaluation example, not an authoritative fee or code source.

## Routing rules and review status

The displayed screening checklist covers Archaeological Sensitivity (High only), Cemeteries, National Register Historic Districts/Properties, Local Landmarks, Local Historic Districts and Florida Master Site File historic structures. **Any** matching category triggers the overall **Historic review: Flagged** status in chat, sidebar and plan, with the matched category retained. A non-local finding remains a coordination decision for City staff, rather than an automatic COA requirement. Medium and Low archaeological sensitivity do not trigger this overall flag. Failed category queries remain incomplete.

The automatic GIS check also queries Archaeological Sensitivity (layer 40), a predictive model separate from local historic designation. Only a **High** intersection triggers an archaeological warning and a scope-dependent coordination item for ground disturbance. Medium, Low and Water classifications remain in raw evidence and do not trigger warnings or actions. The app reports multiple classifications as parcel intersections, never as proof the entire parcel has one classification. Sensitivity alone does not establish a confirmed archaeological site, mandatory survey, permit or COA. Lookup schema versions invalidate older same-project cached results when the screening changes.

`routing_rules.json` records the version, official City sources, section descriptions, triggers and exceptions. `project_workflow.py` selects the relevant guidance and derives a shared preliminary plan. The use conversation precedes building-submission coordination where the use route is applicable or unresolved; this does not mean final COU issuance precedes building/fire approval. Independent site and specialty conversations can begin alongside use clarification. Detailed formal site-review dependencies remain questions for Development Services, rather than invented universal prerequisites.

Sources were checked on October 12, 2026. These rules are an engineering draft, not a City/reviewer-approved rule base. In particular, the supplied preliminary matrix provides the plumbing screening trigger; the linked submission page does not establish a universal plumbing-permit requirement. The City source page identifies specific site and ROW exceptions. Only explicitly supported scope qualifiers activate those exceptions.

## Validation

```powershell
python -m unittest -v test_conversation test_ash test_intake_completion test_chat_rollback test_address_lookup test_demo test_workflow test_research test_historic test_browser
```

The workflow scenarios cover use conversion, unresolved use, specialty work without interior construction, parking restriping, FDOT right-of-way, conflicting/corrected scope, durable state, stale writes, department email purpose and local endpoint parity. The optional headless Chromium test exercises the actual browser flow, email preview, correction, restore and mobile width. Set `BROWSER_EXECUTABLE` to an installed Chromium/Edge executable if the Windows Edge default is not available. Model and Turnstile responses are mocked; these checks do not certify model extraction accuracy, real provider integration or City routing.

City email drafts begin with customer name, potential address, business goal, planned work and property / lease status. The email form prefills supported business and work descriptions and lets the applicant edit them and select a lease status. These email-only details do not change the saved project or its routing; blank details remain explicitly unresolved. An expandable reference list explains all seven official zoning, historic and downtown resources. Drafts include references relevant to the selected review steps, plus conditional downtown guidance when downtown is mentioned. References do not establish historic designation, overlay boundaries or required approvals. Preview, verification and sending behavior are unchanged.

Before promotion, a qualified reviewer should supply approved paths for representative projects. Review each scenario for missed requirements, incorrect first steps, exceptions, unnecessary questions and department relevance. Mark a rule reviewed only after that review and increment its version when changing applicability or dependencies.

The screening rules deliberately distinguish potential permit routes from conditions that require City confirmation. It is not connected to JaxEPICS and cannot submit an application or make a City determination.

## Production notes

- Host behind HTTPS and set `OPENAI_API_KEY` in the host’s secret manager—not in code or a browser variable.
- Add authentication, rate limiting, a privacy/retention policy, and City-approved content/contact data before public launch.
- Review the prompt/rules whenever City procedures change. Final routing belongs with the appropriate City staff.
