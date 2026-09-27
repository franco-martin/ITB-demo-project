# AGENTS.md

Instructions for AI coding agents working in this repository.

This repository manages a tree of container images with [image tree builder](https://gitlab.com/franco-martin/image-tree-builder). Each image is built from its parent, and every image has a version. CI works out which images need building and builds them. Nothing is built or pushed from a workstation.

## Quick reference

Everything you need is in this file. Don't run `--help` or read the scripts; go straight to the command. Run from the repository root. If there's a `venv/` directory, use `venv/bin/python` instead of `python3`.

An image's **path** is its position in the tree, like `ubuntu/python`. **Root images** are the top level, and they're built from images outside this repository.

| Request | Command | Notes |
|---|---|---|
| Show the tree with versions | `python3 ./list.py --show-versions` | Paths are the names joined with `/` |
| List the root images | See the commands below the table | One name per line |
| Bump one image | `python3 ./bump.py -t <path> --patch` | Its children get the same bump, and their `FROM` lines follow |
| Bump the root images (every image) | See the commands below the table | Bumping every root bumps the whole tree |
| Bump an image, but only patch its children | `python3 ./bump.py -t <path> --minor --patch-children` | Also works with `--major` |
| Preview a bump | Add `--dry-run` to any `bump.py` command | Writes nothing |
| Add a root image | `python3 ./createroot.py --from <image:tag> --name <name> --description "<text>"` | See [Creating a new image](#creating-a-new-image). `<image:tag>` must be a static tag, never `latest` |
| Add a child image | `python3 ./new.py --parent <path> --name <name> --description "<text>"` | See [Creating a new image](#creating-a-new-image) |
| Change an image | Edit its `Containerfile`, then bump it | See [Changing an image](#changing-an-image) |
| Change a root image's base | Edit the `FROM` line of `images/<root>/Containerfile`, then bump the root | Its children follow automatically. Keep the `FROM` pinned to a static tag, never `latest` |
| Delete an image | `python3 ./delete.py -t <path> --dry-run`, then without `--dry-run` | Also deletes all of its children. Never pass `--from-registry`, so the registry isn't touched |
| See what CI would build | `python3 ./plan.py` | Lists the tags on stdout and writes `plan.json`. Use `--all` without registry access |
| Tree with the planned images highlighted, for a merge request | `python3 ./plan.py && python3 ./list.py --plan plan.json --markdown` | Paste the output into the merge request |
| Check the README tree | `python3 ./list.py --check --update-file` | Exit 0 means it's current |
| Regenerate the README tree | `python3 ./list.py --update-file` | Only needed after edits no script makes, like a root's `FROM` |
| Set up the repository for GitHub or GitLab | See [Setting up the repository](#setting-up-the-repository-for-github-or-gitlab) | Asks which platforms and base image to use, then creates `.env`, the CI files and the first root image |
| Preview the GitLab child pipeline | `python3 ./plan.py --all && python3 ./pipeline.py -f gitlab` | Prints the pipeline CI would run |

```sh
# List the root images
grep -E '^[^ #][^:]*:' index.yml | cut -d: -f1

# Bump every root image, and so every image (use the level you were asked for)
for root in $(grep -E '^[^ #][^:]*:' index.yml | cut -d: -f1); do python3 ./bump.py -t "$root" --patch || break; done
```

- **Bump level.** `bump.py` needs exactly one of `--major`, `--minor` or `--patch`. If the request doesn't say which, use `--patch` and say so in your reply.
- **Plan after changing versions.** After a bump, create or delete, run `python3 ./plan.py` and include its output in your reply.
- **Not possible with the scripts:** renaming an image, moving it to another parent, and changing an existing image's description or platforms. Don't edit `index.yml` to do these; tell the maintainer.
- **Not allowed:** deleting images from the registry (`delete.py --from-registry`). Leave it to the maintainer.

## Layout

| Path | What it is | Who edits it |
|---|---|---|
| `images/<path>/Containerfile` | The build file for one image, e.g. `images/ubuntu/python/Containerfile`. Older images may have a `Dockerfile` instead | You, for image changes |
| `images/<path>/context/` | The build context for that image | You, for image changes |
| `index.yml` | The tree: every image's parent, version and description | Only through the scripts below |
| `README.md` | Project docs; the image tree sits between `<!-- image-tree:start -->` and `<!-- image-tree:end -->` | You, outside the markers; `list.py` inside them |
| `.env` | Registry settings and credentials, loaded by every script | The maintainer. You create it only during [setup](#setting-up-the-repository-for-github-or-gitlab), when it doesn't exist. Never commit it or print it |
| `.gitlab-ci.yml`, `.gitlab/`, `.github/workflows/` | CI that plans and builds the images | You, when asked |
| `*.py`, `requirements.txt`, `sample-envs`, `examples/`, `docs/`, `CHANGELOG.md`, `version.txt`, `AGENTS.md` | The image tree builder release | Nobody. Updating the release replaces these files, so edits are lost. Report bugs instead |

An image's path is its position in the tree, e.g. `ubuntu/python` is the `python` image whose parent is `ubuntu`. Every script takes this path.

## Setup

- Python 3.11 or newer. Use the repository's virtual environment if there is one (`source ./venv/bin/activate`), otherwise `python3 -m pip install -r requirements.txt`.
- Run every script from the repository root.
- Logs and progress go to stderr; the output you asked for goes to stdout. Set `LOG_LEVEL=DEBUG` for more detail.
- Paths in this file assume the default image directory, `images/`. If `grep -c '^IMAGETREE_IMAGE_DIR=' .env` prints `1`, ask the maintainer which directory it is instead of reading `.env`.
- If `.env` sets `IMAGETREE_CUSTOM_LIST_TAGS_CMD`, `plan.py` runs that command to look up tags instead of calling the registry. If it fails, report its output; don't change the command.

## Commands

Every option each script takes is listed here, so there's no need to run `--help`. Pass every option shown without brackets: no option is enforced as required, and a missing one fails with a confusing error instead of a usage message. `--log-level` is deprecated; use `LOG_LEVEL=DEBUG`.

| Script | Options | What it does |
|---|---|---|
| `list.py` | `[-f tree\|json\|mermaid] [--json] [--show-versions] [--no-versions] [--no-sources] [--direction LR\|RL\|TD\|BT] [--markdown] [--link-base URL] [--update-file [FILE]] [--check] [--plan PLAN]` | Shows the tree (default `tree`). `--update-file` rewrites the README block (default `README.md`); add `--check` to only verify it. `--plan plan.json` highlights the planned images |
| `createroot.py` | `--from IMAGE --name NAME [--version X.Y.Z] [--description TEXT] [--platforms LIST]` | Adds a root image built from an external image |
| `new.py` | `--parent PATH --name NAME [--version X.Y.Z] [--description TEXT] [--platforms LIST]` | Adds a child image under an existing image |
| `bump.py` | `-t PATH (--major \| --minor \| --patch) [--patch-children] [--dry-run]` | Bumps an image by exactly one level. Its children get the same bump, or only a patch bump with `--patch-children`, and their `FROM` lines are updated |
| `delete.py` | `-t PATH [--dry-run] [--from-registry [--all-tags]]` | Removes an image and its children from `index.yml` and `images/`. `--from-registry` and `--all-tags` also delete tags from the registry: maintainer only, never use them |
| `plan.py` | `[-t PATH [--include-children]] [--all] [-o FILE]` | Writes what CI would build to `plan.json` (or `-o FILE`) and lists those tags on stdout, in build order. Reads the registry unless `--all` is set |
| `pipeline.py` | `-f gitlab\|github [-p PLAN] [--template FILE] [-o FILE]` | Turns `plan.json` into a GitLab child pipeline (from `.gitlab/imagetree-pipeline.yml`) or GitHub matrix outputs. CI runs it; you only need it to check a template |

Short forms: `-f` is `--from` in `createroot.py` and `--format` in `list.py`/`pipeline.py`; `-p` is `--parent` in `new.py` and `--plan` in `pipeline.py`; `-n` is `--name`, `-v` is `--version`, `-d` is `--description`, `-t` is `--target`, `-o` is `--output`.

`LIST` is a comma separated list of platforms like `linux/amd64,linux/arm64`. Leave `--platforms` out unless asked: an image then gets `IMAGETREE_PLATFORMS` stored in `index.yml`, or uses its parent's platforms when it isn't set, and a child can't ask for a platform its parent isn't built for.

## Creating a new image

1. **Pick the parent.** Run `python3 ./list.py` and find the image to build on. Use its full path, e.g. `ubuntu/python`. To build on an image from outside this repository (like `docker.io/ubuntu:24.04`), create a root image instead.
2. **Pick the name.** Lowercase letters, digits, `-`, `_` and `.` only; no colons or slashes (`ubuntu:24.04` becomes `ubuntu24-04`). It must not already exist under that parent: the script fails with `FileExistsError` and changes nothing.
3. **Create it.**
   - Child image: `python3 ./new.py --parent ubuntu --name node --description "Node.js 22 on ubuntu"`
   - Root image: `python3 ./createroot.py --from docker.io/ubuntu:24.04 --name ubuntu24-04 --description "Ubuntu 24.04 base"`

   `--from` must be a static tag, never `latest` (or another floating tag like `stable`). In a stricter environment — ask the maintainer if unsure — pin the tag **and** its digest instead, e.g. `--from docker.io/ubuntu:24.04@sha256:<digest>`.

   Leave out `--version` to start at `1.0.0`. A version you pass must be strict semver `X.Y.Z` without leading zeros (`24.4.0`, not `24.04.0`).
4. **Check what it created.** The image gets a new entry in `index.yml` and a directory at `images/<parent path>/<name>/` (for a root image, `images/<name>/`) holding:
   - `Containerfile`: a single line, `FROM <registry>/<parent path>:<parent version>` for a child or `FROM <image>` for a root, **with no newline at the end**;
   - `context/.gitkeep`: the build context, otherwise empty.
5. **Write the build.** Add a newline after the `FROM` line, then append the instructions below it. Keep the `FROM` line exactly as generated. For a multi-stage build, put the other stages after it and leave that line alone. Put every file the build `COPY`s or `ADD`s under `context/`, and write their paths relative to `context/` (e.g. `COPY app/ /app/`).
6. **Don't bump it.** A new image already has a version that isn't in the registry, so CI builds it. Bump only images you changed that already existed.
7. **Check:** `createroot.py` and `new.py` already updated the README tree.
   - `python3 ./list.py --check --update-file` must exit 0. If it doesn't, see the link base rule below.
   - `python3 ./plan.py`: its output (and `plan.json`) must list the new image, with its parent in `depends_on` only if the parent is also being built. Without registry access, use `--all`.
8. **Hand over.** Show the maintainer the new directory, the `index.yml` diff, the README change and the plan. Don't commit or push unless asked.

## Changing an image

1. Edit the image's build file (`images/<path>/Containerfile`, or its `Dockerfile` if it has no `Containerfile`) and anything under `images/<path>/context/`.
2. Bump its version: `python3 ./bump.py -t <path> --patch` (use `--minor` or `--major` to match the change). A tag already in the registry is never rebuilt, so a change without a bump never ships.
3. `bump.py` updates the README tree itself. Run `python3 ./list.py --update-file` only if you changed something no script tracks, like a root image's `FROM` line.
4. Check the plan: run `python3 ./plan.py`. The images it lists should be the bumped image and its children, and nothing else. Without registry access, use `--all` and read the ordering only.
5. Show the maintainer the diff and the plan. Don't commit or push unless they asked you to.

To add an image, follow [Creating a new image](#creating-a-new-image). To remove one, use `delete.py -t <path>`, then steps 3–5.

## Setting up the repository for GitHub or GitLab

Follow this when asked to "set up for GitHub" or "set up for GitLab", usually right after the release was unzipped into the repository. If the request doesn't say which, use the host in `git remote get-url origin` (`github.com` is GitHub; anything else is GitLab), and say so in your reply.

Before changing anything, ask the maintainer, in one message, anything the request doesn't already answer:
- which platforms the images should support (step 4), for example `linux/amd64` only, or `linux/amd64,linux/arm64`;
- which image the first root image should be built from (step 8), for example `docker.io/ubuntu:24.04`.

1. **Python.** Check that `python3 --version` is 3.11 or newer. If there's no `venv/`, create one with a 3.11+ interpreter (`python3.11 -m venv venv`, or whichever `python3.1x` exists) and run `venv/bin/python -m pip install -r requirements.txt`. An older Python fails with `SyntaxError` on `match`.
2. **Work out the registry.** It's the lowercased project path on the host's registry, and it must match what CI uses exactly, because it's written into the `FROM` line of every child image:
   - GitHub: `ghcr.io/<owner>/<repo>`, all lowercase (`franco-martin/ITB-demo-project` becomes `ghcr.io/franco-martin/itb-demo-project`). The example workflow lowercases it the same way.
   - GitLab.com: `registry.gitlab.com/<group>/<project>`, all lowercase, the same as `$CI_REGISTRY_IMAGE`.
   - Self-hosted GitLab or any other registry: ask the maintainer. The registry host often differs from the web host.
3. **Create `.env`.** If it doesn't exist, copy `sample-envs` to `.env` and set `IMAGETREE_REGISTRY` to the value from step 2. If it exists, check it with `grep -c '^IMAGETREE_REGISTRY=' .env` and, if the value might be wrong, ask the maintainer rather than reading or overwriting it. Never write credentials into `.env`; CI uses its own token and `plan.py --all` works without one.
4. **Platforms.** Use the platforms the maintainer asked for. Set them before creating any image: `createroot.py` and `new.py` store `IMAGETREE_PLATFORMS` in `index.yml` for every image they add, and changing it later only affects new images.
   - Only the runners' own platform (`linux/amd64` on the hosted runners): leave `IMAGETREE_PLATFORMS` unset. Each image is built once, with no per-platform tags or manifest job.
   - Several platforms: set `IMAGETREE_PLATFORMS=linux/amd64,linux/arm64` (their list, comma separated) in `.env`. Each platform is built on a runner of that architecture and merged into the tag by a manifest job.
   - GitHub-hosted runners only cover `linux/amd64` and `linux/arm64`; GitLab.com's hosted runners the same. Any other platform needs self-hosted runners of that architecture: say so, and set `runs-on` (GitHub) or `tags` (GitLab) in the build jobs if the maintainer tells you the runner labels.
5. **Link base.** If `origin` is an SSH alias from `~/.ssh/config`, a self-hosted GitLab whose SSH host differs from its web url, or GitHub Enterprise, ask the maintainer for the url files are browsed at and set `IMAGETREE_LINK_BASE` in `.env`. See `README.md`, "`--link-base` and absolute urls".
6. **Copy the CI files.** Don't overwrite an existing CI file: if one exists, merge the jobs into it and show the maintainer the result.
   - GitHub: `examples/github/build-images.yml` to `.github/workflows/build-images.yml`.
   - GitLab: `examples/gitlab/.gitlab-ci.yml` to `.gitlab-ci.yml`, and `examples/gitlab/imagetree-pipeline.yml` to `.gitlab/imagetree-pipeline.yml`.

   Use them as they are unless the registry isn't the host's own (step 2): then change the registry in the plan job and the build jobs' login, following `docs/private-registries.md`.

   With several platforms on GitLab.com, uncomment the `tags: [saas-linux-small-%ARCH%]` lines of the `.imagetree-image` job in `.gitlab/imagetree-pipeline.yml`, so each platform runs on a runner of its architecture. The GitHub workflow already runs arm64 builds on `ubuntu-24.04-arm`.
7. **README markers.** `README.md` must contain the `<!-- image-tree:start -->` and `<!-- image-tree:end -->` lines, or the tree is never written and CI's README check fails. The release `README.md` has them. If the maintainer kept their own README, add the two lines where the tree should go.
8. **First root image.** CI fails until the tree has at least one image: `plan.py` and `list.py --check` both fail on an empty `index.yml`. If `index.yml` is missing or empty, create a root image as in [Creating a new image](#creating-a-new-image). Use the base image the maintainer gave you; don't pick one yourself. Leave out `--platforms`, so it gets `IMAGETREE_PLATFORMS` from step 4. For several platforms, the base image must be published for all of them.
9. **Check.**
   - `python3 ./list.py --check --update-file` exits 0.
   - `python3 ./plan.py --all` lists the root image, and `plan.json` shows the platforms from step 4 (none if it was left unset).
   - `python3 ./pipeline.py -f github` (or `-f gitlab`) runs without errors.
   - `git status` shows no `.env`.
10. **Hand over.** Show the maintainer the files you added and the plan, and don't commit or push unless asked. Tell them what's left for them, since you can't do it from the repository:
   - Images are only built on pushes to the default branch; merge/pull requests only plan.
   - GitHub: the workflow pushes to ghcr.io with the built-in `GITHUB_TOKEN`. If a package with that name already exists and isn't linked to this repository, give the repository write access in the package settings.
   - GitLab: the project's container registry must be enabled (Settings > General > Visibility, project features).
   - Platforms other than `linux/amd64` and `linux/arm64` need self-hosted runners of that architecture. See `README.md`, "Multi-platform images".
   - To add a platform to an image later, it has to be bumped; changing `IMAGETREE_PLATFORMS` alone doesn't rebuild anything.
   - Protect the default branch.

## Rules

- **Never edit `index.yml` by hand.** Versions, parents and names change only through `createroot.py`, `new.py`, `bump.py` and `delete.py`.
- **Keep the generated `FROM` line of child images.** It points at the parent's registry tag, and `bump.py` rewrites it when the parent's version changes. Change the base of a root image only by editing its `FROM` and bumping it.
- **Pin every `FROM` to a static tag, never `latest`.** A floating tag like `latest`, `stable` or a branch name can change what an image builds without a version bump. In a stricter environment, pin the tag and its digest together (`<image>:<tag>@sha256:<digest>`) so the build resolves to one immutable image; otherwise a static tag alone is enough. Ask the maintainer which images need digest pinning if unsure.
- **Never edit between the README markers.** Run `python3 ./list.py --update-file` instead. CI runs `list.py --check --update-file` and fails when the block is stale.
- **Diagram links must match CI.** In CI, the tree's links are built from the project url (`$CI_PROJECT_URL/-/blob/$CI_DEFAULT_BRANCH` on GitLab, `$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/blob/HEAD` on GitHub). Locally, the same url is built from the `origin` remote, or taken from `IMAGETREE_LINK_BASE` when set. If `update-file` warns that no link base is set, ask the maintainer to add `IMAGETREE_LINK_BASE` to `.env` instead of guessing.
- **Don't build or push images locally.** CI builds them from the plan. Running `plan.py` is fine; it only reads the registry.
- **Never delete images from the registry.** The maintainer does that manually. Never pass `--from-registry` or `--all-tags` to `delete.py`; without them it only changes the local tree.
- **Secrets.** Never print, log, commit or copy the contents of `.env` or any registry credential. Check that a variable is set with `grep -c '^IMAGETREE_REGISTRY_PASSWORD=' .env`, never `cat .env`. Tokens a build needs (npm, pip, …) go in as build secrets (`--secret` / `secrets:`), never as `ARG` or `--build-arg`, and never in a `Containerfile` or its context. See `docs/private-registries.md`.
- **Don't edit the release files** listed in the layout table. If a script misbehaves, report it with the command and its output.
- **Build file names.** New images get a `Containerfile`. An image without one is built from its `Dockerfile`, which is fine; edit whichever file it has. Don't add a second build file or rename one unless asked: when both exist, the `Containerfile` is used and the `Dockerfile` is ignored.

## Verifying your work

- `python3 ./list.py --check --update-file` exits 0.
- `python3 ./plan.py` (or `--all`) runs without errors, and `plan.json` lists only the images you meant to change.
- `git status` shows no `.env`, no credentials and no generated files (`plan.json`, `build-pipeline.yml`).

## More

- `README.md`: the full user guide, including CI setup and updating the release.
- `docs/private-registries.md`: registry credentials for planning and building.
- `examples/`: the GitLab and GitHub pipelines.
- `CHANGELOG.md`: what changed between releases.
