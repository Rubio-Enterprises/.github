# Release orchestration glossary

## App Store application identity

The immutable association between an Apple team, a host application bundle identifier, and the App Store Connect application record that receives distributed builds.

## Target signing identity

The association between an Apple team, one executable target’s bundle identifier, entitlements, and provisioning profiles. A host application and each embedded extension have separate target signing identities, while only the host has the App Store application identity that receives the combined build.

## Apple resource bootstrap

The deliberate, one-time creation of application identifiers, capabilities, cloud containers, App Store Connect records, and tester groups before automated signing or release work begins.

## Signing authority

The system that owns the certificates and provisioning profiles accepted for local device builds and distributed builds. A release process has one signing authority even when several tools consume its assets.

## Signing vault

The encrypted shared store used by the signing authority. It contains signing certificates and application-specific provisioning profiles, but not application source code or release policy.

## App release contract

The common set of release operations every participating Apple application exposes. The contract separates application-specific archive and distribution knowledge from shared release orchestration.

## Release Orchestrator

The system that authorizes a release, prepares the build environment, provides signing credentials, invokes the app release contract, and reports the final distribution result.

## Device attestation

A human confirmation that a clean, release-configured build was exported for registered-device testing, installed on physical hardware, and launched successfully.

Device attestation proves build, signing, installation, and launch behavior. It does not claim that every product flow was manually tested.

## Release-testing build

A Release-configured application exported for registered physical devices with distribution signing. It is used for device attestation before TestFlight distribution.

## Internal Release

A build uploaded to TestFlight, processed successfully by Apple, and made available to a configured internal tester group. An Internal Release is not an external beta or a public App Store release.

## Reference implementation

The first application to implement and validate a shared contract. Later applications adopt the proven contract rather than independently redefining it.

## CloudKit Production environment

The CloudKit environment used by distribution-signed applications, including Ad Hoc release-testing, TestFlight, and App Store builds. Deploying a schema to this environment does not make an application public or submit it to App Review.
