# Maintaining scrcpy-web

Most upkeep is automated. This page explains what runs on its own, and the few things that need a person.

## What runs automatically

| Workflow | When | What it does |
|---|---|---|
| `ci.yml` | every push and PR | ShellCheck, pyflakes, unit tests, image build, unit tests inside the image, smoke test |
| `publish.yml` | push to `main`, tags `v*`, **every Monday** | Tests, then builds `linux/amd64` + `linux/arm64` and pushes to `ghcr.io/<owner>/scrcpy-web`. The Monday run rebuilds with the latest Ubuntu security updates. |
| `update-scrcpy.yml` | **every Tuesday** | If scrcpy has a newer release: bumps it, builds, tests, then opens a PR (or commits and publishes when `AUTO_UPDATE` is `true`). If it fails, an issue is opened. |
| Dependabot | monthly | PRs to update the GitHub Actions used by the workflows. |

### First-time setup on GitHub

1. **Actions › General.** Allow GitHub Actions to create and approve pull requests, so the scrcpy updater can open PRs.
2. After the first publish, open the package (your profile › Packages › `scrcpy-web` › Package settings) and set **visibility to Public**. Otherwise pulls need a login.
3. Optional: set repository variable `AUTO_UPDATE` to `true` for hands-off scrcpy updates.
4. Optional, Docker Hub: set variable `DOCKERHUB_IMAGE` (e.g. `you/scrcpy-web`) and secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.

PRs opened by the updater are created with the built-in token, so GitHub doesn't run `ci.yml` on them. They've already been built and tested inside the updater run, so merging is safe.

## Updating scrcpy by hand

```sh
scripts/bump-scrcpy.sh 5.1
docker build -t scrcpy-web:test . && tests/run-unit-tests.sh && tests/smoke.sh
```

The build reads the server jar's URL and SHA-256 from upstream's `install_release.sh` at that tag, so the version is the only thing to change. If a new scrcpy release needs new build dependencies (as 4.0 did with SDL3), add them to the `scrcpy-build` stage. The runtime stage picks up whatever libraries the binary links against.

If a release renames or removes a command-line flag used in `run-scrcpy.sh`, the smoke test won't catch it, because there is no phone in CI. Skim the scrcpy release notes for removed options when merging a major update.

## Moving to a new Ubuntu LTS

The image uses the Ubuntu LTS in `ARG UBUNTU_VERSION` (26.04, supported until 2031). When the next LTS is out:

1. Change `UBUNTU_VERSION`.
2. Build and run the tests. Package names in the runtime stage (`tigervnc-standalone-server`, `novnc`, `python3-aiohttp`, `supervisor`, `tini`, `adb`) have been stable for years. The scrcpy runtime libraries are worked out automatically.
3. Release it as a new minor version.

## Releasing

1. Update `CHANGELOG.md`.
2. `git tag v2.1.0 && git push --tags`. `publish.yml` pushes `2.1.0`, `2.1`, `2` and `latest`.

## Testing with a real phone

CI can't plug in a phone, so before a release run the image against one:
- Mirroring shows up.
- Taps and swipes work.
- Rotating the phone resizes the view.
- Wake, Unlock, keys, Type and Paste, and Screenshot all work.
- Replugging the phone recovers without restarting the container.
