## Solution Overview

This repository is an **AI Landing Zone implemented in Bicep** for Azure.
It provides a reusable, production-oriented infrastructure baseline for AI workloads built on Microsoft Foundry and related Azure services.

Primary goals:
- Standardize secure, repeatable provisioning with `azd` + Bicep.
- Offer modular deployment with feature flags and strong parameterization.
- Support both quick-start and hardened network-isolated topologies.
- Act as a reusable infra core for other accelerators and agent-based solutions.

Repository:
- https://github.com/azure/bicep-ptn-aiml-landing-zone

---

## What To Understand First

This is an IaC-first repository. Most work happens in:
- `main.bicep`: orchestrates all resources, modules, conditions, identities, role assignments.
- `main.parameters.json`: deployment topology, feature flags, model list, app list, networking options.
- `modules/`: reusable custom Bicep modules for networking, security role assignment, AI Foundry connections, app config population.
- `constants/constants.bicep`: role IDs and naming abbreviations.
- `azure.yaml`: azd project definition (`infra.path: .`, `infra.module: main`).
- `manifest.json`: release metadata + optional component repos + jumpbox install script source.
- `install.ps1`: jumpbox bootstrap logic for isolated environments.

If behavior changes, update Bicep and parameters consistently.

---

## IaC Architecture and Design Patterns

### 1) Single entrypoint orchestration
- `main.bicep` is the deployment orchestrator.
- It composes AVM modules and custom modules using feature-flag conditions (`if (...)`).

### 2) Feature-flag driven composition
Common toggles:
- `deployAiFoundry`
- `deployAppConfig`
- `deployKeyVault`
- `deploySearchService`
- `deployStorageAccount`
- `deployCosmosDb`
- `deployContainerEnv`
- `deployContainerApps`
- `networkIsolation`
- `useExistingVNet`

Preserve this conditional model when adding resources.

### 3) Strong parameterization + substitution model
- `main.parameters.json` supports env var substitution (`"${ENV_NAME}"`) for `azd` workflows.
- In Bicep, values that can come empty should have safe fallback handling.
- Avoid hardcoding tenant/subscription/resource names in templates.

### 4) Modular networking for Zero Trust
- Supports public and isolated modes.
- Isolated mode includes VNet/subnets, private DNS zones, private endpoints, and controlled dependencies.
- PE creation is serialized in places to avoid parallel conflicts.

### 5) Identity and RBAC by design
- Supports system-assigned MI and optional UAI (`useUAI`).
- Role assignments are explicit and centralized, including data-plane Cosmos assignments.

### 6) App topology as data
- `containerAppsList`, `modelDeploymentList`, `databaseContainersList`, `storageAccountContainersList` drive infra shape.
- New app/model/service behavior should be added by extending these lists and mapping logic.

---

## Current Container App Port Behavior

Container app ingress and Dapr app port are parameterized per app entry:
- `app.target_port` when provided.
- fallback to `8080` when omitted.

Pattern in `main.bicep`:
- `ingressTargetPort: int(app.?target_port ?? 8080)`
- `dapr.appPort: int(app.?target_port ?? 8080)`

Implications:
- If you add apps in `main.parameters.json`, you can set `target_port` explicitly.
- If omitted, app config defaults to 8080.

---

## Parameterization Guidance

When adding new capability, follow this sequence:
1. Add parameter in `main.bicep` with description and sensible default.
2. Add corresponding value in `main.parameters.json` (literal or `"${ENV_VAR}"`).
3. If substitution can resolve to empty, add fallback handling in Bicep.
4. Wire the value to modules/resources.
5. If runtime needs the value, publish it to App Configuration through `appConfigPopulate`.
6. If downstream automation needs it, expose as Bicep output.

Good practices:
- Keep booleans as true/false semantics in Bicep.
- Keep names deterministic (resource token + abbreviations).
- Keep module params minimal but explicit.
- Preserve idempotency.

---

## Reusing This Landing Zone from Another Repository

This section is critical for derived accelerators.

### Recommended consumption model
Use this repository as an **infra submodule** mounted at `infra/` in the consumer repo.

Why:
- Consumer gets a stable IaC core.
- Consumer customizes only overlays (`main.parameters.json`, `manifest.json`, optional scripts).
- Infra updates are versioned by submodule pin.

### Pinning strategy for consistency
Pin submodule to a specific landing zone release/tag.

Example commands to add and pin the submodule to `v1.0.0`:
```bash
git submodule add https://github.com/Azure/bicep-ptn-aiml-landing-zone.git infra
git -C infra fetch --tags
git -C infra checkout tags/v1.0.0
git config -f .gitmodules submodule.infra.branch v1.0.0
git config -f .gitmodules submodule.infra.ignore dirty
git add .gitmodules infra
git commit -m "Add infra submodule pinned to v1.0.0"
```

Initialization command for consumers:
```bash
git submodule update --init --recursive
```

Example `.gitmodules` pattern:
```ini
[submodule "infra"]
	path = infra
	url = https://github.com/Azure/bicep-ptn-aiml-landing-zone.git
	branch = v1.0.0
	ignore = dirty
```

Notes:
- Keep pin explicit to avoid drift between environments.
- Treat infra version bumps as controlled upgrades.

### Consumer `azure.yaml` pattern
Use:
- `infra.provider: bicep`
- `infra.path: infra`
- `infra.module: main`
- `preprovision` hook to prepare submodule and overlays.

### Preprovision mechanism (important)
A robust preprovision hook should:
1. Run `git submodule update --init --recursive`.
2. Detect `azd init` ZIP scenario (no gitlink/submodule metadata in git index).
3. If `infra/main.bicep` is missing, clone infra repo directly using `.gitmodules` URL + pinned ref.
4. Copy consumer overlay files into `infra/`:
   - `main.parameters.json`
   - `manifest.json`

Result:
- Consumer-specific parameters override landing zone defaults.
- Consumer controls component graph and release metadata without forking IaC templates.

---

## `manifest.json` Contract and Jumpbox Bootstrap Pattern

`manifest.json` in this repository currently contains:
- landing zone release metadata (`tag`, `repo`)
- `install_script` URL
- optional `components` array

### Why this matters in network-isolated deployments
When `deployVM` + `deploySoftware` are used, the VM custom script extension runs `install.ps1`.
`install.ps1`:
- installs tools (az, azd, git, etc.)
- clones this repo by release
- initializes azd environment
- reads `manifest.json.components`
- clones each component repo at pinned tags
- copies `.azure` environment context into each component repo

This enables post-provision work from the jumpbox where public access is constrained.

### Consumer pattern for derived accelerators
In a consumer repo, set `manifest.json` to:
- point `install_script` to the desired landing zone tag URL
- define component repos/tags the jumpbox should clone
- keep all tags pinned for reproducibility

Use this as the primary mechanism to bootstrap isolated deployments without reauthoring infra templates.

---

## Module Map (High-Level)

- `modules/ai-foundry/*`: AI Foundry account/project and service connections.
- `modules/networking/*`: subnet, private endpoint, private DNS modules.
- `modules/security/*`: role assignment wrappers (control-plane and Cosmos data-plane).
- `modules/container-apps/*`: app list shaping for configuration publishing.
- `modules/app-configuration/*`: key-value population in App Configuration.

Rule:
- Reuse existing module patterns before creating new module files.
- Keep custom logic centralized and avoid duplicated resource blocks.

---

## Deployment Modes

### Standard mode
- Faster setup.
- Public networking where applicable.

### Zero Trust mode
- Enable network isolation.
- Private DNS and private endpoints activated.
- Jumpbox/Bastion workflow becomes central for post-provision operations.

Do not mix assumptions between these modes.

---

## Operational Commands

Typical operator flow:
```bash
az login
azd auth login
azd provision
```

Parameter overrides:
```bash
azd env set NETWORK_ISOLATION true
azd env set USE_UAI true
azd env set ENABLE_AGENTIC_RETRIEVAL true
```

You can also update `main.parameters.json` directly instead of using `azd env set`.
Use `azd env set` when you want per-environment values without editing files; use `main.parameters.json` when you want explicit, versioned defaults in source control.

---

## Change Checklist

Before submitting changes, verify:
1. Feature flags still gate optional resources correctly.
2. New params exist in both `main.bicep` and `main.parameters.json` when required.
3. Network isolation path still works (private DNS/PE dependencies intact).
4. Role assignments remain least-privilege and scoped correctly.
5. App Configuration population includes any new runtime settings.
6. Names remain deterministic and compliant.
7. Changes are compatible with submodule consumer pattern.

---

## Do and Do Not

Do:
- Prefer extending parameter lists over hardcoded values.
- Keep modules reusable and environment-agnostic.
- Preserve compatibility for downstream repos consuming this as `infra/`.
- Keep release pinning explicit in consumer patterns.

Do not:
- Assume a single workload topology.
- Break fallback behavior for substituted params.
- Couple consumer-specific app logic directly into landing zone core unless generic.
- Remove the `manifest.json` bootstrap contract used by jumpbox automation.

---

## External Reference for a Full Consumer Implementation

For an end-to-end example of the submodule + preprovision override + manifest bootstrap pattern in practice, see:
- https://github.com/azure/gpt-rag

Use that reference to validate mechanics, but keep this repository generic and reusable.
