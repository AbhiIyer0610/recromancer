---
inclusion: always
---

# Product: Recromancer

**Tagline:** Their code lives on. So does their reasoning.

Recromancer lets a team ask questions of an engineer who has left. Answers come *only* from that
engineer's recorded work: commits, pull requests, reviews, issues and design docs. Every factual
sentence cites the record it came from, and when the record is silent the ghost says so.

**Hackathon category:** Frankenstein. The product is a chimera of technologies: GitHub records,
hybrid vector and lexical retrieval, a grounding verifier, small open models, Claude and MCP,
stitched into one app.

## Who it is for
New hires and maintainers inheriting code who need the *why* behind decisions, not just the *what*.

## Principles (these outrank convenience)
1. **Grounded or silent.** No sentence reaches a user unless it passed the grounding verifier.
   Abstaining is a feature, not a failure.
2. **A reconstruction, never the person.** The UI and prompts always say this is an AI summary of
   a documented record. No invented feelings, motives, opinions or private facts.
3. **Consent and scope.** Ingest only repositories the operator may read. A named subject can be
   excluded and purged. Evidence from other workspaces is never reachable.
4. **Production quality.** Typed, tested, observable, secure by default. No toy features and no
   demo-only shortcuts; demo mode is seeded data on the real code path.
5. **Clear, polished UI.** A first-time user understands what to do within seconds. Visuals show
   real data and never decorate with invented numbers.

## Non-goals
Impersonating a person to deceive, ingesting private chat or email, or answering from model
knowledge instead of evidence.

