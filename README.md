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

In the Vercel import screen, select `jax-project-advisor` as the **Root Directory**. No build command or Python dependencies are required. Configure shared project storage before deploying this version; Vercel function memory and temporary files are not durable project storage.

Add these Environment Variables in Vercel before deploying:

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (optional; defaults to `gpt-4.1-mini`)
- `OPENAI_BASE_URL` (optional; defaults to `https://api.openai.com/v1`)
- `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` (required on Vercel for shared, durable project state)
- `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` (required to enable the protected City coordination-email form)
- `CITY_COORDINATION_EMAIL` (required to enable the protected City coordination-email form)

Vercel routes `/` to the app page, serves `advisor.js` as a static asset, and exposes `/api/chat` and `/api/coordination-request`. The local Python server serves those same paths. The email workflow validates the Turnstile token and returns a short draft for review. The visitor then chooses to open their email app; no email is sent by this application.

## What it does

1. The visitor describes a project in ordinary language.
2. The model receives the saved project record and relevant versioned guidance. It asks one useful plain-language question when an answer could change routing, and returns field updates with exact applicant quotes.
3. The server preserves scope, reported approvals, explicit uncertainty, corrections and conflicts. Silence never deletes an earlier supported detail.
4. One authoritative action plan supplies the start-here conversations, prerequisites, applicable/unresolved/excluded routes, sources and department email requests.
5. Applicants can continue adding or correcting details after seeing a plan. The updated plan replaces the previous one. Reloading the same tab restores the server-side project and recent conversation.

## Project storage

Local development uses `.project-state.sqlite3` (ignored by Git). Override the location with `PROJECT_DB_PATH` if needed. Vercel uses the configured Upstash Redis REST endpoint with atomic revision checks, so concurrent requests cannot silently overwrite another update.

Project records expire seven days after their last update. The browser stores only a random project access reference in session storage; anyone with that reference can access that project. There is no authenticated project library or cross-device account history. Server state keeps the supported field record independently of the model's recent-message window, plus the last 200 dialogue turns. Email contact information is used to prepare the draft and is not added to the project record. Production authentication and a retention policy still require product decisions.

## Live research and conversational advice

Each turn first updates applicant-supported scope, then retrieves relevant official City guidance and renders a concise explanation with one useful follow-up. The chat is no longer limited to acknowledgements. Sources appear as clickable links under the reply. Public reads are bounded, run concurrently and cache for an hour; unavailable sources do not supply prices.

`city_research.py` searches the Duval Property Appraiser's public search form, ranks address matches (including small spelling errors), and reads the matching property-use records. It deliberately excludes owner names, mailing addresses and valuations. A parcel/tax classification is kept in `project.research`, separate from applicant-confirmed `existing_use` and occupancy. Multiple records at one address remain ambiguous. The address alone never establishes the suite use or permitted occupancy.

The City fee page is parsed into rows retaining amounts, categories, units, minimums and qualifiers. The model chooses a relevant fee ID, but the server inserts the exact fetched amount and qualification. Model-written dollar figures are rejected. COU fees, hood review fees and mobility calculation/review fees are distinct from total project costs. The actual mobility obligation is not calculated or guessed. Applicable fee references also appear inside the checklist disclosures.

Retrieved references include COU/converting-use guidance, BID's current code-edition statement, converting-use/design-professional FAQs, current forms, CMMSO guidance, and relevant site/LDPM overview pages. BID currently identifies the 2023 Florida Building Code, 8th Edition. This confirms the edition reference; it does **not** mean the full code, all LDPM PDF volumes or Municipal Code Chapters 652/654/655/656 have been ingested. Additional expert-reviewed documents can be incorporated when supplied. The example ChatGPT transcript is a style/evaluation example, not an authoritative fee or code source.

## Routing rules and review status

`routing_rules.json` records the version, official City sources, section descriptions, triggers and exceptions. `project_workflow.py` selects the relevant guidance and derives a shared preliminary plan. The use conversation precedes building-submission coordination where the use route is applicable or unresolved; this does not mean final COU issuance precedes building/fire approval. Independent site and specialty conversations can begin alongside use clarification. Detailed formal site-review dependencies remain questions for Development Services, rather than invented universal prerequisites.

Sources were checked on October 12, 2026. These rules are an engineering draft, not a City/reviewer-approved rule base. In particular, the supplied preliminary matrix provides the plumbing screening trigger; the linked submission page does not establish a universal plumbing-permit requirement. The City source page identifies specific site and ROW exceptions. Only explicitly supported scope qualifiers activate those exceptions.

## Validation

```powershell
python -m unittest -v test_workflow test_research test_browser
```

The workflow scenarios cover use conversion, unresolved use, specialty work without interior construction, parking restriping, FDOT right-of-way, conflicting/corrected scope, durable state, stale writes, department email purpose and local endpoint parity. The optional headless Chromium test exercises the actual browser flow, email preview, correction, restore and mobile width. Set `BROWSER_EXECUTABLE` to an installed Chromium/Edge executable if the Windows Edge default is not available. Model and Turnstile responses are mocked; these checks do not certify model extraction accuracy, real provider integration or City routing.

Before promotion, a qualified reviewer should supply approved paths for representative projects. Review each scenario for missed requirements, incorrect first steps, exceptions, unnecessary questions and department relevance. Mark a rule reviewed only after that review and increment its version when changing applicability or dependencies.

The screening rules deliberately distinguish potential permit routes from conditions that require City confirmation. It is not connected to JaxEPICS and cannot submit an application or make a City determination.

## Production notes

- Host behind HTTPS and set `OPENAI_API_KEY` in the host’s secret manager—not in code or a browser variable.
- Add authentication, rate limiting, a privacy/retention policy, and City-approved content/contact data before public launch.
- Review the prompt/rules whenever City procedures change. Final routing belongs with the appropriate City staff.
