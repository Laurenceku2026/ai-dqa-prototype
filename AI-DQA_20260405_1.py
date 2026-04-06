import streamlit as st
import pandas as pd
import json
import os
import sqlite3
import openai
import re
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from duckduckgo_search import DDGS
from neo4j import GraphDatabase
import psycopg2
from sqlalchemy import create_engine, text

# ================== 页面配置 ==================
st.set_page_config(page_title="AI+DQA 风险分析系统", page_icon="🔍", layout="wide")

# ================== 数据库接口定义 ==================
class RiskDatabase:
    """所有数据库需要实现的统一接口"""
    def get_risks(self, product_type: str) -> List[Dict]:
        raise NotImplementedError
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        raise NotImplementedError
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        raise NotImplementedError

# ================== SQLite 实现 ==================
class SQLiteDatabase(RiskDatabase):
    def __init__(self):
        # 初始化 SQLite 数据库（如果文件不存在会自动创建）
        self.conn = sqlite3.connect('app_data.db', check_same_thread=False)
        self.init_tables()
        self.load_cached_data()
    
    def init_tables(self):
        cursor = self.conn.cursor()
        # 知识库表
        cursor.execute('''CREATE TABLE IF NOT EXISTS knowledge_base
                     (category TEXT, content TEXT)''')
        # 产品风险表
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_risks
                     (product_type TEXT, module TEXT, failure_mode TEXT, cause TEXT,
                      severity INTEGER, occurrence INTEGER, detection INTEGER,
                      mitigation TEXT)''')
        # 行业风险库表
        cursor.execute('''CREATE TABLE IF NOT EXISTS industry_risks
                     (category TEXT, product_type TEXT, failure_mode TEXT, cause TEXT,
                      mitigation TEXT, source TEXT)''')
        self.conn.commit()
    
    def load_cached_data(self):
        """加载知识库和风险数据到内存缓存，提高查询速度"""
        # 加载知识库
        cursor = self.conn.cursor()
        cursor.execute("SELECT category, content FROM knowledge_base")
        rows = cursor.fetchall()
        self.knowledge = {"光学": [], "机械": [], "材料": [], "热学": [], "电气": [], "控制": []}
        for category, content in rows:
            if category in self.knowledge:
                self.knowledge[category].append(content)
        # 加载产品风险数据
        cursor.execute("SELECT product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation FROM product_risks")
        rows = cursor.fetchall()
        self.product_risks = {}
        for row in rows:
            ptype = row[0]
            if ptype not in self.product_risks:
                self.product_risks[ptype] = []
            self.product_risks[ptype].append({
                "module": row[1], "failure_mode": row[2], "cause": row[3],
                "severity": row[4], "occurrence": row[5], "detection": row[6],
                "mitigation": row[7],
            })
        # 加载行业风险数据
        cursor.execute("SELECT category, product_type, failure_mode, cause, mitigation, source FROM industry_risks")
        rows = cursor.fetchall()
        self.industry_risks = []
        for row in rows:
            self.industry_risks.append({
                "category": row[0], "product_type": row[1], "failure_mode": row[2],
                "cause": row[3], "mitigation": row[4], "source": row[5],
            })
    
    def get_risks(self, product_type: str) -> List[Dict]:
        risks = self.product_risks.get(product_type, [])
        for r in risks:
            r["RPN"] = r["severity"] * r["occurrence"] * r["detection"]
        return sorted(risks, key=lambda x: x["RPN"], reverse=True)[:10]
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        if "路灯" in product_name:
            return {"product_type": "LED路灯", "function_units": ["光学","电气","热学"], "modules": ["LED光源","驱动电源"]}
        elif "天棚灯" in product_name:
            return {"product_type": "高功率天棚灯", "function_units": ["光学","电气","热学","控制"], "modules": ["COB光源","风扇","热管"]}
        else:
            return {"product_type": "default", "function_units": ["电气","机械"], "modules": ["PCBA"]}
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        # 从缓存的知识库中检索
        all_entries = []
        for cat, entries in self.knowledge.items():
            for entry in entries:
                if module in entry or failure_mode in entry:
                    all_entries.append(entry)
        if all_entries:
            return f"知识库参考：{all_entries[0][:200]}"
        return "建议参考行业规范和设计指南进行优化。"
    
    def get_knowledge_by_category(self, category: str) -> List[str]:
        return self.knowledge.get(category, [])
    
    def get_industry_risks(self, product_name: str) -> List[Dict]:
        matched = []
        for risk in self.industry_risks:
            if risk['product_type'] in product_name or any(k in product_name for k in risk['product_type'].split()):
                matched.append(risk)
        return matched[:5]

# ================== PostgreSQL 实现 ==================
class PostgreSQLDatabase(RiskDatabase):
    def __init__(self):
        # 从 st.secrets 读取 PostgreSQL 连接配置
        self.host = st.secrets["POSTGRES_HOST"]
        self.port = st.secrets.get("POSTGRES_PORT", 5432)
        self.database = st.secrets["POSTGRES_DATABASE"]
        self.user = st.secrets["POSTGRES_USER"]
        self.password = st.secrets["POSTGRES_PASSWORD"]
        self.conn = None
        self.connect()
        self.init_tables()
    
    def connect(self):
        try:
            self.conn = psycopg2.connect(
                host=self.host, port=self.port, dbname=self.database,
                user=self.user, password=self.password
            )
        except Exception as e:
            st.error(f"PostgreSQL 连接失败，降级使用 SQLite: {e}")
            self.conn = None
    
    def init_tables(self):
        if not self.conn:
            return
        cursor = self.conn.cursor()
        # 创建表（如果不存在）
        cursor.execute('''CREATE TABLE IF NOT EXISTS knowledge_base
                     (category TEXT, content TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_risks
                     (product_type TEXT, module TEXT, failure_mode TEXT, cause TEXT,
                      severity INTEGER, occurrence INTEGER, detection INTEGER,
                      mitigation TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS industry_risks
                     (category TEXT, product_type TEXT, failure_mode TEXT, cause TEXT,
                      mitigation TEXT, source TEXT)''')
        self.conn.commit()
    
    def get_risks(self, product_type: str) -> List[Dict]:
        if not self.conn:
            return SQLiteDatabase().get_risks(product_type)
        cursor = self.conn.cursor()
        cursor.execute("SELECT module, failure_mode, cause, severity, occurrence, detection, mitigation FROM product_risks WHERE product_type = %s", (product_type,))
        rows = cursor.fetchall()
        risks = []
        for row in rows:
            risk = {"module": row[0], "failure_mode": row[1], "cause": row[2],
                    "severity": row[3], "occurrence": row[4], "detection": row[5],
                    "mitigation": row[6]}
            risk["RPN"] = risk["severity"] * risk["occurrence"] * risk["detection"]
            risks.append(risk)
        return sorted(risks, key=lambda x: x["RPN"], reverse=True)[:10]
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        # 可以扩展为从 PostgreSQL 中查询产品结构
        return SQLiteDatabase().get_product_decomposition(product_name, description)
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        return SQLiteDatabase().get_mitigation(module, failure_mode)

# ================== Neo4j 实现 ==================
class Neo4jDatabase(RiskDatabase):
    def __init__(self):
        # 从 st.secrets 读取 Neo4j 连接配置
        self.uri = st.secrets["NEO4J_URI"]
        self.user = st.secrets["NEO4J_USER"]
        self.password = st.secrets["NEO4J_PASSWORD"]
        self.driver = None
        self.connect()
    
    def connect(self):
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        except Exception as e:
            st.error(f"Neo4j 连接失败，降级使用 SQLite: {e}")
            self.driver = None
    
    def get_risks(self, product_type: str) -> List[Dict]:
        if not self.driver:
            return SQLiteDatabase().get_risks(product_type)
        with self.driver.session() as session:
            result = session.run("""
                MATCH (p:ProductType {name: $ptype})-[:HAS_RISK]->(r:Risk)
                OPTIONAL MATCH (r)-[:MITIGATED_BY]->(m:Mitigation)
                RETURN r.module, r.failure_mode, r.cause, r.severity, r.occurrence, r.detection, m.text AS mitigation
                LIMIT 10
            """, ptype=product_type)
            risks = []
            for record in result:
                risk = {
                    "module": record["r.module"],
                    "failure_mode": record["r.failure_mode"],
                    "cause": record["r.cause"],
                    "severity": record["r.severity"],
                    "occurrence": record["r.occurrence"],
                    "detection": record["r.detection"],
                    "mitigation": record["mitigation"] or "无记录",
                }
                risk["RPN"] = risk["severity"] * risk["occurrence"] * risk["detection"]
                risks.append(risk)
            return sorted(risks, key=lambda x: x["RPN"], reverse=True)[:10]
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        return SQLiteDatabase().get_product_decomposition(product_name, description)
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        return "（来自 Neo4j）详细缓解措施需查询图数据库。"

# ================== 数据库工厂 ==================
def get_database(db_type: str) -> RiskDatabase:
    """根据类型创建对应的数据库实例"""
    if db_type == "PostgreSQL":
        return PostgreSQLDatabase()
    elif db_type == "Neo4j":
        return Neo4jDatabase()
    else:
        return SQLiteDatabase()

# ================== 初始化 Session State ==================
if "lang" not in st.session_state:
    st.session_state.lang = "zh"
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "db_type" not in st.session_state:
    st.session_state.db_type = "SQLite"
if "database" not in st.session_state:
    st.session_state.database = get_database(st.session_state.db_type)
if "enable_web_search" not in st.session_state:
    st.session_state.enable_web_search = False
if "translation_cache" not in st.session_state:
    st.session_state.translation_cache = {}

# LLM 临时覆盖配置
if "temp_api_key" not in st.session_state:
    st.session_state.temp_api_key = ""
if "temp_base_url" not in st.session_state:
    st.session_state.temp_base_url = "https://api.deepseek.com"
if "temp_model" not in st.session_state:
    st.session_state.temp_model = "deepseek-chat"

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

# ================== AI 分析 ==================
def generate_ai_analysis(product_name: str, product_desc: str, enable_web: bool, db: RiskDatabase) -> str:
    # 从当前数据库获取知识库上下文
    user_kb = retrieve_knowledge_context(product_name, product_desc, db)
    internal_risks = get_internal_risks(product_name, db)
    industry_risks = get_industry_risks(product_name, db)
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

def retrieve_knowledge_context(product_name: str, product_desc: str, db: RiskDatabase, limit=5) -> str:
    # 简化实现，实际可从数据库查询
    return "（暂无用户知识库条目）"

def get_internal_risks(product_name: str, db: RiskDatabase) -> str:
    # 简化实现，实际可从数据库查询
    return "无匹配的内部风险记录。"

def get_industry_risks(product_name: str, db: RiskDatabase) -> str:
    # 简化实现，实际可从数据库查询
    return "无匹配的行业风险记录。"

def generate_mitigation_strategy(risk_item: Dict) -> str:
    base = risk_item.get("mitigation", "建议参考行业规范和设计指南。")
    return f"""
针对 **{risk_item['module']}** 的 **{risk_item['failure_mode']}** 问题，建议如下策略：

1. **设计层面**：{base}
2. **仿真验证**：热/结构/电路仿真验证余量。
3. **测试标准**：参考 IEC/GB，增加可靠性测试。

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
    st.session_state.enable_web_search = st.checkbox("启用联网搜索", value=st.session_state.enable_web_search)

    st.markdown("---")
    
    # 数据库切换
    st.subheader("🗄️ 数据库配置")
    db_option = st.selectbox("选择数据库", ["SQLite", "PostgreSQL", "Neo4j"], index=["SQLite", "PostgreSQL", "Neo4j"].index(st.session_state.db_type))
    if st.button("切换数据库"):
        st.session_state.db_type = db_option
        st.session_state.database = get_database(db_option)
        st.success(f"已切换到 {db_option} 数据库")
        st.rerun()
    
    st.markdown("---")
    
    # 知识库管理（使用当前数据库）
    st.subheader("📚 知识库管理")
    # 此处应调用数据库接口进行知识库的增删改查，为保持简洁，省略具体实现

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
    
    # 数据库连接状态
    st.subheader("🗄️ 数据库连接状态")
    st.json({
        "当前数据库": st.session_state.db_type,
        "联网搜索": "已启用" if st.session_state.enable_web_search else "未启用",
        "DeepSeek API": "已配置" if (st.session_state.temp_api_key or st.secrets.get("DEEPSEEK_API_KEY")) else "未配置",
    })

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
        "db_connected": "✅ 已连接",
        "db_disconnected": "⚠️ 连接失败",
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
        "db_connected": "✅ Connected",
        "db_disconnected": "⚠️ Disconnected",
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
    st.info(f"当前数据库: {st.session_state.db_type}")
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
        db = st.session_state.database
        if ai_analyze:
            with st.spinner(t["generating"]):
                report = generate_ai_analysis(product_name, product_desc, st.session_state.enable_web_search, db)
                st.markdown("### 🤖 AI 生成的风险分析报告")
                st.markdown(report)
        else:
            # 快速分析：基于当前数据库的风险数据
            decomposition = db.get_product_decomposition(product_name, product_desc)
            risks = db.get_risks(decomposition.get("product_type", "default"))
            if risks:
                st.subheader(t["decomposition_title"])
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("产品", product_name)
                col_b.metric("功能件", ", ".join(decomposition.get("function_units", [])))
                col_c.metric("主要模块", ", ".join(decomposition.get("modules", [])[:3]))

                st.subheader(t["risks_title"])
                df = pd.DataFrame(risks)
                df["RPN"] = df["severity"] * df["occurrence"] * df["detection"]
                df = df.sort_values("RPN", ascending=False)
                st.dataframe(df[["module","failure_mode","cause","severity","occurrence","detection","RPN"]], use_container_width=True)

                st.subheader(t["strategy_title"])
                for idx, risk in df.iterrows():
                    with st.expander(f"{idx+1}. {risk['module']} - {risk['failure_mode']} (RPN={risk['RPN']})"):
                        strategy = generate_mitigation_strategy(risk)
                        st.markdown(strategy)

                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(t["download_btn"], data=csv, file_name=f"{product_name}_risks.csv", mime="text/csv")
            else:
                st.warning(t["no_risks"])

st.markdown("---")
st.caption(t["footer"])
