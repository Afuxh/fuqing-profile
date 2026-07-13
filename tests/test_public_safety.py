import json
import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("generate_v3_7", ROOT / "generate_v3.7.py")
assert SPEC and SPEC.loader
generate_v3_7 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_v3_7)


class HotNewsSafetyTests(unittest.TestCase):
    def setUp(self):
        self.config = generate_v3_7.load_config(str(ROOT / "config.yaml"))

    def test_url_protocol_gate(self):
        self.assertEqual(generate_v3_7._safe_http_url("javascript:alert(1)"), "")
        self.assertEqual(generate_v3_7._safe_http_url("data:text/html,x"), "")
        self.assertEqual(generate_v3_7._safe_http_url("https://example.com/a?b=1"), "https://example.com/a?b=1")

    def test_public_topic_gate_requires_ai_and_blocks_adultized_tools(self):
        self.assertTrue(generate_v3_7.is_publishable_topic({"title": "Claude agent memory benchmark"}))
        self.assertFalse(generate_v3_7.is_publishable_topic({"title": "Quarterly earnings update"}))
        self.assertFalse(generate_v3_7.is_publishable_topic({"title": "AI-powered undress design tool"}))

    def test_xml_outputs_parse_and_sitemap_is_local(self):
        topic = {
            "title": "A & B",
            "url": "https://example.com/?a=1&b=2",
            "summary": "x < y & z",
            "summary_original": "x < y & z",
            "source_name": "Example",
            "score": 70,
            "level": {"name": "精选", "color": "#000"},
        }
        categories = {"精选": [topic], "推荐": [], "参考": [], "其他": []}
        ET.fromstring(generate_v3_7.generate_rss_feed(categories, self.config))
        sitemap = ET.fromstring(generate_v3_7.generate_sitemap(categories, self.config))
        locations = [node.text for node in sitemap.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertEqual(locations, ["https://afuxh.github.io/fuqing-profile/hot-news.html"])

    def test_generated_card_escapes_untrusted_feed_content(self):
        topic = {
            "title": "<script>alert(1)</script>",
            "url": "javascript:alert(1)",
            "summary": "<img src=x onerror=alert(1)>",
            "summary_original": "<img src=x onerror=alert(1)>",
            "source_name": "<b>source</b>",
            "score": 70,
            "hot_score": 0,
            "audience": "both",
            "value_tags": ["技术<前沿"],
            "level": {"name": "精选", "color": "#c8786a"},
        }
        categories = {"精选": [topic], "推荐": [], "参考": [], "其他": []}
        output = generate_v3_7.generate_html(categories, self.config)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", output)
        self.assertNotIn('href="javascript:', output)
        self.assertNotIn("<img src=x onerror=alert(1)>", output)

    def test_json_total_matches_categories(self):
        categories = {"精选": [], "推荐": [], "参考": [], "其他": [{"title": "hidden"}]}
        payload = json.loads(generate_v3_7.generate_json_api(categories, self.config))
        actual = sum(len(items) for items in payload["categories"].values())
        self.assertEqual(payload["meta"]["total"], actual)


class PublishedArtifactTests(unittest.TestCase):
    def test_hot_news_has_no_known_regressions(self):
        text = (ROOT / "hot-news.html").read_text(encoding="utf-8")
        self.assertNotIn("+ ' ' + zh", text)
        self.assertNotIn("edgeone.cool", text)
        self.assertNotIn("开发者阿扶", text)
        self.assertNotIn("微信号：fqxh_", text)

    def test_chat_report_is_aggregate_only(self):
        text = (ROOT / "chat-report" / "index.html").read_text(encoding="utf-8")
        for marker in ("wxid_", "@chatroom", "sender_name", "树林发言精选", "成员 · TOP"):
            self.assertNotIn(marker, text)
        self.assertNotRegex(text, r"https?://[^\s\"']+\?[^\s\"']+")


if __name__ == "__main__":
    unittest.main()
