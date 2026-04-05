import streamlit as st
import openai
import json
import os
import re
import secrets
import string
import time
import stripe
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# ================== 多数据库抽象层（可扩展） ==================
class RiskDatabase:
    def get_risks(self, product_type: str) -> List[Dict]:
        raise NotImplementedError
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        raise NotImplementedError
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        raise NotImplementedError

class MockDatabase(RiskDatabase):
    def __init__(self):
        self.product_risks = {
            "LED路灯": {
                "risks": [
                    {"module": "LED光源", "failure_mode": "光衰过快", "cause": "结温过高", "severity": 8, "occurrence": 7, "detection": 5, "mitigation": "优化散热设计，选用优质灯珠"},
                    {"module": "驱动电源", "failure_mode": "电容鼓包", "cause": "高温/纹波大", "severity": 9, "occurrence": 6, "detection": 6, "mitigation": "选用长寿命电容，降低纹波"},
                    {"module": "防水结构", "failure_mode": "进水短路", "cause": "密封圈老化", "severity": 9, "occurrence": 4, "detection": 7, "mitigation": "双重密封，IP68测试"},
                ]
            },
            "高功率天棚灯": {
                "risks": [
                    {"module": "COB光源", "failure_mode": "死灯", "cause": "过温/过流", "severity": 9, "occurrence": 6, "detection": 5, "mitigation": "降额使用，热仿真优化"},
                    {"module": "风扇", "failure_mode": "停转", "cause": "轴承磨损", "severity": 8, "occurrence": 7, "detection": 6, "mitigation": "双风扇冗余，转速监控"},
                ]
            },
            "default": {
                "risks": [
                    {"module": "PCBA", "failure_mode": "虚焊", "cause": "工艺不良", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "AOI检测，工艺优化"},
                ]
            }
        }
    def get_risks(self, product_type: str) -> List[Dict]:
        risks = self.product_risks.get(product_type, self.product_risks["default"])["risks"]
        for r in risks:
            r["RPN"] = r["severity"] * r["occurrence"] * r["detection"]
        return sorted(risks, key=lambda x: x["RPN"], reverse=True)[:10]
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        if "路灯" in product_name:
            return {"product_type": "LED路灯", "function_units": ["光学","电气","热学"], "modules": ["LED光源","驱动电源"]}
        elif "天棚灯" in product_name:
            return {"product_type": "高功率天棚灯", "function_units": ["光学","电气","热学","控制"], "modules": ["COB光源","风扇"]}
        else:
            return {"product_type": "default", "function_units": ["电气","机械"], "modules": ["PCBA"]}
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        return f"针对 {module} 的 {failure_mode}，建议：设计优化 + 仿真验证 + 可靠性测试。"

# 可扩展 Neo4j / PostgreSQL / Pinecone，这里先保留接口
class Neo4jDatabase(RiskDatabase):
    def __init__(self, uri, user, password):
        # 实际连接代码略，演示时降级到Mock
        self.mock = MockDatabase()
    def get_risks(self, product_type): return self.mock.get_risks(product_type)
    def get_product_decomposition(self, product_name, description): return self.mock.get_product_decomposition(product_name, description)
    def get_mitigation(self, module, failure_mode): return self.mock.get_mitigation(module, failure_mode)

class PostgresDatabase(RiskDatabase):
    def __init__(self, host, port, db, user, password):
        self.mock = MockDatabase()
    def get_risks(self, product_type): return self.mock.get_risks(product_type)
    def get_product_decomposition(self, product_name, description): return self.mock.get_product_decomposition(product_name, description)
    def get_mitigation(self, module, failure_mode): return self.mock.get_mitigation(module, failure_mode)

class PineconeDatabase(RiskDatabase):
    def __init__(self, api_key, env, index):
        self.mock = MockDatabase()
    def get_risks(self, product_type): return self.mock.get_risks(product_type)
    def get_product_decomposition(self, product_name, description): return self.mock.get_product_decomposition(product_name, description)
    def get_mitigation(self, module, failure_mode): return self.mock.get_mitigation(module, failure_mode)

# ================== 授权与支付模块（从原APP移植） ==================
ADMIN_USERNAME = "Laurence_ku"
ADMIN_PASSWORD = "Ku_product$2026"

try:
    PERSISTENT_API_KEY = st.secrets["AI_API_KEY"]
except:
    PERSISTENT_API_KEY = ""
try:
    PERSISTENT_BASE_URL = st.secrets["AI_BASE_URL"]
except:
    PERSISTENT_BASE_URL = "https://api.deepseek.com"
try:
    PERSISTENT_MODEL_NAME = st.secrets["AI_MODEL_NAME"]
except:
    PERSISTENT_MODEL_NAME = "deepseek-coder"

try:
    stripe.api_key = st.secrets["STRIPE_SECRET_KEY"]
except:
    stripe.api_key = ""

LICENSE_TYPES = {
    "trial": {"name": "试用版", "max_uses": 60, "max_months": 3, "en_name": "Trial"},
    "level1": {"name": "一级用户", "max_uses": 100, "max_months": 12, "en_name": "Level 1"},
    "level2": {"name": "二级用户", "max_uses": 300, "max_months": 24, "en_name": "Level 2"},
    "level3": {"name": "三级用户", "max_uses": 500, "max_months": 36, "en_name": "Level 3"},
    "level4": {"name": "四级用户", "max_uses": 1000, "max_months": 60, "en_name": "Level 4"},
}

USAGE_FILE = "usage_data.json"

def load_usage_data():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_usage_data(data):
    with open(USAGE_FILE, "w") as f:
        json.dump(data, f, indent=2)

if "usage_db" not in st.session_state:
    st.session_state.usage_db = load_usage_data()
if "current_report_key" not in st.session_state:
    st.session_state.current_report_key = ""
if "current_license_type" not in st.session_state:
    st.session_state.current_license_type = None
if "trial_uses_left" not in st.session_state:
    st.session_state.trial_uses_left = 3
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "ai_api_key" not in st.session_state:
    st.session_state.ai_api_key = PERSISTENT_API_KEY
if "ai_base_url" not in st.session_state:
    st.session_state.ai_base_url = PERSISTENT_BASE_URL
if "ai_model_name" not in st.session_state:
    st.session_state.ai_model_name = PERSISTENT_MODEL_NAME
if "lang" not in st.session_state:
    st.session_state.lang = "zh"
if "db_choice" not in st.session_state:
    st.session_state.db_choice = "模拟数据库 (Mock)"

def activate_license(report_key):
    if report_key in st.session_state.usage_db:
        record = st.session_state.usage_db[report_key]
        remaining = record["remaining"]
        expiry_str = record["expiry"]
        expiry = datetime.fromisoformat(expiry_str)
        if remaining > 0 and datetime.now() <= expiry:
            st.session_state.current_license_type = record.get("type", "unknown")
            return True, remaining, expiry_str
    st.session_state.current_license_type = None
    return False, 0, None

def consume_usage(report_key):
    if st.session_state.admin_logged_in:
        return True
    if not report_key:
        if st.session_state.trial_uses_left > 0:
            st.session_state.trial_uses_left -= 1
            return True
        return False
    valid, remaining, _ = activate_license(report_key)
    if not valid:
        return False
    record = st.session_state.usage_db[report_key]
    record["remaining"] -= 1
    record["total_uses"] = record.get("total_uses", 0) + 1
    save_usage_data(st.session_state.usage_db)
    return True

def get_remaining_info(report_key):
    if st.session_state.admin_logged_in:
        return "无限", "永久"
    if report_key:
        valid, remaining, expiry_str = activate_license(report_key)
        if valid:
            expiry = datetime.fromisoformat(expiry_str)
            return str(remaining), expiry.strftime("%Y-%m-%d")
    return str(st.session_state.trial_uses_left), "试用剩余次数"

def is_premium_user(report_key):
    if st.session_state.admin_logged_in:
        return True
    if report_key:
        valid, _, _ = activate_license(report_key)
        return valid
    return False

def generate_report_key(license_type, custom_uses=None, custom_months=None, custom_key=None):
    if license_type == "custom":
        max_uses = custom_uses
        max_months = custom_months
        type_name = "自定义"
    else:
        lic_info = LICENSE_TYPES[license_type]
        max_uses = lic_info["max_uses"]
        max_months = lic_info["max_months"]
        type_name = lic_info["name"]
    expiry = datetime.now() + timedelta(days=max_months*30)
    expiry_str = expiry.isoformat()
    if custom_key and custom_key.strip():
        new_key = custom_key.strip().upper()
        if new_key in st.session_state.usage_db:
            return None, 0, None, "授权码已存在"
    else:
        while True:
            random_str = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            new_key = f"{license_type.upper()}_{random_str}"
            if new_key not in st.session_state.usage_db:
                break
    st.session_state.usage_db[new_key] = {
        "type": license_type,
        "remaining": max_uses,
        "expiry": expiry_str,
        "total_uses": 0,
        "generated_at": datetime.now().isoformat()
    }
    save_usage_data(st.session_state.usage_db)
    return new_key, max_uses, expiry_str, type_name

# ================== 数据库工厂 ==================
def get_database(choice: str) -> RiskDatabase:
    if choice == "Neo4j":
        # 实际应读取secrets，这里简化
        return Neo4jDatabase("", "", "")
    elif choice == "PostgreSQL":
        return PostgresDatabase("", 5432, "", "", "")
    elif choice == "Pinecone":
        return PineconeDatabase("", "", "")
    else:
        return MockDatabase()

# ================== 管理员弹窗 ==================
@st.dialog("管理员设置")
def admin_settings_dialog():
    st.subheader("⚙️ AI API 配置（临时覆盖）")
    new_key = st.text_input("API Key", value=st.session_state.ai_api_key, type="password")
    new_url = st.text_input("Base URL", value=st.session_state.ai_base_url)
    new_model = st.text_input("模型名称", value=st.session_state.ai_model_name)
    if st.button("应用临时配置"):
        st.session_state.ai_api_key = new_key
        st.session_state.ai_base_url = new_url
        st.session_state.ai_model_name = new_model
        st.success("已应用（刷新页面恢复永久配置）")
        st.rerun()
    st.markdown("---")
    st.subheader("🗄️ 数据库选择")
    db_opt = st.selectbox("后端数据库", ["模拟数据库 (Mock)", "Neo4j", "PostgreSQL", "Pinecone"], index=0)
    if st.button("切换数据库"):
        st.session_state.db_choice = db_opt
        st.success(f"已切换至 {db_opt}")
        st.rerun()
    st.markdown("---")
    st.subheader("🔑 授权码生成器")
    key_type = st.selectbox("授权类型", ["试用版", "一级用户", "二级用户", "三级用户", "四级用户", "自定义"])
    custom_uses = None
    custom_months = None
    if key_type == "自定义":
        col1, col2 = st.columns(2)
        with col1:
            custom_uses = st.number_input("使用次数", min_value=1, value=100)
        with col2:
            custom_months = st.number_input("有效期（月）", min_value=1, value=12)
    custom_key_input = st.text_input("自定义授权码（可选）", placeholder="例如 VIP_2026_001")
    if st.button("生成授权码"):
        lt_map = {"试用版":"trial","一级用户":"level1","二级用户":"level2","三级用户":"level3","四级用户":"level4","自定义":"custom"}
        result = generate_report_key(lt_map[key_type], custom_uses, custom_months, custom_key_input)
        if result[0] is None:
            st.error(result[3])
        else:
            st.success(f"生成成功：")
            st.code(result[0])
            st.write(f"次数：{result[1]}，有效期至：{result[2][:10]}")
    st.markdown("---")
    st.subheader("💳 生成付费套餐码")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("单次通行 (3次)"):
            new_key, max_uses, expiry_str, _ = generate_report_key("custom", custom_uses=3, custom_months=9999)
            st.code(new_key)
    with col2:
        if st.button("100次套餐 (1个月)"):
            new_key, max_uses, expiry_str, _ = generate_report_key("custom", custom_uses=100, custom_months=1)
            st.code(new_key)
    with col3:
        if st.button("1200次套餐 (12个月)"):
            new_key, max_uses, expiry_str, _ = generate_report_key("custom", custom_uses=1200, custom_months=12)
            st.code(new_key)
    st.markdown("---")
    st.subheader("📋 已生成授权码列表")
    records = []
    for key, data in st.session_state.usage_db.items():
        records.append({
            "授权码": key,
            "剩余次数": data["remaining"],
            "总使用": data.get("total_uses",0),
            "有效期至": data["expiry"][:10]
        })
    if records:
        st.dataframe(pd.DataFrame(records), use_container_width=True)
    else:
        st.info("暂无")

# ================== 右上角按钮 ==================
col1, col2, col3, col4 = st.columns([8, 1, 1, 1])
with col2:
    if st.button("中文", key="zh_btn", type="primary"):
        st.session_state.lang = "zh"
        st.rerun()
with col3:
    if st.button("English", key="en_btn", type="primary"):
        st.session_state.lang = "en"
        st.rerun()
with col4:
    if st.button("⚙️", key="settings_btn"):
        if st.session_state.admin_logged_in:
            admin_settings_dialog()
        else:
            # 管理员登录弹窗
            @st.dialog("管理员登录")
            def admin_login():
                username = st.text_input("用户名")
                password = st.text_input("密码", type="password")
                if st.button("登录"):
                    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                        st.session_state.admin_logged_in = True
                        st.rerun()
                    else:
                        st.error("错误")
            admin_login()

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
        "report_key_label": "授权码 (Report Key)",
        "license_info": "授权信息",
        "remaining_label": "剩余次数",
        "expiry_label": "有效期至",
        "contact_info": "📞 **联系：**  \n✉️ 电邮: Techlife2027@gmail.com",
        "purchase_title": "💰 购买+解锁",
        "purchase_button": "💰 购买授权码",
        "goto_stripe_button": "前往 Stripe 支付",
        "input_title": "📝 产品风险分析",
        "product_name": "产品名称",
        "product_name_ph": "例如：高功率LED天棚灯",
        "product_desc": "设计描述",
        "product_desc_ph": "例如：200W COB光源，主动风扇散热，IP65",
        "analyze_btn": "🚀 开始风险分析",
        "product_name_missing": "请填写产品名称",
        "generating": "分析中，请稍候...",
        "error_prefix": "分析失败：",
        "decomposition_title": "📐 产品分解结果",
        "risks_title": "⚠️ Top 10 潜在风险 (按RPN排序)",
        "strategy_title": "💡 设计策略与缓解措施",
        "download_btn": "📎 导出风险表格 (CSV)",
        "back_btn": "← 返回重新填写",
        "footer": "© 2026 Laurence Ku | AI+DQA 风险分析 | 基于25年研发经验",
        "trial_ended": "试用次数已用完，请联系购买授权码",
        "no_license": "未输入授权码，当前为试用模式（剩余次数：{}）",
        "trial_warning": "⚠️ 您还有 {} 次试用机会，输入授权码可解锁无限使用。",
        "payment_link_generated": "✅ 支付链接已生成",
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
        "report_key_label": "License Key",
        "license_info": "License Info",
        "remaining_label": "Remaining",
        "expiry_label": "Valid until",
        "contact_info": "📞 **Contact:**  \n✉️ Email: Techlife2027@gmail.com",
        "purchase_title": "💰 Purchase + Unlock",
        "purchase_button": "💰 Purchase License",
        "goto_stripe_button": "Go to Stripe",
        "input_title": "📝 Product Risk Analysis",
        "product_name": "Product Name",
        "product_name_ph": "e.g., High Bay LED Light",
        "product_desc": "Design Description",
        "product_desc_ph": "e.g., 200W COB, active fan cooling, IP65",
        "analyze_btn": "🚀 Start Analysis",
        "product_name_missing": "Please enter product name",
        "generating": "Analyzing, please wait...",
        "error_prefix": "Analysis failed: ",
        "decomposition_title": "📐 Product Decomposition",
        "risks_title": "⚠️ Top 10 Potential Risks (by RPN)",
        "strategy_title": "💡 Design Strategies & Mitigations",
        "download_btn": "📎 Export Risk Table (CSV)",
        "back_btn": "← Back",
        "footer": "© 2026 Laurence Ku | AI+DQA Risk Analysis | 25+ years R&D",
        "trial_ended": "Trial credits exhausted. Please purchase a license.",
        "no_license": "No license key. Trial mode (remaining: {})",
        "trial_warning": "⚠️ You have {} trial credits left. Enter a license key for unlimited access.",
        "payment_link_generated": "✅ Payment link generated",
    }
}

# ================== 辅助函数 ==================
def generate_mitigation_strategy(risk_item: Dict, db: RiskDatabase) -> str:
    base = db.get_mitigation(risk_item["module"], risk_item["failure_mode"])
    strategy = f"""
针对 **{risk_item['module']}** 的 **{risk_item['failure_mode']}** 问题（原因：{risk_item['cause']}），建议如下策略：

1. **设计层面**：{base}
2. **仿真验证**：热/电路/结构仿真，验证设计余量。
3. **测试标准**：参考 IEC/GB，增加 HALT/HASS。
4. **制程管控**：关键工艺 SPC 监控。
5. **售后闭环**：建立失效分析数据库。

**RPN**：严重度 {risk_item['severity']} × 发生度 {risk_item['occurrence']} × 探测度 {risk_item['detection']} = **{risk_item['RPN']}**
"""
    return strategy

# ================== 页面主布局 ==================
lang = st.session_state.lang
t = TEXTS[lang]
t["no_license"] = t["no_license"].format(st.session_state.trial_uses_left)
t["trial_warning"] = t["trial_warning"].format(st.session_state.trial_uses_left)

st.title(t["title"])

# ================== 侧边栏 ==================
with st.sidebar:
    report_key_input = st.text_input(
        t["report_key_label"],
        value=st.session_state.current_report_key,
        type="password",
        key="license_key_input"
    )
    if report_key_input:
        valid, remaining, expiry_str = activate_license(report_key_input)
        if valid:
            st.success(f"授权成功！剩余 {remaining} 次，有效期至 {expiry_str[:10]}" if lang=="zh" else f"Success! {remaining} uses left until {expiry_str[:10]}")
            st.session_state.current_report_key = report_key_input
        else:
            if report_key_input != st.session_state.current_report_key:
                st.error("授权码无效或已过期" if lang=="zh" else "Invalid or expired key")
                st.session_state.current_report_key = ""
    else:
        if st.session_state.trial_uses_left > 0:
            st.warning(t["trial_warning"])
        else:
            st.error(t["trial_ended"])
    
    if st.session_state.admin_logged_in:
        st.info("管理员模式：无限使用" if lang=="zh" else "Admin mode: unlimited")
    else:
        remaining_str, expiry_str = get_remaining_info(st.session_state.current_report_key)
        st.markdown(f"**{t['license_info']}**")
        st.write(f"{t['remaining_label']}: {remaining_str}")
        if expiry_str != "试用剩余次数":
            st.write(f"{t['expiry_label']}: {expiry_str}")
    
    st.markdown("---")
    st.markdown(f"## {t['purchase_title']}")
    # 购买弹窗（简化版，保留Stripe支付）
    @st.dialog("购买授权码" if lang=="zh" else "Purchase License", width="large")
    def purchase_dialog():
        st.markdown("### 选择套餐" if lang=="zh" else "### Select Plan")
        st.markdown("| 套餐 | 价格 | 次数 | 有效期 |\n|------|------|------|--------|\n| 单次通行 | 18元 / 3美元 | 3次 | 无限制 |\n| 100次套餐 | 180元 / 30美元 | 100次 | 1个月 |\n| 1200次套餐 | 1200元 / 200美元 | 1200次 | 12个月 |" if lang=="zh" else "| Plan | Price | Credits | Validity |\n|------|-------|---------|----------|\n| Single Pass | 18 RMB / $3 | 3 | Unlimited |\n| 100 Credits | 180 RMB / $30 | 100 | 1 month |\n| 1200 Credits | 1200 RMB / $200 | 1200 | 12 months |")
        if stripe.api_key:
            col1, col2, col3 = st.columns(3)
            # 实际应调用stripe.checkout.Session，这里仅示意
            for plan, price, uses, months, label in [("single",300,3,9999,"单次通行"), ("100",3000,100,1,"100次套餐"), ("1200",20000,1200,12,"1200次套餐")]:
                with [col1, col2, col3][[0,1,2][["single","100","1200"].index(plan)]]:
                    if st.button(label):
                        # 生成授权码（模拟支付成功）
                        new_key, _, _, _ = generate_report_key("custom", custom_uses=uses, custom_months=months)
                        st.success(f"授权码已生成：{new_key}")
                        st.session_state.current_report_key = new_key
                        st.rerun()
        else:
            st.warning("Stripe 未配置，请联系管理员")
    if st.button(t["purchase_button"], use_container_width=True):
        purchase_dialog()
    
    st.markdown("---")
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
    if st.session_state.ai_api_key:
        st.success(t["api_configured"])
    else:
        st.error(t["api_not_configured"])
    st.markdown("---")
    st.markdown(t["contact_info"])

# ================== 主界面：风险分析表单 ==================
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
        can_analyze = False
        if st.session_state.admin_logged_in:
            can_analyze = True
        elif is_premium_user(st.session_state.current_report_key):
            if consume_usage(st.session_state.current_report_key):
                can_analyze = True
            else:
                st.error(t["trial_ended"])
        else:
            if st.session_state.trial_uses_left > 0:
                can_analyze = True
            else:
                st.error(t["trial_ended"])
        if can_analyze:
            if not is_premium_user(st.session_state.current_report_key):
                consume_usage("")
            with st.spinner(t["generating"]):
                try:
                    db = get_database(st.session_state.db_choice)
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
                                strategy = generate_mitigation_strategy(risk, db)
                                st.markdown(strategy)
                        
                        csv = df.to_csv(index=False).encode('utf-8')
                        st.download_button(t["download_btn"], data=csv, file_name=f"{product_name}_risks.csv", mime="text/csv")
                    else:
                        st.warning("未检索到风险数据")
                except Exception as e:
                    st.error(f"{t['error_prefix']}{e}")

st.markdown("---")
st.caption(t["footer"])
