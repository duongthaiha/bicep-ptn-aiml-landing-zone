"""Create-or-update the customer-support-faq prompt agent on a Foundry project.

Auth: DefaultAzureCredential (works locally with `az login` and in CI with
the OIDC env vars exported by azure/login@v2).

Required env vars:
  FOUNDRY_PROJECT_ENDPOINT  e.g. https://<account>.services.ai.azure.com/api/projects/<project>

Optional:
  AGENT_DIR        Path to the agent directory (default: parent of this file)
  AGENT_OUTPUT     Path to write the resulting agent id (default: agent.id)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def _load_agent_spec(agent_dir: Path) -> dict:
    spec = yaml.safe_load((agent_dir / "agent.yaml").read_text(encoding="utf-8"))
    instructions_file = agent_dir / spec.pop("instructions_file", "instructions.md")
    spec["instructions"] = instructions_file.read_text(encoding="utf-8")
    return spec


def _find_existing(client: AIProjectClient, name: str):
    # Agents API uses list_agents() returning items with .name
    for agent in client.agents.list_agents():
        if getattr(agent, "name", None) == name:
            return agent
    return None


def main() -> int:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        print("ERROR: FOUNDRY_PROJECT_ENDPOINT is not set.", file=sys.stderr)
        return 2

    agent_dir = Path(os.environ.get("AGENT_DIR") or Path(__file__).parent).resolve()
    spec = _load_agent_spec(agent_dir)

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    name = spec["name"]
    common = dict(
        model=spec["model"],
        name=name,
        description=spec.get("description"),
        instructions=spec["instructions"],
        temperature=spec.get("temperature"),
        top_p=spec.get("top_p"),
        metadata={k: str(v) for k, v in (spec.get("metadata") or {}).items()},
    )

    existing = _find_existing(client, name)
    if existing is None:
        print(f"Creating agent '{name}'...")
        agent = client.agents.create_agent(**common)
    else:
        print(f"Updating agent '{name}' (id={existing.id})...")
        agent = client.agents.update_agent(agent_id=existing.id, **common)

    out_path = Path(os.environ.get("AGENT_OUTPUT", agent_dir / "agent.id"))
    out_path.write_text(agent.id, encoding="utf-8")

    print(json.dumps({"agent_id": agent.id, "name": name, "model": spec["model"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
