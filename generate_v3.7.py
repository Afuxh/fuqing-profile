"""
Hot News Aggregator - 通用热点聚合网站生成器
支持可配置的评分档位、主题、动画等
"""
import json
import subprocess
import re
import yaml
import asyncio
import logging
import argparse
import time
import hashlib
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
_keyword_hit_stats = {}  # keyword -> {"hits": N, "last_hit": timestamp}

import httpx
import email.mime.text
import email.mime.multipart
import smtplib
import os
import sys
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

# 导入版本信息（单一数据源）
try:
    from version import __version__, get_generator_name, get_full_title
except ImportError:
    __version__ = "3.7.1"
    get_generator_name = lambda: f"热点聚合网站生成器 v{__version__}"
    get_full_title = lambda: "AI 时代观察 - 效率归机器，意义归人类"

# ============ 初始化 ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)


# ============ 进度条工具 ============
class ProgressBar:
    """简易终端进度条，支持时间预估"""
    
    def __init__(self, total: int, desc: str = "", width: int = 40):
        self.total = max(total, 1)
        self.current = 0
        self.desc = desc
        self.width = width
        self.start_time = time.time()
        self.last_update = 0
    
    def update(self, n: int = 1):
        self.current = min(self.current + n, self.total)
        now = time.time()
        if now - self.last_update < 0.1 and self.current < self.total:
            return
        self.last_update = now
        self._render()
    
    def _render(self):
        pct = self.current / self.total
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        elapsed = time.time() - self.start_time
        if pct > 0 and pct < 1:
            eta = elapsed / pct - elapsed
            eta_str = f" 预计剩余 {self._fmt_time(eta)}"
        elif pct >= 1:
            eta_str = f" 用时 {self._fmt_time(elapsed)}"
        else:
            eta_str = ""
        # 使用 \\r 原地刷新（非 Actions 环境友好）
        sys.stderr.write(f"\r  {self.desc} [{bar}] {self.current}/{self.total} ({pct*100:.0f}%){eta_str}   ")
        sys.stderr.flush()
        if self.current >= self.total:
            sys.stderr.write("\n")
    
    @staticmethod
    def _fmt_time(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            return f"{seconds/60:.0f}分{seconds%60:.0f}秒"
        else:
            h = int(seconds / 3600)
            m = int((seconds % 3600) / 60)
            return f"{h}时{m}分"


def save_data_snapshot(topics: list, config: dict, output_dir: str = "data"):
    """保存数据快照到 data/ 目录，用于趋势分析"""
    data_path = Path(output_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    
    # 计算跨源出现次数（同一标题在不同源出现的次数作为补充热度指标）
    title_counts = {}
    for t in topics:
        norm_title = t.get("title", "").strip().lower()
        title_counts[norm_title] = title_counts.get(norm_title, 0) + 1

    snapshot = {
        "timestamp": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(topics),
        "topics": [
            {
                "topic_id": hashlib.sha256(t.get("title", "").strip().lower().encode()).hexdigest()[:12],
                "title": t.get("title", ""),
                "url": t.get("url", ""),
                "source": t.get("source", ""),
                "source_name": t.get("source_name", ""),
                "score": t.get("score", 0),
                "hot_score": t.get("hot_score", 0),
                "level": t.get("level", {}).get("name", ""),
                "cross_source_count": title_counts.get(t.get("title", "").strip().lower(), 1),
                "zh_summary": t.get("zh_summary", "")[:100],
                "audience": t.get("audience", "both"),
                "perspective_comment": (t.get("perspective_comment", "") or "")[:120],
                "action_guidance": (t.get("action_guidance", "") or "")[:120],
            }
            for t in topics
        ]
    }
    
    filename = f"snapshot_{NOW.strftime('%Y%m%d_%H%M')}.json"
    filepath = data_path / filename
    filepath.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # 更新 latest.json 符号引用
    latest_path = data_path / "latest.json"
    latest_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # 清理超过30天的旧快照
    _cleanup_old_snapshots(data_path, max_days=30)

    # 同步到 history_data 目录（长期积累）
    try:
        history_dir = Path(r"d:\新建文件夹\history_data\hot_news")
        history_dir.mkdir(parents=True, exist_ok=True)
        history_file = history_dir / filename
        shutil.copy2(filepath, history_file)
        logger.info(f"  历史存档: {history_file}")
    except Exception as e:
        logger.warning(f"  历史存档失败: {e}")

    logger.info(f"  数据快照: {filepath.name} ({len(topics)} 条)")
    return filepath


def _cleanup_old_snapshots(data_path: Path, max_days: int = 30):
    """清理超过 max_days 天的旧快照文件"""
    import glob as _glob
    cutoff = NOW - timedelta(days=max_days)
    for f in data_path.glob("snapshot_*.json"):
        try:
            # 从文件名解析日期: snapshot_YYYYMMDD_HHMM.json
            name = f.stem
            date_str = name.replace("snapshot_", "")
            if len(date_str) >= 8:
                file_date = datetime.strptime(date_str[:8], "%Y%m%d").replace(tzinfo=CST)
                if file_date < cutoff:
                    f.unlink()
                    logger.debug(f"  清理旧快照: {f.name}")
        except (ValueError, OSError):
            pass


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"配置文件 {config_path} 不存在，使用默认配置")
        return get_default_config()
    
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_default_config() -> dict:
    """获取默认配置"""
    return {
        "site": {
            "title": "AI 时代观察",
            "subtitle": "效率归机器，意义归人类",
            "description": "追踪AI前沿动态，为独立思考提供信息底座。扶清闲话 · AI时代观察栏目。",
            "author": "扶清闲话",
            "url": ""
        },
        "sources": {
            "hackernews": {"enabled": True, "limit": 50},
            "github": {"enabled": True, "queries": ["ai", "llm"], "days": 7, "limit": 15},
            "producthunt": {"enabled": True, "limit": 40}
        },
        "scoring": {
            "levels": [
                {"name": "精选", "threshold": 80, "color": "#c8786a", "description": "高质量内容"},
                {"name": "推荐", "threshold": 70, "color": "#b8956a", "description": "值得阅读"},
                {"name": "参考", "threshold": 60, "color": "#8a9598", "description": "可作参考"}
            ],
            "keywords": {
                "core": ["AI", "LLM", "GPT", "Claude", "Agent"],
                "extended": ["开源", "工具", "效率"],
                "exclude": ["股票", "娱乐", "游戏"]
            }
        },
        "theme": {
            "mode": "dark",
            "dark": {
                "background": "#121118",
                "card": "rgba(28,26,35,0.65)",
                "text_primary": "#e8e0d4",
                "text_secondary": "#9a9284",
                "accent": "#c8a45c",
                "border": "rgba(150,140,120,0.1)"
            },
            "light": {
                "background": "#f8f5f0",
                "card": "rgba(255,252,245,0.85)",
                "text_primary": "#2c2822",
                "text_secondary": "#6b655c",
                "accent": "#a08050",
                "border": "rgba(160,140,110,0.15)"
            }
        },
        "animations": {"enabled": True, "breathe": True, "pulse": True, "float": True, "hover": True},
        "misc": {"show_score": True, "show_source": True, "show_update_time": True}
    }


# ============ 数据抓取 ============
async def fetch_hackernews(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 Hacker News"""
    if not config.get("enabled", True):
        return []
    
    topics = []
    limit = config.get("limit", 50)
    
    try:
        ids = (await client.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=15
        )).json()[:limit]
        
        tasks = [
            client.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=15)
            for i in ids
        ]
        
        for resp in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(resp, Exception) or resp.status_code != 200:
                continue
            item = resp.json()
            if item and item.get("type") == "story":
                topics.append({
                    "title": item.get("title", ""),
                    "hot_score": item.get("score", 0),
                    "summary": f"{item.get('descendants', 0)} 评论",
                    "url": item.get("url", f"https://news.ycombinator.com/item?id={item.get('id', '')}"),
                    "source": "hackernews",
                    "source_name": "Hacker News",
                })
        
        logger.info(f"[Hacker News] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[Hacker News] 失败: {e}")
    
    return topics


async def fetch_github(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 GitHub Trending"""
    if not config.get("enabled", True):
        return []
    
    topics = []
    queries = config.get("queries", ["ai"])
    days = config.get("days", 7)
    limit = config.get("limit", 15)
    date_str = (datetime.now(CST) - timedelta(days=days)).strftime("%Y-%m-%d")
    
    try:
        # 并行搜索多个关键词
        async def _search_one(q):
            resp = await client.get(
                f"https://api.github.com/search/repositories?q={q}+created:>{date_str}&sort=stars&order=desc&per_page={limit}",
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "HotNewsAggregator"
                },
                timeout=15
            )
            items = []
            if resp.status_code == 200:
                for item in resp.json().get("items", []):
                    items.append({
                        "title": item.get("full_name", ""),
                        "hot_score": item.get("stargazers_count", 0),
                        "summary": (item.get("description") or "")[:1000],
                        "url": item.get("html_url", ""),
                        "source": "github",
                        "source_name": "GitHub",
                    })
            return items
        
        results = await asyncio.gather(*[_search_one(q) for q in queries], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                topics.extend(r)
        
        logger.info(f"[GitHub] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[GitHub] 失败: {e}")
    
    return topics


async def fetch_producthunt(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 Product Hunt"""
    if not config.get("enabled", True):
        return []
    
    topics = []
    limit = config.get("limit", 40)
    
    try:
        resp = await client.get(
            "https://www.producthunt.com/feed",
            headers={"User-Agent": "HotNewsAggregator"},
            timeout=15,
            follow_redirects=True
        )
        if resp.status_code == 200:
            items = re.findall(r'<item>(.*?)</item>', resp.text, re.DOTALL)
            for item in items[:limit]:
                t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.DOTALL)
                l = re.search(r'<link>(.*?)</link>', item)
                d = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', item, re.DOTALL)
                if t:
                    topics.append({
                        "title": t.group(1).strip(),
                        "hot_score": 0,
                        "summary": re.sub(r'<[^>]+>', '', d.group(1).strip()[:1000]) if d else "",
                        "url": l.group(1).strip() if l else "",
                        "source": "producthunt",
                        "source_name": "Product Hunt",
                    })
        logger.info(f"[Product Hunt] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[Product Hunt] 失败: {e}")
    
    return topics


async def fetch_36kr(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 36氪 RSS"""
    if not config.get("enabled", False):
        return []
    
    topics = []
    limit = config.get("limit", 30)
    rss_url = config.get("rss_url", "https://36kr.com/feed")
    
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "36kr",
                        "source_name": "36氪",
                    })
                    items_processed += 1
        logger.info(f"[36氪] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[36氪] 失败: {e}")
    
    return topics


async def fetch_v2ex(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 V2EX 热门"""
    if not config.get("enabled", False):
        return []
    
    topics = []
    limit = config.get("limit", 30)
    api_url = config.get("api_url", "https://www.v2ex.com/api/topics/hot.json")
    
    try:
        resp = await client.get(api_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            items = resp.json()
            for item in items[:limit]:
                topics.append({
                    "title": item.get("title", ""),
                    "hot_score": item.get("replies", 0),
                    "summary": f"{item.get('replies', 0)} 回复 · {item.get('node', {}).get('title', '')}",
                    "url": item.get("url", ""),
                    "source": "v2ex",
                    "source_name": "V2EX",
                })
        logger.info(f"[V2EX] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[V2EX] 失败: {e}")
    
    return topics


async def fetch_weibo(client: httpx.AsyncClient, config: dict) -> list:
    """抓取微博热搜"""
    if not config.get("enabled", False):
        return []
    
    topics = []
    limit = config.get("limit", 20)
    api_url = config.get("api_url", "")
    
    try:
        if api_url:
            resp = await client.get(api_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        else:
            # 默认第三方API（可替换为自己的服务）
            resp = await client.get(
                "https://tenapi.cn/v2/weibohot",
                headers={"User-Agent": "HotNewsAggregator"},
                timeout=15
            )
        
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            for item in items[:limit]:
                if isinstance(item, dict):
                    topics.append({
                        "title": item.get("name", item.get("title", "")),
                        "hot_score": item.get("hot", item.get("hot_score", 0)),
                        "summary": f"热搜指数: {item.get('hot', item.get('hot_score', 0))}",
                        "url": item.get("url", f"https://s.weibo.com/weibo?q={item.get('name', '')}"),
                        "source": "weibo",
                        "source_name": "微博热搜",
                    })
        logger.info(f"[微博] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[微博] 失败: {e}")
    
    return topics


async def fetch_jiqizhixin(client: httpx.AsyncClient, config: dict) -> list:
    """抓取机器之心 RSS"""
    if not config.get("enabled", False):
        return []
    
    topics = []
    limit = config.get("limit", 20)
    rss_url = config.get("rss_url", "https://www.jiqizhixin.com/rss")
    
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "jiqizhixin",
                        "source_name": "机器之心",
                    })
                    items_processed += 1
        logger.info(f"[机器之心] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[机器之心] 失败: {e}")
    
    return topics


async def fetch_qbitai(client: httpx.AsyncClient, config: dict) -> list:
    """抓取量子位 RSS"""
    if not config.get("enabled", False):
        return []
    
    topics = []
    limit = config.get("limit", 20)
    rss_url = config.get("rss_url", "https://www.qbitai.com/feed")
    
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "qbitai",
                        "source_name": "量子位",
                    })
                    items_processed += 1
        logger.info(f"[量子位] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[量子位] 失败: {e}")
    
    return topics


async def fetch_sspai(client: httpx.AsyncClient, config: dict) -> list:
    """抓取少数派 RSS https://sspai.com"""
    if not config.get("enabled", False):
        return []

    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://sspai.com/feed")

    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "sspai",
                        "source_name": "少数派",
                    })
                    items_processed += 1
        logger.info(f"[少数派] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[少数派] 失败: {e}")

    return topics


async def fetch_ithome(client: httpx.AsyncClient, config: dict) -> list:
    """抓取IT之家 RSS http://www.ithome.com/rss.xml"""
    if not config.get("enabled", False):
        return []

    topics = []
    limit = config.get("limit", 20)
    rss_url = config.get("rss_url", "http://www.ithome.com/rss.xml")

    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "ithome",
                        "source_name": "IT之家",
                    })
                    items_processed += 1
        logger.info(f"[IT之家] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[IT之家] 失败: {e}")

    return topics


async def fetch_huxiu(client: httpx.AsyncClient, config: dict) -> list:
    """抓取虎嗅网 RSS https://www.huxiu.com/rss/0.xml"""
    if not config.get("enabled", False):
        return []

    topics = []
    limit = config.get("limit", 20)
    rss_url = config.get("rss_url", "https://www.huxiu.com/rss/0.xml")

    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "huxiu",
                        "source_name": "虎嗅",
                    })
                    items_processed += 1
        logger.info(f"[虎嗅] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[虎嗅] 失败: {e}")

    return topics


async def fetch_tmtpost(client: httpx.AsyncClient, config: dict) -> list:
    """抓取钛媒体 RSS http://www.tmtpost.com/rss.xml"""
    if not config.get("enabled", False):
        return []

    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "http://www.tmtpost.com/rss.xml")

    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "tmtpost",
                        "source_name": "钛媒体",
                    })
                    items_processed += 1
        logger.info(f"[钛媒体] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[钛媒体] 失败: {e}")

    return topics


async def fetch_geekpark(client: httpx.AsyncClient, config: dict) -> list:
    """抓取极客公园 RSS http://feeds.geekpark.net/"""
    if not config.get("enabled", False):
        return []

    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "http://feeds.geekpark.net/")

    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "geekpark",
                        "source_name": "极客公园",
                    })
                    items_processed += 1
        logger.info(f"[极客公园] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[极客公园] 失败: {e}")

    return topics


async def fetch_ifanr(client: httpx.AsyncClient, config: dict) -> list:
    """抓取爱范儿 RSS https://www.ifanr.com/feed"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://www.ifanr.com/feed")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "ifanr",
                        "source_name": "爱范儿",
                    })
                    items_processed += 1
        logger.info(f"[爱范儿] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[爱范儿] 失败: {e}")
    return topics


async def fetch_pingwest(client: httpx.AsyncClient, config: dict) -> list:
    """抓取品玩 RSS https://www.pingwest.com/feed"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://www.pingwest.com/feed")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "pingwest",
                        "source_name": "品玩",
                    })
                    items_processed += 1
        logger.info(f"[品玩] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[品玩] 失败: {e}")
    return topics


async def fetch_cnbeta(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 cnBeta — 优先 RSS，失败则 HTML 抓取"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 20)
    rss_url = config.get("rss_url", "https://www.cnbeta.com.tw/backend.php?mod=news")
    
    # 方案1：尝试 RSS
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "cnbeta",
                        "source_name": "cnBeta",
                    })
                    items_processed += 1
            if topics:
                logger.info(f"[cnBeta] {len(topics)} 条 (RSS)")
                return topics
    except Exception:
        pass
    
    # 方案2：HTML 抓取（cnBeta RSS 已失效，改为解析首页）
    try:
        html_url = "https://www.cnbeta.com.tw"
        resp = await client.get(html_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        }, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            html = resp.text
            # 提取文章链接和标题：<a href="/articles/...">标题</a>
            pattern = r'<a[^>]+href="(/articles/[^"]+)"[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, html)
            seen_urls = set()
            for path, title_text in matches:
                if len(topics) >= limit:
                    break
                title_text = title_text.strip()
                if not title_text or len(title_text) < 4:
                    continue
                full_url = f"https://www.cnbeta.com.tw{path}"
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                topics.append({
                    "title": title_text,
                    "hot_score": 0,
                    "summary": "",
                    "url": full_url,
                    "source": "cnbeta",
                    "source_name": "cnBeta",
                })
        logger.info(f"[cnBeta] {len(topics)} 条 (HTML)")
    except Exception as e:
        logger.error(f"[cnBeta] 失败: {e}")
    return topics


async def fetch_williamlong(client: httpx.AsyncClient, config: dict) -> list:
    """抓取月光博客 RSS https://feed.williamlong.info/"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://feed.williamlong.info/")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "williamlong",
                        "source_name": "月光博客",
                    })
                    items_processed += 1
        logger.info(f"[月光博客] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[月光博客] 失败: {e}")
    return topics


async def fetch_techcrunch(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 TechCrunch RSS https://techcrunch.com/feed/"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://techcrunch.com/feed/")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "techcrunch",
                        "source_name": "TechCrunch",
                    })
                    items_processed += 1
        logger.info(f"[TechCrunch] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[TechCrunch] 失败: {e}")
    return topics


async def fetch_slashdot(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 Slashdot RSS https://rss.slashdot.org/Slashdot/slashdotMain"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://rss.slashdot.org/Slashdot/slashdotMain")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            # Slashdot 使用 RDF 格式，item 在命名空间内
            items = list(root.iter("item"))
            if not items:
                items = list(root.iter("{http://purl.org/rss/1.0/}item"))
            items_processed = 0
            for item in items:
                if items_processed >= limit:
                    break
                title = item.find("title")
                if title is None:
                    title = item.find("{http://purl.org/rss/1.0/}title")
                link = item.find("link")
                if link is None:
                    link = item.find("{http://purl.org/rss/1.0/}link")
                desc = item.find("description")
                if desc is None:
                    desc = item.find("{http://purl.org/rss/1.0/}description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "slashdot",
                        "source_name": "Slashdot",
                    })
                    items_processed += 1
        logger.info(f"[Slashdot] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[Slashdot] 失败: {e}")
    return topics


async def fetch_nikkei(client: httpx.AsyncClient, config: dict) -> list:
    """抓取日经亚洲 RSS https://asia.nikkei.com/rss/feed"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://asia.nikkei.com/rss/feed")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "nikkei",
                        "source_name": "日经亚洲",
                    })
                    items_processed += 1
        logger.info(f"[日经亚洲] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[日经亚洲] 失败: {e}")
    return topics


async def fetch_zdnet(client: httpx.AsyncClient, config: dict) -> list:
    """抓取 ZDNet RSS https://www.zdnet.com/rss.xml"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://www.zdnet.com/rss.xml")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "zdnet",
                        "source_name": "ZDNet",
                    })
                    items_processed += 1
        logger.info(f"[ZDNet] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[ZDNet] 失败: {e}")
    return topics


async def fetch_pengpai(client: httpx.AsyncClient, config: dict) -> list:
    """抓取澎湃新闻 RSS https://www.thepaper.cn/rss/newsDetail.xml"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 20)
    rss_url = config.get("rss_url", "https://www.thepaper.cn/rss/newsDetail.xml")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "pengpai",
                        "source_name": "澎湃新闻",
                    })
                    items_processed += 1
        logger.info(f"[澎湃新闻] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[澎湃新闻] 失败: {e}")
    return topics


async def fetch_zhihu(client: httpx.AsyncClient, config: dict) -> list:
    """抓取知乎热榜 RSS https://rsshub.app/zhihu/hotlist"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 20)
    rss_url = config.get("rss_url", "https://rsshub.app/zhihu/hotlist")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "zhihu",
                        "source_name": "知乎热榜",
                    })
                    items_processed += 1
        logger.info(f"[知乎热榜] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[知乎热榜] 失败: {e}")
    return topics


async def fetch_infzm(client: httpx.AsyncClient, config: dict) -> list:
    """抓取南方周末 RSS https://rsshub.app/infzm/2"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://rsshub.app/infzm/2")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "infzm",
                        "source_name": "南方周末",
                    })
                    items_processed += 1
        logger.info(f"[南方周末] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[南方周末] 失败: {e}")
    return topics


async def fetch_zaobao(client: httpx.AsyncClient, config: dict) -> list:
    """抓取联合早报 RSS https://rsshub.app/zaobao/realtime/china"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://rsshub.app/zaobao/realtime/china")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "zaobao",
                        "source_name": "联合早报",
                    })
                    items_processed += 1
        logger.info(f"[联合早报] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[联合早报] 失败: {e}")
    return topics


async def fetch_bbc(client: httpx.AsyncClient, config: dict) -> list:
    """抓取BBC中文 RSS https://rsshub.app/bbc/chinese"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://rsshub.app/bbc/chinese")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "bbc",
                        "source_name": "BBC中文",
                    })
                    items_processed += 1
        logger.info(f"[BBC中文] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[BBC中文] 失败: {e}")
    return topics


async def fetch_arstechnica(client: httpx.AsyncClient, config: dict) -> list:
    """抓取Ars Technica RSS https://feeds.arstechnica.com/arstechnica/features"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://feeds.arstechnica.com/arstechnica/features")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "arstechnica",
                        "source_name": "Ars Technica",
                    })
                    items_processed += 1
        logger.info(f"[Ars Technica] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[Ars Technica] 失败: {e}")
    return topics


async def fetch_theverge(client: httpx.AsyncClient, config: dict) -> list:
    """抓取The Verge RSS https://www.theverge.com/rss/index.xml"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://www.theverge.com/rss/index.xml")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "theverge",
                        "source_name": "The Verge",
                    })
                    items_processed += 1
        logger.info(f"[The Verge] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[The Verge] 失败: {e}")
    return topics


async def fetch_npr(client: httpx.AsyncClient, config: dict) -> list:
    """抓取NPR新闻 RSS https://feeds.npr.org/1001/rss.xml"""
    if not config.get("enabled", False):
        return []
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "https://feeds.npr.org/1001/rss.xml")
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator"}, timeout=15)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items_processed = 0
            for item in root.iter("item"):
                if items_processed >= limit:
                    break
                title = item.find("title")
                link = item.find("link")
                desc = item.find("description")
                summary = ""
                if desc is not None and desc.text:
                    summary = re.sub(r"<[^>]+>", "", desc.text)[:1000]
                if title is not None and title.text:
                    topics.append({
                        "title": title.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": link.text.strip() if link is not None and link.text else "",
                        "source": "npr",
                        "source_name": "NPR新闻",
                    })
                    items_processed += 1
        logger.info(f"[NPR新闻] {len(topics)} 条")
    except Exception as e:
        logger.error(f"[NPR新闻] 失败: {e}")
    return topics


async def fetch_aihot(client: httpx.AsyncClient, config: dict) -> list:
    """抓取卡兹克AI热点 https://aihot.virxact.com/

    该站为SSR服务端渲染HTML，通过解析HTML正文提取热点条目。
    每个条目包含：发布时间、来源、精选热度、标题、摘要、标签、推荐理由。
    使用两次抓取策略：先尝试结构化解析，失败时回退到广泛抓取。
    """
    if not config.get("enabled", False):
        return []

    topics = []
    limit = config.get("limit", 40)
    pages = config.get("pages", 2)
    base_url = config.get("base_url", "https://aihot.virxact.com/")

    async def _fetch_single_page(page: int) -> list:
        """抓取单个页面的AI热点"""
        page_topics = []
        url = f"{base_url}?page={page}"
        try:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "HotNewsAggregator/3.4",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=15,
                follow_redirects=True
            )

            if resp.status_code != 200:
                logger.warning(f"[AI热点] 第{page}页请求失败: {resp.status_code}")
                return page_topics

            html = resp.text

            # 策略1: 从 <article> 标签中结构化提取
            article_blocks = re.findall(
                r'<article[^>]*>(.*?)</article>',
                html, re.DOTALL | re.IGNORECASE
            )

            if article_blocks and len(article_blocks) >= 3:
                logger.debug(f"[AI热点] 第{page}页 从{len(article_blocks)}个article块解析")
                for block in article_blocks:
                    topic = _parse_aihot_block(block)
                    if topic:
                        page_topics.append(topic)

            # 策略2: 从全页链接中提取（总是作为补充）
            fallback_topics = _parse_aihot_fallback(html)
            for pt in fallback_topics:
                # 去重：避免与策略1的结果重复
                if not any(t["title"] == pt["title"] for t in page_topics):
                    page_topics.append(pt)

            logger.info(f"[AI热点] 第{page}页 {len(page_topics)} 条")
        except Exception as e:
            logger.error(f"[AI热点] 第{page}页失败: {e}")
        return page_topics

    try:
        # 并行抓取所有页面
        page_results = await asyncio.gather(
            *[_fetch_single_page(page) for page in range(1, pages + 1)]
        )

        # 合并结果并去重
        for page_topics in page_results:
            for t in page_topics:
                if not any(existing["title"] == t["title"] for existing in topics):
                    topics.append(t)

        if len(topics) >= limit:
            topics = topics[:limit]

        logger.info(f"[AI热点] 总计 {len(topics)} 条")
    except Exception as e:
        logger.error(f"[AI热点] 失败: {e}")

    return topics


def _parse_aihot_block(block: str) -> dict:
    """解析单个卡兹克热点条目块"""
    # 提取标题和链接
    title_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]{15,200})</a>', block)
    if not title_match:
        return None

    url = title_match.group(1)
    title = title_match.group(2).strip()
    # 清理HTML实体
    title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')

    # 过滤导航/页脚链接
    skip_patterns = ["登录", "关于", "反馈", "更新日志", "首页", "下一页", "page"]
    if any(p in title for p in skip_patterns):
        return None

    # 提取热度
    hot_match = re.search(r'(?:精选|hot|热度|score)[\s:]*(\d+)', block, re.IGNORECASE)
    hot_score = int(hot_match.group(1)) if hot_match else 0

    # 提取摘要（紧跟在标题后的长文本段落）
    summary = ""
    # 找标题后最近的<p>或文本块
    after_title = block[title_match.end():]
    desc_match = re.search(r'<p[^>]*>([^<]{20,300})</p>', after_title)
    if desc_match:
        summary = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()[:1000]
    if not summary:
        # 回退: 找任意长文本
        text_match = re.search(r'>([^<>{]{30,300})<', after_title)
        if text_match:
            summary = text_match.group(1).strip()[:1000]

    # 提取标签
    tags = []
    tag_matches = re.findall(r'(?:tag|category|label)[\s\S]*?>([^<]{1,10})<', block, re.IGNORECASE)
    if not tag_matches:
        # 回退: 找小号彩色标签
        tag_matches = re.findall(r'<(?:span|a)[^>]*?(?:class="[^"]*(?:tag|badge|label)[^"]*")[^>]*>([^<]+)</(?:span|a)>', block)

    tags = [t.strip() for t in tag_matches if t.strip() and len(t.strip()) <= 10]

    # 提取推荐理由
    recommendation = ""
    rec_match = re.search(r'(?:推荐理由|推荐|荐)[\s:：]*[:：]?\s*(.{20,300})', block)
    if rec_match:
        recommendation = rec_match.group(1).strip()[:1000]
        recommendation = re.sub(r'<[^>]+>', '', recommendation)

    topic = {
        "title": title,
        "hot_score": hot_score,
        "summary": summary,
        "url": url,
        "source": "aihot",
        "source_name": "AI热点",
    }

    # 附加信息存储
    if tags:
        topic["tags"] = tags
    if recommendation:
        topic["recommendation"] = recommendation

    return topic


def _parse_aihot_fallback(html: str) -> list:
    """回退解析: 从整页HTML中提取所有有效标题链接"""
    topics = []
    seen = set()

    # 找所有外部链接+标题的组合
    # 模式: <a href="https://..." ...>较长的标题文本</a>
    links = re.findall(
        r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]{15,200})</a>',
        html, re.DOTALL
    )

    skip_patterns = ["登录", "关于", "反馈", "更新日志", "首页", "下一页", "page",
                     "github.com", "hackernews", "rss"]

    for url, title in links:
        title = title.strip()
        title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')

        # 过滤导航/无关链接
        if any(p in title for p in skip_patterns):
            continue
        # 去重
        key = _normalize_title(title)
        if key in seen:
            continue
        seen.add(key)

        topics.append({
            "title": title,
            "hot_score": 0,
            "summary": "",
            "url": url,
            "source": "aihot",
            "source_name": "AI热点",
        })

    return topics


def _normalize_title(title: str) -> str:
    """归一化标题用于去重比对"""
    if not title:
        return ""
    # 转小写、去标点、去多余空格
    title = title.lower().strip()
    title = re.sub(r'[^\w\s]', '', title)
    title = re.sub(r'\s+', ' ', title)
    return title


def dedup_topics(topics: list, config: dict) -> list:
    """多源智能去重
    - 完全匹配：标题归一化后相同
    - 模糊匹配：编辑距离相似度 >= fuzzy_threshold
    - 合并显示：同一条新闻从多源抓到时合并来源标签
    """
    if not config.get("enabled", True):
        # 基础去重（仅完全匹配）
        seen, result = set(), []
        for t in topics:
            key = _normalize_title(t["title"])
            if key not in seen:
                seen.add(key)
                result.append(t)
        return result
    
    fuzzy_threshold = config.get("fuzzy_threshold", 0.8)
    merge_sources = config.get("merge_sources", True)
    
    result = []
    seen_keys = []  # [(key, index_in_result)]
    
    for t in topics:
        key = _normalize_title(t["title"])
        if not key:
            result.append(t)
            continue
        
        is_dup = False
        for seen_key, idx in seen_keys:
            # 完全匹配
            if key == seen_key:
                is_dup = True
                if merge_sources:
                    existing_sources = result[idx].get("source_name", "")
                    new_source = t.get("source_name", "")
                    if new_source not in existing_sources:
                        result[idx]["source_name"] = f"{existing_sources} + {new_source}"
                    # 取更高的热度分数
                    if t.get("hot_score", 0) > result[idx].get("hot_score", 0):
                        result[idx]["hot_score"] = t["hot_score"]
                break
            # 模糊匹配
            if len(key) > 5 and len(seen_key) > 5:
                similarity = SequenceMatcher(None, key, seen_key).ratio()
                if similarity >= fuzzy_threshold:
                    is_dup = True
                    if merge_sources:
                        existing_sources = result[idx].get("source_name", "")
                        new_source = t.get("source_name", "")
                        if new_source not in existing_sources:
                            result[idx]["source_name"] = f"{existing_sources} + {new_source}"
                    logger.debug(f"去重(模糊): '{t['title'][:40]}' ≈ '{result[idx]['title'][:40]}' (相似度: {similarity:.2f})")
                    break
        
        if not is_dup:
            seen_keys.append((key, len(result)))
            result.append(t)
    
    removed = len(topics) - len(result)
    if removed > 0:
        logger.info(f"去重: 移除 {removed} 条重复内容，保留 {len(result)} 条")
    
    return result


async def fetch_generic_rss(client: httpx.AsyncClient, config: dict) -> list:
    """通用 RSS 抓取（支持 RSSHub 降级）"""
    if not config.get("enabled", False):
        return []
    
    topics = []
    limit = config.get("limit", 15)
    rss_url = config.get("rss_url", "")
    source_key = config.get("_source_key", "generic")
    source_name = config.get("source_name", source_key)
    
    if not rss_url:
        return []
    
    # 尝试主 RSS URL
    try:
        resp = await client.get(rss_url, headers={"User-Agent": "HotNewsAggregator/3.7"}, timeout=15)
        if resp.status_code == 200 and len(resp.text) > 100:
            topics = _parse_rss_xml(resp.text, source_key, source_name, limit)
            if topics:
                logger.info(f"[{source_name}] {len(topics)} 条 (RSS)")
                return topics
    except Exception as e:
        logger.warning(f"[{source_name}] RSS 失败: {e}")
    
    # RSSHub 源降级：尝试直接 HTML 抓取
    if 'rsshub.app' in rss_url:
        logger.info(f"[{source_name}] RSSHub 超时，尝试直接抓取...")
        try:
            # 从 RSSHub URL 提取目标站点
            html_url = _rsshub_to_direct_url(rss_url)
            if html_url:
                resp = await client.get(html_url, headers={"User-Agent": "HotNewsAggregator/3.7"}, timeout=15)
                if resp.status_code == 200:
                    topics = _parse_html_fallback(resp.text, source_key, source_name, limit)
                    if topics:
                        logger.info(f"[{source_name}] {len(topics)} 条 (HTML降级)")
                        return topics
        except Exception as e2:
            logger.warning(f"[{source_name}] HTML降级也失败: {e2}")
    
    # 记录失败源到日志（供决策系统使用）
    logger.warning(f"[{source_name}] 所有抓取方式均失败，跳过")
    return []


def _parse_rss_xml(xml_text: str, source_key: str, source_name: str, limit: int) -> list:
    """解析 RSS/Atom XML"""
    topics = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"content": "http://purl.org/rss/1.0/modules/content/",
              "atom": "http://www.w3.org/2005/Atom",
              "dc": "http://purl.org/dc/elements/1.1/"}
        count = 0
        for item in root.iter("item"):
            if count >= limit:
                break
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")
            summary = ""
            if desc_el is not None and desc_el.text:
                summary = re.sub(r"<[^>]+>", "", desc_el.text)[:1000]
            if title_el is not None and title_el.text:
                topics.append({
                    "title": title_el.text.strip(),
                    "hot_score": 0,
                    "summary": summary,
                    "url": link_el.text.strip() if link_el is not None and link_el.text else "",
                    "source": source_key,
                    "source_name": source_name,
                })
                count += 1
        # Atom format
        if not topics:
            for entry in root.iter("entry"):
                if count >= limit:
                    break
                title_el = entry.find("title")
                link_el = entry.find("link")
                summary_el = entry.find("summary")
                summary = ""
                if summary_el is not None and summary_el.text:
                    summary = re.sub(r"<[^>]+>", "", summary_el.text)[:1000]
                href = ""
                if link_el is not None:
                    href = link_el.get("href", "")
                if title_el is not None and title_el.text:
                    topics.append({
                        "title": title_el.text.strip(),
                        "hot_score": 0,
                        "summary": summary,
                        "url": href,
                        "source": source_key,
                        "source_name": source_name,
                    })
                    count += 1
    except Exception as e:
        logger.warning(f"RSS XML 解析失败: {e}")
    return topics


def _rsshub_to_direct_url(rsshub_url: str) -> str:
    """从 RSSHub URL 推导直接站点 URL（降级用）"""
    mapping = {
        "caixin": "https://www.caixin.com/",
        "guancha": "https://www.guancha.cn/",
        "thepaper": "https://www.thepaper.cn/",
        "ft/chinese": "https://www.ftchinese.com/",
    }
    for key, url in mapping.items():
        if key in rsshub_url:
            return url
    return ""


def _parse_html_fallback(html_text: str, source_key: str, source_name: str, limit: int) -> list:
    """HTML 降级解析（提取标题和链接）"""
    topics = []
    try:
        # 简单提取 <a> 标签中的标题
        pattern = re.compile(r'<a[^>]*href=["\x27]([^"\x27]+)["\x27][^>]*>([^<]{10,100})</a>', re.DOTALL)
        matches = pattern.findall(html_text)[:limit]
        seen_titles = set()
        for url, title in matches:
            title = title.strip()
            if title and title not in seen_titles and not url.startswith("javascript"):
                seen_titles.add(title)
                topics.append({
                    "title": title,
                    "hot_score": 0,
                    "summary": "",
                    "url": url,
                    "source": source_key,
                    "source_name": source_name,
                })
    except Exception as e:
        logger.warning(f"HTML降级解析失败: {e}")
    return topics


async def fetch_all(config: dict) -> list:
    """抓取所有数据源"""
    all_topics = []
    sources = config.get("sources", {})
    
    # 统计启用的数据源
    enabled_sources = []
    source_map = {
        "hackernews": ("Hacker News", fetch_hackernews),
        "github": ("GitHub", fetch_github),
        "producthunt": ("Product Hunt", fetch_producthunt),
        "_36kr": ("36氪", fetch_36kr),
        "v2ex": ("V2EX", fetch_v2ex),
        "weibo": ("微博", fetch_weibo),
        "aihot": ("AI热点", fetch_aihot),
        "jiqizhixin": ("机器之心", fetch_jiqizhixin),
        "qbitai": ("量子位", fetch_qbitai),
        "sspai": ("少数派", fetch_sspai),
        "ithome": ("IT之家", fetch_ithome),
        "huxiu": ("虎嗅", fetch_huxiu),
        "tmtpost": ("钛媒体", fetch_tmtpost),
        "geekpark": ("极客公园", fetch_geekpark),
        "ifanr": ("爱范儿", fetch_ifanr),
        "pingwest": ("品玩", fetch_pingwest),
        "cnbeta": ("cnBeta", fetch_cnbeta),
        "williamlong": ("月光博客", fetch_williamlong),
        "techcrunch": ("TechCrunch", fetch_techcrunch),
        "slashdot": ("Slashdot", fetch_slashdot),
        "nikkei": ("日经亚洲", fetch_nikkei),
        "zdnet": ("ZDNet", fetch_zdnet),
        "pengpai": ("澎湃新闻", fetch_pengpai),
        "zhihu": ("知乎热榜", fetch_zhihu),
        "infzm": ("南方周末", fetch_infzm),
        "zaobao": ("联合早报", fetch_zaobao),
        "bbc": ("BBC中文", fetch_bbc),
        "arstechnica": ("Ars Technica", fetch_arstechnica),
        "theverge": ("The Verge", fetch_theverge),
        "npr": ("NPR新闻", fetch_npr),
    }
    
    # 自动发现 config 中有 rss_url 但未注册的源
    for key, src_cfg in sources.items():
        if key in source_map:
            continue
        if not src_cfg.get("enabled", False):
            continue
        rss_url = src_cfg.get("rss_url", "")
        if rss_url:
            src_cfg["_source_key"] = key
            src_cfg["source_name"] = key.replace("_", " ").title()
            source_map[key] = (src_cfg["source_name"], fetch_generic_rss)
            logger.info(f"自动注册 RSS 源: {key} -> {rss_url}")
    
    for key, (name, fetcher) in source_map.items():
        if sources.get(key, {}).get("enabled", key in ("hackernews", "github", "producthunt")):
            enabled_sources.append((key, name, fetcher))
    
    total_sources = len(enabled_sources)
    pbar = ProgressBar(total_sources, "📡 数据抓取")
    start_time = time.time()
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        # 并行抓取所有数据源（串行→并行，最大提速）
        async def _fetch_one(idx_key_fetcher):
            idx, key, name, fetcher = idx_key_fetcher
            src_cfg = sources.get(key, {})
            try:
                result = await fetcher(client, src_cfg)
                return result
            except Exception as e:
                logger.error(f"[{name}] 抓取失败: {e}")
                return []
        
        tasks = [_fetch_one((i, k, n, f)) for i, (k, n, f) in enumerate(enabled_sources)]
        results = await asyncio.gather(*tasks)
        for result in results:
            all_topics.extend(result)
            pbar.update(1)
    
    elapsed = time.time() - start_time
    
    # 多源智能去重
    dedup_cfg = config.get("dedup", {})
    all_topics = dedup_topics(all_topics, dedup_cfg)
    
    return all_topics


# ============ 翻译/摘要系统 ============
_translator = None
_translator_error = None

def _get_translator():
    global _translator, _translator_error
    if _translator is None and _translator_error is None:
        try:
            from deep_translator import GoogleTranslator
            _translator = GoogleTranslator(source="auto", target="zh-CN")
        except Exception as e:
            _translator_error = str(e)
            logger.warning(f"翻译器初始化失败（将跳过翻译）: {e}")
    return _translator


def is_english(text: str) -> bool:
    """判断文本是否主要是英文"""
    if not text:
        return False
    english_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    total_chars = sum(1 for c in text if c.isalpha())
    return total_chars > 0 and english_chars / total_chars > 0.7


def translate_text(text: str) -> str:
    """使用 deep_translator (Google Translate) 翻译，带缓存，失败则返回原文"""
    if not text or not is_english(text):
        return text
    
    # 检查缓存
    cache_key = _get_cache_key(text)
    if cache_key in _translate_cache:
        return _translate_cache[cache_key]
    
    translator = _get_translator()
    if translator is None:
        return text
    try:
        result = translator.translate(text[:500])
        if result and is_english(result) is False:
            # 保存到缓存
            _translate_cache[cache_key] = result
            return result
    except Exception as e:
        logger.debug(f"翻译失败（将在下次重试）: {e}")
    return text


# ============ 翻译缓存 ============
_translate_cache = {}  # 内存缓存
_translate_cache_file = Path("data/translate_cache.json")

def _load_translate_cache():
    """加载翻译缓存"""
    global _translate_cache
    if _translate_cache_file.exists():
        try:
            with open(_translate_cache_file, 'r', encoding='utf-8') as f:
                _translate_cache = json.load(f)
            logger.info(f"  翻译缓存: 已加载 {len(_translate_cache)} 条")
        except Exception as e:
            logger.warning(f"  翻译缓存加载失败: {e}")
            _translate_cache = {}
    else:
        _translate_cache = {}

def _save_translate_cache():
    """保存翻译缓存"""
    try:
        _translate_cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(_translate_cache_file, 'w', encoding='utf-8') as f:
            json.dump(_translate_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"  翻译缓存保存失败: {e}")

def _get_cache_key(text: str) -> str:
    """生成缓存key（取前100字符的hash）"""
    return hashlib.md5(text[:100].encode()).hexdigest()

# ============ AI 评论生成（视角评论 + 行动引导）============
_ai_insights_cache = {}  # 缓存已生成的评论

def generate_perspective_comment(topic: dict, config: dict, mode: str = "general") -> str:
    """生成视角评论（从缓存读取，批量生成时已填充，支持大众版/专业版）"""
    insights_cfg = config.get("ai_insights", {})
    if not insights_cfg.get("enabled", False):
        return ""
    cache_key = hashlib.md5(topic["title"].encode()).hexdigest()
    cached = _ai_insights_cache.get(cache_key, {})
    return cached.get("perspective", "")


def generate_action_guidance(topic: dict, config: dict, mode: str = "general") -> str:
    """生成行动引导（支持大众版/专业版）"""
    insights_cfg = config.get("ai_insights", {})
    if not insights_cfg.get("enabled", False):
        return ""
    cache_key = hashlib.md5(topic["title"].encode()).hexdigest()
    cached = _ai_insights_cache.get(cache_key, {})
    return cached.get("action", "")


def _batch_generate_insights(topics: list, config: dict, mode: str = "general") -> int:
    """分批生成AI评论：每批最多15条，多次API调用（支持大众版/专业版）"""
    insights_cfg = config.get("ai_insights", {})
    # 根据模式选择提示词风格
    prompts_cfg = insights_cfg.get("prompts", {})
    mode_prompts = prompts_cfg.get(mode, prompts_cfg.get("general", {}))
    api_cfg = insights_cfg.get("api", {})
    
    api_key = api_cfg.get("api_key", "")
    if not api_key:
        return 0
    if api_key.startswith("${") and api_key.endswith("}"):
        env_var = api_key[2:-1]
        api_key = os.environ.get(env_var, "")
        if not api_key:
            logger.warning(f"AI评论API: 环境变量 {env_var} 未设置")
            return 0
    
    provider = api_cfg.get("provider", "openai")
    endpoint = api_cfg.get("endpoint", "")
    model = api_cfg.get("model", "gpt-3.5-turbo")
    max_tokens = api_cfg.get("max_tokens", 200)
    
    # 确定 endpoint
    if provider == "siliconflow":
        if not endpoint:
            endpoint = "https://api.siliconflow.cn/v1/chat/completions"
    elif provider == "groq":
        if not endpoint:
            endpoint = "https://api.groq.com/openai/v1/chat/completions"
    elif provider == "deepseek":
        if not endpoint:
            endpoint = "https://api.deepseek.com/v1/chat/completions"
    elif provider == "openai":
        if not endpoint:
            endpoint = "https://api.openai.com/v1/chat/completions"
    else:
        if not endpoint:
            endpoint = "https://api.openai.com/v1/chat/completions"
    
    import urllib.request
    import json as _json
    
    # 分批处理：每批最多15条
    BATCH_SIZE = 15
    total_parsed = 0
    batches = [topics[i:i+BATCH_SIZE] for i in range(0, len(topics), BATCH_SIZE)]
    logger.info(f"AI评论: 共 {len(topics)} 条，分 {len(batches)} 批请求（每批≤{BATCH_SIZE}条）...")
    
    for batch_idx, batch_topics in enumerate(batches):
        # 构建批量请求
        items_text = []
        for i, t in enumerate(batch_topics):
            title = t.get("title", "")
            summary = t.get("summary_original", t.get("summary", ""))[:150]
            items_text.append(f"{i+1}. 标题: {title}\n摘要: {summary}")
        
        # 根据模式构建不同的批量提示
        if mode == "pro":
            sys_prompt = (
                "你是AI行业资深分析师。请对以下每条内容，分别用中文生成：\n"
                "1. \U0001f4dd技术摘要（1-2句，概括核心技术要点和行业影响，可含技术术语）\n"
                "2. \U0001f4a1行业视角（1-2句，从内容创作或技术实现角度点评行业价值和机会）\n"
                "3. \U0001f3af专业建议（1句，给从业者/创作者具体可行的行动建议：选题方向/技术选型/商业机会）\n"
                "\n"
                "严格按格式返回，每条用===N===分隔（N为序号）：\n"
                "===1===\n"
                "\U0001f4dd技术摘要：...\n"
                "\U0001f4a1行业视角：...\n"
                "\U0001f3af专业建议：...\n"
                "===2===\n"
                "\U0001f4dd技术摘要：...\n"
                "\U0001f4a1行业视角：...\n"
                "\U0001f3af专业建议：...\n"
            )
        else:
            sys_prompt = (
                "你是AI时代观察者，擅长用大白话解释AI新闻。请对以下每条内容，分别用中文生成：\n"
                "1. \U0001f4dd中文小结（1-2句，不用专业术语，像给完全不懂AI的朋友解释）\n"
                "2. \U0001f4a1视角解读（1-2句，这条新闻对普通人意味着什么？怎么影响我们的生活和工作？）\n"
                "3. \U0001f3af行动引导（1句，给普通人今天就能做的小行动：学什么工具/避开什么坑/调整什么心态）\n"
                "\n"
                "严格按格式返回，每条用===N===分隔（N为序号）：\n"
                "===1===\n"
                "\U0001f4dd中文小结：...\n"
                "\U0001f4a1视角解读：...\n"
                "\U0001f3af行动引导：...\n"
                "===2===\n"
                "\U0001f4dd中文小结：...\n"
                "\U0001f4a1视角解读：...\n"
                "\U0001f3af行动引导：...\n"
            )
        batch_prompt = sys_prompt + "\n以下是需要评论的内容：\n" + chr(10).join(items_text)

        try:
            total_tokens = min(max_tokens * len(batch_topics) * 2, 4000)
            
            req_data = _json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": batch_prompt}],
                "max_tokens": total_tokens,
                "temperature": 0.7,
            }).encode("utf-8")
            
            req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            logger.info(f"AI评论: 第 {batch_idx+1}/{len(batches)} 批，{len(batch_topics)} 条...")
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = _json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"].strip()
            
            # 解析批量结果
            parsed = 0
            blocks = re.split(r'===\d+===', content)
            for i, t in enumerate(batch_topics):
                block = blocks[i+1] if i+1 < len(blocks) else ""
                zh_match = re.search(r'📝中文小结[：:]\s*(.+?)(?=💡|$)', block, re.DOTALL)
                persp_match = re.search(r'💡视角解读[：:]\s*(.+?)(?=🎯|$)', block, re.DOTALL)
                action_match = re.search(r'🎯行动引导[：:]\s*(.+?)$', block, re.DOTALL)
                
                cache_key = hashlib.md5(t["title"].encode()).hexdigest()
                if cache_key not in _ai_insights_cache:
                    _ai_insights_cache[cache_key] = {}
                
                if zh_match:
                    _ai_insights_cache[cache_key]["zh_summary"] = zh_match.group(1).strip()
                    parsed += 1
                if persp_match:
                    _ai_insights_cache[cache_key]["perspective"] = persp_match.group(1).strip()
                    parsed += 1
                if action_match:
                    _ai_insights_cache[cache_key]["action"] = action_match.group(1).strip()
                    parsed += 1
            
            logger.info(f"AI评论: 第 {batch_idx+1} 批完成，解析 {parsed} 条")
            total_parsed += parsed
        except Exception as e:
            logger.warning(f"AI评论: 第 {batch_idx+1} 批失败: {e}")
    
    logger.info(f"AI评论: 全部完成，成功解析 {total_parsed} 条")
    return total_parsed



def _call_llm_api(topic: dict, config: dict, insight_type: str) -> str:
    """调用LLM API生成评论"""
    insights_cfg = config.get("ai_insights", {})
    api_cfg = insights_cfg.get("api", {})
    
    api_key = api_cfg.get("api_key", "")
    if not api_key:
        return ""
    
    # 环境变量替换
    if api_key.startswith("${") and api_key.endswith("}"):
        env_var = api_key[2:-1]
        api_key = os.environ.get(env_var, "")
        if not api_key:
            logger.warning(f"AI评论API: 环境变量 {env_var} 未设置")
            return ""
    
    prompt_template = insights_cfg.get("prompts", {}).get(
        insight_type,
        "请对以下内容进行简短评论：{title}"
    )
    prompt = prompt_template.format(
        title=topic.get("title", ""),
        summary=topic.get("summary_original", topic.get("summary", ""))[:1000]
    )
    
    provider = api_cfg.get("provider", "openai")
    endpoint = api_cfg.get("endpoint", "")
    model = api_cfg.get("model", "gpt-3.5-turbo")
    max_tokens = api_cfg.get("max_tokens", 200)
    
    # 确定 API endpoint
    if provider == "siliconflow":
        if not endpoint:
            endpoint = "https://api.siliconflow.cn/v1/chat/completions"
    elif provider == "groq":
        if not endpoint:
            endpoint = "https://api.groq.com/openai/v1/chat/completions"
    elif provider == "deepseek":
        if not endpoint:
            endpoint = "https://api.deepseek.com/v1/chat/completions"
    elif provider == "openai":
        if not endpoint:
            endpoint = "https://api.openai.com/v1/chat/completions"
    else:
        if not endpoint:
            endpoint = "https://api.openai.com/v1/chat/completions"
    
    try:
        import urllib.request
        import json as _json
        
        req_data = _json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }).encode("utf-8")
        
        req = urllib.request.Request(
            endpoint,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"].strip()
            return content
    except Exception as e:
        logger.warning(f"AI评论API调用失败 ({insight_type}): {e}")
        return ""


def _resolve_env(value: str) -> str:
    """解析环境变量占位 ${VAR_NAME}"""
    if value and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var, "")
    return value


def send_email_digest(categories: dict, config: dict):
    """发送邮件摘要"""
    email_cfg = config.get("email", {})
    if not email_cfg.get("enabled", False):
        logger.info("邮件订阅: 未启用")
        return
    
    send_mode = email_cfg.get("send_mode", "both")
    if send_mode == "manual":
        logger.info("邮件订阅: 手动模式，跳过自动发送")
        return
    
    min_level = email_cfg.get("min_level", "精选")
    recipients = email_cfg.get("recipients", [])
    sender = email_cfg.get("sender", "")
    password = _resolve_env(email_cfg.get("password", ""))
    
    if not recipients or not sender or not password:
        logger.warning("邮件订阅: 缺少必要配置（sender/recipients/password）")
        return
    
    # 筛选 S/A 级内容
    levels = config.get("scoring", {}).get("levels", [])
    level_order = {lvl["name"]: i for i, lvl in enumerate(levels)}
    min_level_idx = level_order.get(min_level, 0)
    
    digest_items = []
    for lvl in levels:
        if level_order.get(lvl["name"], 99) <= min_level_idx:
            for t in categories.get(lvl["name"], []):
                digest_items.append(t)
    
    if not digest_items:
        logger.info("邮件订阅: 无符合条件的内容")
        return
    
    # 构建邮件
    site = config.get("site", {})
    title = f"[{site.get('title', '热点聚合')}] 每日精选摘要 - {NOW.strftime('%Y-%m-%d')}"
    
    html_body = f"""
    <html>
    <body style="font-family: -apple-system, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; background: #f5f5f7;">
      <div style="background: #fff; border-radius: 12px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);">
        <h1 style="color: #1a1a1a; font-size: 1.5rem; margin-bottom: 4px;">{site.get('title', '热点聚合')}</h1>
        <p style="color: #666; font-size: 0.9rem; margin-bottom: 24px;">{NOW.strftime('%Y年%m月%d日')} 热点摘要 · 共 {len(digest_items)} 条</p>
        <hr style="border: none; border-top: 1px solid #eee; margin-bottom: 24px;">
    """
    
    for t in digest_items:
        level_info = t.get("level", {})
        color = level_info.get("color", "#c8a45c")
        title_text = t.get("title", "")
        url = t.get("url", "")
        zh_summary = t.get("zh_summary", "")
        perspective = t.get("perspective_comment", "")
        action = t.get("action_guidance", "")
        source = t.get("source_name", "")
        score = t.get("score", 0)
        
        html_body += f"""
        <div style="margin-bottom: 24px; padding: 16px; border-left: 3px solid {color}; background: #fafafa; border-radius: 0 8px 8px 0;">
          <div style="margin-bottom: 8px;">
            <span style="background: {color}; color: #fff; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 700;">{score}分 · {level_info.get('name', '')}</span>
            <span style="color: #999; font-size: 0.75rem; margin-left: 8px;">{source}</span>
          </div>
          <h2 style="margin: 8px 0; font-size: 1.1rem;"><a href="{url}" style="color: #1a1a1a; text-decoration: none;">{title_text}</a></h2>
          {f'<p style="color: #c8a45c; font-size: 0.9rem; margin: 8px 0;">📝 {zh_summary}</p>' if zh_summary else ''}
          {f'<p style="color: #666; font-size: 0.85rem; margin: 8px 0;">💡 {perspective}</p>' if perspective else ''}
          {f'<p style="color: #c88c78; font-size: 0.85rem; margin: 8px 0;">🎯 {action}</p>' if action else ''}
        </div>
        """
    
    html_body += f"""
        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
        <p style="color: #999; font-size: 0.8rem; text-align: center;">
          此邮件由热点聚合自动生成 · <a href="{site.get('url', '#')}" style="color: #999;">访问网站</a>
        </p>
      </div>
    </body>
    </html>
    """
    
    try:
        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = f"{email_cfg.get('sender_name', '热点聚合')} <{sender}>"
        msg["To"] = ", ".join(recipients)
        msg.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))
        
        smtp_host = email_cfg.get("smtp_host", "smtp.qq.com")
        smtp_port = email_cfg.get("smtp_port", 587)
        
        if email_cfg.get("smtp_use_tls", True):
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())
        server.quit()
        
        logger.info(f"邮件订阅: 已发送 {len(digest_items)} 条摘要到 {len(recipients)} 个收件人")
    except Exception as e:
        logger.error(f"邮件订阅: 发送失败 - {e}")


# ============ 主题处理（翻译+AI评论）============
async def process_topics(topics: list, config: dict, no_translate: bool = False,
                        quick: bool = False) -> list:
    """处理所有内容：翻译 + AI评论（视角评论 + 行动引导）"""
    translate_enabled = config.get("translate", {}).get("enabled", True)
    ai_enabled = config.get("ai_insights", {}).get("enabled", False)
    
    # 加载翻译缓存
    _load_translate_cache()
    
    # 快速模式：跳过翻译和AI评论API调用，但从 ai_content.json 加载缓存
    if quick:
        # 从 ai_content.json 加载缓存的AI内容
        ai_cache = {}
        try:
            ai_json_path = os.path.join(os.path.dirname(__file__), "ai_content.json")
            if not os.path.exists(ai_json_path):
                ai_json_path = os.path.join(os.getcwd(), "ai_content.json")
            if os.path.exists(ai_json_path):
                with open(ai_json_path, "r", encoding="utf-8") as f:
                    ai_cache = json.load(f)
                logger.info(f"快速模式: 从 ai_content.json 加载了 {len(ai_cache)} 条AI缓存")
        except Exception as e:
            logger.warning(f"快速模式: 加载 ai_content.json 失败 - {e}")
        
        for t in topics:
            t["title_original"] = t.get("title", "")
            t["title"] = t["title_original"]
            t["title_translated"] = False
            t["summary_original"] = t.get("summary", "")
            # 从缓存读取AI内容
            cached = ai_cache.get(t.get("title", ""), {})
            t["zh_summary"] = cached.get("zh", "") or cached.get("summary", "") or ""
            t["perspective_comment"] = cached.get("persp", "") or cached.get("perspective", "") or ""
            t["action_guidance"] = cached.get("action", "") or cached.get("guidance", "") or ""
        logger.info(f"快速模式: {sum(1 for t in topics if t.get('perspective_comment'))} 条有视角评论, {sum(1 for t in topics if t.get('action_guidance'))} 条有行动引导")
        return topics
    
    total_items = len(topics)
    
    # 翻译关闭或跳过翻译
    if not translate_enabled or no_translate:
        logger.info(f"  翻译: 跳过（共 {total_items} 条）")
        for t in topics:
            t["title_original"] = t.get("title", "")
            t["title"] = t["title_original"]
            t["title_translated"] = False
            t["summary_original"] = t.get("summary", "")
            t["zh_summary"] = ""
    else:
        pbar = ProgressBar(total_items, "🌐 翻译处理")
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=5) as pool:
            # 并发翻译标题
            title_tasks = [
                loop.run_in_executor(pool, translate_text, t.get("title", ""))
                for t in topics
            ]
            titles = await asyncio.gather(*title_tasks)

            # 并发翻译摘要
            summary_tasks = []
            for t in topics:
                s = t.get("summary", "")
                if s and is_english(s):
                    summary_tasks.append(
                        loop.run_in_executor(pool, translate_text, s[:1000])
                    )
                else:
                    summary_tasks.append(asyncio.sleep(0, result=""))
            summaries = await asyncio.gather(*summary_tasks)

            for i, t in enumerate(topics):
                title = t.get("title", "")
                summary = t.get("summary", "")

                t["title_original"] = title
                t["title"] = titles[i]
                t["title_translated"] = (titles[i] != title)

                t["summary_original"] = summary
                t["zh_summary"] = summaries[i] if isinstance(summaries[i], str) else ""
                pbar.update(1)
    
    # AI评论生成（批量模式：一次API调用生成所有条目）
    # CI/CD模式下(--no_translate)跳过AI批量生成，由inject_ai_from_json.py单独注入
    if ai_enabled and not no_translate:
        insights_cfg = config.get("ai_insights", {})
        min_level = insights_cfg.get("min_level", "参考")
        max_items = insights_cfg.get("max_items", 100)
        
        levels = config.get("scoring", {}).get("levels", [])
        min_level_idx = next((i for i, l in enumerate(levels) if l["name"] == min_level), 0)
        
        # 先筛选需要生成AI评论的条目
        ai_topics = []
        for t in topics:
            _s = score_topic(t, config); score_val = _s[0] if isinstance(_s, tuple) else _s; t["audience"] = _s[1] if isinstance(_s, tuple) else "both"; t["value_tags"] = _s[2] if isinstance(_s, tuple) and len(_s) > 2 else ["综合"]
            t["score"] = score_val
            level_idx = len(levels)
            for i, lvl in enumerate(levels):
                if score_val >= lvl["threshold"]:
                    level_idx = i
                    break
            if level_idx <= min_level_idx and len(ai_topics) < max_items:
                ai_topics.append(t)
            else:
                t["perspective_comment"] = ""
                t["action_guidance"] = ""
        
        # 批量生成（一次API调用）
        if ai_topics:
            logger.info(f"AI评论: 批量生成 {len(ai_topics)} 条内容...")
            _batch_generate_insights(ai_topics, config, mode="general")
        
        # 从缓存读取结果（包括中文小结、视角评论、行动引导）
        for t in ai_topics:
            t["perspective_comment"] = generate_perspective_comment(t, config)
            t["action_guidance"] = generate_action_guidance(t, config)
            # 如果没有翻译生成的中文小结，用AI批量生成的
            cache_key = hashlib.md5(t["title"].encode()).hexdigest()
            cached = _ai_insights_cache.get(cache_key, {})
            if not t.get("zh_summary") and cached.get("zh_summary"):
                t["zh_summary"] = cached["zh_summary"]
        
        logger.info(f"AI评论: 已为 {len(ai_topics)} 条内容生成中文小结、视角评论和行动引导")

    # 保存翻译缓存
    _save_translate_cache()
    
    return topics


# ============ 评分系统 ============
def _flatten_keywords(kw_list):
    """扁平化嵌套关键词列表，兼容 YAML flow sequence 格式"""
    if not kw_list:
        return []
    flat = []
    for item in kw_list:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    return flat


def score_topic(t: dict, config: dict) -> float:
    """计算内容分数"""
    text = f"{t.get('title','')} {t.get('summary','')}".lower()
    keywords = config.get("scoring", {}).get("keywords", {})
    
    # 核心关键词
    core_hits = sum(1 for kw in _flatten_keywords(keywords.get("core", [])) if kw.lower() in text)
    # 扩展关键词
    ext_hits = sum(1 for kw in _flatten_keywords(keywords.get("extended", [])) if kw.lower() in text)
    # 排除关键词
    exclude_hits = sum(1 for kw in _flatten_keywords(keywords.get("exclude", [])) if kw.lower() in text)
    
    relevance = min(core_hits * 15, 50) + min(ext_hits * 8, 25) - exclude_hits * 15
    
    # 标题关键词加分
    title_lower = t.get("title", "").lower()
    for kw in _flatten_keywords(keywords.get("core", [])):
        if kw.lower() in title_lower:
            relevance += 8
    
    # 热度分数
    try:
        heat = float(str(t.get("hot_score", 0)).replace(",", "").replace("万", ""))
        if "万" in str(t.get("hot_score", "")):
            heat *= 10000
    except:
        heat = 0
    heat_score = 25 if heat <= 0 else min(35, 20 + min(heat, 5000) * 0.003)
    
    # 来源加分
    source_bonus = {"hackernews": 12, "github": 10, "producthunt": 8, "aihot": 10}.get(t.get("source"), 5)
    
    # 受众标签（大众版/专业版）- 基于关键词，但保证平衡分布
    # 注意：这里只做初步分类，最终平衡在 generate_html 中处理
    audience_cfg = config.get("scoring", {}).get("audience_keywords", {})
    gen_kws = audience_cfg.get("general", [])
    pro_kws = audience_cfg.get("pro", [])
    gen_hits = sum(1 for kw in gen_kws if kw.lower() in text)
    pro_hits = sum(1 for kw in pro_kws if kw.lower() in text)
    
    # 简单的二分类，无both（both后续会重新分配）
    if gen_hits >= pro_hits:
        audience = "general"
    else:
        audience = "pro"
    
    # ===== AI 价值标签识别 =====
    value_tags = []
    summary_text = t.get('summary', '') or ''
    text_for_value = title_lower + ' ' + summary_text.lower()
    
    # AI变现赛道
    monetization_kws = ['变现', '盈利', '收入', '营收', '赚钱', '商业化', '商业模式', 'b2b', 'c端', '订阅', '付费', '收费', '定价', '销售', '市场', '客户', '用户增长', '获客', '转化率', 'arpu', 'ltv', 'gmv', '融资', 'ipo', '上市', '估值', '投资', '并购', '收购', '财报', '季度', '年报', '利润', '亏损', '增长', '扩张', '裁员', '招聘', '团队', '组织架构', 'revenue', 'profit', 'monetize', 'business model', 'saas', 'enterprise', 'consumer', 'freemium', 'license', 'royalty', 'affiliate', 'ads', 'advertising', 'sponsor', 'partnership', 'deal', 'contract', '订单', '成交额', '营收', '回本', 'roi', '投入产出']
    if any(kw in text_for_value for kw in monetization_kws):
        value_tags.append('AI变现赛道')
    
    # 产业关联
    industry_kws = ['医疗', '医药', '医院', '医生', '诊断', '药物', '临床', '病历', '影像', '病理', '金融', '银行', '保险', '证券', '基金', '支付', '风控', '信贷', '理财', '投顾', '量化', '教育', '学校', '学生', '教师', '课程', '学习', '培训', '考试', '辅导', '游戏', '电竞', '娱乐', '影视', '音乐', '动漫', '直播', '短视频', '设计', '创意', 'ui', 'ux', '营销', '广告', '品牌', '推广', '运营', '电商', '零售', '物流', '供应链', '仓储', '制造', '工业', '农业', '法律', '政务', '城市', '交通', '能源', '环保', '建筑', '房地产', '汽车', '自动驾驶', '机器人', '无人机', '航天', '航空', '旅游', '酒店', '餐饮', '体育', '健身', '美容', '时尚', '纺织', '化工', '材料', '生物', '基因', '合成生物', '农业', '养殖', '种植', '渔业', '矿业', '石油', '天然气', '电力', '水利', '通信', '电信', '5g', '6g', '物联网', 'iot', '智慧城市', '数字孪生', '元宇宙', 'vr', 'ar', 'xr', '区块链', 'nft', 'web3', '云计算', '云原生', 'devops', 'ci/cd', '容器', 'kubernetes', 'docker', 'serverless', '微服务', '中台', '大数据', '数据仓库', '数据湖', 'bi', 'etl', '数据治理', '推荐系统', '搜索引擎', '广告系统', '风控系统', '客服系统', 'crm', 'erp', 'scm', 'hrm', 'oa', 'cms', 'mes', 'wms', 'tms', 'oms', 'pms']
    if any(kw in text_for_value for kw in industry_kws):
        value_tags.append('产业关联')
    
    # 技术前沿
    tech_kws = ['模型', '大模型', 'llm', '算法', '架构', '训练', '推理', '微调', '对齐', 'rlhf', 'agent', '多模态', '视觉', '语音', 'nlp', '生成式', 'diffusion', 'transformer', '开源', 'github', '论文', '研究', '实验室', 'arxiv', '芯片', 'gpu', 'tpu', '算力', '集群', '推理加速', '量化', '蒸馏', '压缩', '边缘计算', '量子', '神经网络', '深度学习', '机器学习', '强化学习', '监督学习', '无监督学习', '自监督学习', '迁移学习', '联邦学习', '对比学习', '表征学习', '图神经网络', 'gnn', 'cnn', 'rnn', 'lstm', 'gru', 'attention', 'self-attention', 'moe', 'mixture of experts', '状态空间模型', 'mamba', 'rwkv', 'retnet', '线性注意力', '稀疏注意力', '长上下文', '上下文窗口', 'token', 'embedding', '向量', '检索增强', 'rag', '知识图谱', '符号推理', '神经符号', '因果推理', '贝叶斯', '概率图', '马尔可夫', '蒙特卡洛', '模拟', '仿真', '数字孪生', '具身智能', 'embodied ai', '世界模型', 'world model', '神经辐射场', 'nerf', '3d生成', '视频生成', '图像生成', '文本生成', '代码生成', '音乐生成', '语音合成', 'tts', '语音识别', 'asr', 'ocr', '目标检测', '语义分割', '实例分割', '姿态估计', '人脸识别', '情感分析', '文本分类', '命名实体识别', 'ner', '关系抽取', '事件抽取', '机器翻译', 'mt', '摘要生成', '问答系统', '对话系统', 'chatbot', '虚拟助手', '数字人', '智能体', '自主智能体', 'autoagent', '工具使用', 'function calling', '代码解释器', '插件系统', '浏览器自动化', 'gui自动化', 'rpa', '具身智能', '机器人学习', 'sim2real', '域适应', '持续学习', '终身学习', '元学习', 'few-shot', 'zero-shot', 'prompt engineering', '提示工程', 'chain of thought', 'cot', '思维链', 'tree of thoughts', 'tot', 'self-consistency', 'reflection', '迭代优化', '对抗训练', 'gan', 'vae', 'flow model', '能量模型', '玻尔兹曼机', 'hopfield网络', '脉冲神经网络', 'snn', '神经形态计算', '类脑计算', '存算一体', '近存计算', 'chiplet', '先进封装', '光计算', 'dna存储', '量子计算', '量子机器学习', '量子神经网络']
    if any(kw in text_for_value for kw in tech_kws):
        value_tags.append('技术前沿')
    
    # 产品工具
    product_kws = ['工具', '应用', 'app', '软件', '平台', '插件', '扩展', 'api', 'sdk', '服务', '上线', '发布', '更新', '版本', '功能', '特性', '体验', '界面', '交互', '设计', '原型', 'demo', 'beta', '公测', '内测', '效率', '生产力', '自动化', '工作流', '助手', 'copilot', '编程', '代码', '开发', 'debug', '测试', '部署', 'ide', '编辑器', 'notebook', 'jupyter', 'vscode', 'cursor', 'windsurf', 'trae', 'github copilot', 'codeium', 'tabnine', 'replit', 'codesandbox', 'stackblitz', 'gitpod', 'docker', 'kubernetes', 'terraform', 'ansible', 'jenkins', 'gitlab ci', 'github actions', 'vercel', 'netlify', 'aws', 'azure', 'gcp', '阿里云', '腾讯云', '华为云', '百度云', '火山引擎', '无代码', '低代码', 'nocode', 'lowcode', 'airtable', 'notion', 'figma', 'sketch', 'adobe', 'canva', 'midjourney', 'stable diffusion', 'dall-e', 'sora', 'runway', 'pika', 'heygen', 'synthesia', 'elevenlabs', 'chatgpt', 'claude', 'gemini', 'gpt-4', 'gpt-3.5', 'llama', 'mistral', 'anthropic', 'openai', 'google ai', 'microsoft ai', 'meta ai', 'baidu ai', 'alibaba ai', 'tencent ai', '字节跳动', 'bytedance', '月之暗面', 'minimax', '智谱', '百川', '零一万物', '阶跃星辰', '面壁智能', '深度求索', 'deepseek']
    if any(kw in text_for_value for kw in product_kws):
        value_tags.append('产品工具')
    
    # 政策监管
    policy_kws = ['政策', '法规', '监管', '合规', '安全', '隐私', '数据保护', 'gdpr', '伦理', '道德', '风险', '治理', '标准', '认证', '许可', '版权', '知识产权', '专利', '诉讼', '禁令', '限制', '审查', '审核', '问责', '透明', '可解释', '公平', '偏见', '歧视', 'deepfake', '虚假信息', '诈骗', '滥用', 'ai法案', 'ai act', '欧盟', '美国', '中国', '英国', '日本', '韩国', '新加坡', '联合国', 'who', 'ieee', 'iso', 'nist', '网络安全', '信息安全', '数据安全', '个人信息保护法', '数据安全法', '网络安全法', '算法推荐管理规定', '深度合成管理规定', '生成式ai管理办法', '人工智能法', 'ai治理', 'ai伦理准则', 'responsible ai', 'trustworthy ai', 'human-centric ai', 'ai for good', '可持续发展', 'esg', '碳足迹', '绿色ai', 'ai能耗', '数据中心能耗', 'pue', '可再生能源', '社会责任', '数字鸿沟', '技术普惠', 'ai普惠', '数字包容', '无障碍', '适老化', '儿童保护', '未成年人保护', '内容安全', '有害内容', '仇恨言论', '暴力内容', '色情内容', '赌博', '毒品', '恐怖主义', '极端主义', '国家秘密', '军事', '国防', '情报', '间谍', '网络战', '信息战', '认知战', '舆论操控', '选举干预', '社会工程', '钓鱼', '勒索软件', '恶意软件', '病毒', '蠕虫', '木马', '后门', '漏洞', '0day', 'cve', '渗透测试', '红队', '蓝队', ' purple team', 'soc', 'siem', 'soar', 'xdr', 'edr', 'mdr', '零信任', 'sase', 'sse', 'casb', 'swg', 'ztna', 'sdp', 'mfa', 'sso', 'iam', 'pki', '证书', '加密', '解密', '哈希', '签名', '区块链安全', '智能合约安全', 'defi安全', 'nft安全', '钱包安全', '交易所安全']
    if any(kw in text_for_value for kw in policy_kws):
        value_tags.append('政策监管')
    
    # 创业投资
    venture_kws = ['创业', '初创', 'startup', '独角兽', '孵化器', '加速器', 'vc', 'pe', '天使', '种子', 'a轮', 'b轮', 'c轮', 'd轮', 'e轮', 'pre-ipo', '战略投资', '并购', '收购', 'ipo', '上市', '退市', '估值', '市值', '股价', '股东', '董事会', 'ceo', '创始人', '高管', '离职', '加盟', '任命', '合作', '联盟', '生态', '竞争', '对手', '市场份额', '行业格局', '洗牌', '融资', '募资', '资本', '基金', 'lp', 'gp', '有限合伙', '风险投资', '私募', '对冲基金', '主权基金', '养老金', '家族办公室', 'fo', 'cvc', '企业风投', '战略投资', '产业投资', '财务投资', 'PIPE', '可转债', '优先股', '普通股', '期权', '股权', '股权激励', 'esop', '员工持股', '创始人股份', '控制权', '投票权', 'ab股', '同股不同权', 'vie', '红筹', '借壳', 'spac', '反向并购', '私有化', '分拆', '重组', '破产', '清算', '重整', '债务重组', '债转股', '资产剥离', '业务整合', '协同效应', '规模效应', '网络效应', '飞轮效应', '护城河', '壁垒', '护城河', '竞争优势', '差异化', '成本领先', '聚焦战略', '蓝海', '红海', '颠覆', '创新', '迭代', '试错', 'mvp', 'pmf', '产品市场匹配', '增长黑客', '病毒传播', '网络效应', '平台效应', '双边市场', '多边市场', '生态系统', '超级应用', '杀手级应用', '爆款', '现象级', '风口', '赛道', '赛道选择', '时机', '窗口期', '红利', '先发优势', '后发优势', '弯道超车', '降维打击', '跨界', '融合', '边界模糊', '重新定义', '范式转移', '技术周期', '创新周期', '经济周期', '资本周期', '行业周期', '生命周期', 's曲线', 'j曲线', '增长曲线', '指数增长', '线性增长', '爆发增长', '稳健增长', '存量博弈', '增量市场', '下沉市场', '出海', '全球化', '本地化', 'glocalization']
    if any(kw in text_for_value for kw in venture_kws):
        value_tags.append('创业投资')
    
    # 去重并限制最多3个标签
    value_tags = list(dict.fromkeys(value_tags))[:3]
    if not value_tags:
        value_tags = ['综合']
    
    return round(max(0, min(100, relevance + heat_score + source_bonus)), 1), audience, value_tags


def get_level(score: float, config: dict) -> dict:
    """获取分数档位"""
    levels = config.get("scoring", {}).get("levels", [])
    if not levels:
        levels = [
            {"name": "精选", "threshold": 80, "color": "#c8786a"},
            {"name": "推荐", "threshold": 70, "color": "#b8956a"},
            {"name": "参考", "threshold": 60, "color": "#8a9598"}
        ]
    
    for i, level in enumerate(levels):
        if score >= level["threshold"]:
            return {
                "name": level["name"],
                "threshold": level["threshold"],
                "color": level.get("color", "#c8a45c"),
                "description": level.get("description", ""),
                "index": i
            }
    
    # 低于最低阈值
    return {
        "name": "其他",
        "threshold": 0,
        "color": "#999999",
        "description": "",
        "index": len(levels)
    }


def categorize_topics(topics: list, config: dict) -> dict:
    """按档位分类"""
    levels = config.get("scoring", {}).get("levels", [])
    if not levels:
        levels = [
            {"name": "精选", "threshold": 80},
            {"name": "推荐", "threshold": 70},
            {"name": "参考", "threshold": 60}
        ]
    
    # 按阈值从高到低排序
    sorted_levels = sorted(levels, key=lambda x: x["threshold"], reverse=True)
    
    categories = {lvl["name"]: [] for lvl in sorted_levels}
    categories["其他"] = []
    
    total = len(topics)
    pbar = ProgressBar(total, "📊 智能评分")
    
    for t in topics:
        score, audience, value_tags = score_topic(t, config)
        t["score"] = score
        t["audience"] = audience
        t["value_tags"] = value_tags
        t["level"] = get_level(t["score"], config)
        
        categorized = False
        for lvl in sorted_levels:
            if t["score"] >= lvl["threshold"]:
                categories[lvl["name"]].append(t)
                categorized = True
                break
        
        if not categorized:
            categories["其他"].append(t)
        
        pbar.update(1)
    
    return categories


# ============ HTML 生成 ============
def _escape_attr(s: str) -> str:
    """转义 HTML 属性值中的特殊字符"""
    return s.replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')

def generate_css(config: dict) -> str:
    """生成 CSS 样式"""
    theme = config.get("theme", {})
    mode = theme.get("mode", "dark")
    colors = theme.get(mode, theme.get("dark", {}))
    animations = config.get("animations", {})
    anim_enabled = animations.get("enabled", True)
    
    # ===== 扶清闲话 · AI时代观察 设计系统 =====
    accent = colors.get('accent', '#c8a45c')
    css = f"""
/* ===== 扶清闲话 · AI时代观察 — 设计系统 ===== */
:root {{
  --bg: {colors.get('background', '#121118')};
  --bg-warm: #1a1820;
  --card: {colors.get('card', 'rgba(28,26,35,0.65)')};
  --card-hover: rgba(32,30,40,0.8);
  --text-primary: {colors.get('text_primary', '#e8e0d4')};
  --text-secondary: {colors.get('text_secondary', '#9a9284')};
  --text-muted: #6b6560;
  --accent: {accent};
  --accent-soft: #b8944f;
  --accent-glow: rgba(200,164,92,0.15);
  --accent-light: rgba(200,164,92,0.08);
  --border: {colors.get('border', 'rgba(150,140,120,0.1)')};
  --border-strong: rgba(150,140,120,0.18);
  --shadow: 0 4px 32px rgba(0,0,0,0.35);
  --radius: 12px;
  --font-serif: 'Georgia', 'Noto Serif SC', 'STSong', 'Songti SC', 'SimSun', serif;
  --font-sans: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{
  scroll-behavior: smooth;
  scrollbar-width: thin;
  scrollbar-color: rgba(150,140,120,0.25) transparent;
}}
/* WebKit 滚动条样式 */
::-webkit-scrollbar {{ width: 6px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{
  background: rgba(150,140,120,0.25);
  border-radius: 3px;
}}
::-webkit-scrollbar-thumb:hover {{ background: rgba(200,164,92,0.35); }}
::-webkit-scrollbar-corner {{ background: transparent; }}
body {{
  transition: opacity 0.12s ease;
  font-family: var(--font-sans);
  background: var(--bg);
  background-image:
    radial-gradient(ellipse at 50% 0%, rgba(200,164,92,0.04) 0%, transparent 60%),
    radial-gradient(ellipse at 20% 80%, rgba(180,150,120,0.03) 0%, transparent 50%);
  color: var(--text-primary);
  line-height: 1.75;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}}
.container {{ max-width: 860px; margin: 0 auto; padding: 24px 20px; }}

/* ===== 品牌导航栏 — 扶清闲话统一站点头 ===== */
.brand-nav {{
  position: sticky; top: 0; z-index: 100;
  background: rgba(18,17,24,0.88);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  transition: background 0.3s ease;
}}
[data-theme="light"] .brand-nav {{
  background: rgba(248,245,240,0.92);
}}
.brand-nav-inner {{
  max-width: 860px; margin: 0 auto;
  display: flex; align-items: center; gap: 1.2rem;
  padding: 12px 0; flex-wrap: wrap;
}}
.brand-nav .brand-link {{
  font-family: var(--font-serif);
  font-size: 1.1rem; font-weight: 700;
  color: var(--accent); text-decoration: none;
  letter-spacing: 0.04em; white-space: nowrap;
  transition: opacity 0.2s ease;
}}
.brand-nav .brand-link:hover {{ opacity: 0.75; }}
.brand-nav nav {{
  display: flex; gap: 1rem; flex-wrap: wrap; align-items: center;
}}
.brand-nav nav a {{
  color: var(--text-secondary); text-decoration: none;
  font-size: 0.85rem; transition: color 0.2s ease;
  font-family: var(--font-sans);
}}
.brand-nav nav a:hover {{ color: var(--accent-soft); }}
.brand-nav nav a.active {{
  color: var(--accent); font-weight: 600;
  position: relative;
}}
.brand-nav nav a.active::after {{
  content: ''; position: absolute; bottom: -4px; left: 0; right: 0;
  height: 2px; background: var(--accent); border-radius: 1px;
}}

/* 汉堡菜单按钮 — 仅移动端可见 */
.nav-hamburger {{
  display: none;
  background: none; border: none; cursor: pointer;
  padding: 6px; margin-left: auto;
  flex-direction: column; gap: 4px; justify-content: center;
  align-items: center; width: 32px; height: 32px;
  border-radius: 6px; transition: background 0.2s;
}}
.nav-hamburger:hover {{ background: rgba(200,164,92,0.08); }}
.nav-hamburger span {{
  display: block; width: 18px; height: 2px;
  background: var(--text-secondary); border-radius: 1px;
  transition: all 0.3s ease;
}}
.nav-hamburger.open span:nth-child(1) {{
  transform: rotate(45deg) translate(4px, 4px);
}}
.nav-hamburger.open span:nth-child(2) {{ opacity: 0; }}
.nav-hamburger.open span:nth-child(3) {{
  transform: rotate(-45deg) translate(4px, -4px);
}}
"""

    # 动画
    if anim_enabled:
        if animations.get("breathe", True):
            css += """
@keyframes breathe {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 0.75; }
}
"""
    
    css += """
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ===== 页头 — 杂志式排版 ===== */
header {
  text-align: center;
  padding: 70px 20px 48px;
  position: relative;
}
header .brand-signature {
  font-family: var(--font-serif);
  font-size: 0.82rem; color: var(--accent);
  letter-spacing: 0.12em; text-transform: uppercase;
  margin-bottom: 18px; opacity: 0.7;
  font-style: italic;
}
header::after {
  content: '';
  display: block;
  width: 40px;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  margin: 28px auto 0;
  opacity: 0.6;
}
header h1 {
  font-family: var(--font-serif);
  font-size: 2.4rem;
  font-weight: 400;
  letter-spacing: 0.08em;
  color: var(--text-primary);
  margin-bottom: 14px;
}
header .subtitle {
  color: var(--text-secondary);
  font-size: 0.95rem;
  font-style: italic;
  letter-spacing: 0.04em;
  animation: breathe 4s ease-in-out infinite;
}
header .philosophy-line {
  color: var(--text-muted);
  font-size: 0.78rem; font-family: var(--font-serif);
  font-style: italic; letter-spacing: 0.06em;
  margin-top: 12px; opacity: 0.6;
}
header .update-time {
  color: var(--text-muted);
  font-size: 0.78rem;
  margin-top: 20px;
  font-family: var(--font-sans);
  font-style: normal;
}
"""
    
    # 搜索栏
    css += f"""
.search-bar {{ max-width: 560px; margin: 0 auto 28px; }}
.search-bar input {{
  width: 100%; padding: 11px 20px; border-radius: 24px;
  border: 1px solid var(--border); background: var(--card);
  color: var(--text-primary); font-size: 0.9rem;
  outline: none; transition: all 0.3s ease;
  backdrop-filter: blur(10px);
}}
.search-bar input:focus {{
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}}
.search-bar input::placeholder {{ color: var(--text-muted); font-style: italic; }}
"""
    
    # 档位标签
    css += """
.level-tabs {
  display: flex; gap: 8px; justify-content: center;
  margin: 32px 0 28px; flex-wrap: wrap;
}
.level-tab {
  padding: 7px 18px; border-radius: 20px; font-size: 0.82rem;
  font-weight: 500; cursor: pointer; border: 1px solid var(--border);
  transition: all 0.25s ease; opacity: 0.55;
  background: transparent;
}
.level-tab:hover { opacity: 0.85; border-color: var(--border-strong); }
.level-tab.active {
  opacity: 1; transform: translateY(-1px);
}
"""
    
    # 卡片
    css += """
.topic-list { display: block; }
.topic-list.active { animation: fadeIn 0.5s ease; }

/* ===== 价值标签系统 ===== */
.value-tags {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin: 8px 0 4px; padding: 0 2px;
}
.value-tag {
  display: inline-flex; align-items: center;
  padding: 3px 10px; border-radius: 12px;
  font-size: 0.72rem; font-weight: 500;
  cursor: pointer; transition: all 0.2s ease;
  border: 1px solid transparent;
}
/* 各标签颜色 */
.value-tag[data-tag="AI变现赛道"] { background: #e8f5e9; color: #2e7d32; }
.value-tag[data-tag="产业关联"] { background: #e3f2fd; color: #1565c0; }
.value-tag[data-tag="技术前沿"] { background: #f3e5f5; color: #7b1fa2; }
.value-tag[data-tag="产品工具"] { background: #fff3e0; color: #ef6c00; }
.value-tag[data-tag="政策监管"] { background: #fce4ec; color: #c62828; }
.value-tag[data-tag="创业投资"] { background: #fff8e1; color: #f57f17; }
.value-tag[data-tag="综合"] { background: #f5f5f5; color: #666; }
.value-tag:hover { opacity: 0.8; transform: translateY(-1px); }
.value-tag.active {
  opacity: 1; font-weight: 600;
  box-shadow: 0 0 0 2px var(--accent);
}

/* 价值标签筛选栏 */
.value-filter-bar {
  display: flex; gap: 8px; justify-content: center;
  margin: 16px 0 24px; flex-wrap: wrap;
}
.value-filter-btn {
  padding: 5px 14px; border-radius: 16px;
  font-size: 0.78rem; font-weight: 500;
  cursor: pointer; transition: all 0.25s ease;
  border: 1px solid var(--border);
  background: transparent; opacity: 0.6;
  color: var(--text-secondary);
}
.value-filter-btn:hover { opacity: 0.9; }
.value-filter-btn.active {
  opacity: 1; transform: translateY(-1px);
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-bg, rgba(200,164,92,0.08));
}

.heat-stage-badge{display:inline-flex;align-items:center;gap:3px;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap}
.heat-stage-badge[data-stage="萌芽"]{background:#e8f5e9;color:#2e7d32}
.heat-stage-badge[data-stage="上升"]{background:#fff3e0;color:#ef6c00}
.heat-stage-badge[data-stage="爆发"]{background:#fce4ec;color:#c62828;animation:pulse 2s infinite}
.heat-stage-badge[data-stage="热门"]{background:#fff8e1;color:#f57f17}
.heat-stage-badge[data-stage="持续"]{background:#e3f2fd;color:#1565c0}
.heat-stage-badge[data-stage="降温"]{background:#f3e5f5;color:#7b1fa2}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.7}}
.topic-card {
  background: var(--card);
  border-radius: var(--radius);
  padding: 28px 30px;
  margin-bottom: 18px;
  border: 1px solid var(--border);
  box-shadow: 0 2px 12px rgba(0,0,0,0.12);
  transition: all 0.3s ease;
  backdrop-filter: blur(12px);
  position: relative;
  animation: cardFadeIn 0.4s ease backwards;
}
@keyframes cardFadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
.topic-card:hover {
  background: var(--card-hover);
  border-color: var(--border-strong);
  transform: translateY(-2px);
  box-shadow: 0 6px 24px rgba(0,0,0,0.22);
}
.topic-card h2 {
  font-size: 1.1rem; font-weight: 600;
  margin-bottom: 8px; line-height: 1.5;
}
.topic-card h2 a {
  color: var(--text-primary); text-decoration: none;
  transition: color 0.2s ease;
}
.topic-card h2 a:hover { color: var(--accent-soft); }
.topic-card .summary {
  color: var(--text-secondary); font-size: 0.9rem;
  margin-bottom: 10px; line-height: 1.65;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-height: 3.3em;
}
/* 隐藏内容小结 */
.topic-card .zh-summary {
  display: none !important;
}

/* AI 内容区块 - 统一容器 */
.ai-insights {
  margin-top: 14px; padding-top: 12px;
  border-top: 1px solid var(--border);
  position: relative;
}
.ai-insights::before {
  content: '';
  position: absolute; top: -1px; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(200,164,92,0.2), transparent);
}
.ai-insight-item {
  margin-bottom: 10px; padding: 10px 14px;
  border-radius: 0 8px 8px 0; line-height: 1.7;
  border-left: 3px solid var(--accent-glow);
}
.ai-insight-item.perspective-comment { border-left-color: rgba(100,180,220,0.5); }
.ai-insight-item.action-guidance { border-left-color: rgba(120,200,150,0.5); }

.card-ai-persp { transition: opacity 0.2s ease; }
.card-ai-action { transition: opacity 0.2s ease; }
.card-difficulty { transition: opacity 0.2s ease; }
.ai-insight-item:last-child { margin-bottom: 0; }
.ai-label {
  display: block; font-size: 0.78rem; font-weight: 600;
  margin-bottom: 4px; letter-spacing: 0.02em;
}
.ai-label::before { content: ''; }
.ai-insight-item.zh-summary .ai-label::before { content: '\U0001F4DD '; }
.ai-insight-item.perspective-comment .ai-label::before { content: '\U0001F4A1 '; }
.ai-insight-item.action-guidance .ai-label::before { content: '\U0001F3AF '; }
.ai-insight-item p {
  margin: 0; font-size: 0.88rem;
}
.ai-insight-item.zh-summary {
  background: rgba(200,164,92,0.06);
  border-left: 3px solid var(--accent);
  color: """ + accent + """;
}
.ai-insight-item.zh-summary .ai-label { color: var(--accent); }
.ai-insight-item.perspective-comment {
  background: rgba(140,160,180,0.06);
  border-left: 3px solid #8899aa;
  color: var(--text-primary);
}
.ai-insight-item.perspective-comment .ai-label { color: #8899aa; }
.ai-insight-item.action-guidance {
  background: rgba(200,140,120,0.06);
  border-left: 3px solid #c88c78;
  color: var(--text-primary);
}
.ai-insight-item.action-guidance .ai-label { color: #c88c78; }
"""
    
    # 标签
    css += """
.card-header {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 14px; flex-wrap: wrap;
}
.score-badge {
  padding: 3px 12px; border-radius: 14px;
  font-size: 0.76rem; font-weight: 600;
  letter-spacing: 0.04em;
}
.source-tag {
  padding: 3px 10px; border-radius: 10px; font-size: 0.72rem;
  background: var(--bg-warm); color: var(--text-muted);
}
.meta {
  display: flex; gap: 16px; margin-top: 14px;
  font-size: 0.76rem; color: var(--text-muted); flex-wrap: wrap;
}
.meta a { color: var(--accent-soft); text-decoration: none; }
.meta a:hover { opacity: 0.7; }
"""
    
    # NEW 角标
    css += """
.new-badge {
  position: absolute; top: -9px; right: -9px;
  background: linear-gradient(135deg, #d4756b, #c96b5a);
  color: #fff; font-size: 0.68rem; font-weight: 600;
  padding: 4px 10px; border-radius: 10px;
  letter-spacing: 0.06em;
  animation: breathe 3s ease-in-out infinite;
  box-shadow: 0 2px 10px rgba(200,100,80,0.3);
}
"""
    
    # 卡片操作栏
    css += """
.card-actions {
  display: flex; gap: 10px; margin-top: 12px;
  padding-top: 12px; border-top: 1px solid var(--border);
  flex-wrap: wrap;
}
.copy-btn, .share-btn {
  background: transparent; border: 1px solid var(--border);
  color: var(--text-muted); padding: 5px 14px;
  border-radius: 14px; font-size: 0.75rem; cursor: pointer;
  transition: all 0.2s ease;
}
.copy-btn:hover, .share-btn:hover {
  border-color: var(--accent-soft); color: var(--accent-soft);
}
.copy-btn.copied {
  background: var(--accent-soft); color: var(--bg);
  border-color: var(--accent-soft);
}
"""
    
    # 收藏按钮
    css += """
.bookmark-btn {
  background: none; border: none; cursor: pointer;
  font-size: 1.15rem; padding: 3px 6px;
  transition: transform 0.2s ease;
  color: var(--text-muted); line-height: 1;
}
.bookmark-btn:hover { transform: scale(1.2); }
.bookmark-btn.bookmarked { color: #d4a85c; }
"""
    
    # 工具栏
    css += f"""
.toolbar {{
  position: fixed; top: 14px; right: 14px; z-index: 999;
  display: flex; gap: 6px; align-items: center;
}}
.toolbar button, .toolbar a {{
  padding: 7px 14px; border-radius: 18px; font-size: 0.78rem;
  font-weight: 500; cursor: pointer; border: none; text-decoration: none;
  transition: all 0.25s ease; box-shadow: 0 2px 12px rgba(0,0,0,0.3);
  backdrop-filter: blur(8px);
}}
.theme-toggle-btn {{
  background: var(--card); color: var(--text-secondary);
  border: 1px solid var(--border);
}}
.theme-toggle-btn:hover {{ border-color: var(--accent-soft); color: var(--accent-soft); }}
/* 回到顶部按钮 */
.back-to-top {{
  position: fixed; bottom: 30px; right: 30px;
  width: 50px; height: 50px; border-radius: 50%;
  background: var(--card); border: 1px solid var(--border);
  color: var(--text-primary); font-size: 20px; cursor: pointer;
  opacity: 0; visibility: hidden; transition: all 0.3s ease;
  z-index: 999; display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}}
.back-to-top.visible {{ opacity: 1; visibility: visible; }}
.back-to-top:hover {{ transform: translateY(-3px); box-shadow: 0 6px 20px rgba(0,0,0,0.2); }}

/* 阅读进度条 */
.reading-progress {{
  position: fixed; top: 0; left: 0; width: 0%; height: 2px;
  background: linear-gradient(90deg, var(--accent), #c8786a, var(--accent-soft));
  z-index: 10001; transition: width 0.1s ease;
}}
"""
    
    # 设置弹窗
    css += """
.settings-btn {
  background: var(--card) !important; color: var(--text-secondary) !important;
  border: 1px solid var(--border) !important;
}
.settings-btn:hover { border-color: var(--accent-soft) !important; color: var(--accent-soft) !important; }

.modal-overlay {
  display: none; position: fixed; inset: 0; z-index: 10000;
  background: rgba(0,0,0,0.6); backdrop-filter: blur(4px);
  justify-content: center; align-items: center;
  animation: fadeIn 0.2s ease;
}
.modal-overlay.active { display: flex; }
.modal-box {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 16px; width: 92%; max-width: 680px; max-height: 80vh;
  display: flex; flex-direction: column; box-shadow: 0 16px 48px rgba(0,0,0,0.4);
  overflow: hidden;
}
.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 20px 24px 16px; border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.modal-header h2 {
  font-family: var(--font-serif); font-size: 1.2rem; font-weight: 400;
  color: var(--text-primary); letter-spacing: 0.04em;
}
.modal-close {
  background: none; border: none; color: var(--text-muted);
  font-size: 1.4rem; cursor: pointer; padding: 4px 8px; line-height: 1;
  transition: color 0.2s;
}
.modal-close:hover { color: var(--text-primary); }
.modal-tabs {
  display: flex; gap: 0; border-bottom: 1px solid var(--border);
  padding: 0 24px; overflow-x: auto; flex-shrink: 0;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
}
.modal-tabs::-webkit-scrollbar { height: 3px; }
.modal-tabs::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.modal-tab {
  padding: 10px 16px; font-size: 0.82rem; cursor: pointer;
  color: var(--text-muted); border-bottom: 2px solid transparent;
  transition: all 0.2s; white-space: nowrap; background: none; border-top: none;
  border-left: none; border-right: none;
}
.modal-tab:hover { color: var(--text-secondary); }
.modal-tab.active { color: var(--accent-soft); border-bottom-color: var(--accent-soft); }
.modal-body {
  padding: 20px 24px 24px; overflow-y: auto; flex: 1;
}
.modal-panel { display: none; }
.modal-panel.active { display: block; animation: fadeIn 0.25s ease; }
.modal-panel h3 {
  font-size: 0.95rem; color: var(--text-primary); margin: 20px 0 10px;
  padding-bottom: 6px; border-bottom: 1px solid var(--border);
}
.modal-panel h3:first-child { margin-top: 0; }
.modal-panel p, .modal-panel li {
  font-size: 0.85rem; color: var(--text-secondary); line-height: 1.8;
}
.modal-panel ul { padding-left: 18px; }
.modal-panel li { margin-bottom: 4px; }
.modal-panel .tip {
  background: var(--accent-light); border-radius: 8px;
  padding: 10px 14px; margin: 10px 0; font-size: 0.82rem;
  color: var(--accent-soft);
}
.modal-panel .link-list { list-style: none; padding: 0; }
.modal-panel .link-list li {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-radius: 8px; margin-bottom: 4px;
  background: var(--card); border: 1px solid var(--border);
}
.modal-panel .link-list li a {
  color: var(--accent-soft); text-decoration: none; font-size: 0.82rem;
}
.modal-panel .link-list li a:hover { text-decoration: underline; }
.modal-panel .link-list li span { font-size: 0.78rem; color: var(--text-muted); }
.modal-panel .source-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
}
.modal-panel .source-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; border-radius: 8px;
  background: var(--card); border: 1px solid var(--border);
  font-size: 0.82rem; color: var(--text-secondary);
}
.modal-panel .source-item .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent-soft); flex-shrink: 0;
}
.modal-panel .score-bar {
  display: flex; align-items: center; gap: 12px; margin: 8px 0;
  padding: 10px 14px; border-radius: 8px;
  background: var(--card); border: 1px solid var(--border);
}
.modal-panel .score-bar .label {
  font-size: 0.82rem; font-weight: 500; min-width: 50px;
}
.modal-panel .score-bar .range {
  flex: 1; height: 6px; border-radius: 3px; background: var(--border);
  position: relative;
}
.modal-panel .score-bar .fill {
  position: absolute; left: 0; top: 0; height: 100%;
  border-radius: 3px;
}
.modal-panel .score-bar .val {
  font-size: 0.78rem; color: var(--text-muted); min-width: 40px; text-align: right;
}
.modal-panel kbd {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 4px; padding: 1px 6px; font-size: 0.78rem;
  font-family: monospace; color: var(--text-secondary);
}

/* ===== 自定义设置组件 ===== */
.setting-group {
  margin: 16px 0;
}
.setting-group-title {
  font-size: 0.88rem; font-weight: 500; color: var(--text-primary);
  margin-bottom: 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}
.setting-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; gap: 12px;
}
.setting-row + .setting-row { border-top: 1px solid var(--border); }
.setting-label {
  font-size: 0.82rem; color: var(--text-secondary); flex: 1;
}
.setting-label small {
  display: block; font-size: 0.72rem; color: var(--text-muted); margin-top: 2px;
}
.setting-select {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 5px 10px; font-size: 0.8rem;
  color: var(--text-primary); cursor: pointer; outline: none;
  min-width: 100px;
}
.setting-select:focus { border-color: var(--accent-soft); }

/* Toggle 开关 */
.toggle-switch {
  position: relative; width: 40px; height: 22px;
  background: var(--border); border-radius: 11px;
  cursor: pointer; transition: background 0.3s; flex-shrink: 0;
}
.toggle-switch.active { background: var(--accent-soft); }
.toggle-switch::after {
  content: ''; position: absolute; top: 2px; left: 2px;
  width: 18px; height: 18px; border-radius: 50%;
  background: var(--text-primary); transition: transform 0.3s;
}
.toggle-switch.active::after { transform: translateX(18px); }

/* 数据源开关网格 */
.source-toggle-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 6px; margin-top: 8px;
}
.source-toggle-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: 8px;
  background: var(--card); border: 1px solid var(--border);
  font-size: 0.8rem; color: var(--text-secondary);
  cursor: pointer; transition: all 0.2s;
}
.source-toggle-item:hover { border-color: var(--accent-soft); }
.source-toggle-item.disabled { opacity: 0.4; }
.source-toggle-item .toggle-switch { width: 32px; height: 18px; }
.source-toggle-item .toggle-switch::after { width: 14px; height: 14px; }
.source-toggle-item .toggle-switch.active::after { transform: translateX(14px); }
.source-toggle-item .src-name { flex: 1; }
.source-toggle-item .src-url {
  font-size: 0.68rem; color: var(--text-muted);
  max-width: 0; overflow: hidden; white-space: nowrap;
  transition: max-width 0.3s;
}
.source-toggle-item:hover .src-url { max-width: 200px; }

/* 设置操作按钮 */
.setting-actions {
  display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end;
}
.setting-actions button {
  padding: 6px 16px; border-radius: 8px; font-size: 0.8rem;
  cursor: pointer; border: 1px solid var(--border);
  background: var(--card); color: var(--text-secondary);
  transition: all 0.2s;
}
.setting-actions button:hover { border-color: var(--accent-soft); color: var(--accent-soft); }
.setting-actions button.primary {
  background: var(--accent-soft); color: var(--bg); border-color: var(--accent-soft);
}
.setting-actions button.primary:hover { opacity: 0.85; }

@media (max-width: 600px) {
  .modal-box { width: 96%; max-height: 85vh; }
  .modal-header { padding: 16px 18px 12px; }
  .modal-body { padding: 16px 18px 20px; }
  .modal-panel .source-grid { grid-template-columns: 1fr; }
  .source-toggle-grid { grid-template-columns: 1fr; }
}
"""

    # 页脚
    css += """
footer {
  text-align: center; padding: 48px 24px 36px;
  color: var(--text-muted); font-size: 0.78rem;
  border-top: 1px solid var(--border);
  margin-top: 56px; line-height: 2.2;
}
footer a {
  color: var(--accent-soft); text-decoration: none;
  transition: opacity 0.2s ease;
}
footer a:hover { opacity: 0.7; }
footer .footer-links { margin-top: 6px; }
footer .footer-links a { margin: 0 8px; }

/* 品牌装饰分隔符 */
.brand-divider {
  text-align: center; color: var(--accent);
  margin: 3rem 0; font-size: 1rem;
  letter-spacing: 3px; opacity: 0.45;
  font-family: var(--font-serif);
}

/* 品牌身份卡片 */
.brand-identity {
  max-width: 480px; margin: 32px auto 0;
  padding: 24px 28px; border-radius: 16px;
  background: linear-gradient(135deg, rgba(200,164,92,0.05), rgba(200,120,106,0.04));
  border: 1px solid rgba(200,164,92,0.1);
  text-align: center;
}
.brand-identity .brand-name {
  font-family: var(--font-serif);
  font-size: 1rem; color: var(--accent);
  letter-spacing: 0.08em; margin-bottom: 8px;
}
.brand-identity .brand-desc {
  font-size: 0.8rem; color: var(--text-secondary);
  line-height: 1.8; margin-bottom: 4px;
}
.brand-identity .brand-philosophy {
  font-family: var(--font-serif);
  font-size: 0.78rem; color: var(--text-muted);
  font-style: italic; letter-spacing: 0.06em;
  margin-top: 12px; opacity: 0.7;
}
.brand-identity .brand-cornerstones {
  display: flex; justify-content: center; gap: 16px;
  margin-top: 14px; flex-wrap: wrap;
}
.brand-identity .cornerstone {
  font-size: 0.72rem; color: var(--text-muted);
  padding: 3px 12px; border-radius: 99px;
  border: 1px solid var(--border);
  letter-spacing: 0.04em;
  transition: all 0.2s ease;
}
.brand-identity .cornerstone:hover {
  border-color: var(--accent-soft); color: var(--accent-soft);
}

/* 页脚统一站点导航 */
.footer-site-nav {
  display: flex; justify-content: center; gap: 1rem;
  flex-wrap: wrap; margin-top: 16px;
  padding-top: 16px; border-top: 1px solid var(--border);
}
.footer-site-nav a {
  color: var(--text-secondary); font-size: 0.8rem;
  text-decoration: none; transition: color 0.2s;
  font-family: var(--font-sans);
}
.footer-site-nav a:hover { color: var(--accent-soft); }
"""
    
    # 暗色模式 CSS 变量切换
    dark_colors = theme.get("dark", {})
    light_colors = theme.get("light", {})
    css += f"""
[data-theme="light"] {{
  --bg: {light_colors.get('background', '#f8f5f0')};
  --bg-warm: #f0ece6;
  --card: {light_colors.get('card', 'rgba(255,252,245,0.85)')};
  --card-hover: rgba(255,252,245,0.95);
  --text-primary: {light_colors.get('text_primary', '#2c2822')};
  --text-secondary: {light_colors.get('text_secondary', '#6b655c')};
  --text-muted: #9a9488;
  --accent: {light_colors.get('accent', '#a08050')};
  --accent-soft: #8b6f3f;
  --accent-glow: rgba(160,128,80,0.12);
  --accent-light: rgba(160,128,80,0.06);
  --border: {light_colors.get('border', 'rgba(160,140,110,0.15)')};
  --border-strong: rgba(160,140,110,0.25);
  --shadow: 0 4px 24px rgba(0,0,0,0.08);
  scrollbar-color: rgba(160,140,110,0.3) transparent;
  background-image:
    radial-gradient(ellipse at 50% 0%, rgba(160,128,80,0.06) 0%, transparent 60%),
    radial-gradient(ellipse at 20% 80%, rgba(140,110,80,0.04) 0%, transparent 50%);
}}
[data-theme="light"] ::-webkit-scrollbar-thumb {{ background: rgba(160,140,110,0.3); }}
[data-theme="light"] ::-webkit-scrollbar-thumb:hover {{ background: rgba(160,128,80,0.45); }}
/* 内容小结已移除 */
[data-theme="light"] .ai-insight-item.perspective-comment {{ background: rgba(100,120,140,0.05); }}
[data-theme="light"] .ai-insight-item.action-guidance {{ background: rgba(180,120,100,0.05); }}
"""
    
    # 收藏筛选标签样式
    css += """
.bookmark-tab { background: rgba(212,168,92,0.08); }
.bookmark-tab.active { background: rgba(212,168,92,0.15) !important; }

.empty-state {
  text-align: center; padding: 60px 20px;
  color: var(--text-muted);
}
.empty-state .icon { font-size: 3rem; margin-bottom: 16px; }
"""
    
    # 响应式
    css += """
@media (max-width: 600px) {
  header { padding: 36px 16px 28px; }
  header h1 { font-size: 1.5rem; }
  header .brand-signature { display: none; }
  header .philosophy-line { display: none; }
  .topic-card { padding: 18px 16px; }
  .level-tab { padding: 6px 12px; font-size: 0.75rem; }
  .level-tab span { display: none; }
  .toolbar {
    top: 50px; right: auto; left: 12px;
    flex-direction: column; gap: 0;
    background: rgba(28,26,35,0.75);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 4px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.25);
    transition: opacity 0.25s ease, transform 0.25s ease;
    align-items: stretch;
  }
  .toolbar button, .toolbar a {
    padding: 0; border-radius: 10px;
    width: 38px; height: 38px;
    display: flex; align-items: center; justify-content: center;
    border: none; box-shadow: none;
    transition: all 0.2s ease;
    font-size: 16px; line-height: 1;
    background: transparent;
    color: var(--text-secondary);
  }
  .toolbar button:hover, .toolbar a:hover { background: rgba(200,164,92,0.08); }
  .toolbar button + button { margin-top: 2px; }
  .toolbar button:active { transform: scale(0.92); }
  .brand-nav.nav-expanded { z-index: 1001; }
  .brand-nav.nav-expanded ~ .toolbar { opacity: 0; pointer-events: none; transform: translateX(-8px); }
  .card-actions { gap: 8px; }
  .copy-btn, .share-btn { padding: 4px 10px; font-size: 0.7rem; }
  .brand-nav-inner { gap: 0.5rem; padding: 10px 0; }
  .brand-nav .brand-link { font-size: 0.92rem; }
  .nav-hamburger { display: flex; }
  .brand-nav nav {
    display: flex; flex-direction: column; gap: 0;
    width: 100%; order: 3;
    padding: 0; margin-top: 0;
    border-top: none;
    max-height: 0; overflow: hidden; opacity: 0;
    transition: max-height 0.35s ease, opacity 0.25s ease, padding 0.3s ease, margin 0.3s ease;
  }
  .brand-nav nav.nav-open {
    max-height: 300px; opacity: 1;
    padding: 8px 0 4px; margin-top: 8px;
    border-top: 1px solid var(--border);
  }
  .brand-nav nav a {
    font-size: 0.85rem; padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .brand-nav nav a:last-child { border-bottom: none; }
  .brand-nav nav a.active::after { display: none; }
  .brand-nav nav a.active { color: var(--accent); }
  .brand-identity { margin: 20px auto 0; padding: 16px 16px; }
  .brand-identity .brand-name { font-size: 0.9rem; }
  .brand-identity .brand-cornerstones { gap: 6px; }
  .brand-identity .cornerstone { font-size: 0.68rem; padding: 2px 10px; }
  .footer-site-nav { gap: 0.5rem; }
  .footer-site-nav a { font-size: 0.75rem; }
  .brand-divider { margin: 2rem 0; font-size: 0.85rem; }
  .search-bar input { padding: 10px 16px; font-size: 0.85rem; }
  .back-to-top {
    bottom: 24px; right: auto; left: 12px;
    width: 38px; height: 38px; border-radius: 12px;
    font-size: 16px;
    background: rgba(28,26,35,0.75);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    box-shadow: 0 4px 20px rgba(0,0,0,0.25);
  }
}
"""
    
    return css


def generate_json_api(categories: dict, config: dict) -> str:
    """生成 JSON API 输出，供程序化访问"""
    site_cfg = config.get("site", {})
    api_data = {
        "meta": {
            "title": site_cfg.get("title", "热点聚合"),
            "url": site_cfg.get("url", ""),
            "updated": NOW.strftime("%Y-%m-%d %H:%M:%S"),
            "total": sum(min(len(v), 30) for v in categories.values())
        },
        "categories": {}
    }
    for lvl_name, topics in categories.items():
        if lvl_name == "其他":
            continue
        api_data["categories"][lvl_name] = [
            {
                "title": t.get("title", ""),
                "url": t.get("url", ""),
                "score": t.get("score", 0),
                "source": t.get("source_name", ""),
                "summary": t.get("zh_summary", "") or t.get("summary", "")[:150],
                "perspective": t.get("perspective_comment", ""),
                "action": t.get("action_guidance", "")
            }
            for t in topics
        ]
    return json.dumps(api_data, ensure_ascii=False, indent=2)


def generate_sitemap(categories: dict, config: dict) -> str:
    """生成 sitemap.xml 用于 SEO"""
    site_cfg = config.get("site", {})
    base_url = site_cfg.get("url", "").rstrip("/")
    urls = [f"""  <url>
    <loc>{base_url}/hot-news.html</loc>
    <lastmod>{NOW.strftime("%Y-%m-%d")}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>"""]
    
    # 为每个热点条目生成 URL（可选，取决于是否需要深度索引）
    for lvl_name, topics in categories.items():
        for t in topics:
            item_url = t.get("url", "")
            if item_url and item_url.startswith("http"):
                urls.append(f"""  <url>
    <loc>{_escape_xml(item_url)}</loc>
    <lastmod>{NOW.strftime("%Y-%m-%d")}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.5</priority>
  </url>""")
    
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls[:100])}  <!-- 限制最多100条 -->
</urlset>"""


def generate_opml(config: dict) -> str:
    """生成 OPML 订阅列表，列出所有数据源 RSS 地址"""
    sources = config.get("sources", {})
    site_cfg = config.get("site", {})
    
    # RSS 源映射
    rss_sources = {
        "_36kr": ("36氪", "https://36kr.com/feed"),
        "jiqizhixin": ("机器之心", "https://www.jiqizhixin.com/rss"),
        "qbitai": ("量子位", "https://www.qbitai.com/feed"),
        "sspai": ("少数派", "https://sspai.com/feed"),
        "ithome": ("IT之家", "http://www.ithome.com/rss.xml"),
        "huxiu": ("虎嗅", "https://www.huxiu.com/rss/0.xml"),
        "tmtpost": ("钛媒体", "http://www.tmtpost.com/rss.xml"),
        "geekpark": ("极客公园", "http://feeds.geekpark.net/"),
        "ifanr": ("爱范儿", "https://www.ifanr.com/feed"),
        "pingwest": ("品玩", "https://www.pingwest.com/feed"),
        "cnbeta": ("cnBeta", "https://www.cnbeta.com/backend.php?mod=news"),
        "williamlong": ("月光博客", "https://feed.williamlong.info/"),
    }
    
    outlines = []
    for key, (name, url) in rss_sources.items():
        if sources.get(key, {}).get("enabled", False):
            outlines.append(f"""    <outline text="{name}" title="{name}" type="rss" xmlUrl="{url}" htmlUrl="{url.rsplit('/', 1)[0]}/"/>""")
    
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>{site_cfg.get('title', '热点聚合')} - 数据源订阅列表</title>
    <dateCreated>{NOW.strftime("%a, %d %b %Y %H:%M:%S +0800")}</dateCreated>
  
</head>
  <body>
{chr(10).join(outlines)}
  </body>
</opml>"""


def generate_rss_feed(categories: dict, config: dict) -> str:
    """生成 RSS 2.0 Feed XML"""
    site_cfg = config.get("site", {})
    title = site_cfg.get("title", "热点聚合")
    link = site_cfg.get("url", "")
    description = site_cfg.get("description", "")
    now_str = NOW.strftime("%a, %d %b %Y %H:%M:%S +0800")

    items_xml = []
    for lvl_name, topics in categories.items():
        if lvl_name == "其他":
            continue
        for t in topics:
            item_title = _escape_xml(t.get("title", ""))
            item_link = t.get("url", "")
            item_desc = _escape_xml(t.get("zh_summary", "") or t.get("summary", "")[:1000])
            item_category = _escape_xml(f"{t.get('level', {}).get('name', lvl_name)}")
            items_xml.append(f"""    <item>
      <title>{item_title}</title>
      <link>{item_link}</link>
      <description>{item_desc}</description>
      <category>{item_category}</category>
      <pubDate>{now_str}</pubDate>
    </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{_escape_xml(title)}</title>
    <link>{link}</link>
    <description>{_escape_xml(description)}</description>
    <language>zh-CN</language>
    <lastBuildDate>{now_str}</lastBuildDate>
    <atom:link href="{link}/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(items_xml)}
  </channel>
</rss>"""
    return rss


def _escape_xml(s: str) -> str:
    """转义 XML 特殊字符"""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))



def _get_heat_stage(topic, prev_map):
    """判断热点的热度成长阶段"""
    import hashlib as _hl
    score = topic.get("score", 0)
    topic_id = topic.get("topic_id", _hl.sha256(topic.get("title", "").strip().lower().encode()).hexdigest()[:12])
    cross = topic.get("cross_source_count", 1)
    if topic_id not in prev_map:
        if score >= 70: return "爆发", "🔥", "首次出现即为高分，可能是突发热点"
        elif score >= 40: return "萌芽", "🌱", "首次出现，值得关注"
        else: return "萌芽", "🌱", "新热点，持续观察"
    prev = prev_map[topic_id]
    prev_score = prev.get("score", 0)
    diff = score - prev_score
    if cross >= 3 and diff >= 20: return "爆发", "💥", f"多源共振+大幅上升(+{diff:.0f})"
    elif diff >= 30: return "爆发", "🔥", f"热度急剧上升(+{diff:.0f}分)"
    elif diff >= 10: return "上升", "📈", f"热度上升中(+{diff:.0f}分)"
    elif abs(diff) < 5 and score >= 70: return "热门", "⭐", "持续热门，稳定高分"
    elif abs(diff) < 5: return "持续", "➡️", "热度稳定"
    elif diff <= -10: return "降温", "📉", f"热度下降({diff:.0f}分)"
    else: return "持续", "➡️", "热度小幅波动"

def _load_prev_snapshot(data_dir="data"):
    """加载上一次快照"""
    import json as _json
    latest = Path(data_dir) / "latest.json"
    if not latest.exists(): return {}
    try:
        with open(latest, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return {t.get("topic_id", ""): t for t in data.get("topics", []) if t.get("topic_id")}
    except Exception: return {}

def generate_html(categories: dict, config: dict, mode: str = "web") -> str:
    """生成 HTML 页面"""
    prev_topics_map = _load_prev_snapshot("data")
    site = config.get("site", {})
    misc = config.get("misc", {})
    levels = config.get("scoring", {}).get("levels", [])
    
    # 构建启用的数据源列表（用于设置弹窗展示）
    _source_map = {
        "hackernews": "Hacker News", "github": "GitHub", "producthunt": "Product Hunt",
        "_36kr": "36氪", "v2ex": "V2EX", "weibo": "微博", "aihot": "AI热点",
        "jiqizhixin": "机器之心", "qbitai": "量子位", "sspai": "少数派",
        "ithome": "IT之家", "huxiu": "虎嗅", "tmtpost": "钛媒体", "geekpark": "极客公园",
        "ifanr": "爱范儿", "pingwest": "品玩", "cnbeta": "cnBeta", "williamlong": "月光博客",
        "techcrunch": "TechCrunch", "slashdot": "Slashdot", "nikkei": "日经亚洲",
        "zdnet": "ZDNet", "pengpai": "澎湃新闻", "zhihu": "知乎热榜",
        "infzm": "南方周末", "zaobao": "联合早报", "bbc": "BBC中文",
        "arstechnica": "Ars Technica", "theverge": "The Verge", "npr": "NPR新闻",
    }
    _sources_cfg = config.get("sources", {})
    enabled_source_names = [name for key, name in _source_map.items() if _sources_cfg.get(key, {}).get("enabled", key in ("hackernews", "github", "producthunt"))]
    _source_urls = [
        url for key, url in {
            "hackernews": "https://news.ycombinator.com", "github": "https://api.github.com",
            "producthunt": "https://producthunt.com", "_36kr": "https://36kr.com/feed",
            "v2ex": "https://v2ex.com", "weibo": "https://weibo.com", "aihot": "https://aihot.virxact.com",
            "jiqizhixin": "https://www.jiqizhixin.com/rss", "qbitai": "https://www.qbitai.com/feed",
            "sspai": "https://sspai.com/feed", "ithome": "https://www.ithome.com/rss.xml",
            "huxiu": "https://www.huxiu.com/rss/0.xml", "tmtpost": "https://www.tmtpost.com/rss.xml",
            "geekpark": "https://feeds.geekpark.net/", "ifanr": "https://www.ifanr.com/feed",
            "pingwest": "https://www.pingwest.com/feed", "cnbeta": "https://www.cnbeta.com/backend.php",
            "williamlong": "https://feed.williamlong.info/", "techcrunch": "https://techcrunch.com/feed/",
            "slashdot": "https://rss.slashdot.org/Slashdot/slashdotMain", "nikkei": "https://asia.nikkei.com/rss/feed",
            "zdnet": "https://www.zdnet.com/rss.xml", "pengpai": "https://www.thepaper.cn/rss/newsDetail.xml",
            "zhihu": "https://rsshub.app/zhihu/hotlist", "infzm": "https://rsshub.app/infzm/2",
            "zaobao": "https://rsshub.app/zaobao/realtime/china", "bbc": "https://rsshub.app/bbc/chinese",
            "arstechnica": "https://feeds.arstechnica.com/arstechnica/features",
            "theverge": "https://www.theverge.com/rss/index.xml", "npr": "https://feeds.npr.org/1001/rss.xml",
        }.items() if _sources_cfg.get(key, {}).get("enabled", key in ("hackernews", "github", "producthunt"))]
    
    # CSS
    css = generate_css(config)
    
    # 档位标签
    sorted_levels = sorted(levels, key=lambda x: x["threshold"], reverse=True)
    tabs_html = '<div class="level-tab active" onclick="showLevel(\'all\')">全部</div>\n'

    for lvl in sorted_levels:
        color = lvl.get("color", "#c8a45c")
        desc = lvl.get("description", "")
        tabs_html += f'<div class="level-tab" style="background: {color}22; color: {color}; border-color: {color};" onclick="showLevel(\'{lvl["name"]}\')">{lvl["name"]}<span style="display:block;font-size:0.7rem;opacity:0.7;margin-top:2px;">{desc}</span></div>\n'
    tabs_html += '<div class="level-tab bookmark-tab" onclick="showLevel(\'bookmarks\')" style="border-color:#d4a85c;">\u2b50 收藏</div>\n'
    tabs_html += '<button onclick="exportBookmarks()" style="background:none;border:1px solid #d4a85c;color:#d4a85c;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:0.75rem;margin-left:4px;white-space:nowrap;transition:all 0.2s;" title="将收藏内容导出为文本文件" onmouseover="this.style.background=\'rgba(212,168,92,0.1)\'" onmouseout="this.style.background=\'none\'">📥 导出收藏</button>\n'
    tabs_html += '<span style="margin:0 6px;color:var(--text2);font-size:0.75rem;">|</span>'
    tabs_html += '<button id="sortToggle" onclick="toggleSort()" style="background:rgba(232,112,90,0.15);border:1px solid #e8705a;color:#e8705a;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:0.75rem;white-space:nowrap;transition:all 0.2s;" title="点击切换排序方向">🔥 高→低</button>\n'
    
    # 时间戳用于 NEW 角标判断
    gen_timestamp = NOW.strftime("%Y-%m-%dT%H:%M:%S")
    
    # 全部内容的列表（用于"全部"标签）
    all_cards = ""
    all_items = []
    
    # 内容列表
    content_html = ""

    def _clean_summary(text):
        """清洗摘要中的推广内容"""
        if not text:
            return text
        # 移除微信公众号推广
        patterns = [
            r'#?欢迎关注[^#]+官方微信公众号：[^#]+#?',
            r'#?欢迎关注[^#]+微信公众号[^#]*#?',
            r'更多精彩内容第一时间为您奉上[。.]?',
            r'\[&#8230;\]',
        ]
        for p in patterns:
            text = re.sub(p, '', text, flags=re.IGNORECASE)
        return text.strip()

    def _normalize_keys(t):
        """兼容 api.json 中的键名"""
        if not t.get("perspective_comment") and t.get("perspective"):
            t["perspective_comment"] = t["perspective"]
        if not t.get("action_guidance") and t.get("action"):
            t["action_guidance"] = t["action"]
        if not t.get("zh_summary") and t.get("summary"):
            t["zh_summary"] = t["summary"]
        if not t.get("source_name") and t.get("source"):
            t["source_name"] = t["source"]

    def _build_ai_insights(t):
        """构建AI内容区块，只有当至少有一个有效内容时才创建容器"""
        _normalize_keys(t)
        zh = t.get("zh_summary", "")
        persp = t.get("perspective_comment", "")
        action = t.get("action_guidance", "")
        if persp and persp.startswith("<!--AI_"):
            persp = ""
        if action and action.startswith("<!--AI_"):
            action = ""
        if not zh and not persp and not action:
            return ""
        parts = []
        # 内容小结已移除，使用卡片顶部的摘要替代
        if persp:
            parts.append('<div class="ai-insight-item perspective-comment card-ai-persp"><span class="ai-label">视角解读</span><p>' + persp + '</p></div>')
        if action:
            parts.append('<div class="ai-insight-item action-guidance card-ai-action"><span class="ai-label">行动引导</span><p>' + action + '</p></div>')
        # 难度标签（大众版显示）
        score = t.get("score", 0)
        if score >= 80: diff, diff_label = "\U0001f680", "进阶"
        elif score >= 50: diff, diff_label = "\U0001f527", "实用"
        else: diff, diff_label = "\U0001f331", "入门"
        parts.append('<span class="card-difficulty" title="难度: ' + diff_label + '">' + diff + ' ' + diff_label + '</span>')
        return '<div class="ai-insights">\n    ' + '\n    '.join(parts) + '\n  </div>' 

    # 限制每个等级最多30条，总卡片不超过60条
    MAX_CARDS_PER_LEVEL = 30
    MAX_TOTAL_CARDS = 60
    TARGET_PER_VERSION = 30  # 每个版本目标30条
    total_card_count = 0
    
    # 第一步：收集所有卡片用于平衡
    all_cards_for_balance = []
    for lvl in sorted_levels:
        items = categories.get(lvl["name"], [])[:MAX_CARDS_PER_LEVEL]
        all_cards_for_balance.extend(items)
    
    # 第二步：平衡受众分布（大众版和专业版各30条）
    general_cards = [t for t in all_cards_for_balance if t.get('audience') == 'general']
    pro_cards = [t for t in all_cards_for_balance if t.get('audience') == 'pro']
    
    # 如果总数超过60，裁剪低分卡片
    if len(all_cards_for_balance) > MAX_TOTAL_CARDS:
        all_cards_for_balance.sort(key=lambda x: x['score'], reverse=True)
        all_cards_for_balance = all_cards_for_balance[:MAX_TOTAL_CARDS]
        general_cards = [t for t in all_cards_for_balance if t.get('audience') == 'general']
        pro_cards = [t for t in all_cards_for_balance if t.get('audience') == 'pro']
    
    # 平衡转移
    gen_count = len(general_cards)
    pro_count = len(pro_cards)
    
    if gen_count > TARGET_PER_VERSION and pro_count < TARGET_PER_VERSION:
        transfer = min(gen_count - TARGET_PER_VERSION, TARGET_PER_VERSION - pro_count)
        for i in range(transfer):
            general_cards[-(i+1)]['audience'] = 'pro'
    elif pro_count > TARGET_PER_VERSION and gen_count < TARGET_PER_VERSION:
        transfer = min(pro_count - TARGET_PER_VERSION, TARGET_PER_VERSION - gen_count)
        for i in range(transfer):
            pro_cards[-(i+1)]['audience'] = 'general'
    
    # 第三步：按等级分组生成卡片
    balanced_cards_by_level = {lvl['name']: [] for lvl in sorted_levels}
    for t in all_cards_for_balance:
        lvl_name = t.get('level', {}).get('name', '参考')
        if len(balanced_cards_by_level.get(lvl_name, [])) < MAX_CARDS_PER_LEVEL:
            balanced_cards_by_level[lvl_name].append(t)
    
    for lvl in sorted_levels:
        remaining = MAX_TOTAL_CARDS - total_card_count
        if remaining <= 0:
            break
        items = balanced_cards_by_level.get(lvl["name"], [])[:min(MAX_CARDS_PER_LEVEL, remaining)]
        all_items.extend(items)
        
        cards = ""
        for t in sorted(items, key=lambda x: x["score"], reverse=True):
            _normalize_keys(t)
            level_info = t["level"]
            color = level_info["color"]
            card_hash = hashlib.md5((t["title"] + t.get("url", "")).encode()).hexdigest()[:20]
            # 预计算属性值，避免 Python 3.11 下 f-string 解析报错
            bk_esc_title = _escape_attr(t['title'])
            bk_esc_url = _escape_attr(t.get('url', ''))
            bk_esc_summary = _escape_attr(t.get('summary_original', '')[:100])
            bk_esc_zhsummary = _escape_attr(t.get('zh_summary', '')[:100])
            bk_esc_perspective = _escape_attr(t.get('perspective_comment', '')[:120])
            bk_esc_action = _escape_attr(t.get('action_guidance', '')[:120])
            bk_score = t["score"]
            bk_source = t.get("source_name", "")
            bk_grade = level_info["name"]
            
            # 标题：web模式可点击跳转，local模式纯文本
            if mode == "web":
                title_html = f'<h2><a href="{t["url"]}" target="_blank" rel="noopener">{t["title"]}</a></h2>'
            else:
                title_html = f'<h2>{t["title"]}</h2>'
            
            # 过滤模板卡片
            if t.get("source_name") and t["title"].strip() == t["source_name"].strip() and not t.get("summary_original"):
                continue

            # 热度阶段判断
            heat_stage, heat_stage_icon, heat_stage_desc = _get_heat_stage(t, prev_topics_map)
            value_tags_html = ""
            if t.get("value_tags"):
                tag_spans = "\n    ".join([f'<span class="value-tag" data-tag="{tag}">{tag}</span>' for tag in t.get("value_tags", ["综合"])])
                value_tags_html = f'\n  <div class="value-tags">\n    {tag_spans}\n  </div>'
            card = f'''
<div class="topic-card" data-hash="{card_hash}" data-updated="{gen_timestamp}" data-grade="{lvl["name"]}" data-source="{t.get("source_name", "")}" data-heat-stage="{heat_stage}" data-audience="{t.get('audience', 'both')}" data-value-tags="{",".join(t.get("value_tags", ["综合"]))}">
  <span class="new-badge" style="display:none;">NEW</span>
  <div class="card-header">
    <span class="score-badge" style="background: {color};">{t["score"]}分 · {level_info["name"]}</span>
    <span class="heat-stage-badge" data-stage="{heat_stage}" title="{heat_stage_desc}">{heat_stage_icon} {heat_stage}</span>
    <span class="source-tag">{t["source_name"]}</span>
    <button class="bookmark-btn" onclick="toggleBookmark(this)" data-hash="{card_hash}" data-title="{bk_esc_title}" data-url="{bk_esc_url}" data-score="{bk_score}" data-grade="{bk_grade}" data-source="{bk_source}" data-summary="{_clean_summary(bk_esc_summary)}" data-zhsummary="{bk_esc_zhsummary}" title="收藏">☆</button>
  </div>
  {value_tags_html}
  {title_html}
  {f'<p class="summary">{_clean_summary(t.get("zh_summary") or t.get("summary_original", ""))[:200]}</p>' if (t.get("zh_summary") or t.get("summary_original")) else ''}
  <div class="meta">
    <span>{t["source_name"]}</span>
    {f'<span>🔥 {t["hot_score"]}</span>' if t.get("hot_score") else ''}
  </div>
  {f'<div class="card-actions"><button class="copy-btn" onclick="copyCardLink(this)" data-url="{bk_esc_url}" data-title="{bk_esc_title}">📋 复制链接</button></div>' if mode == "web" else ''}
  {_build_ai_insights(t)}
</div>
'''
            cards += card
            total_card_count += 1
        
        if not cards:
            cards = f'<div style="text-align:center;padding:60px;color:var(--text-secondary);"><p>暂无{lvl["name"]}内容</p></div>'
        
        content_html += f'<div id="level-{lvl["name"]}" class="topic-list">{cards}</div>\n'

    
    # JavaScript - 五个功能
    _feishu_webhook = config.get('misc', {}).get('feishu_webhook', '')
    js = f"""
<script>
// ===== 全局状态 =====
const GEN_TS = '{gen_timestamp}';
let currentLevel = 'all';
let currentValueTag = 'all';
let currentSearch = '';
let bookmarks = JSON.parse(localStorage.getItem('hn_bookmarks') || '[]');
let lastVisit = localStorage.getItem('hn_last_visit') || '';
let theme = localStorage.getItem('hn_theme') || '{mode}';

// ===== 主题切换 =====
function applyTheme(t) {{
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeToggle').textContent = t === 'dark' ? '\u2600\ufe0f' : '\U0001f319';
  localStorage.setItem('hn_theme', t);
}}

// ===== 热度排序（单按钮切换） =====
let heatSortDir = 'desc'; // desc=高到低, asc=低到高
function toggleSort() {{
  heatSortDir = heatSortDir === 'desc' ? 'asc' : 'desc';
  const btn = document.getElementById('sortToggle');
  if (heatSortDir === 'desc') {{
    btn.textContent = '🔥 高→低';
    btn.style.background = 'rgba(232,112,90,0.15)';
    btn.style.borderColor = '#e8705a';
    btn.style.color = '#e8705a';
  }} else {{
    btn.textContent = '❄️ 低→高';
    btn.style.background = 'rgba(100,180,220,0.15)';
    btn.style.borderColor = '#64b4dc';
    btn.style.color = '#64b4dc';
  }}
  // 获取当前可见卡片并排序
  const cards = Array.from(document.querySelectorAll('.topic-card'));
  const visible = cards.filter(c => c.style.display !== 'none');
  visible.sort((a, b) => {{
    const sa = parseFloat(a.querySelector('.score-badge')?.textContent) || 0;
    const sb = parseFloat(b.querySelector('.score-badge')?.textContent) || 0;
    return heatSortDir === 'desc' ? sb - sa : sa - sb;
  }});
  const parent = visible[0]?.parentElement;
  if (parent) {{
    visible.forEach(c => parent.appendChild(c));
  }}
}}
function showLevel(level) {{
  currentLevel = level;
  currentSearch = document.getElementById('searchInput').value;
  document.querySelectorAll('.level-tab').forEach(t => t.classList.remove('active'));
  const tabEl = document.querySelector('.level-tab[onclick*="' + level + '"]');
  if (tabEl) tabEl.classList.add('active');
  filterCards();
}}

// ===== 价值标签筛选 =====
function filterByValueTag(tag) {{
  currentValueTag = tag;
  document.querySelectorAll('.value-filter-btn').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.tag === tag);
  }});
  filterCards();
}}

// ===== 搜索 + 筛选 =====
function filterCards() {{
  const searchText = document.getElementById('searchInput').value.toLowerCase().trim();
  currentSearch = searchText;
  
  // 更新档位容器的可见性
  document.querySelectorAll('.topic-list').forEach(l => l.classList.remove('active'));
  
  let visibleCount = 0;
  document.querySelectorAll('.topic-card').forEach(card => {{
    const grade = card.dataset.grade || '';
    const hash = card.dataset.hash || '';
    const title = (card.querySelector('h2')?.textContent || '').toLowerCase();
    const summary = (card.querySelector('.summary')?.textContent || '').toLowerCase();
    // zh-summary已移除
    const allText = title + ' ' + summary + ' ' + zh;
    
    // 搜索过滤
    if (searchText && !allText.includes(searchText)) {{
      card.style.display = 'none';
      return;
    }}
    
    // 数据源过滤（自定义设置）
    const hiddenSources = userSettings.hiddenSources || [];
    const cardSource = card.dataset.source || '';
    if (hiddenSources.length > 0 && hiddenSources.includes(cardSource)) {{
      card.style.display = 'none';
      return;
    }}
    
    // 受众模式过滤（与 applyMode 协同，防止互相覆盖）
    const audience = card.dataset.audience || 'both';
    if (currentMode === '\u5927\u4f17\u7248' && audience === 'pro') {{
      card.style.display = 'none';
      return;
    }}
    if (currentMode === '\u4e13\u4e1a\u7248' && audience === 'general') {{
      card.style.display = 'none';
      return;
    }}
    
    // 价值标签筛选
    if (currentValueTag !== 'all') {{
      const cardTags = (card.dataset.valueTags || '').split(',');
      if (!cardTags.includes(currentValueTag)) {{
        card.style.display = 'none';
        return;
      }}
    }}
    
    // 档位/收藏过滤
    if (currentLevel === 'all') {{
      card.style.display = '';
      visibleCount++;
    }} else if (currentLevel === 'bookmarks') {{
      if (bookmarks.includes(hash)) {{
        card.style.display = '';
        visibleCount++;
      }} else {{
        card.style.display = 'none';
      }}
    }} else {{
      if (grade === currentLevel) {{
        card.style.display = '';
        visibleCount++;
      }} else {{
        card.style.display = 'none';
      }}
    }}
  }});
  
  // 显示空状态
  let emptyEl = document.getElementById('emptyState');
  if (visibleCount === 0) {{
    if (!emptyEl) {{
      emptyEl = document.createElement('div');
      emptyEl.id = 'emptyState';
      emptyEl.className = 'empty-state';
      emptyEl.innerHTML = currentLevel === 'bookmarks' 
        ? '<div class="icon">\u2b50</div><p>还没有收藏内容</p><p style="font-size:0.85rem;margin-top:8px;">点击卡片上的 \u2606 收藏你感兴趣的内容</p>'
        : '<div class="icon">\U0001f50d</div><p>没有匹配的内容</p>';
      document.querySelector('main').appendChild(emptyEl);
    }}
    emptyEl.style.display = '';
  }} else if (emptyEl) {{
    emptyEl.style.display = 'none';
  }}
  // 应用卡片数量限制（MAX_CARDS=30）
  const _MAX = 30;
  const _vis = Array.from(document.querySelectorAll('.topic-card')).filter(c => c.style.display !== 'none');
  if (_vis.length > _MAX) {{ _vis.slice(_MAX).forEach(c => c.style.display = 'none'); }}
}}

// ===== 收藏功能 =====
function toggleBookmark(btn) {{
  const hash = btn.dataset.hash;
  const idx = bookmarks.indexOf(hash);
  if (idx >= 0) {{
    bookmarks.splice(idx, 1);
    btn.textContent = '\u2606';
    btn.classList.remove('bookmarked');
  }} else {{
    bookmarks.push(hash);
    btn.textContent = '\u2b50';
    btn.classList.add('bookmarked');
  }}
  localStorage.setItem('hn_bookmarks', JSON.stringify(bookmarks));
  if (currentLevel === 'bookmarks') filterCards();
}}

function restoreBookmarks() {{
  document.querySelectorAll('.bookmark-btn').forEach(btn => {{
    if (bookmarks.includes(btn.dataset.hash)) {{
      btn.textContent = '\u2b50';
      btn.classList.add('bookmarked');
    }}
  }});
}}

// ===== 导出收藏 =====
function exportBookmarks() {{
    const bookmarks = JSON.parse(localStorage.getItem('hn_bookmarks') || '[]');
    if (bookmarks.length === 0) {{ alert('暂无收藏'); return; }}
    const text = bookmarks.map(hash => {{
        const card = document.querySelector(`[data-hash="${hash}"]`);
        if (!card) return null;
        const title = card.querySelector('.card-title')?.textContent || '';
        const url = card.querySelector('a[href^="http"]')?.href || '';
        return title + ' ' + url;
    }}).filter(Boolean).join('\\n');
    const blob = new Blob([text], {{ type: 'text/plain;charset=utf-8' }});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'bookmarks.txt';
    a.click();
}}

// ===== 复制链接 =====
function copyCardLink(btn) {{
  const url = btn.dataset.url;
  const title = btn.dataset.title;
  const text = title + '\\n' + url;
  navigator.clipboard.writeText(text).then(() => {{
    btn.textContent = '\u2705 已复制';
    btn.classList.add('copied');
    setTimeout(() => {{
      btn.textContent = '\U0001f4cb 复制链接';
      btn.classList.remove('copied');
    }}, 2000);
  }}).catch(() => {{
    // 回退方案
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    btn.textContent = '\u2705 已复制';
    btn.classList.add('copied');
    setTimeout(() => {{
      btn.textContent = '\U0001f4cb 复制链接';
      btn.classList.remove('copied');
    }}, 2000);
  }});
}}

// ===== NEW 角标 =====
function checkNewContent() {{
  if (!lastVisit) {{
    // 首次访问不显示 NEW
    localStorage.setItem('hn_last_visit', new Date().toISOString());
    return;
  }}
  const lastTime = new Date(lastVisit).getTime();
  document.querySelectorAll('.topic-card').forEach(card => {{
    const updated = new Date(card.dataset.updated).getTime();
    if (updated > lastTime) {{
      const badge = card.querySelector('.new-badge');
      if (badge) badge.style.display = '';
    }}
  }});
  localStorage.setItem('hn_last_visit', new Date().toISOString());
}}

// ===== 设置弹窗 =====
function openSettings(tab) {{
  document.getElementById('settingsModal').classList.add('active');
  if (tab) switchTab(tab);
  document.body.style.overflow = 'hidden';
}}
function closeSettings() {{
  document.getElementById('settingsModal').classList.remove('active');
  document.body.style.overflow = '';
}}
function switchTab(tabName) {{
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.modal-panel').forEach(p => p.classList.remove('active'));
  document.querySelector('.modal-tab[data-tab="' + tabName + '"]').classList.add('active');
  document.getElementById('panel-' + tabName).classList.add('active');
}}

// ===== 自定义设置管理 =====
let userSettings = JSON.parse(localStorage.getItem('hn_settings') || '{{}}');

function toggleSetting(el, key) {{
  el.classList.toggle('active');
  userSettings[key] = el.classList.contains('active');
  saveSettings();
  applySettings();
}}

function toggleSourceFilter(el) {{
  el.classList.toggle('disabled');
  const sw = el.querySelector('.toggle-switch');
  sw.classList.toggle('active');
  if (!userSettings.hiddenSources) userSettings.hiddenSources = [];
  const src = el.dataset.source;
  if (el.classList.contains('disabled')) {{
    if (!userSettings.hiddenSources.includes(src)) userSettings.hiddenSources.push(src);
  }} else {{
    userSettings.hiddenSources = userSettings.hiddenSources.filter(s => s !== src);
  }}
  saveSettings();
  filterCards();
}}

function setAllSources(enable) {{
  document.querySelectorAll('.source-toggle-item').forEach(item => {{
    const sw = item.querySelector('.toggle-switch');
    if (enable) {{
      item.classList.remove('disabled');
      sw.classList.add('active');
    }} else {{
      item.classList.add('disabled');
      sw.classList.remove('active');
    }}
  }});
  userSettings.hiddenSources = enable ? [] : Array.from(document.querySelectorAll('.source-toggle-item')).map(i => i.dataset.source);
  saveSettings();
  filterCards();
}}

function saveSettings() {{
  // 保存下拉框设置
  const dv = document.getElementById('setDefaultView');
  if (dv) userSettings.defaultView = dv.value;
  const ps = document.getElementById('setPageSize');
  if (ps) userSettings.pageSize = ps.value;
  localStorage.setItem('hn_settings', JSON.stringify(userSettings));
}}

function applySettings() {{
  // 显示/隐藏摘要
  document.querySelectorAll('.summary').forEach(el => {{
    el.style.display = userSettings.showSummary !== false ? '' : 'none';
  }});
  // 显示/隐藏视角评论
  document.querySelectorAll('.perspective-comment, .action-guidance').forEach(el => {{
    el.style.display = userSettings.showPerspective !== false ? '' : 'none';
  }});
  // 显示/隐藏热度
  document.querySelectorAll('.meta').forEach(el => {{
    el.style.display = userSettings.showMeta !== false ? '' : 'none';
  }});
  // 紧凑模式
  if (userSettings.compactMode) {{
    document.querySelectorAll('.topic-card').forEach(el => {{
      el.style.padding = '16px 18px';
      el.style.marginBottom = '8px';
    }});
  }} else {{
    document.querySelectorAll('.topic-card').forEach(el => {{
      el.style.padding = '';
      el.style.marginBottom = '';
    }});
  }}
  // 每页数量限制
  const pageSize = parseInt(userSettings.pageSize) || 0;
  let count = 0;
  document.querySelectorAll('.topic-card').forEach(card => {{
    if (pageSize > 0 && count >= pageSize && card.style.display !== 'none') {{
      card.style.display = 'none';
    }}
    if (card.style.display !== 'none') count++;
  }});
}}

function restoreSettings() {{
  // 恢复开关状态
  ['showSummary', 'showPerspective', 'showMeta', 'compactMode'].forEach(key => {{
    const map = {{ 'showSummary': 'toggleSummary', 'showPerspective': 'togglePerspective', 'showMeta': 'toggleMeta', 'compactMode': 'toggleCompact' }};
    const el = document.getElementById(map[key]);
    if (el) {{
      const val = userSettings[key] !== undefined ? userSettings[key] : key !== 'compactMode';
      if (val) el.classList.add('active'); else el.classList.remove('active');
    }}
  }});
  // 恢复下拉框
  const dv = document.getElementById('setDefaultView');
  if (dv && userSettings.defaultView) dv.value = userSettings.defaultView;
  const ps = document.getElementById('setPageSize');
  if (ps && userSettings.pageSize) ps.value = userSettings.pageSize;
  // 恢复数据源过滤
  const hidden = userSettings.hiddenSources || [];
  document.querySelectorAll('.source-toggle-item').forEach(item => {{
    if (hidden.includes(item.dataset.source)) {{
      item.classList.add('disabled');
      item.querySelector('.toggle-switch').classList.remove('active');
    }}
  }});
  // 绑定下拉框事件
  if (dv) dv.addEventListener('change', () => {{ userSettings.defaultView = dv.value; saveSettings(); }});
  if (ps) ps.addEventListener('change', () => {{ userSettings.pageSize = ps.value; saveSettings(); applySettings(); }});
  // 应用设置
  applySettings();
}}

function clearAllBookmarks() {{
  if (confirm('确定清除所有收藏？此操作不可撤销。')) {{
    bookmarks = [];
    localStorage.setItem('hn_bookmarks', JSON.stringify(bookmarks));
    document.querySelectorAll('.bookmark-btn').forEach(btn => {{
      btn.textContent = '\\u2606';
      btn.classList.remove('bookmarked');
    }});
    if (currentLevel === 'bookmarks') filterCards();
  }}
}}

function resetAllSettings() {{
  if (confirm('确定重置所有自定义设置？')) {{
    userSettings = {{}};
    localStorage.removeItem('hn_settings');
    restoreSettings();
    filterCards();
  }}
}}

function exportSettings() {{
  saveSettings();
  const blob = new Blob([JSON.stringify(userSettings, null, 2)], {{ type: 'application/json' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'hot-news-settings.json';
  a.click();
}}

function importSettings() {{
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json';
  input.onchange = (e) => {{
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {{
      try {{
        const imported = JSON.parse(ev.target.result);
        userSettings = {{ ...userSettings, ...imported }};
        localStorage.setItem('hn_settings', JSON.stringify(userSettings));
        restoreSettings();
        filterCards();
        alert('设置导入成功！');
      }} catch (err) {{
        alert('设置文件格式错误，请检查 JSON 格式。');
      }}
    }};
    reader.readAsText(file);
  }};
  input.click();
}}


// ===== 初始化 =====

// ===== 用户反馈弹窗 =====
function openFeedback() {{
  const modal = document.createElement('div');
  modal.id = 'feedbackModal';
  modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:10000;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
  modal.innerHTML = `
    <div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;max-width:480px;width:90%;max-height:80vh;overflow-y:auto;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h3 style="margin:0;font-size:1.1rem;color:var(--text-primary);">💬 意见与建议</h3>
        <button onclick="this.closest('#feedbackModal').remove()" style="background:none;border:none;color:var(--text-secondary);font-size:1.2rem;cursor:pointer;">✕</button>
      </div>
      <p style="font-size:0.85rem;color:var(--text-secondary);margin:0 0 16px 0;">感谢你的反馈！每一条建议都会被认真阅读。</p>
      <div style="margin-bottom:12px;">
        <label style="font-size:0.8rem;color:var(--text-secondary);display:block;margin-bottom:4px;">反馈类型</label>
        <select id="feedbackType" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text-primary);font-size:0.85rem;">
          <option value="suggestion">功能建议</option>
          <option value="bug">问题报告</option>
          <option value="content">内容反馈</option>
          <option value="other">其他</option>
        </select>
      </div>
      <div style="margin-bottom:12px;">
        <label style="font-size:0.8rem;color:var(--text-secondary);display:block;margin-bottom:4px;">详细描述</label>
        <textarea id="feedbackContent" rows="4" placeholder="请描述你的建议或遇到的问题..." style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text-primary);font-size:0.85rem;resize:vertical;font-family:inherit;"></textarea>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button onclick="this.closest('#feedbackModal').remove()" style="padding:8px 16px;border-radius:8px;border:1px solid var(--border);background:none;color:var(--text-secondary);cursor:pointer;font-size:0.85rem;">取消</button>
        <button onclick="submitFeedback()" style="padding:8px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:0.85rem;">提交反馈</button>
      </div>
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);">
        <p style="font-size:0.78rem;color:var(--text-secondary);margin:0 0 6px 0;">或通过以下方式联系：</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <span style="font-size:0.78rem;color:var(--accent);">📱 联系开发者阿扶 17343414886（V同号）</span>
        </div>
      </div>
    </div>
  `;
  modal.addEventListener('click', (e) => {{ if (e.target === modal) modal.remove(); }});
  document.body.appendChild(modal);
}}
function submitFeedback() {{
  const type = document.getElementById('feedbackType').value;
  const content = document.getElementById('feedbackContent').value.trim();
  if (!content) {{ alert('请填写反馈内容'); return; }}

  // 保存到 localStorage（本地备份）
  const feedbacks = JSON.parse(localStorage.getItem('hn_feedbacks') || '[]');
  const fb = {{ type, content, time: new Date().toISOString(), url: window.location.href }};
  feedbacks.push(fb);
  localStorage.setItem('hn_feedbacks', JSON.stringify(feedbacks));

  // 1. 飞书群机器人 Webhook 推送
  const FEISHU_WEBHOOK = '{_feishu_webhook}';
  if (FEISHU_WEBHOOK && FEISHU_WEBHOOK.startsWith('https://')) {{
    const typeLabels = {{ suggestion: '💡 功能建议', bug: '🐛 问题报告', content: '📝 内容反馈', other: '❓ 其他' }};
    fetch(FEISHU_WEBHOOK, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        msg_type: 'interactive',
        card: {{
          header: {{ title: {{ tag: 'plain_text', content: '📋 热点聚合网站 - 用户反馈' }}, template: 'blue' }},
          elements: [
            {{ tag: 'div', text: {{ tag: 'lark_md', content: '**类型：** ' + (typeLabels[type] || type) }} }},
            {{ tag: 'div', text: {{ tag: 'lark_md', content: '**内容：** ' + content }} }},
            {{ tag: 'div', text: {{ tag: 'lark_md', content: '**时间：** ' + new Date().toLocaleString() }} }},
            {{ tag: 'div', text: {{ tag: 'lark_md', content: '**页面：** ' + window.location.href }} }},
            {{ tag: 'action', actions: [
              {{ tag: 'button', text: {{ tag: 'plain_text', content: '查看 GitHub Issues' }}, url: 'https://github.com/Afuxh/fuqing-profile/issues', type: 'primary' }},
              {{ tag: 'button', text: {{ tag: 'plain_text', content: '前往论坛' }}, url: 'https://forum.trae.cn/t/topic/19830', type: 'default' }}
            ]}}
          ]
        }}
      }})
    }}).catch(() => {{}});
  }}

  // 2. GitHub Issues 跳转预填（新窗口）
  const titleMap = {{ suggestion: '[建议] ', bug: '[Bug] ', content: '[内容] ', other: '[其他] ' }};
  const issueTitle = encodeURIComponent((titleMap[type] || '') + content.substring(0, 50));
const issueBody = encodeURIComponent('## 反馈类型\\n' + type + '\\n\\n## 详细描述\\n' + content + '\\n\\n---\\n' + '时间: ' + new Date().toLocaleString() + '\\n' + '页面: ' + window.location.href + '\\n');
  window.open('https://github.com/Afuxh/fuqing-profile/issues/new?title=' + issueTitle + '&body=' + issueBody, '_blank');

  document.getElementById('feedbackModal').remove();
  alert('反馈已提交！感谢你的建议 🙏');
}}
document.addEventListener('DOMContentLoaded', () => {{
  // 主题
  applyTheme(theme);
  document.getElementById('themeToggle').addEventListener('click', () => {{
    theme = theme === 'dark' ? 'light' : 'dark';
    applyTheme(theme);
  }});

  // 设置弹窗
  document.getElementById('settingsBtn').addEventListener('click', () => openSettings('guide'));
  document.getElementById('modalClose').addEventListener('click', closeSettings);
  document.getElementById('settingsModal').addEventListener('click', (e) => {{
    if (e.target === e.currentTarget) closeSettings();
  }});
  document.querySelectorAll('.modal-tab').forEach(tab => {{
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  }});
  // ESC 关闭弹窗
  document.addEventListener('keydown', (e) => {{
    if (e.key === 'Escape') closeSettings();
  }});

  // 翻译按钮
  const tBtn = document.getElementById('translateBtn');
  if (tBtn) tBtn.href = 'https://translate.google.com/translate?sl=en&tl=zh-CN&u=' + encodeURIComponent(window.location.href);

  // 搜索实时过滤
  document.getElementById('searchInput').addEventListener('input', filterCards);

  // 恢复状态
  restoreBookmarks();
  restoreSettings();
  checkNewContent();


  // 显示全部内容（或用户设置的默认视图）
  const initView = userSettings.defaultView || 'all';
  showLevel(initView);

  // 首次访问引导
  if (!localStorage.getItem('hn_visited')) {{
    localStorage.setItem('hn_visited', '1');
  }}
}});
</script>
"""
    
    # 动态生成来源列表
    sources_cfg = config.get("sources", {})
    source_names = []
    if sources_cfg.get("hackernews", {}).get("enabled", True):
        source_names.append("Hacker News")
    if sources_cfg.get("github", {}).get("enabled", True):
        source_names.append("GitHub")
    if sources_cfg.get("producthunt", {}).get("enabled", True):
        source_names.append("Product Hunt")
    if sources_cfg.get("aihot", {}).get("enabled", False):
        source_names.append("AI热点")
    if sources_cfg.get("_36kr", {}).get("enabled", False):
        source_names.append("36氪")
    if sources_cfg.get("v2ex", {}).get("enabled", False):
        source_names.append("V2EX")
    source_line = " · ".join(source_names)
    
    # 完整 HTML
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{get_full_title()}</title>
<meta name="description" content="{site.get("description", "")}">
<style>
{css}
</style>



</head>
<body>
<!-- 扶清闲话 · 统一站点导航 -->
<div class="brand-nav">
  <div class="brand-nav-inner">
    <a href="https://afuxh.github.io/fuqing-profile/" class="brand-link">扶清闲话</a>
    <button class="nav-hamburger" id="navHamburger" aria-label="展开导航" onclick="toggleMobileNav()">
      <span></span><span></span><span></span>
    </button>
    <nav id="mainNav">
      <a href="https://afuxh.github.io/fuqing-profile/index.html">首页</a>
      <a href="https://afuxh.github.io/fuqing-profile/margins.html">读书笔记</a>
      <a href="https://afuxh.github.io/fuqing-profile/handbook.html">交流手册</a>
      <a href="https://afuxh.github.io/fuqing-profile/hot-news.html" class="active">AI热点</a>
      <a href="https://afuxh.github.io/fuqing-profile/archive.html">档案馆</a>
    </nav>
  </div>
</div>
<!-- 阅读进度条 -->
<div class="reading-progress" id="readingProgress"></div>
<!-- 回到顶部按钮 -->
<button class="back-to-top" id="backToTop" title="回到顶部">↑</button>
<div class="toolbar">
  <button class="theme-toggle-btn" id="themeToggle" title="切换主题">{"☀️" if config.get("theme", {}).get("mode", "dark") == "dark" else "🌙"}</button>
  <button class="settings-btn" id="settingsBtn" title="设置与帮助">⚙️</button>
</div>

<!-- 设置弹窗 -->
<div class="modal-overlay" id="settingsModal">
  <div class="modal-box">
    <div class="modal-header">
      <h2>⚙️ 设置与帮助</h2>
      <button class="modal-close" id="modalClose">&times;</button>
    </div>
    <div class="modal-tabs">
      <button class="modal-tab active" data-tab="guide">📖 功能指南</button>
      <button class="modal-tab" data-tab="sources">📡 数据源</button>
      <button class="modal-tab" data-tab="scoring">📊 评分规则</button>
      <button class="modal-tab" data-tab="outputs">🔗 输出格式</button>
      <button class="modal-tab" data-tab="customize">🛠️ 自定义</button>
    </div>
    <div class="modal-body">
      <!-- 功能指南 -->
      <div class="modal-panel active" id="panel-guide">
        <h3>🔍 搜索</h3>
        <p>在搜索框中输入关键词，实时过滤所有内容。搜索范围包括：<strong>标题、原文摘要、中文小结、视角评论</strong>。</p>
        <div class="tip">💡 提示：输入英文关键词也能匹配英文标题和摘要</div>

        <h3>📂 档位筛选</h3>
        <p>点击标签栏中的档位标签，按内容质量分级浏览：</p>
        <ul>
          <li><strong>全部</strong> — 显示所有内容</li>
          <li><strong>精选</strong> — 80分以上，高质量核心内容</li>
          <li><strong>推荐</strong> — 70分以上，值得阅读</li>
          <li><strong>参考</strong> — 60分以上，可作参考</li>
          <li><strong>⭐ 收藏</strong> — 仅显示你收藏的内容</li>
        </ul>

        <h3>⭐ 收藏</h3>
        <p>点击每张卡片右上角的 <kbd>☆</kbd> 按钮收藏内容。收藏数据保存在浏览器本地存储中，刷新页面不会丢失。</p>
        <div class="tip">⚠️ 注意：收藏存在浏览器中，清除浏览器数据或更换设备后会丢失。建议定期使用「📥 导出收藏」按钮导出备份。</div>

        <h3>📥 导出收藏</h3>
        <p>点击档位标签栏右侧的「导出收藏」按钮，将所有已收藏内容的标题和链接下载为 <code>bookmarks.txt</code> 文本文件。</p>

        <h3>🆕 NEW 角标</h3>
        <p>卡片左上角的红色 <strong>NEW</strong> 标签表示该内容是你上次访问后新增的。系统会自动记录你的访问时间，下次打开时对比内容更新时间来标注新内容。</p>

        <h3>📋 复制链接</h3>
        <p>点击卡片底部的「📋 复制链接」按钮，将<strong>标题 + 原始链接</strong>复制到剪贴板，方便分享给他人。</p>

        <h3>🌐 语言切换</h3>
        <p>点击右上角「🌐 EN/中」按钮切换中英文界面。你的语言偏好会被自动记住。</p>

        <h3>🌙 主题切换</h3>
        <p>点击右上角的 ☀️/🌙 按钮切换亮色/暗色模式。你的偏好会被自动记住。</p>
      </div>

      <!-- 数据源 -->
      <div class="modal-panel" id="panel-sources">
        <h3>📡 数据源（{len(enabled_source_names)} 个）</h3>
        <p>内容从以下 {len(enabled_source_names)} 个数据源自动采集，覆盖中英文科技、时事、财经、生活方式等领域。悬停可查看接口地址：</p>
        <div class="source-grid">
{chr(10).join(f'          <div class="source-item" title="{url}"><span class="dot"></span>{name}<span style="margin-left:auto;font-size:0.68rem;color:var(--text-muted);max-width:0;overflow:hidden;white-space:nowrap;transition:max-width 0.3s;">{url}</span></div>' for name, url in [(_n, _u) for _n, _u in zip(enabled_source_names, _source_urls[:len(enabled_source_names)])])}
        </div>
        <div class="tip">💡 数据源可在 <code>config.yaml</code> 中自由增减和配置，也支持通过 <code>--config</code> 参数加载预设配置（creator/team/lifestyle）。页面端可在「🛠️ 自定义」标签页中开关数据源的显示。</div>
      </div>

      <!-- 评分规则 -->
      <div class="modal-panel" id="panel-scoring">
        <h3>📊 评分机制</h3>
        <p>每条内容通过以下维度计算 0-100 分：</p>
        <ul>
          <li><strong>关键词匹配</strong>（最高 75 分）— 核心关键词每个 +15 分（上限 50），扩展关键词每个 +8 分（上限 25），排除关键词每个 -15 分</li>
          <li><strong>标题加分</strong>（额外 +8 分/个）— 核心关键词出现在标题中额外加分</li>
          <li><strong>热度分数</strong>（最高 35 分）— 基于原始平台的热度/点赞/评论数据</li>
          <li><strong>来源权重</strong>（5-12 分）— Hacker News +12、GitHub +10、Product Hunt +8、AI热点 +10、其他 +5</li>
        </ul>

        <h3>档位划分</h3>
{chr(10).join(f'        <div class="score-bar"><span class="label" style="color:{lvl["color"]}">{lvl["name"]}</span><div class="range"><div class="fill" style="width:{lvl["threshold"]}%;background:{lvl["color"]}"></div></div><span class="val">≥ {lvl["threshold"]}分</span></div>' for lvl in config.get("scoring", {}).get("levels", [{"name":"精选","threshold":80,"color":"#c8786a"},{"name":"推荐","threshold":70,"color":"#b8956a"},{"name":"参考","threshold":60,"color":"#8a9598"}]))}
        <p style="margin-top:8px;font-size:0.78rem;color:var(--text-muted);">低于最低阈值的内容归入「其他」，默认不显示</p>

        <h3>关键词列表</h3>
        <p><strong>核心关键词</strong>（高权重）：{', '.join(_flatten_keywords(config.get('scoring',{}).get('keywords',{}).get('core',[]))[:10])} 等</p>
        <p><strong>扩展关键词</strong>（中权重）：{', '.join(_flatten_keywords(config.get('scoring',{}).get('keywords',{}).get('extended',[]))[:10])} 等</p>
        <p><strong>排除关键词</strong>（降权）：{', '.join(_flatten_keywords(config.get('scoring',{}).get('keywords',{}).get('exclude',[])))}</p>
      </div>

      <!-- 输出格式 -->
      <div class="modal-panel" id="panel-outputs">
        <h3>🔗 可用输出格式</h3>
        <p>除网页外，本站还提供以下结构化输出，可供程序化访问和订阅：</p>
        <ul class="link-list">
          <li><span>📡 RSS 2.0 订阅源</span><a href="feed.xml" target="_blank">feed.xml →</a></li>
          <li><span>📊 JSON 数据接口</span><a href="api.json" target="_blank">api.json →</a></li>
          <li><span>🗺️ Sitemap 站点地图</span><a href="sitemap.xml" target="_blank">sitemap.xml →</a></li>
          <li><span>📋 OPML 数据源列表</span><a href="sources.opml" target="_blank">sources.opml →</a></li>
        </ul>

        <h3>使用方式</h3>
        <ul>
          <li><strong>RSS</strong>：将 <code>feed.xml</code> 的 URL 添加到你的 RSS 阅读器（如 Inoreader、Feedly），即可订阅更新</li>
          <li><strong>JSON API</strong>：可供脚本、自动化工具调用，返回结构化 JSON 数据</li>
          <li><strong>OPML</strong>：导入到 RSS 阅读器，一次性订阅所有 17 个数据源的原始 RSS</li>
          <li><strong>Sitemap</strong>：提交给搜索引擎，帮助索引</li>
        </ul>
        <div class="tip">💡 这些文件在每次生成时自动更新，无需手动维护</div>

        <h3>🌐 部署平台</h3>
        <p>本站支持多平台部署，解决不同网络环境下的访问问题：</p>
        <ul class="link-list">
          <li><span>🌍 GitHub Pages（国际）</span><a href="https://afuxh.github.io/fuqing-profile/hot-news.html" target="_blank">访问 →</a></li>
          <li><span>☁️ EdgeOne Pages（国内首选）</span><a href="https://fuqing-profile-fqh7n0sj.edgeone.cool/hot-news.html" target="_blank">访问 →</a></li>
        </ul>
        <div class="tip">💡 GitHub Pages 在国内访问较慢，推荐使用 EdgeOne Pages（国内可直访）。两个平台通过 GitHub Actions 自动同步部署。</div>
      </div>

      <!-- 自定义设置 -->
      <div class="modal-panel" id="panel-customize">
        <h3>🛠️ 自定义设置</h3>
        <p>以下设置保存在浏览器本地，仅影响当前设备的显示效果。修改后立即生效。</p>

        <div class="setting-group">
          <div class="setting-group-title">📊 显示选项</div>
          <div class="setting-row">
            <div class="setting-label">默认视图<small>每次打开页面时默认显示的档位</small></div>
            <select class="setting-select" id="setDefaultView">
              <option value="all">全部</option>
              <option value="精选">精选</option>
              <option value="推荐">推荐</option>
              <option value="参考">参考</option>
              <option value="bookmarks">收藏</option>
            </select>
          </div>
          <div class="setting-row">
            <div class="setting-label">每页显示数量<small>留空则显示全部</small></div>
            <select class="setting-select" id="setPageSize">
              <option value="">全部</option>
              <option value="10">10 条</option>
              <option value="20">20 条</option>
              <option value="50">50 条</option>
            </select>
          </div>
          <div class="setting-row">
            <div class="setting-label">显示原文摘要<small>关闭后只显示中文小结和视角评论</small></div>
            <div class="toggle-switch active" id="toggleSummary" onclick="toggleSetting(this, 'showSummary')"></div>
          </div>
          <div class="setting-row">
            <div class="setting-label">显示视角评论<small>关闭后隐藏 AI 视角评论和行动引导</small></div>
            <div class="toggle-switch active" id="togglePerspective" onclick="toggleSetting(this, 'showPerspective')"></div>
          </div>
          <div class="setting-row">
            <div class="setting-label">显示热度标签<small>关闭后隐藏来源和热度信息</small></div>
            <div class="toggle-switch active" id="toggleMeta" onclick="toggleSetting(this, 'showMeta')"></div>
          </div>
          <div class="setting-row">
            <div class="setting-label">紧凑模式<small>减小卡片间距和内边距，显示更多内容</small></div>
            <div class="toggle-switch" id="toggleCompact" onclick="toggleSetting(this, 'compactMode')"></div>
          </div>
        </div>

        <div class="setting-group">
          <div class="setting-group-title">📡 数据源过滤</div>
          <p style="font-size:0.82rem;color:var(--text-secondary);margin-bottom:8px;">关闭后，来自该数据源的内容将被隐藏。悬停可查看接口地址。</p>
          <div class="source-toggle-grid" id="sourceToggleGrid">
{chr(10).join(f'            <div class="source-toggle-item" data-source="{name}" onclick="toggleSourceFilter(this)"><div class="toggle-switch active"></div><span class="src-name">{name}</span><span class="src-url">{url}</span></div>' for name, url in [(_n, _u) for _n, _u in zip(enabled_source_names, _source_urls[:len(enabled_source_names)])])}
          </div>
          <div style="display:flex;gap:6px;margin-top:10px;">
            <button onclick="setAllSources(true)" style="background:var(--card);border:1px solid var(--border);color:var(--text-secondary);padding:4px 10px;border-radius:6px;cursor:pointer;font-size:0.75rem;">全部开启</button>
            <button onclick="setAllSources(false)" style="background:var(--card);border:1px solid var(--border);color:var(--text-secondary);padding:4px 10px;border-radius:6px;cursor:pointer;font-size:0.75rem;">全部关闭</button>
          </div>
        </div>

        <div class="setting-group">
          <div class="setting-group-title">🔄 数据管理</div>
          <div class="setting-row">
            <div class="setting-label">清除收藏<small>删除所有已收藏的内容</small></div>
            <button onclick="clearAllBookmarks()" style="background:none;border:1px solid #c8786a;color:#c8786a;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:0.75rem;">清除</button>
          </div>
          <div class="setting-row">
            <div class="setting-label">重置所有设置<small>恢复为默认配置</small></div>
            <button onclick="resetAllSettings()" style="background:none;border:1px solid #c8786a;color:#c8786a;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:0.75rem;">重置</button>
          </div>
        </div>

        <div class="setting-actions">
          <button onclick="exportSettings()">📤 导出设置</button>
          <button onclick="importSettings()">📥 导入设置</button>
        </div>
        <div class="tip">💡 设置导出为 JSON 文件，可在不同设备间迁移。导入时会覆盖当前设置。</div>
      </div>
    </div>
  </div>
</div>

<div class="container">
<header>
<div class="brand-signature">扶清闲话 · AI时代观察</div>
<h1>{site.get("title", "AI 时代观察")}</h1>
<p class="subtitle">{site.get("subtitle", "效率归机器，意义归人类")}</p>
<p class="philosophy-line">「让被豁免的，重新在场」</p>
<p class="update-time">更新时间：{NOW.strftime("%Y-%m-%d %H:%M")}{' · 共 ' + str(min(len(all_items), 60)) + ' 条内容' if all_items else ''}</p>
</header>

<!-- 四化方法论banner已移除 -->

<div class="search-bar">
  <input type="text" id="searchInput" placeholder="搜索标题、摘要...（支持关键词过滤）" autocomplete="off">
</div>
  <button id="mode-toggle" onclick="toggleMode()" style="position:fixed;top:12px;left:12px;z-index:100;padding:5px 14px;border-radius:16px;border:1px solid var(--border);background:var(--card);color:var(--text-primary);cursor:pointer;font-size:13px;transition:all 0.3s;white-space:nowrap;backdrop-filter:blur(8px);">大众版</button>

<div class="level-tabs">
{tabs_html}
</div>

<!-- 价值标签筛选 -->
<div class="value-filter-bar" id="valueFilterBar">
  <button class="value-filter-btn active" onclick="filterByValueTag('all')" data-tag="all">全部价值</button>
  <button class="value-filter-btn" onclick="filterByValueTag('AI变现赛道')" data-tag="AI变现赛道">💰 AI变现赛道</button>
  <button class="value-filter-btn" onclick="filterByValueTag('产业关联')" data-tag="产业关联">🏭 产业关联</button>
  <button class="value-filter-btn" onclick="filterByValueTag('技术前沿')" data-tag="技术前沿">🔬 技术前沿</button>
  <button class="value-filter-btn" onclick="filterByValueTag('产品工具')" data-tag="产品工具">🛠️ 产品工具</button>
  <button class="value-filter-btn" onclick="filterByValueTag('政策监管')" data-tag="政策监管">⚖️ 政策监管</button>
  <button class="value-filter-btn" onclick="filterByValueTag('创业投资')" data-tag="创业投资">🚀 创业投资</button>
</div>

<main>
{content_html}
<div style="text-align:center;padding:16px 0 32px;">
  <button id="expand-btn" onclick="toggleExpand()" style="background:none;border:1px solid var(--border);color:var(--text-secondary);padding:8px 24px;border-radius:8px;cursor:pointer;font-size:0.85rem;transition:all 0.2s;">▼ 查看更多</button>
</div>
</main>
<footer>
<p>数据来源：{source_line}（共 {len(enabled_source_names)} 个数据源）</p>
<p>内容由 AI 自动采集、智能评分。支持收藏、搜索、暗色模式、RSS 订阅</p>
<p class="footer-links">
  <a href="feed.xml" target="_blank">📡 RSS</a>
  <a href="api.json" target="_blank">📊 API</a>
  <a href="sources.opml" target="_blank">📋 OPML</a>
  <a href="sitemap.xml" target="_blank">🗺️ Sitemap</a>
  <a href="javascript:void(0)" onclick="openSettings('guide')">📖 帮助</a>
</p>

<div class="brand-divider">———— ✦ ————</div>

<!-- 品牌身份卡片 -->
<div class="brand-identity">
  <div class="brand-name">扶清闲话 · AI时代观察</div>
  <div class="brand-desc">以个人成长视角追踪时代脉搏，做 AI 内容的翻译者</div>
  <div class="brand-philosophy">「不掠夺，只滋养」</div>
  <div class="brand-cornerstones">
    <span class="cornerstone">复利</span>
    <span class="cornerstone">自动化</span>
    <span class="cornerstone">外化</span>
    <span class="cornerstone">专注</span>
  </div>
</div>

<!-- 用户反馈 / 联系方式 -->
<div id="feedback-section" style="margin-top:24px;padding:16px 24px;background:linear-gradient(135deg,rgba(200,168,92,0.06),rgba(200,120,106,0.06));border:1px solid rgba(200,168,92,0.12);border-radius:14px;text-align:center;">
  <p style="font-size:0.85rem;color:var(--text-secondary);margin:0 0 10px 0;">🛠️ 开发不易，如有意见/建议欢迎联系</p>
  <div style="display:flex;justify-content:center;gap:12px;flex-wrap:wrap;align-items:center;">
    <a href="javascript:void(0)" onclick="openFeedback()" style="display:inline-flex;align-items:center;gap:4px;padding:6px 16px;border-radius:8px;background:var(--card);border:1px solid var(--border);color:var(--text-primary);text-decoration:none;font-size:0.8rem;transition:all 0.2s;">💬 提交反馈</a>
    <span style="display:inline-flex;align-items:center;gap:4px;padding:6px 16px;border-radius:8px;background:var(--card);border:1px solid var(--accent);color:var(--accent);font-size:0.8rem;">📱 开发者阿扶 17343414886（v同）</span>
  </div>
</div>

<p style="margin-top:20px;font-size:0.78rem;">© {NOW.year} {site.get("title", "AI 时代观察")}</p>

<!-- 统一站点导航 -->
<div class="footer-site-nav">
  <a href="https://afuxh.github.io/fuqing-profile/index.html">首页</a>
  <a href="https://afuxh.github.io/fuqing-profile/margins.html">读书笔记</a>
  <a href="https://afuxh.github.io/fuqing-profile/handbook.html">交流手册</a>
  <a href="https://afuxh.github.io/fuqing-profile/hot-news.html">AI热点</a>
  <a href="https://afuxh.github.io/fuqing-profile/archive.html">档案馆</a>
</div>
</footer>
</div>

{js}
<script>
// ===== 移动端导航收起 =====
function toggleMobileNav() {{
  var btn = document.getElementById('navHamburger');
  var nav = document.getElementById('mainNav');
  var bar = nav.closest('.brand-nav');
  var isOpen = nav.classList.toggle('nav-open');
  btn.classList.toggle('open', isOpen);
  bar.classList.toggle('nav-expanded', isOpen);
  btn.setAttribute('aria-label', isOpen ? '收起导航' : '展开导航');
}}
// 点击导航链接后自动收起（移动端体验优化）
document.addEventListener('click', function(e) {{
  var nav = document.getElementById('mainNav');
  var btn = document.getElementById('navHamburger');
  if (!nav || !btn) return;
  var bar = nav.closest('.brand-nav');
  if (!nav.contains(e.target) && !btn.contains(e.target) && nav.classList.contains('nav-open')) {{
    nav.classList.remove('nav-open');
    btn.classList.remove('open');
    bar.classList.remove('nav-expanded');
    btn.setAttribute('aria-label', '展开导航');
  }}
}});

// 回到顶部按钮
const backToTopBtn = document.getElementById('backToTop');
const readingProgress = document.getElementById('readingProgress');
window.addEventListener('scroll', () => {{
  const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
  const scrollHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
  const scrollPercent = (scrollTop / scrollHeight) * 100;
  readingProgress.style.width = scrollPercent + '%';
  if (scrollTop > 300) {{ backToTopBtn.classList.add('visible'); }}
  else {{ backToTopBtn.classList.remove('visible'); }}
}});
backToTopBtn.addEventListener('click', () => {{ window.scrollTo({{ top: 0, behavior: 'smooth' }}); }});
</script>
<script>
// 双模式切换：大众版（易懂） / 专业版（技术）
let currentMode = localStorage.getItem('hot-news-mode') || '\u5927\u4f17\u7248';
function toggleMode() {{
  currentMode = currentMode === '\u5927\u4f17\u7248' ? '\u4e13\u4e1a\u7248' : '\u5927\u4f17\u7248';
  localStorage.setItem('hot-news-mode', currentMode);
  applyMode();
}}
function applyMode() {{
  const btn = document.getElementById('mode-toggle');
  const cards = document.querySelectorAll('.topic-card');
  const MAX_CARDS = 30;
  const MIN_CARDS = 15;
  let expanded = false;
  // 切换动画
  document.body.style.opacity = '0.92';
  requestAnimationFrame(() => {{
    setTimeout(() => {{ document.body.style.opacity = '1'; }}, 80);
  }});
  if (currentMode === '\u5927\u4f17\u7248') {{
    btn.textContent = '\u5927\u4f17\u7248';
    document.querySelectorAll('.card-ai-persp').forEach(el => el.style.display = '');
    document.querySelectorAll('.card-ai-action').forEach(el => el.style.display = '');
    document.querySelectorAll('.card-difficulty').forEach(el => el.style.display = 'inline-block');
    document.querySelectorAll('.source-tag').forEach(el => el.style.display = 'none');
    const seen = {{}};
    const qualified = [];
    cards.forEach(card => {{
      const audience = card.dataset.audience || 'both';
      if (audience === 'pro') {{ card.style.display = 'none'; return; }}
      // 尊重当前档位筛选
      const grade = card.dataset.grade || '';
      if (currentLevel !== 'all' && currentLevel !== 'bookmarks' && grade !== currentLevel) {{ card.style.display = 'none'; return; }}
      if (currentLevel === 'bookmarks' && !bookmarks.includes(card.dataset.hash || '')) {{ card.style.display = 'none'; return; }}
      // 尊重价值标签筛选
      if (currentValueTag !== 'all') {{
        const cardTags = (card.dataset.valueTags || '').split(',');
        if (!cardTags.includes(currentValueTag)) {{ card.style.display = 'none'; return; }}
      }}
      const title = card.querySelector('h2')?.textContent?.trim();
      if (!title) return;
      if (seen[title]) {{ card.style.display = 'none'; return; }}
      seen[title] = true;
      qualified.push(card);
    }});
    qualified.sort((a, b) => {{
      const sa = parseFloat(a.querySelector('.score-badge')?.textContent) || 0;
      const sb = parseFloat(b.querySelector('.score-badge')?.textContent) || 0;
      return sb - sa;
    }});
    qualified.forEach((card, idx) => {{
      card.style.display = idx < MAX_CARDS || expanded ? '' : 'none';
    }});
    // 更新计数
    _updateCardCount(qualified.length, expanded ? qualified.length : Math.min(MAX_CARDS, qualified.length));
  }} else {{
    btn.textContent = '\u4e13\u4e1a\u7248';
    // 显示所有AI内容（不隐藏视角和行动引导）
    document.querySelectorAll('.card-difficulty').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.source-tag').forEach(el => el.style.display = 'inline-block');
    const qualified = [];
    cards.forEach(card => {{
      const audience = card.dataset.audience || 'both';
      if (audience === 'general') {{ card.style.display = 'none'; return; }}
      // 尊重当前档位筛选
      const grade = card.dataset.grade || '';
      if (currentLevel !== 'all' && currentLevel !== 'bookmarks' && grade !== currentLevel) {{ card.style.display = 'none'; return; }}
      if (currentLevel === 'bookmarks' && !bookmarks.includes(card.dataset.hash || '')) {{ card.style.display = 'none'; return; }}
      // 尊重价值标签筛选
      if (currentValueTag !== 'all') {{
        const cardTags = (card.dataset.valueTags || '').split(',');
        if (!cardTags.includes(currentValueTag)) {{ card.style.display = 'none'; return; }}
      }}
      qualified.push(card);
    }});
    // 限制专业版最多显示30条
    qualified.sort((a, b) => {{
      const sa = parseFloat(a.querySelector('.score-badge')?.textContent) || 0;
      const sb = parseFloat(b.querySelector('.score-badge')?.textContent) || 0;
      return sb - sa;
    }});
    // 限制专业版最多显示30条
    const visibleCount = expanded ? qualified.length : Math.min(30, Math.max(MIN_CARDS, Math.min(MAX_CARDS, qualified.length)));
    qualified.forEach((card, idx) => {{
      card.style.display = idx < visibleCount ? '' : 'none';
    }});
    _updateCardCount(qualified.length, visibleCount);
  }}
}}
function _updateCardCount(total, visible) {{
  let el = document.getElementById('card-count');
  if (!el) {{
    el = document.createElement('span');
    el.id = 'card-count';
    el.style.cssText = 'font-size:11px;opacity:0.7;margin-left:4px;';
    document.getElementById('mode-toggle').appendChild(el);
  }}
  el.textContent = visible + '/' + total;
}}
function toggleExpand() {{
  expanded = !expanded;
  applyMode();
  const btn = document.getElementById('expand-btn');
  if (btn) btn.textContent = expanded ? '\u25b2 \u6536\u8d77' : '\u25bc \u67e5\u770b\u66f4\u591a';
}}
applyMode();
</script>
</body>
</html>
'''
    
    return html


# ============ 主流程 ============
async def main(config_path: str = "config.yaml", output_dir: str = "site",
              mode: str = "web", no_translate: bool = False, update_only: bool = False,
              quick: bool = False):
    """主流程
    
    Args:
        config_path: 配置文件路径
        output_dir: 输出目录
        mode: 产物类型，web（在线网页，支持跳转和翻译）或 local（本地HTML，无跳转无翻译）
        no_translate: 跳过翻译，快速生成
        update_only: 仅更新内容（重新抓取+评分+生成HTML），不修改配置和代码
        quick: 快速模式（跳过翻译+AI评论），约30秒出站
    """
    total_start = time.time()
    
    # 模式检查：正式运维必须使用完整模式
    if quick:
        logger.warning("⚠️ 当前使用快速模式（--quick），AI评论和翻译将被跳过！")
        logger.warning("⚠️ 正式运维更新请使用完整模式，确保内容质量！")
    else:
        logger.info("✅ 使用完整模式，将生成AI评论和翻译（预计10-15分钟）")
    
    phases = 5 if not quick else 3  # 快速模式少2个阶段
    
    logger.info("=" * 50)
    logger.info(f"  {get_generator_name()}")
    logger.info(f"  时间: {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  模式: {'在线网页' if mode == 'web' else '本地HTML'}")
    if quick:
        logger.info(f"  快速模式: 跳过翻译和AI评论（约15-30秒）")
    elif no_translate:
        logger.info(f"  翻译: 跳过（约20-40秒）")
    else:
        logger.info(f"  翻译: 已启用（约60-120秒，取决于数据量和网络）")
    if update_only:
        logger.info(f"  更新: 仅刷新内容")
    logger.info("=" * 50)
    
    phase_pbar = ProgressBar(phases, "🚀 总体进度")
    
    # --- 阶段1: 环境检查 ---
    phase_pbar.update(1)
    
    # 检查是否在已有 git 仓库中（避免覆盖其他项目）
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            remotes = result.stdout.strip().split('\n')
            remote_names = [r.split('\t')[0] for r in remotes]
            logger.warning(f"检测到当前目录已在 Git 仓库中:")
            for r in remotes:
                logger.warning(f"  {r}")
            logger.warning(f"建议：")
            logger.warning(f"  1. 在新目录中初始化项目，避免影响现有仓库")
            logger.warning(f"  2. 或确认当前仓库就是本项目的专用仓库")
            logger.warning(f"  如需新建独立仓库，请先退出并执行：")
            logger.warning(f"    mkdir my-hot-news && cd my-hot-news")
            logger.warning(f"    git init")
            logger.warning(f"    cp -r (本项目文件) .")
            logger.warning(f"    git add . && git commit -m 'init'")
            logger.warning(f"    git remote add origin <你的仓库地址>")
            logger.warning(f"    git push -u origin main")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # git 未安装，忽略
    
    # 加载配置
    config = load_config(config_path)
    logger.info(f"配置加载完成")
    
    # --- 阶段2: 数据抓取 ---
    phase_pbar.update(1)
    logger.info("\n📡 抓取热点数据...")
    topics = await fetch_all(config)
    logger.info(f"共抓取 {len(topics)} 条原始数据")
    
    # --- 阶段3: 翻译处理 ---
    phase_pbar.update(1)
    if quick:
        logger.info("\n⚡ 快速模式：跳过翻译和AI评论（预计10秒）")
    elif no_translate:
        logger.info("\n⏭️ 跳过翻译（预计5秒）")
    else:
        logger.info(f"\n🌐 翻译处理（{len(topics)} 条，预计30-90秒）...")
    topics = await process_topics(topics, config, no_translate=no_translate, quick=quick)
    logger.info(f"处理完成")
    
    # --- 阶段4: 评分分类 ---
    phase_pbar.update(1)
    logger.info(f"\n📊 智能评分分类（{len(topics)} 条，预计3-10秒）...")
    categories = categorize_topics(topics, config)
    
    # 给所有 topic 添加 topic_id（用于跨快照追踪）
    for lvl_topics in categories.values():
        for t in lvl_topics:
            if "topic_id" not in t:
                t["topic_id"] = hashlib.sha256(t.get("title", "").strip().lower().encode()).hexdigest()[:12]

    # 保存数据快照（用于趋势分析）
    all_topics_flat = []
    for lvl_topics in categories.values():
        all_topics_flat.extend(lvl_topics)
    save_data_snapshot(all_topics_flat, config, output_dir="data")
    
    levels = config.get("scoring", {}).get("levels", [])
    sorted_levels = sorted(levels, key=lambda x: x["threshold"], reverse=True)
    for lvl in sorted_levels:
        count = len(categories.get(lvl["name"], []))
        logger.info(f"  [{lvl['name']}] {count} 条 (阈值: {lvl['threshold']})")
    
    # --- 阶段5: 生成网站 ---
    phase_pbar.update(1)
    logger.info("\n🏗️ 生成网站HTML（预计2-5秒）...")
    html = generate_html(categories, config, mode=mode)
    
    # 保存
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "hot-news.html").write_text(html, encoding="utf-8", errors="replace")
    
    # 生成 RSS Feed
    logger.info("  生成 RSS Feed...")
    rss_xml = generate_rss_feed(categories, config)
    (output_path / "feed.xml").write_text(rss_xml, encoding="utf-8")
    
    # 生成 JSON API
    logger.info("  生成 JSON API...")
    json_api = generate_json_api(categories, config)
    (output_path / "api.json").write_text(json_api, encoding="utf-8")
    
    # 生成 sitemap.xml
    logger.info("  生成 sitemap.xml...")
    sitemap_xml = generate_sitemap(categories, config)
    (output_path / "sitemap.xml").write_text(sitemap_xml, encoding="utf-8")
    
    # 生成 OPML 订阅列表
    logger.info("  生成 OPML...")
    opml_xml = generate_opml(config)
    (output_path / "sources.opml").write_text(opml_xml, encoding="utf-8")
    
    # 统计（从生成的HTML中计算实际卡片数）
    total = len(re.findall(r'<div class="topic-card"', html))
    
    # 邮件订阅
    if not quick:
        send_email_digest(categories, config)
    
    total_elapsed = time.time() - total_start
    logger.info(f"\n{'=' * 50}")
    logger.info(f"  ✅ 完成!")
    logger.info(f"  位置: {output_path.absolute()}")
    logger.info(f"  展示: {total} 篇")
    logger.info(f"  总用时: {ProgressBar._fmt_time(total_elapsed)}")
    if mode == "local":
        logger.info(f"  提示: 本地HTML模式，标题不可跳转，无翻译按钮")
    logger.info(f"{'=' * 50}")
    
    return categories


async def run_multi_sites(sites_config_path: str = "sites.yaml") -> dict:
    """运行多站点生成"""
    import yaml as _yaml
    
    sites_path = Path(sites_config_path)
    if not sites_path.exists():
        logger.error(f"站点配置文件不存在: {sites_path}")
        return {}
    
    with open(sites_path, "r", encoding="utf-8") as f:
        sites_cfg = _yaml.safe_load(f)
    
    sites = sites_cfg.get("sites", [])
    options = sites_cfg.get("options", {})
    
    enabled_sites = [s for s in sites if s.get("enabled", True)]
    logger.info(f"多站点模式: {len(enabled_sites)} 个站点")
    
    results = {}
    
    if options.get("parallel", True):
        # 并行生成所有站点
        async def _run_one(site):
            name = site["name"]
            logger.info(f"  开始生成: {name}")
            try:
                cats = await main(
                    config_path=site.get("config", "config.yaml"),
                    output_dir=site.get("output", "site/"),
                    mode="web",
                    no_translate=True,
                    quick=True
                )
                return (name, {"status": "success", "total": sum(len(v) for v in cats.values())})
            except Exception as e:
                logger.error(f"  {name} 生成失败: {e}")
                return (name, {"status": "error", "error": str(e)})
        
        site_results = await asyncio.gather(*[_run_one(s) for s in enabled_sites])
        for name, result in site_results:
            results[name] = result
    else:
        # 串行生成
        for site in enabled_sites:
            name = site["name"]
            logger.info(f"  开始生成: {name}")
            try:
                cats = await main(
                    config_path=site.get("config", "config.yaml"),
                    output_dir=site.get("output", "site/"),
                    mode="web",
                    no_translate=True,
                    quick=True
                )
                results[name] = {"status": "success", "total": sum(len(v) for v in cats.values())}
            except Exception as e:
                logger.error(f"  {name} 生成失败: {e}")
                results[name] = {"status": "error", "error": str(e)}
    
    # 生成站点索引页
    if options.get("generate_index", True):
        _generate_sites_index(enabled_sites, results)
    
    logger.info(f"多站点生成完成: {len([r for r in results.values() if r['status']=='success'])}/{len(enabled_sites)} 成功")
    return results


def _generate_sites_index(sites: list, results: dict) -> None:
    """生成站点索引页"""
    index_html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>热点聚合站点索引</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; background: #121118; color: #e8e0d4; }
    h1 { color: #c8a45c; }
    .site-card { background: rgba(28,26,35,0.65); border: 1px solid rgba(150,140,120,0.1); border-radius: 8px; padding: 20px; margin: 16px 0; }
    .site-name { font-size: 1.2em; font-weight: 600; color: #c8a45c; }
    .site-status { font-size: 0.9em; margin-top: 8px; }
    .status-success { color: #6b8e6b; }
    .status-error { color: #c8786a; }
    a { color: #c8a45c; text-decoration: none; }
    a:hover { text-decoration: underline; }
  </style>



</head>
<body>
  <h1>📡 热点聚合站点索引</h1>
"""
    for site in sites:
        name = site["name"]
        output = site.get("output", "site/")
        url = output.rstrip("/") + "/hot-news.html"
        result = results.get(name, {})
        status = result.get("status", "pending")
        total = result.get("total", 0)
        status_class = f"status-{status}" if status in ["success", "error"] else ""
        status_text = f"✅ {total} 条" if status == "success" else f"❌ {result.get('error', '失败')}" if status == "error" else "⏳ 待生成"
        
        index_html += f"""  <div class="site-card">
    <div class="site-name"><a href="{url}">{name}</a></div>
    <div class="site-status {status_class}">{status_text}</div>
  </div>
"""
    
    index_html += """</body>
</html>"""
    
    index_path = PROJECT_DIR / "site" / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_html, encoding="utf-8")
    logger.info(f"  站点索引页: {index_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="热点聚合网站生成器")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("-o", "--output", default="site", help="输出目录")
    parser.add_argument("-m", "--mode", default="web", choices=["web", "local"],
                        help="产物类型: web=在线网页(支持跳转和翻译), local=本地HTML(无跳转无翻译)")
    parser.add_argument("--no-translate", action="store_true",
                        help="跳过翻译，快速生成（不调用任何翻译API）")
    parser.add_argument("--update-only", action="store_true",
                        help="仅更新内容（重新抓取数据并生成HTML），不修改配置和代码")
    parser.add_argument("--quick", action="store_true",
                        help="【开发测试用】快速模式：跳过翻译和AI评论，约30秒出站。正式运维请勿使用！")
    parser.add_argument("--multi", action="store_true",
                        help="多站点模式：读取 sites.yaml 批量生成")
    args = parser.parse_args()
    
    import traceback
    try:
        if args.multi:
            asyncio.run(run_multi_sites())
        else:
            asyncio.run(main(
                config_path=args.config,
                output_dir=args.output,
                mode=args.mode,
                no_translate=args.no_translate,
                update_only=args.update_only,
                quick=args.quick,
            ))
    except Exception as e:
        # 打印完整 traceback，确保 Actions 日志可见
        traceback.print_exc()
        logger.error(f"运行失败: {e}")
        sys.exit(1)
