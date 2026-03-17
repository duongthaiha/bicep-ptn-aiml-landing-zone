# Changelog

All notable changes to this project will be documented in this file.  
This format follows [Keep a Changelog](https://keepachangelog.com/) and adheres to [Semantic Versioning](https://semver.org/).

## [v1.0.2] - 2026-03-17
### Fixed
- Fixed provisioning failures caused by unguarded references to outputs from optional resources when feature flags are disabled.
- Aligned App Configuration population with resource deployment toggles for Search, Key Vault, Storage, Container Apps, and Container Environment values.
- Aligned role-assignment loop conditions with `deployContainerApps` to prevent unsafe indexed references when Container Apps are disabled.
- Aligned user-assigned identity creation conditions with owning resource flags to avoid orphan identities.

## [v1.0.1] - 2026-03-06
### Fixed
- Fixed conditional references to Log Analytics outputs when `deployLogAnalytics=false`.
- Aligned App Insights, Private Link Scope, and scoped resources conditions with the Log Analytics flag.
- Prevented App Configuration values from referencing non-deployed Log Analytics/App Insights resources.

### Removed
- Removed all API Management (APIM) mentions and related configuration from the landing zone templates and constants.

## [v1.0.0] - 2026-03-02
### Added
- Initial release of the Azure AI Landing Zone Bicep implementation.