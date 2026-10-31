# Deletion and cover replacement

[繁體中文](DELETION_AND_COVERS.md) · **English**

This is an **Instagram native-browser handoff**, not a standalone deletion API. The host needs explicit deletion and replacement authorization for the exact work, then checks the signed-in account, work ID and deletion scope. Upload authorization alone does not authorize deletion.

```sh
vpub browser delete-request KEY --identity OBSERVED_ACCOUNT --id ORIGINAL_ID
# Host inspects the exact work and deletes once in the official UI.
# Refresh the same profile and record the acknowledgement and absence.
vpub browser delete-record KEY --evidence deletion.json
```

`deletion.json` This is a structural example only; replace it with actual observations, never treat fixtures as proof.

```json
{
  "id": "ORIGINAL_ID",
  "identity": "OBSERVED_ACCOUNT",
  "page_url": "https://www.instagram.com/reel/ORIGINAL_ID/",
  "profile_url": "https://www.instagram.com/OBSERVED_ACCOUNT/",
  "delete_acknowledged": true,
  "work_absent": true,
  "observation": "Describe the actual confirmation result and refreshed profile check.",
  "recovery_note": "Describe the actual recovery wording; state if recovery was not tested."
}
```

An uncertain deletion stays `DELETE_PENDING`; inspect the original work instead of clicking Delete again. Confirmed deletion plus refreshed absence becomes `AGENT_REPORTED_DELETED`. Evidence comes from the host UI, not an independent API query; the original receipt remains intact.

## Repost the same video with a replacement cover

```sh
vpub plan replacement.json
vpub browser repost-deleted KEY replacement.json --confirm NEW_PLAN_HASH --identity OBSERVED_ACCOUNT --deleted-id ORIGINAL_ID
vpub browser begin KEY --identity OBSERVED_ACCOUNT
# Host selects the returned snapshot, checks caption/AI label/audience/cover,
# submits once, then records the new durable ID through browser record.
```

This command retains identical video bytes, account, metadata, audience and declarations while allowing a new cover. It creates a new snapshot and archives the previous plan/publication/deletion receipts in `deleted_generations`. It does not erase history, bypass deduplication or click Share. Queueing is not publishing; read back a new durable work ID separately. A changed video version needs its own explicit plan and is outside this cover-replacement command.

## Cover limitations

Observed in this web session: Edit info after publication had no cover-replacement control; a portrait image before publication was cropped to the landscape video's preview ratio. Complete source artwork does not guarantee complete previews or profile thumbnails. Inspect both; never automatically delete a work to fix its cover. Retain the original video ratio. On mobile, use Edit cover → Add from camera roll with the shared Douyin portrait and inspect the profile grid.

Current Instagram publishing retains original footage/ratio with a separate cover, using shared Douyin portrait artwork through the phone camera-roll picker. The 0.2.4 lead-in experiment is withdrawn; publication history remains intact.

Related links: [Instagram publishing and covers](OPERATIONS_EN.md#instagram-publishing-and-covers)

