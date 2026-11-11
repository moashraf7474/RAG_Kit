## What this changes

<!-- One or two sentences. Link the issue: Fixes #123 -->

## Why

<!-- The problem being solved. If it is a behaviour change, say what was wrong
     with the old behaviour. -->

## How it was tested

<!-- Be specific. "Ran the tests" is less useful than "uploaded a 40-page PDF
     with tables, confirmed tables stayed whole and citations pointed at the
     right pages". -->

- [ ] `make test` passes
- [ ] `pre-commit run --all-files` is clean
- [ ] Tested manually against a running stack

## Checklist

- [ ] Commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`)
- [ ] New behaviour has a test; a bug fix has a test that fails without it
- [ ] `README.md` updated if behaviour, configuration, or the test count changed
- [ ] New comments explain *why*, not *what*
- [ ] No new `font-bold` or font weight above `font-medium`

## Areas needing extra review

<!-- Tick anything this PR touches — these get a closer look. -->

- [ ] `owner_id` filtering / tenant isolation
- [ ] `backend/rag/prompts.py` (prompt-injection guards)
- [ ] Upload validation or the SSRF guard (`backend/rag/security.py`)
- [ ] Qdrant version or client version
- [ ] Code ported from `stackitcloud/rag-template` (keep the attribution
      docstring, and note deliberate divergence from upstream)
- [ ] None of the above
