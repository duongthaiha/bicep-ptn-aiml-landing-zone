"""Evaluate the customer-support-faq agent's instructions against the dataset.

We exercise the underlying chat model directly using the same instructions the
prompt agent ships with, then ask the same model to grade each answer on
relevance and groundedness with a 1-5 LLM-as-judge prompt. This avoids the
azure-ai-evaluation library's hard-coded ``max_tokens`` parameter, which is
rejected by gpt-5 / o-series deployments (they only accept
``max_completion_tokens``).

Writes:
  eval.json  Aggregate + per-row scores.

Exit code:
  0 if mean(Relevance, Groundedness) >= EVAL_THRESHOLD (default 3.5).
  1 otherwise.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
from pathlib import Path

import yaml
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI


HERE = Path(__file__).parent
AGENT_DIR = HERE.parent
DATASET = HERE / "dataset.jsonl"
RESULT = HERE / "eval.json"
SCOPE = "https://cognitiveservices.azure.com/.default"

JUDGE_TEMPLATE = """You are a strict evaluator. Score the assistant ANSWER on
the {dimension} criterion using an integer from 1 (worst) to 5 (best).

{criterion}

QUESTION:
{query}

GROUND TRUTH:
{truth}

ASSISTANT ANSWER:
{answer}

Respond with a single integer 1-5 and nothing else."""

CRITERIA = {
    "relevance": "Relevance: does the answer directly address the question?",
    "groundedness": "Groundedness: are the factual claims supported by the ground truth?",
}


def _load_agent_spec() -> dict:
    spec = yaml.safe_load((AGENT_DIR / "agent.yaml").read_text(encoding="utf-8"))
    spec["instructions"] = (AGENT_DIR / spec.get("instructions_file", "instructions.md")).read_text(encoding="utf-8")
    return spec


def _load_dataset() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def _account_endpoint(project_endpoint: str) -> str:
    return project_endpoint.split("/api/projects/")[0].rstrip("/") + "/"


def _ask(client: AzureOpenAI, model: str, system: str, user: str) -> str:
    completion = client.chat.completions.create(
        model=model,
        max_completion_tokens=2048,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return completion.choices[0].message.content or ""


def _score(client: AzureOpenAI, model: str, dimension: str, **kwargs) -> int:
    raw = _ask(
        client,
        model,
        system="You are an evaluation assistant.",
        user=JUDGE_TEMPLATE.format(dimension=dimension, criterion=CRITERIA[dimension], **kwargs),
    )
    match = re.search(r"[1-5]", raw)
    return int(match.group(0)) if match else 0


def main() -> int:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    threshold = float(os.environ.get("EVAL_THRESHOLD", "3.5"))
    api_version = os.environ.get("EVAL_API_VERSION", "2025-01-01-preview")

    spec = _load_agent_spec()
    model = spec["model"]
    instructions = spec["instructions"]

    cred = DefaultAzureCredential()
    azure_endpoint = _account_endpoint(project_endpoint)
    token_provider = get_bearer_token_provider(cred, SCOPE)
    client = AzureOpenAI(
        azure_endpoint=azure_endpoint,
        api_version=api_version,
        azure_ad_token_provider=token_provider,
    )

    rows: list[dict] = []
    for case in _load_dataset():
        query = case["query"]
        truth = case["ground_truth"]
        answer = _ask(client, model, instructions, query)
        scores = {
            dim: _score(client, model, dim, query=query, truth=truth, answer=answer)
            for dim in CRITERIA
        }
        rows.append({"query": query, "answer": answer, "ground_truth": truth, "scores": scores})
        print(f"- {query[:60]:60s}  rel={scores['relevance']}  grd={scores['groundedness']}")

    flat = [s for r in rows for s in r["scores"].values()]
    mean = round(statistics.mean(flat), 3) if flat else 0.0
    summary = {
        "model": model,
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
