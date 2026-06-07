# AssedGuard AI — Product Brief
**Track:** 01 — Data Rescue
**Team:** Solo builder
**Date:** June 7, 2026

## Who it's for
A compliance officer at a mid-size manufacturer preparing for a regulatory
audit. Has never opened a database. Works in Excel. Needs answers, not code.

## What it does (one sentence)
AssedGuard AI scans a corrupted manufacturing dataset, ranks every data
integrity issue by audit risk, fixes what it can automatically, and delivers
a plain-English narrative the compliance officer can sign and hand to an auditor.

## What success looks like
A compliance officer with zero technical skills uploads a CSV, clicks one button,
watches the system work, and downloads a PDF they can submit to a regulator —
without asking an engineer a single question.

## How it works
Four specialized agents hand off through a shared Cognee memory layer:
1. **Scout** reads the dataset and detects every data-quality issue.
2. **Ranker** prioritizes each issue by regulatory audit risk, with a visible
   reason for every ranking.
3. **Fixer** auto-corrects what is safe, flags what needs review, and escalates
   what a human must sign off on — logging a reason for every action.
4. **Narrator** reads the full pipeline memory and writes a signed-ready audit
   narrative, exported as a professional PDF.

## What we are NOT building
- Enterprise SSO or multi-tenant authentication
- Custom database connectors (CSV upload only)
- Real-time monitoring or continuous audit pipelines
