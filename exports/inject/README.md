# Synthetic demo injections

**Everything in this folder is synthetic.** These messages are not HERB data; they were written for Yaan's live demo (CONTEXT rule 5, PLAN 11.7, D057). Their ids start with `msg:synthetic-`, the UI marks them, and they are only ever written to the demo graph `lore_demo`, never to `lore_herb`.

| File | Story | What Yaan should do |
|---|---|---|
| `01-regression.json` | An engineering lead says customer CUST-0076 has reopened 'Insufficient Rate Limiting on API Requests', which the team marked resolved via PR 52. | `regression` alert: blast radius = PR 52, its author and reviewer, the customer's company, the message that said "resolved", sibling issues in the API Security Module. |
| `02-unauthorized-dismissal.json` | A software engineer who isn't the assignee dismisses the same bug as "not a bug". | The policy says `unauthorized`: the bug stays reopened, the claim CONFLICTS_WITH the decision, and a `conflict` alert asks the owner to confirm or reject. |

Their timestamps follow HERB's clock (the product's last artifact is 8 Mar 2027), so the fold orders them after the fix. Run them with `yaan demo reset` then `yaan demo inject exports/inject/01-regression.json`.
