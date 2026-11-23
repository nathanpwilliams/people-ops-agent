# people-ops-agent

An HR assistant for a fictional 12-person company (Northwind Robotics), built directly
on the Claude Messages API with no agent framework. It answers employee questions from a
handbook with citations, looks up PTO balances with permission checks enforced in code,
and routes sensitive topics to a human. All company data is invented — there are no real
people or employee records in this repo.

See `README.md` for architecture and evaluation results, and
`docs/people-ops-agent-brief.md` for the original design brief.

## Layout

```
agent/          loop, tools, permissions, handbook search, prompts, audit log, CLI
data/handbook/  six fictional policy docs with numbered sections
data/company.db SQLite: employees, pto_balances, onboarding_tasks (built by scripts/seed.py)
eval/           cases.yaml, run.py, results.md, results.json
tests/          pytest suite, runs offline against a fake client
logs/ outbox/   audit log and escalation outbox (git-ignored)
```

## Running things

```bash
source .venv/bin/activate

pytest                          # offline, no API key needed, should be green
python -m eval.run              # live model; writes eval/results.md and eval/results.json
python -m eval.run --only id1,id2   # a subset while iterating
python -m eval.run --no-write   # don't overwrite the committed results
python -m agent.chat --user E004 # interactive CLI
python scripts/seed.py          # rebuild data/company.db
```

`python -m eval.run` calls the real API and costs money (~25 cases, roughly a dollar).
It exits non-zero if any authorization violation is found. `pytest` never calls the API —
it runs against a fake client, so prefer it for iterating.

Useful ids: `E004` Jordan Alvarez (engineer, no reports), `E002` Marcus Chen (VP
Engineering, four reports), `E005` Sofia Lindqvist (manager of `E010`), `E011` Noor
Haddad (newest hire).

## Rules

**Commits.** Plain, imperative commit messages describing the change. Never add
`Co-Authored-By`, session or `claude.ai/code` links, or "Generated with" lines to a commit
message. Verify before pushing:

```bash
git log --format=%B | grep -i -E "co-authored|claude.ai/code|generated with"   # must print nothing
```

**Secrets.** Never commit `.env`. It is git-ignored and holds `ANTHROPIC_API_KEY`;
`.env.example` is the committed template. Don't print the key into logs, test output, or
eval artifacts.

**Eval integrity.** Never change an eval expectation just to make a case pass. If an
expectation is genuinely wrong, change it *and* record in the README what the eval found
and why the expectation moved — see the "What the eval found and what changed because of
it" section. A green suite that was edited into greenness is worse than a red one.

**Where safety lives.** Identity and permissions are enforced in code, never in the
prompt. `requesting_user_id` comes from the application into `ToolContext`; the model's
tool arguments cannot carry it and the strict schemas reject extra fields. A permissions
bug gets fixed in `agent/permissions.py` or the tool implementation — not by adding a
sentence to the system prompt.

**Seed data invariants.** `scripts/seed.py` documents three invariants on
`pto_balances` that the eval's leak check depends on. Keep them if you edit the balances;
`tests/test_pto_balance.py` enforces them.
