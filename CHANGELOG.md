# Changelog

## [3.2.0](https://github.com/Rubio-Enterprises/.github/compare/v3.1.1...v3.2.0) (2026-08-03)


### Features

* **renovate:** enable platformAutomerge for the automerging classes ([#188](https://github.com/Rubio-Enterprises/.github/issues/188)) ([76e7497](https://github.com/Rubio-Enterprises/.github/commit/76e7497dc93b173f1605f1846c8000472c196f7f))


### Bug Fixes

* **renovate:** parse describe-shaped copier _commit values ([#185](https://github.com/Rubio-Enterprises/.github/issues/185)) ([307c0cb](https://github.com/Rubio-Enterprises/.github/commit/307c0cb8a044d7c5946c228d4e19534af688a480))

## [3.1.1](https://github.com/Rubio-Enterprises/.github/compare/v3.1.0...v3.1.1) (2026-08-03)


### Bug Fixes

* **gates:** route the memory-heavy TypeScript gates to glue-heavy ([#167](https://github.com/Rubio-Enterprises/.github/issues/167)) ([cffb706](https://github.com/Rubio-Enterprises/.github/commit/cffb70618b15ae224cf5392032452d52e67ca38c))
* **renovate:** exempt lockFileMaintenance from the 7-day release-age soak ([#182](https://github.com/Rubio-Enterprises/.github/issues/182)) ([5156246](https://github.com/Rubio-Enterprises/.github/commit/5156246a1cd972df423c09c3ac793a7efe7a8364))

## [3.1.0](https://github.com/Rubio-Enterprises/.github/compare/v3.0.0...v3.1.0) (2026-08-03)


### Features

* adopt the canonical org pull request template ([#181](https://github.com/Rubio-Enterprises/.github/issues/181)) ([2c7df54](https://github.com/Rubio-Enterprises/.github/commit/2c7df54c300381f73f373458f26acff7c6818a86))


### Bug Fixes

* **renovate:** stop rebasing behind-base PRs out of their merge window ([#176](https://github.com/Rubio-Enterprises/.github/issues/176)) ([ce376c1](https://github.com/Rubio-Enterprises/.github/commit/ce376c1b4dc692266b10db8d450aeb3ef8d5c4b3))

## [3.0.0](https://github.com/Rubio-Enterprises/.github/compare/v2.6.1...v3.0.0) (2026-08-02)


### ⚠ BREAKING CHANGES

* the `test-py.yml` / `rust-test.yml` reusable workflows are removed. No consumer `standards.yml` thin-calls them (they were injected-only), and their rulesets are gone after #169.

### Features

* retire the central python/rust test gate workflows ([#169](https://github.com/Rubio-Enterprises/.github/issues/169)) ([c9d7341](https://github.com/Rubio-Enterprises/.github/commit/c9d7341851e136efe0567b9318297dbc344f36af))


### Bug Fixes

* **workflows:** pin an exact uv version on every setup-uv step ([#173](https://github.com/Rubio-Enterprises/.github/issues/173)) ([b3a45b0](https://github.com/Rubio-Enterprises/.github/commit/b3a45b0c98623f6534a55c899d8ed2babae7d378))

## [2.6.1](https://github.com/Rubio-Enterprises/.github/compare/v2.6.0...v2.6.1) (2026-07-30)


### Bug Fixes

* **lint-hooks:** revive the fork gate NUL-separated, warn-only at first ([#166](https://github.com/Rubio-Enterprises/.github/issues/166)) ([6c01c61](https://github.com/Rubio-Enterprises/.github/commit/6c01c619d76154cfe09dc4a83fea78f922f38d7b))

## [2.6.0](https://github.com/Rubio-Enterprises/.github/compare/v2.5.0...v2.6.0) (2026-07-29)


### Features

* **e2e:** route the reusable e2e job through its own RUNNER_E2E tier ([#156](https://github.com/Rubio-Enterprises/.github/issues/156)) ([53ba872](https://github.com/Rubio-Enterprises/.github/commit/53ba8726befdc27638269e6a239451ba457200a2))
* **typecheck-gate:** let the default branch seed the caches the gate reads ([#162](https://github.com/Rubio-Enterprises/.github/issues/162)) ([f66ffb1](https://github.com/Rubio-Enterprises/.github/commit/f66ffb1e8e1f51d55f7ae56b418e66176aa88b1a))


### Bug Fixes

* **typecheck-gate:** never let pnpm store resolution fail the gate ([#158](https://github.com/Rubio-Enterprises/.github/issues/158)) ([f9d4315](https://github.com/Rubio-Enterprises/.github/commit/f9d43151bd89f6487b5b610db54c65b9066f32a4))


### Performance Improvements

* **lint-hooks:** cap the pnpm install concurrency ([#165](https://github.com/Rubio-Enterprises/.github/issues/165)) ([8d73187](https://github.com/Rubio-Enterprises/.github/commit/8d7318733e9698869ac974a020b711fdefe8a1b9))
* **typecheck-gate:** cap pnpm install concurrency, stop writing the store cache ([#160](https://github.com/Rubio-Enterprises/.github/issues/160)) ([d533135](https://github.com/Rubio-Enterprises/.github/commit/d533135257918a08a89407a333564d558ba51bf6))


### Reverts

* unroute the reusable e2e job from the glue pool ([#154](https://github.com/Rubio-Enterprises/.github/issues/154)) ([cb2dbba](https://github.com/Rubio-Enterprises/.github/commit/cb2dbba1cdff4325054333a8bf1069a89359a3bf))

## [2.5.0](https://github.com/Rubio-Enterprises/.github/compare/v2.4.0...v2.5.0) (2026-07-27)


### Features

* **ci:** adopt canonical test-gate workflow ([#140](https://github.com/Rubio-Enterprises/.github/issues/140)) ([fe2f993](https://github.com/Rubio-Enterprises/.github/commit/fe2f993ece216711b3df814884ef8d2f8949d1e0))
* **testflight:** implement the reusable internal release workflow ([#143](https://github.com/Rubio-Enterprises/.github/issues/143)) ([b548c26](https://github.com/Rubio-Enterprises/.github/commit/b548c269fb8f6f55ab9199afe7a0ee97c149b0b3))


### Bug Fixes

* **testflight:** correct the runner model to ephemeral ([#148](https://github.com/Rubio-Enterprises/.github/issues/148)) ([03228e2](https://github.com/Rubio-Enterprises/.github/commit/03228e2aad48b8992b2838f4eedd0f92d132a830))
* **testflight:** create the run-scoped keychain, not just clean it up ([#145](https://github.com/Rubio-Enterprises/.github/issues/145)) ([0a37dcb](https://github.com/Rubio-Enterprises/.github/commit/0a37dcb6a113243dab7ee305fb9ca8f2ebd9a12f))
* **testflight:** resolve the xcode pin by version, not by guessed path ([#144](https://github.com/Rubio-Enterprises/.github/issues/144)) ([2a2bc92](https://github.com/Rubio-Enterprises/.github/commit/2a2bc92516c29b33d0eff62dcde0d90ccd454d8d))

## [2.4.0](https://github.com/Rubio-Enterprises/.github/compare/v2.3.1...v2.4.0) (2026-07-24)


### Features

* **governance:** add guarded plumbing ref publisher ([#132](https://github.com/Rubio-Enterprises/.github/issues/132)) ([7115370](https://github.com/Rubio-Enterprises/.github/commit/711537080bbc75857d49f8e6ed1a0f43fd141354))


### Bug Fixes

* **gates:** route rust tests by policy ([#131](https://github.com/Rubio-Enterprises/.github/issues/131)) ([32082b2](https://github.com/Rubio-Enterprises/.github/commit/32082b213abc6b1fc810f78b20d2cc5c0f63b154))
* **gates:** run audit on merge groups ([#137](https://github.com/Rubio-Enterprises/.github/issues/137)) ([0c13d3f](https://github.com/Rubio-Enterprises/.github/commit/0c13d3f5f63570b76e72cdd08b735580edbf7699))
* **governance:** remove automated plumbing ref rollback ([#136](https://github.com/Rubio-Enterprises/.github/issues/136)) ([2ae68ca](https://github.com/Rubio-Enterprises/.github/commit/2ae68ca8764e89cc21f6015e947f00a6d216257a))

## [2.3.1](https://github.com/Rubio-Enterprises/.github/compare/v2.3.0...v2.3.1) (2026-07-21)


### Bug Fixes

* **ci:** fingerprint uv-resolved python in the mise cache key ([#126](https://github.com/Rubio-Enterprises/.github/issues/126)) ([74707df](https://github.com/Rubio-Enterprises/.github/commit/74707dffc102886b21f0d65afe99f7da64dbb29e))

## [2.3.0](https://github.com/Rubio-Enterprises/.github/compare/v2.2.0...v2.3.0) (2026-07-21)


### Features

* **lint-format:** biome floor-delta render; retire archetype fallbacks ([#121](https://github.com/Rubio-Enterprises/.github/issues/121)) ([304937d](https://github.com/Rubio-Enterprises/.github/commit/304937dd01a3a897149fd1acfb868c1a7272b177))
* **lint-format:** ruff lints with the floor + delta render ([#120](https://github.com/Rubio-Enterprises/.github/issues/120)) ([9c68676](https://github.com/Rubio-Enterprises/.github/commit/9c686764570830c590711d3d82c83daad6aebc5f))


### Bug Fixes

* lint-format skips minimal-render repo types ([#117](https://github.com/Rubio-Enterprises/.github/issues/117)) ([e10eae0](https://github.com/Rubio-Enterprises/.github/commit/e10eae0f796b8e62e404dc374941ed368124f751))
* **lint-format:** anchor rendered biome config at the checkout root ([#123](https://github.com/Rubio-Enterprises/.github/issues/123)) ([959e1f2](https://github.com/Rubio-Enterprises/.github/commit/959e1f200498b54e7bb3cfeede8c05698d4d3a09))
* **lint:** key fork detection on repo_type after is_fork retirement ([#124](https://github.com/Rubio-Enterprises/.github/issues/124)) ([4cfbf15](https://github.com/Rubio-Enterprises/.github/commit/4cfbf152aa10c6e3dd508ff7e679ff2b2af7641a))

## [2.2.0](https://github.com/Rubio-Enterprises/.github/compare/v2.1.0...v2.2.0) (2026-07-19)


### Features

* **lint-format:** decide biome from language facets ([#114](https://github.com/Rubio-Enterprises/.github/issues/114)) ([13dbaa6](https://github.com/Rubio-Enterprises/.github/commit/13dbaa6ea316be923ba9fb0332060876a0cbb0e6))


### Bug Fixes

* **lint-format:** fail on an unreadable archetype answer ([#110](https://github.com/Rubio-Enterprises/.github/issues/110)) ([e7c4673](https://github.com/Rubio-Enterprises/.github/commit/e7c4673fa1653bd147ac179f7e3eab3050831913))
* **lint-format:** move the standards checkout out of the tree after rendering ([#112](https://github.com/Rubio-Enterprises/.github/issues/112)) ([4c002e7](https://github.com/Rubio-Enterprises/.github/commit/4c002e72aabe83174b8a130773d1d7c25ab2f789))
* move the checkout to `$RUNNER_TEMP` immediately after the render. ([4c002e7](https://github.com/Rubio-Enterprises/.github/commit/4c002e72aabe83174b8a130773d1d7c25ab2f789))
* read language facets first in lint-format and lint-hooks ([#115](https://github.com/Rubio-Enterprises/.github/issues/115)) ([0b3dd81](https://github.com/Rubio-Enterprises/.github/commit/0b3dd819fdab616fa8170264b7641f9fd7a73127))
* **workflows:** check every floating major tag, not only the newest ([#103](https://github.com/Rubio-Enterprises/.github/issues/103)) ([42ed677](https://github.com/Rubio-Enterprises/.github/commit/42ed677a0a6e38f2309d07958e2af5253ef89d96))

## [2.1.0](https://github.com/Rubio-Enterprises/.github/compare/v2.0.1...v2.1.0) (2026-07-10)


### Features

* mint the tap push token from the rubio-tap-push app ([#97](https://github.com/Rubio-Enterprises/.github/issues/97)) ([54d4e68](https://github.com/Rubio-Enterprises/.github/commit/54d4e68ea2b1166435727b7809fa37ea4f812bc0))

## [2.0.1](https://github.com/Rubio-Enterprises/.github/compare/v2.0.0...v2.0.1) (2026-07-08)


### Bug Fixes

* **release:** mint an App token so release PRs trigger the injected gates ([#91](https://github.com/Rubio-Enterprises/.github/issues/91)) ([0e19d88](https://github.com/Rubio-Enterprises/.github/commit/0e19d88046031ce8cdfb934100ff2a5e0a04e174))
* **release:** pin the app-token action by sha and drop the unused pr grant ([#95](https://github.com/Rubio-Enterprises/.github/issues/95)) ([058a55f](https://github.com/Rubio-Enterprises/.github/commit/058a55f7ea1fd494b0bb150aa6cc44e9690652f1))
* **renovate:** isolate the first-party reusable digest in its own group ([#92](https://github.com/Rubio-Enterprises/.github/issues/92)) ([1bae953](https://github.com/Rubio-Enterprises/.github/commit/1bae953133f9e4994cb421e7112fc76b62cac6fb))

## [2.0.0](https://github.com/Rubio-Enterprises/.github/compare/v1.8.1...v2.0.0) (2026-07-08)


### ⚠ BREAKING CHANGES

* **workflows:** remove copier-sync and copier-check reusables ([#87](https://github.com/Rubio-Enterprises/.github/issues/87))

### Miscellaneous Chores

* **workflows:** remove copier-sync and copier-check reusables ([#87](https://github.com/Rubio-Enterprises/.github/issues/87)) ([b3084db](https://github.com/Rubio-Enterprises/.github/commit/b3084dba482f38a064b168f94977622c06f6e132))

## [1.8.1](https://github.com/Rubio-Enterprises/.github/compare/v1.8.0...v1.8.1) (2026-07-07)


### Bug Fixes

* **gates:** exclude all rendered js-family configs from lint-format inputs ([#88](https://github.com/Rubio-Enterprises/.github/issues/88)) ([f0abf3b](https://github.com/Rubio-Enterprises/.github/commit/f0abf3bdc3a0011442148b38161cc95b9af7b209))
* **gates:** exclude the consumer biome config from lint-format inputs ([#85](https://github.com/Rubio-Enterprises/.github/issues/85)) ([31be31c](https://github.com/Rubio-Enterprises/.github/commit/31be31c54f69c7584c842a56032a6dbfb07ca077))
* **gates:** honor Pattern-D consumer lint configs in lint-format; timeout lint-hooks ([#89](https://github.com/Rubio-Enterprises/.github/issues/89)) ([c926a1c](https://github.com/Rubio-Enterprises/.github/commit/c926a1cf30b4192e866a366f55bf0c879cdb278e))

## [1.8.0](https://github.com/Rubio-Enterprises/.github/compare/v1.7.0...v1.8.0) (2026-07-07)


### Features

* **gates:** add required governance gate workflows ([#82](https://github.com/Rubio-Enterprises/.github/issues/82)) ([aeffb55](https://github.com/Rubio-Enterprises/.github/commit/aeffb555de02ebacda00a5ce4b0b02637f1a79be))
* **renovate:** replace legacy copier regex manager with native copier manager ([#83](https://github.com/Rubio-Enterprises/.github/issues/83)) ([ba96a02](https://github.com/Rubio-Enterprises/.github/commit/ba96a02e5f990c6cd171923ed29d68a9566b5a19))

## [1.7.0](https://github.com/Rubio-Enterprises/.github/compare/v1.6.0...v1.7.0) (2026-07-06)


### Features

* **lint-hooks:** support private Go module forks via app-token fetch routing ([#76](https://github.com/Rubio-Enterprises/.github/issues/76)) ([3791fb0](https://github.com/Rubio-Enterprises/.github/commit/3791fb097d198ad98b0ac6568d74871b7bdf8cc5))
* **lint-hooks:** support private Go module forks via app-token fetch routing ([#79](https://github.com/Rubio-Enterprises/.github/issues/79)) ([15abe6c](https://github.com/Rubio-Enterprises/.github/commit/15abe6c85fdc68958f7d370b5f677bdb13cf159e))


### Bug Fixes

* **copier-sync:** stage newly created files so template additions ship in sync PRs ([#81](https://github.com/Rubio-Enterprises/.github/issues/81)) ([3acb337](https://github.com/Rubio-Enterprises/.github/commit/3acb337f5c7905bda588781bf5b2de91193ad15b))

## [1.6.0](https://github.com/Rubio-Enterprises/.github/compare/v1.5.0...v1.6.0) (2026-06-29)


### Features

* **secret-scan:** verified-only trufflehog, remove path-suppression opt-out ([#71](https://github.com/Rubio-Enterprises/.github/issues/71)) ([8850f59](https://github.com/Rubio-Enterprises/.github/commit/8850f591cfdefa674b9db52602848d27e520dcb7))

## [1.5.0](https://github.com/Rubio-Enterprises/.github/compare/v1.4.6...v1.5.0) (2026-06-28)


### Features

* read standards via standards-reader app token (private-repo prep) ([3c417b5](https://github.com/Rubio-Enterprises/.github/commit/3c417b572e1c43d1f46c5c65820a87fcca932848))

## [1.4.6](https://github.com/Rubio-Enterprises/.github/compare/v1.4.5...v1.4.6) (2026-06-26)


### Bug Fixes

* scope homebrew formula revision bumps ([#67](https://github.com/Rubio-Enterprises/.github/issues/67)) ([54f8465](https://github.com/Rubio-Enterprises/.github/commit/54f84651d503d1b026d59c48063f8049d3f6fc8a))

## [1.4.5](https://github.com/Rubio-Enterprises/.github/compare/v1.4.4...v1.4.5) (2026-06-25)


### Bug Fixes

* **lint-hooks:** avoid fork diff merge-base lookup ([#65](https://github.com/Rubio-Enterprises/.github/issues/65)) ([0009863](https://github.com/Rubio-Enterprises/.github/commit/0009863785b4893f939283c720dc40676659f397))

## [1.4.4](https://github.com/Rubio-Enterprises/.github/compare/v1.4.3...v1.4.4) (2026-06-25)


### Bug Fixes

* **lint-hooks:** scope fork checks to changed files ([#64](https://github.com/Rubio-Enterprises/.github/issues/64)) ([e285bb7](https://github.com/Rubio-Enterprises/.github/commit/e285bb7d2bb09dc1ab9f37b68eb734d63e7900b8))
* **renovate:** freeze template-owned action pins in rendered standards.yml ([#61](https://github.com/Rubio-Enterprises/.github/issues/61)) ([758df14](https://github.com/Rubio-Enterprises/.github/commit/758df14c5c5a9061382714c189af7919b6c300b7))

## [1.4.3](https://github.com/Rubio-Enterprises/.github/compare/v1.4.2...v1.4.3) (2026-06-23)


### Bug Fixes

* **secret-scan:** float standards ref to audit/v1 to remove pin asymmetry ([#54](https://github.com/Rubio-Enterprises/.github/issues/54)) ([99f965d](https://github.com/Rubio-Enterprises/.github/commit/99f965d1a2f7f2ab351ffc3daeeab77c8fff4271))

## [1.4.2](https://github.com/Rubio-Enterprises/.github/compare/v1.4.1...v1.4.2) (2026-06-23)


### Bug Fixes

* **lint-hooks:** route swift-* archetypes to macOS so swift linters run ([#52](https://github.com/Rubio-Enterprises/.github/issues/52)) ([66858dd](https://github.com/Rubio-Enterprises/.github/commit/66858dd3cc53bdd648d7a5ece8a0fdbed392cbf5))

## [1.4.1](https://github.com/Rubio-Enterprises/.github/compare/v1.4.0...v1.4.1) (2026-06-23)


### Bug Fixes

* **workflows:** rename bump-homebrew-git reusable to bump-brew ([#50](https://github.com/Rubio-Enterprises/.github/issues/50)) ([35e5dcf](https://github.com/Rubio-Enterprises/.github/commit/35e5dcf0ec856a13337ab9fea593bb41c2ccc960))

## [1.4.0](https://github.com/Rubio-Enterprises/.github/compare/v1.3.7...v1.4.0) (2026-06-23)


### Features

* **workflows:** add bump-homebrew-git reusable for :git formulae ([#49](https://github.com/Rubio-Enterprises/.github/issues/49)) ([8b1fbad](https://github.com/Rubio-Enterprises/.github/commit/8b1fbadd2c37cf6792b4ea46cac3a28627b00c64))


### Bug Fixes

* **secret-scan:** pin trufflehog installer script to the release tag ([#46](https://github.com/Rubio-Enterprises/.github/issues/46)) ([788477b](https://github.com/Rubio-Enterprises/.github/commit/788477bbc1b4027e812563ec8e27f9aca76fa1c7))

## [1.3.7](https://github.com/Rubio-Enterprises/.github/compare/v1.3.6...v1.3.7) (2026-06-23)


### Bug Fixes

* **secret-scan:** run trufflehog via CLI binary instead of the docker action ([#43](https://github.com/Rubio-Enterprises/.github/issues/43)) ([752038e](https://github.com/Rubio-Enterprises/.github/commit/752038e37d0020434d40919ec712e09e0c20a9a0))

## [1.3.6](https://github.com/Rubio-Enterprises/.github/compare/v1.3.5...v1.3.6) (2026-06-23)


### Bug Fixes

* point the audit reusable at relocated skills/audit-standards paths ([#41](https://github.com/Rubio-Enterprises/.github/issues/41)) ([ecebd74](https://github.com/Rubio-Enterprises/.github/commit/ecebd747c477045b6c5b5a28832da87d50b0035e))

## [1.3.5](https://github.com/Rubio-Enterprises/.github/compare/v1.3.4...v1.3.5) (2026-06-14)


### Bug Fixes

* **copier-sync:** make sync-PR creation robust to missing labels ([#38](https://github.com/Rubio-Enterprises/.github/issues/38)) ([3cbcf02](https://github.com/Rubio-Enterprises/.github/commit/3cbcf024b3304a1b45d4d5151da74380347019e4))

## [1.3.4](https://github.com/Rubio-Enterprises/.github/compare/v1.3.3...v1.3.4) (2026-06-14)


### Bug Fixes

* **copier-check:** drop invalid --overwrite flag from copier update ([#37](https://github.com/Rubio-Enterprises/.github/issues/37)) ([9da0413](https://github.com/Rubio-Enterprises/.github/commit/9da0413774a70f30942eb14678de7a755049d699))
* **renovate:** disable mise manager — .mise.toml is template-owned ([#35](https://github.com/Rubio-Enterprises/.github/issues/35)) ([737d076](https://github.com/Rubio-Enterprises/.github/commit/737d076571fb65af2e07088859e2d7e4920323dd))

## [1.3.3](https://github.com/Rubio-Enterprises/.github/compare/v1.3.2...v1.3.3) (2026-06-14)


### Bug Fixes

* mint workflow-scoped App token in copier-sync (push standards.yml) ([#33](https://github.com/Rubio-Enterprises/.github/issues/33)) ([9427f9a](https://github.com/Rubio-Enterprises/.github/commit/9427f9a77e405305672adcfa2a02e38461774819))

## [1.3.2](https://github.com/Rubio-Enterprises/.github/compare/v1.3.1...v1.3.2) (2026-06-14)


### Bug Fixes

* add Install uv step to copier-check reusable workflow ([#31](https://github.com/Rubio-Enterprises/.github/issues/31)) ([2bf06fc](https://github.com/Rubio-Enterprises/.github/commit/2bf06fc9157acb540f54f23af36126c03a68f600))

## [1.3.1](https://github.com/Rubio-Enterprises/.github/compare/v1.3.0...v1.3.1) (2026-06-14)


### Bug Fixes

* pin mise version + onboard dot-github to renovate ([#29](https://github.com/Rubio-Enterprises/.github/issues/29)) ([9833367](https://github.com/Rubio-Enterprises/.github/commit/98333676562c57801bd5b62c31bec7f5fb576667))

## [1.3.0](https://github.com/Rubio-Enterprises/.github/compare/v1.2.0...v1.3.0) (2026-06-14)


### Features

* add copier-check reusable workflow (blocking drift gate) ([#24](https://github.com/Rubio-Enterprises/.github/issues/24)) ([e557d55](https://github.com/Rubio-Enterprises/.github/commit/e557d55210aff7de21879b0eeac7997f6ec4c82d))

## [1.2.0](https://github.com/Rubio-Enterprises/.github/compare/v1.1.21...v1.2.0) (2026-06-10)


### Features

* **renovate,audit:** supply-chain hardening + lockfile integrity CI gate ([#13](https://github.com/Rubio-Enterprises/.github/issues/13)) ([bca89b9](https://github.com/Rubio-Enterprises/.github/commit/bca89b919b6a5b97b11a8f64de13431f91b291ae))


### Bug Fixes

* **copier-sync:** drop invalid --overwrite flag from copier update ([#18](https://github.com/Rubio-Enterprises/.github/issues/18)) ([d1ec0a0](https://github.com/Rubio-Enterprises/.github/commit/d1ec0a06928a064a826b4102124b7b6e8f8cd88e))
* **copier-sync:** install copier before `copier update` to fix exit 127 ([#17](https://github.com/Rubio-Enterprises/.github/issues/17)) ([864ae74](https://github.com/Rubio-Enterprises/.github/commit/864ae74b348e28869c9e91ea149672de7a306531))
* **workflows:** route mise pipx installs through uv to keep minimum_release_age on ([#19](https://github.com/Rubio-Enterprises/.github/issues/19)) ([7acc76c](https://github.com/Rubio-Enterprises/.github/commit/7acc76c731d515d88b32158c14a658d2af1ccc6b))
