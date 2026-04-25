# ACP x PCC x Memory x Fusion Gate

## One-Line Definition

VORTEX routes work by combining ACP execution surfaces, PCC judgment posture, evidence-ranked memory, and Fusion Gate enforcement so stored context can help without becoming unchecked truth.

## Responsibility Split

| Layer | Role | Trust Rule |
|---|---|---|
| ACP | Connects execution surfaces such as CLI, MCP, Antigravity, Warp, and agent lanes. | Transport is not approval. A response still needs evidence. |
| PCC | Selects the judgment posture for exploration, audit, compression, critique, or implementation review. | A preset constrains reasoning but does not prove correctness. |
| Memory | Recalls prior design, facts, references, and artifacts. | Stored memory is a candidate artifact, not truth. |
| Fusion Gate | Applies routing, cache controls, critic lanes, PE checks, and commit gates. | Passing requires grounded evidence, not persuasive wording. |

## Gate Lanes

The default commit boundary uses two independent critic lanes:

- Gemini CLI with `gemini-3.1-pro-preview` for the PCC critic lane.
- Copilot CLI with `gpt-5-mini` as the Perfect Equilibrium umpire lane focused on unsupported claims, missing evidence, scope drift, semantic drift, fake verification, and completion illusion.

## Operating Model

```text
user task / repo change
  -> ACP surface chooses where the work can run
  -> PCC defines how the work should be judged
  -> memory recall provides bounded context with trust labels
  -> Fusion Gate checks evidence, drift, cache effects, and critic output
  -> commit / promotion only if the gate passes
```

## Anti-Regression Rules

1. Do not treat recalled memory as canonical unless it has current evidence.
2. Do not let cache stability masquerade as correctness.
3. Do not promote agent self-reports without diffs, tests, logs, or source references.
4. Do not dispatch Jules while the quarantine flag is active.
5. Commit gates must stay small enough to inspect and strict enough to fail on missing evidence.

## Practical Meaning

The system is not "more context for the model." It is a trust boundary:

- ACP moves packets between tools.
- PCC tells each lane how to think.
- Memory supplies reusable context with evidence labels.
- Fusion Gate decides whether the output is safe to use, cache, commit, or promote.
