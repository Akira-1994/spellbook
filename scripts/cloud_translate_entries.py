from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from scripts.spellbook_lib import load_entries


ROOT = Path(__file__).resolve().parents[1]
DELIMITER = "@@@SPELLBOOK_FIELD@@@"

FIELD_KEYS = (
    "school",
    "subschool",
    "levels",
    "components",
    "casting_time",
    "range_text",
    "target_text",
    "area_text",
    "effect_text",
    "duration",
    "saving_throw",
    "spell_resistance",
)

NAME_OVERRIDES = {
    "SHADOW MASK": "陰影面具",
    "SHADOW RADIANCE": "陰影輝光",
    "SHADOW WELL": "陰影之井",
    "SHADOWBLAST": "陰影爆破",
    "SHADOWFADE": "陰影消退",
    "SHADOWY GRAPPLER": "陰影擒抱者",
    "SHARD STORM": "碎片風暴",
    "SHARE HUSK": "共享軀殼",
    "SHARPTOOTH": "銳齒術",
    "SHATTERFLOOR": "碎地術",
    "SHIELDBEARER": "持盾者",
    "SHIFTING PATHS": "易位路徑",
    "SHROUD OF FLAME": "火焰罩衣",
    "SHROUD OF UNDEATH": "不死罩衣",
    "SIGN": "徵兆術",
    "SILENT PORTAL": "寂靜門扉",
    "SILVERBEARD": "銀鬚術",
    "SINK": "沉陷術",
    "SKELETAL GUARD": "骸骨守衛",
    "SKULL WATCH": "骷髏警戒",
    "SLAPPING HAND": "掌摑之手",
    "SLASHING DARKNESS": "斬裂黑暗",
    "SLIDE": "滑移術",
    "SLIDE, GREATER": "高等滑移術",
    "SLIME WAVE": "黏液浪潮",
    "SLOW BURN": "緩燃術",
    "SMELL OF FEAR": "恐懼氣味",
    "SNAKE’S SWIFTNESS": "蛇之迅捷",
    "SNAKE’S SWIFTNESS,MASS": "群體蛇之迅捷",
    "SNAKEBITE": "蛇咬術",
    "SNOWBALL SWARM": "雪球群",
    "SNOWSHOES": "雪鞋術",
    "SNOWSHOES, MASS": "群體雪鞋術",
    "SOLIPSISM": "唯我論",
    "SONIC BLAST": "音波爆破",
    "SONIC RUMBLE": "音波轟鳴",
    "SONIC SNAP": "音爆術",
    "SOUND LANCE": "音波長槍",
    "SPELL MATRIX,GREATER": "高等法術矩陣",
    "SPIDER CURSE": "蜘蛛詛咒",
    "SPIRITJAWS": "靈魂之顎",
    "STICKY SADDLE": "黏固馬鞍",
    "STOLEN BREATH": "竊息術",
    "STONE BODY": "石軀術",
    "STONE BONES": "石骨術",
    "STONE SPHERE": "石球術",
    "STONE SPIDERS": "石蜘蛛",
    "STONEHOLD": "石牢術",
    "STORM OF ELEMENTAL FURY": "元素狂怒風暴",
    "STORM TOWER": "風暴之塔",
    "STUN RAY": "震懾射線",
    "STUNNING BREATH": "震懾吐息",
    "STUNNING BREATH,GREATER": "高等震懾吐息",
    "SUBMERGE SHIP": "潛沒船隻",
    "SUDDEN STALAGMITE": "突發石筍",
    "SUMMON BABAU DEMON": "召喚巴布魔",
    "SUMMON BEARDED DEVIL": "召喚鬚魔",
    "SUMMON BRALANI ELADRIN": "召喚布拉尼愛剌天族",
    "SUMMON ELEMENTITE SWARM": "召喚元素微粒群",
    "SUMMON GREATER ELEMENTAL": "召喚高等元素",
    "SUMMON HOUND ARCHON": "召喚犬首神使",
    "SUPPRESS GLYPH": "壓制刻文",
    "SUREFOOT": "穩固步伐",
    "SUSPENDED SILENCE": "懸置沉默",
    "SWAMP LUNG": "沼肺術",
    "SWAMP STRIDE": "沼澤步行",
    "SYMBOL OF SPELL LOSS": "法術失落徽記",
}

TERM_REPLACEMENTS = {
    "術士/巫師": "術士/法師",
    "吟遊詩人": "吟遊詩人",
    "遊俠": "巡林客",
    "法術抵抗": "法術抗力",
    "法術抗性": "法術抗力",
    "豁免投擲": "豁免檢定",
    "意志豁免": "意志豁免",
    "強韌豁免": "強韌豁免",
    "反射豁免": "反射豁免",
    "施法者級別": "施法者等級",
    "施法者等級別": "施法者等級",
    "命中骰": "生命骰",
    "盔甲等級": "AC",
    "護甲等級": "AC",
    "生命值點數": "生命值",
    "生命點數": "生命值",
    "標準操作": "標準動作",
    "全回合動作": "整輪動作",
    "自由行動": "自由動作",
    "立即行動": "即時動作",
    "檢定難度": "難度等級",
}


def normalize_translation(value: str) -> str:
    value = value.strip()
    for source, target in TERM_REPLACEMENTS.items():
        value = value.replace(source, target)
    value = value.replace("英尺", "呎")
    return value


def split_chunks(value: str, limit: int = 3500) -> list[str]:
    if len(value) <= limit:
        return [value]
    paragraphs = value.splitlines()
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else current + "\n" + paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            cut = paragraph.rfind(". ", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


class CloudTranslator:
    def __init__(self, cache_path: Path, provider: str):
        self.cache_path = cache_path
        self.provider = provider
        self.microsoft_token = ""
        if cache_path.exists():
            self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            self.cache = {}

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def translate(self, text: str) -> str:
        if not text:
            return ""
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in self.cache:
            return self.cache[key]

        last_error = None
        delays = [2, 5, 15, 45, 90]
        for attempt, delay in enumerate(delays):
            try:
                if self.provider == "google":
                    data = urllib.parse.urlencode(
                        {"client": "gtx", "sl": "en", "tl": "zh-TW", "dt": "t", "q": text}
                    ).encode("utf-8")
                    request = urllib.request.Request(
                        "https://translate.googleapis.com/translate_a/single",
                        data=data,
                        headers={"User-Agent": "spellbook-offline-builder/1.0"},
                    )
                    with urllib.request.urlopen(request, timeout=60) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    translated = "".join(segment[0] for segment in payload[0] if segment[0])
                else:
                    if not self.microsoft_token:
                        auth_request = urllib.request.Request(
                            "https://edge.microsoft.com/translate/auth",
                            headers={"User-Agent": "Mozilla/5.0"},
                        )
                        with urllib.request.urlopen(auth_request, timeout=60) as response:
                            self.microsoft_token = response.read().decode("utf-8")
                    data = json.dumps([{"Text": text}]).encode("utf-8")
                    request = urllib.request.Request(
                        "https://api-edge.cognitive.microsofttranslator.com/translate?api-version=3.0&from=en&to=zh-Hant",
                        data=data,
                        headers={
                            "Authorization": "Bearer " + self.microsoft_token,
                            "Content-Type": "application/json",
                            "User-Agent": "Mozilla/5.0",
                        },
                    )
                    with urllib.request.urlopen(request, timeout=60) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    translated = payload[0]["translations"][0]["text"]
                translated = normalize_translation(translated)
                self.cache[key] = translated
                self.save()
                time.sleep(0.15)
                return translated
            except Exception as exc:
                last_error = exc
                if self.provider == "microsoft" and isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
                    self.microsoft_token = ""
                time.sleep(delay)
        raise RuntimeError(f"Translation failed after retries: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a translation catalog for English spell entries.")
    parser.add_argument("--input", type=Path, default=ROOT / "tmp" / "pdfs" / "az-parsed.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "translations.generated.json")
    parser.add_argument("--cache", type=Path, default=ROOT / "tmp" / "translations" / "google-cache.json")
    parser.add_argument("--provider", choices=("google", "microsoft"), default="microsoft")
    args = parser.parse_args()

    entries = [entry for entry in load_entries(args.input) if entry.needs_translation]
    translator = CloudTranslator(args.cache, args.provider)
    catalog = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}

    for number, entry in enumerate(entries, start=1):
        existing = catalog.get(entry.name_en, {})
        if existing.get("name_zh") and existing.get("description_zh"):
            if entry.name_en in NAME_OVERRIDES:
                existing["name_zh"] = NAME_OVERRIDES[entry.name_en]
            existing.setdefault("provider", args.provider + "-api")
            existing["needs_review"] = True
            catalog[entry.name_en] = existing
            args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{number}/{len(entries)}] cached {entry.name_en} -> {existing['name_zh']}", flush=True)
            continue
        source_values = [entry.name_en if not entry.name_zh else ""] + [getattr(entry, key) for key in FIELD_KEYS]
        bundle = ("\n" + DELIMITER + "\n").join(value or " " for value in source_values)
        translated_bundle = translator.translate(bundle)
        translated_values = [normalize_translation(value) for value in translated_bundle.split(DELIMITER)]
        if len(translated_values) != len(source_values):
            translated_values = [
                translator.translate(value) if re.search(r"[A-Za-z]", value or "") else value
                for value in source_values
            ]

        description_parts = [translator.translate(chunk) for chunk in split_chunks(entry.description_en)]
        description_zh = "\n".join(part for part in description_parts if part)
        translated = {
            "name_zh": entry.name_zh or NAME_OVERRIDES.get(entry.name_en, translated_values[0]),
            "description_zh": description_zh or entry.description_zh,
            "provider": args.provider + "-api",
            "needs_review": True,
        }
        for key, value in zip(FIELD_KEYS, translated_values[1:]):
            if value and value != getattr(entry, key):
                translated[key + "_zh"] = value
        existing.update(translated)
        catalog[entry.name_en] = existing
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{number}/{len(entries)}] {entry.name_en} -> {translated['name_zh']}", flush=True)


if __name__ == "__main__":
    main()
