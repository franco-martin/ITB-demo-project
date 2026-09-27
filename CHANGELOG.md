# Changelog

## 3.0.0 (unreleased)

### Breaking

- `build.py` removed; use `plan.py` and build in CI.
- The custom build and push commands and `IMAGETREE_BUILD_COMMAND` removed.
- `bump.py --generate-build-file` removed.
- The catalog and `list.py --build-catalog` removed.
- Logs go to stderr.
- `utils.find_tag_in_registry(image, version)`.
- `installer.py` removed; follow the README's first-install steps.

### Added

- `plan.py`, which writes the images to build, in order, to `plan.json`.
- `pipeline.py`, which turns `plan.json` into a GitLab child pipeline rendered from a template (the `.imagetree-image` and `.imagetree-manifest` jobs with `%NAME%`-style placeholders), or into GitHub Actions matrices, one per stage.
- Multi-platform images: `IMAGETREE_PLATFORMS` and `createroot.py`/`new.py --platforms`, inherited by children. Without `--platforms`, `createroot.py`/`new.py` store `IMAGETREE_PLATFORMS` in `index.yml`. Each platform is built in its own job and merged into the tag by a manifest job.
- `list.py -f mermaid`, `--markdown`, `--update-file`, `--check` and `--plan`, plus the README image tree with links. Outside CI, the links' base url comes from `IMAGETREE_LINK_BASE` or the git `origin` remote. `createroot.py`, `new.py`, `bump.py` and `delete.py` keep that tree up to date (`IMAGETREE_TREE_FILE`, default `README.md`). The tree's table lists each image's platforms from `index.yml`.
- Bearer-token registry auth and pagination.
- Private registry credentials: auth files, helpers and the CA setting.
- `Containerfile` support. New images get a `Containerfile`; an image without one is still built from its `Dockerfile`, and when an image has both, the `Containerfile` wins.
- `examples/` for GitLab and GitHub, building with kaniko (`ghcr.io/osscontainertools/kaniko:v1.28.5`), and `docs/private-registries.md`.
- `AGENTS.md` with instructions for AI coding agents.

### Fixed

- Namespaced registries.
- Registries with a port.
- Wrong tags when an image name contained the image directory name.
- The integration cleanup.
- Invalid escape-sequence warnings.

### Migration

Follow the README's update steps; replace CI jobs that used `build.py` with the examples; delete `catalog/`, `catalog.js` and `installer.py`.
