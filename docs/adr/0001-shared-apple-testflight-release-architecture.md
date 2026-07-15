# ADR 0001: Shared Apple TestFlight release architecture

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

The organization maintains multiple personal Apple application forks that must be built from source, installed on registered iOS devices, and distributed through internal TestFlight. The applications have different targets and capabilities, but their signing, release authorization, runner preparation, and TestFlight operations should not drift.

One application already has a repository-local Fastlane and GitHub Actions implementation. Another has richer Apple capabilities and an archive-ready Xcode scheme but no complete repository-controlled release process. Copying either implementation unchanged would preserve avoidable differences:

- duplicated GitHub runner and temporary-keychain logic would drift;
- automatic and manual signing would create two signing authorities;
- centralizing all archive behavior would produce an unwieldy cross-application parameter surface;
- Xcode Cloud would move important release configuration outside the repositories;
- a public-release-oriented workflow would have more authority than personal internal TestFlight distribution requires.

The architecture must remain understandable for a single maintainer, allow local signed builds, and support later adoption by additional Apple applications without turning the standards template into a product-release system.

## Decision

### Release Orchestrator

GitHub Actions and Fastlane are the canonical Release Orchestrator.

Generic orchestration will be implemented as a reusable workflow in the organization `.github` repository. Participating applications will call it through a small, hand-authored workflow. Consumers will pin the reusable workflow to an immutable commit SHA. The organization Renovate preset will include a caller-specific rule that proposes these pin updates without automerging them.

The shared workflow owns runner selection, pinned toolchain setup, dependency-cache policy, temporary keychain lifecycle, explicit secret handoff, invocation of the app release contract, cleanup, and release summaries.

The caller owns application release policy: manual dispatch, validation that the selected ref is the default branch, reliance on the Terraform-managed protected-main policy, physical-device attestation, app-specific concurrency, and optional release notes.

### App release contract

Application-specific Fastlane configuration remains in each application repository. Every participating application exposes equivalent operations for:

- registering a physical device;
- synchronizing signing assets;
- exporting a release-testing build for a registered device;
- exporting an App Store-signed build without uploading it;
- uploading, processing, and assigning an Internal Release.

The central reusable workflow invokes the application’s standard TestFlight release operation instead of receiving every Xcode target, entitlement, and provisioning detail as workflow inputs.

Local TestFlight uploads may invoke the same app release operation. GitHub-only authorization checks are not reimplemented locally merely to imitate the Release Orchestrator.

### Signing authority

Fastlane Match is the sole signing authority for participating applications.

The shared private signing vault owns Apple Development and Apple Distribution certificates plus Development, Ad Hoc, and App Store provisioning profiles. Local machines may perform deliberate write-capable signing operations. CI consumes existing assets in read-only mode.

A registered-device release-testing build uses an Ad Hoc profile and Xcode’s current release-testing export method. A TestFlight build uses an App Store profile and the current App Store Connect export method.

### Internal release policy

The shared architecture distributes only to a fixed internal TestFlight group. External TestFlight review and public App Store submission are separate concerns and receive no path through this workflow.

A release is manually dispatched from protected `main`. The workflow archives the immutable dispatch revision even if `main` advances while the run is queued. Releases queue per application and are never cancelled after acceptance.

The marketing version is stored once in repository configuration and uses `major.minor.patch`. A reviewed version-bump change advances it. The build number is calculated from App Store Connect as the next build for that marketing version. Build-number collisions fail and require a new dispatch. Rebuilding the same source revision is permitted.

The workflow succeeds only after Apple finishes processing the build and confirms its association with the configured internal tester group.

### Build provenance and validation

The full source revision is embedded in distributed build metadata and reported in the release summary.

The architecture intentionally does not add a custom IPA entitlement or provisioning-profile verifier. Successful archive/export signing and App Store Connect processing are the distribution validation boundary. This keeps the common contract small and avoids duplicating Apple’s validation logic.

The workflow retains no IPA, archive, or symbol artifact after completion. App Store Connect remains the distributed-build record.

### Toolchain and dependency policy

Each application pins Xcode, Ruby, Fastlane, and package lockfiles in its own repository. The shared workflow reads those pins rather than imposing one fleet-wide application toolchain.

The organization’s configurable macOS runner selection remains available, but a release runner must be ephemeral. Persistent self-hosted runners are unsupported because Match-installed provisioning profiles and other user-scoped signing state must not survive between releases.

CI may cache dependency downloads keyed by lockfile and toolchain. It does not reuse compiled DerivedData for release archives. Swift Package Manager must honor the committed lockfile without selecting compatible updates during release.

Native dependencies are consumed as pinned, checksummed, or committed prebuilt artifacts during a normal release. Rebuilding native dependencies is a separate update operation.

### Scope of centralization

The reusable workflow belongs in the organization `.github` repository because that repository is the established delivery vehicle for shared GitHub Actions behavior. It is released and pinned like other thin-called reusables.

The workflow is opt-in. It is not rendered wholesale by the standards Copier template, and product release policy remains application-owned.

## Consequences

### Positive

- One signing authority and one release mental model apply across applications.
- Shared runner, keychain, and cleanup behavior can be fixed once.
- Application targets, extensions, capabilities, and archive details remain close to their source.
- Local signed builds and local TestFlight uploads use the same application release logic as CI.
- Internal-only distribution limits the workflow’s authority.
- Immutable workflow pins keep write-capable shared logic reviewable in each consumer.

### Negative

- Local development requires Match setup and profile synchronization.
- Adding a device or changing a capability requires an explicit signing-vault mutation.
- The shared team App Store Connect credential has cross-application scope.
- Allowing local uploads means GitHub is canonical but not exclusive.
- Omitting custom IPA verification delegates some failures to App Store Connect processing.
- No retained release artifacts are available for independent forensic analysis.

## Alternatives considered

### Xcode Cloud with automatic signing

Rejected because it would place important workflow state outside Git, reduce custom orchestration control, and introduce a second release authority alongside existing GitHub infrastructure.

### Automatic development signing with Match-managed releases

Rejected after prioritizing a single signing authority and a uniform mental model over the lower-friction local setup automatic signing provides.

### Fully duplicated application workflows

Rejected because temporary-keychain, runner, secret-handoff, cleanup, and reporting behavior would drift.

### Fully centralized Fastlane implementation

Rejected because Xcode targets, extensions, entitlements, capabilities, and archive inputs are application concerns. Passing all of them through one reusable workflow would create a fragile parameter matrix.

### Public-release workflow

Rejected because the intended distribution boundary is personal internal TestFlight. External beta and App Store release authority would be unused and unnecessarily dangerous.
