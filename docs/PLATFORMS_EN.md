# Platform scope and boundaries

[简体中文](PLATFORMS.md) · [繁體中文](PLATFORMS_ZH_TW.md) · [English](PLATFORMS_EN.md) · [Home](../README_EN.md)

CrossCast adapts one local video plan for selected platforms. Actual capability depends on platform permissions, host browser tools, account state, and the current platform UI. Installation does not mean you are signed in or authorized to publish. Run `vpub capabilities` to see the backends available locally.

## Platforms

- **Bilibili:** the default is host-operated official web publishing; the upstream `bili` CLI is an optional backend. CrossCast does not reimplement or verify every `bili` feature.
- **YouTube:** publish through the official web handoff or optionally use the YouTube Data API. API use requires your own OAuth client and consent. Additional scopes, such as playlist management, must be explicitly selected. Google determines quotas, test limits, and review requirements.
- **Douyin:** a host can assist with the official web workflow. The standalone backend remains experimental and may not work for every account or environment.
- **Instagram:** the host operates the official website. Cross-platform preparation and draft support are not an independent Instagram API. Covers and profile grids may crop differently; a user may need to select a cover on a phone.
- **X:** the host operates the official website; an independent X API is not implemented. Custom-cover access depends on account features and the official UI.
- **TikTok:** support is currently suspended; the CLI rejects login and publishing requests.

## Not currently provided

There are no general-purpose comments, replies, direct messages, inbox, search/recommendation, or creator-analytics commands. Interaction terms and metrics from one platform must not be assumed to apply to another.

## Important boundaries

- A plan, login URL, browser job, or local receipt does not prove that the platform is signed in, uploaded, published, or public.
- Web operations require a host with browser-control capability. Otherwise, the assistant can only prepare materials and handoff instructions.
- Review the target account, work, copy, audience, and AI disclosure on the platform before submitting.
- If a submission result is uncertain, inspect the same task instead of blindly uploading again.
- Platform menus, features, format requirements, and fees can change; check the official page at the time of use.

See the [usage guide](USAGE_EN.md).
