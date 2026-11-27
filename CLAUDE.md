# CLAUDE.md — GlassineOS/handwriting

On-device online handwriting recognition for GlassineOS (see `README.md`): one program
with a session interface that the on-screen keyboard and any app use: `recognizer/`,
contract in `docs/recognizer.md` (keep it in step with the code; `make -C recognizer check`);
the first spec is `specs/001-recognizer/`.

- Nothing leaves the device: no network code, no telemetry. Samples come only from a
  person's confirmation, never from private fields; a change touching samples needs a test
  of these rules.
- Base models only from synthetic data, data under a suitable licence, data used with its
  authors' permission (BRUSH: non-commercial use, never redistributed), or samples their
  writer gave for training (those samples never go into the repository, CI, a pull request, a release or
  a log; only aggregate numbers do), with recipe, sources and licences next to them. No
  vendor engine, model or resource in this repository.
- Commits carry an `Assisted-by:` or `Co-Authored-By:` trailer. Never add
  `Signed-off-by`. Run `.glassine/org/scripts/safety-scan.sh --staged` before committing.
- Specs: Spec Kit 1.1.0 with the org preset (`.specify/`, setup in
  `.glassine/org/SPECS.md`); features go in `specs/###-name/`, only `spec.md` is committed.
  The repository principles are the last section of `.specify/memory/constitution.md`.
- Organization guidance for coding agents, including the project knowledge search
  (run `git submodule update --init` if the import below is missing):

@.glassine/org/AGENTS.md
