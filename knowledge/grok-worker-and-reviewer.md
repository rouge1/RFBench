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
`~/.grok/trusted_folders.toml`. This repository was trusted that way on
2026-10-04. The swarm's worktrees (below) started without any grant of
their own.

`read-only` can read the whole home directory, so a prompt is the only
thing keeping it from credentials. Use a custom profile with a `deny` list
instead: it is enforced by the kernel through `bwrap`, which works here,
and beats a request in a prompt. Two are kept in this repository's
untracked `.grok/sandbox.toml`:

```toml
[profiles.sdr-review]          # reads everything it is not denied, writes /tmp only
extends = "read-only"
restrict_network = true        # commands it runs cannot reach the network
deny = ["/home/<user>/.ssh", "/tmp/<claude scratchpad's parent>"]

[profiles.sdr-worker]          # reads its working directory and system paths only
extends = "strict"
restrict_network = true
read_only = ["/home/<user>/miniconda3/envs/gnu", "<this repo>/.git"]   # tests; git resolves a worktree
deny = ["/home/<user>/.ssh", "/tmp/<claude scratchpad's parent>"]
```

`strict` is an allowlist, which is safer than listing secrets, but it makes
`/tmp` readable and writable, and the Claude Code scratchpad lives there:
name it in `deny`. A worker under `strict` can write only its working
directory and `/tmp`.

Web access: Grok's search and fetch tools run inside its own process, so a
sandbox's `restrict_network` does not touch them; it blocks only what a
command it runs does (`curl`, `pip install`). The headless review was
started with `--disallowed-tools web_search,web_fetch`. **Runs through the
swarm are not**: its launcher does not pass that flag, so there a prompt's
"do not use the network" is only a request, and page text a worker reads is
untrusted input to a model holding the repository. Say so in the prompt, and
treat what it brings back from the web as a lead.

## Running it through the swarm

The `agent-swarm` skill (kept in its own repository on this machine) runs
Grok and Claude subagents as workers and reviewers in git worktrees, and
logs the time and cost of every run. Here it is installed as a symlink,
`.claude/skills/agent-swarm`, with its settings in `.swarm/swarm.toml`;
both are kept out of git, because the settings hold absolute paths.

- **The permission rule.** Claude Code's auto mode blocks a command that
  launches `grok --always-approve`. Allow the one wrapper and not `grok`:
  `Bash(python3 .claude/skills/agent-swarm/scripts/swarm.py run:*)`, in
  `.claude/settings.local.json`. It is a prefix match, so the command must
  begin exactly that way, and `--config` goes after `run`, not before.
- **Worktrees, outside `/tmp`.** `swarm.py wt <phase> <task> --model <key>`
  makes `/data/python/SDR-wt/<phase>-<task>-<model>`; note that it adds the
  model to the name. A worker is only ever started in one of these, and
  never in the main checkout.
- **The profile has to be in the worktree.** Grok looks for a project
  sandbox profile from the folder it is started in, so a profile kept in
  the main checkout is not found: the first run failed in two seconds with
  `profile 'sdr-worker' not found` and a second, misleading error about
  the deny list. After `swarm.py wt` and before **every** run, copy
  `.grok/sandbox.toml` into the worktree's `.grok/`. Re-copying overwrites
  anything a worker changed. A worker could edit its own copy; if one ever
  does, move the profiles into `~/.grok/sandbox.toml`, which a sandboxed
  worker cannot write.
- **A smoke test comes first.** One trivial task, then a resumed session,
  then a write and a read in the home folder, outside `/tmp`: the task ran
  in 6 s for $0.034, the resumed session answered from its context for
  $0.006, and both home accesses failed with `Permission denied` and left
  nothing behind.
- **A Sonnet subagent is not sandboxed.** The Agent tool's workers have no
  kernel boundary, so only the prompt keeps one inside its worktree. Give
  it the path `swarm.py wt` printed: one given `.../p2-modulator` for
  `.../p2-modulator-sonnet` found nothing, did nothing, and said so.
- **`--text-out`** saves a run's final answer to a file; `swarm.py agents`
  shows what each worker is doing.

## Building the same thing twice, and a hold-out

For a kernel written from a specification, which two readers can get
wrong in the same way, build it twice and check against a third source:

1. Write the test first, from the standard's own sample data, and parse
   that data with a parser that is itself checked (counts and a few
   values read off the page).
2. Give each builder **part** of the data, and keep the rest. Neither
   builder sees the other's code, and neither sees the hold-out, so code
   that merely fits what it was shown fails it. Here each saw one of three
   addresses; the one address it saw exercises none of the address bits.
3. Run the hold-out, then compare the two kernels on hundreds of thousands
   of random inputs, and against any existing kernel.

Stage 3's hop kernel went this way: Sonnet's took 3.5 minutes and
Grok's 7.7 minutes and $0.17, both passed the hold-out (7,680 of 7,680),
and the two agreed on 360,000 inputs. The comparison with bluey's kernel
turned up one real difference, on adapted sequences at slave-slot clocks,
which bluey confirmed.

## The first review, 2026-10-04

Grok reviewed the stage 2 grader (`scripts/bt_ota_check.py`) twice: once
as a session started by the user, once headless as above, with the
`sdr-review` profile, no web tools, and a prompt that named the files and
forbade the radios. The second took about 45 minutes and wrote its
experiments under `/tmp/sdr-review/`. It left the tree as it found it.

What it found, checked here before anything was changed:

- **Real and fixed:** the detector offset was 50 samples where 49.5 is
  right, which put every start half a sample early; a recording shorter
  than a template crashed the grader; a fit with a zero or negative
  slope would have looped forever; a burst in the first 12,000 samples
  was counted found and then silently dropped; `--iq` with `--capture`
  opened the wrong path; the notes' paragraph on missed bursts did not
  match the data.
- **Right to leave alone:** three extra peaks on a noiseless full-scale
  loop, and the 40-sample match window.
- **Held up:** the chunk-boundary fix, at every chunk size it tried,
  including one smaller than the overlap.

It found these by planting recordings with a known clock and fractional
starts, which the reference files cannot do: every start in them is a
whole sample. That is the thing to ask of the next review. Its own
count of findings was a guide to where to look, not a list to work
through; two were its reading of a convention that this repo had
already settled differently.

## After a review

Verify each finding, fix, and record both what was true and what was not,
in the document under review (finding, what was checked, done). Add the
review text to `knowledge/` with explicit `git add <paths>`, and run the
tests again.
