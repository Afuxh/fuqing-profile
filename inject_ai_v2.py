"""
AI内容注入工具 v7 - 修复三重BUG（字段名/匹配算法/容器创建）
修复记录：
  v7: 1. 字段名修正 zh->zh_summary, persp->perspective, action->action
      2. 匹配算法支持MD5 key（ai_content.json的key是标题MD5哈希）
      3. 策略3优化：在卡片</div>前插入时使用更可靠的模式
      4. 新增策略4：在h2后直接插入（兜底方案）
策略：
1. 先清理所有旧格式注入
2. 找到每个卡片的.ai-insights容器
3. 如果容器为空，填充AI评论
4. 如果容器不存在，在卡片末尾创建
5. 自动清理AI内容中的emoji前缀
"""
import json
import re
import hashlib

# emoji前缀模式
EMOJI_PREFIX = re.compile(r'^[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF]\s*')

def clean_emoji(text):
    """清理emoji前缀"""
    return EMOJI_PREFIX.sub('', text).strip() if text else text

def normalize(s):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', s.lower())

def title_md5(title):
    """计算标题的MD5哈希"""
    return hashlib.md5(title.encode('utf-8')).hexdigest()

def match_score(key, title):
    """匹配评分：支持MD5 key和标题文本两种模式"""
    # 模式A: key是MD5哈希（32位hex），直接与标题MD5比较
    if len(key) == 32 and re.match(r'^[a-f0-9]{32}$', key):
        title_hash = title_md5(title)
        if key == title_hash:
            return 1.0
        # MD5不匹配则无法通过文本匹配，直接返回0
        return 0

    # 模式B: key是标题文本，做模糊匹配
    key_norm = normalize(key)
    title_norm = normalize(title)
    if not key_norm or not title_norm:
        return 0
    if key_norm == title_norm:
        return 1.0
    if key_norm in title_norm or title_norm in key_norm:
        return 0.9
    if len(key_norm) >= 10 and len(title_norm) >= 10 and key_norm[:20] == title_norm[:20]:
        return 0.8
    return 0

def build_ai_block(content):
    """构建AI评论HTML块，使用正确的字段名"""
    zh_clean = clean_emoji(content.get('zh_summary', ''))
    persp_clean = clean_emoji(content.get('perspective', ''))
    action_clean = clean_emoji(content.get('action', ''))

    parts = []
    if zh_clean:
        parts.append(f'<div class="ai-insight-item zh-summary"><span class="ai-label">内容小结</span><p>{zh_clean}</p></div>')
    if persp_clean:
        parts.append(f'<div class="ai-insight-item perspective-comment"><span class="ai-label">视角解读</span><p>{persp_clean}</p></div>')
    if action_clean:
        parts.append(f'<div class="ai-insight-item action-guidance"><span class="ai-label">行动引导</span><p>{action_clean}</p></div>')

    return '\n    '.join(parts) if parts else ''

def main():
    with open('ai_content.json', 'r', encoding='utf-8') as f:
        ai_content = json.load(f)

    with open('site/hot-news.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # Step 1: 清理所有旧格式AI评论（</h2>后面直接跟着的）
    html = re.sub(
        r'</h2>\s*<p class="zh-summary">.*?</p>\s*<div class="perspective-comment">.*?</div>\s*<div class="action-guidance">.*?</div>',
        '</h2>',
        html, flags=re.DOTALL
    )

    # Step 2: 清理空的.ai-insights容器
    html = re.sub(
        r'<div class="ai-insights">\s*</div>',
        '',
        html, flags=re.DOTALL
    )

    # Step 3: 找到所有卡片标题
    card_pattern = r'(<h2[^>]*>(?:<a[^>]*>)?(.*?)(?:</a>)?</h2>)'
    cards = list(re.finditer(card_pattern, html, re.DOTALL))

    print(f"找到 {len(cards)} 个卡片")
    print(f"AI内容库有 {len(ai_content)} 条")

    # 预计算所有标题的MD5，用于快速查找
    title_to_md5 = {}
    for card_match in cards:
        title = card_match.group(2).strip()
        if title and len(title) >= 3:
            title_to_md5[title] = title_md5(title)

    replacements = []
    injected = 0
    not_matched = []

    for card_match in cards:
        full_h2 = card_match.group(1)
        title = card_match.group(2).strip()

        if not title or len(title) < 3:
            continue

        best_key = None
        best_score = 0
        for key in ai_content.keys():
            score = match_score(key, title)
            if score > best_score:
                best_score = score
                best_key = key

        if best_key and best_score >= 0.5:
            content = ai_content[best_key]
            ai_block = build_ai_block(content)

            if not ai_block:
                print(f"⚠️ 匹配成功但AI内容为空: {title[:50]}")
                not_matched.append(title)
                continue

            h2_end = card_match.end()
            search_area = html[h2_end:h2_end+5000]

            # 策略1: 找到空的.ai-insights容器并填充
            empty_match = re.search(r'<div class="ai-insights">\s*</div>', search_area)
            if empty_match:
                abs_start = h2_end + empty_match.start()
                abs_end = h2_end + empty_match.end()
                new_content = f'<div class="ai-insights">\n    {ai_block}\n  </div>'
                replacements.append((abs_start, abs_end, new_content))
                injected += 1
                print(f"✅ [{injected}] {title[:50]} (填充空容器)")
                continue

            # 策略2: 找到有内容的.ai-insights容器并替换内容
            filled_match = re.search(r'<div class="ai-insights">.*?</div>\s*</div>', search_area, re.DOTALL)
            if filled_match:
                abs_start = h2_end + filled_match.start()
                abs_end = h2_end + filled_match.end()
                new_content = f'<div class="ai-insights">\n    {ai_block}\n  </div>'
                replacements.append((abs_start, abs_end, new_content))
                injected += 1
                print(f"✅ [{injected}] {title[:50]} (替换旧内容)")
                continue

            # 策略3: 在卡片内的操作按钮区域后、卡片</div>前插入
<<<<<<< HEAD
            # 查找卡片结束位置（topic-card 的关闭标签）
            card_end_patterns = [
                # 匹配 topic-card 内最后一个 </div> 之前
=======
            card_end_patterns = [
>>>>>>> master
                r'(<div class="card-actions"[^>]*>.*?</div>)\s*(</div>\s*(?:<div class="topic-card"|</div>\s*<div id="level-|$))',
            ]
            for pat in card_end_patterns:
                insert_match = re.search(pat, search_area, re.DOTALL)
                if insert_match:
                    abs_pos = h2_end + insert_match.end() - len(insert_match.group(2))
                    new_content = f'<div class="ai-insights">\n    {ai_block}\n  </div>\n'
                    replacements.append((abs_pos, abs_pos, new_content))
                    injected += 1
                    print(f"✅ [{injected}] {title[:50]} (插入到操作区后)")
                    break
            else:
                # 策略4（兜底）: 在 </h2> 后面直接插入
                abs_pos = h2_end
                new_content = f'\n  <div class="ai-insights">\n    {ai_block}\n  </div>\n'
                replacements.append((abs_pos, abs_pos, new_content))
                injected += 1
                print(f"✅ [{injected}] {title[:50]} (h2后直接插入-兜底)")
        else:
            not_matched.append(title)

    # 从后往前替换，避免位置偏移
    replacements.sort(key=lambda x: x[0], reverse=True)
    for start, end, new_content in replacements:
        html = html[:start] + new_content + html[end:]

    # 清理所有仍然为空的.ai-insights容器
    empty_removed = 0
    def remove_empty_ai(match):
        nonlocal empty_removed
        inner = match.group(1).strip()
        if not inner or inner == '':
            empty_removed += 1
            return ''
        return match.group(0)
    html = re.sub(r'<div class="ai-insights">\s*(.*?)\s*</div>', remove_empty_ai, html, flags=re.DOTALL)

    with open('site/hot-news.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n=== 注入报告 ===")
    print(f"注入成功: {injected}")
    print(f"清理空容器: {empty_removed}")
    print(f"未匹配: {len(not_matched)}")
    if not_matched:
        for t in not_matched[:20]:
            print(f"  - {t[:80]}")
        if len(not_matched) > 20:
            print(f"  ... 还有 {len(not_matched)-20} 条")

if __name__ == "__main__":
    main()
