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

# ================== 页面配置 ==================
st.set_page_config(page_title="AI+DQA 风险分析系统", page_icon="🔍", layout="wide")

# 自定义CSS：报告铺满宽度、中英文红底、齿轮无红底、主按钮超大居中
st.markdown("""
<style>
    .main .block-container {
        max-width: 100% !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
    }
    .stMarkdown, .stMarkdown div, .stMarkdown table {
        width: 100% !important;
    }
    .stMarkdown table {
        display: table !important;
        overflow-x: auto;
    }
    .stButton button:has(span:contains("中文")),
    .stButton button:has(span:contains("English")) {
        background-color: #ff4b4b !important;
        color: white !important;
        font-size: 18px !important;
        font-weight: bold !important;
        border-radius: 40px !important;
        padding: 0.5rem 1.2rem !important;
        border: none !important;
    }
    .main-analyze button {
        font-size: 36px !important;
        padding: 20px 60px !important;
        background-color: #ff4b4b !important;
        color: white !important;
        border-radius: 60px !important;
        border: none !important;
        box-shadow: 0 8px 16px rgba(0,0,0,0.2);
        width: auto !important;
        min-width: 400px !important;
        transition: all 0.3s ease;
        cursor: pointer !important;
    }
    .main-analyze button:hover {
        transform: scale(1.02);
        background-color: #e03a3a !important;
    }
    .main-analyze {
        text-align: center;
        margin: 30px 0;
    }
    .stButton button:has(span:contains("⚙️")) {
        background-color: transparent !important;
        color: #31333f !important;
        border: 1px solid #ccc !important;
        border-radius: 8px !important;
        box-shadow: none !important;
    }
    .stButton button:has(span:contains("⚙️")):hover {
        background-color: #f0f2f6 !important;
        transform: none !important;
    }
    section[data-testid="stSidebar"] .stButton button {
        background-color: #f0f2f6 !important;
        color: #31333f !important;
        font-size: 14px !important;
        border-radius: 8px !important;
        box-shadow: none !important;
    }
</style>
""", unsafe_allow_html=True)

# ================== 初始化 Session State ==================
if "lang" not in st.session_state:
    st.session_state.lang = "zh"
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "enable_web_search" not in st.session_state:
    st.session_state.enable_web_search = True
if "translation_cache" not in st.session_state:
    st.session_state.translation_cache = {}

if "temp_api_key" not in st.session_state:
    st.session_state.temp_api_key = ""
if "temp_base_url" not in st.session_state:
    st.session_state.temp_base_url = "https://api.deepseek.com"
if "temp_model" not in st.session_state:
    st.session_state.temp_model = "deepseek-chat"

# ================== 管理员凭证 ==================
ADMIN_USERNAME = "Laurence_ku"
ADMIN_PASSWORD = "Ku_product$2026"

# ================== 数据库抽象接口 ==================
class RiskDatabase:
    def get_risks(self, product_type: str) -> List[Dict]:
        raise NotImplementedError
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        raise NotImplementedError
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        raise NotImplementedError
    def get_knowledge_by_category(self, category: str) -> List[str]:
        raise NotImplementedError
    def add_knowledge(self, category: str, content: str) -> None:
        raise NotImplementedError
    def delete_knowledge(self, category: str, content: str) -> None:
        raise NotImplementedError
    def clear_knowledge_category(self, category: str) -> None:
        raise NotImplementedError
    def get_all_knowledge(self) -> Dict[str, List[str]]:
        raise NotImplementedError
    def load_initial_data(self) -> None:
        raise NotImplementedError

# ================== SQLite 实现 ==================
class SQLiteDatabase(RiskDatabase):
    def __init__(self):
        self.conn = sqlite3.connect('app_data.db', check_same_thread=False)
        self.init_tables()
        self.load_caches()

    def init_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (category TEXT, content TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_risks
                          (product_type TEXT, module TEXT, failure_mode TEXT, cause TEXT,
                           severity INTEGER, occurrence INTEGER, detection INTEGER, mitigation TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS industry_risks
                          (category TEXT, product_type TEXT, failure_mode TEXT, cause TEXT,
                           mitigation TEXT, source TEXT)''')
        self.conn.commit()

    def load_caches(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT category, content FROM knowledge_base")
        rows = cursor.fetchall()
        self.knowledge = {"光学": [], "机械": [], "材料": [], "热学": [], "电气": [], "控制": []}
        for cat, cont in rows:
            if cat in self.knowledge:
                self.knowledge[cat].append(cont)

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
                "mitigation": row[7]
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
        for cat, entries in self.knowledge.items():
            for entry in entries:
                if module in entry or failure_mode in entry:
                    return entry[:200]
        return "建议参考行业规范和设计指南进行优化。"

    def get_knowledge_by_category(self, category: str) -> List[str]:
        return self.knowledge.get(category, [])

    def add_knowledge(self, category: str, content: str):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO knowledge_base (category, content) VALUES (?, ?)", (category, content))
        self.conn.commit()
        self.load_caches()

    def delete_knowledge(self, category: str, content: str):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM knowledge_base WHERE category = ? AND content = ?", (category, content))
        self.conn.commit()
        self.load_caches()

    def clear_knowledge_category(self, category: str):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM knowledge_base WHERE category = ?", (category,))
        self.conn.commit()
        self.load_caches()

    def get_all_knowledge(self) -> Dict[str, List[str]]:
        return self.knowledge

    def load_initial_data(self):
        cursor = self.conn.cursor()
        industry_data = [
            ("LED", "LED路灯", "光衰过快", "结温过高", "优化散热设计", "IEC 62031"),
            ("LED", "LED路灯", "浪涌损坏", "雷击", "加装SPD", "IEC 61643-11"),
            ("LED", "LED吸顶灯", "频闪", "纹波过大", "增加滤波", "IEEE 1789"),
            ("清洁电器", "洗地机", "滚刷堵转", "毛发缠绕", "防缠绕结构", "行业实践"),
            ("清洁电器", "吸尘器", "吸力下降", "滤网堵塞", "定期清理", "IEC 60312-1"),
            ("宠物电器", "宠物饮水机", "水泵噪音", "叶轮磨损", "无刷水泵", "行业标准"),
            ("宠物电器", "宠物喂食器", "卡粮", "粮食受潮", "干燥剂", "行业实践"),
        ]
        cursor.execute("DELETE FROM industry_risks")
        for row in industry_data:
            cursor.execute("INSERT INTO industry_risks (category, product_type, failure_mode, cause, mitigation, source) VALUES (?,?,?,?,?,?)", row)
        cursor.execute("SELECT COUNT(*) FROM product_risks")
        if cursor.fetchone()[0] == 0:
            default_risks = [
                ("LED路灯", "LED光源", "光衰过快", "结温过高", 8,7,5,"优化散热"),
                ("LED路灯", "驱动电源", "电容鼓包", "高温",9,6,6,"长寿命电容"),
                ("洗地机", "滚刷电机", "堵转", "毛发",8,7,6,"过流保护"),
                ("吸尘器", "电机", "吸力下降", "堵塞",7,6,5,"定期清理"),
                ("宠物饮水机", "水泵", "噪音", "磨损",6,5,4,"无刷电机"),
                ("宠物喂食器", "出粮机构", "卡粮", "受潮",8,5,6,"干燥剂"),
            ]
            for row in default_risks:
                cursor.execute("INSERT INTO product_risks VALUES (?,?,?,?,?,?,?,?)", row)
        self.conn.commit()
        self.load_caches()

# ================== Neo4j 实现 ==================
class Neo4jDatabase(RiskDatabase):
    def __init__(self):
        self.driver = None
        self.connect()

    def connect(self):
        try:
            uri = st.secrets.get("NEO4J_URI", "")
            user = st.secrets.get("NEO4J_USERNAME", "neo4j")
            password = st.secrets.get("NEO4J_PASSWORD", "")
            if not uri or not password:
                return
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            with self.driver.session() as session:
                session.run("RETURN 1")
        except Exception:
            self.driver = None

    def _query(self, query, params=None):
        if not self.driver:
            return []
        with self.driver.session() as session:
            result = session.run(query, params or {})
            return [record.data() for record in result]

    def get_risks(self, product_type: str) -> List[Dict]:
        if not self.driver:
            return []
        cypher = """
            MATCH (p:ProductType {name: $ptype})-[:HAS_RISK]->(r:Risk)
            RETURN r.module AS module, r.failure_mode AS failure_mode, r.cause AS cause,
                   r.severity AS severity, r.occurrence AS occurrence, r.detection AS detection,
                   r.mitigation AS mitigation
            LIMIT 10
        """
        results = self._query(cypher, {"ptype": product_type})
        risks = []
        for rec in results:
            risk = {
                "module": rec.get("module"),
                "failure_mode": rec.get("failure_mode"),
                "cause": rec.get("cause"),
                "severity": rec.get("severity"),
                "occurrence": rec.get("occurrence"),
                "detection": rec.get("detection"),
                "mitigation": rec.get("mitigation", "（来自Neo4j）"),
                "source": "Neo4j"
            }
            if all(k in risk for k in ["severity","occurrence","detection"]):
                risk["RPN"] = risk["severity"] * risk["occurrence"] * risk["detection"]
            risks.append(risk)
        return sorted(risks, key=lambda x: x.get("RPN", 0), reverse=True)[:10]

    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        return {}

    def get_mitigation(self, module: str, failure_mode: str) -> str:
        if not self.driver:
            return ""
        cypher = """
            MATCH (m:Module {name: $module})-[:HAS_FAILURE]->(f:FailureMode {name: $failure})
            OPTIONAL MATCH (f)-[:MITIGATED_BY]->(mit:Mitigation)
            RETURN mit.text AS mitigation
            LIMIT 1
        """
        results = self._query(cypher, {"module": module, "failure": failure_mode})
        if results and results[0].get("mitigation"):
            return results[0]["mitigation"]
        return ""

    def get_knowledge_by_category(self, category: str) -> List[str]:
        if not self.driver:
            return []
        cypher = "MATCH (k:Knowledge {category: $cat}) RETURN k.content AS content"
        results = self._query(cypher, {"cat": category})
        return [r["content"] for r in results]

    def add_knowledge(self, category: str, content: str):
        if not self.driver:
            return
        cypher = "CREATE (k:Knowledge {category: $cat, content: $cont})"
        with self.driver.session() as session:
            session.run(cypher, {"cat": category, "cont": content})

    def delete_knowledge(self, category: str, content: str):
        if not self.driver:
            return
        cypher = "MATCH (k:Knowledge {category: $cat, content: $cont}) DELETE k"
        with self.driver.session() as session:
            session.run(cypher, {"cat": category, "cont": content})

    def clear_knowledge_category(self, category: str):
        if not self.driver:
            return
        cypher = "MATCH (k:Knowledge {category: $cat}) DELETE k"
        with self.driver.session() as session:
            session.run(cypher, {"cat": category})

    def get_all_knowledge(self) -> Dict[str, List[str]]:
        if not self.driver:
            return {}
        cypher = "MATCH (k:Knowledge) RETURN k.category AS cat, k.content AS cont"
        results = self._query(cypher)
        knowledge = {}
        for r in results:
            cat = r["cat"]
            if cat not in knowledge:
                knowledge[cat] = []
            knowledge[cat].append(r["cont"])
        return knowledge

    def load_initial_data(self):
        pass

# ================== 混合数据库 ==================
class HybridDatabase(RiskDatabase):
    def __init__(self):
        self.sqlite = SQLiteDatabase()
        self.neo4j = Neo4jDatabase()
        self.neo4j_available = self.neo4j.driver is not None

    def get_risks(self, product_type: str) -> List[Dict]:
        risks_sql = self.sqlite.get_risks(product_type)
        risks_neo = self.neo4j.get_risks(product_type) if self.neo4j_available else []
        seen = set()
        merged = []
        for r in risks_sql + risks_neo:
            key = (r.get("module"), r.get("failure_mode"))
            if key not in seen:
                seen.add(key)
                merged.append(r)
        merged.sort(key=lambda x: x.get("RPN", 0), reverse=True)
        return merged[:10]

    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        return self.sqlite.get_product_decomposition(product_name, description)

    def get_mitigation(self, module: str, failure_mode: str) -> str:
        sql_mit = self.sqlite.get_mitigation(module, failure_mode)
        if sql_mit and "建议参考" not in sql_mit:
            return sql_mit
        neo_mit = self.neo4j.get_mitigation(module, failure_mode) if self.neo4j_available else ""
        if neo_mit:
            return neo_mit
        return sql_mit

    def get_knowledge_by_category(self, category: str) -> List[str]:
        sql_kb = self.sqlite.get_knowledge_by_category(category)
        neo_kb = self.neo4j.get_knowledge_by_category(category) if self.neo4j_available else []
        seen = set()
        merged = []
        for item in sql_kb + neo_kb:
            if item not in seen:
                seen.add(item)
                merged.append(item)
        return merged

    def add_knowledge(self, category: str, content: str):
        self.sqlite.add_knowledge(category, content)
        if self.neo4j_available:
            self.neo4j.add_knowledge(category, content)

    def delete_knowledge(self, category: str, content: str):
        self.sqlite.delete_knowledge(category, content)
        if self.neo4j_available:
            self.neo4j.delete_knowledge(category, content)

    def clear_knowledge_category(self, category: str):
        self.sqlite.clear_knowledge_category(category)
        if self.neo4j_available:
            self.neo4j.clear_knowledge_category(category)

    def get_all_knowledge(self) -> Dict[str, List[str]]:
        sql_all = self.sqlite.get_all_knowledge()
        if not self.neo4j_available:
            return sql_all
        neo_all = self.neo4j.get_all_knowledge()
        all_cats = set(sql_all.keys()) | set(neo_all.keys())
        merged = {}
        for cat in all_cats:
            merged[cat] = list(set(sql_all.get(cat, []) + neo_all.get(cat, [])))
        return merged

    def load_initial_data(self):
        self.sqlite.load_initial_data()
        if self.neo4j_available:
            self.neo4j.load_initial_data()

# ================== 数据库工厂 ==================
def get_database() -> RiskDatabase:
    return HybridDatabase()

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

# ================== AI 分析（增加分析人信息） ==================
def generate_ai_analysis(product_name: str, product_desc: str, enable_web: bool, db: RiskDatabase, analyst_name: str, analyst_title: str) -> str:
    all_knowledge = db.get_all_knowledge()
    kb_text = "\n".join([f"[{cat}] {item}" for cat, items in all_knowledge.items() for item in items[:3]])
    risks = db.get_risks(product_name)
    internal_text = "\n".join([f"- {r['module']}: {r['failure_mode']}（原因：{r['cause']}）" for r in risks[:5]])
    web_context = ""
    if enable_web:
        with st.spinner("正在联网搜索..."):
            web_context = web_search(f"{product_name} 失效 案例", max_results=3)
    
    # 构建作者信息
    if analyst_name and analyst_name.strip():
        if analyst_title and analyst_title.strip():
            author_info = f"分析人：{analyst_name.strip()} ({analyst_title.strip()})"
        else:
            author_info = f"分析人：{analyst_name.strip()}"
    else:
        author_info = "AI生成的风险分析报告"
    
    # 固定提示句
    disclaimer = "此报告是基于以上提供的有限信息，结合行业数据库和联网搜索结果生成的初步分析，仅供参考。"
    
    prompt = f"""
你是一位资深可靠性工程师。请根据以下信息对产品进行风险分析。

产品名称：{product_name}
设计描述：{product_desc}

=== 企业内部知识库（SQLite+Neo4j） ===
{kb_text if kb_text else "暂无"}

=== 产品风险数据库（融合） ===
{internal_text if internal_text else "暂无"}

=== 联网搜索结果 ===
{web_context if web_context else "未启用"}

请输出 Markdown 格式报告。报告必须以以下两行开头（不要添加额外说明）：

{author_info}

{disclaimer}

然后继续输出：
### 1. 产品分解
### 2. Top 5 潜在风险（表格：模块、失效模式、原因、严重度、发生度、探测度、RPN）
### 3. 关键风险缓解策略（针对RPN最高的3项）
"""
    return call_deepseek(prompt, max_tokens=4000)

def generate_mitigation_strategy(risk: Dict) -> str:
    base = risk.get("mitigation", "建议参考行业规范。")
    return f"""
针对 **{risk['module']}** 的 **{risk['failure_mode']}** 问题，建议如下策略：

1. **设计层面**：{base}
2. **仿真验证**：热/结构/电路仿真验证余量。
3. **测试标准**：参考 IEC/GB，增加可靠性测试。

**RPN**：{risk.get('severity',0)} × {risk.get('occurrence',0)} × {risk.get('detection',0)} = **{risk.get('RPN',0)}**
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
    st.subheader("🌐 联网搜索配置")
    st.session_state.enable_web_search = st.checkbox("启用联网搜索", value=st.session_state.enable_web_search)
    st.markdown("---")
    st.subheader("🗄️ 数据库状态")
    db = st.session_state.database
    neo_available = hasattr(db, 'neo4j_available') and db.neo4j_available
    st.json({
        "当前模式": "混合数据库 (SQLite + Neo4j)",
        "Neo4j 连接": "✅ 已连接" if neo_available else "⚠️ 未连接（仅使用 SQLite）",
        "联网搜索": "启用" if st.session_state.enable_web_search else "禁用",
        "DeepSeek API": "已配置" if (st.session_state.temp_api_key or st.secrets.get("DEEPSEEK_API_KEY")) else "未配置",
    })
    st.markdown("---")
    st.subheader("📚 知识库管理")
    categories = ["光学", "机械", "材料", "热学", "电气", "控制"]
    selected_cat = st.selectbox("选择分类", categories)
    items = db.get_knowledge_by_category(selected_cat)
    st.write(f"共 {len(items)} 条记录")
    if items:
        with st.container(height=400):
            for idx, item in enumerate(items):
                col1, col2 = st.columns([10,1])
                with col1:
                    display_item = item[:150] + "..." if len(item) > 150 else item
                    st.write(f"{idx+1}. {display_item}")
                with col2:
                    if st.button("❌", key=f"del_{selected_cat}_{idx}"):
                        db.delete_knowledge(selected_cat, item)
                        st.rerun()
    else:
        st.info("暂无条目")
    new_item = st.text_area("添加新经验教训", height=100)
    if st.button("添加条目"):
        if new_item.strip():
            db.add_knowledge(selected_cat, new_item.strip())
            st.rerun()
    st.markdown("---")
    st.subheader("📥 导出/导入知识库（Excel）")
    if st.button("下载知识库模板 (Excel)"):
        all_knowledge = db.get_all_knowledge()
        export_data = {}
        for cat in categories:
            export_data[cat] = ["\n".join(all_knowledge.get(cat, []))] if all_knowledge.get(cat) else [""]
        df = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="知识库", index=False)
        st.download_button("下载 Excel 文件", data=output.getvalue(), file_name=f"knowledge_base_{datetime.now().strftime('%Y%m%d')}.xlsx")
    uploaded = st.file_uploader("上传 Excel 文件（覆盖）", type=["xlsx"])
    if uploaded:
        try:
            df = pd.read_excel(uploaded, sheet_name="知识库")
            for cat in categories:
                if cat in df.columns:
                    cell = df[cat].iloc[0]
                    if isinstance(cell, str) and cell.strip():
                        entries = [line.strip() for line in cell.split("\n") if line.strip()]
                        db.clear_knowledge_category(cat)
                        for entry in entries:
                            db.add_knowledge(cat, entry)
            st.success("知识库已更新")
            st.rerun()
        except Exception as e:
            st.error(f"导入失败: {e}")
    st.markdown("---")
    st.subheader("⚙️ LLM API 临时配置")
    new_key = st.text_input("DeepSeek API Key", value=st.session_state.temp_api_key, type="password")
    new_url = st.text_input("Base URL", value=st.session_state.temp_base_url)
    new_model = st.text_input("Model", value=st.session_state.temp_model)
    if st.button("应用临时配置"):
        st.session_state.temp_api_key = new_key
        st.session_state.temp_base_url = new_url
        st.session_state.temp_model = new_model
        st.rerun()

# ================== 右上角按钮 ==================
col_left, col_spacer, col_zh, col_en, col_gear = st.columns([5, 3, 1.2, 1.2, 1])
with col_zh:
    if st.button("🇨🇳 中文", key="zh_btn", use_container_width=True):
        st.session_state.lang = "zh"
        st.rerun()
with col_en:
    if st.button("🇬🇧 English", key="en_btn", use_container_width=True):
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
        "analyze_btn": "开始AI深度分析",
        "product_name_missing": "请填写产品名称",
        "generating": "AI 正在分析中，请稍候...",
        "decomposition_title": "📐 产品分解结果",
        "risks_title": "⚠️ Top 潜在风险 (按RPN排序)",
        "strategy_title": "💡 设计策略与缓解措施",
        "download_btn": "📎 导出风险表格 (CSV)",
        "footer": "© 2026 Laurence Ku | AI+DQA 风险分析",
        "no_risks": "未检索到风险数据，请检查产品类型或先加载基础数据。",
        "db_status": "数据库状态",
        "db_connected": "✅ 混合模式 (SQLite + Neo4j)",
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
        "analyze_btn": "Start AI Deep Analysis",
        "product_name_missing": "Please enter product name",
        "generating": "AI is analyzing, please wait...",
        "decomposition_title": "📐 Product Decomposition",
        "risks_title": "⚠️ Top Potential Risks (by RPN)",
        "strategy_title": "💡 Design Strategies & Mitigations",
        "download_btn": "📎 Export Risk Table (CSV)",
        "footer": "© 2026 Laurence Ku | AI+DQA Risk Analysis",
        "no_risks": "No risk data found. Please check product type or load base data first.",
        "db_status": "Database Status",
        "db_connected": "✅ Hybrid Mode (SQLite + Neo4j)",
    }
}

lang = st.session_state.lang
t = TEXTS[lang]

st.title(t["title"])

# 初始化全局数据库实例
if "database" not in st.session_state:
    st.session_state.database = get_database()
    st.session_state.database.load_initial_data()

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
    st.info(t["db_connected"])
    st.markdown("---")
    st.markdown(t["contact_info"])

# ================== 主界面（垂直布局） ==================
st.markdown(f"### {t['input_title']}")

# 产品名称
product_name = st.text_input(t["product_name"], placeholder=t["product_name_ph"])

# 设计描述（放在产品名称下方）
product_desc = st.text_area(t["product_desc"], placeholder=t["product_desc_ph"], height=100)

# 主分析按钮：居中、超大
col_center = st.columns([1, 2, 1])[1]
with col_center:
    st.markdown('<div class="main-analyze">', unsafe_allow_html=True)
    if st.button(t["analyze_btn"], key="main_analyze_btn", type="primary"):
        if not product_name:
            st.error(t["product_name_missing"])
        else:
            db = st.session_state.database
            with st.spinner(t["generating"]):
                # 获取侧边栏的分析人信息
                report = generate_ai_analysis(product_name, product_desc, st.session_state.enable_web_search, db, analyst_name, analyst_title)
                st.markdown("### 🤖 AI 生成的风险分析报告")
                st.markdown(report)
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")
st.caption(t["footer"])
