"""pdf_extractor.detect_tables の回帰防止テスト。

ユーザー報告: PDF テキスト抽出時にページ罫線/フッター
(例: "10 20 30 40 50 JP 2024-37328 A 2024.3.19") が末尾に紛れ込んだ通常段落が
表として誤検出され、UI モーダルに本文が表示されてしまっていた。

修正: 表ヘッダー (【表N】/Table N 等) を必須条件にして誤検出を抑える。
"""
from __future__ import annotations

import pytest

from modules.pdf_extractor import detect_tables


class TestRejectFalsePositives:
    def test_paragraph_with_footer_is_not_table(self):
        """段落本文 + ページフッター数字列を表と誤検出しない"""
        paras = [{
            "id": "0005",
            "page": 2,
            "section": "課題",
            "text": (
                "そこで、原液を泡状に吐出することで上記のようなデメリットを解決し液だれを防止出"
                "来る。しかし、油性原料を多量に含有する処方は油が消泡傾向を示す為に泡状吐出させる"
                "事が難しく、従来技術では液だれを泡で防止する事は困難であった。\n"
                "10 20 30 40 50 JP 2024-37328 A 2024.3.19 (3) くすることはさらに困難であった。"
            ),
        }]
        assert detect_tables(paras) == []

    def test_paragraph_mentioning_table_keyword_only_is_not_table(self):
        """段落本文中に '表' という単語が出るだけでは表扱いしない"""
        paras = [{
            "id": "0010", "page": 3, "section": "実施形態",
            "text": (
                "上記の方法によって表のような結果が得られた。"
                "この表現は当業者にとって明らかである。\n"
                "詳細は下記参照。"
            ),
        }]
        assert detect_tables(paras) == []


class TestAcceptRealTables:
    def test_kakko_kanji_header(self):
        """【表1】を含み、_looks_like_table を満たす段落は採用される"""
        rows = "\n".join([f"成分A\t{i}\t{i*2}\t{i*3}" for i in range(1, 8)])
        text = f"【表1】\n成分\t実施例1\t実施例2\t実施例3\n{rows}"
        paras = [{"id": "0040", "page": 5, "section": "実施例", "text": text}]
        result = detect_tables(paras)
        assert len(result) == 1
        assert result[0]["id"] == "表1"
        assert result[0]["page"] == 5
        assert "【表1】" in result[0]["content"]

    def test_table_n_with_brackets(self):
        rows = "\n".join([f"配合 {i}.0 {i*2}.0 {i*3}.0" for i in range(1, 6)])
        text = f"〔表 2〕\n成分名 実施例1 実施例2 実施例3\n{rows}"
        paras = [{"id": "0042", "page": 6, "section": "実施例", "text": text}]
        result = detect_tables(paras)
        assert len(result) == 1
        assert "〔表 2〕" in result[0]["content"]

    def test_english_table_header(self):
        rows = "\n".join([f"row{i}\t{i*1.1}\t{i*2.2}\t{i*3.3}" for i in range(1, 6)])
        text = f"Table 3\nname\tA\tB\tC\n{rows}"
        paras = [{"id": "0050", "page": 8, "section": "実施例", "text": text}]
        result = detect_tables(paras)
        assert len(result) == 1
        assert result[0]["id"] == "表1"

    def test_consecutive_table_ids_increment(self):
        def _mk(text, pid, page):
            return {"id": pid, "page": page, "section": "実施例", "text": text}
        rows = "\n".join([f"row{i}\t1.1\t2.2\t3.3\t4.4" for i in range(1, 6)])
        paras = [
            _mk(f"【表1】\nh\ta\tb\tc\n{rows}", "0040", 5),
            # ヘッダーなし、誤検出されない
            _mk("通常段落です。\n10 20 30 JP X 2024.3.19 (4)\n以下続く", "0041", 5),
            _mk(f"【表2】\nh\ta\tb\tc\n{rows}", "0042", 6),
        ]
        result = detect_tables(paras)
        assert len(result) == 2
        assert result[0]["id"] == "表1"
        assert result[1]["id"] == "表2"


class TestRejectOcrGarbledTables:
    """画像表を OCR したときに発生する「孤立 1 文字 CJK の羅列」を弾く。

    実データ (2024-080911 / 2024-051653 / 2025-47348 など) で検出された
    OCR 化け表の特徴:
      - 単一 CJK 文字 token (例: '回 責 男 画 画') が token 全体の 50%超
      - 一見すると【表N】を含んでいて、_looks_like_table も通ってしまう
    本物の表は単語 (2 文字以上) が並ぶので、ratio で確実に分離できる。
    """

    def test_garbled_single_chars_rejected(self):
        # 表ヘッダーは含むが、中身が 1 文字 CJK の羅列 (実データ 2024-080911 表1 を簡略化)
        garbled = (
            "(14) JP 2024-80911 A 2024.6.17 【 表 2 】 "
            + " ".join(["回", "責", "男", "画", "画", "画", "男", "男"] * 30)
            + " 10 20 30 40 50"
        )
        paras = [{"id": "0089", "page": 13, "section": "実施例", "text": garbled}]
        assert detect_tables(paras) == [], "OCR 化け表は除外されるべき"

    def test_real_table_with_meaningful_tokens_kept(self):
        # 意味ある単語 (2+ chars) が並ぶ本物の表 (wa-2024-541157 の表に類似)
        rows = "\n".join([
            "成分名\t実施例1\t実施例2\t実施例3\t実施例4",
            "フェニルベンゾイミダゾール\t10\t20\t30\t40",
            "メチレンビススルホン酸\t5\t10\t15\t20",
            "ステアリン酸グリセリル\t100\t100\t100\t100",
            "比較例の配合データ\t1.0\t2.0\t3.0\t4.0",
        ])
        text = f"【表1】\n{rows}"
        paras = [{"id": "0040", "page": 5, "section": "実施例", "text": text}]
        result = detect_tables(paras)
        assert len(result) == 1, "意味あるトークンが並ぶ本物の表は採用されるべき"


class TestEdgeCases:
    def test_short_text_rejected(self):
        # 50 字以下は拒否
        paras = [{"id": "0001", "page": 1, "section": "x", "text": "【表1】 短い"}]
        assert detect_tables(paras) == []

    def test_empty_list(self):
        assert detect_tables([]) == []
