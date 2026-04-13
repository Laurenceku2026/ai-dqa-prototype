import streamlit as st
import pandas as pd
import json
import os
import sqlite3
import openai
import re
import secrets
import string
import stripe
from io import BytesIO
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from duckduckgo_search import DDGS
from neo4j import GraphDatabase
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

# ================== 页面配置 ==================
st.set_page_config(page_title="AI+DQA 风险分析系统", page_icon="🔍", layout="wide")

# ================== 试用模式的安全防护代码（CSS + JS）水印加大版 ==================
TRIAL_SECURITY_HTML = """
<style>
    body, .stApp, .report-card, .markdown-text-container {
        user-select: none !important;
        -webkit-user-select: none !important;
        -moz-user-select: none !important;
        -ms-user-select: none !important;
        -webkit-touch-callout: none !important;
    }
    .trial-watermark-bg {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 9999;
        background-image: repeating-linear-gradient(45deg, 
            rgba(0,0,0,0.05) 0px, rgba(0,0,0,0.05) 4px,
            transparent 4px, transparent 60px,
            rgba(0,0,0,0.05) 60px, rgba(0,0,0,0.05) 64px,
            transparent 64px, transparent 120px);
        background-size: 120px 120px;
    }
    .trial-watermark-text {
        position: fixed;
        bottom: 20px;
        right: 20px;
        opacity: 0.5;
        font-size: 14px;
        color: #666;
        background: rgba(255,255,255,0.8);
        padding: 8px 16px;
        border-radius: 8px;
        font-family: monospace;
        pointer-events: none;
        z-index: 10000;
        width: 360px;
        max-width: 80%;
        text-align: right;
        box-shadow: 0 2px 6px rgba(0,0,0,0.1);
    }
</style>
<script>
    document.addEventListener('contextmenu', function(e) { e.preventDefault(); return false; });
    document.addEventListener('keydown', function(e) {
        if (e.ctrlKey && (e.key === 'c' || e.key === 'C' || e.key === 'v' || e.key === 'V' || 
                          e.key === 'x' || e.key === 'X' || e.key === 's' || e.key === 'S')) {
            e.preventDefault(); return false;
        }
        if (e.key === 'F12') { e.preventDefault(); return false; }
    });
    document.addEventListener('selectstart', function(e) { e.preventDefault(); return false; });
</script>
<div class="trial-watermark-bg"></div>
<div class="trial-watermark-text">⚠️ 机密报告 · 请联系 Techlife2027@gmail.com 购买授权 ⚠️</div>
"""

# ================== Stripe 配置 ==================
try:
    stripe.api_key = st.secrets["STRIPE_SECRET_KEY"]
except:
    stripe.api_key = ""
    st.warning("Stripe Secret Key 未配置，支付功能不可用。请管理员在 Secrets 中添加 STRIPE_SECRET_KEY。")

# 套餐定义（次数，有效期月数）
PLANS = {
    "single": {"uses": 3, "months": 9999, "price_id": "price_1R8H1yFdO2L3jCxQ4X2y5K7W", "name_zh": "单次通行", "name_en": "Single Pass", "price_usd": 3},
    "50": {"uses": 50, "months": 1, "price_id": "price_1R8H2zFdO2L3jCxQ4X2y5K8X", "name_zh": "50次套餐", "name_en": "50 Credits", "price_usd": 30},
    "1000": {"uses": 1000, "months": 12, "price_id": "price_1R8H3aFdO2L3jCxQ4X2y5K9Y", "name_zh": "1000次套餐", "name_en": "1000 Credits", "price_usd": 200},
}
# 注意：上面的 price_id 需要替换为你自己在 Stripe 中实际创建的对应价格 ID

# ================== 授权与试用数据管理 ==================
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

LICENSE_TYPES = {
    "trial": {"name": "试用版", "max_uses": 3, "max_months": 1, "en_name": "Trial"},
    "level1": {"name": "一级用户", "max_uses": 100, "max_months": 12, "en_name": "Level 1"},
    "level2": {"name": "二级用户", "max_uses": 300, "max_months": 24, "en_name": "Level 2"},
    "level3": {"name": "三级用户", "max_uses": 500, "max_months": 36, "en_name": "Level 3"},
    "level4": {"name": "四级用户", "max_uses": 1000, "max_months": 60, "en_name": "Level 4"},
}

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
    usage_db = load_usage_data()
    if custom_key and custom_key.strip():
        new_key = custom_key.strip().upper()
        if new_key in usage_db:
            return None, 0, None, "授权码已存在"
    else:
        while True:
            random_str = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            new_key = f"{license_type.upper()}_{random_str}"
            if new_key not in usage_db:
                break
    usage_db[new_key] = {
        "type": license_type,
        "remaining": max_uses,
        "expiry": expiry_str,
        "total_uses": 0,
        "generated_at": datetime.now().isoformat()
    }
    save_usage_data(usage_db)
    return new_key, max_uses, expiry_str, type_name

def activate_license(report_key):
    if not report_key:
        return False, 0, None, None
    usage_db = load_usage_data()
    if report_key in usage_db:
        record = usage_db[report_key]
        remaining = record["remaining"]
        expiry_str = record["expiry"]
        expiry = datetime.fromisoformat(expiry_str)
        if remaining > 0 and datetime.now() <= expiry:
            return True, remaining, expiry_str, record.get("type", "unknown")
    return False, 0, None, None

def consume_usage(report_key):
    if st.session_state.get("admin_logged_in", False):
        return True
    if not report_key:
        if st.session_state.trial_uses_left > 0:
            st.session_state.trial_uses_left -= 1
            return True
        else:
            return False
    usage_db = load_usage_data()
    if report_key in usage_db:
        record = usage_db[report_key]
        if record["remaining"] > 0 and datetime.now() <= datetime.fromisoformat(record["expiry"]):
            record["remaining"] -= 1
            record["total_uses"] = record.get("total_uses", 0) + 1
            save_usage_data(usage_db)
            return True
    return False

def get_remaining_info(report_key):
    if st.session_state.get("admin_logged_in", False):
        return "无限", "永久"
    if report_key:
        valid, remaining, expiry_str, _ = activate_license(report_key)
        if valid:
            return str(remaining), expiry_str[:10]
    return str(st.session_state.trial_uses_left), "试用剩余次数"

def is_premium_user(report_key):
    if st.session_state.get("admin_logged_in", False):
        return True
    if report_key:
        valid, _, _, _ = activate_license(report_key)
        return valid
    return False

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
if "analyst_name" not in st.session_state:
    st.session_state.analyst_name = ""
if "analyst_title" not in st.session_state:
    st.session_state.analyst_title = ""
if "current_report_key" not in st.session_state:
    st.session_state.current_report_key = ""
if "trial_uses_left" not in st.session_state:
    st.session_state.trial_uses_left = 3
if "report_content" not in st.session_state:
    st.session_state.report_content = None
if "last_product_name" not in st.session_state:
    st.session_state.last_product_name = ""
if "last_product_desc" not in st.session_state:
    st.session_state.last_product_desc = ""
if "show_payment_dialog" not in st.session_state:
    st.session_state.show_payment_dialog = False
if "payment_new_key" not in st.session_state:
    st.session_state.payment_new_key = ""

ADMIN_USERNAME = "Laurence_ku"
ADMIN_PASSWORD = "Ku_product$2026"

# ================== 数据库部分（SQLite + Neo4j + Hybrid，同原文件） ==================
# ... 此处保持与 AI-DQA_20260413.py 完全相同的数据库类实现 ...
# 为了节省篇幅，假设下面已完整包含 SQLiteDatabase, Neo4jDatabase, HybridDatabase, get_database 等
# 实际部署时请确保这些类完整复制过来

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
    def search_knowledge(self, keywords: str, limit: int = 5) -> List[str]:
        raise NotImplementedError

class SQLiteDatabase(RiskDatabase):
    # ... 完整实现（同原文件）
    pass

class Neo4jDatabase(RiskDatabase):
    # ... 完整实现
    pass

class HybridDatabase(RiskDatabase):
    # ... 完整实现
    pass

def get_database() -> RiskDatabase:
    return HybridDatabase()

# ================== DeepSeek 客户端等函数（同原文件） ==================
def get_openai_client():
    # ... 同原文件
    pass

def call_deepseek(prompt: str, max_tokens=4000) -> str:
    # ... 同原文件
    pass

def translate_text(text: str, target_lang: str) -> str:
    # ... 同原文件
    pass

def web_search(query: str, max_results=3) -> str:
    # ... 同原文件
    pass

def clean_ai_response(text: str, lang: str = "zh") -> str:
    # ... 同原文件
    pass

def generate_ai_analysis_content(product_name: str, product_desc: str, enable_web: bool, db: RiskDatabase, lang: str = "zh") -> str:
    # ... 同原文件
    pass

def markdown_to_docx(md_text: str, doc: Document):
    # ... 同原文件
    pass

def generate_word_report(product_name: str, product_desc: str, analyst_name: str, analyst_title: str, report_content: str, lang: str = "zh", add_watermark: bool = False) -> BytesIO:
    # ... 同原文件
    pass

# ================== 管理员设置弹窗（同原文件，保持不变） ==================
@st.dialog("管理员设置", width="large")
def admin_settings_dialog():
    # ... 同 AI-DQA_20260413.py 中的完整实现
    pass

# ================== 购买对话框 ==================
@st.dialog("购买+解锁", width="large")
def purchase_dialog():
    lang = st.session_state.lang
    if lang == "zh":
        st.markdown("### 选择套餐")
        st.markdown("""
| 套餐 | 价格 | 次数 | 有效期 |
|------|------|------|--------|
| 单次通行 | 18元 / 3美元 | 3次 | 无限制 |
| 50次套餐 | 180元 / 30美元 | 50次 | 1个月 |
| 1000次套餐 | 1200元 / 200美元 | 1000次 | 12个月 |
""")
    else:
        st.markdown("### Select Plan")
        st.markdown("""
| Plan | Price | Credits | Validity |
|------|-------|---------|----------|
| Single Pass | 18 RMB / $3 | 3 uses | Unlimited |
| 50 Credits | 180 RMB / $30 | 50 uses | 1 month |
| 1000 Credits | 1200 RMB / $200 | 1000 uses | 12 months |
""")
    st.markdown("#### 💳 银行卡/数字钱包支付（Stripe）" if lang=="zh" else "#### 💳 Card / Digital Wallet Payment (Stripe)")
    
    if not stripe.api_key:
        st.error("Stripe 未配置，请联系管理员。" if lang=="zh" else "Stripe not configured. Please contact admin.")
        return
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🎟️ " + (PLANS["single"]["name_zh"] if lang=="zh" else PLANS["single"]["name_en"]) + f"\n${PLANS['single']['price_usd']}", use_container_width=True):
            create_checkout_session("single")
    with col2:
        if st.button("📦 " + (PLANS["50"]["name_zh"] if lang=="zh" else PLANS["50"]["name_en"]) + f"\n${PLANS['50']['price_usd']}", use_container_width=True):
            create_checkout_session("50")
    with col3:
        if st.button("🚀 " + (PLANS["1000"]["name_zh"] if lang=="zh" else PLANS["1000"]["name_en"]) + f"\n${PLANS['1000']['price_usd']}", use_container_width=True):
            create_checkout_session("1000")
    
    st.markdown("#### 🇨🇳 国内支付（微信 / 支付宝）" if lang=="zh" else "#### 🇨🇳 Domestic Payment (WeChat Pay / Alipay)")
    st.info("支持信用卡、微信支付和支付宝。" if lang=="zh" else "Supports credit cards, WeChat Pay and Alipay.")
    st.markdown("支付成功后会自动跳回本页面，授权码将自动激活。" if lang=="zh" else "You will be redirected back after payment, and the license key will be auto-activated.")

def create_checkout_session(plan_key):
    """创建 Stripe Checkout 会话并跳转"""
    plan = PLANS[plan_key]
    price_id = plan["price_id"]
    # 获取当前应用的基础 URL（用于成功/取消跳转）
    base_url = st.secrets.get("APP_URL", "https://ai-app-design-dfmea.streamlit.app")
    success_url = f"{base_url}?order_success=1&plan={plan_key}"
    cancel_url = f"{base_url}"
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card", "wechat_pay", "alipay"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="payment",
            payment_method_options={"wechat_pay": {"client": "web"}},
            success_url=success_url,
            cancel_url=cancel_url,
            customer_creation="always",
        )
        st.success("支付链接已生成，请点击下方按钮完成支付" if st.session_state.lang=="zh" else "Payment link generated. Click below to pay.")
        button_html = f'<a href="{checkout_session.url}" target="_blank" style="display: block; background-color: #E60000; color: white; font-weight: bold; font-size: 18px; padding: 12px; border-radius: 8px; text-align: center; text-decoration: none; width: 100%;">前往 Stripe 支付页面</a>'
        st.markdown(button_html, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"创建支付会话失败: {e}" if st.session_state.lang=="zh" else f"Failed to create checkout session: {e}")

# ================== 支付成功回调处理 ==================
def handle_payment_callback():
    params = st.query_params
    if "order_success" in params and "plan" in params:
        plan_key = params["plan"]
        if plan_key in PLANS:
            uses = PLANS[plan_key]["uses"]
            months = PLANS[plan_key]["months"]
            # 生成授权码
            new_key, max_uses, expiry_str, _ = generate_report_key("custom", custom_uses=uses, custom_months=months)
            if new_key:
                st.session_state.current_report_key = new_key
                st.session_state.payment_new_key = new_key
                st.session_state.show_payment_dialog = True
                # 清除 URL 参数，避免重复触发
                st.query_params.clear()
                st.rerun()
            else:
                st.error("生成授权码失败，请联系管理员。" if st.session_state.lang=="zh" else "Failed to generate license key. Contact admin.")
                st.query_params.clear()
        else:
            st.error("无效的套餐类型。" if st.session_state.lang=="zh" else "Invalid plan type.")
            st.query_params.clear()

# ================== 支付成功弹窗 ==================
def show_payment_success_dialog():
    if st.session_state.get("show_payment_dialog", False):
        @st.dialog("✅ 支付成功")
        def payment_success_dialog():
            lang = st.session_state.lang
            st.markdown("### 您的授权码已生成" if lang=="zh" else "### Your license key has been generated")
            st.code(st.session_state.payment_new_key, language="text")
            st.caption("请妥善保管此授权码，下次使用时可手动复制并粘贴到左侧输入框。" if lang=="zh" else "Please save this license key. You can copy and paste it into the left sidebar next time.")
            st.info("🔑 请复制上方授权码，然后关闭本窗口，回到您原先生成报告的那个窗口，将授权码粘贴到左侧边栏输入框中即可解锁下载。" if lang=="zh" else "🔑 Please copy the license key above, close this window, return to your original report window, and paste the key into the left sidebar to unlock download.")
            if st.button("确定" if lang=="zh" else "OK"):
                st.session_state.show_payment_dialog = False
                st.session_state.payment_new_key = ""
                st.rerun()
        payment_success_dialog()

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
        "footer": "© 2026 Laurence Ku | AI+DQA 风险分析",
        "db_status": "数据库状态",
        "db_connected": "✅ 混合模式 (SQLite + Neo4j)",
        "license_info": "授权信息",
        "remaining_label": "剩余次数",
        "expiry_label": "有效期至",
        "report_key_label": "授权码 (Report Key)",
        "no_license": "未输入授权码，当前为试用模式（剩余次数：{}）",
        "trial_warning": "⚠️ 您还有 {} 次试用机会，输入授权码可解锁无限使用和下载功能。",
        "purchase_button": "💰 购买授权码",
        "download_btn": "📥 下载 Word 报告",
        "need_license": "⚠️ 请先购买授权码后再下载报告。",
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
        "footer": "© 2026 Laurence Ku | AI+DQA Risk Analysis",
        "db_status": "Database Status",
        "db_connected": "✅ Hybrid Mode (SQLite + Neo4j)",
        "license_info": "License Info",
        "remaining_label": "Remaining uses",
        "expiry_label": "Valid until",
        "report_key_label": "Report Key",
        "no_license": "No Report Key. Trial mode (remaining credits: {})",
        "trial_warning": "⚠️ You have {} trial credits left. Enter a license key to unlock unlimited usage.",
        "purchase_button": "💰 Purchase License",
        "download_btn": "📥 Download Word Report",
        "need_license": "⚠️ Please purchase a license before downloading.",
    }
}

lang = st.session_state.lang
t = TEXTS[lang]
st.title(t["title"])

# 初始化数据库
if "database" not in st.session_state:
    st.session_state.database = get_database()
    st.session_state.database.load_initial_data()

# 处理支付回调
handle_payment_callback()
show_payment_success_dialog()

# ================== 侧边栏（整合授权码输入 + 购买按钮） ==================
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
    
    # 授权码输入区域
    st.markdown(f"### 🔑 {t['report_key_label']}")
    new_report_key = st.text_input("", value=st.session_state.current_report_key, type="password", key="report_key_input", placeholder="输入授权码后按 Enter")
    if new_report_key != st.session_state.current_report_key:
        st.session_state.current_report_key = new_report_key
        if new_report_key:
            valid, remaining, expiry_str, _ = activate_license(new_report_key)
            if valid:
                st.success(f"授权成功！剩余 {remaining} 次，有效期至 {expiry_str[:10]}" if lang=="zh" else f"Success! {remaining} uses left, valid until {expiry_str[:10]}")
                st.rerun()
            else:
                st.error("授权码无效或已过期" if lang=="zh" else "Invalid or expired license key")
                st.session_state.current_report_key = ""
                st.rerun()
        else:
            st.rerun()
    
    remaining_str, expiry_str = get_remaining_info(st.session_state.current_report_key)
    st.markdown(f"**{t['license_info']}**")
    st.write(f"{t['remaining_label']}: {remaining_str}")
    if expiry_str != "试用剩余次数" and expiry_str != "Trial left":
        st.write(f"{t['expiry_label']}: {expiry_str}")
    if not is_premium_user(st.session_state.current_report_key):
        st.warning(t["trial_warning"].format(st.session_state.trial_uses_left))
    st.markdown("---")
    
    # 购买按钮
    if st.button(t["purchase_button"], use_container_width=True):
        purchase_dialog()
    st.markdown("---")
    st.markdown(t["contact_info"])

# ================== 主界面 ==================
st.markdown(f"### {t['input_title']}")
product_name = st.text_input(t["product_name"], placeholder=t["product_name_ph"], key="product_name_input")
product_desc = st.text_area(t["product_desc"], placeholder=t["product_desc_ph"], height=100, key="product_desc_input")

col_center = st.columns([1, 2, 1])[1]
with col_center:
    st.markdown('<div class="main-analyze">', unsafe_allow_html=True)
    if st.button(t["analyze_btn"], key="main_analyze_btn", type="primary"):
        if not product_name:
            st.error(t["product_name_missing"])
        else:
            if is_premium_user(st.session_state.current_report_key):
                if not consume_usage(st.session_state.current_report_key):
                    st.error("授权码次数已用完或已过期，请购买新授权码。")
                    st.stop()
            else:
                if st.session_state.trial_uses_left <= 0:
                    st.error("试用次数已用完，请购买授权码。")
                    # 弹出购买对话框
                    purchase_dialog()
                    st.stop()
                consume_usage("")
            
            db = st.session_state.database
            with st.spinner(t["generating"]):
                report_content = generate_ai_analysis_content(
                    product_name, product_desc,
                    st.session_state.enable_web_search,
                    db,
                    lang=st.session_state.lang
                )
                st.session_state.report_content = report_content
                st.session_state.last_product_name = product_name
                st.session_state.last_product_desc = product_desc
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ================== 显示已生成的报告 ==================
if st.session_state.report_content:
    saved_name = st.session_state.get("analyst_name", "")
    saved_title = st.session_state.get("analyst_title", "")
    if saved_name and saved_name.strip():
        author_line = f"分析人：{saved_name.strip()}" + (f" ({saved_title.strip()})" if saved_title.strip() else "") if lang=="zh" else f"Analyst: {saved_name.strip()}" + (f" ({saved_title.strip()})" if saved_title.strip() else "")
    else:
        author_line = "AI生成的风险分析报告" if lang=="zh" else "AI-generated risk analysis report"
    disclaimer_line = "此报告是基于以上提供的有限信息，结合行业数据库和联网搜索结果生成的初步分析，仅供参考。" if lang=="zh" else "This report is a preliminary analysis based on the limited information provided, for reference only."
    full_report_display = f"{author_line}\n\n{disclaimer_line}\n\n{st.session_state.report_content}"
    
    st.markdown("---")
    is_premium = is_premium_user(st.session_state.current_report_key)
    if not is_premium:
        st.markdown(TRIAL_SECURITY_HTML, unsafe_allow_html=True)
    st.markdown('<div class="report-card">', unsafe_allow_html=True)
    st.markdown("### AI赋能DQA-产品设计风险分析报告" if lang=="zh" else "### AI-Enabled DQA Product Design Risk Analysis Report")
    st.markdown(full_report_display)
    st.markdown('</div>', unsafe_allow_html=True)
    
    # 下载按钮：未授权时弹出购买对话框
    col_download = st.columns([1,2,1])[1]
    with col_download:
        if st.button(t["download_btn"], use_container_width=True):
            if not is_premium:
                purchase_dialog()
            else:
                word_bytes = generate_word_report(
                    st.session_state.last_product_name,
                    st.session_state.last_product_desc,
                    saved_name, saved_title,
                    st.session_state.report_content,
                    lang=st.session_state.lang,
                    add_watermark=False
                )
                file_name = f"{st.session_state.last_product_name}_风险分析报告_{datetime.now().strftime('%Y%m%d')}.docx" if lang=="zh" else f"{st.session_state.last_product_name}_Risk_Analysis_Report_{datetime.now().strftime('%Y%m%d')}.docx"
                st.download_button(
                    label="📥 确认下载",
                    data=word_bytes,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="real_download"
                )
    if st.button("← 返回重新填写"):
        st.session_state.report_content = None
        st.session_state.last_product_name = ""
        st.session_state.last_product_desc = ""
        st.rerun()

st.markdown("---")
st.caption(t["footer"])
