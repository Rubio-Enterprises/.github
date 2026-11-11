---
status: accepted
date: 2026-07-14
---

# Shared Apple TestFlight release architecture

## Context and Problem Statement

Apple applications need a common Release Orchestrator and signing authority without centralizing their distinct targets and capabilities. Duplicated release machinery drifts, but fully centralized archive logic creates a fragile application-parameter matrix. Where should shared release orchestration, application-specific archive knowledge, and signing authority live?

## Considered Options

- Shared GitHub Actions orchestration, application-owned Fastlane, and Match as sole signing authority.
- Duplicated application workflows: rejected because runner, keychain, secret-handoff, and cleanup behavior would drift.
- Fully centralized Fastlane: rejected because application targets, extensions, entitlements, and capabilities create a fragile parameter matrix.
- Xcode Cloud with automatic signing: rejected because important workflow state moves outside Git and introduces another release authority.
- Automatic development signing alongside Match releases: rejected because lower-friction local setup introduces competing signing authorities.

## Decision Outcome

Chosen option: "Shared GitHub Actions orchestration, application-owned Fastlane, and Match as sole signing authority", because it prevents orchestration drift and competing signing authorities without centralizing application-specific archive logic.

## Consequences

- Good, because runner and signing-environment fixes are shared while application targets and capabilities remain close to their source.
- Bad, because local development requires Match setup, and device or capability changes require explicit signing-vault mutations rather than CI writes.
- Bad, because the shared team App Store Connect credential has cross-application scope.
