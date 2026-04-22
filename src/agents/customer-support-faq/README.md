# customer-support-faq prompt agent

A minimal **prompt-only** Azure AI Foundry agent that answers FAQs about the
landing-zone Bicep pattern in this repository. It is the working example
exercised by the [`deploy-prompt-agent.yml`](../../../.github/workflows/deploy-prompt-agent.yml)
workflow.

## Layout
- `agent.yaml` – name, model deployment, generation params, instruction file pointer.
- `instructions.md` – the system prompt.
- `evals/dataset.jsonl` – eight Q/A cases used for the gating evaluation.
- `upsert.py` – idempotent create/update of the agent on a Foundry project.
- `evals/eval.py` – batch eval using `azure-ai-evaluation` (Relevance +
  Groundedness). Writes `eval.json` and exits non-zero if the mean is below
  `EVAL_THRESHOLD` (default `3.5`).

## Run locally
Pre-reqs: a Foundry project (deploy this landing zone first), Python 3.10+,
and a logged-in `az` CLI.

```pwsh
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python upsert.py
python evals\eval.py
```

## Run in CI
Trigger the [`deploy-prompt-agent`](../../../.github/workflows/deploy-prompt-agent.yml)
workflow. It auths via OIDC, resolves the Foundry endpoint from the sandbox
resource group, upserts the agent and runs the eval. Promotion is gated on
the eval threshold input (default `3.5`).

## Conventions
- The `model` field in `agent.yaml` must match a deployment present on the
  Foundry project (the landing zone provisions `chat` by default).
- Keep ground-truth strings short – the Groundedness evaluator uses them as
  `context`, not the assistant's reply, so they must contain the canonical
  fact you expect the model to produce.
