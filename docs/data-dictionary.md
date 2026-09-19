# A-Z 法術資料字典

`data/spellbook.sqlite` 是擷取流程產生的種子資料庫，隨應用程式唯讀發佈；`data/export/spells.json` 是它的唯讀匯出。

應用程式第一次執行時，會把種子複製成使用者自己的資料庫（見文末「使用者資料庫」），之後所有編輯都寫在那一份。

## 核心狀態

- `not_required`：原文已有中文正文，不需補譯。
- `generated`：中文翻譯由自動流程生成（介面顯示為「機器翻譯」）。

`review_status`（`unreviewed`／`needs_review`／`reviewed`）是舊校對流程留下的欄位，應用程式已不再讀寫。

## `spells`

| 欄位 | 說明 |
| --- | --- |
| `id` | `spl_` 加 ULID 的永久公開 ID |
| `source_key` | 擷取流程用的穩定來源鍵 |
| `name_zh` | 中文標準名稱，必填 |
| `name_en` | 英文標準名稱，必填 |
| `alphabet` | PDF 的 A-Z 分區 |
| `variant_group_id` | 同名異效條目的關聯群組 |
| `review_status` | 舊校對流程欄位，已不使用 |

## `spell_entries`

保存 PDF 中一個具體規則版本。`school`、`subschool`、`components`、`casting_time`、`range_text`、`target_text`、`area_text`、`effect_text`、`duration`、`saving_throw`、`spell_resistance` 對應原條目的規則欄位。

| 欄位 | 說明 |
| --- | --- |
| `description_zh` | 中文效果正文；英文條目使用生成翻譯 |
| `description_en` | PDF 中原有的英文正文；沒有時為空 |
| `raw_text` | 未加工原始條目文字 |
| `pdf_page_start/end` | PDF 實際起訖頁碼 |
| `parse_confidence` | 0 至 1 的解析信心 |
| `translation_status` | 翻譯狀態 |
| `translation_method` | 翻譯方法；自動初稿為 `contextual_glossary` |

## 關聯表

- `spell_names`：別名、其他譯名及拼寫變體。
- `classes`、`spell_levels`：職業與法術等級。
- `sources`、`spell_sources`：來源書籍。
- `descriptors`、`spell_descriptors`：法術描述符。
- `extraction_issues`：解析與翻譯問題。
- `spell_search`：可重建的 FTS5 全文搜尋索引。

`duplicate_english_name` 表示英文標準名稱相同、但尚未人工判定應合併或視為規則版本的條目。第一版保留各自的永久 ID，不自動刪除或合併。

## 翻譯來源目錄

`data/translations.generated.json` 保存 71 筆自動翻譯初稿及其 `provider`。這些條目在資料庫中皆為 `translation_status = generated`。

## 使用者資料庫

位置：`%LOCALAPPDATA%\Spellbook\spellbook.sqlite`（可用環境變數 `SPELLBOOK_DATA_DIR` 改變資料夾）。由種子複製而來，啟動時套用 migration 3：

- 刪除舊校對流程的空資料表：`review_events`、`review_checks`、`review_conflicts`、`duplicate_decisions`。
- `spells.edited_at`：內容與原始內容不同時為最後一次修改時間；與原始內容相同時為 `NULL`。
- `spell_versions`：每次編輯、還原版本或還原原始內容前，先保存被取代的內容。每個法術只保留最近 5 筆。

| 欄位 | 說明 |
| --- | --- |
| `spell_id` | 所屬法術 |
| `snapshot_json` | 被取代時的可編輯欄位內容 |
| `content_saved_at` | 這份內容當初儲存的時間；`NULL` 代表它是原始內容 |
| `replaced_at` | 被取代的時間 |
| `replaced_by` | 取代它的動作：`edit`、`rollback` 或 `restore_original` |

「還原成原始內容」一律從種子資料庫讀取，所以原始內容永遠不會因為超過 5 個版本而遺失。
