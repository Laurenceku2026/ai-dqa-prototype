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
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
import plotly.express as px
import plotly.graph_objects as go

# -------------------------- 【唯一修改：加这一行】宽屏布局，让报告铺满页面 --------------------------
st.set_page_config(layout="wide", page_title="AI+DQA产品风险分析系统", page_icon="📊")

# 初始化数据库
def init_db():
    if not os.path.exists("data"):
        os.makedirs("data")
    
    conn = sqlite3.connect("data/dqa_database.db")
    cursor = conn.cursor()
    
    # 创建产品信息表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        product_desc TEXT,
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 创建风险分析结果表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS risk_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        risk_type TEXT,
        risk_description TEXT,
        severity INTEGER,
        occurrence INTEGER,
        detection INTEGER,
        rpn INTEGER,
        suggestions TEXT,
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    ''')
    
    # 创建历史分析记录表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS analysis_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        analysis_result TEXT,
        analysis_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    ''')
    
    conn.commit()
    conn.close()

# 保存产品信息
def save_product_info(product_name: str, product_desc: str) -> int:
    conn = sqlite3.connect("data/dqa_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (product_name, product_desc) VALUES (?, ?)", 
                   (product_name, product_desc))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return product_id

# 保存风险分析结果
def save_risk_analysis(product_id: int, risk_data: List[Dict]):
    conn = sqlite3.connect("data/dqa_database.db")
    cursor = conn.cursor()
    
    for risk in risk_data:
        cursor.execute('''
        INSERT INTO risk_analysis 
        (product_id, risk_type, risk_description, severity, occurrence, detection, rpn, suggestions)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            product_id,
            risk.get('risk_type', ''),
            risk.get('risk_description', ''),
            risk.get('severity', 1),
            risk.get('occurrence', 1),
            risk.get('detection', 1),
            risk.get('rpn', 1),
            risk.get('suggestions', '')
        ))
    
    conn.commit()
    conn.close()

# 保存分析历史
def save_analysis_history(product_id: int, analysis_result: str):
    conn = sqlite3.connect("data/dqa_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO analysis_history (product_id, analysis_result) VALUES (?, ?)", 
                   (product_id, analysis_result))
    conn.commit()
    conn.close()

# 获取产品列表
def get_product_list() -> List[Tuple]:
    conn = sqlite3.connect("data/dqa_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, product_name, create_time FROM products ORDER BY create_time DESC")
    products = cursor.fetchall()
    conn.close()
    return products

# 获取产品风险分析结果
def get_product_risks(product_id: int) -> List[Dict]:
    conn = sqlite3.connect("data/dqa_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM risk_analysis WHERE product_id = ?", (product_id,))
    risks = cursor.fetchall()
    conn.close()
    
    risk_list = []
    for risk in risks:
        risk_list.append({
            'id': risk[0],
            'product_id': risk[1],
            'risk_type': risk[2],
            'risk_description': risk[3],
            'severity': risk[4],
            'occurrence': risk[5],
            'detection': risk[6],
            'rpn': risk[7],
            'suggestions': risk[8],
            'create_time': risk[9]
        })
    return risk_list

# 解析AI返回的风险数据
def parse_risk_data(ai_response: str) -> List[Dict]:
    try:
        # 尝试解析JSON格式
        if '{' in ai_response and '}' in ai_response:
            # 简单提取风险数据
            risk_data = []
            
            # 基于关键词提取风险信息
            lines = ai_response.split('\n')
            current_risk = {}
            
            for line in lines:
                if '风险类型' in line or '风险类别' in line:
                    if current_risk and 'risk_type' in current_risk:
                        risk_data.append(current_risk)
                    current_risk = {'risk_type': line.replace('风险类型：', '').replace('风险类别：', '').strip()}
                elif '风险描述' in line:
                    current_risk['risk_description'] = line.replace('风险描述：', '').strip()
                elif '严重度' in line:
                    severity = re.findall(r'\d+', line)
                    current_risk['severity'] = int(severity[0]) if severity else 5
                elif '频度' in line or '发生概率' in line:
                    occurrence = re.findall(r'\d+', line)
                    current_risk['occurrence'] = int(occurrence[0]) if occurrence else 5
                elif '探测度' in line:
                    detection = re.findall(r'\d+', line)
                    current_risk['detection'] = int(detection[0]) if detection else 5
                elif 'RPN' in line or '风险优先级' in line:
                    rpn = re.findall(r'\d+', line)
                    current_risk['rpn'] = int(rpn[0]) if rpn else 125
                elif '改进建议' in line or '建议措施' in line:
                    current_risk['suggestions'] = line.replace('改进建议：', '').replace('建议措施：', '').strip()
            
            if current_risk and 'risk_type' in current_risk:
                risk_data.append(current_risk)
            
            return risk_data
    except:
        pass
    
    return []

# 联网搜索产品风险信息
def search_product_risk(product_name: str, product_desc: str) -> str:
    try:
        search_query = f"{product_name} {product_desc} 设计风险 可靠性问题 常见故障"
        results = DDGS().text(search_query, max_results=5)
        
        search_context = "相关行业风险信息：\n"
        for i, result in enumerate(results, 1):
            search_context += f"{i}. {result['title']}: {result['body']}\n\n"
        
        return search_context
    except:
        return "无法获取联网风险信息"

# 生成Word格式报告
def generate_word_report(product_name: str, product_desc: str, 
                        ai_analysis: str, risk_data: List[Dict],
                        analyst_name: str = "", analyst_title: str = "") -> BytesIO:
    doc = Document()
    
    # 设置默认中文字体
    doc.styles['Normal'].font.name = '宋体'
    doc.styles['Normal']._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    doc.styles['Normal'].font.size = Pt(12)
    
    # 添加标题
    title = doc.add_heading(f'{product_name} - 产品设计风险分析报告', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # 添加基本信息
    doc.add_paragraph('=' * 50)
    doc.add_paragraph(f'产品名称：{product_name}')
    doc.add_paragraph(f'产品描述：{product_desc}')
    if analyst_name:
        doc.add_paragraph(f'分析人员：{analyst_name}')
    if analyst_title:
        doc.add_paragraph(f'分析头衔：{analyst_title}')
    doc.add_paragraph(f'分析时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    doc.add_paragraph('=' * 50)
    
    # 添加AI分析结果
    doc.add_heading('一、AI深度分析结果', level=1)
    for paragraph in ai_analysis.split('\n'):
        if paragraph.strip():
            doc.add_paragraph(paragraph)
    
    # 添加风险详情表格
    if risk_data:
        doc.add_heading('二、详细风险清单', level=1)
        table = doc.add_table(rows=1, cols=6)
        table.style = 'Table Grid'
        
        # 设置表头
        header_cells = table.rows[0].cells
        headers = ['风险类型', '严重度', '频度', '探测度', 'RPN值', '改进建议']
        for i, header in enumerate(headers):
            header_cells[i].text = header
        
        # 添加数据
        for risk in risk_data:
            row_cells = table.add_row().cells
            row_cells[0].text = risk.get('risk_type', '')
            row_cells[1].text = str(risk.get('severity', ''))
            row_cells[2].text = str(risk.get('occurrence', ''))
            row_cells[3].text = str(risk.get('detection', ''))
            row_cells[4].text = str(risk.get('rpn', ''))
            row_cells[5].text = risk.get('suggestions', '')
    
    # 保存文档到内存
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer

# 主函数
def main():
    # 初始化数据库
    init_db()
    
    # 页面样式设置
    st.markdown("""
    <style>
    .main-header {
        font-size: 2.5rem;
        color: #1E3A8A;
        text-align: center;
        margin-bottom: 2rem;
    }
    .section-header {
        font-size: 1.5rem;
        color: #2563EB;
        margin-top: 1rem;
        margin-bottom: 1rem;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 侧边栏
    with st.sidebar:
        st.image("https://img.icons8.com/fluency/96/000000/brain-connect.png", width=100)
        st.title("AI+DQA")
        st.markdown("### 产品风险智能分析平台")
        st.divider()
        
        # 分析人员信息
        st.subheader("分析人员设置")
        analyst_name = st.text_input("分析人员姓名", placeholder="请输入您的姓名")
        analyst_title = st.text_input("分析人员头衔", placeholder="请输入您的头衔")
        st.divider()
        
        # API配置状态
        st.subheader("系统配置")
        api_configured = "deepseek_api_key" in st.secrets
        if api_configured:
            st.success("✅ DeepSeek API 已配置")
        else:
            st.error("❌ 请配置DeepSeek API密钥")
        
        st.divider()
        st.markdown("### 功能说明")
        st.markdown("""
        - 🧠 AI深度风险分析
        - 🔍 联网行业信息检索
        - 📊 自动生成风险报告
        - 💾 数据持久化存储
        - 📥 Word报告导出
        """)
    
    # 主页面标题
    st.markdown("<h1 class='main-header'>🔍 AI+DQA 产品设计风险智能分析系统</h1>", unsafe_allow_html=True)
    
    # 创建标签页
    tab1, tab2, tab3 = st.tabs(["📝 风险分析", "📊 历史记录", "⚙️ 系统设置"])
    
    with tab1:
        st.markdown("<h2 class='section-header'>产品信息录入</h2>", unsafe_allow_html=True)
        
        # 产品信息输入
        col1, col2 = st.columns(2)
        with col1:
            product_name = st.text_input("产品名称", placeholder="请输入产品名称", value="150瓦路灯")
        with col2:
            product_series = st.text_input("产品系列（可选）", placeholder="请输入产品系列")
        
        product_desc = st.text_area(
            "产品设计描述",
            value="IP65,沿海使用，防雷电源",
            placeholder="请详细描述产品设计特点、使用环境、关键技术要求等",
            height=100
        )
        
        # 分析按钮
        col_analyze = st.columns([1, 2, 1])
        with col_analyze[1]:
            analyze_button = st.button(
                "🚀 开始AI深度分析",
                type="primary",
                use_container_width=True
            )
        
        if analyze_button:
            if not product_name or not product_desc:
                st.error("❌ 请填写完整的产品信息！")
            elif not api_configured:
                st.error("❌ 请先配置DeepSeek API密钥！")
            elif not analyst_name:
                st.warning("⚠️ 建议填写分析人员姓名，便于报告追溯")
            else:
                with st.spinner("🧠 AI正在进行深度风险分析..."):
                    # 1. 保存产品信息
                    product_id = save_product_info(product_name, product_desc)
                    
                    # 2. 获取联网搜索信息
                    search_info = search_product_risk(product_name, product_desc)
                    
                    # 3. 构建AI提示
                    system_prompt = """你是一位拥有25年以上研发管理经验的资深DQA专家，精通DFMEA、六西格玛设计、可靠性工程。
                    请基于产品信息和行业数据，提供专业的产品设计风险分析。
                    分析要求：
                    1. 专业严谨，基于实际工程经验
                    2. 涵盖：材料风险、结构风险、电子风险、制程风险、可靠性风险、成本风险
                    3. 给出具体风险项、风险等级、改进建议
                    4. 输出格式清晰，便于阅读和导出报告
                    5. 必须包含风险评估参数：严重度(S)、频度(O)、探测度(D)、RPN值
                    """
                    
                    user_prompt = f"""
                    产品名称：{product_name}
                    产品描述：{product_desc}
                    
                    {search_info}
                    
                    请提供全面的产品设计风险分析报告。
                    """
                    
                    # 4. 调用DeepSeek API
                    try:
                        client = openai.OpenAI(
                            api_key=st.secrets["deepseek_api_key"],
                            base_url="https://api.deepseek.com/v1"
                        )
                        
                        response = client.chat.completions.create(
                            model=st.secrets.get("deepseek_model", "deepseek-reasoner"),
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            temperature=0.1,
                            max_tokens=4000
                        )
                        
                        ai_analysis_result = response.choices[0].message.content
                        
                        # 5. 解析风险数据
                        risk_data = parse_risk_data(ai_analysis_result)
                        
                        # 6. 保存分析结果
                        save_risk_analysis(product_id, risk_data)
                        save_analysis_history(product_id, ai_analysis_result)
                        
                        # 7. 显示分析结果
                        st.success("✅ AI深度分析完成！", icon="🎉")
                        
                        st.divider()
                        st.markdown("<h2 class='section-header'>📋 AI风险分析报告</h2>", unsafe_allow_html=True)
                        
                        # --------------------------
                        # 报告区域现在自动铺满宽度
                        # --------------------------
                        st.markdown(ai_analysis_result, unsafe_allow_html=True)
                        
                        # 8. 显示风险数据表格
                        if risk_data:
                            st.subheader("📊 风险优先级清单")
                            df_risk = pd.DataFrame(risk_data)
                            df_display = df_risk[['risk_type', 'severity', 'occurrence', 'detection', 'rpn', 'suggestions']]
                            df_display.columns = ['风险类型', '严重度', '频度', '探测度', 'RPN值', '改进建议']
                            st.dataframe(df_display, use_container_width=True)
                        
                        # 9. 生成Word报告
                        word_buffer = generate_word_report(
                            product_name, product_desc,
                            ai_analysis_result, risk_data,
                            analyst_name, analyst_title
                        )
                        
                        # 10. 下载按钮
                        st.divider()
                        col_download = st.columns([1, 2, 1])
                        with col_download[1]:
                            st.download_button(
                                label="📥 下载完整分析报告(Word)",
                                data=word_buffer,
                                file_name=f"{product_name}_风险分析报告_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx",
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                use_container_width=True
                            )
                            
                    except Exception as e:
                        st.error(f"❌ AI分析出错：{str(e)}")
                        st.info("请检查API密钥配置或稍后重试")
    
    with tab2:
        st.markdown("<h2 class='section-header'>历史分析记录</h2>", unsafe_allow_html=True)
        
        products = get_product_list()
        if products:
            product_options = [f"{p[1]} (创建时间：{p[2]})" for p in products]
            selected_product = st.selectbox("选择产品查看历史记录", product_options)
            
            if selected_product:
                product_idx = product_options.index(selected_product)
                product_id = products[product_idx][0]
                
                # 显示风险数据
                risks = get_product_risks(product_id)
                if risks:
                    st.subheader("📊 风险分析数据")
                    df_risk = pd.DataFrame(risks)
                    df_display = df_risk[['risk_type', 'severity', 'occurrence', 'detection', 'rpn', 'suggestions']]
                    df_display.columns = ['风险类型', '严重度', '频度', '探测度', 'RPN值', '改进建议']
                    st.dataframe(df_display, use_container_width=True)
                    
                    # 可视化RPN值
                    st.subheader("📈 风险优先级(RPN)分布")
                    df_chart = df_display.copy()
                    fig = px.bar(
                        df_chart,
                        x='风险类型',
                        y='RPN值',
                        color='RPN值',
                        title='各风险项RPN值对比',
                        color_continuous_scale='Reds'
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("该产品暂无详细风险分析数据")
        else:
            st.info("暂无历史分析记录，请先进行风险分析")
    
    with tab3:
        st.markdown("<h2 class='section-header'>系统设置</h2>", unsafe_allow_html=True)
        
        st.subheader("数据库信息")
        if os.path.exists("data/dqa_database.db"):
            st.success(f"✅ 数据库正常，文件大小：{os.path.getsize('data/dqa_database.db')/1024:.2f} KB")
        else:
            st.error("❌ 数据库文件不存在")
        
        st.divider()
        st.subheader("关于系统")
        st.markdown("""
        **AI+DQA 产品风险智能分析系统**
        
        版本：v2.0
        
        功能特点：
        - 基于DeepSeek大模型AI分析
        - 整合25年研发管理经验
        - 智能DFMEA风险分析
        - 联网实时行业信息检索
        - 自动生成专业报告
        """)

if __name__ == "__main__":
    main()
