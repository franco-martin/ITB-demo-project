# Private registries

There are two separate places credentials matter, and they work differently:

- **The plan step.** `plan.py` reads your registry (read-only `GET`s) to see which image versions already exist, so it knows what needs building. This document is mostly about that step.
- **The build step.** Whatever tool your CI pipeline uses to actually build and push (buildah, docker, etc) needs its own credentials, both to pull private base images and to push the result. See [Build step credentials](#build-step-credentials) below.

## Plan-step credentials
`plan.py` (through `utils.registry_get`) tries these sources, in order, and uses the first one that has credentials for the registry's host:

1. **Environment variables:** `IMAGETREE_REGISTRY_USERNAME` and `IMAGETREE_REGISTRY_PASSWORD`. Set both, or neither.
2. **An auth file**, in the format used by Docker and Podman (`{"auths": {"host": {"auth": "base64(user:pass)"}}, "credHelpers": {...}, "credsStore": "..."}`). The file is `$IMAGETREE_REGISTRY_AUTH_FILE` if set, otherwise the first of these that exists:
   - `$REGISTRY_AUTH_FILE`
   - `$XDG_RUNTIME_DIR/containers/auth.json`
   - `$DOCKER_CONFIG/config.json`
   - `~/.docker/config.json`

   Inside the file, in this order:
   - `credHelpers[host]`, by running `docker-credential-<helper> get`
   - the longest match under `auths` for `host/namespace`, `host`, or `https://host`
   - `credsStore`, by running the matching `docker-credential-<store> get`
3. **Anonymous access**, if none of the above has anything for the host.

Only *which* source was used is ever logged (e.g. "using credentials from the environment" or "using credentials from ~/.docker/config.json"), never the username or password.

A few other things that apply regardless of registry:
- `IMAGETREE_REGISTRY_CA_CERT` points at a CA bundle to trust for both the registry and its token service (sets `requests`' `verify=`). Use this for a self-hosted registry with an internal CA.
- Bearer tokens (used by GitLab, GitHub, Docker Hub and most cloud registries) are cached per `(realm, service, scope)`, so a plan with many images doesn't re-authenticate for each one.
- **Namespaced registry paths are supported.** `IMAGETREE_REGISTRY` can be `registry.example.com/some/group`, and the group/namespace is inserted after `/v2/` when talking to the registry API, exactly like `docker pull registry.example.com/some/group/image:tag` would.
- A repository the credentials can't see (403, or 401 after a token was already issued) is treated as "not in the registry" rather than a hard failure, so `plan.py` still plans to build it. A 401 straight from the token service (the credentials themselves were rejected) is a hard failure. See "Refused repositories" in `README.md`.

### GitLab
Set `IMAGETREE_REGISTRY=registry.gitlab.com/your-group/your-project` and:
```
IMAGETREE_REGISTRY_USERNAME=gitlab-ci-token
IMAGETREE_REGISTRY_PASSWORD=$CI_JOB_TOKEN
```
in a GitLab CI job, or use a [deploy token](https://docs.gitlab.com/ee/user/project/deploy_tokens/) or personal/project access token with `read_registry` scope for local use.

### GHCR (GitHub Container Registry)
Set `IMAGETREE_REGISTRY=ghcr.io/your-org` and:
```
IMAGETREE_REGISTRY_USERNAME=your-github-username-or-org
IMAGETREE_REGISTRY_PASSWORD=<a PAT with read:packages, or $GITHUB_TOKEN in a workflow>
```

### ECR (Amazon)
ECR doesn't take a long-lived username/password; get a short-lived token instead:
```
IMAGETREE_REGISTRY_USERNAME=AWS
IMAGETREE_REGISTRY_PASSWORD=$(aws ecr get-login-password --region <region>)
```
Set `IMAGETREE_REGISTRY=<account-id>.dkr.ecr.<region>.amazonaws.com`. The token is only valid for 12 hours, so refresh it before each `plan.py` run (e.g. as a step right before it in CI).

### Artifact Registry / GCR (Google)
```
IMAGETREE_REGISTRY_USERNAME=oauth2accesstoken
IMAGETREE_REGISTRY_PASSWORD=$(gcloud auth print-access-token)
```
or, with a service account key file, `IMAGETREE_REGISTRY_USERNAME=_json_key` and `IMAGETREE_REGISTRY_PASSWORD` set to the raw JSON key content. `IMAGETREE_REGISTRY` looks like `us-docker.pkg.dev/your-project/your-repo` (Artifact Registry) or `gcr.io/your-project` (GCR).

### ACR (Azure Container Registry)
Either an access token:
```
IMAGETREE_REGISTRY_USERNAME=00000000-0000-0000-0000-000000000000
IMAGETREE_REGISTRY_PASSWORD=$(az acr login --name <registry> --expose-token --output tsv --query accessToken)
```
or a service principal with `AcrPull` (`IMAGETREE_REGISTRY_USERNAME`/`PASSWORD` set to its app id / secret). `IMAGETREE_REGISTRY=<registry>.azurecr.io`.

### Harbor, Nexus or Artifactory, with a CA
Self-hosted registries commonly sit behind an internal CA. Use a robot account (Harbor) or a dedicated deploy user (Nexus/Artifactory) for `IMAGETREE_REGISTRY_USERNAME`/`PASSWORD`, and point `IMAGETREE_REGISTRY_CA_CERT` at the CA bundle so the plan step's TLS verification succeeds:
```
IMAGETREE_REGISTRY=harbor.example.com/your-project
IMAGETREE_REGISTRY_USERNAME=robot$your-project+imagetree
IMAGETREE_REGISTRY_PASSWORD=<robot account secret>
IMAGETREE_REGISTRY_CA_CERT=/etc/ssl/certs/internal-ca.pem
```

## Build step credentials
The plan step never builds anything, so none of the above helps your build tool pull or push images. That's entirely up to your CI job:

- **Private base images.** If a `Containerfile`'s `FROM` points at an image in a private registry, your build job's own login needs pull access to it, in addition to whatever `IMAGETREE_REGISTRY` credentials plan.py used to check the *target* tag. The kaniko examples read registry credentials from `/kaniko/.docker/config.json`: add one entry to its `"auths"` per registry, each with its own (masked) CI variables or secrets.
- **npm and pip tokens must be build secrets, never build args.** A `--build-arg NPM_TOKEN=...` (or an `ARG`/`ENV` baked into the `Containerfile`) ends up in the image's build history and can be extracted from any layer, even a later stage that doesn't reference it. Use your builder's secret-mount mechanism instead, e.g. kaniko's `--secret id=npm_token,env=NPM_TOKEN` (or BuildKit/Buildah's `--secret id=npmrc,src=.npmrc`) with `RUN --mount=type=secret,id=npm_token ...` in the `Containerfile`, so the token never lands in a layer.
- **Point the package manager at your private registry's global index** rather than passing credentials per-install. For npm, set the registry in `.npmrc` (`registry=https://your-registry/npm/`) alongside the token secret above. For pip, set `index-url`/`extra-index-url` in `pip.conf` (or `PIP_INDEX_URL`) to your private index (e.g. an Artifactory/Nexus/CodeArtifact pip proxy), again supplying any credentials it needs as a build secret rather than a build arg.
