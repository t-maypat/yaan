// Suggested questions: HERB dev questions, word for word, so a click replays the answer the
// eval already paid for (data/cache/llm) whenever the prompt and tools are unchanged. Dev only:
// held-out questions never appear in the UI (CLAUDE.md non-negotiable 3).

export type Suggestion = { short: string; question: string; product: string };

export const SUGGESTIONS: Suggestion[] = [
  {
    short: "Which company has the most unresolved bugs?",
    question: "Find the name of company that has the maximum number of unresolved bugs in VizForce?",
    product: "VizForce",
  },
  {
    short: "Which PRs were reverted, and why?",
    question:
      "Can you provide links to PRs for developing the AI Engine in VizForce that were reverted due to changes in product direction?",
    product: "VizForce",
  },
  {
    short: "Who wrote the fixes customers reopened?",
    question:
      "Find the employee IDs of engineers who authored maximum number of PRs in ExplainabilityForce for bug fixes that are now reopened by customers.",
    product: "ExplainabilityForce",
  },
  {
    short: "Which company has the most active bugs?",
    question: "Find the name of company that has the maximum number of active bugs in ExplainabilityForce.",
    product: "ExplainabilityForce",
  },
  {
    short: "Who wrote and reviewed the market research?",
    question:
      "Find employee IDs of the authors and key reviewers of the Market Research Report for the ExplainabilityForce product?",
    product: "ExplainabilityForce",
  },
  {
    short: "Which PRs still need merging?",
    question:
      "Could you provide the links to the PRs for the AI engine feedback loop in CollaborateForce that need to be merged?",
    product: "CollaborateForce",
  },
  {
    short: "Who shared competitor demos?",
    question: "Find employee IDs of team members who shared demos of CollaborateForce's competitor products?",
    product: "CollaborateForce",
  },
  {
    short: "A question the data can't answer",
    question:
      "What are the employee IDs of team members who shared demos of ExplainabilityForce's competitor products?",
    product: "ExplainabilityForce",
  },
];
