import streamlit as st
import pandas as pd
import json
import os
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Optional
import sqlite3
import openai

# ================== 页面配置 ==================
st.set_page_config(page_title="AI+DQA 风险分析系统", page_icon="🔍", layout="wide")

# ================== 初始化 Session State 和数据库 ==================
def init_db():
    """初始化 SQLite 数据库，创建表结构（如果不存在）"""
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    # 创建知识库表
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base
                 (category TEXT, content TEXT)''')
    # 创建产品风险表
    c.execute('''CREATE TABLE IF NOT EXISTS product_risks
                 (product_type TEXT, module TEXT, failure_mode TEXT, cause TEXT,
                  severity INTEGER, occurrence INTEGER, detection INTEGER,
                  mitigation TEXT)''')
    # 创建用户分析记录表（可选）
    c.execute('''CREATE TABLE IF NOT EXISTS analysis_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  product_name TEXT, product_desc TEXT, report TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def load_knowledge_from_db():
    """从数据库加载知识库"""
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("SELECT category, content FROM knowledge_base")
    rows = c.fetchall()
    conn.close()
    knowledge = {"光学": [], "机械": [], "热学": [], "电气": [], "控制": []}
    for category, content in rows:
        if category in knowledge:
            knowledge[category].append(content)
    return knowledge

def save_knowledge_to_db(category, content):
    """保存单条知识库条目到数据库"""
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("INSERT INTO knowledge_base (category, content) VALUES (?, ?)", (category, content))
    conn.commit()
    conn.close()

def delete_knowledge_from_db(category, content):
    """从数据库删除知识库条目"""
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM knowledge_base WHERE category = ? AND content = ?", (category, content))
    conn.commit()
    conn.close()

def clear_knowledge_category(category):
    """清空指定分类的所有知识库条目"""
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM knowledge_base WHERE category = ?", (category,))
    conn.commit()
    conn.close()

def load_product_risks_from_db():
    """从数据库加载产品风险数据"""
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
    """插入产品风险数据"""
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    c.execute('''INSERT INTO product_risks (product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (product_type, module, failure_mode, cause, severity, occurrence, detection, mitigation))
    conn.commit()
    conn.close()

# 初始化数据库和 session_state
init_db()

if "lang" not in st.session_state:
    st.session_state.lang = "zh"
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "knowledge_db" not in st.session_state:
    st.session_state.knowledge_db = load_knowledge_from_db()
if "product_risks_db" not in st.session_state:
    st.session_state.product_risks_db = load_product_risks_from_db()

# ================== 管理员凭证 ==================
ADMIN_USERNAME = "Laurence_ku"
ADMIN_PASSWORD = "Ku_product$2026"

# ================== DeepSeek AI 分析 ==================
def call_deepseek(prompt: str) -> str:
    """调用 DeepSeek API 生成报告"""
    try:
        client = openai.OpenAI(
            api_key=st.secrets["DEEPSEEK_API_KEY"],
            base_url=st.secrets.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        )
        response = client.chat.completions.create(
            model=st.secrets.get("DEEPSEEK_MODEL", "deepseek-chat"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=4000
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI 分析失败: {str(e)}。请检查 API 配置。"

def generate_ai_analysis(product_name: str, product_desc: str) -> str:
    """构建 prompt 并调用 AI 生成报告"""
    prompt = f"""
你是一位拥有25年经验的资深产品可靠性工程师。
请对以下产品进行风险分析，并以中文输出结果。

产品名称：{product_name}
设计描述：{product_desc}

请按照以下 Markdown 格式输出风险分析报告：

### 1. 产品分解
*   **功能件**: [根据产品描述推测其主要功能模块，如光学、电气、热学、机械、控制等]
*   **主要模块**: [列出3-5个核心模块，如LED光源、驱动电源、散热器、PCBA、传感器等]

### 2. Top 5 潜在风险
| 模块 | 失效模式 | 潜在原因 | 严重度(1-10) | 发生度(1-10) | 探测度(1-10) | RPN |
|------|----------|----------|--------------|--------------|--------------|-----|
| [模块名称] | [失效模式] | [潜在原因] | [数字] | [数字] | [数字] | [乘积] |
... (共5行)

### 3. 关键风险缓解策略
针对RPN最高的前3项风险，提供具体的设计建议：
*   **[风险1]**: [提供1-2句具体的解决方案，如选用更高规格的元件、优化散热结构等]
*   **[风险2]**: [提供1-2句具体的解决方案]
*   **[风险3]**: [提供1-2句具体的解决方案]

请确保分析结果专业、具体、可执行。
"""
    return call_deepseek(prompt)

# ================== 辅助函数 ==================
def generate_mitigation_strategy(risk_item: Dict) -> str:
    """基于知识库生成缓解策略"""
    # 这里可以增强逻辑，从 knowledge_db 中检索相关内容
    base_mitigation = risk_item.get("mitigation", "建议参考行业规范和设计指南进行优化。")
    strategy = f"""
针对 **{risk_item['module']}** 的 **{risk_item['failure_mode']}** 问题，建议如下策略：

1. **设计层面**：{base_mitigation}
2. **仿真验证**：通过热仿真/电路仿真验证设计余量。
3. **测试标准**：参考 IEC/GB 标准，增加可靠性测试。
4. **知识库参考**：本机知识库中可能有相关经验。

**RPN**：严重度 {risk_item['severity']} × 发生度 {risk_item['occurrence']} × 探测度 {risk_item['detection']} = **{risk_item['RPN']}**
"""
    return strategy

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
    st.markdown("## 📚 经验知识库管理")
    categories = ["光学", "机械", "热学", "电气", "控制"]
    selected_cat = st.selectbox("选择分类", categories)

    # 显示现有条目
    st.markdown(f"**{selected_cat} 分类现有条目：**")
    items = st.session_state.knowledge_db.get(selected_cat, [])
    if items:
        for idx, item in enumerate(items):
            col1, col2 = st.columns([10, 1])
            with col1:
                st.write(f"{idx+1}. {item}")
            with col2:
                if st.button("❌", key=f"del_{selected_cat}_{idx}"):
                    delete_knowledge_from_db(selected_cat, item)
                    st.session_state.knowledge_db = load_knowledge_from_db()
                    st.rerun()
    else:
        st.info("暂无条目")

    # 添加新条目
    new_item = st.text_area(f"添加新经验教训", height=100, placeholder="例如：LED路灯防水结构必须采用双重密封设计，避免IP等级虚标。")
    if st.button("添加条目"):
        if new_item.strip():
            save_knowledge_to_db(selected_cat, new_item.strip())
            st.session_state.knowledge_db = load_knowledge_from_db()
            st.success("已添加")
            st.rerun()

    st.markdown("---")
    st.subheader("📥 导出/导入知识库（Excel）")
    # 导出功能
    if st.button("下载知识库模板 (Excel)"):
        export_data = {}
        for cat in categories:
            export_data[cat] = ["\n".join(st.session_state.knowledge_db.get(cat, []))] if st.session_state.knowledge_db.get(cat) else [""]
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

    # 上传功能
    uploaded_file = st.file_uploader("上传 Excel 文件（覆盖现有知识库）", type=["xlsx"])
    if uploaded_file is not None:
        try:
            df_upload = pd.read_excel(uploaded_file, sheet_name="知识库")
            if all(cat in df_upload.columns for cat in categories):
                # 清空现有数据
                for cat in categories:
                    clear_knowledge_category(cat)
                # 导入新数据
                for cat in categories:
                    cell_value = df_upload[cat].iloc[0] if not df_upload[cat].isna().iloc[0] else ""
                    if isinstance(cell_value, str) and cell_value.strip():
                        items_list = [line.strip() for line in cell_value.split("\n") if line.strip()]
                        for item in items_list:
                            save_knowledge_to_db(cat, item)
                st.session_state.knowledge_db = load_knowledge_from_db()
                st.success("知识库已更新！")
                st.rerun()
            else:
                st.error("Excel 文件列名不正确，请使用下载的模板格式。")
        except Exception as e:
            st.error(f"读取文件失败：{e}")

    # 风险数据初始化（预置数据）
    st.markdown("---")
    st.subheader("⚙️ 初始化基础风险数据")
    if st.button("一键加载基础风险数据"):
        # LED 灯具
        insert_product_risk("LED路灯", "LED光源", "光衰过快", "结温过高", 8, 7, 5, "优化散热设计，选用优质灯珠")
        insert_product_risk("LED路灯", "驱动电源", "电容鼓包", "高温/纹波大", 9, 6, 6, "选用长寿命电容，降低纹波")
        insert_product_risk("LED路灯", "防水结构", "进水短路", "密封圈老化", 9, 4, 7, "双重密封，IP68测试")
        insert_product_risk("LED吸顶灯", "LED灯珠", "单颗死灯", "静电击穿/过流", 7, 5, 6, "ESD防护，恒流驱动")
        insert_product_risk("LED吸顶灯", "驱动电源", "频闪", "纹波过大/电路设计", 6, 6, 5, "增加输出滤波，满足IEEE 1789")
        insert_product_risk("LED吸顶灯", "弹簧卡扣", "断裂导致掉落", "金属疲劳/材料脆性", 8, 3, 4, "选用弹簧钢，疲劳测试")
        # 洗地机
        insert_product_risk("洗地机", "滚刷电机", "堵转烧毁", "毛发缠绕/异物卡滞", 8, 7, 6, "过流保护+防缠绕结构")
        insert_product_risk("洗地机", "水泵", "不出水/流量小", "堵塞/膜片老化", 7, 6, 5, "滤网+自清洁模式")
        insert_product_risk("洗地机", "电池包", "续航衰减", "电芯老化/BMS不均衡", 6, 8, 4, "选用A品电芯，均衡充电")
        # 吸尘器
        insert_product_risk("吸尘器", "电机", "吸力下降/异响", "滤网堵塞/轴承磨损", 7, 6, 5, "定期清理滤网，更换轴承")
        insert_product_risk("吸尘器", "电池", "续航不足/无法充电", "电芯老化/充电电路故障", 7, 6, 5, "使用原装充电器，避免过放")
        insert_product_risk("吸尘器", "尘盒密封", "漏尘", "密封圈老化/安装不到位", 5, 4, 3, "定期检查密封圈")
        # 宠物饮水机
        insert_product_risk("宠物饮水机", "水泵", "不出水/噪音大", "堵塞/叶轮磨损", 7, 6, 5, "定期清洗，更换水泵")
        insert_product_risk("宠物饮水机", "水位传感器", "误报缺水/溢水", "脏污/元件老化", 6, 5, 4, "定期清洁传感器")
        insert_product_risk("宠物饮水机", "密封圈", "漏水", "老化/破损", 8, 4, 6, "定期更换密封圈")
        # 宠物喂食器
        insert_product_risk("宠物喂食器", "出粮机构", "卡粮/不出粮", "粮食受潮/电机故障", 8, 5, 6, "保持粮食干燥，定期清理")
        insert_product_risk("宠物喂食器", "控制板", "程序异常/无法连接", "固件bug/网络问题", 7, 4, 5, "升级固件，检查网络")
        insert_product_risk("宠物喂食器", "电池", "断电后无备份", "电池老化/未安装", 6, 3, 4, "定期检查备用电池")
        # 刷新 session_state
        st.session_state.product_risks_db = load_product_risks_from_db()
        st.success("基础风险数据已加载！")
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
        "sidebar_basis": "本系统基于：",
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
        "error_prefix": "分析失败：",
        "decomposition_title": "📐 产品分解结果",
        "risks_title": "⚠️ Top 潜在风险 (按RPN排序)",
        "strategy_title": "💡 设计策略与缓解措施",
        "download_btn": "📎 导出风险表格 (CSV)",
        "back_btn": "← 返回重新填写",
        "footer": "© 2026 Laurence Ku | AI+DQA 风险分析 | 基于25年研发经验",
        "no_risks": "未检索到风险数据，请检查产品类型或先加载基础数据。",
    },
    "en": {
        "title": "🔍 AI+DQA Product Risk Analysis",
        "sidebar_title": "About",
        "sidebar_basis": "Based on:",
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
        "error_prefix": "Analysis failed: ",
        "decomposition_title": "📐 Product Decomposition",
        "risks_title": "⚠️ Top Potential Risks (by RPN)",
        "strategy_title": "💡 Design Strategies & Mitigations",
        "download_btn": "📎 Export Risk Table (CSV)",
        "back_btn": "← Back",
        "footer": "© 2026 Laurence Ku | AI+DQA Risk Analysis | 25+ years R&D",
        "no_risks": "No risk data found. Please check product type or load base data first.",
    }
}

lang = st.session_state.lang
t = TEXTS[lang]

st.title(t["title"])

# ================== 侧边栏（精简版） ==================
with st.sidebar:
    st.markdown(f"## {t['sidebar_title']}")
    st.markdown(t["sidebar_basis"])
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
    if "DEEPSEEK_API_KEY" in st.secrets and st.secrets["DEEPSEEK_API_KEY"]:
        st.success(t["api_configured"])
    else:
        st.error(t["api_not_configured"])
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
                ai_report = generate_ai_analysis(product_name, product_desc)
                st.markdown("### 🤖 AI 生成的风险分析报告")
                st.markdown(ai_report)
        else:
            # 快速分析：基于本地数据库
            # 简单的产品类型匹配
            product_type = "default"
            if any(keyword in product_name for keyword in ["路灯", "吸顶灯", "筒灯"]):
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
                # 简单模拟分解结果
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("产品", product_name)
                col_b.metric("功能件", "待分析")
                col_c.metric("主要模块", "待分析")

                st.subheader(t["risks_title"])
                df = pd.DataFrame(risks)
                display_cols = ["module", "failure_mode", "cause", "severity", "occurrence", "detection"]
                df["RPN"] = df["severity"] * df["occurrence"] * df["detection"]
                df = df.sort_values("RPN", ascending=False)
                st.dataframe(df[display_cols + ["RPN"]], use_container_width=True)

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
