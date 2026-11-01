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

## 刪除評論而不是作品

評論用它們自己的指令刪除，不走這個交接流程：刪除自己的評論用 `vpub comments delete PLATFORM WORK COMMENT_ID`，移除觀眾的評論用 `vpub comments remove PLATFORM WORK COMMENT_ID`。兩者都不可復原，所以只能針對使用者指名的那一則，並且先用 `vpub comments list` 讀過它。刪除或移除只有在確實證明該評論已不在時，才會回報為完成（`COMMENT_VERIFIED`）。在 YouTube 與 Instagram 上，這表示重新讀取時沒有它、按下那一次之後它馬上消失，而且平台對那次按下的回覆指名了它並帶有固定的成功字樣；頁面可能在平台同意之前就把那一列拿掉，只載入一部分的清單也不能因為少了某則評論就當作已刪除。第一次真實的 YouTube 刪除，請求送到了預期的位址並指名了這則評論，但回覆裡沒有帶那個成功字樣，所以雖然那則回覆已從頁面上消失，仍停在 `COMMENT_UNVERIFIED`；Instagram 的字樣還沒用真實的刪除核對過。在之後的真實刪除顯示成功長什麼樣之前，刪除都會停在 `COMMENT_UNVERIFIED`，直到 `vpub comments verify-action RECEIPT_KEY` 或到平台上確認它已消失。回執不確定時，絕不要再按一次刪除。各平台提供的證明見[操作指南](OPERATIONS.md#管理單條評論)。

