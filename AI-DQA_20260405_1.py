import streamlit as st
import pandas as pd
import json
import os
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Optional

# ================== 页面配置 ==================
st.set_page_config(page_title="AI+DQA 风险分析系统", page_icon="🔍", layout="wide")

# ================== 初始化 session_state ==================
if "lang" not in st.session_state:
    st.session_state.lang = "zh"
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "knowledge_db" not in st.session_state:
    # 经验知识库结构：分类 + 经验条目
    st.session_state.knowledge_db = {
        "光学": [],
        "机械": [],
        "热学": [],
        "电气": [],
        "控制": []
    }

# ================== 管理员凭证 ==================
ADMIN_USERNAME = "Laurence_ku"
ADMIN_PASSWORD = "Ku_product$2026"

# ================== 模拟风险数据库（Mock） ==================
class RiskDatabase:
    def get_risks(self, product_type: str) -> List[Dict]:
        # 模拟风险数据
        risks = {
            "LED路灯": [
                {"module": "LED光源", "failure_mode": "光衰过快", "cause": "结温过高", "severity": 8, "occurrence": 7, "detection": 5, "mitigation": "优化散热设计"},
                {"module": "驱动电源", "failure_mode": "电容鼓包", "cause": "高温/纹波大", "severity": 9, "occurrence": 6, "detection": 6, "mitigation": "选用长寿命电容"},
                {"module": "防水结构", "failure_mode": "进水短路", "cause": "密封圈老化", "severity": 9, "occurrence": 4, "detection": 7, "mitigation": "双重密封"},
            ],
            "高功率天棚灯": [
                {"module": "COB光源", "failure_mode": "死灯", "cause": "过温/过流", "severity": 9, "occurrence": 6, "detection": 5, "mitigation": "降额使用"},
                {"module": "风扇", "failure_mode": "停转", "cause": "轴承磨损", "severity": 8, "occurrence": 7, "detection": 6, "mitigation": "双风扇冗余"},
                {"module": "热管", "failure_mode": "效率下降", "cause": "工质泄漏", "severity": 7, "occurrence": 3, "detection": 5, "mitigation": "真空检漏"},
            ],
            "default": [
                {"module": "PCBA", "failure_mode": "虚焊", "cause": "工艺不良", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "AOI检测"},
            ]
        }
        data = risks.get(product_type, risks["default"])
        for r in data:
            r["RPN"] = r["severity"] * r["occurrence"] * r["detection"]
        return sorted(data, key=lambda x: x["RPN"], reverse=True)[:10]

    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        if "路灯" in product_name:
            return {"product_type": "LED路灯", "function_units": ["光学","电气","热学"], "modules": ["LED光源","驱动电源"]}
        elif "天棚灯" in product_name:
            return {"product_type": "高功率天棚灯", "function_units": ["光学","电气","热学","控制"], "modules": ["COB光源","风扇","热管"]}
        else:
            return {"product_type": "default", "function_units": ["电气","机械"], "modules": ["PCBA"]}

    def get_mitigation(self, module: str, failure_mode: str) -> str:
        # 可从经验库中检索，简化返回通用
        return f"针对 {module} 的 {failure_mode}，建议参考设计规范并加强测试。"

db = RiskDatabase()

# ================== 辅助函数 ==================
def generate_mitigation_strategy(risk_item: Dict) -> str:
    base = db.get_mitigation(risk_item["module"], risk_item["failure_mode"])
    strategy = f"""
针对 **{risk_item['module']}** 的 **{risk_item['failure_mode']}** 问题（原因：{risk_item['cause']}），建议如下策略：

1. **设计层面**：{base}
2. **仿真验证**：热/结构/电路仿真，验证设计余量。
3. **测试标准**：参考 IEC/GB，增加 HALT/HASS。
4. **制程管控**：关键工艺 SPC 监控。
5. **售后闭环**：建立失效分析数据库。

**RPN**：严重度 {risk_item['severity']} × 发生度 {risk_item['occurrence']} × 探测度 {risk_item['detection']} = **{risk_item['RPN']}**
"""
    return strategy

# ================== 管理员设置弹窗（包含经验知识库管理） ==================
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
        return  # 未登录不显示其他内容

    # 已登录显示完整设置
    st.success("管理员已登录")
    
    # ----- 经验知识库管理 -----
    st.markdown("## 📚 经验知识库管理")
    st.markdown("按分类记录过往的设计教训、失效案例、最佳实践等。")
    
    # 选择分类
    categories = ["光学", "机械", "热学", "电气", "控制"]
    selected_cat = st.selectbox("选择分类", categories)
    
    # 显示当前分类下的条目
    st.markdown(f"**{selected_cat} 分类现有条目：**")
    items = st.session_state.knowledge_db[selected_cat]
    if items:
        for idx, item in enumerate(items):
            col1, col2 = st.columns([10, 1])
            with col1:
                st.write(f"{idx+1}. {item}")
            with col2:
                if st.button("❌", key=f"del_{selected_cat}_{idx}"):
                    st.session_state.knowledge_db[selected_cat].pop(idx)
                    st.rerun()
    else:
        st.info("暂无条目")
    
    # 添加新条目
    new_item = st.text_area(f"添加新经验教训（{selected_cat}）", height=100, placeholder="例如：LED路灯防水结构必须采用双重密封设计，避免IP等级虚标。")
    if st.button("添加条目"):
        if new_item.strip():
            st.session_state.knowledge_db[selected_cat].append(new_item.strip())
            st.success("已添加")
            st.rerun()
    
    st.markdown("---")
    st.subheader("📥 导出/导入知识库（Excel）")
    # 导出功能：将所有分类数据转为 Excel
    if st.button("下载知识库模板 (Excel)"):
        # 构建 DataFrame，每个分类一列，每个单元格存储该分类的所有条目（换行分隔）
        export_data = {}
        for cat in categories:
            export_data[cat] = ["\n".join(st.session_state.knowledge_db[cat])] if st.session_state.knowledge_db[cat] else [""]
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
    
    # 上传功能：上传 Excel 后替换当前知识库
    uploaded_file = st.file_uploader("上传 Excel 文件（覆盖现有知识库）", type=["xlsx"])
    if uploaded_file is not None:
        try:
            df_upload = pd.read_excel(uploaded_file, sheet_name="知识库")
            # 检查列名是否匹配
            if all(cat in df_upload.columns for cat in categories):
                for cat in categories:
                    cell_value = df_upload[cat].iloc[0] if not df_upload[cat].isna().iloc[0] else ""
                    if isinstance(cell_value, str) and cell_value.strip():
                        items_list = [line.strip() for line in cell_value.split("\n") if line.strip()]
                        st.session_state.knowledge_db[cat] = items_list
                    else:
                        st.session_state.knowledge_db[cat] = []
                st.success("知识库已更新！")
                st.rerun()
            else:
                st.error("Excel 文件列名不正确，请使用下载的模板格式。")
        except Exception as e:
            st.error(f"读取文件失败：{e}")

    # 可选：显示所有分类的预览
    with st.expander("查看全部知识库内容"):
        for cat in categories:
            st.markdown(f"**{cat}**")
            for item in st.session_state.knowledge_db[cat]:
                st.write(f"- {item}")
    
    st.markdown("---")
    st.subheader("⚙️ 其他设置（预留）")
    st.info("后续可扩展：数据库连接配置、API 密钥等。")

# ================== 右上角按钮（中英文 + 齿轮） ==================
# 使用 columns 并设置比例，使按钮宽度适中
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
        "api_status": "AI API 状态",
        "api_configured": "✅ 已配置",
        "api_not_configured": "❌ 未配置",
        "contact_info": "📞 **联系：**  \n✉️ 电邮: Techlife2027@gmail.com",
        "input_title": "📝 产品风险分析",
        "product_name": "产品名称",
        "product_name_ph": "例如：高功率LED天棚灯",
        "product_desc": "设计描述",
        "product_desc_ph": "例如：200W COB光源，主动风扇散热，IP65",
        "analyze_btn": "🚀 开始风险分析",
        "product_name_missing": "请填写产品名称",
        "generating": "分析中，请稍候...",
        "decomposition_title": "📐 产品分解结果",
        "risks_title": "⚠️ Top 10 潜在风险 (按RPN排序)",
        "strategy_title": "💡 设计策略与缓解措施",
        "download_btn": "📎 导出风险表格 (CSV)",
        "back_btn": "← 返回重新填写",
        "footer": "© 2026 Laurence Ku | AI+DQA 风险分析 | 基于25年研发经验",
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
        "api_status": "AI API Status",
        "api_configured": "✅ Configured",
        "api_not_configured": "❌ Not configured",
        "contact_info": "📞 **Contact:**  \n✉️ Email: Techlife2027@gmail.com",
        "input_title": "📝 Product Risk Analysis",
        "product_name": "Product Name",
        "product_name_ph": "e.g., High Bay LED Light",
        "product_desc": "Design Description",
        "product_desc_ph": "e.g., 200W COB, active fan cooling, IP65",
        "analyze_btn": "🚀 Start Analysis",
        "product_name_missing": "Please enter product name",
        "generating": "Analyzing, please wait...",
        "decomposition_title": "📐 Product Decomposition",
        "risks_title": "⚠️ Top 10 Potential Risks (by RPN)",
        "strategy_title": "💡 Design Strategies & Mitigations",
        "download_btn": "📎 Export Risk Table (CSV)",
        "back_btn": "← Back",
        "footer": "© 2026 Laurence Ku | AI+DQA Risk Analysis | 25+ years R&D",
    }
}

lang = st.session_state.lang
t = TEXTS[lang]

st.title(t["title"])

# ================== 侧边栏（精简版，移除授权码和购买） ==================
with st.sidebar:
    st.markdown(f"## {t['sidebar_title']}")
    st.markdown(t["sidebar_basis"])
    for item in t["basis_items"]:
        st.markdown(f"- {item}")
    st.markdown("---")
    
    # 分析人信息
    analyst_name = st.text_input(t["analyst_name_label"], placeholder=t["analyst_name_ph"])
    analyst_title = st.text_input(t["analyst_title_label"], placeholder=t["analyst_title_ph"])
    if analyst_name:
        st.markdown(f"**{t['analyst_name_label']}: {analyst_name}**")
        if analyst_title:
            st.markdown(f"_{analyst_title}_")
    st.markdown("---")
    
    # API 状态（简化，不实际调用，仅显示）
    st.markdown(f"**{t['api_status']}**")
    # 为了演示，假设备置了（实际可检查 st.secrets 中是否有 key）
    st.success(t["api_configured"])
    st.markdown("---")
    st.markdown(t["contact_info"])

# ================== 主界面 ==================
st.markdown(f"### {t['input_title']}")
col1, col2 = st.columns(2)
with col1:
    product_name = st.text_input(t["product_name"], placeholder=t["product_name_ph"])
with col2:
    product_desc = st.text_area(t["product_desc"], placeholder=t["product_desc_ph"], height=100)

if st.button(t["analyze_btn"], type="primary", use_container_width=True):
    if not product_name:
        st.error(t["product_name_missing"])
    else:
        with st.spinner(t["generating"]):
            # 调用风险分析
            decomposition = db.get_product_decomposition(product_name, product_desc)
            risks = db.get_risks(decomposition.get("product_type", "default"))
            
            st.subheader(t["decomposition_title"])
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("产品", product_name)
            col_b.metric("功能件", ", ".join(decomposition.get("function_units", [])))
            col_c.metric("主要模块", ", ".join(decomposition.get("modules", [])[:3]))
            
            st.subheader(t["risks_title"])
            if risks:
                df = pd.DataFrame(risks)
                display_cols = ["module", "failure_mode", "cause", "severity", "occurrence", "detection", "RPN"]
                st.dataframe(df[display_cols], use_container_width=True)
                
                st.subheader(t["strategy_title"])
                for idx, risk in enumerate(risks):
                    with st.expander(f"{idx+1}. {risk['module']} - {risk['failure_mode']} (RPN={risk['RPN']})"):
                        strategy = generate_mitigation_strategy(risk)
                        st.markdown(strategy)
                
                # 导出 CSV
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(t["download_btn"], data=csv, file_name=f"{product_name}_risks.csv", mime="text/csv")
            else:
                st.warning("未检索到风险数据，请检查产品类型。")

st.markdown("---")
st.caption(t["footer"])
