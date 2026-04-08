import streamlit as st
import pandas as pd
import sqlite3
import re
import json
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Optional

# 可选依赖：如果未安装，则禁用相应功能
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from docx import Document
    from docx.shared import Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

# ================== 页面配置 ==================
st.set_page_config(page_title="AI+DQA 风险分析系统", page_icon="🔍", layout="wide")

# 自定义 CSS
st.markdown("""
<style>
    .stButton button:has(span:contains("中文")),
    .stButton button:has(span:contains("English")) {
        background-color: #ff4b4b !important;
        color: white !important;
        font-size: 16px !important;
        font-weight: bold !important;
        border-radius: 40px !important;
        padding: 0.5rem 1rem !important;
        min-width: 120px !important;
        white-space: nowrap !important;
    }
    .main-analyze button {
        font-size: 36px !important;
        padding: 20px 60px !important;
        background-color: #ff4b4b !important;
        color: white !important;
        border-radius: 60px !important;
        min-width: 400px !important;
        transition: all 0.3s ease;
    }
    .main-analyze button:hover {
        transform: scale(1.02);
        background-color: #e03a3a !important;
    }
    .report-card {
        background-color: #f8f9fa;
        padding: 1.5rem;
        border-radius: 12px;
        margin: 1rem 0;
    }
    .report-card table {
        width: 100%;
        border-collapse: collapse;
        margin: 1em 0;
    }
    .report-card th, .report-card td {
        border: 1px solid #ddd;
        padding: 8px;
        text-align: left;
    }
    .report-card th {
        background-color: #f2f2f2;
    }
</style>
""", unsafe_allow_html=True)

# ================== Session State 初始化 ==================
if "lang" not in st.session_state:
    st.session_state.lang = "zh"
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "temp_api_key" not in st.session_state:
    st.session_state.temp_api_key = ""
if "temp_base_url" not in st.session_state:
    st.session_state.temp_base_url = "https://api.deepseek.com"
if "temp_model" not in st.session_state:
    st.session_state.temp_model = "deepseek-chat"
if "analyst_name" not in st.session_state:
    st.session_state.analyst_name = ""
if "analyst_title" not in st.session_state:
    st.session_state.analyst_title = ""

# ================== 管理员凭证 ==================
ADMIN_USERNAME = "Laurence_ku"
ADMIN_PASSWORD = "Ku_product$2026"

# ================== SQLite 数据库（单库，无外部依赖） ==================
class RiskDatabase:
    def __init__(self):
        self.conn = sqlite3.connect('app_data.db', check_same_thread=False)
        self.init_tables()
        self.load_caches()

    def init_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS knowledge_base
                          (category TEXT, content TEXT, content_en TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_risks
                          (product_type TEXT, module TEXT, failure_mode TEXT, cause TEXT,
                           severity INTEGER, occurrence INTEGER, detection INTEGER, mitigation TEXT)''')
        cursor.execute("PRAGMA table_info(knowledge_base)")
        cols = [col[1] for col in cursor.fetchall()]
        if 'content_en' not in cols:
            cursor.execute("ALTER TABLE knowledge_base ADD COLUMN content_en TEXT")
        self.conn.commit()
        self._init_default_data()

    def _init_default_data(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM product_risks")
        if cursor.fetchone()[0] == 0:
            default_risks = [
                ("LED路灯", "LED光源", "光衰过快", "结温过高", 8, 7, 5, "优化散热设计，控制结温低于85°C"),
                ("LED路灯", "驱动电源", "电容鼓包", "高温", 9, 6, 6, "选用105°C长寿命电解电容"),
                ("洗地机", "滚刷电机", "堵转", "毛发缠绕", 8, 7, 6, "增加过流保护电路，设计防缠绕结构"),
                ("吸尘器", "电机", "吸力下降", "滤网堵塞", 7, 6, 5, "增加滤网堵塞报警和清理提醒"),
                ("宠物饮水机", "水泵", "噪音增大", "叶轮磨损", 6, 5, 4, "采用无刷陶瓷轴水泵"),
            ]
            for row in default_risks:
                cursor.execute("INSERT INTO product_risks VALUES (?,?,?,?,?,?,?,?)", row)
        self.conn.commit()

    def load_caches(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT category, content, content_en FROM knowledge_base")
        rows = cursor.fetchall()
        self.knowledge_zh = {"光学": [], "机械": [], "材料": [], "热学": [], "电气": [], "控制": []}
        self.knowledge_en = {"光学": [], "机械": [], "材料": [], "热学": [], "电气": [], "控制": []}
        for cat, zh, en in rows:
            if cat in self.knowledge_zh:
                self.knowledge_zh[cat].append(zh)
                self.knowledge_en[cat].append(en if en else zh)
        # 加载产品风险缓存
        cursor.execute("SELECT product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation FROM product_risks")
        self.product_risks = {}
        for row in cursor.fetchall():
            ptype = row[0]
            if ptype not in self.product_risks:
                self.product_risks[ptype] = []
            self.product_risks[ptype].append({
                "module": row[1], "failure_mode": row[2], "cause": row[3],
                "severity": row[4], "occurrence": row[5], "detection": row[6],
                "mitigation": row[7]
            })

    def get_risks(self, product_type: str) -> List[Dict]:
        # 模糊匹配产品类型
        matched = []
        for ptype, risks in self.product_risks.items():
            if product_type.lower() in ptype.lower() or ptype.lower() in product_type.lower():
                matched.extend(risks)
        if not matched:
            matched = self.product_risks.get(product_type, [])
        for r in matched:
            r["RPN"] = r["severity"] * r["occurrence"] * r["detection"]
        return sorted(matched, key=lambda x: x["RPN"], reverse=True)[:10]

    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        if "路灯" in product_name or "street light" in product_name.lower():
            return {"product_type": "LED路灯", "function_units": ["光学","电气","热学"], "modules": ["LED光源","驱动电源"]}
        elif "天棚灯" in product_name or "high bay" in product_name.lower():
            return {"product_type": "高功率天棚灯", "function_units": ["光学","电气","热学","控制"], "modules": ["COB光源","风扇","热管"]}
        else:
            return {"product_type": product_name, "function_units": ["电气","机械"], "modules": ["PCBA"]}

    def search_knowledge(self, keywords: str, limit: int = 5) -> List[str]:
        if not keywords.strip():
            return []
        cursor = self.conn.cursor()
        lang = st.session_state.lang
        if lang == "zh":
            cursor.execute("SELECT content FROM knowledge_base WHERE content LIKE ? LIMIT ?", (f"%{keywords}%", limit))
        else:
            cursor.execute("SELECT content_en FROM knowledge_base WHERE content_en LIKE ? LIMIT ?", (f"%{keywords}%", limit))
        return [row[0] for row in cursor.fetchall()]

    def get_knowledge_by_category(self, category: str) -> List[str]:
        lang = st.session_state.lang
        if lang == "zh":
            return self.knowledge_zh.get(category, [])
        else:
            return self.knowledge_en.get(category, [])

    def add_knowledge(self, category: str, content: str):
        # 简单双语存储（如果输入是中文，自动用相同内容作为英文；反之亦然）
        lang = st.session_state.lang
        if lang == "zh":
            zh, en = content, content
        else:
            zh, en = content, content
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO knowledge_base (category, content, content_en) VALUES (?, ?, ?)",
                       (category, zh, en))
        self.conn.commit()
        self.load_caches()

    def delete_knowledge(self, category: str, content: str):
        cursor = self.conn.cursor()
        lang = st.session_state.lang
        if lang == "zh":
            cursor.execute("DELETE FROM knowledge_base WHERE category = ? AND content = ?", (category, content))
        else:
            cursor.execute("DELETE FROM knowledge_base WHERE category = ? AND content_en = ?", (category, content))
        self.conn.commit()
        self.load_caches()

    def clear_knowledge_category(self, category: str):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM knowledge_base WHERE category = ?", (category,))
        self.conn.commit()
        self.load_caches()

    def get_all_knowledge(self) -> Dict[str, List[str]]:
        lang = st.session_state.lang
        return self.knowledge_zh if lang == "zh" else self.knowledge_en

# ================== AI 调用 ==================
def get_openai_client():
    if not OPENAI_AVAILABLE:
        return None, "未安装 openai 库，请运行: pip install openai"
    api_key = st.session_state.temp_api_key
    base_url = st.session_state.temp_base_url
    if not api_key:
        return None, "未配置 API Key"
    return openai.OpenAI(api_key=api_key, base_url=base_url), None

def call_deepseek(prompt: str, max_tokens=4000) -> str:
    client, error = get_openai_client()
    if error:
        return f"AI 调用失败: {error}"
    try:
        model = st.session_state.temp_model
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI 调用失败: {str(e)}"

def clean_ai_response(text: str, lang: str = "zh") -> str:
    patterns_zh = [r'^好的[，,].*?\n', r'^作为一名资深可靠性工程师.*?\n', r'^基于以上提供的信息.*?\n']
    patterns_en = [r'^Okay[,.]?\s*\n', r'^As a senior reliability engineer.*?\n', r'^Based on the above information.*?\n']
    for pat in (patterns_zh if lang=="zh" else patterns_en):
        text = re.sub(pat, '', text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()

# ================== 生成 AI 分析内容 ==================
def generate_ai_analysis(product_name: str, product_desc: str, db: RiskDatabase, lang: str) -> str:
    # 获取产品分解，用于匹配风险
    decomp = db.get_product_decomposition(product_name, product_desc)
    product_type = decomp.get("product_type", product_name)
    # 检索知识库
    kb_items = db.search_knowledge(f"{product_name} {product_desc}", limit=8)
    kb_text = "\n".join(kb_items) if kb_items else ("暂无相关经验知识" if lang=="zh" else "No relevant knowledge found")
    # 获取风险数据库中的风险
    risks = db.get_risks(product_type)
    risk_table_rows = []
    for r in risks[:5]:
        risk_table_rows.append(f"| {r['module']} | {r['failure_mode']} | {r['cause']} | {r['severity']} | {r['occurrence']} | {r['detection']} | {r['RPN']} |")
    risk_table = "\n".join(risk_table_rows) if risk_table_rows else ("| - | - | - | - | - | - | - |" if lang=="zh" else "| - | - | - | - | - | - | - |")
    if lang == "zh":
        prompt = f"""
你是一位资深可靠性工程师。请根据以下信息对产品进行风险分析。

产品名称：{product_name}
设计描述：{product_desc}

=== 企业内部知识库 ===
{kb_text}

=== 产品风险数据库（匹配类型：{product_type}） ===
{risk_table}

请直接输出风险分析报告，不要添加任何开场白。报告必须包含以下三个部分（使用 Markdown 格式）：
### 1. 产品分解
（根据产品名称和设计描述，分解出主要功能单元和关键模块）

### 2. Top 5 潜在风险
（用表格形式，列：模块、失效模式、原因、严重度、发生度、探测度、RPN）

### 3. 关键风险缓解策略
（针对RPN最高的3项风险，给出具体的改进建议）

注意：表格中不要使用加粗符号（**）。
"""
    else:
        prompt = f"""
You are a senior reliability engineer. Conduct a risk analysis based on the information below.

Product Name: {product_name}
Design Description: {product_desc}

=== Internal Knowledge Base ===
{kb_text}

=== Product Risk Database (matched type: {product_type}) ===
{risk_table}

Output the report directly, no preamble. The report MUST include exactly the following three sections (Markdown format):
### 1. Product Decomposition
### 2. Top 5 Potential Risks (Table: Module, Failure Mode, Cause, Severity, Occurrence, Detection, RPN)
### 3. Key Risk Mitigation Strategies (for the top 3 risks by RPN)

Do not use ** in the table.
"""
    raw = call_deepseek(prompt, max_tokens=4000)
    return clean_ai_response(raw, lang)

# ================== Word 报告生成 ==================
def markdown_to_docx(md_text: str, doc):
    lines = md_text.split('\n')
    i = 0
    in_table = False
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('# '):
            doc.add_heading(line[2:], level=1)
            i += 1
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=2)
            i += 1
        elif line.startswith('### '):
            doc.add_heading(line[4:], level=3)
            i += 1
        elif line.startswith('|') and not in_table:
            in_table = True
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            if len(table_lines) >= 2:
                header_cells = [cell.strip() for cell in table_lines[0].split('|')[1:-1]]
                if '---' in table_lines[1]:
                    data_lines = table_lines[2:]
                else:
                    data_lines = table_lines[1:]
                num_cols = len(header_cells)
                if num_cols > 0 and data_lines:
                    table = doc.add_table(rows=1+len(data_lines), cols=num_cols)
                    table.style = 'Table Grid'
                    for col, cell_text in enumerate(header_cells):
                        table.cell(0, col).text = cell_text
                    for row_idx, data_line in enumerate(data_lines):
                        cells = [cell.strip() for cell in data_line.split('|')[1:-1]]
                        for col_idx, cell_text in enumerate(cells):
                            if col_idx < num_cols:
                                table.cell(row_idx+1, col_idx).text = cell_text
                    doc.add_paragraph()
            in_table = False
        elif line:
            doc.add_paragraph(line)
        else:
            doc.add_paragraph()
            i += 1

def generate_word_report(product_name: str, product_desc: str, analyst_name: str, analyst_title: str, report_content: str, lang: str) -> BytesIO:
    if not DOCX_AVAILABLE:
        st.error("python-docx 未安装，无法生成 Word 报告")
        return BytesIO()
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
    if lang == "en":
        title_text = "AI-Enabled DQA Product Design Risk Analysis Report"
        url_label = "Report online address:"
        labels = {"product_name": "Product Name", "design_desc": "Design Description", "date": "Report Date", "analyst": "Analyst"}
    else:
        title_text = "AI赋能DQA-产品设计风险分析报告"
        url_label = "报告在线地址："
        labels = {"product_name": "产品名称", "design_desc": "设计描述", "date": "报告日期", "analyst": "分析人"}
    title = doc.add_heading(title_text, level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(url_label).add_run("https://ai-app-design-dfmea.streamlit.app/").italic = True
    doc.add_paragraph()
    info_table = doc.add_table(rows=4, cols=2)
    info_table.style = 'Table Grid'
    info_table.cell(0,0).text = labels["product_name"]
    info_table.cell(0,1).text = product_name
    info_table.cell(1,0).text = labels["design_desc"]
    info_table.cell(1,1).text = product_desc
    info_table.cell(2,0).text = labels["date"]
    info_table.cell(2,1).text = datetime.now().strftime("%Y-%m-%d")
    analyst_str = analyst_name if analyst_name else ("未填写" if lang=="zh" else "Not filled")
    if analyst_title:
        analyst_str += f" ({analyst_title})"
    info_table.cell(3,0).text = labels["analyst"]
    info_table.cell(3,1).text = analyst_str
    doc.add_paragraph()
    markdown_to_docx(report_content, doc)
    doc_bytes = BytesIO()
    doc.save(doc_bytes)
    doc_bytes.seek(0)
    return doc_bytes

# ================== 管理员设置弹窗 ==================
@st.dialog("管理员设置", width="large")
def admin_settings_dialog():
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
    st.subheader("🌐 功能配置")
    st.session_state.enable_web_search = st.checkbox("启用联网搜索（需要安装 duckduckgo-search）", value=False)
    st.markdown("---")
    st.subheader("🗄️ 数据库状态")
    st.json({
        "数据库模式": "SQLite (本地文件)",
        "知识库分类": ["光学","机械","材料","热学","电气","控制"],
        "产品风险条目": len(st.session_state.database.product_risks),
    })
    st.markdown("---")
    st.subheader("📚 知识库管理（双语）")
    categories = ["光学", "机械", "材料", "热学", "电气", "控制"]
    selected_cat = st.selectbox("选择分类", categories)
    items = st.session_state.database.get_knowledge_by_category(selected_cat)
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
                        st.session_state.database.delete_knowledge(selected_cat, item)
                        st.rerun()
    else:
        st.info("暂无条目")
    new_item = st.text_area("添加新经验教训", height=100)
    if st.button("添加条目") and new_item.strip():
        st.session_state.database.add_knowledge(selected_cat, new_item.strip())
        st.rerun()
    st.markdown("---")
    st.subheader("📥 导出/导入知识库（Excel）")
    if st.button("下载知识库模板 (Excel)"):
        all_knowledge = st.session_state.database.get_all_knowledge()
        max_len = max((len(all_knowledge.get(cat, [])) for cat in categories), default=0)
        export_data = {}
        for cat in categories:
            items = all_knowledge.get(cat, [])
            export_data[cat] = items + [''] * (max_len - len(items))
        df = pd.DataFrame(export_data)
        df.columns = ["光学 / Optical", "机械 / Mechanical", "材料 / Material", "热学 / Thermal", "电气 / Electrical", "控制 / Control"]
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="知识库", index=False)
        st.download_button(label="下载 Excel 文件", data=output.getvalue(), file_name=f"knowledge_base_{datetime.now().strftime('%Y%m%d')}.xlsx")
    uploaded = st.file_uploader("上传 Excel 文件（覆盖）", type=["xlsx"])
    if uploaded:
        try:
            df = pd.read_excel(uploaded, sheet_name="知识库")
            mapping = {
                "光学 / Optical": "光学", "机械 / Mechanical": "机械", "材料 / Material": "材料",
                "热学 / Thermal": "热学", "电气 / Electrical": "电气", "控制 / Control": "控制",
                "光学": "光学", "机械": "机械", "材料": "材料", "热学": "热学", "电气": "电气", "控制": "控制"
            }
            for cat in categories:
                st.session_state.database.clear_knowledge_category(cat)
            for excel_col, cat in mapping.items():
                if excel_col in df.columns:
                    for item in df[excel_col].dropna().astype(str).str.strip():
                        if item:
                            st.session_state.database.add_knowledge(cat, item)
            st.success(f"知识库已更新！")
            st.rerun()
        except Exception as e:
            st.error(f"导入失败：{e}")
    st.markdown("---")
    st.subheader("⚙️ LLM API 配置")
    new_key = st.text_input("DeepSeek API Key", value=st.session_state.temp_api_key, type="password")
    new_url = st.text_input("Base URL", value=st.session_state.temp_base_url)
    new_model = st.text_input("Model", value=st.session_state.temp_model)
    if st.button("应用配置"):
        st.session_state.temp_api_key = new_key
        st.session_state.temp_base_url = new_url
        st.session_state.temp_model = new_model
        st.rerun()

# ================== 右上角按钮 ==================
col_left, col_spacer, col_zh, col_en, col_gear = st.columns([5, 2, 1.8, 1.8, 1])
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
        "basis_items": ["25+年研发管理经验", "AI大模型数据分析", "DFSS/六西格玛方法论"],
        "analyst_name_label": "分析人姓名", "analyst_name_ph": "请输入姓名",
        "analyst_title_label": "分析人头衔（可选）", "analyst_title_ph": "例如：研发总监",
        "api_status": "DeepSeek API 状态",
        "api_configured": "✅ 已配置", "api_not_configured": "❌ 未配置",
        "contact_info": "📞 **联系：**  \n✉️ 电邮: Techlife2027@gmail.com",
        "input_title": "📝 产品风险分析",
        "product_name": "产品名称", "product_name_ph": "例如：高功率LED天棚灯",
        "product_desc": "设计描述", "product_desc_ph": "例如：200W COB光源，主动风扇散热，IP65",
        "analyze_btn": "开始AI深度分析",
        "product_name_missing": "请填写产品名称",
        "generating": "AI 正在分析中，请稍候...",
        "footer": "© 2026 Laurence Ku | AI+DQA 风险分析",
        "db_status": "数据库状态",
        "db_connected": "✅ SQLite 本地数据库",
    },
    "en": {
        "title": "🔍 AI+DQA Product Risk Analysis",
        "sidebar_title": "About",
        "basis_items": ["25+ years R&D", "AI big data", "DFSS/Six Sigma"],
        "analyst_name_label": "Analyst Name", "analyst_name_ph": "Enter name",
        "analyst_title_label": "Title (Optional)", "analyst_title_ph": "e.g., R&D Director",
        "api_status": "DeepSeek API Status",
        "api_configured": "✅ Configured", "api_not_configured": "❌ Not configured",
        "contact_info": "📞 **Contact:**  \n✉️ Email: Techlife2027@gmail.com",
        "input_title": "📝 Product Risk Analysis",
        "product_name": "Product Name", "product_name_ph": "e.g., High Bay LED Light",
        "product_desc": "Design Description", "product_desc_ph": "e.g., 200W COB, active fan cooling, IP65",
        "analyze_btn": "Start AI Deep Analysis",
        "product_name_missing": "Please enter product name",
        "generating": "AI is analyzing, please wait...",
        "footer": "© 2026 Laurence Ku | AI+DQA Risk Analysis",
        "db_status": "Database Status",
        "db_connected": "✅ SQLite Local DB",
    }
}

lang = st.session_state.lang
t = TEXTS[lang]
st.title(t["title"])

# 初始化数据库
if "database" not in st.session_state:
    st.session_state.database = RiskDatabase()

# ================== 侧边栏 ==================
with st.sidebar:
    st.markdown(f"## {t['sidebar_title']}")
    for item in t["basis_items"]:
        st.markdown(f"- {item}")
    st.markdown("---")
    analyst_name_input = st.text_input(t["analyst_name_label"], placeholder=t["analyst_name_ph"], key="analyst_name_input")
    analyst_title_input = st.text_input(t["analyst_title_label"], placeholder=t["analyst_title_ph"], key="analyst_title_input")
    st.session_state.analyst_name = analyst_name_input
    st.session_state.analyst_title = analyst_title_input
    if analyst_name_input:
        st.markdown(f"**{t['analyst_name_label']}: {analyst_name_input}**")
        if analyst_title_input:
            st.markdown(f"_{analyst_title_input}_")
    st.markdown("---")
    st.markdown(f"**{t['api_status']}**")
    has_api = bool(st.session_state.temp_api_key)
    if has_api:
        st.success(t["api_configured"])
        st.caption(f"模型: {st.session_state.temp_model}")
    else:
        st.error(t["api_not_configured"])
        st.caption("请在管理员设置中配置 API Key")
    st.markdown("---")
    st.markdown(f"**{t['db_status']}**")
    st.info(t["db_connected"])
    st.markdown("---")
    st.markdown(t["contact_info"])

# ================== 主界面 ==================
st.markdown(f"### {t['input_title']}")
product_name = st.text_input(t["product_name"], placeholder=t["product_name_ph"])
product_desc = st.text_area(t["product_desc"], placeholder=t["product_desc_ph"], height=100)

col_center = st.columns([1, 2, 1])[1]
with col_center:
    st.markdown('<div class="main-analyze">', unsafe_allow_html=True)
    if st.button(t["analyze_btn"], key="main_analyze_btn", type="primary"):
        if not product_name:
            st.error(t["product_name_missing"])
        else:
            with st.spinner(t["generating"]):
                report_content = generate_ai_analysis(product_name, product_desc, st.session_state.database, lang)
                # 组合显示内容
                analyst_info = ""
                if st.session_state.analyst_name:
                    analyst_info = f"**{t['analyst_name_label']}:** {st.session_state.analyst_name}"
                    if st.session_state.analyst_title:
                        analyst_info += f" ({st.session_state.analyst_title})"
                    analyst_info += "\n\n"
                disclaimer = "> *此报告是基于有限信息生成的初步分析，仅供参考。*" if lang=="zh" else "> *This report is a preliminary analysis based on limited information, for reference only.*"
                full_display = f"{analyst_info}{disclaimer}\n\n{report_content}"
                st.markdown("---")
                st.markdown('<div class="report-card">', unsafe_allow_html=True)
                st.markdown("### " + ("AI赋能DQA-产品设计风险分析报告" if lang=="zh" else "AI-Enabled DQA Product Design Risk Analysis Report"))
                st.markdown(full_display)
                st.markdown('</div>', unsafe_allow_html=True)
                # 提供 Word 下载
                if report_content and DOCX_AVAILABLE:
                    word_bytes = generate_word_report(
                        product_name, product_desc,
                        st.session_state.analyst_name, st.session_state.analyst_title,
                        report_content, lang
                    )
                    file_name = f"{product_name}_风险分析报告_{datetime.now().strftime('%Y%m%d')}.docx" if lang=="zh" else f"{product_name}_Risk_Analysis_Report_{datetime.now().strftime('%Y%m%d')}.docx"
                    st.download_button(label="📥 下载 Word 报告" if lang=="zh" else "📥 Download Word Report", data=word_bytes, file_name=file_name, mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                elif not DOCX_AVAILABLE:
                    st.info("如需 Word 报告，请安装 python-docx: pip install python-docx")
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")
st.caption(t["footer"])
