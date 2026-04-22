"""Create-or-update a versioned prompt agent on a Foundry project.

Uses the Foundry Prompt Agents API (PromptAgentDefinition + create_version).
Each invocation publishes a new version under the same agent name; the
returned id (name + version) is written to ``agent.id``.

Required env vars:
  FOUNDRY_PROJECT_ENDPOINT  https://<account>.services.ai.azure.com/api/projects/<project>

Optional env vars:
  AGENT_DIR     Path to the agent directory (default: parent of this file)
  AGENT_OUTPUT  Path to write "<name>:<version>" (default: agent.id)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential


def _load_spec(agent_dir: Path) -> dict:
    spec = yaml.safe_load((agent_dir / "agent.yaml").read_text(encoding="utf-8"))
    instructions_path = agent_dir / spec.pop("instructions_file", "instructions.md")
    spec["instructions"] = instructions_path.read_text(encoding="utf-8")
    return spec


def main() -> int:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        print("ERROR: FOUNDRY_PROJECT_ENDPOINT is not set.", file=sys.stderr)
        return 2

    agent_dir = Path(os.environ.get("AGENT_DIR") or Path(__file__).parent).resolve()
    spec = _load_spec(agent_dir)

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    definition = PromptAgentDefinition(
        model=spec["model"],
        instructions=spec["instructions"],
    )

    print(f"Publishing agent '{spec['name']}' on model '{spec['model']}'...")
    agent = client.agents.create_version(
        agent_name=spec["name"],
        definition=definition,
    )

    out_path = Path(os.environ.get("AGENT_OUTPUT", agent_dir / "agent.id"))
    out_path.write_text(f"{agent.name}:{agent.version}", encoding="utf-8")

    print(json.dumps({"name": agent.name, "version": agent.version}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
