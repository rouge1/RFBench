# Grok as a second reviewer and worker

A second model, from another vendor, reads the work and runs code against
it. It is a source of **leads, not a verdict**: verify every finding
against the code or the data before acting, fix what is true, and answer
what is not. Claude Code's own sub-agents (Sonnet, Haiku) stay on the
Agent tool; Grok is run through Bash as its own process.

The method is borrowed from bluey-ox-walker, which has used it for several
reviews (its `knowledge/grok-review-guide.md`). This is the SDR repo's
version. Grok CLI 1.0.46 and model `grok-4.7` were what ran here on
2026-10-04, and a headless smoke test in another repository finished
cleanly (`stopReason: end_turn`, about $0.02).

## When to use it

- **Reviewer.** Before work is reported done: an encoder against the
  spec's own vectors, a grader against a known truth, a test that might
  not test what it claims. Best asked to try to prove the work wrong, and
  to recompute every number itself.
- **Worker.** A bounded job whose result can be checked: recomputing a
  number, an independent implementation to compare against, a
  self-contained script. A worker that writes does so in a `/tmp` copy or
  a worktree, never the main tree.
- **Review before the result exists** when the work is an experiment: hand
  over the design, the script and the inputs, not the outcome.

## Rules for every prompt

- **It never opens a radio.** The VSG60, the BB60D, the HackRF and the
  ssh aliases are off limits; say so in the prompt. Transmitting stays
  with the user ([radios](radios.md#vsg60-notes)).
- **It reads what the repo holds and nothing else.** Name the files; do
  not point it at the home directory, ssh configuration, key files or
  the session's scratchpad. If a step is refused by Claude Code's action
  classifier, do not hand the same step to Grok: it counts as the same
  outcome by another route.
- **Public repo rules apply to its output too**: no bench-machine address,
  key or account name goes into a review that is committed.
- **Review a fixed tree.** Give it a commit and have it
  `git archive <commit> | tar -x -C /tmp/<name>/tree`, not the working
  tree, which moves under it. Do not commit while it runs if the prompt
  names a branch tip.

## Running it

Headless, in the background, one prompt file:

```sh
GROK_MEMORY=0 GROK_DISABLE_AUTOUPDATER=1 grok --cwd /data/python/SDR \
  --sandbox <profile> --always-approve --no-subagents --no-auto-update \
  -m grok-4.7 --reasoning-effort high --max-turns 250 \
  --output-format streaming-json --prompt-file <prompt>.md \
  > $SCRATCH/grok_run.jsonl 2> $SCRATCH/grok_run.err
```

- `--prompt-file` takes the prompt; there is no `--prompt`. A bare string
  is the positional argument.
- `--always-approve` skips Grok's own questions only. The kernel sandbox
  is what limits it.
- The last line of the `.jsonl` is the `end` record (`stopReason`,
  `num_turns`, `total_cost_usd`). An empty `.err` and `end_turn` mean it
  finished. Poll for `{"type":"end"` in the log. `pgrep -f "grok.*<prompt>"`
  in a wait loop matches the loop's own command line and never exits;
  use a bracket (`[g]rok`) or poll the log.
- The answer is not at a fixed place in the stream. Have the prompt tell
  Grok to write its review to a named file under `/tmp` and read that.
- A reviewer with `--sandbox read-only` or `strict` has no network for the
  commands it runs, so everything it needs is on local disk.

## The sandbox, and the folder trust

Grok will not start headless in a folder it has not been told to trust:
`Error: Permission denied (os error 13)`, with nothing more. A prior grant
or `--trust` fixes it, and under a sandbox only `grok --trust` run in the
directory beforehand is saved, because the profile write-protects
`~/.grok/trusted_folders.toml`. It was trusted for one other repository
here and **not yet for this one**.

`read-only` can read the whole home directory, so a prompt is the only
thing keeping it from credentials. Use a custom profile in
`~/.grok/sandbox.toml` instead: kernel-enforced `deny` (through
`bwrap`) beats a request in a prompt.

```toml
[profiles.sdr-review]
extends = "strict"            # reads the working directory and system paths only
restrict_network = true       # commands it runs cannot reach the network
read_only = ["/home/<user>/miniconda3/envs/gnu"]    # to run the tests
deny = ["/home/<user>/.ssh", "/tmp/<claude scratchpad>"]
```

`strict` is an allowlist, which is safer than listing secrets, but it
makes `/tmp` readable and writable, and the Claude Code scratchpad
lives there: name it in `deny`.

Not tried yet: a full review run in this repo under that profile.

## After a review

Verify each finding, fix, and record both what was true and what was not,
in the document under review (finding, what was checked, done). Add the
review text to `knowledge/` with explicit `git add <paths>`, and run the
tests again.
