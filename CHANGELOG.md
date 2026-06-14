# Changelog

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
