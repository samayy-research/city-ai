# Jax Project Advisor

An independent Python application that turns a natural-language project conversation into preliminary Jacksonville permit-routing guidance, a coordination plan, and a checklist.

## Run locally

PowerShell:

```powershell
cd C:\city\jax-project-advisor
$env:GEMINI_API_KEY = "your Gemini API key"
# Optional: choose a Gemini model supported by your API account
$env:GEMINI_MODEL = "gemini-2.5-flash"
python app.py
```

Open http://127.0.0.1:8000. The key stays on the Python server and is never sent to the browser.

## Deploy to Vercel

This folder is ready to deploy as a Vercel project. In the Vercel import screen, select `jax-project-advisor` as the **Root Directory**. No build command or Python dependencies are required.

Add these Environment Variables in Vercel before deploying:

- `GEMINI_API_KEY` (required)
- `GEMINI_MODEL` (optional; defaults to `gemini-3.5-flash`)
- `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` (required to enable the protected City coordination-email form)
- `CITY_COORDINATION_EMAIL` (required to enable the protected City coordination-email form)

Vercel routes `/` to the app page, `/api/chat` to the Gemini chat function, and `/api/coordination-request` to the Turnstile-protected coordination-email function. The email workflow validates the Turnstile token and then opens the visitor's email app with a pre-filled message; it does not send email from Vercel.

## What it does

1. The visitor describes a project in ordinary language.
2. Gemini reviews the conversation and asks one useful follow-up at a time when necessary.
3. When sufficient scope is known, it returns a readable project brief, likely preliminary routes, City groups to contact and what to bring, and a checklist.

The screening rules deliberately distinguish potential permit routes from conditions that require City confirmation. It is not connected to JaxEPICS and cannot submit an application or make a City determination.

## Production notes

- Host behind HTTPS and set `GEMINI_API_KEY` in the host’s secret manager—not in code or a browser variable.
- Add authentication, rate limiting, a privacy/retention policy, and City-approved content/contact data before public launch.
- Review the prompt/rules whenever City procedures change. Final routing belongs with the appropriate City staff.
