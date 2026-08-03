# Playwright, containerised — so `git clone && make` still needs only Docker,
# uv and make. Node and the browsers live here, not on a contributor's machine.
FROM mcr.microsoft.com/playwright:v1.49.0-noble
# The image ships the BROWSERS at /ms-playwright but not the npm package, so a
# `require('playwright')` fails with a bare Node banner and no explanation.
# Pinned to the image's own version — a mismatch downloads a second browser set
# or refuses to start.
RUN npm install -g playwright@1.49.0
ENV NODE_PATH=/usr/lib/node_modules

WORKDIR /capture
ENTRYPOINT ["node"]
