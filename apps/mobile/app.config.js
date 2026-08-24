// Overlays app.json at `expo start` time. The real bearer token comes from the
// environment (source the repo-root .env first) so it never lands in git.
export default ({ config }) => ({
  ...config,
  extra: {
    ...config.extra,
    apiUrl: process.env.SUPERAPP_API_URL ?? config.extra.apiUrl,
    apiToken: process.env.SUPERAPP_API_TOKEN ?? config.extra.apiToken,
  },
});
