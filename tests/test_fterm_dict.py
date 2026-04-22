"""modules/fterm_dict.py の分野別辞書ロードの回帰テスト"""

from modules.fterm_dict import (
    get_nodes,
    get_reverse_index,
    get_tree,
    _normalize_structure_to_tree,
)


class TestGetNodesByField:
    def test_cosmetics_has_many_nodes(self):
        nodes = get_nodes("cosmetics")
        assert len(nodes) > 100
        assert "AA01" in nodes or any(c.startswith("AA") for c in nodes)

    def test_laminate_loads_from_structure(self):
        """4F100 + 3E086 の structure 辞書がツリーに正規化されて読まれる"""
        nodes = get_nodes("laminate")
        assert len(nodes) >= 500, "laminate 辞書が公式データを読めていない"
        # 4F100 主要エントリ (JPO 公式コード / AK は衝突しないので素のキー)
        assert "AK01" in nodes
        assert "有機高分子" in nodes["AK01"]["label"]
        assert "AK05" in nodes and "高密度" in nodes["AK05"]["label"]
        # 衝突コードはテーマ接頭辞付きで格納される
        assert "4F100:AD01" in nodes  # 粘土製品 (4F100 セラミック)
        assert "3E086:AD01" in nodes  # 袋 (3E086 被包形態)
        assert "袋" in nodes["3E086:AD01"]["label"]

    def test_laminate_group_nodes_are_depth1(self):
        """カテゴリ（AK, BA, GB...）はグループノードとして depth=1 で登録される"""
        nodes = get_nodes("laminate")
        # AK は 4F100 にしか無いので素のキー
        assert "AK" in nodes
        assert nodes["AK"]["depth"] == 1
        assert nodes["AK"]["parent"] is None
        assert "AK01" in nodes["AK"]["children"]

    def test_laminate_multiple_themes(self):
        """laminate は 4F100 と 3E086 を統合している"""
        tree = get_tree("laminate")
        themes = tree.get("themes", [])
        assert "4F100" in themes
        assert "3E086" in themes

    def test_unknown_field_returns_empty(self):
        assert get_nodes("unknown_field_xyz") == {}


class TestReverseIndex:
    def test_laminate_reverse_index_common_terms(self):
        """実務でよく使う検索語から code へ逆引きできる (examples 経由)"""
        ri = get_reverse_index("laminate")
        # 4F100 系
        assert "PE" in ri and "AK04" in ri["PE"]
        assert "HDPE" in ri and "AK05" in ri["HDPE"]
        assert "PP" in ri and "AK07" in ri["PP"]
        # EVOH はエチレン-ビニルアルコール共重合体 = AK69 (オレフィン-酢酸ビニル共重合体加水分解物)
        assert "EVOH" in ri and "AK69" in ri["EVOH"]
        # PVC は AK15, PVDC は AK16
        assert "PVC" in ri and "AK15" in ri["PVC"]
        assert "PVDC" in ri and "AK16" in ri["PVDC"]
        # 3E086 系 (AD01/AD07 は 4F100 と衝突するので prefixed)
        assert "パウチ" in ri and "3E086:AD01" in ri["パウチ"]
        assert "ブリスター" in ri and "3E086:AD07" in ri["ブリスター"]

    def test_laminate_reverse_index_from_label(self):
        """label 自体からも逆引きできる (PDFから取得した公式ラベル)"""
        ri = get_reverse_index("laminate")
        # 高密度ポリエチレン のラベルは AK05
        assert "高密度ポリエチレン" in ri
        assert "AK05" in ri["高密度ポリエチレン"]


class TestNormalizeStructure:
    def test_minimal_structure(self):
        raw = {
            "theme_code": "XYZ",
            "theme_name": "テスト",
            "categories": {
                "AA": {
                    "label": "第一カテゴリ",
                    "entries": {
                        "AA01": {"label": "項目1", "examples": ["ex1"]},
                        "AA02": {"label": "項目2", "examples": []},
                    },
                }
            },
        }
        tree = _normalize_structure_to_tree(raw)
        assert tree["theme"] == "XYZ"
        assert set(tree["nodes"].keys()) == {"AA", "AA01", "AA02"}
        assert tree["nodes"]["AA"]["depth"] == 1
        assert tree["nodes"]["AA01"]["depth"] == 2
        assert tree["nodes"]["AA01"]["parent"] == "AA"
        assert tree["nodes"]["AA"]["children"] == ["AA01", "AA02"]
        assert tree["reverse_index"]["ex1"] == ["AA01"]
        assert tree["reverse_index"]["項目1"] == ["AA01"]

    def test_empty_structure(self):
        tree = _normalize_structure_to_tree({})
        assert tree["nodes"] == {}
        assert tree["reverse_index"] == {}
