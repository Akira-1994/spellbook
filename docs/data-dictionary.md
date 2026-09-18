# A-Z 法術資料字典

SQLite 是主資料來源；`data/export/spells.json` 是唯讀匯出。

## 核心狀態

- `unreviewed`：已解析但尚未人工校對。
- `needs_review`：解析、翻譯或版本判斷需要人工確認。
- `reviewed`：已人工確認。
- `not_required`：原文已有中文正文，不需補譯。
- `generated`：中文翻譯由自動流程生成，尚待校對。

## `spells`

| 欄位 | 說明 |
| --- | --- |
| `id` | `spl_` 加 ULID 的永久公開 ID |
| `source_key` | 擷取流程用的穩定來源鍵 |
| `name_zh` | 中文標準名稱，必填 |
| `name_en` | 英文標準名稱，必填 |
| `alphabet` | PDF 的 A-Z 分區 |
| `variant_group_id` | 同名異效條目的關聯群組 |
| `review_status` | 法術整體校對狀態 |

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

`data/translations.generated.json` 保存 71 筆自動翻譯初稿及其 `provider`。這些條目在資料庫中皆為 `translation_status = generated`、`review_status = needs_review`；完成人工校對前不得改為 `reviewed`。
