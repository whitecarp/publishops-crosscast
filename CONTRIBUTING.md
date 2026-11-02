# Contributing

Thanks for helping improve PublishOps CrossCast. Please open an issue before a large change and describe the affected platform, expected behavior, and how it can be verified.

Pull requests should keep platform-specific behavior explicit, preserve account and publication safety checks, and avoid including credentials, cookies, personal account data, local paths, or private receipts. Use synthetic fixtures for tests. Do not claim a platform workflow is verified unless the evidence and scope are stated.

## Updating the included bili-cli

`src/bili_cli` is an unmodified copy of a tagged [bili-cli](https://github.com/kukemc/bili-cli) release, so fixes to it belong upstream. To update it, replace that folder with the new tag's `src/bili_cli`, copy the tag's `skills/bili-cli` to `src/bili_cli/_skills/bili-cli` and its `LICENSE` to `src/bili_cli/LICENSE`, match its dependencies in `pyproject.toml`, and update the version in `NOTICE.md` and `tests/test_bili_merged.py`. Read its changelog and the diff of every command listed in that test, because CrossCast's other tests mock bili-cli and cannot see a changed output field.

## License of contributions

By submitting a contribution, you agree that it may be distributed under the repository's MIT License.
