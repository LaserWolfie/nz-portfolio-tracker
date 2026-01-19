# Dev Workflow (Aider + Streamlit Cloud)

## Branching
- `feature/<name>` for features
- `fix/<name>` for bug fixes
- `chore/<name>` for tooling/docs

## Safe change loop
1) Create branch
2) Make minimal diff
3) Run fast checks:
   - `python -m compileall -q .`
   - relevant smoketest scripts in `scripts/`
   - `streamlit run app.py`
4) Commit with tight message
5) Push and verify Streamlit Cloud deploy

## Dont break prod guardrails
- Avoid wide refactors.
- Keep I/O boundaries explicit (Sheets/API).
- Never overwrite user-entered Sheet values unless explicitly requested.
- Prefer additive columns/tabs over destructive migrations.

## How to write tasks for the agent
Always specify:
- Target files
- Constraints
- Commands to run
- Acceptance criteria (what done means)

