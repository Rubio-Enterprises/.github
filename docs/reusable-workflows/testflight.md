# Apple TestFlight reusable workflow contract

- **Status:** Implemented in `.github/workflows/testflight.yml`
- **Reference implementation:** VVTerm

## Purpose

The reusable workflow provides the generic GitHub Actions half of the organization’s Apple Internal Release architecture. It prepares a clean macOS runner, establishes a temporary signing environment, invokes a standard application-owned Fastlane operation, and reports whether Apple processed and distributed the build to the application’s configured internal tester group.

It does not know an application’s Xcode project structure, targets, extensions, entitlements, CloudKit containers, bundle identifiers, or provisioning-profile names.

## Ownership boundary

### Reusable workflow responsibilities

The reusable workflow owns:

- checkout of the immutable caller revision;
- selection of the organization’s configurable macOS runner;
- loading the application’s pinned Xcode version;
- setup of the application’s pinned Ruby and Bundler environment;
- caching dependency downloads without caching compiled release products;
- creation, unlocking, search-list insertion, restoration, and deletion of a run-scoped keychain;
- explicit mapping of the App Store Connect and Match secrets required by the release operation;
- invocation of the standard application TestFlight release operation;
- cleanup that runs on success or failure;
- a generic GitHub summary containing the source revision and job outcome.

The runner selected through the organization’s configurable macOS runner variable may be ephemeral or persistent self-hosted.

A temporary keychain does not isolate everything Match touches: Match also installs provisioning profiles into user-scoped locations that outlive the job. On an ephemeral runner that is harmless, because the machine is discarded. On a persistent runner it is not, so the reusable workflow captures the runner’s default keychain and search list before the release and restores them afterwards, deleting the run-scoped keychain on success or failure. Persistent runners are supported only through that capture-and-restore path — a release must never leave a shared machine’s keychain search list reordered.

### Caller responsibilities

The app-local caller owns:

- a manual dispatch trigger;
- a required boolean `physical_device_tested` input;
- an optional single-line `changelog` input mapped to `TESTFLIGHT_CHANGELOG`;
- rejection of tags and non-default branches;
- physical-device attestation;
- per-application concurrency configured to queue rather than cancel;
- minimal permissions;
- immutable pinning of the reusable workflow;
- explicit secret mapping rather than secret inheritance.

Protected-main policy is the prior-CI evidence. The caller does not query and reinterpret individual check runs.

### Application responsibilities

The application repository owns:

- the standard Fastlane release operations;
- the fixed internal TestFlight group name;
- App Store Connect application lookup;
- marketing-version and fallback-build metadata;
- next-build-number calculation;
- Match application identifiers and profile types;
- Xcode project, scheme, target, extension, signing, and export details;
- optional release notes, with an empty value allowed;
- waiting for App Store Connect processing;
- associating the processed build with the fixed internal group **where association is required** — see “Internal group association” below;
- appending application version, build number, bundle identifier, internal group, and distribution outcome to `GITHUB_STEP_SUMMARY` when that environment variable is available.

## Executable application contract

Every caller provides the same repository layout and command contract:

- the macOS runner is selected from `vars.RUNNER_MACOS`, defaulting to `["macos-15"]` when unset;
- the pinned Xcode version is stored in root `.xcode-version`;
- the pinned Ruby version is stored in root `.ruby-version`;
- the Bundler definition and lockfile are stored at the repository root;
- Fastlane is invoked from the repository root;
- the release command is `bundle exec fastlane ios testflight_release`;
- optional release notes are supplied through `TESTFLIGHT_CHANGELOG`;
- source revision is supplied through the standard `GITHUB_SHA` environment variable in CI and resolved from Git locally;
- the application lane writes detailed release information directly to `GITHUB_STEP_SUMMARY` when running in GitHub Actions;
- a zero exit status means the build finished processing and is available to the fixed internal group;
- any archive, upload, processing, or group-association failure exits non-zero.

### Internal group association

An internal TestFlight group configured with `hasAccessToAllBuilds = true` receives every processed build automatically, and the build’s `betaGroups` relationship stays empty. For such a group, a per-build association call is unnecessary and an association *assertion* fails on every otherwise-successful release.

Applications therefore associate and assert only when their internal group does not already have access to all builds. Where the group does, reaching `processingState == VALID` is the complete success condition. VVTerm — the reference implementation — uses a `hasAccessToAllBuilds = true` group and deliberately performs no association call.

The central workflow does not parse application metadata or require an additional result-file protocol.

## Required secret contract

The caller passes only the named secrets required by the application release operation:

| Caller secret | Fastlane environment | Purpose |
|---|---|---|
| `APP_STORE_CONNECT_API_KEY_ID` | `ASC_KEY_ID` | App Store Connect key identifier |
| `APP_STORE_CONNECT_API_KEY_ISSUER_ID` | `ASC_ISSUER_ID` | App Store Connect issuer identifier |
| `APP_STORE_CONNECT_API_KEY_CONTENT_BASE64` | `ASC_KEY_CONTENT_BASE64` | Base64-encoded App Store Connect private key |
| `MATCH_GIT_URL` | `MATCH_GIT_URL` | Private signing-vault repository URL |
| `MATCH_GIT_BASIC_AUTHORIZATION` | `MATCH_GIT_BASIC_AUTHORIZATION` | Signing-vault repository authentication |
| `MATCH_PASSWORD` | `MATCH_PASSWORD` | Match encryption password |

The architecture uses one shared team App Store Connect key across participating applications. Secrets remain repository-scoped and are mapped explicitly into the reusable workflow.

## Release behavior

1. The caller validates that the dispatch is from the default branch and that physical-device testing was confirmed.
2. The reusable workflow checks out the dispatch revision, not the branch’s later head.
3. The workflow selects the configured macOS runner and application-pinned toolchain.
4. The workflow creates an isolated temporary keychain.
5. The application’s TestFlight release operation installs Match assets in read-only mode.
6. The application determines the next App Store Connect build number for its repository-owned marketing version.
7. The application performs a clean archive and App Store Connect export while honoring committed dependency lockfiles.
8. The application uploads the build and waits for Apple processing.
9. The application assigns the build to its fixed internal group and confirms the association.
10. The reusable workflow restores signing state, removes transient build output, and records the outcome.

A source-identical rebuild is allowed and receives a new build number. A build-number collision is not retried automatically.

## Validation boundary

The reusable workflow does not inspect exported IPA entitlements or provisioning-profile internals. A valid archive/export signature and successful App Store Connect processing are the accepted distribution validation boundary.

The workflow retains no release artifact after completion.

## Versioning and consumer updates

The workflow is released through the `.github` repository’s existing release process. Consumers pin the workflow to an immutable commit SHA with the compatible major release recorded for Renovate. They do not call a floating tag directly.

The organization Renovate preset must include a rule scoped to the hand-authored TestFlight callers and the `Rubio-Enterprises/.github` dependency. That rule disables automerge and isolates the update from general GitHub Actions groups, so shared write-capable logic changes only through a reviewed consumer commit.

## Non-goals

The reusable workflow does not:

- register devices or mutate signing assets from CI;
- create Apple Developer or App Store Connect resources;
- deploy CloudKit schemas;
- run external TestFlight review;
- submit an application to the public App Store;
- retain IPA, archive, or symbol artifacts;
- enforce TestFlight build-expiry refreshes;
- replace application-owned Fastlane logic with workflow inputs.
