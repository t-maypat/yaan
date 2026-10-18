"""Answer contract (Phase 9.0; spec docs/CONTEXT.md §9.6).

The agent's final turn is constrained to SCHEMA by structured output, then validated with
the Pydantic model. as_of_ms and confidence (§9.6) arrive with the decision tools in slice B.
"""

from pydantic import BaseModel


class Citation(BaseModel):
    n: int
    artifact_id: str
    quote: str


class Answer(BaseModel):
    answer_markdown: str
    citations: list[Citation]
    abstain: bool


SCHEMA = {
    "type": "object",
    "properties": {
        "answer_markdown": {
            "type": "string",
            "description": "the answer; every factual sentence or list item ends with [n] markers",
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "the [n] marker"},
                    "artifact_id": {"type": "string", "description": "an id from tool results"},
                    "quote": {
                        "type": "string",
                        "description": "a short span copied word for word from that artifact",
                    },
                },
                "required": ["n", "artifact_id", "quote"],
                "additionalProperties": False,
            },
        },
        "abstain": {
            "type": "boolean",
            "description": "true when the evidence does not answer the question",
        },
    },
    "required": ["answer_markdown", "citations", "abstain"],
    "additionalProperties": False,
}
