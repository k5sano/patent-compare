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
        """structure 形式の 4F100 辞書がツリー形式に正規化されて読まれる"""
        nodes = get_nodes("laminate")
        assert len(nodes) >= 20, "laminate 辞書が空か極端に少ない"
        # 主要エントリの存在確認
        assert "AK01" in nodes
        assert "ポリオレフィン" in nodes["AK01"]["label"]
        assert "ポリエチレン" in nodes["AK01"]["examples"]
        assert nodes["AK01"]["parent"] == "AK"
        assert nodes["AK01"]["depth"] == 2

    def test_laminate_group_nodes_are_depth1(self):
        """カテゴリ（AK, BA, GB...）はグループノードとして depth=1 で登録される"""
        nodes = get_nodes("laminate")
        assert "AK" in nodes
        assert nodes["AK"]["depth"] == 1
        assert nodes["AK"]["parent"] is None
        assert "AK01" in nodes["AK"]["children"]

    def test_unknown_field_returns_empty(self):
        assert get_nodes("unknown_field_xyz") == {}


class TestReverseIndex:
    def test_laminate_reverse_index_from_examples(self):
        """examples に登場する語から code へ逆引きできる"""
        ri = get_reverse_index("laminate")
        assert "PE" in ri and "AK01" in ri["PE"]
        assert "PET" in ri and "AK25" in ri["PET"]

    def test_laminate_reverse_index_from_label(self):
        ri = get_reverse_index("laminate")
        assert "EVOH" in ri and "AK51" in ri["EVOH"]


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
