# Notices

PublishOps CrossCast is distributed under the MIT License; see [LICENSE](LICENSE).

This is an independent integration project, not an official product of the platforms below. 本项目为独立整合工具，保留第三方授权。此專案為獨立整合工具，保留第三方授權。

- [`kukemc/bili-cli`](https://github.com/kukemc/bili-cli), MIT, Copyright (c) 2026 bili-cli contributors. A copy of v1.2.1 (commit `1181a8f`) is included unchanged in `src/bili_cli`, with its license in `src/bili_cli/LICENSE`. Its login, rate limits and audit behavior are that code's own. Its viewer-friendly README also informed this project's documentation structure; its artwork was not copied.
- [`bilibili-api-python`](https://pypi.org/project/bilibili-api-python/), GPL-3.0-or-later. bili-cli needs it. It is installed as a separate dependency, is not included in this repository, and only runs inside the separate `bili` process.
- [Google API Python Client](https://github.com/googleapis/google-api-python-client) and [google-auth-oauthlib](https://github.com/googleapis/google-auth-library-python-oauthlib), Apache-2.0, used only by the separate paid API add-on.
- [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python), Apache-2.0, for the dedicated Douyin, Instagram and X browser backends. This project does not implement verification bypasses.

All dependencies retain their own licenses; direct versions are listed in [pyproject.toml](pyproject.toml). CrossCast was extracted from [the original project](https://github.com/whitecarp/ai-video-lab); private operation records and account data were not carried into this repository.
