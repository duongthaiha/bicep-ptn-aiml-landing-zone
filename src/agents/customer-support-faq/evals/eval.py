"""Evaluate the customer-support-faq agent's instructions against the dataset.

We exercise the underlying chat model directly using the same instructions the
prompt agent ships with. This decouples the gating eval from the agent runtime
(which is in preview) while still validating the prompt + model combination
that the agent will execute.

Writes:
  eval.json  Aggregate + per-row scores.

Exit code:
  0 if mean(Relevance, Groundedness) >= EVAL_THRESHOLD (default 3.5).
  1 otherwise.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

import yaml
from azure.ai.evaluation import GroundednessEvaluator, RelevanceEvaluator
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI


HERE = Path(__file__).parent
AGENT_DIR = HERE.parent
DATASET = HERE / "dataset.jsonl"
RESULT = HERE / "eval.json"
SCOPE = "https://cognitiveservices.azure.com/.default"


def _load_agent_spec() -> dict:
    spec = yaml.safe_load((AGENT_DIR / "agent.yaml").read_text(encoding="utf-8"))
    spec["instructions"] = (AGENT_DIR / spec.get("instructions_file", "instructions.md")).read_text(encoding="utf-8")
    return spec


def _load_dataset() -> list[dict]:
    return [json.loads(l) for l in DATASET.read_text(encoding="utf-8").splitlines() if l.strip()]


def _account_endpoint(project_endpoint: str) -> str:
    # https://acct.services.ai.azure.com/api/projects/proj -> https://acct.services.ai.azure.com/
    return project_endpoint.split("/api/projects/")[0].rstrip("/") + "/"


def main() -> int:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    threshold = float(os.environ.get("EVAL_THRESHOLD", "3.5"))
    api_version = os.environ.get("EVAL_API_VERSION", "2024-10-21")

    spec = _load_agent_spec()
    model = spec["model"]
    instructions = spec["instructions"]
    temperature = float(spec.get("temperature", 0.2))
    top_p = float(spec.get("top_p", 0.95))

    cred = DefaultAzureCredential()
    azure_endpoint = _account_endpoint(project_endpoint)
    token_provider = get_bearer_token_provider(cred, SCOPE)
    chat = AzureOpenAI(
        azure_endpoint=azure_endpoint,
        api_version=api_version,
        azure_ad_token_provider=token_provider,
    )

    model_config = {
        "azure_endpoint": azure_endpoint,
        "azure_deployment": model,
        "api_version": api_version,
    }
    relevance = RelevanceEvaluator(model_config=model_config)
    groundedness = GroundednessEvaluator(model_config=model_config)

    rows: list[dict] = []
    for case in _load_dataset():
        query = case["query"]
        truth = case["ground_truth"]
        completion = chat.chat.completions.create(
            model=model,
            temperature=temperature,
            top_p=top_p,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": query},
            ],
        )
        answer = completion.choices[0].message.content or ""
        scores = {
            "relevance": relevance(query=query, response=answer).get("relevance", 0),
            "groundedness": groundedness(response=answer, context=truth).get("groundedness", 0),
        }
        rows.append({"query": query, "answer": answer, "ground_truth": truth, "scores": scores})
        print(f"- {query[:60]:60s}  rel={scores['relevance']}  grd={scores['groundedness']}")

    flat = [s for r in rows for s in r["scores"].values()]
    mean = round(statistics.mean(flat), 3) if flat else 0.0
    summary = {
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
