# Changelog

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
