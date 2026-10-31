# 刪除與封面重發

**繁體中文** · [English](DELETION_AND_COVERS_EN.md)

這是 **Instagram 原生瀏覽器交接**，不是獨立刪除 API。Agent 必須先取得針對該作品的刪除及重發授權，再在官方頁核對帳號、作品 ID 與刪除範圍。一般上傳授權不包含刪除。

```sh
vpub browser delete-request KEY --identity OBSERVED_ACCOUNT --id ORIGINAL_ID
# Host inspects the exact work and deletes once in the official UI.
# Refresh the same profile and record the acknowledgement and absence.
vpub browser delete-record KEY --evidence deletion.json
```

`deletion.json` 範例只含結構示意，實際欄位必須由觀察填寫；不得將範例當成成功證據。

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

刪除途中不確定時停在 `DELETE_PENDING`，檢查原作品，不重複點刪除。只有實際確認及刷新後不存在，才記為 `AGENT_REPORTED_DELETED`。這是宿主提供的 UI 證據，CLI 沒有獨立查詢平台；原發布回執仍保留。

## 重發同一影片、替換封面

```sh
vpub plan replacement.json
vpub browser repost-deleted KEY replacement.json --confirm NEW_PLAN_HASH --identity OBSERVED_ACCOUNT --deleted-id ORIGINAL_ID
vpub browser begin KEY --identity OBSERVED_ACCOUNT
# Host selects the returned snapshot, checks caption/AI label/audience/cover,
# submits once, then records the new durable ID through browser record.
```

本命令只允許同一影片位元組、同一帳號、相同文案／受眾／聲明，封面可以不同。它建立新快照並將原方案、發布及刪除回執放進 `deleted_generations`，不刪除歷史、不繞過去重，也不自動點擊分享。成功佇列不代表已發布；重發必須另行讀回新作品 ID。變更影片版本需要另建明確方案，不屬於此封面重發命令。

## Instagram 封面限制

本次網頁實測：發布後「編輯資訊」沒有換封面入口；發布前直式圖片仍被裁成橫式影片的預覽比例。因此專用圖本身完整，並不代表預覽及個人主頁縮圖都完整。發布前檢查文字，發布後再看主頁；不因封面問題自動刪除作品。保留原片比例；手機使用「編輯封面 → 從相機膠卷中添加」選抖音共用直式圖，再檢查主頁網格。

目前預設 Instagram 保留原片及原比例，上傳時另外設定封面；手機可從相機膠卷選抖音共用直式圖。[目前指南](OPERATIONS.md#instagram-發布與封面)。0.2.4 的片頭實驗已撤回，既有發布記錄仍保留。

