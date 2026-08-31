# Publishing & consuming `@runspace/ui`

The runspace frontend is published as `@runspace/ui` to a local Verdaccio
registry on `127.0.0.1:4873`. Consumer apps install it like any npm
package — no rsync mirror, no webpack alias, no `HOT_MODE` toggle.

## Architecture

```
runspace/workspace/frontend/  ─┐
  package.json (name: @runspace/ui)    │  pnpm publish / npm publish
                                       ▼
                        Verdaccio @ http://localhost:4873
                                       ▲
  package.json deps: "@runspace/ui": "^0.3.0"  │  npm install
  .npmrc: registry=http://localhost:4873/      │
                                               │
  consumer app ───────────────────────┘
```

Verdaccio also proxies and caches everything else (react, next, lucide-react, …)
from npmjs.org, so consumer `npm install` resolves the full graph through one
registry.

## Publishing a new version

```bash
cd path/to/runspace/workspace/frontend

# 1. Bump version
npm version patch     # 0.3.0 → 0.3.1   (bug fix)
npm version minor     # 0.3.0 → 0.4.0   (new component, backwards compat)
npm version major     # 0.3.0 → 1.0.0   (breaking import path / props change)

# 2. Publish — registry comes from package.json publishConfig
npm publish

# 3. Verify
npm view @runspace/ui --registry http://localhost:4873/
```

After publish, each consumer that wants the new version:
```bash
cd <consumer>/web
npm install @runspace/ui@latest    # pulls newest
# or pin: npm install @runspace/ui@0.4.0
npm run build                      # verify
```

## New consumer app — checklist

1. **`.npmrc`** in the app's web/ dir (commit it):
   ```
   registry=http://localhost:4873/
   //localhost:4873/:_authToken=anonymous
   //localhost:4873/:always-auth=false
   ```

2. **`package.json`** — add to `dependencies`:
   ```jsonc
   // inside "dependencies"
   "@runspace/ui": "^0.3.0"
   ```

3. **`next.config.js`** — add to top-level config:
   ```js
   transpilePackages: ['@runspace/ui'],
   ```
   (Required because `@runspace/ui` ships TypeScript source; Next must
   transpile it during build.)

4. **`tsconfig.json`** — NO `paths` entry needed for `@runspace/*`. Module
   resolution finds it via `node_modules/@runspace/ui/package.json#exports`.

5. **Imports**:
   ```ts
   import WorkspaceLayout from '@runspace/ui/team/pages/WorkspaceLayout'
   import DialogChat from '@runspace/ui/dialog/pages/DialogChat'
   import BotAvatar from '@runspace/ui/shared/components/BotAvatar'
   ```

6. **Run** `npm install && npm run build` — should succeed with no
   workspace-frontend mirror, no webpack alias.

## Migrating an existing consumer

Replace the legacy mirror plumbing with the npm dep:

```bash
cd <consumer>/web

# 1. Add .npmrc (see above)
# 2. Add @runspace/ui dep, drop HOT_MODE script flag from package.json
# 3. Replace next.config.js: drop HOT_MODE branch + webpack alias,
#    keep only transpilePackages: ['@runspace/ui']
# 4. tsconfig.json: drop "@runspace/*" path entry
# 5. Source: sed -i 's|@runspace/team/|@runspace/ui/team/|g; \
#                    s|@runspace/dialog/|@runspace/ui/dialog/|g; \
#                    s|@runspace/shared/|@runspace/ui/shared/|g' src/**/*.{ts,tsx}
# 6. rm -rf workspace-frontend/   (the rsynced mirror)
# 7. npm install && npm run build
```

## Docker builds

Inside a Dockerfile, `localhost:4873` is the *container's* localhost, not
the host. Fix one of two ways:

**Option A — host network for the install step:**
```dockerfile
RUN --network=host npm ci
```

**Option B — pass the registry URL via build arg:**
```dockerfile
ARG NPM_REGISTRY=http://host.docker.internal:4873
RUN echo "registry=${NPM_REGISTRY}/" > .npmrc \
 && echo "//host.docker.internal:4873/:_authToken=anonymous" >> .npmrc \
 && npm ci
```
Then build with `--add-host=host.docker.internal:host-gateway`.

Option A is simpler on a single host. Option B is friendlier when builds move
off-host.

## Verdaccio admin

```bash
# Container lifecycle
cd /root/services/verdaccio
docker compose up -d        # start
docker compose ps           # status
docker compose logs -f      # tail logs
docker compose restart      # bounce

# Browse via web UI
curl http://localhost:4873/        # JSON listing
# or in browser: http://<vps>:4873/  (after exposing)

# Storage
ls /root/services/verdaccio/storage/@runspace/ui/    # tarballs + db
```

## When to upgrade to "real" registry

Triggers — any one means time to expose Verdaccio publicly (or migrate
to a managed registry like GitHub Packages / npm Enterprise):

- Builds happen on a remote machine (CI runner, customer VPS).
- More than one human publishes — anonymous publish stops being safe.
- Need audit log of who pushed what.

To expose:
1. Edit `/root/services/verdaccio/docker-compose.yml`: revert `network_mode: host`
   to `ports: ["4873:4873"]`, fix UFW + DOCKER FORWARD chains.
2. Add nginx with TLS + htpasswd auth in front.
3. Disable anonymous `publish` in `config.yaml` → require `npm adduser`.
4. Update consumer `.npmrc` to the public URL.

Consumer source code does not change.
