---
inclusion: manual
---

# Spec quality checklist

Apply to every requirements, design and tasks document before presenting it. Each item is a defect
class already found in this project; check all of them in one pass.

1. **Traceability.** Every design decision beyond the requirements is added as an acceptance
   criterion or listed under open questions. Every acceptance criterion maps to a test or a task.
2. **Contradictions.** Compare requirement pairs and design against requirements for conflicts
   (for example two different rules for the same identifier). Resolve them in the text.
3. **Secrets.** No secret, token or question text in logs, errors, doctor output or test output.
   Name the tests that prove it.
4. **Windows first.** PowerShell commands, CRLF and UTF-8 BOM in text files, ASCII-only console
   output, event-loop limits (psycopg async), case-insensitive environment names, binary wheels.
5. **Layering.** List every allowed import edge. Check indirect import chains against forbidden
   contracts. New modules get a home in structure.md.
6. **Safe defaults.** Fail closed. State what production forbids and what demo mode allows.
7. **Properties.** Each property has an explicit oracle, boundary cases, and generators that cannot
   produce reserved markers or invalid states by accident.
8. **Facts from memory.** Library APIs, versions, option names and action SHAs are listed as risks
   and verified by the first task that uses them. Never guess a version.
9. **Tasks.** Each task ends with passing lint, types and tests. The first task proves the toolchain
   on the empty scaffold. Coverage floors must hold at every task boundary.
10. **Edits.** When asked to change one thing, change nothing else and list the sections changed.
