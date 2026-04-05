import streamlit as st
import pandas as pd
from typing import Dict, List, Any, Optional
import os
import json

# ==================== 数据库抽象基类 ====================
class RiskDatabase:
    """数据库抽象接口"""
    def get_risks(self, product_type: str) -> List[Dict]:
        raise NotImplementedError
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        raise NotImplementedError
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        raise NotImplementedError
    
    def log_analysis(self, product_name: str, user_id: str, result: str) -> None:
        """记录用户分析历史（可选）"""
        pass

# ==================== 模拟数据库（Fallback） ====================
class MockDatabase(RiskDatabase):
    """模拟知识库（硬编码）"""
    def __init__(self):
        self.product_risks = {
            "LED路灯": {
                "risks": [
                    {"module": "LED光源", "failure_mode": "光衰过快", "cause": "结温过高", "severity": 8, "occurrence": 7, "detection": 5, "mitigation": "优化散热"},
                    # ... 更多模拟数据（为了简洁，只列一条，实际可补充）
                ]
            },
            "高功率天棚灯": {
                "risks": [
                    {"module": "COB光源", "failure_mode": "死灯", "cause": "过温", "severity": 9, "occurrence": 6, "detection": 5, "mitigation": "降额使用"},
                ]
            },
            "default": {
                "risks": [
                    {"module": "PCBA", "failure_mode": "虚焊", "cause": "工艺不良", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "AOI检测"},
                ]
            }
        }
    
    def get_risks(self, product_type: str) -> List[Dict]:
        risks = self.product_risks.get(product_type, self.product_risks["default"])["risks"]
        for r in risks:
            r["RPN"] = r["severity"] * r["occurrence"] * r["detection"]
        return sorted(risks, key=lambda x: x["RPN"], reverse=True)[:10]
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        # 简单规则匹配
        if "路灯" in product_name:
            return {"product_type": "LED路灯", "function_units": ["光学","电气","热学"], "modules": ["LED光源","驱动电源"]}
        elif "天棚灯" in product_name:
            return {"product_type": "高功率天棚灯", "function_units": ["光学","电气","热学","控制"], "modules": ["COB光源","风扇"]}
        else:
            return {"product_type": "default", "function_units": ["电气","机械"], "modules": ["PCBA"]}
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        return f"针对 {module} 的 {failure_mode}，建议：设计优化 + 验证测试。"

# ==================== Neo4j 实现 ====================
class Neo4jDatabase(RiskDatabase):
    def __init__(self, uri: str, user: str, password: str):
        try:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.connected = True
        except Exception as e:
            st.error(f"Neo4j 连接失败: {e}")
            self.connected = False
            self.driver = None
    
    def get_risks(self, product_type: str) -> List[Dict]:
        if not self.connected:
            return MockDatabase().get_risks(product_type)
        with self.driver.session() as session:
            # Cypher 查询示例：根据产品类型获取关联的风险
            result = session.run("""
                MATCH (p:ProductType {name: $ptype})-[:HAS_RISK]->(r:Risk)
                OPTIONAL MATCH (r)-[:MITIGATED_BY]->(m:Mitigation)
                RETURN r.module, r.failure_mode, r.cause, r.severity, r.occurrence, r.detection, m.text AS mitigation
                LIMIT 10
            """, ptype=product_type)
            risks = []
            for record in result:
                risks.append({
                    "module": record["r.module"],
                    "failure_mode": record["r.failure_mode"],
                    "cause": record["r.cause"],
                    "severity": record["r.severity"],
                    "occurrence": record["r.occurrence"],
                    "detection": record["r.detection"],
                    "mitigation": record["mitigation"] or "无记录",
                    "RPN": record["r.severity"] * record["r.occurrence"] * record["r.detection"]
                })
            return sorted(risks, key=lambda x: x["RPN"], reverse=True)[:10]
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        # 可调用 Neo4j 中预定义的产品结构
        # 简化：用规则或返回模拟
        return MockDatabase().get_product_decomposition(product_name, description)
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        return "（来自 Neo4j）详细缓解措施需查询图数据库。"

# ==================== PostgreSQL 实现 ====================
class PostgresDatabase(RiskDatabase):
    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        try:
            import psycopg2
            self.conn = psycopg2.connect(
                host=host, port=port, dbname=database, user=user, password=password
            )
            self.connected = True
        except Exception as e:
            st.error(f"PostgreSQL 连接失败: {e}")
            self.connected = False
    
    def get_risks(self, product_type: str) -> List[Dict]:
        if not self.connected:
            return MockDatabase().get_risks(product_type)
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT module, failure_mode, cause, severity, occurrence, detection, mitigation
            FROM risks WHERE product_type = %s
            ORDER BY (severity * occurrence * detection) DESC
            LIMIT 10
        """, (product_type,))
        rows = cursor.fetchall()
        risks = []
        for row in rows:
            risks.append({
                "module": row[0],
                "failure_mode": row[1],
                "cause": row[2],
                "severity": row[3],
                "occurrence": row[4],
                "detection": row[5],
                "mitigation": row[6],
                "RPN": row[3] * row[4] * row[5]
            })
        return risks
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        # 可从数据库查询产品结构，此处简化
        return MockDatabase().get_product_decomposition(product_name, description)
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        return "（来自 PostgreSQL）请参考数据库记录。"

# ==================== Pinecone 向量数据库实现 ====================
class PineconeDatabase(RiskDatabase):
    def __init__(self, api_key: str, environment: str, index_name: str):
        try:
            import pinecone
            pinecone.init(api_key=api_key, environment=environment)
            self.index = pinecone.Index(index_name)
            self.connected = True
        except Exception as e:
            st.error(f"Pinecone 连接失败: {e}")
            self.connected = False
    
    def get_risks(self, product_type: str) -> List[Dict]:
        # 向量数据库通常用于语义搜索，这里演示根据 product_type 查询
        # 实际应用中，需将 product_type 转为向量并检索最相似的失效模式
        if not self.connected:
            return MockDatabase().get_risks(product_type)
        # 模拟：返回空，示意需要提前索引数据
        st.info("Pinecone 模式需要预先嵌入产品风险向量。当前返回模拟数据。")
        return MockDatabase().get_risks(product_type)
    
    def get_product_decomposition(self, product_name: str, description: str) -> Dict:
        return MockDatabase().get_product_decomposition(product_name, description)
    
    def get_mitigation(self, module: str, failure_mode: str) -> str:
        return "（来自 Pinecone）请基于语义相似检索。"

# ==================== 数据库工厂 ====================
def get_database(choice: str) -> RiskDatabase:
    """根据用户选择返回对应的数据库实例"""
    secrets = st.secrets
    if choice == "Neo4j":
        try:
            return Neo4jDatabase(
                uri=secrets["NEO4J_URI"],
                user=secrets["NEO4J_USER"],
                password=secrets["NEO4J_PASSWORD"]
            )
        except:
            st.warning("Neo4j 凭证未配置，使用模拟数据库")
            return MockDatabase()
    elif choice == "PostgreSQL":
        try:
            return PostgresDatabase(
                host=secrets["PG_HOST"],
                port=secrets.get("PG_PORT", 5432),
                database=secrets["PG_DATABASE"],
                user=secrets["PG_USER"],
                password=secrets["PG_PASSWORD"]
            )
        except:
            st.warning("PostgreSQL 凭证未配置，使用模拟数据库")
            return MockDatabase()
    elif choice == "Pinecone":
        try:
            return PineconeDatabase(
                api_key=secrets["PINECONE_API_KEY"],
                environment=secrets["PINECONE_ENV"],
                index_name=secrets.get("PINECONE_INDEX", "dqa-risks")
            )
        except:
            st.warning("Pinecone 凭证未配置，使用模拟数据库")
            return MockDatabase()
    else:
        return MockDatabase()

# ==================== 辅助函数 ====================
def generate_mitigation_strategy(risk_item: Dict, db: RiskDatabase) -> str:
    """生成缓解策略，优先从数据库获取"""
    base = db.get_mitigation(risk_item["module"], risk_item["failure_mode"])
    strategy = f"""
针对 **{risk_item['module']}** 的 **{risk_item['failure_mode']}** 问题（原因：{risk_item['cause']}），建议如下策略：

1. **设计层面**：{base}
2. **仿真验证**：使用有限元分析/热仿真/电路仿真验证设计余量。
3. **测试标准**：参考 IEC/GB 相关条款，增加 HALT/HASS 测试。
4. **制程管控**：关键工艺参数 SPC 监控，首件确认。
5. **售后反馈**：建立失效分析闭环，持续更新 DFMEA 数据库。

**RPN 评分**：严重度 {risk_item['severity']} × 发生度 {risk_item['occurrence']} × 探测度 {risk_item['detection']} = **{risk_item['RPN']}**
"""
    return strategy

# ==================== 主界面 ====================
st.set_page_config(page_title="AI+DQA 多数据库风险分析", layout="wide")
st.title("🔍 AI+DQA 产品风险分析原型（多数据库版）")

# 语言切换（简化）
if "lang" not in st.session_state:
    st.session_state.lang = "中文"
lang = st.sidebar.radio("Language", ["中文", "English"], index=0 if st.session_state.lang=="中文" else 1)
st.session_state.lang = lang

# 管理员验证
if "admin_auth" not in st.session_state:
    st.session_state.admin_auth = False

with st.sidebar:
    st.header("🔐 管理员")
    admin_pw = st.text_input("管理员密码", type="password")
    if admin_pw:
        # 使用 secrets 中的密码，或默认简单密码
        correct = st.secrets.get("ADMIN_PASSWORD", "admin123")
        if admin_pw == correct:
            st.session_state.admin_auth = True
            st.success("已登录管理员模式")
        else:
            st.error("密码错误")
            st.session_state.admin_auth = False
    
    st.divider()
    st.header("⚙️ 数据库选择")
    db_choice = st.selectbox(
        "当前后端",
        ["模拟数据库 (Mock)", "Neo4j", "PostgreSQL", "Pinecone"],
        index=0
    )
    # 映射到数据库实例
    db_map = {
        "模拟数据库 (Mock)": "Mock",
        "Neo4j": "Neo4j",
        "PostgreSQL": "PostgreSQL",
        "Pinecone": "Pinecone"
    }
    db = get_database(db_map[db_choice])
    
    if st.session_state.admin_auth:
        st.info(f"当前数据库: {db_choice}")

# 主内容区域
st.markdown("基于知识图谱和 GNN 的产品前端风险识别与策略推荐（支持多数据库）")

with st.expander("📘 使用说明"):
    st.markdown("""
    1. 输入产品名称和设计描述
    2. 系统自动分解产品结构（功能件→模块→零件）
    3. 从所选数据库检索 Top 10 风险项（按 RPN 排序）
    4. 点击“生成策略”查看详细设计建议
    5. 管理员可切换数据库后端
    """)

col1, col2 = st.columns(2)
with col1:
    product_name = st.text_input("产品名称", value="高功率LED天棚灯")
with col2:
    product_desc = st.text_area("设计描述", value="功率200W，采用COB光源，主动风扇散热，IP65防护，0-10V调光")

if st.button("🚀 开始风险分析", type="primary"):
    if not product_name:
        st.warning("请输入产品名称")
        st.stop()
    
    with st.spinner("正在分析产品结构..."):
        decomposition = db.get_product_decomposition(product_name, product_desc)
    
    st.subheader("📐 产品分解结果")
    col1, col2, col3 = st.columns(3)
    col1.metric("产品", product_name)
    col2.metric("功能件", ", ".join(decomposition.get("function_units", [])))
    col3.metric("主要模块", ", ".join(decomposition.get("modules", [])[:3]))
    
    with st.spinner(f"正在从 {db_choice} 检索风险..."):
        risks = db.get_risks(decomposition.get("product_type", "default"))
    
    st.subheader("⚠️ Top 10 潜在风险 (按RPN排序)")
    if risks:
        df = pd.DataFrame(risks)
        display_cols = ["module", "failure_mode", "cause", "severity", "occurrence", "detection", "RPN"]
        st.dataframe(df[display_cols], use_container_width=True)
        
        st.subheader("💡 设计策略与缓解措施")
        for idx, risk in enumerate(risks):
            with st.expander(f"{idx+1}. {risk['module']} - {risk['failure_mode']} (RPN={risk['RPN']})"):
                strategy = generate_mitigation_strategy(risk, db)
                st.markdown(strategy)
                if st.button(f"🤖 AI 深度分析", key=f"deep_{idx}"):
                    st.info("正式版将调用大模型+知识图谱生成详细设计参数。")
        
        # 导出
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("📎 下载 FMEA 表格 (CSV)", data=csv, file_name=f"{product_name}_FMEA.csv", mime="text/csv")
    else:
        st.warning("未检索到风险数据，请检查数据库连接或使用模拟模式。")

# 管理员面板（仅认证后显示）
if st.session_state.admin_auth:
    with st.expander("🔧 管理员高级设置"):
        st.subheader("数据库连接配置（示例）")
        st.json({
            "当前选择": db_choice,
            "Neo4j": "已配置" if st.secrets.get("NEO4J_URI") else "未配置",
            "PostgreSQL": "已配置" if st.secrets.get("PG_HOST") else "未配置",
            "Pinecone": "已配置" if st.secrets.get("PINECONE_API_KEY") else "未配置"
        })
        st.info("请在 `.streamlit/secrets.toml` 中配置对应数据库凭证。")
        st.subheader("知识库编辑（仅演示）")
        new_risk = st.text_input("新增风险（格式：模块,失效模式,原因,严重度,发生度,探测度）")
        if st.button("添加临时风险"):
            st.success("演示模式：实际需写入数据库。")

st.caption(f"当前后端：{db_choice} | 数据来源于 {db_choice}（若未配置则降级为模拟）")
