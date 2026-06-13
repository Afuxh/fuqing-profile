"""
从HTML中提取所有卡片标题，为未匹配的标题生成AI评论，追加到ai_content.json
v2: 修复字段名 zh->zh_summary, persp->perspective, key使用MD5哈希
"""
import json
import re
import hashlib

def title_md5(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()

def main():
    with open('site/hot-news.html', 'r', encoding='utf-8') as f:
        html = f.read()

    with open('ai_content.json', 'r', encoding='utf-8') as f:
        ai_content = json.load(f)

    # 提取所有卡片标题
    card_pattern = r'<h2[^>]*><a[^>]*>(.*?)</a></h2>'
    cards = re.findall(card_pattern, html, re.DOTALL)

    print(f"HTML中有 {len(cards)} 个卡片")
    print(f"AI内容库有 {len(ai_content)} 条")
<<<<<<< HEAD
    
=======

>>>>>>> master
    # 找出未匹配的标题（支持MD5 key匹配）
    def normalize(s):
        return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', s.lower())

    unmatched = []
    for title in cards:
        title = title.strip()
        if not title or len(title) < 3:
            continue
        matched = False
        title_hash = title_md5(title)
        for key in ai_content.keys():
            # 支持MD5匹配
            if key == title_hash:
                matched = True
                break
            # 支持文本匹配
            key_n = normalize(key)
            title_n = normalize(title)
            if key_n == title_n or key_n in title_n or title_n in key_n:
                matched = True
                break
        if not matched:
            unmatched.append(title)

    print(f"未匹配: {len(unmatched)}")

    # 为未匹配的标题生成AI评论
    for title in unmatched:
        key = title_md5(title)
        # 根据内容特征生成
        if any(w in title for w in ['融资', '投资', '亿美元', '万元', '基金', '上市', 'IPO']):
            ai_content[key] = {
                "zh_summary": f"{title[:30]}...",
                "perspective": "资本市场动态持续活跃，反映行业信心和投资方向。",
                "action": "关注相关赛道的投资趋势和商业化进展。",
                "value_tags": ["投资风向"]
            }
        elif any(w in title for w in ['NVIDIA', '英伟达', 'nvidia']):
            ai_content[key] = {
                "zh_summary": "NVIDIA发布最新AI基础设施和工具，持续巩固其在AI硬件领域的领导地位。",
                "perspective": "NVIDIA正在从芯片公司转型为AI全栈平台，其每一步动作都影响整个AI行业走向。",
                "action": "关注NVIDIA新产品的技术细节和定价策略，评估对自身工作的影响。",
                "value_tags": ["技术突破", "产业落地"]
            }
        elif any(w in title for w in ['模型', 'Model', 'model', 'LLM', '大模型']):
            ai_content[key] = {
                "zh_summary": "新AI模型发布，推动模型能力边界持续扩展。",
                "perspective": "模型迭代速度加快，开源与闭源之争持续。关注模型在实际场景中的表现而非仅看基准分数。",
                "action": "评估新模型是否适合你的使用场景，关注其成本效益比。",
                "value_tags": ["技术突破"]
            }
        elif any(w in title for w in ['Agent', 'agent', '智能体', 'Claw', 'claw']):
            ai_content[key] = {
                "zh_summary": "AI Agent领域持续创新，新的框架和工具不断涌现。",
                "perspective": "Agent是2025-2026年AI最重要的赛道之一，从编程到日常任务自动化，应用场景不断扩展。",
                "action": "体验新的Agent工具，思考如何将Agent集成到你的工作流中。",
                "value_tags": ["产业落地", "提效工具"]
            }
        elif any(w in title for w in ['教程', '指南', '入门', '安装']):
            ai_content[key] = {
                "zh_summary": "实用技术教程分享，帮助开发者快速上手新工具。",
                "perspective": "社区驱动的技术教程是学习新技术的最佳途径，实践导向的内容比理论更有价值。",
                "action": "跟随教程动手实践，将学到的技能应用到实际项目中。",
                "value_tags": ["提效工具"]
            }
        elif any(w in title for w in ['GitHub', 'github', '开源', 'Open Source']):
            ai_content[key] = {
                "zh_summary": "新的开源项目发布，为开发者社区贡献新工具和资源。",
                "perspective": "开源生态是AI创新的重要驱动力，社区贡献者正在以惊人速度构建实用工具。",
                "action": "浏览项目源码，学习其设计思路，考虑为其贡献或在其基础上构建。",
                "value_tags": ["技术突破", "开源生态"]
            }
        elif any(w in title for w in ['腾讯', 'Tencent', '美团', '阿里', '字节', 'MiniMax', '智谱', '阶跃', 'Anthropic', 'OpenAI', '微软', '华为', '苹果']):
            ai_content[key] = {
                "zh_summary": "科技巨头在AI领域的最新动态，展示AI产业的快速发展。",
                "perspective": "头部AI公司的战略布局和技术突破直接影响行业走向，值得密切关注。",
                "action": "关注相关产品的实际体验和技术细节，评估对自身工作的影响。",
                "value_tags": ["产业落地", "投资风向"]
            }
        elif any(w in title for w in ['早报', '晚报', '周报', '日报', '数智', '氪星']):
            ai_content[key] = {
                "zh_summary": "今日AI行业重要新闻汇总，帮助你快速了解行业动态。",
                "perspective": "信息聚合是AI时代的核心能力，高效获取关键信息比海量阅读更有价值。",
                "action": "浏览要点新闻，对感兴趣的话题深入阅读原文。",
                "value_tags": ["信息聚合"]
            }
        else:
            ai_content[key] = {
                "zh_summary": f"{title[:40]}...",
                "perspective": "AI行业持续快速演进，保持学习和关注是跟上趋势的关键。",
                "action": "深入了解该内容，评估其对你的工作或学习是否有参考价值。",
                "value_tags": ["行业动态"]
            }
        print(f"  + {title[:50]}")

    with open('ai_content.json', 'w', encoding='utf-8') as f:
        json.dump(ai_content, f, ensure_ascii=False, indent=2)

    print(f"\n总计: {len(ai_content)} 条AI内容")

if __name__ == "__main__":
    main()
