"""
Role: The ask-tools optimization loop harness — dev tooling that evaluates the app.ask agent
      against the gold question set: run_eval (answer questions, cache by config), grade
      (deterministic tier + judging worksheet), and the loop's file formats.
Used by: the loop operator (Claude session) driving mutate → evaluate → select iterations.
Depends on: app.ask (runner/tools/adapter), app.core.database.reader_session.
Key invariants:
  - PII BOUNDARY: question files, answers, and transcripts live in the artifacts dir OUTSIDE
    the repo (Benchmarks/_ask_loop by default) — nothing content-bearing is ever committed.
  - Answers cache by (config_hash, qid): identical configurations never re-spend tokens —
    the screening ladder's economics depend on this.
  - The runner never sees gold answers; grading is a separate pass (grader-vs-runner isolation).
"""
