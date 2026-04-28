"""Update 4C083 Fターム dictionary from the J-PlatPat official table PDF.

Source: 4C083 Fタームテーブル.pdf (J-PlatPat https://www.j-platpat.inpit.go.jp/p1113)

What this script does:
  1. Loads existing dictionaries/cosmetics/fterm_4c083_tree.json
  2. Replaces / extends nodes with the full official structure (AA/AB/AC/AD/BB/CC/DD/EE/FF/KK)
  3. Preserves the existing examples/definition fields where the code already existed
     (those entries were carefully curated from nakajimaip.jp + PMGS Fターム解説).
  4. Records the FI適用範囲 metadata per viewpoint (PDF column "FI適用範囲").
  5. Extends reverse_index with labels of newly added nodes (BB/CC/DD/EE/FF/KK).
  6. Writes back to the same JSON path.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JSON_PATH = REPO / "dictionaries" / "cosmetics" / "fterm_4c083_tree.json"

# (depth, code, label) for every Fターム row in the PDF.
# depth = number of "・" prefix characters in the PDF cell;
# the "XX00" viewpoint heading row is depth 0 with parent = None.
DATA: list[tuple[int, str, str]] = [
    # ========== AA ==========
    (0, "AA00", "天然系成分と構造，組成が不明な成分"),
    (1, "AA01", "構造，組成が不明な物質"),
    (1, "AA02", "天然系物質"),
    (2, "AA03", "微生物による物質"),
    (2, "AA07", "動物由来物質"),
    (3, "AA08", "動物由来のロウ，ワックス，油脂"),
    (2, "AA11", "植物由来物質"),
    (3, "AA12", "植物由来のロウ，ワックス，油脂"),
    (2, "AA16", "鉱物由来物質"),
    # ========== AB ==========
    (0, "AB00", "無機系成分"),
    (1, "AB01", "無機酸"),
    (1, "AB03", "無機塩基，水酸化物"),
    (1, "AB05", "水"),
    (1, "AB06", "酸素"),
    (1, "AB08", "窒素化合物"),
    (1, "AB10", "ハロゲン化合物（フッ素はＡＢ４７へ）"),
    (1, "AB11", "イオウ化合物"),
    (1, "AB13", "炭素化合物"),
    (1, "AB15", "硼素化合物"),
    (1, "AB17", "珪素化合物"),
    (1, "AB19", "金属"),
    (1, "AB21", "金属化合物（アルカリ金属，土類金属の水酸化物を除く）"),
    (2, "AB22", "アルミ化合物"),
    (2, "AB23", "鉄化合物"),
    (2, "AB24", "チタン化合物"),
    (1, "AB27", "無機塩"),
    (2, "AB28", "リン酸塩"),
    (3, "AB29", "アルカリ土類金属リン酸塩"),
    (2, "AB31", "炭酸塩"),
    (3, "AB32", "アルカリ土類金属炭酸塩"),
    (2, "AB33", "ハロゲン化合物"),
    (3, "AB34", "アルカリ土類金属ハロゲン化合物"),
    (2, "AB35", "硫酸塩（亜硫酸塩含む）"),
    (3, "AB36", "アルカリ土類金属硫酸塩（亜硫酸塩含む）"),
    (2, "AB37", "珪酸塩"),
    (3, "AB38", "アルカリ土類金属珪酸塩"),
    (1, "AB41", "過酸化物"),
    (1, "AB43", "珪酸塩鉱物"),
    (2, "AB44", "粘土鉱物（ゼオライトを含む）"),
    (1, "AB47", "フッ素化合物"),
    (1, "AB50", "その他無機化合物"),
    # ========== AC ==========
    (0, "AC00", "元素で特徴づけられる有機系成分"),
    (1, "AC01", "炭化水素（重合体を除く）"),
    (2, "AC02", "流動パラフィン，スクワラン"),
    (2, "AC03", "環式炭化水素"),
    (1, "AC05", "酸素含有化合物"),
    (2, "AC06", "アルコール（フェノールはＡＣ４７）"),
    (3, "AC07", "１価アルコール（脂肪族アルコール）"),
    (4, "AC08", "１価不飽和アルコール"),
    (4, "AC09", "１価分岐アルコール"),
    (4, "AC10", "１価低級アルコール（Ｃ７以下）"),
    (3, "AC11", "多価アルコール"),
    (4, "AC12", "グリセリン，１，３―ＢＧ，ＰＧ"),
    (4, "AC13", "糖アルコール"),
    (3, "AC15", "芳香族アルコール"),
    (2, "AC17", "エーテル"),
    (3, "AC18", "ＰＯＡ付加体"),
    (2, "AC21", "アルデヒト，ケトン（キノンはＡＣ４９）"),
    (2, "AC23", "カルボン酸（有機酸）"),
    (3, "AC24", "脂肪酸"),
    (4, "AC25", "不飽和脂肪酸"),
    (4, "AC26", "分岐脂肪酸"),
    (4, "AC27", "低級脂肪酸（Ｃ７以下）"),
    (3, "AC29", "多価カルボン酸"),
    (3, "AC30", "オキシカルボン酸"),
    (3, "AC31", "環式構造を有するカルボン酸（芳香族含む）"),
    (2, "AC33", "エステル（油脂はＡＡへ）"),
    (3, "AC34", "１価アルコールの１価カルボン酸エステル"),
    (4, "AC35", "脂肪酸エステル"),
    (3, "AC37", "１価アルコールの多価カルボン酸エステル"),
    (3, "AC39", "多価アルコールの１価カルボン酸エステル"),
    (3, "AC40", "ＰＯＡ体を含むエステル"),
    (3, "AC42", "（ポリ）グリセリンエステル（ＰＯＡ体含む）"),
    (4, "AC43", "ヒマシ油エステル（ＰＯＡ体含む）"),
    (3, "AC44", "糖アルコールエステル（ＰＯＡ体含む）"),
    (2, "AC46", "過酸"),
    (2, "AC47", "フェノール類"),
    (3, "AC48", "パラベン類（パラオキシン安息香酸類）"),
    (2, "AC49", "キノン"),
    (1, "AC51", "窒素含有化合物"),
    (2, "AC52", "アミン類"),
    (3, "AC53", "脂肪族アミン（ＰＯＡ体含む）"),
    (4, "AC54", "アルカノールアミン"),
    (3, "AC55", "芳香族アミン（ＰＯＡ体含む）"),
    (3, "AC56", "アミンオキシド"),
    (2, "AC58", "アミノ酸及びそのエステル"),
    (3, "AC61", "ピロリドンカルボン酸"),
    (3, "AC62", "α以外のアミノ酸"),
    (2, "AC64", "アミド（ＰＯＡ体含む）"),
    (2, "AC66", "アシルアミノ酸"),
    (2, "AC68", "尿素（カルバミン酸，アラントイン等）"),
    (2, "AC69", "第４級アンモニウム塩"),
    (2, "AC71", "ベタイン"),
    (2, "AC73", "ニトロ化合物"),
    (2, "AC74", "グアニド（クロルヘキシジン含む）"),
    (1, "AC76", "硫黄含有化合物（アミノ酸，ベタイン除く）"),
    (2, "AC77", "ＳＨ基を含有する化合物"),
    (2, "AC78", "ＯＳＯ３基（サルフェート）（４級塩はＡＣ６９へ）"),
    (2, "AC79", "ＳＯ３基（スルホネート）（タウリン含む）"),
    (1, "AC81", "ハロゲン化合物（４級塩はＡＣ６９へ）"),
    (1, "AC83", "複素環（アミノ酸を除く）"),
    (2, "AC84", "含酸素複素環"),
    (2, "AC85", "含窒素（アラントイン，ベタイン，４級塩ピロリドンを除く）"),
    (2, "AC86", "含硫黄複素環"),
    (1, "AC88", "リン含有化合物"),
    (2, "AC89", "Ｐ―Ｃ結合を有するもの"),
    (2, "AC90", "リン酸誘導体（リン酸エステル，アミド）"),
    (1, "AC91", "珪素含有化合物（ｎ＞＝２重合物はＡＤ１５へ）"),
    (1, "AC93", "金属含有有機化合物（錯塩）"),
    # ========== AD ==========
    (0, "AD00", "構造で特徴づけられる有機系成分"),
    (1, "AD01", "重合体"),
    (2, "AD02", "炭化水素，ハロゲン含有（共）重合体"),
    (2, "AD04", "ポリアルキレンオキシド（共）重合体（付加体はＡＣへ）"),
    (3, "AD05", "ＰＯＰ―ＰＯＥブロックポリマー"),
    (2, "AD07", "窒素含有（共）重合体"),
    (2, "AD09", "カルボン酸，そのエステル含有（共）重合体"),
    (2, "AD11", "アルコール，そのエーテル含有（共）重合体"),
    (2, "AD13", "第４級窒素含有重合体（カチオン化セルロース）"),
    (2, "AD15", "珪素含有重合体（シリコーン）（ｎ＞＝２の重合体）"),
    (3, "AD16", "変性シリコーン"),
    (3, "AD17", "環状シリコーン"),
    (1, "AD19", "糖類（エステル，エーテル含む）"),
    (2, "AD20", "単糖類（糖アルコールはＡＣ１３へ）"),
    (2, "AD21", "多糖類（糖アルコールはＡＣ１３へ）"),
    (3, "AD22", "蔗糖"),
    (3, "AD24", "澱粉（デキストリン等）"),
    (3, "AD25", "シクロデキストリン"),
    (3, "AD26", "セルロース類（カチオン化はＡＤ１３へ）"),
    (4, "AD27", "カルボキシメチルセルロース"),
    (4, "AD28", "ヒドロキシアルキルセルロース"),
    (3, "AD30", "アルギン酸"),
    (3, "AD31", "ムコ多糖類"),
    (4, "AD32", "キチン，キトサン"),
    (4, "AD33", "ヒアルロン酸"),
    (4, "AD34", "コンドロイチン硫酸"),
    (3, "AD35", "ガム質(クインスシード，カラギーナン等)"),
    (3, "AD37", "ペクチン"),
    (3, "AD39", "配糖体（糖脂質を含む）"),
    (1, "AD41", "蛋白質（加水分解誘導体を含む）"),
    (2, "AD42", "カゼイン"),
    (2, "AD43", "コラーゲン"),
    (2, "AD44", "ケラチン"),
    (2, "AD45", "シルク（フィブロイン）"),
    (1, "AD47", "酵素"),
    (1, "AD49", "ステロイド（コレステロール等）"),
    (1, "AD51", "ラノリン（水添，ＰＯＡ体含む）"),
    (1, "AD53", "テルペノイド（グリチルリチン酸等）"),
    (1, "AD55", "トロポロン（ヒノキチオール等）"),
    (1, "AD57", "リン脂質（レシチン等）"),
    (1, "AD59", "ホルモン"),
    (1, "AD60", "核酸"),
    (1, "AD61", "ビタミン"),
    (2, "AD62", "ビタミンＡ"),
    (2, "AD63", "ビタミンＢ"),
    (2, "AD64", "ビタミンＣ"),
    (2, "AD65", "ビタミンＤ"),
    (2, "AD66", "ビタミンＥ"),
    (2, "AD67", "ビタミンＫ"),
    (1, "AD70", "その他有機化合物"),
    # ========== BB ==========
    (0, "BB00", "機能特定成分"),
    (1, "BB01", "界面活性剤（乳化剤，可溶化剤，分散剤）"),
    (2, "BB02", "親水性"),
    (2, "BB03", "親油性"),
    (2, "BB04", "非イオン性"),
    (2, "BB05", "陰イオン性"),
    (2, "BB06", "陽イオン性"),
    (2, "BB07", "両性"),
    (1, "BB11", "油"),
    (2, "BB12", "固形，半固形油（ペースト状）"),
    (2, "BB13", "液状油"),
    (2, "BB14", "揮発性油"),
    (1, "BB21", "粉体，顔料，色素，染料（着色料，粒子）"),
    (2, "BB22", "天然"),
    (2, "BB23", "無機"),
    (2, "BB24", "有機（合成）"),
    (2, "BB25", "処理加工粉末"),
    (2, "BB26", "形状に特徴のあるもの"),
    (1, "BB31", "高分子（付与しない，ＡＤ０１を付与）"),
    (2, "BB32", "非イオン性高分子"),
    (2, "BB33", "陰イオン性高分子"),
    (2, "BB34", "陽イオン性高分子"),
    (2, "BB35", "両性高分子"),
    (2, "BB36", "水溶性高分子"),
    (1, "BB41", "香料（精油）"),
    (1, "BB42", "酸"),
    (1, "BB43", "塩基（アルカリ）"),
    (1, "BB44", "ＰＨ調整剤（緩衝液）"),
    (1, "BB45", "キレート剤（金属イオン封鎖剤）"),
    (1, "BB46", "紫外線吸収剤"),
    (1, "BB47", "酸化防止剤"),
    (1, "BB48", "殺菌，防腐剤"),
    (1, "BB49", "噴射剤"),
    (1, "BB51", "皮膚に機能を付与する剤"),
    (1, "BB53", "毛髪に機能を付与する剤"),
    (1, "BB55", "口腔に機能を付与する剤"),
    (1, "BB60", "その他"),
    # ========== CC ==========
    (0, "CC00", "製品の種類"),
    (1, "CC01", "一般化粧料"),
    (1, "CC02", "皮膚用"),
    (2, "CC03", "基礎化粧料（プレメーク，化粧下地を含む）"),
    (3, "CC04", "化粧水"),
    (3, "CC05", "乳液，クリーム"),
    (3, "CC06", "化粧油"),
    (3, "CC07", "パック"),
    (2, "CC11", "メーキャップ化粧料"),
    (3, "CC12", "ファンデーション（白粉，パウダーを含む）"),
    (3, "CC13", "口紅"),
    (3, "CC14", "アイメーキャップ"),
    (2, "CC17", "制汗，デオドラント（ボディパウダーを含む）"),
    (2, "CC18", "脱毛，除毛剤"),
    (2, "CC19", "日焼け止め（サンタンを含む）"),
    (3, "CC20", "虫除け"),
    (2, "CC21", "ひげそり用化粧料"),
    (2, "CC22", "身体の清浄用"),
    (3, "CC23", "洗浄剤（洗顔料，ボディ洗浄剤）"),
    (3, "CC24", "清浄剤（清拭剤）"),
    (2, "CC25", "浴用剤"),
    (1, "CC28", "爪用"),
    (2, "CC29", "除去液（リムーバー）"),
    (1, "CC31", "毛髪用，頭皮用"),
    (2, "CC32", "整髪料（セット料）"),
    (2, "CC33", "ヘアトリートメント，コンディショニング"),
    (2, "CC34", "パーマ剤（ウェーブ剤）"),
    (2, "CC35", "脱色剤（ブリーチ剤）"),
    (2, "CC36", "染毛剤"),
    (2, "CC37", "養毛，育毛，発毛剤"),
    (2, "CC38", "シャンプー"),
    (2, "CC39", "リンス"),
    (1, "CC41", "口腔，歯科"),
    (2, "CC42", "義歯洗浄剤"),
    (1, "CC50", "その他"),
    # ========== DD ==========
    (0, "DD00", "製品の形態"),
    (1, "DD01", "透明（半透明）"),
    (1, "DD02", "白濁（パール，ラスターも含む）"),
    (1, "DD04", "多色（模様入り含む）"),
    (1, "DD05", "多層（相）"),
    (1, "DD06", "多剤"),
    (1, "DD08", "エアゾール（スプレー，フォームも含む）"),
    (1, "DD11", "スティック状（棒状，ペンシル状）"),
    (1, "DD12", "シート状（フィルムも含む）"),
    (1, "DD14", "カプセル"),
    (1, "DD15", "錠剤"),
    (1, "DD16", "顆粒（粒状）"),
    (1, "DD17", "粉末（パウダー）"),
    (1, "DD21", "固型状物"),
    (1, "DD22", "半固型状物（ペースト）"),
    (1, "DD23", "液状物"),
    (1, "DD25", "気体"),
    (1, "DD27", "水系"),
    (1, "DD28", "非水系"),
    (1, "DD30", "油系"),
    (1, "DD31", "乳化系（クリーム状，乳液状）"),
    (2, "DD32", "Ｗ／Ｏ"),
    (2, "DD33", "Ｏ／Ｗ"),
    (2, "DD34", "多層乳化（Ｗ／Ｏ／Ｗ，Ｏ／Ｗ／Ｏ）"),
    (2, "DD35", "マイクロエマルジョン"),
    (1, "DD38", "可溶化系"),
    (1, "DD39", "分散系"),
    (1, "DD41", "ゲル"),
    (1, "DD42", "ゾル"),
    (1, "DD44", "液晶（ラメラ，ヘキサゴナル，キュービック）"),
    (1, "DD45", "リポソーム"),
    (1, "DD47", "包装，容器"),
    (2, "DD48", "溶出性袋"),
    (1, "DD50", "その他"),
    # ========== EE ==========
    (0, "EE00", "効果"),
    (1, "EE01", "安定性に関するもの"),
    (1, "EE03", "品質に関するもの"),
    (1, "EE05", "使用性に関するもの"),
    (2, "EE06", "感覚的な使用性"),
    (2, "EE07", "物性的な使用特性"),
    (1, "EE09", "安全性に関するもの"),
    (2, "EE10", "皮膚の安全性"),
    (1, "EE11", "皮膚，頭皮に対する効果"),
    (2, "EE12", "皮膚，頭皮の保護，賦活"),
    (2, "EE13", "皮膚，頭皮の治癒"),
    (3, "EE14", "にきび治療"),
    (2, "EE16", "美白（チロシナーゼ阻害，メラニン抑制）"),
    (2, "EE17", "日焼け防止（サンタンも含む）"),
    (2, "EE18", "消臭，防臭"),
    (1, "EE21", "毛髪に特有の効果"),
    (2, "EE22", "養毛（育毛，発毛，脱毛防止）"),
    (2, "EE23", "フケ防止"),
    (2, "EE24", "白髪防止"),
    (2, "EE25", "カール効果，カール保持（ウェーブ付与）"),
    (2, "EE26", "染毛"),
    (2, "EE27", "脱色（ブリーチ）"),
    (2, "EE28", "状態を整える効果（コンディショニング）"),
    (2, "EE29", "毛髪保護（損傷防止，枝毛防止）"),
    (1, "EE31", "口腔歯用に特有の効果"),
    (2, "EE32", "虫歯予防（う触予防，再石灰化）"),
    (2, "EE33", "歯周疾患防止（歯肉炎予防，止血効果）"),
    (2, "EE34", "口臭防止"),
    (2, "EE35", "美白（ヤニ除去，研磨力，清浄力）"),
    (2, "EE36", "歯垢防止（プラーク除去）"),
    (2, "EE37", "歯石防止"),
    (2, "EE38", "知覚過敏"),
    (1, "EE41", "入浴用に特有の効果"),
    (2, "EE42", "薬湯効果"),
    (1, "EE50", "その他"),
    # ========== FF ==========
    (0, "FF00", "製造方法，装置"),
    (1, "FF01", "原材料の製造方法，装置"),
    (1, "FF04", "製品の製造方法，装置の特徴"),
    (2, "FF05", "混合，乳化，粉砕"),
    (2, "FF06", "充填，成型"),
    (2, "FF07", "減菌，殺菌，消毒工程"),
    (1, "FF10", "その他"),
    # ========== KK ==========
    (0, "KK00", "香料用の製剤または添加剤"),
    (1, "KK01", "香料組成物"),
    (2, "KK02", "香料成分に特徴のあるもの"),
    (2, "KK03", "香料の担体、保留剤に特徴のあるもの"),
    (1, "KK11", "線香；その関連物（例．香袋、薫香）"),
    (2, "KK12", "線香"),
    (2, "KK13", "線香の製造法又は装置"),
]

# Per-viewpoint FI適用範囲 (PDF rightmost column)
VIEWPOINT_FI_RANGE: dict[str, str] = {
    "AA": "A61K8/00-8/99;A61Q1/00-90/00",
    "AB": "A61K8/00-8/99;A61Q1/00-90/00",
    "AC": "A61K8/00-8/99;A61Q1/00-90/00",
    "AD": "A61K8/00-8/99;A61Q1/00-90/00",
    "BB": "A61K8/00-8/99;A61Q1/00-90/00",
    "CC": "A61K8/00-8/99;A61Q1/00-11/02;15/00-90/00",
    "DD": "A61K8/00-8/99;A61Q1/00-90/00",
    "EE": "A61K8/00-8/99;A61Q1/00-90/00",
    "FF": "A61K8/00-8/99;A61Q1/00-90/00",
    "KK": "A61K8/00-8/99;A61Q13/00-13/00,202",
}


_BRACKET_PAT = re.compile(r"[（(][^）)]*[）)]")


def _label_keywords(label: str) -> list[str]:
    """Extract crude search keywords from a label string."""
    if not label:
        return []
    s = label.replace("＊", "").strip()
    # split off parenthetical hints (use them too as separate keywords)
    paren = _BRACKET_PAT.findall(s)
    base = _BRACKET_PAT.sub("", s).strip()
    parts: list[str] = []
    for chunk in re.split(r"[，,；;・]", base):
        chunk = chunk.strip()
        if chunk and len(chunk) >= 2:
            parts.append(chunk)
    for p in paren:
        body = p.strip("（）()")
        body = re.sub(r"^[^：:]*[：:]", "", body)  # drop leading "例：" etc
        for chunk in re.split(r"[，,；;・]", body):
            chunk = chunk.strip()
            if chunk and len(chunk) >= 2 and "は" not in chunk[:3]:
                parts.append(chunk)
    # de-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for k in parts:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def build_nodes(existing: dict[str, dict]) -> dict[str, dict]:
    """Build nodes from DATA, merging in existing definitions/examples."""
    nodes: dict[str, dict] = {}
    # depth -> last seen code, used to find parent
    depth_stack: dict[int, str] = {}

    for depth, code, label in DATA:
        # parent = nearest code with depth-1 (or None for depth 0)
        parent = None
        if depth > 0:
            for d in range(depth - 1, -1, -1):
                if d in depth_stack:
                    parent = depth_stack[d]
                    break

        prev = existing.get(code) or {}
        node = {
            "label": label,
            "definition": prev.get("definition", ""),
            "depth": depth,
            "parent": parent,
            "children": [],
            "examples": list(prev.get("examples", [])),
            "fi_range": VIEWPOINT_FI_RANGE.get(code[:2], ""),
        }
        nodes[code] = node
        depth_stack[depth] = code
        # Clear deeper levels that no longer apply
        for d in list(depth_stack.keys()):
            if d > depth:
                del depth_stack[d]

    # populate children
    for code, node in nodes.items():
        p = node["parent"]
        if p and p in nodes and code not in nodes[p]["children"]:
            nodes[p]["children"].append(code)

    return nodes


def build_reverse_index(nodes: dict[str, dict], existing_rev: dict[str, list[str]]) -> dict[str, list[str]]:
    """Merge existing reverse_index with label-derived keywords for new viewpoints."""
    out: dict[str, list[str]] = {}
    for k, v in existing_rev.items():
        out[k] = list(v)

    # Add label-derived keywords for ALL nodes (especially BB/CC/DD/EE/FF/KK
    # which had no examples in the previous dict).
    for code, node in nodes.items():
        for kw in _label_keywords(node["label"]):
            out.setdefault(kw, [])
            if code not in out[kw]:
                out[kw].append(code)
        # Also index examples (already done in old data, but ensure consistency)
        for ex in node.get("examples", []):
            ex = (ex or "").strip()
            if not ex:
                continue
            out.setdefault(ex, [])
            if code not in out[ex]:
                out[ex].append(code)

    # Sort by key for stable output
    return {k: out[k] for k in sorted(out.keys())}


def main() -> int:
    if not JSON_PATH.exists():
        print(f"[error] not found: {JSON_PATH}", file=sys.stderr)
        return 1
    cur = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    existing_nodes = cur.get("nodes", {})
    existing_rev = cur.get("reverse_index", {})

    new_nodes = build_nodes(existing_nodes)
    new_rev = build_reverse_index(new_nodes, existing_rev)

    new_dict = {
        "theme": "4C083",
        "name": "化粧料",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": (
            "J-PlatPat 公式 4C083 Fタームテーブル "
            "(https://www.j-platpat.inpit.go.jp/p1113) + nakajimaip.jp + PMGS"
        ),
        "fi_cover_range": "A61K8/00-8/99;A61Q1/00-90/00",
        "viewpoints": {
            "AA": "天然系成分と構造，組成が不明な成分",
            "AB": "無機系成分",
            "AC": "元素で特徴づけられる有機系成分",
            "AD": "構造で特徴づけられる有機系成分",
            "BB": "機能特定成分",
            "CC": "製品の種類",
            "DD": "製品の形態",
            "EE": "効果",
            "FF": "製造方法，装置",
            "KK": "香料用の製剤または添加剤",
        },
        "viewpoint_fi_range": VIEWPOINT_FI_RANGE,
        "nodes": new_nodes,
        "reverse_index": new_rev,
    }

    JSON_PATH.write_text(
        json.dumps(new_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # report
    from collections import Counter
    by_vp = Counter(c[:2] for c in new_nodes)
    added = [c for c in new_nodes if c not in existing_nodes]
    removed = [c for c in existing_nodes if c not in new_nodes]
    print(f"[ok] wrote {JSON_PATH}")
    print(f"     total nodes : {len(new_nodes)} (was {len(existing_nodes)})")
    print(f"     by viewpoint: {dict(by_vp)}")
    print(f"     added       : {len(added)}  (e.g., {added[:8]})")
    if removed:
        print(f"     removed     : {len(removed)}  (e.g., {removed[:8]})")
    print(f"     reverse_index: {len(new_rev)} (was {len(existing_rev)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
