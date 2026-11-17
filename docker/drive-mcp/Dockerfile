# Google Drive MCP server (stdio). The upstream project publishes no Docker image, so we
# build a tiny one from the pinned npm package. Pinning keeps every machine on the same version.
FROM node:22-slim

RUN npm install -g @piotr-agier/google-drive-mcp@2.13.0 && npm cache clean --force

# The OAuth keys file is mounted read-only. The token file lives in a mounted DIRECTORY, not a
# single mounted file: the server saves tokens by atomic rename, which fails on a file bind mount.
ENV GOOGLE_DRIVE_OAUTH_CREDENTIALS=/config/gcp-oauth.keys.json \
    GOOGLE_DRIVE_MCP_TOKEN_PATH=/config/tokens/tokens.json

# -i (stdin) is supplied by the caller; MCP talks over stdio, so no port is exposed.
ENTRYPOINT ["google-drive-mcp"]
