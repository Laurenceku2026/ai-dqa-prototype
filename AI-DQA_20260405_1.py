import streamlit as st
import pandas as pd
import json
import os
import sqlite3
import openai
import re
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Optional
from duckduckgo_search import DDGS

# ================== 页面配置 ==================
st.set_page_config(page_title="AI+DQA 风险分析系统", page_icon="🔍", layout="wide")

# ================== 初始化 Session State ==================
if "lang" not in st.session_state:
    st.session_state.lang = "zh"
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "knowledge_db" not in st.session_state:
    st.session_state.knowledge_db = {}
if "product_risks_db" not in st.session_state:
    st.session_state.product_risks_db = {}
if "industry_risks_db" not in st.session_state:
    st.session_state.industry_risks_db = {}
if "translation_cache" not in st.session_state:
    st.session_state.translation_cache = {}
if "enable_web_search" not in st.session_state:
    st.session_state.enable_web_search = False

# LLM 临时覆盖配置
if "temp_api_key" not in st.session_state:
    st.session_state.temp_api_key = ""
if "temp_base_url" not in st.session_state:
    st.session_state.temp_base_url = "https://api.deepseek.com"
if "temp_model" not in st.session_state:
    st.session_state.temp_model = "deepseek-chat"

# ================== 数据库初始化 ==================
def init_db():
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base
                 (category TEXT, content TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS product_risks
                 (product_type TEXT, module TEXT, failure_mode TEXT, cause TEXT,
                  severity INTEGER, occurrence INTEGER, detection INTEGER,
                  mitigation TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS industry_risks
                 (category TEXT, product_type TEXT, failure_mode TEXT, cause TEXT,
                  mitigation TEXT, source TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS analysis_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  product_name TEXT, product_desc TEXT, report TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def load_knowledge_from_db():
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("SELECT category, content FROM knowledge_base")
    rows = c.fetchall()
    conn.close()
    knowledge = {"光学": [], "机械": [], "材料": [], "热学": [], "电气": [], "控制": []}
    for category, content in rows:
        if category in knowledge:
            knowledge[category].append(content)
    return knowledge

def save_knowledge_to_db(category, content):
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("INSERT INTO knowledge_base (category, content) VALUES (?, ?)", (category, content))
    conn.commit()
    conn.close()

def delete_knowledge_from_db(category, content):
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM knowledge_base WHERE category = ? AND content = ?", (category, content))
    conn.commit()
    conn.close()

def clear_knowledge_category(category):
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM knowledge_base WHERE category = ?", (category,))
    conn.commit()
    conn.close()

def load_product_risks_from_db():
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("SELECT product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation FROM product_risks")
    rows = c.fetchall()
    conn.close()
    risks = {}
    for row in rows:
        product_type = row[0]
        if product_type not in risks:
            risks[product_type] = []
        risks[product_type].append({
            "module": row[1],
            "failure_mode": row[2],
            "cause": row[3],
            "severity": row[4],
            "occurrence": row[5],
            "detection": row[6],
            "mitigation": row[7],
        })
    return risks

def insert_product_risk(product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation):
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute('''INSERT INTO product_risks (product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation))
    conn.commit()
    conn.close()

def load_industry_risks_from_db():
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("SELECT category, product_type, failure_mode, cause, mitigation, source FROM industry_risks")
    rows = c.fetchall()
    conn.close()
    risks = []
    for row in rows:
        risks.append({
            "category": row[0],
            "product_type": row[1],
            "failure_mode": row[2],
            "cause": row[3],
            "mitigation": row[4],
            "source": row[5],
        })
    return risks

def init_industry_risks():
    industry_data = [
        ("LED", "LED路灯", "光衰过快", "结温过高", "优化散热设计，选用优质灯珠", "IEC 62031"),
        ("LED", "LED路灯", "浪涌损坏", "雷击或电网波动", "加装SPD，做好接地", "IEC 61643-11"),
        ("LED", "LED吸顶灯", "频闪", "驱动电源纹波过大", "增加输出滤波，满足IEEE 1789", "IEEE 1789"),
        ("LED", "LED筒灯", "死灯", "静电击穿/过流", "ESD防护，恒流驱动", "ANSI/ESD S20.20"),
        ("清洁电器", "洗地机", "滚刷堵转", "毛发缠绕", "防缠绕结构+过流保护", "行业最佳实践"),
        ("清洁电器", "洗地机", "电池续航衰减", "电芯老化/BMS不均衡", "选用A品电芯，均衡充电", "GB 31241"),
        ("清洁电器", "吸尘器", "吸力下降", "滤网堵塞", "定期清理提示，HEPA滤网", "IEC 60312-1"),
        ("清洁电器", "吸尘器", "电机过热", "风道堵塞", "堵塞报警，优化风道", "UL 1017"),
        ("宠物电器", "宠物饮水机", "水泵噪音大", "叶轮磨损/异物", "无刷水泵，易拆洗", "行业标准"),
        ("宠物电器", "宠物饮水机", "水位误报", "传感器脏污", "双传感器冗余", "IPX7防水"),
        ("宠物电器", "宠物喂食器", "卡粮", "粮食受潮", "干燥剂+防潮设计", "行业最佳实践"),
        ("宠物电器", "宠物喂食器", "APP连接失败", "WiFi信号/固件bug", "双频WiFi，OTA升级", "IEEE 802.11"),
    ]
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM industry_risks")
    for row in industry_data:
        c.execute("INSERT INTO industry_risks (category, product_type, failure_mode, cause, mitigation, source) VALUES (?,?,?,?,?,?)", row)
    conn.commit()
    conn.close()

# 初始化数据库和预设数据
init_db()
if not load_industry_risks_from_db():
    init_industry_risks()

# 加载数据到 session_state
if not st.session_state.knowledge_db:
    st.session_state.knowledge_db = load_knowledge_from_db()
if not st.session_state.product_risks_db:
    st.session_state.product_risks_db = load_product_risks_from_db()
if not st.session_state.industry_risks_db:
    st.session_state.industry_risks_db = load_industry_risks_from_db()

# ================== 管理员凭证 ==================
ADMIN_USERNAME = "Laurence_ku"
ADMIN_PASSWORD = "Ku_product$2026"

# ================== DeepSeek 客户端 ==================
def get_openai_client():
    api_key = st.session_state.temp_api_key if st.session_state.temp_api_key else st.secrets.get("DEEPSEEK_API_KEY", "")
    base_url = st.session_state.temp_base_url if st.session_state.temp_base_url else st.secrets.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not api_key:
        return None, "未配置 API Key"
    return openai.OpenAI(api_key=api_key, base_url=base_url), None

def call_deepseek(prompt: str, max_tokens=4000) -> str:
    client, error = get_openai_client()
    if error:
        return f"AI 调用失败: {error}"
    try:
        model = st.session_state.temp_model if st.session_state.temp_model else st.secrets.get("DEEPSEEK_MODEL", "deepseek-chat")
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI 调用失败: {str(e)}"

def translate_text(text: str, target_lang: str) -> str:
    if not text or not text.strip():
        return text
    cache_key = f"{text}_{target_lang}"
    if cache_key in st.session_state.translation_cache:
        return st.session_state.translation_cache[cache_key]
    if target_lang == "zh":
        if re.search(r'[\u4e00-\u9fff]', text):
            return text
    else:
        if not re.search(r'[\u4e00-\u9fff]', text):
            return text
    prompt = f"请将以下文本翻译成{'中文' if target_lang == 'zh' else 'English'}，只输出翻译结果：\n\n{text}"
    translated = call_deepseek(prompt, max_tokens=500)
    st.session_state.translation_cache[cache_key] = translated
    return translated

# ================== 联网搜索 ==================
def web_search(query: str, max_results=3) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "未找到相关结果。"
        output = []
        for r in results:
            output.append(f"- **{r['title']}**: {r['body'][:300]}... [来源]({r['href']})")
        return "\n".join(output)
    except Exception as e:
        return f"搜索失败: {str(e)}"

# ================== 多数据源检索 ==================
def retrieve_knowledge_context(product_name: str, product_desc: str, limit=5) -> str:
    all_entries = []
    for category, entries in st.session_state.knowledge_db.items():
        for entry in entries:
            all_entries.append(f"[{category}] {entry}")
    if not all_entries:
        return "（暂无用户知识库条目）"
    keywords = set(product_name.lower().split() + product_desc.lower().split())
    matched = []
    for entry in all_entries:
        score = sum(1 for kw in keywords if kw in entry.lower())
        if score > 0:
            matched.append((score, entry))
    matched.sort(reverse=True, key=lambda x: x[0])
    top = [entry for _, entry in matched[:limit]]
    if not top:
        top = all_entries[:limit]
    return "\n".join(top)

def get_internal_risks(product_name: str) -> str:
    matched = []
    for ptype, risks in st.session_state.product_risks_db.items():
        if any(k in product_name for k in ptype.split()):
            for r in risks[:3]:
                matched.append(f"- {r['module']}: {r['failure_mode']}（原因：{r['cause']}）")
            break
    if not matched:
        return "无匹配的内部风险记录。"
    return "\n".join(matched)

def get_industry_risks(product_name: str) -> str:
    matched = []
    for risk in st.session_state.industry_risks_db:
        if risk['product_type'] in product_name or any(k in product_name for k in risk['product_type'].split()):
            matched.append(f"- [{risk['source']}] {risk['product_type']}: {risk['failure_mode']}（原因：{risk['cause']}）→ {risk['mitigation']}")
    if not matched:
        return "无匹配的行业风险记录。"
    return "\n".join(matched[:5])

# ================== AI 分析 ==================
def generate_ai_analysis(product_name: str, product_desc: str, enable_web: bool) -> str:
    user_kb = retrieve_knowledge_context(product_name, product_desc)
    internal_risks = get_internal_risks(product_name)
    industry_risks = get_industry_risks(product_name)
    web_context = ""
    if enable_web:
        with st.spinner("正在联网搜索相关失效案例..."):
            web_context = web_search(f"{product_name} 失效 故障 案例 可靠性", max_results=4)
    prompt = f"""
你是一位拥有25年经验的资深产品可靠性工程师。请根据以下信息对产品进行全面的风险分析。

产品名称：{product_name}
设计描述：{product_desc}

=== 企业内部经验知识库 ===
{user_kb}

=== 内部产品风险数据库 ===
{internal_risks}

=== 行业标准失效数据库 ===
{industry_risks}

=== 联网搜索结果（最新） ===
{web_context if web_context else "未启用联网搜索或未找到相关信息"}

请按照以下 Markdown 格式输出风险分析报告：

### 1. 产品分解
*   **功能件**: [根据产品描述推测其主要功能模块]
*   **主要模块**: [列出3-5个核心模块]

### 2. Top 5 潜在风险
| 模块 | 失效模式 | 潜在原因 | 严重度(1-10) | 发生度(1-10) | 探测度(1-10) | RPN |
|------|----------|----------|--------------|--------------|--------------|-----|
| ... | ... | ... | ... | ... | ... | ... |

### 3. 关键风险缓解策略
针对RPN最高的前3项风险，结合上述多个数据源中的经验，提供具体的设计建议、验证方法和参考标准。

要求：
- 充分利用用户知识库、内部风险库和行业数据库中的信息。
- 如果联网搜索有相关案例，也请引用。
- 建议要具体、可执行。
"""
    return call_deepseek(prompt, max_tokens=4000)

# ================== 辅助函数 ==================
def generate_mitigation_strategy(risk_item: Dict) -> str:
    base = risk_item.get("mitigation", "建议参考行业规范和设计指南。")
    return f"""
针对 **{risk_item['module']}** 的 **{risk_item['failure_mode']}** 问题，建议如下策略：

1. **设计层面**：{base}
2. **仿真验证**：热/结构/电路仿真验证余量。
3. **测试标准**：参考 IEC/GB，增加可靠性测试。
4. **知识库参考**：结合用户知识库和行业标准。

**RPN**：{risk_item['severity']} × {risk_item['occurrence']} × {risk_item['detection']} = **{risk_item['RPN']}**
"""

# ================== 管理员设置弹窗 ==================
@st.dialog("管理员设置", width="large")
def admin_settings_dialog():
    st.subheader("🔐 管理员验证")
    if not st.session_state.admin_logged_in:
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        if st.button("登录"):
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                st.session_state.admin_logged_in = True
                st.rerun()
            else:
                st.error("用户名或密码错误")
        return

    st.success("管理员已登录")

    # 联网搜索开关
    st.subheader("🌐 联网搜索配置")
    st.session_state.enable_web_search = st.checkbox("启用联网搜索（AI 分析时自动搜索网络失效案例）", value=st.session_state.enable_web_search)
    st.caption("使用 DuckDuckGo 免费搜索，无需 API Key。")

    st.markdown("---")
    # 知识库管理
    categories = {
        "光学": "光学 / Optical",
        "机械": "机械 / Mechanical",
        "材料": "材料 / Material",
        "热学": "热学 / Thermal",
        "电气": "电气 / Electrical",
        "控制": "控制 / Control"
    }
    selected_cat_key = st.selectbox("选择分类", list(categories.keys()), format_func=lambda x: categories[x])
    items = st.session_state.knowledge_db.get(selected_cat_key, [])
    st.markdown(f"**{categories[selected_cat_key]} 现有条目（共 {len(items)} 条）：**")
    page_size = st.number_input("每页显示条目数", min_value=5, max_value=50, value=20, step=5)
    total_pages = (len(items) + page_size - 1) // page_size if items else 1
    if items:
        page = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1) - 1
        start = page * page_size
        end = min(start + page_size, len(items))
        with st.container(height=400):
            for i in range(start, end):
                col1, col2 = st.columns([10, 1])
                with col1:
                    st.write(f"{i+1}. {items[i]}")
                with col2:
                    if st.button("❌", key=f"del_{selected_cat_key}_{i}"):
                        delete_knowledge_from_db(selected_cat_key, items[i])
                        st.session_state.knowledge_db = load_knowledge_from_db()
                        st.rerun()
        if total_pages > 1:
            st.caption(f"第 {page+1} / {total_pages} 页")
    else:
        st.info("暂无条目")

    new_item = st.text_area(f"添加新经验教训", height=100,
                            placeholder="支持中英文，系统会自动翻译。例如：LED路灯防水结构必须采用双重密封设计。")
    if st.button("添加条目"):
        if new_item.strip():
            save_knowledge_to_db(selected_cat_key, new_item.strip())
            st.session_state.knowledge_db = load_knowledge_from_db()
            st.success("已添加")
            st.rerun()

    st.markdown("---")
    # Excel 导入导出
    st.subheader("📥 导出/导入知识库（Excel）")
    if st.button("下载知识库模板 (Excel)"):
        export_data = {}
        for cat_key, cat_display in categories.items():
            export_data[cat_display] = ["\n".join(st.session_state.knowledge_db.get(cat_key, []))] if st.session_state.knowledge_db.get(cat_key) else [""]
        df_export = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_export.to_excel(writer, sheet_name="知识库", index=False)
        excel_data = output.getvalue()
        st.download_button(
            label="下载 Excel 文件",
            data=excel_data,
            file_name=f"knowledge_base_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    uploaded_file = st.file_uploader("上传 Excel 文件（覆盖现有知识库）", type=["xlsx"])
    if uploaded_file is not None:
        try:
            df_upload = pd.read_excel(uploaded_file, sheet_name="知识库", header=0)
            column_mapping = {
                "光学": ["光学", "光学 / Optical", "Optical"],
                "机械": ["机械", "机械 / Mechanical", "Mechanical"],
                "材料": ["材料", "材料 / Material", "Material"],
                "热学": ["热学", "热学 / Thermal", "Thermal"],
                "电气": ["电气", "电气 / Electrical", "Electrical"],
                "控制": ["控制", "控制 / Control", "Control"]
            }
            actual_columns = {}
            for cat_key, possible_names in column_mapping.items():
                for name in possible_names:
                    if name in df_upload.columns:
                        actual_columns[cat_key] = name
                        break
            if len(actual_columns) == len(column_mapping):
                for cat_key in column_mapping.keys():
                    clear_knowledge_category(cat_key)
                for cat_key, col_name in actual_columns.items():
                    # 读取该列从第2行开始的所有非空值（每行一条经验）
                    items = df_upload[col_name].dropna().astype(str).tolist()
                    items = [item.strip() for item in items if item.strip() and item.strip().lower() != 'nan']
                    for item in items:
                        save_knowledge_to_db(cat_key, item)
                st.session_state.knowledge_db = load_knowledge_from_db()
                total = sum(len(st.session_state.knowledge_db[cat]) for cat in column_mapping)
                st.success(f"知识库已更新！共导入 {total} 条记录。")
                st.rerun()
            else:
                missing = [k for k in column_mapping if k not in actual_columns]
                st.error(f"Excel 缺少以下列：{missing}。请使用下载的模板格式。")
        except Exception as e:
            st.error(f"读取文件失败：{e}")

    st.markdown("---")
    # LLM API 临时配置
    st.subheader("⚙️ LLM API 临时配置")
    new_api_key = st.text_input("DeepSeek API Key", value=st.session_state.temp_api_key, type="password")
    new_base_url = st.text_input("Base URL", value=st.session_state.temp_base_url)
    new_model = st.text_input("Model", value=st.session_state.temp_model)
    if st.button("应用临时配置"):
        st.session_state.temp_api_key = new_api_key
        st.session_state.temp_base_url = new_base_url
        st.session_state.temp_model = new_model
        st.success("已应用")
        st.rerun()

    st.markdown("---")
    # 数据库状态
    st.subheader("🗄️ 数据库连接状态")
    kb_counts = {categories[cat]: len(st.session_state.knowledge_db.get(cat, [])) for cat in categories}
    product_types = list(st.session_state.product_risks_db.keys())
    industry_count = len(st.session_state.industry_risks_db)
    st.json({
        "当前数据库": "SQLite (app_data.db)",
        "用户知识库统计": kb_counts,
        "内置风险产品类型": product_types,
        "行业风险记录数": industry_count,
        "联网搜索": "已启用" if st.session_state.enable_web_search else "未启用"
    })

    st.markdown("---")
    # 一键加载基础风险数据
    st.subheader("⚙️ 初始化内置风险数据")
    if st.button("一键加载内置风险数据"):
        conn = sqlite3.connect('app_data.db')
        c = conn.cursor()
        c.execute("DELETE FROM product_risks")
        insert_product_risk("LED路灯", "LED光源", "光衰过快", "结温过高", 8,7,5,"优化散热")
        insert_product_risk("LED路灯", "驱动电源", "电容鼓包", "高温/纹波大",9,6,6,"选用长寿命电容")
        insert_product_risk("LED吸顶灯", "LED灯珠", "单颗死灯", "静电击穿",7,5,6,"ESD防护")
        insert_product_risk("洗地机", "滚刷电机", "堵转烧毁", "毛发缠绕",8,7,6,"过流保护")
        insert_product_risk("洗地机", "水泵", "不出水", "堵塞",7,6,5,"滤网+自清洁")
        insert_product_risk("吸尘器", "电机", "吸力下降", "滤网堵塞",7,6,5,"定期清理")
        insert_product_risk("宠物饮水机", "水泵", "噪音大", "叶轮磨损",6,5,4,"无刷电机")
        insert_product_risk("宠物喂食器", "出粮机构", "卡粮", "粮食受潮",8,5,6,"干燥剂")
        conn.commit()
        conn.close()
        st.session_state.product_risks_db = load_product_risks_from_db()
        st.success("内置风险数据已加载！")
        st.rerun()

# ================== 右上角按钮 ==================
col_left, col_spacer, col_zh, col_en, col_gear = st.columns([5, 3, 1, 1, 1])
with col_zh:
    if st.button("中文", key="zh_btn", use_container_width=True):
        st.session_state.lang = "zh"
        st.rerun()
with col_en:
    if st.button("English", key="en_btn", use_container_width=True):
        st.session_state.lang = "en"
        st.rerun()
with col_gear:
    if st.button("⚙️", key="settings_btn", use_container_width=True):
        admin_settings_dialog()

# ================== 多语言文本 ==================
TEXTS = {
    "zh": {
        "title": "🔍 AI+DQA 产品风险分析系统",
        "sidebar_title": "关于系统",
        "basis_items": ["25+年研发管理经验", "AI大模型数据分析", "知识图谱+图神经网络", "DFSS/六西格玛方法论"],
        "analyst_name_label": "分析人姓名",
        "analyst_name_ph": "请输入姓名",
        "analyst_title_label": "分析人头衔（可选）",
        "analyst_title_ph": "例如：研发总监",
        "api_status": "DeepSeek API 状态",
        "api_configured": "✅ 已配置",
        "api_not_configured": "❌ 未配置",
        "contact_info": "📞 **联系：**  \n✉️ 电邮: Techlife2027@gmail.com",
        "input_title": "📝 产品风险分析",
        "product_name": "产品名称",
        "product_name_ph": "例如：高功率LED天棚灯",
        "product_desc": "设计描述",
        "product_desc_ph": "例如：200W COB光源，主动风扇散热，IP65",
        "analyze_btn_ai": "🚀 开始 AI 深度分析 (DeepSeek)",
        "analyze_btn_quick": "⚡ 快速分析 (本地知识库)",
        "product_name_missing": "请填写产品名称",
        "generating": "AI 正在分析中，请稍候...",
        "decomposition_title": "📐 产品分解结果",
        "risks_title": "⚠️ Top 潜在风险 (按RPN排序)",
        "strategy_title": "💡 设计策略与缓解措施",
        "download_btn": "📎 导出风险表格 (CSV)",
        "footer": "© 2026 Laurence Ku | AI+DQA 风险分析",
        "no_risks": "未检索到风险数据，请检查产品类型或先加载基础数据。",
        "db_status": "数据库状态",
        "db_connected": "✅ SQLite 已连接",
    },
    "en": {
        "title": "🔍 AI+DQA Product Risk Analysis",
        "sidebar_title": "About",
        "basis_items": ["25+ years R&D", "AI big data", "Knowledge Graph+GNN", "DFSS/Six Sigma"],
        "analyst_name_label": "Analyst Name",
        "analyst_name_ph": "Enter name",
        "analyst_title_label": "Title (Optional)",
        "analyst_title_ph": "e.g., R&D Director",
        "api_status": "DeepSeek API Status",
        "api_configured": "✅ Configured",
        "api_not_configured": "❌ Not configured",
        "contact_info": "📞 **Contact:**  \n✉️ Email: Techlife2027@gmail.com",
        "input_title": "📝 Product Risk Analysis",
        "product_name": "Product Name",
        "product_name_ph": "e.g., High Bay LED Light",
        "product_desc": "Design Description",
        "product_desc_ph": "e.g., 200W COB, active fan cooling, IP65",
        "analyze_btn_ai": "🚀 Start AI Deep Analysis (DeepSeek)",
        "analyze_btn_quick": "⚡ Quick Analysis (Local DB)",
        "product_name_missing": "Please enter product name",
        "generating": "AI is analyzing, please wait...",
        "decomposition_title": "📐 Product Decomposition",
        "risks_title": "⚠️ Top Potential Risks (by RPN)",
        "strategy_title": "💡 Design Strategies & Mitigations",
        "download_btn": "📎 Export Risk Table (CSV)",
        "footer": "© 2026 Laurence Ku | AI+DQA Risk Analysis",
        "no_risks": "No risk data found. Please check product type or load base data first.",
        "db_status": "Database Status",
        "db_connected": "✅ SQLite Connected",
    }
}

lang = st.session_state.lang
t = TEXTS[lang]

st.title(t["title"])

# ================== 侧边栏 ==================
with st.sidebar:
    st.markdown(f"## {t['sidebar_title']}")
    for item in t["basis_items"]:
        st.markdown(f"- {item}")
    st.markdown("---")
    analyst_name = st.text_input(t["analyst_name_label"], placeholder=t["analyst_name_ph"])
    analyst_title = st.text_input(t["analyst_title_label"], placeholder=t["analyst_title_ph"])
    if analyst_name:
        st.markdown(f"**{t['analyst_name_label']}: {analyst_name}**")
        if analyst_title:
            st.markdown(f"_{analyst_title}_")
    st.markdown("---")
    st.markdown(f"**{t['api_status']}**")
    has_api = bool(st.session_state.temp_api_key or st.secrets.get("DEEPSEEK_API_KEY"))
    if has_api:
        st.success(t["api_configured"])
        current_model = st.session_state.temp_model if st.session_state.temp_model else st.secrets.get("DEEPSEEK_MODEL", "deepseek-chat")
        st.caption(f"当前模型: {current_model}")
    else:
        st.error(t["api_not_configured"])
    st.markdown("---")
    st.markdown(f"**{t['db_status']}**")
    st.success(t["db_connected"])
    total_entries = sum(len(v) for v in st.session_state.knowledge_db.values())
    st.caption(f"用户知识库条目: {total_entries}")
    total_risks = sum(len(risks) for risks in st.session_state.product_risks_db.values())
    st.caption(f"内置风险记录: {total_risks}")
    industry_count = len(st.session_state.industry_risks_db)
    st.caption(f"行业风险记录: {industry_count}")
    st.markdown("---")
    st.markdown(t["contact_info"])

# ================== 主界面 ==================
st.markdown(f"### {t['input_title']}")
col1, col2 = st.columns(2)
with col1:
    product_name = st.text_input(t["product_name"], placeholder=t["product_name_ph"])
with col2:
    product_desc = st.text_area(t["product_desc"], placeholder=t["product_desc_ph"], height=100)

col_btn1, col_btn2 = st.columns(2)
with col_btn1:
    ai_analyze = st.button(t["analyze_btn_ai"], type="primary", use_container_width=True)
with col_btn2:
    quick_analyze = st.button(t["analyze_btn_quick"], use_container_width=True)

if ai_analyze or quick_analyze:
    if not product_name:
        st.error(t["product_name_missing"])
    else:
        if ai_analyze:
            with st.spinner(t["generating"]):
                report = generate_ai_analysis(product_name, product_desc, st.session_state.enable_web_search)
                st.markdown("### 🤖 AI 生成的风险分析报告")
                st.markdown(report)
        else:
            product_type = "default"
            if any(k in product_name for k in ["路灯", "吸顶灯", "筒灯"]):
                product_type = "LED路灯" if "路灯" in product_name else "LED吸顶灯"
            elif "洗地机" in product_name:
                product_type = "洗地机"
            elif "吸尘器" in product_name:
                product_type = "吸尘器"
            elif "饮水机" in product_name:
                product_type = "宠物饮水机"
            elif "喂食器" in product_name:
                product_type = "宠物喂食器"

            risks = st.session_state.product_risks_db.get(product_type, [])
            if risks:
                st.subheader(t["decomposition_title"])
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("产品", product_name)
                col_b.metric("功能件", "待分析")
                col_c.metric("主要模块", "待分析")

                st.subheader(t["risks_title"])
                df = pd.DataFrame(risks)
                df["RPN"] = df["severity"] * df["occurrence"] * df["detection"]
                df = df.sort_values("RPN", ascending=False)
                st.dataframe(df[["module","failure_mode","cause","severity","occurrence","detection","RPN"]], use_container_width=True)

                st.subheader(t["strategy_title"])
                for idx, risk in df.iterrows():
                    with st.expander(f"{idx+1}. {risk['module']} - {risk['failure_mode']} (RPN={risk['RPN']})"):
                        related = []
                        for cat, entries in st.session_state.knowledge_db.items():
                            for entry in entries:
                                if risk['module'] in entry or risk['failure_mode'] in entry:
                                    translated = translate_text(entry, lang)
                                    related.append(f"- {translated}")
                        if related:
                            st.markdown("**📚 用户知识库相关经验：**")
                            for item in related[:3]:
                                st.markdown(item)
                        industry_related = []
                        for ir in st.session_state.industry_risks_db:
                            if risk['failure_mode'] in ir['failure_mode'] or risk['module'] in ir['failure_mode']:
                                industry_related.append(f"- [{ir['source']}] {ir['failure_mode']}：{ir['mitigation']}")
                        if industry_related:
                            st.markdown("**🏭 行业标准数据库相关：**")
                            for item in industry_related[:2]:
                                st.markdown(item)
                        st.markdown(generate_mitigation_strategy(risk))

                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(t["download_btn"], data=csv, file_name=f"{product_name}_risks.csv", mime="text/csv")
            else:
                st.warning(t["no_risks"])

st.markdown("---")
st.caption(t["footer"])
