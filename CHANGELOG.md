# Changelog

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
