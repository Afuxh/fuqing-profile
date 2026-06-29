"""
chat-report 数据解析管道 v1.0
阶段二：extract → clean → structure 三级管道

用法：
    python pipeline.py                    # 全量运行
    python pipeline.py --incremental      # 增量模式（仅处理新增日期）
    python pipeline.py --date 2026-06-29 # 仅处理指定日期

产出：
    - temp/raw_messages.json    (extract)
    - temp/clean_messages.json  (clean)
    - output/chat-stats-pipeline/chat_stats.json (structure)
"""

import sqlite3
import zstandard as zstd
import hashlib
import json
import os
import sys
import re
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# ============================================================
# 配置
# ============================================================
BASE_DB = r"C:\Users\45826\AppData\Roaming\wechat-data-analysis-desktop\output\databases\wxid_qkslns979kft22"
CONTACT_DB = os.path.join(BASE_DB, "contact.db")
MESSAGE_DBS = [os.path.join(BASE_DB, f"message_{i}.db") for i in [0, 1]]  # 仅这两个分片有目标群聊数据

TARGET_USERNAME = "48997819170@chatroom"
TARGET_TABLE = "Msg_" + hashlib.md5(TARGET_USERNAME.encode()).hexdigest()
TARGET_NICKNAME = "【交流2群】🌲2026IP训练营"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = SCRIPT_DIR  # 脚本本身在 output/chat-stats-pipeline/ 下
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))  # workspace 根
TEMP_DIR = os.path.join(WORKSPACE_ROOT, "temp")

# zstd magic bytes
ZSTD_MAGIC = b'\x28\xb5/\xfd'

# 系统消息过滤关键词
SYSTEM_MSG_PATTERNS = [
    r'邀请".*?"加入了群聊',
    r'加入了群聊',
    r'撤回了一条消息',
    r'修改群名为',
    r'被移出群聊',
    r'移出了群聊',
    r'已退出群聊',
    r'你修改了群公告',
    r'发布了群公告',
    r'^<sysmsg',
    r'^<msg>',
    r'发送了一条.*消息',
    r'开启了朋友验证',
    r'已开启群聊邀请确认',
    r'修改了',
    r'发起了实时语音聊天',
    r'语音通话',
    r'视频通话',
    r'已结束',
    r'以上是.*的聊天记录',
]

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# ============================================================
# 第一层：Extract — SQL查询 → zstd解压 → raw_messages.json
# ============================================================
def load_contact_mapping():
    """从 contact.db 加载 wxid → nick_name 映射"""
    conn = sqlite3.connect(CONTACT_DB)
    cur = conn.cursor()
    mapping = {}
    try:
        cur.execute("SELECT username, nick_name, remark FROM contact")
        for row in cur.fetchall():
            username, nick_name, remark = row
            display = remark or nick_name or username
            mapping[username] = display
    except Exception as e:
        print(f"[WARN] 加载联系人映射失败: {e}")
    conn.close()
    return mapping

def decompress_content(raw):
    """解压消息内容：zstd BLOB 或 明文 TEXT"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        if raw[:4] == ZSTD_MAGIC:
            try:
                dctx = zstd.ZstdDecompressor()
                decompressed = dctx.decompress(raw)
                return decompressed.decode('utf-8', errors='replace')
            except Exception:
                return raw.decode('utf-8', errors='replace')
        else:
            return raw.decode('utf-8', errors='replace')
    return str(raw)

def parse_group_message(content, contact_map):
    """解析群聊消息：提取发送者wxid和消息正文"""
    if not content:
        return None, ""
    
    # 群聊消息格式: wxid_sender:\n消息内容
    if ':\n' in content or content.startswith('wxid_'):
        parts = content.split(':\n', 1)
        if len(parts) == 2:
            sender_raw = parts[0].strip()
            body = parts[1]
        else:
            # 可能只有 wxid 没有正文
            sender_raw = content.split('\n', 1)[0].strip() if '\n' in content else content.strip()
            body = content[len(sender_raw):].lstrip('\n')
        
        # 去除末尾冒号
        sender = sender_raw.rstrip(':')
        
        # 检查 sender 是否看起来像 wxid
        if sender.startswith('wxid_') or sender.endswith('@chatroom'):
            return sender, body
    
    # 回退：整个内容作为正文
    return None, content

def extract_layer(contact_map):
    """第一层：提取原始消息"""
    print("[EXTRACT] 开始提取消息...")
    all_messages = []
    total = 0
    
    for db_path in MESSAGE_DBS:
        if not os.path.exists(db_path):
            print(f"  跳过不存在的数据库: {db_path}")
            continue
        
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        try:
            cur.execute(f"""
                SELECT local_id, create_time, local_type, message_content, source
                FROM [{TARGET_TABLE}]
                ORDER BY create_time, local_id
            """)
            
            db_name = os.path.basename(db_path)
            count = 0
            for row in cur.fetchall():
                local_id, create_time, local_type, raw_content, source = row
                content = decompress_content(raw_content)
                
                if not content:
                    continue
                
                sender_wxid, body = parse_group_message(content, contact_map)
                sender_name = contact_map.get(sender_wxid, sender_wxid or "Unknown")
                
                msg = {
                    "id": total,
                    "db": db_name,
                    "local_id": local_id,
                    "timestamp": create_time,
                    "datetime": datetime.fromtimestamp(create_time).strftime('%Y-%m-%d %H:%M:%S'),
                    "date": datetime.fromtimestamp(create_time).strftime('%Y-%m-%d'),
                    "hour": datetime.fromtimestamp(create_time).hour,
                    "local_type": local_type,
                    "sender_id": sender_wxid or "",
                    "sender_name": sender_name,
                    "content": body or content,
                    "raw_content_length": len(content),
                    "is_system": False,
                    "printable_ratio": 0,
                }
                
                all_messages.append(msg)
                total += 1
                count += 1
            
            print(f"  {db_name}: 提取 {count:,} 条消息")
        except Exception as e:
            print(f"  {db_name}: 错误 - {e}")
        finally:
            conn.close()
    
    print(f"[EXTRACT] 总计: {total:,} 条原始消息")
    
    # 写入 raw_messages.json
    raw_path = os.path.join(TEMP_DIR, "raw_messages.json")
    with open(raw_path, 'w', encoding='utf-8') as f:
        json.dump(all_messages, f, ensure_ascii=False, indent=1)
    print(f"[EXTRACT] 输出: {raw_path} ({len(all_messages):,} 条)")
    
    return all_messages

# ============================================================
# 第二层：Clean — 过滤系统消息 → 去重 → printable_ratio → clean_messages.json
# ============================================================
def is_system_message(msg):
    """判断是否为系统消息"""
    content = msg.get("content", "")
    
    # 系统消息类型
    if msg.get("local_type") == 10000:
        for pattern in SYSTEM_MSG_PATTERNS:
            if re.search(pattern, content):
                return True
    
    # 检查是否匹配系统消息模式
    for pattern in SYSTEM_MSG_PATTERNS:
        if re.search(pattern, content):
            # 排除一些误判：用户消息中也可能包含"加入"
            if pattern in [r'加入了群聊'] and len(content) > 20:
                continue
            return True
    
    return False

def calc_printable_ratio(text):
    """计算可打印字符占比"""
    if not text:
        return 0
    printable = sum(1 for c in text if c.isprintable() or c in '\n\r\t')
    return printable / len(text) if len(text) > 0 else 0

def clean_layer(messages):
    """第二层：清洗消息"""
    print(f"\n[CLEAN] 开始清洗 {len(messages):,} 条消息...")
    
    # 1. 标记系统消息
    system_count = 0
    for msg in messages:
        if is_system_message(msg):
            msg["is_system"] = True
            system_count += 1
    print(f"  系统消息: {system_count:,} 条")
    
    # 2. 过滤系统消息
    cleaned = [m for m in messages if not m["is_system"]]
    print(f"  过滤后: {len(cleaned):,} 条")
    
    # 3. 计算 printable_ratio
    for msg in cleaned:
        msg["printable_ratio"] = calc_printable_ratio(msg["content"])
    
    # 4. 过滤低质量 (printable_ratio < 0.6)
    before_quality = len(cleaned)
    cleaned = [m for m in cleaned if m["printable_ratio"] >= 0.6]
    print(f"  低质量过滤 (ratio<0.6): {before_quality - len(cleaned):,} 条移除")
    
    # 5. 按 (content, sender_id, date) 去重
    seen = set()
    deduped = []
    for msg in cleaned:
        key = (msg["content"][:200], msg["sender_id"], msg["date"])
        if key not in seen:
            seen.add(key)
            deduped.append(msg)
    print(f"  去重: {len(cleaned) - len(deduped):,} 条移除")
    
    # 6. 合并短消息（同一发送者连续3条<10字符合并）
    merged = []
    i = 0
    merge_count = 0
    while i < len(deduped):
        msg = deduped[i]
        if len(msg["content"]) < 10 and i + 2 < len(deduped):
            # 检查后2条是否同发送者
            next1 = deduped[i+1]
            next2 = deduped[i+2]
            if (msg["sender_id"] == next1["sender_id"] == next2["sender_id"] and
                len(next1["content"]) < 10 and len(next2["content"]) < 10):
                merged_content = msg["content"] + "\n" + next1["content"] + "\n" + next2["content"]
                msg["content"] = merged_content
                msg["merged_from"] = 3
                merged.append(msg)
                i += 3
                merge_count += 1
                continue
        merged.append(msg)
        i += 1
    print(f"  短消息合并: {merge_count} 组合并")
    
    print(f"[CLEAN] 最终: {len(merged):,} 条清洗后消息")
    
    # 写入 clean_messages.json
    clean_path = os.path.join(TEMP_DIR, "clean_messages.json")
    with open(clean_path, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    print(f"[CLEAN] 输出: {clean_path}")
    
    return merged

# ============================================================
# 第三层：Structure — 11个维度聚合统计 → chat_stats.json
# ============================================================
def extract_keywords(messages, top_n=30):
    """从消息中提取高频关键词（简单Token方式）"""
    word_counter = Counter()
    stop_words = set(['的', '了', '是', '我', '不', '在', '人', '有', '和', '就', '都',
                      '也', '一个', '没有', '这个', '那个', '可以', '会', '吗', '吧',
                      '啊', '呢', '哦', '嗯', '哈', '好', '很', '这', '那', '他', '她',
                      '你', '要', '能', '去', '来', '做', '说', '还', '让', '给', '把',
                      '被', '上', '下', '中', '大', '小', '多', '少', '对', '但', '等',
                      '因为', '所以', '如果', '虽然', '自己', '怎么', '什么', '哪', '谁',
                      '些', '然后', '就是', '比较', '不是', '我们', '他们', '你们',
                      '就是', '觉得', '应该', '可能', '已经', '不过', '还是', '只是',
                      '看到', '知道', '现在', '今天', '昨天'])
    
    for msg in messages:
        text = msg.get("content", "")
        # 简单的中文分词：2-4字词组
        for i in range(len(text)-1):
            for length in [2, 3, 4]:
                if i + length <= len(text):
                    word = text[i:i+length]
                    # 过滤纯数字/纯符号/停用词
                    if (word not in stop_words and 
                        all('\u4e00' <= c <= '\u9fff' or c.isalpha() for c in word) and
                        not word.isdigit()):
                        word_counter[word] += 1
    
    # 取 top_n
    keywords = [{"word": w, "count": c} for w, c in word_counter.most_common(top_n)]
    return keywords

def extract_links(messages):
    """提取URL和文件分享"""
    url_pattern = re.compile(r'https?://[^\s]+')
    links = []
    for msg in messages:
        urls = url_pattern.findall(msg.get("content", ""))
        for url in urls:
            links.append({
                "sender_name": msg["sender_name"],
                "datetime": msg["datetime"],
                "url": url,
                "context": msg["content"][:200]
            })
    return links

def extract_long_messages(messages, min_length=200):
    """提取长消息（关键摘要候选）"""
    long_msgs = [m for m in messages if len(m["content"]) >= min_length]
    long_msgs.sort(key=lambda x: len(x["content"]), reverse=True)
    return long_msgs[:50]  # 最多50条

def extract_ai_tools(messages):
    """提取AI工具名称出现频次"""
    ai_tools_patterns = [
        'ChatGPT', 'GPT-4', 'GPT', 'Claude', 'DeepSeek', 'Gemini', 'Copilot',
        'Midjourney', 'DALL-E', 'Stable Diffusion', 'SD',
        'Cursor', 'Windsurf', 'v0', 'Bolt', 'Replit',
        'Coze', '扣子', '文心一言', '通义千问', '智谱', 'Kimi',
        'Notion', '飞书', 'Obsidian', 'Flomo',
        'Perplexity', 'Poe', 'Suno', 'Runway', 'Pika',
        'Make', 'Zapier', 'n8n', 'Dify',
    ]
    
    tool_counter = Counter()
    for msg in messages:
        content = msg.get("content", "")
        for tool in ai_tools_patterns:
            if tool.lower() in content.lower():
                tool_counter[tool] += 1
    
    return [{"tool": t, "count": c} for t, c in tool_counter.most_common(20)]

def structure_layer(messages):
    """第三层：结构化统计"""
    print(f"\n[STRUCTURE] 开始聚合统计...")
    
    # 基础统计
    total_msgs = len(messages)
    unique_senders = set(m["sender_id"] for m in messages if m["sender_id"])
    unique_dates = set(m["date"] for m in messages)
    
    # 日期范围
    dates = sorted(unique_dates)
    date_range_start = dates[0] if dates else ""
    date_range_end = dates[-1] if dates else ""
    
    # 发送者统计
    sender_counter = Counter(m["sender_name"] for m in messages)
    members = []
    for name, count in sender_counter.most_common(20):
        members.append({
            "name": name,
            "message_count": count,
            "percentage": round(count / total_msgs * 100, 1)
        })
    
    # 每日统计
    daily_counter = Counter(m["date"] for m in messages)
    daily_active = defaultdict(set)
    for m in messages:
        daily_active[m["date"]].add(m["sender_id"])
    
    daily_stats = []
    for date in dates:
        daily_stats.append({
            "date": date,
            "message_count": daily_counter[date],
            "active_members": len(daily_active[date]),
        })
    
    # 每小时分布
    hour_counter = Counter(m["hour"] for m in messages)
    hourly_dist = [{"hour": h, "count": hour_counter.get(h, 0)} for h in range(24)]
    
    # 关键词提取
    keywords = extract_keywords(messages, top_n=30)
    
    # URL/链接提取
    links = extract_links(messages)
    
    # 长消息
    long_messages = extract_long_messages(messages, min_length=200)
    
    # AI工具
    ai_tools = extract_ai_tools(messages)
    
    # 特定发送者消息（shulin: 树林相关）
    shulin_messages = []
    for m in messages:
        if '树林' in m.get("sender_name", "") or '树成林' in m.get("sender_name", ""):
            shulin_messages.append({
                "sender_name": m["sender_name"],
                "datetime": m["datetime"],
                "content": m["content"][:300]
            })
    
    # 互动统计（@提及）
    mention_pattern = re.compile(r'@(\S+)')
    mentions = []
    for m in messages:
        ats = mention_pattern.findall(m.get("content", ""))
        for at in ats:
            if len(at) > 1 and len(at) < 30:
                mentions.append({"from": m["sender_name"], "to": at, "datetime": m["datetime"]})
    
    mention_counter = Counter(m["to"] for m in mentions)
    top_mentions = [{"name": n, "count": c} for n, c in mention_counter.most_common(15)]
    
    # 组装输出
    stats = {
        "meta": {
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "group_name": TARGET_NICKNAME,
            "group_username": TARGET_USERNAME,
            "data_range_start": date_range_start,
            "data_range_end": date_range_end,
            "total_days": len(dates),
        },
        "overview": {
            "total_messages": total_msgs,
            "unique_senders": len(unique_senders),
            "unique_dates": len(dates),
            "total_days": len(dates),
            "avg_daily_messages": round(total_msgs / max(len(dates), 1)),
            "avg_daily_active": round(sum(d["active_members"] for d in daily_stats) / max(len(daily_stats), 1)),
        },
        "members": members,
        "topics": {
            "keywords": keywords,
            "ai_tools": ai_tools,
        },
        "highlights": long_messages,
        "interaction": {
            "total_mentions": len(mentions),
            "top_mentioned": top_mentions,
        },
        "time_dist": hourly_dist,
        "daily": daily_stats,
        "student_work": {
            "total_links": len(links),
            "links": links[:50],  # 最多50条链接
        },
        "shulin": shulin_messages[:30],  # 最多30条
        "insights": {
            "message_density": {
                "peak_hour": max(hourly_dist, key=lambda x: x["count"])["hour"] if hourly_dist else 0,
                "peak_day": max(daily_stats, key=lambda x: x["message_count"])["date"] if daily_stats else "",
                "peak_day_count": max(d["message_count"] for d in daily_stats) if daily_stats else 0,
            },
        }
    }
    
    # 写入 chat_stats.json
    stats_path = os.path.join(OUTPUT_DIR, "chat_stats.json")
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    print(f"[STRUCTURE] 输出: {stats_path}")
    print(f"[STRUCTURE] 统计维度:")
    print(f"  overview: {total_msgs} 条消息, {len(unique_senders)} 人, {len(dates)} 天")
    print(f"  members: TOP {len(members)} 说话人")
    print(f"  topics: {len(keywords)} 关键词, {len(ai_tools)} AI工具")
    print(f"  highlights: {len(long_messages)} 条长消息")
    print(f"  interaction: {len(mentions)} 次@提及")
    print(f"  time_dist: 24小时分布")
    print(f"  daily: {len(daily_stats)} 天趋势")
    print(f"  student_work: {len(links)} 条链接")
    
    return stats

# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 60)
    print("chat-report 数据解析管道 v1.0")
    print("=" * 60)
    print(f"目标群聊: {TARGET_NICKNAME}")
    print(f"消息表: {TARGET_TABLE}")
    print(f"日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 加载联系人映射
    contact_map = load_contact_mapping()
    print(f"联系人映射: {len(contact_map)} 条记录")
    
    # 第一层：提取
    raw_messages = extract_layer(contact_map)
    
    # 第二层：清洗
    clean_messages = clean_layer(raw_messages)
    
    # 第三层：结构化
    stats = structure_layer(clean_messages)
    
    print(f"\n{'=' * 60}")
    print(f"管道完成! chat_stats.json 已生成")
    print(f"{'=' * 60}")
    
    return stats

if __name__ == "__main__":
    main()
