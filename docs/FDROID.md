<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Purpose: Official F-Droid metadata, release workflow and publication limits. -->

# F-Droid

BlockProbe is a general website-filter checker. Its built-in regulator feeds
focus on gambling domains, while custom-site scans support other user-selected
checks such as parental controls and adult-content filtering. The app tests
domain responses on the device's network path; it does not inspect complete
sites or certify filtering across every app and browser.

BlockProbe publishes through the official F-Droid repository. F-Droid builds
the app from the tagged source and signs the published APK with its own key;
upstream APKs are never uploaded to F-Droid.

## Canonical metadata

The only metadata file in this repository is
[`fdroid/io.github.ctterrlt.blockprobe.yaml`](../fdroid/io.github.ctterrlt.blockprobe.yaml).
F-Droid requires `metadata/<app-id>.yml` inside `fdroiddata`, so
`scripts/fdroid_submit.py` renders the canonical file to that name only inside
the merge-request branch. Do not commit a `.yml` metadata file here.

`ArchivePolicy: 1` keeps only the current version in F-Droid's main repository.
When a newer version is published, F-Droid moves the older APK and metadata to
its archive; it does not delete the old source tag, commit or GitHub release.

## Release flow

1. Update `src/blockprobe/VERSION`, `CHANGELOG.md` and the source.
2. Commit and push the release changes to the repository's default branch.
3. Tag with `v<version>` (for example `v1.3.0-beta`; the older `v-<version>`
   form is also accepted) and push the tag.
4. After the GitHub `Release` workflow completes successfully for the tag, the
   `F-Droid` workflow renders and validates the metadata, then opens or updates
   one `fdroiddata` merge request using `GITLAB_TOKEN`. Both the recipe and
   source commit come from the completed Release workflow's exact commit SHA.
5. F-Droid maintainers review, build, sign and publish. The workflow cannot
   merge the merge request or upload, replace or delete an F-Droid release.

If F-Droid maintainers change the build recipe during review, copy those
changes back into `fdroid/io.github.ctterrlt.blockprobe.yaml` before the next
release. The next submission regenerates the metadata from that canonical file.

The workflow never creates, edits or deletes GitHub releases, tags or commits.
The separate `Release` workflow owns GitHub release assets.

## One-time setup

1. Create a GitLab account and fork
   [`fdroid/fdroiddata`](https://gitlab.com/fdroid/fdroiddata).
2. Create a personal access token with the `api` scope. The token only needs to
   create or update the fork, branch and merge request.
3. Add repository secrets in GitHub:
   - `GITLAB_TOKEN` (required for submission)
   - `GITLAB_USERNAME` (optional; defaults to the token's user)
   - Android signing secrets listed in [PACKAGING.md](PACKAGING.md) (required
     for tagged Release builds)
4. Run the `F-Droid` workflow manually from the default branch, or push a
   release tag and let the successful `Release` workflow trigger it.

The automatic submission job binds metadata and the F-Droid source commit to
the exact commit reported by the successful Release workflow. This remains
consistent if the release tag is later moved. The job fails with a clear setup
error when `GITLAB_TOKEN` is absent. It executes the trusted default-branch
script and reads the release commit's metadata as data before contacting GitLab.

## Local checks

Validate the canonical metadata without writing a `.yml` file:

```console
python scripts/fdroid_submit.py --check
```

Render the exact file that would be submitted:

```console
python scripts/fdroid_submit.py --output /tmp/io.github.ctterrlt.blockprobe.yml
```

Check it with the same tools F-Droid uses:

```console
git clone --depth 1 --filter=blob:none --sparse \
  https://gitlab.com/fdroid/fdroiddata.git /tmp/fdroiddata
cd /tmp/fdroiddata
git sparse-checkout set metadata config
python /path/to/BlockProbe/scripts/fdroid_submit.py \
  --output metadata/io.github.ctterrlt.blockprobe.yml
fdroid readmeta
fdroid lint io.github.ctterrlt.blockprobe
```

`fdroid readmeta` and `fdroid lint` need the `fdroiddata` `config/` directory,
including its category and anti-feature definitions.

Test the Android build helper locally:

```console
bash fdroid/build.sh prepare
bash fdroid/build.sh build
```

The helper needs Python 3.10 or newer for the Flet CLI, JDK 17 or newer, the
Android SDK, and network access. It builds in
`/tmp/blockprobe-fdroid-build` and copies the APK to
`build/flet/apk/blockprobe.apk`.

## Build recipe

`fdroid/build.sh prepare` installs `flet[cli]==1.0.5`, copies the source to a
clean directory outside the git tree, and runs `flet build apk` with Python
3.14, the release version/code and readable Python sources. `build` copies the
single APK to the path declared in the metadata. The recipe checks out Flutter
3.44.8 from F-Droid's `flutter` srclib and puts it on `PATH`; Flet then uses
that SDK while its `prebuild` step downloads the Android Python runtime and
wheels. `build` copies the prepared APK offline.

The build entry installs `python3-venv` through `sudo` because the F-Droid
build VM does not guarantee it. The extended timeout covers Android runtime
and SDK setup.

## Risks and limits

`src/blockprobe/VERSION` is the canonical source version. Confirm the build
version and monotonically increasing Android version code from the produced
APK before submitting metadata; an older installed APK does not identify the
current source revision.

- This is the first Flet/`serious_python` app submitted to `fdroiddata`. The
  Android recipe may need changes during F-Droid review.
- The universal APK is large because it contains Python, Flutter and the
  `arm64-v8a`, `armeabi-v7a` and `x86_64` ABIs. The current local debug build
  is about 159 MiB; per-ABI splits would reduce each download but require a
  different metadata recipe.
- F-Droid signs the published APK. The debug signature on local builds is not
  the publication signature.
- F-Droid has no upload, replace or delete API. Publication is always a
  maintainer action on the merge request.
