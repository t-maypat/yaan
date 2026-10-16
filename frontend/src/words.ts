// Plain words for the system's names: what a tool did, what kind of artifact a source is.

export const TOOL_WORDS: Record<string, string> = {
  search_artifacts: "Searched the text",
  resolve_entity: "Looked up a name",
  get_artifact: "Read one source in full",
  doc_discussion: "Read a document's review",
  issue_reports: "Found customer bug reports",
  list_prs: "Listed pull requests",
  list_issues: "Listed customer issues",
  links_shared: "Listed shared links",
};

export const KIND_WORDS: Record<string, string> = {
  message: "Slack message",
  meeting: "Meeting transcript",
  meeting_chat: "Meeting chat",
  doc: "Document",
  pr: "Pull request",
  link: "Shared link",
};

// Evidence-graph node labels (evidence_subgraph.cypher) as legend words.
export const NODE_WORDS: Record<string, string> = {
  person: "Person",
  product: "Product",
  customer: "Customer",
  company: "Company",
  claim: "Claim",
  issue: "Issue",
  decision: "Decision",
  ...KIND_WORDS,
};

export const ARTIFACT_KINDS = new Set(Object.keys(KIND_WORDS));

export function when(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
