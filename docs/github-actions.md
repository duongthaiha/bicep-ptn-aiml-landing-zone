# GitHub Actions for the AI/ML Landing Zone

This document describes the CI/CD model shipped with the landing zone, how to consume it from a downstream accelerator repository, and the design decisions behind it (mapped to the Azure Well-Architected Framework).

> **Template-repo scope.** This repository is consumed downstream as a git submodule mounted at `infra/`. To avoid shipping CD entry points wired to subscriptions you do not own, the upstream repo only ships:
> - `pr-validate.yml` — static checks on every PR; sandbox `azd preview` on internal PRs.
> - `release.yml` — version bump + tag + GitHub Release; keeps `manifest.json.tag` and `manifest.json.ailz_tag` in sync so the jumpbox `install.ps1` referenced by `main.bicep` matches the released code.
> - `reusable-bicep-validate.yml` and `reusable-azd-deploy.yml` — `workflow_call` building blocks consumer repos can call by tag.
> - Opt-in workflow **templates** under `docs/templates/workflows/` for `cd-landingzone`, `foundry-hosted-agent`, and `foundry-prompt-agent`. These are NOT in `.github/workflows/` so GitHub will not try to run them.

---

## 1. Authentication: OIDC, no secrets

All workflows use **federated workload identity** via `azure/login@v2`. No client secrets are stored in GitHub.

Identity model — **two kinds of identities**:

| Identity | Federated subject | RBAC | Purpose |
|---|---|---|---|
| **Validation identity** | `repo:<owner>/<repo>:environment:pr-sandbox` | Reader + Contributor on a sandbox RG only | `azd provision --preview` on internal PRs |
| **Deploy identity** (one per env) | `repo:<owner>/<repo>:environment:<env>` | Contributor + User Access Administrator on the env RG | `azd provision` to dev / test / prod |

> User Access Administrator is required because `main.bicep` performs role assignments (control-plane RBAC) for the workload identities created during deployment.

Bootstrap with the included script:

```bash
./scripts/bootstrap-github-oidc.sh \
  --repo my-org/my-accelerator \
  --subscription <sub-id> \
  --location eastus2 \
  --envs dev,test,prod \
  --sandbox-rg rg-ailz-pr-sandbox \
  --env-rg-prefix rg-ailz-
```

Then in the GitHub UI: Settings → Environments → for `test` and `prod`, add **required reviewers** and (optional) wait timer.

---

## 2. Workflows that ship in this repo

### 2.1 `pr-validate.yml`
- Triggered on `pull_request` to `main` for IaC paths.
- Job 1 — **Static** (always, including fork PRs): bicep build, `bicep format --verify`, `manifest.json` schema check, PSRule for Azure with the curated baseline.
- Job 2 — **azd preview** (skipped on fork PRs because GitHub does not issue OIDC tokens to forks): logs in as the validation identity, calls `azd provision --preview` against `pr-sandbox` and posts the diff as a job summary.

### 2.2 `release.yml`
- `workflow_dispatch` with a semver `version` input.
- Validates semver, ensures the tag does not already exist.
- Updates `manifest.json` `tag` and `ailz_tag` in lockstep, commits to `main`, then creates the annotated tag from that commit and a GitHub Release with auto-generated notes.
- **Why both fields?** `main.bicep` (lines ~392, 992, 1006) downloads the jumpbox bootstrap from `refs/tags/${manifest.ailz_tag}/install.ps1`. If `ailz_tag` lags `tag`, a freshly-released landing zone deploys old jumpbox logic.

### 2.3 `reusable-bicep-validate.yml`
- `workflow_call`. Always runs static checks; runs `azd provision --preview` only when the caller passes `runPreview: true` and has performed `azure/login` first.

### 2.4 `reusable-azd-deploy.yml`
- `workflow_call`. OIDC login, azd install, env init, forwards every environment-scoped GitHub variable matching the landing-zone substitution prefixes (`AZURE_`, `DEPLOY_`, `NETWORK_`, `USE_`, `ENABLE_`, `EXISTING_`, `SIDE_`) into the `azd` env so `${VAR}` substitution in `main.parameters.json` resolves.

---

## 3. Consumer repo pattern

In a consumer accelerator that mounts this repo at `infra/`:

```yaml
# .github/workflows/cd-landingzone.yml in your repo
jobs:
  dev:
    uses: Azure/bicep-ptn-aiml-landing-zone/.github/workflows/reusable-azd-deploy.yml@v1.0.9
    with:
      environment: dev
      azdEnvName: my-accel-dev
      location: eastus2
```

Pin the `@vX.Y.Z` ref to the same tag your `infra/` submodule points at (see `manifest.json.ailz_tag`). Copy the starter from `docs/templates/workflows/cd-landingzone.template.yml`.

Per-env GitHub Environment variables (NOT secrets, since OIDC is passwordless):

| Variable | Required | Notes |
|---|---|---|
| `AZURE_CLIENT_ID` | yes | Per-env deploy identity app id |
| `AZURE_TENANT_ID` | yes | |
| `AZURE_SUBSCRIPTION_ID` | yes | |
| `AZURE_LOCATION` | yes | |
| `AZURE_RESOURCE_GROUP` | yes | Pre-created or created by deploy identity |
| `NETWORK_ISOLATION` | optional | `true` enables Zero Trust topology |
| `USE_UAI` | optional | Use user-assigned MI for workloads |
| `ENABLE_AGENTIC_RETRIEVAL` | optional | |
| `DEPLOY_VM_KEY_VAULT`, `USE_EXISTING_VNET`, `DEPLOY_SUBNETS`, `SIDE_BY_SIDE`, `EXISTING_VNET_RESOURCE_ID` | optional | Topology toggles |
| `AZURE_AI_FOUNDRY_LOCATION`, `AZURE_PSQL_LOCATION`, `AZURE_COSMOS_LOCATION`, `AZURE_PE_LOCATION`, `AZURE_PE_RESOURCE_GROUP_NAME` | optional | Per-service overrides |

---

## 4. Networking options for runners

Choose by deployment topology. **The landing zone’s `azd provision` itself is a control-plane operation against ARM and works from public runners even when `NETWORK_ISOLATION=true`.** Runner placement only matters for **data-plane** post-provision steps (e.g., pushing images to ACR with public network access disabled, seeding Key Vault secrets through the private endpoint, or talking to the Foundry project privately).

| Option | When to use | Pros | Cons |
|---|---|---|---|
| **GitHub-hosted (`ubuntu-latest`)** | Standard topology; control-plane-only steps in any topology | Zero ops, free for public repos | Cannot reach private endpoints |
| **Azure Container Apps job as ephemeral runner, VNet-injected** | Zero Trust, data-plane steps | Scale-to-zero, no VM patching, fully private | Newer pattern, requires ACA env in landing-zone VNet |
| **VNet-injected GitHub-hosted larger runners** | Zero Trust, GitHub Enterprise Cloud customers | Managed by GitHub, sits in your VNet | Requires Enterprise + paid larger runners |
| **Self-hosted runner on a VM in the landing-zone VNet** | Zero Trust, no Enterprise plan | Works with any plan | You own VM patching, scaling, and runner registration |

**Recommendation:**

- **Standard mode** → GitHub-hosted runners for everything.
- **Zero Trust mode** → GitHub-hosted runners for `azd provision` itself; ACA-jobs ephemeral runners (recommended) or VNet-injected larger runners for data-plane post-provision steps.
- The shipped `install.ps1` does **not** install or register a GitHub Actions runner on the jumpbox today. Self-hosted-on-jumpbox is documented as **future work** rather than a current recommendation.

---

## 5. Three agent workflow patterns

These are deliberately separated so a consumer can adopt only what they need. Templates live in `docs/templates/workflows/`.

### 5.1 Foundry infrastructure (this landing zone)
- Owned by `cd-landingzone` (consumer copy of the template) calling `reusable-azd-deploy.yml`.
- Provisions Foundry account/project, model deployments, ACR, Key Vault, Cosmos, Storage, Search, AI Search, App Configuration, Container Apps env, networking — all gated by the `deploy*` feature flags in `main.parameters.json`.
- RBAC produced for workloads includes `CognitiveServicesUser`, `CognitiveServicesOpenAIUser`, `AcrPull`, `KeyVaultSecretsUser`, etc.

### 5.2 Hosted agent (`foundry-hosted-agent.template.yml`)
- Runs after the landing zone is up. Builds your agent container, pushes to the landing-zone ACR, then creates/updates a hosted Foundry agent and runs a smoke invocation.
- Required RBAC for the deploy identity: **AcrPush** on the ACR, **Azure AI User** on the Foundry project.
- Environment variables: `ACR_NAME`, `FOUNDRY_PROJECT_ENDPOINT`, `AGENT_NAME`.

### 5.3 Prompt agent (`foundry-prompt-agent.template.yml`)
- Smaller runner, no container build. Upserts a prompt agent from `agent.yaml`, runs batch evaluation against a curated dataset, optionally runs the prompt optimizer, then gates promotion on an eval threshold.
- Required RBAC: **Azure AI User** on the Foundry project.
- Inputs: `runOptimizer`, `evalThreshold`.

### 5.4 Working example: `customer-support-faq`
A runnable instance of pattern 5.3 lives in this repo:

- Agent definition: [`src/agents/customer-support-faq/`](../src/agents/customer-support-faq/)
  - `agent.yaml` + `instructions.md` – prompt-only agent on the `chat` deployment.
  - `upsert.py` – idempotent create/update via `azure-ai-projects` and `DefaultAzureCredential`.
  - `evals/eval.py` – Relevance + Groundedness scoring with `azure-ai-evaluation`, threshold-gated.
- Workflow: [`.github/workflows/deploy-prompt-agent.yml`](../.github/workflows/deploy-prompt-agent.yml)
  - Resolves the Foundry endpoint from the `aif-*` account in the env's resource group (no extra config).
  - Adds an idempotent **Azure AI Developer** role assignment for the workflow service principal.
  - Inputs: `environment`, `agentDir`, `evalThreshold`, `dryRun`.
  - Pre-req: run `Sandbox azd preview` with `mode=provision` first; the workflow self-checks and fails clean otherwise.

---

## 6. WAF pillar mapping

| Pillar | Decision |
|---|---|
| **Security** | OIDC only (no long-lived secrets); separate validation vs deploy identities; per-env federated subjects; fork PRs blocked from Azure-authenticated steps; PSRule for Azure with curated baseline blocks merges; pinned action versions; dependabot enabled. |
| **Reliability** | `azd preview` gates every PR on internal branches; environment promotion with required reviewers on test/prod; idempotent reusable workflow uses `azd env select` before `azd env new`; `release.yml` keeps `tag`/`ailz_tag` in lockstep so jumpbox bootstrap matches infra. |
| **Operational Excellence** | Reusable `workflow_call` templates avoid duplication; consumer repos pin to released versions of this repo; `manifest.json` is the single source of truth for the released ref; preview output is captured as a Job Summary for reviewer audit. |
| **Performance Efficiency** | Concurrency groups cancel superseded PR runs; static checks parallelize with preview; only IaC paths trigger validation. |
| **Cost Optimization** | GitHub-hosted runners by default; ACA ephemeral runners scale-to-zero option for Zero Trust; preview before deploy avoids paying for failed full provisions. |

---

## 7. PSRule baseline & suppressions

`.github/ps-rule.yaml` includes `PSRule.Rules.Azure` with the `Azure.Default` baseline. Curated suppressions live in `.ps-rule/Suppression.Rule.yaml` for landing-zone-intentional trade-offs (CMK is opt-in, optional services gated by `deploy*` flags, etc.). Each entry MUST link to a justification documented here so reviewers can audit accepted exceptions.

| Suppression | Justification |
|---|---|
| `Azure.Storage.UseReplication` | Landing zone defaults to LRS; consumer chooses higher SKU per environment via parameter. |
| `Azure.KeyVault.AutoPurge` | Soft-delete is on by default; auto-purge is environment-policy-dependent. |
| `Azure.Resource.UseTags` | `deploymentTags` is parameterized; consumer-supplied tag policies own enforcement. |

Add new suppressions sparingly; prefer fixing the underlying Bicep.

---

## 8. Fork PR safety

- `pr-validate.yml` job 1 (static) runs on fork PRs. It uses no secrets and no Azure credentials.
- `pr-validate.yml` job 2 (`azd preview`) is gated by `github.event.pull_request.head.repo.full_name == github.repository`, so fork PRs never reach OIDC login. Do **not** convert this to `pull_request_target` with checkout of fork code.

---

## 9. Consumer / submodule guidance

If your consumer repo follows the recommended pattern (`infra/` is this repo as a submodule pinned to `vX.Y.Z`):

1. Set your consumer repo’s `manifest.json.ailz_tag` to the same `vX.Y.Z` you pinned in `.gitmodules`.
2. Reference reusable workflows from this repo by tag, not by branch:
   ```yaml
   uses: Azure/bicep-ptn-aiml-landing-zone/.github/workflows/reusable-azd-deploy.yml@v1.0.9
   ```
3. Do not modify files in `infra/`. Override via consumer-side `main.parameters.json` and `manifest.json` copied in by your `azd` `preprovision` hook (see `AGENTS.md`).
4. Run `scripts/bootstrap-github-oidc.sh` from this repo against your consumer repo and your subscriptions.

---

## 11. Region selection for AI/ML workloads

The landing zone deploys AI Search, Cognitive Services / AI Foundry, Cosmos DB and Container Apps Environments in a single region. Capacity for **AI Search Standard SKU** and the **broadest Foundry model catalog** is the binding constraint — pick the region for those services first and let the rest follow.

### Recommended primary regions (Apr 2026 — verified against Microsoft Foundry catalog)

| Region | Foundry models | AI Search | Notes |
|---|---|---|---|
| **Sweden Central** ⭐ | ~123 (largest in EU) | ✅ Standard + extra capacity | Best for EU data residency, broadest model selection |
| **East US 2** ⭐ | ~120 | ✅ Standard + extra capacity | First-tier US region for new model rollouts |
| **Central US** | ~102 | ✅ Standard | Strong fallback for North America |
| **France Central** | ~99 | ✅ Standard | Good EU alternative to Sweden Central |
| **West Europe** | ~95 | ✅ Standard | Established EU region |
| **East US** | ~95 | ✅ Standard | Established US region; can be capacity-tight |
| **UK South** | ~95 | ✅ Standard | UK data residency |
| **West US 3** | (widely supported) | ✅ Standard | Good for west-coast latency |

### Lessons from live testing of this CI/CD

We hit `InsufficientResourcesAvailable` for AI Search Standard in **eastus2** (during a busy window). Re-running the same pipeline in another region succeeded for the rest of the stack. Treat region selection as a **per-deployment** decision, not a one-time choice — bake the region into an `azd env` per environment and have a documented fallback.

**Operational tips:**

1. Probe live SKU availability before committing to a region:
   ```sh
   az search service create --name probe-$RANDOM --resource-group $RG \
     --location $LOC --sku Standard --partition-count 1 --replica-count 1 --no-wait
   # If it accepts → capacity exists. Delete the probe immediately afterwards.
   ```
2. For Foundry model availability use the Azure CLI:
   ```sh
   az cognitiveservices model list -l swedencentral -o table
   ```
3. The `sandbox-region-check.yml` workflow in this repo hits the Azure REST API and prints a per-region matrix to the job summary — use it as a quick comparator across your subscription.
4. **Avoid mixing regions** for tightly coupled services (Search ↔ Foundry ↔ Cosmos) unless you’ve measured the cross-region latency cost.

### When `InsufficientResourcesAvailable` strikes

This is **environmental**, not a code defect. Sequence of action:

1. Re-run the workflow 15–30 minutes later (capacity often returns).
2. If still failing, switch `AZURE_LOCATION` to the next region from the table above and re-run `sandbox-cleanup.yml` then re-provision.
3. If a region is persistently constrained, request capacity via the Azure portal (Subscriptions → Quotas) or open a support case.

---

## 12. Future work
- Implement self-hosted runner registration in `install.ps1` (gated by a new `deployGitHubRunner` parameter and a runner registration token from a managed identity-protected Key Vault secret).
- Provide an ACA-jobs runner Bicep module as an opt-in extension.
- Auto-create the Entra apps + federated credentials from a one-time GitHub Action (currently scripted via `bootstrap-github-oidc.sh`).
