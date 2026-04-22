"""Run a small batch evaluation against the deployed prompt agent.

For each row in dataset.jsonl:
  1. Create a thread + user message.
  2. Invoke the agent and read the assistant reply.
  3. Score the reply against the ground truth using azure-ai-evaluation
     Relevance + Groundedness evaluators (model-graded by the same Foundry
     model, so no extra deployment is required).

Writes:
  eval.json  Aggregate results + per-row detail.
Exit code:
  0 if mean score >= EVAL_THRESHOLD (default 3.5 / 5).
  1 otherwise.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

from azure.ai.evaluation import GroundednessEvaluator, RelevanceEvaluator
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


HERE = Path(__file__).parent
DATASET = HERE / "dataset.jsonl"
RESULT = HERE / "eval.json"


def _load_dataset() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def _agent_id(agent_dir: Path) -> str:
    explicit = os.environ.get("AGENT_ID", "").strip()
    if explicit:
        return explicit
    return (agent_dir / "agent.id").read_text(encoding="utf-8").strip()


def _ask(client: AIProjectClient, agent_id: str, prompt: str, timeout: int = 90) -> str:
    thread = client.agents.threads.create()
    client.agents.messages.create(thread_id=thread.id, role="user", content=prompt)
    run = client.agents.runs.create(thread_id=thread.id, agent_id=agent_id)

    deadline = time.time() + timeout
    while run.status in ("queued", "in_progress", "requires_action") and time.time() < deadline:
        time.sleep(2)
        run = client.agents.runs.get(thread_id=thread.id, run_id=run.id)

    if run.status != "completed":
        return f"[run did not complete: status={run.status}]"

    # Newest first; find first assistant message.
    for msg in client.agents.messages.list(thread_id=thread.id):
        if msg.role == "assistant" and msg.content:
            for part in msg.content:
                text = getattr(part, "text", None)
                if text and getattr(text, "value", None):
                    return text.value
    return "[no assistant reply]"


def main() -> int:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    threshold = float(os.environ.get("EVAL_THRESHOLD", "3.5"))
    model = os.environ.get("EVAL_MODEL", "chat")

    agent_dir = Path(os.environ.get("AGENT_DIR") or HERE.parent).resolve()
    cred = DefaultAzureCredential()
    client = AIProjectClient(endpoint=endpoint, credential=cred)
    agent_id = _agent_id(agent_dir)

    model_config = {
        "azure_endpoint": endpoint.split("/api/projects/")[0],
        "azure_deployment": model,
        "api_version": os.environ.get("EVAL_API_VERSION", "2024-10-21"),
    }
    relevance = RelevanceEvaluator(model_config=model_config)
    groundedness = GroundednessEvaluator(model_config=model_config)

    rows: list[dict] = []
    for case in _load_dataset():
        query = case["query"]
        truth = case["ground_truth"]
        answer = _ask(client, agent_id, query)
        scores = {
            "relevance": relevance(query=query, response=answer).get("relevance", 0),
            "groundedness": groundedness(response=answer, context=truth).get("groundedness", 0),
        }
        rows.append({"query": query, "answer": answer, "ground_truth": truth, "scores": scores})
        print(f"- {query[:60]:60s}  rel={scores['relevance']}  grd={scores['groundedness']}")

    flat = [s for r in rows for s in r["scores"].values()]
    mean = round(statistics.mean(flat), 3) if flat else 0.0
    summary = {
        "agent_id": agent_id,
        "threshold": threshold,
        "mean_score": mean,
        "passed": mean >= threshold,
        "rows": rows,
    }
    RESULT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nMean score: {mean} (threshold {threshold}) -> {'PASS' if summary['passed'] else 'FAIL'}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
