import streamlit as st
import pandas as pd
import json
import re
from typing import Dict, List, Tuple
import random

# 如果不想调用真实LLM，使用模拟生成器
# 若需要真实LLM，请取消注释并设置 OPENAI_API_KEY
# import openai
# openai.api_key = st.secrets["OPENAI_API_KEY"]

# ==================== 模拟知识库 ====================
# 不同产品类型的风险知识库（后续可替换为真实知识图谱查询）
PRODUCT_RISK_DB = {
    "LED路灯": {
        "function_units": ["光学", "电气", "热学", "结构"],
        "risks": [
            {"module": "LED光源", "failure_mode": "光衰过快", "cause": "结温过高/驱动电流过大", "severity": 8, "occurrence": 7, "detection": 5, "mitigation": "采用热仿真优化散热器，限流设计"},
            {"module": "驱动电源", "failure_mode": "电解电容鼓包", "cause": "高温/纹波电流超标", "severity": 9, "occurrence": 6, "detection": 6, "mitigation": "选用长寿命电容，降低纹波，加强散热"},
            {"module": "透镜", "failure_mode": "黄变/透光率下降", "cause": "UV辐射/高温老化", "severity": 6, "occurrence": 5, "detection": 4, "mitigation": "添加UV吸收剂，采用玻璃透镜"},
            {"module": "防水结构", "failure_mode": "进水气导致短路", "cause": "密封圈老化/设计缺陷", "severity": 9, "occurrence": 4, "detection": 7, "mitigation": "IP68验证，双重密封设计"},
            {"module": "浪涌保护", "failure_mode": "雷击损坏", "cause": "无SPD或SPD失效", "severity": 10, "occurrence": 3, "detection": 8, "mitigation": "加装10kV SPD，接地设计"},
            {"module": "PCBA", "failure_mode": "焊点开裂", "cause": "热循环应力", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "选用高TG PCB，优化回流焊工艺"},
            {"module": "螺丝紧固", "failure_mode": "松动导致接触不良", "cause": "振动/扭矩不足", "severity": 5, "occurrence": 4, "detection": 5, "mitigation": "螺纹胶+扭矩规范"},
            {"module": "散热器", "failure_mode": "散热鳍片积尘", "cause": "户外环境", "severity": 4, "occurrence": 8, "detection": 3, "mitigation": "自清洁涂层，定期维护建议"},
            {"module": "线材", "failure_mode": "外皮老化开裂", "cause": "紫外线/臭氧", "severity": 6, "occurrence": 5, "detection": 4, "mitigation": "选用耐UV线材，加套管"},
            {"module": "灌胶", "failure_mode": "灌胶不饱满", "cause": "工艺不良/胶水粘度", "severity": 7, "occurrence": 3, "detection": 8, "mitigation": "真空灌胶工艺，AOI检测"}
        ]
    },
    "高功率天棚灯": {
        "function_units": ["光学", "电气", "热学", "结构", "控制"],
        "risks": [
            {"module": "COB光源", "failure_mode": "死灯", "cause": "金线断裂/芯片过温", "severity": 9, "occurrence": 6, "detection": 5, "mitigation": "选用优质COB，降额使用"},
            {"module": "风扇主动散热", "failure_mode": "风扇停转", "cause": "轴承磨损/堵转", "severity": 8, "occurrence": 7, "detection": 6, "mitigation": "双风扇冗余，转速监控报警"},
            {"module": "驱动电源", "failure_mode": "MOSFET击穿", "cause": "过压/过温", "severity": 9, "occurrence": 5, "detection": 7, "mitigation": "增加保护电路，选用低Rds(on) MOS"},
            {"module": "透镜阵列", "failure_mode": "部分透镜脱落", "cause": "胶水老化/振动", "severity": 6, "occurrence": 3, "detection": 4, "mitigation": "卡扣+胶水双重固定"},
            {"module": "吊装结构", "failure_mode": "掉落风险", "cause": "螺丝松动/材料疲劳", "severity": 10, "occurrence": 2, "detection": 8, "mitigation": "安全钢丝绳+定期检查"},
            {"module": "调光接口", "failure_mode": "调光失效/闪烁", "cause": "信号干扰/不匹配", "severity": 5, "occurrence": 4, "detection": 5, "mitigation": "隔离调光信号，匹配0-10V标准"},
            {"module": "防雷击", "failure_mode": "浪涌损坏", "cause": "电网波动", "severity": 8, "occurrence": 4, "detection": 7, "mitigation": "差模/共模保护，气体放电管"},
            {"module": "密封圈", "failure_mode": "老化漏水", "cause": "高温/化学腐蚀", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "硅胶密封圈，IP67测试"},
            {"module": "线束连接器", "failure_mode": "接触电阻增大发热", "cause": "氧化/松动", "severity": 7, "occurrence": 4, "detection": 5, "mitigation": "镀金端子，防松设计"},
            {"module": "散热器表面处理", "failure_mode": "涂层脱落影响散热", "cause": "附着力不足", "severity": 4, "occurrence": 3, "detection": 4, "mitigation": "阳极氧化+附着力测试"}
        ]
    },
    "LED筒灯": {
        "function_units": ["光学", "电气", "热学", "结构"],
        "risks": [
            {"module": "LED灯珠", "failure_mode": "单颗死灯", "cause": "静电击穿/过流", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "ESD防护，恒流驱动"},
            {"module": "驱动电源", "failure_mode": "频闪", "cause": "纹波过大/电路设计", "severity": 6, "occurrence": 6, "detection": 5, "mitigation": "增加输出滤波，满足IEEE 1789"},
            {"module": "弹簧卡扣", "failure_mode": "断裂导致掉落", "cause": "金属疲劳/材料脆性", "severity": 8, "occurrence": 3, "detection": 4, "mitigation": "选用弹簧钢，疲劳测试"},
            {"module": "扩散板", "failure_mode": "黄变/脆化", "cause": "高温/UV", "severity": 5, "occurrence": 4, "detection": 3, "mitigation": "PC+UV涂层"},
            {"module": "接线端子", "failure_mode": "接触不良发热", "cause": "螺丝未锁紧", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "免螺丝端子+扭力批管控"}
        ]
    },
    "智能洗地机": {
        "function_units": ["机械", "电气", "流体", "控制", "传感"],
        "risks": [
            {"module": "滚刷电机", "failure_mode": "堵转烧毁", "cause": "毛发缠绕/异物卡滞", "severity": 8, "occurrence": 7, "detection": 6, "mitigation": "过流保护+防缠绕结构"},
            {"module": "水泵", "failure_mode": "不出水/流量小", "cause": "堵塞/膜片老化", "severity": 7, "occurrence": 6, "detection": 5, "mitigation": "滤网+自清洁模式"},
            {"module": "电池包", "failure_mode": "续航衰减", "cause": "电芯老化/BMS不均衡", "severity": 6, "occurrence": 8, "detection": 4, "mitigation": "选用A品电芯，均衡充电"},
            {"module": "污水箱传感器", "failure_mode": "误报满水", "cause": "脏污覆盖", "severity": 5, "occurrence": 6, "detection": 3, "mitigation": "双传感器冗余"},
            {"module": "显示屏", "failure_mode": "黑屏/花屏", "cause": "排线松动/静电", "severity": 4, "occurrence": 3, "detection": 5, "mitigation": "FPC连接器加固"},
            {"module": "充电触点", "failure_mode": "氧化接触不良", "cause": "潮湿/腐蚀", "severity": 6, "occurrence": 7, "detection": 4, "mitigation": "镀金+密封设计"}
        ]
    }
}

# 通用默认风险（当产品类型未匹配时）
DEFAULT_RISKS = [
    {"module": "PCBA", "failure_mode": "虚焊", "cause": "回流焊温度不当", "severity": 7, "occurrence": 5, "detection": 6, "mitigation": "AOI检测+工艺优化"},
    {"module": "连接器", "failure_mode": "接触不良", "cause": "插拔力不足", "severity": 6, "occurrence": 4, "detection": 5, "mitigation": "选用品牌连接器"},
    {"module": "外壳", "failure_mode": "开裂", "cause": "应力集中", "severity": 5, "occurrence": 3, "detection": 4, "mitigation": "圆角设计+材料韧性"}
]

# ==================== 辅助函数 ====================
def product_decomposition(product_name: str, description: str) -> Dict:
    """
    产品分解：识别功能件、模块、零件
    当前使用简单规则匹配，后续可替换为LLM调用
    """
    # 模拟分解结果
    decomposition = {
        "product": product_name,
        "function_units": [],
        "modules": [],
        "parts": []
    }
    # 根据产品名称关键词判断类型
    if "路灯" in product_name or "street light" in product_name.lower():
        product_type = "LED路灯"
        decomposition["function_units"] = ["光学", "电气", "热学", "结构"]
        decomposition["modules"] = ["LED光源", "驱动电源", "透镜", "防水结构", "浪涌保护"]
    elif "天棚灯" in product_name or "high bay" in product_name.lower():
        product_type = "高功率天棚灯"
        decomposition["function_units"] = ["光学", "电气", "热学", "结构", "控制"]
        decomposition["modules"] = ["COB光源", "风扇", "驱动电源", "透镜阵列", "吊装结构"]
    elif "筒灯" in product_name or "downlight" in product_name.lower():
        product_type = "LED筒灯"
        decomposition["function_units"] = ["光学", "电气", "热学", "结构"]
        decomposition["modules"] = ["LED灯珠", "驱动电源", "弹簧卡扣", "扩散板"]
    elif "洗地机" in product_name or "cleaner" in product_name.lower():
        product_type = "智能洗地机"
        decomposition["function_units"] = ["机械", "电气", "流体", "控制", "传感"]
        decomposition["modules"] = ["滚刷电机", "水泵", "电池包", "污水箱传感器"]
    else:
        product_type = "general"
        decomposition["function_units"] = ["电气", "机械"]
        decomposition["modules"] = ["PCBA", "连接器", "外壳"]
    
    decomposition["product_type"] = product_type
    return decomposition

def get_risks_from_knowledge(product_type: str) -> List[Dict]:
    """从知识库获取风险列表（模拟图谱查询）"""
    if product_type in PRODUCT_RISK_DB:
        risks = PRODUCT_RISK_DB[product_type]["risks"]
    else:
        risks = DEFAULT_RISKS.copy()
    # 计算RPN = Severity * Occurrence * Detection
    for r in risks:
        r["RPN"] = r["severity"] * r["occurrence"] * r["detection"]
    # 按RPN降序排序，取前10
    risks_sorted = sorted(risks, key=lambda x: x["RPN"], reverse=True)
    return risks_sorted[:10]

def generate_mitigation_strategy(risk_item: Dict) -> str:
    """
    生成缓解策略（模拟LLM生成）
    可替换为真实LLM调用
    """
    # 模拟策略生成
    module = risk_item["module"]
    failure = risk_item["failure_mode"]
    cause = risk_item["cause"]
    base_mitigation = risk_item["mitigation"]
    
    strategy = f"""针对 **{module}** 的 **{failure}** 问题（原因：{cause}），建议如下策略：
    
1. **设计层面**：{base_mitigation}
2. **仿真验证**：使用有限元分析/热仿真/电路仿真验证设计余量。
3. **测试标准**：参考 IEC/GB 相关条款，增加 HALT/HASS 测试。
4. **制程管控**：关键工艺参数 SPC 监控，首件确认。
5. **售后反馈**：建立失效分析闭环，持续更新 DFMEA 数据库。

**RPN 评分**：严重度 {risk_item['severity']} × 发生度 {risk_item['occurrence']} × 探测度 {risk_item['detection']} = **{risk_item['RPN']}**
"""
    return strategy

# ==================== Streamlit UI ====================
st.set_page_config(page_title="AI+DQA 风险分析助手", layout="wide")
st.title("🔍 AI+DQA 产品风险分析原型")
st.markdown("基于知识图谱和GNN的产品前端风险识别与策略推荐")

with st.expander("📘 使用说明"):
    st.markdown("""
    1. 输入产品名称和设计描述（支持中英文）
    2. 系统自动分解产品结构（功能件→模块→零件）
    3. 匹配知识图谱，输出 Top 10 风险项（按 RPN 排序）
    4. 点击“生成策略”查看详细设计建议
    5. 可导出 FMEA 表格
    """)

# 侧边栏输入
with st.sidebar:
    st.header("📝 产品信息")
    product_name = st.text_input("产品名称", value="高功率LED天棚灯")
    product_desc = st.text_area("设计描述", value="功率200W，采用COB光源，主动风扇散热，IP65防护，0-10V调光")
    analyze_btn = st.button("🚀 开始风险分析", type="primary")

# 主区域
if analyze_btn:
    if not product_name:
        st.warning("请输入产品名称")
        st.stop()
    
    # Step 1: 产品分解
    with st.spinner("正在分解产品结构..."):
        decomposition = product_decomposition(product_name, product_desc)
    
    st.subheader("📐 产品分解结果")
    col1, col2, col3 = st.columns(3)
    col1.metric("产品", decomposition["product"])
    col2.metric("功能件", ", ".join(decomposition["function_units"]))
    col3.metric("主要模块", ", ".join(decomposition["modules"][:3]) + ("..." if len(decomposition["modules"])>3 else ""))
    
    # Step 2: 获取风险列表
    with st.spinner("正在检索知识图谱风险项..."):
        risks = get_risks_from_knowledge(decomposition["product_type"])
    
    st.subheader("⚠️ Top 10 潜在风险 (按RPN排序)")
    
    # 显示风险表格
    df_risks = pd.DataFrame(risks)
    df_display = df_risks[["module", "failure_mode", "cause", "severity", "occurrence", "detection", "RPN"]]
    df_display.columns = ["模块", "失效模式", "原因", "严重度(S)", "发生度(O)", "探测度(D)", "RPN"]
    st.dataframe(df_display, use_container_width=True)
    
    # Step 3: 策略生成（可展开每个风险）
    st.subheader("💡 设计策略与缓解措施")
    for idx, risk in enumerate(risks):
        with st.expander(f"{idx+1}. {risk['module']} - {risk['failure_mode']} (RPN={risk['RPN']})"):
            # 模拟生成策略（可替换为LLM调用）
            strategy = generate_mitigation_strategy(risk)
            st.markdown(strategy)
            # 添加一个按钮模拟“AI深度分析”（未来可接入真实LLM）
            if st.button(f"🤖 AI 深度分析", key=f"deep_{idx}"):
                st.info("（演示版）深度分析将调用行业数据库和设计理论模型，输出更详细的设计参数建议。正式版中此功能由LLM+知识图谱实现。")
    
    # Step 4: 导出功能
    st.subheader("📎 导出报告")
    csv = df_risks.to_csv(index=False).encode('utf-8')
    st.download_button("下载 FMEA 表格 (CSV)", data=csv, file_name=f"{product_name}_FMEA.csv", mime="text/csv")
    
    # 显示当前匹配的知识图谱类型
    st.caption(f"当前知识库匹配类型：{decomposition['product_type']} | 数据来源：内置模拟知识库（后续可替换为真实Neo4j+GNN）")

else:
    st.info("请在左侧输入产品信息，点击『开始风险分析』")

# 页脚
st.markdown("---")
st.caption("AI+DQA 原型 v0.1 | 技术路线：本体论+知识图谱+GNN | 演示版本，数据为模拟")
