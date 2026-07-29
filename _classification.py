"""
_classification.py — Simplified hierarchical classification.

Just two maps:
  1. TAXONOMY:  Sector (L1) → Industry (L2) → Keyword rules (L3)
  2. SYNONYMS:  keyword synonym mappings (variant → canonical)

The TAXONOMY dict unifies what were previously four separate lists
(SECTORS, INDUSTRIES, ETF_THEMES, INDEX_RULES) into a single nested
structure.  Each sector contains its industries (or ETF themes), and
each industry carries its keyword list plus optional index codes.

Backward-compatible exports are derived from TAXONOMY:
  - ETF_THEME_RULES, ETF_THEMES_COMPAT  (for _study_select_etf.py)
  - classify_etf, classify_industry_from_name, compute_keyword_match_score,
    get_theme_taxonomy  (same signatures as before)
"""
import re
from collections import OrderedDict


# Theme sectors (ETF-specific classifications like 创业板, 港股, 债券, …)
# vs industry sectors (real industries like 金融, 科技, 医药, …).
_THEME_SECTOR_IDS = frozenset({
    "GEM", "STAR", "BROAD", "MMF", "BOND", "MIXED",
    "HK", "OVERSEAS", "SMART", "ALT", "REG", "ESG",
})


# ============================================================================
#  Map 1:  TAXONOMY  —  Sector (L1) → Industry (L2) → Keywords (L3)
# ============================================================================
TAXONOMY = {
    # ── INDUSTRY SECTORS ──────────────────────────────────────────────
    "金融": {
        "_id": "FIN", "_en": "Financials",
        "银行":     {"id": "BANKS",       "en": "Banks",            "slug": "banks",       "kw": ["银行", "中证银行"], "codes": ["399986"], "index": {"399986": "中证银行"}},
        "证券":     {"id": "BROKERS",     "en": "Securities",       "slug": "brokers",     "kw": ["券商", "证券", "证保", "证券公司"], "codes": ["399975"], "index": {"399975": "证券公司"}},
        "保险":     {"id": "INSURANCE",   "en": "Insurance",        "slug": "insurance",   "kw": ["保险"]},
        "金融科技": {"id": "FINTECH",     "en": "FinTech",          "slug": "fintech",     "kw": ["金融科技", "金融"]},
    },
    "科技": {
        "_id": "TECH", "_en": "Technology",
        "半导体":          {"id": "SEMI",          "en": "Semiconductor",            "slug": "semi",           "kw": ["芯片", "半导体", "集成电路", "封测", "光刻", "存储", "晶圆", "IC", "中证半导体", "芯片产业"], "codes": ["931786", "H30007"], "index": {"931786": "中证半导体", "H30007": "芯片产业"}},
        "半导体材料":      {"id": "SEMI_MAT",      "en": "Semi Materials",           "slug": "semi_mat",       "kw": ["半导体材料", "光刻胶", "靶材", "电子特气", "硅片", "抛光片", "电子化学品", "光刻材料", "光刻设备", "玻璃基板"]},
        "CPO/共封装":      {"id": "CPO",           "en": "Co-Packaged Optics",        "slug": "cpo",            "kw": ["CPO", "共封装光学", "光电共封装", "共封装"]},
        "光学/激光":       {"id": "OPTICAL",       "en": "Optical · Laser",           "slug": "optical",        "kw": ["光学", "光电子", "激光", "光通信", "光纤", "光学镜头", "摄像头", "镜头"]},
        "显示面板":        {"id": "DISPLAY",       "en": "Display",                  "slug": "display",        "kw": ["面板", "OLED", "MiniLED", "MicroLED", "显示", "京东方", "TCL"]},
        "传感器":          {"id": "SENSORS",       "en": "Sensors · MEMS",            "slug": "sensors",        "kw": ["传感器", "MEMS", "传感", "CMOS"]},
        "PCB":             {"id": "PCB",           "en": "Printed Circuit Board",     "slug": "pcb",            "kw": ["PCB", "印制电路板", "电路板"]},
        "人工智能":        {"id": "AI",            "en": "AI",                       "slug": "ai",             "kw": ["人工智能", "AI", "大模型", "AIGC", "ChatGPT", "机器视觉", "算力", "中证人工智能"], "codes": ["930713"], "index": {"930713": "中证人工智能"}},
        "云计算/大数据":   {"id": "CLOUD",         "en": "Cloud · Big Data",         "slug": "cloud",          "kw": ["云计算", "大数据"], "index": {"931688": "中证算力"}},
        "软件":            {"id": "SOFTWARE",      "en": "Software",                  "slug": "software",       "kw": ["软件"]},
        "信创":            {"id": "XINCHUANG",     "en": "Xinchuang",                 "slug": "xinchuang",      "kw": ["信创"]},
        "计算机":          {"id": "COMPUTER",      "en": "Computer",                  "slug": "computer",       "kw": ["计算机"]},
        "物联网/工业互联": {"id": "IOT",           "en": "IoT · Industrial Internet", "slug": "iot",             "kw": ["物联网", "工业互联网", "工业互联", "工业软件"]},
        "数字经济":        {"id": "DIGITAL",       "en": "Digital Economy",           "slug": "digital",        "kw": ["数字经济"]},
        "科技创新":        {"id": "TECH_INNOV",    "en": "Sci-Tech Innovation",      "slug": "tech_innov",     "kw": ["科技创新", "科技100", "科技龙头", "科技30", "科技ETF", "科技"], "codes": ["931187"], "index": {"931447": "科技先锋"}},
        "5G/通信":         {"id": "COMMS",         "en": "5G · Communications",       "slug": "comms",          "kw": ["5G", "通信", "电信", "通信设备"], "index": {"931160": "通信设备"}},
        "消费电子":        {"id": "CONSUMER_ELEC", "en": "Consumer Electronics",      "slug": "consumer_elec",  "kw": ["消费电子", "电子50", "电子ETF", "中证电子", "电子"], "codes": ["930652"], "index": {"930652": "中证电子"}},
        "传媒/影视/游戏":  {"id": "MEDIA",         "en": "Media · Gaming",            "slug": "media",          "kw": ["传媒", "影视", "游戏", "VR", "元宇宙"], "codes": ["399971"], "index": {"399971": "中证传媒"}},
        "互联网":          {"id": "INTERNET",      "en": "Internet",                 "slug": "internet",       "kw": ["互联网"]},
        "量子科技":        {"id": "QUANTUM",       "en": "Quantum",                   "slug": "quantum",        "kw": ["量子"]},
        "信息安全/IT":     {"id": "INFO_SEC",      "en": "IT · Cybersecurity",       "slug": "info_sec",       "kw": ["信息安全", "信息技术", "TMT", "中证TMT", "全指信息"], "codes": ["000998", "000993"], "index": {"000998": "中证TMT", "000993": "全指信息"}},
    },
    "医药": {
        "_id": "HC", "_en": "Healthcare",
        "创新药":       {"id": "INNO_DRUG",    "en": "Innovative Drug",     "slug": "inno_drug",    "kw": ["创新药", "生物药", "生物医药", "生物科技"]},
        "医疗器械":     {"id": "MED_DEVICES",  "en": "Medical Devices",     "slug": "med_devices",  "kw": ["医疗器械", "医疗设备", "器械"]},
        "疫苗":         {"id": "VACCINE",      "en": "Vaccines",            "slug": "vaccine",      "kw": ["疫苗", "生物疫苗"]},
        "中药":         {"id": "TCM",          "en": "TCM",                 "slug": "tcm",          "kw": ["中药"]},
        "医药":         {"id": "PHARMA_BROAD", "en": "Broad Pharma",        "slug": "pharma_broad", "kw": ["医药", "医药100", "全指医药"], "codes": ["000978", "000991"], "index": {"000978": "医药100", "000991": "全指医药"}},
        "医疗服务":     {"id": "HC_SERVICES",  "en": "Healthcare Services", "slug": "hc_services",  "kw": ["医疗", "健康", "医院", "CXO", "医美", "中证医疗"], "codes": ["399989"], "index": {"399989": "中证医疗"}},
        "港股生物科技": {"id": "HK_BIOTECH",   "en": "HK Biotech",          "slug": "hk_biotech",   "kw": ["恒生生物科技", "港股通生物科技", "生物科技ETF港股"]},
    },
    "消费": {
        "_id": "CONS", "_en": "Consumer",
        "白酒":       {"id": "BAIJIU",        "en": "Baijiu",                "slug": "baijiu",        "kw": ["白酒", "中证白酒"], "codes": ["399997"], "index": {"399997": "中证白酒"}},
        "必选消费":   {"id": "STAPLES",       "en": "Staples",               "slug": "staples",       "kw": ["消费", "主要消费", "粮油", "饮食"], "codes": ["000932"], "index": {"000932": "主要消费"}},
        "农业":       {"id": "AGRI",          "en": "Agriculture",           "slug": "agri",          "kw": ["农业", "畜牧", "养殖", "粮食", "猪肉"], "index": {"000949": "中证农业"}},
        "食品饮料":   {"id": "FOOD_BEV",      "en": "Food & Beverage",       "slug": "food_bev",      "kw": ["食品", "饮料", "乳业", "生鲜", "酒", "糖"], "index": {"000807": "食品饮料"}},
        "可选消费":   {"id": "DISCRETIONARY", "en": "Discretionary · Auto",  "slug": "discretionary", "kw": ["可选", "消费服务", "家电", "家居", "汽车", "娱乐", "酒店", "旅游", "餐饮", "零售", "纺织", "服装"], "codes": ["000806"], "index": {"000806": "消费服务"}},
        "体育":       {"id": "SPORTS",        "en": "Sports",                "slug": "sports",        "kw": ["体育"], "index": {"399804": "中证体育"}},
        "教育":       {"id": "EDUCATION",     "en": "Education",             "slug": "education",     "kw": ["教育"], "index": {"931456": "中国教育"}},
    },
    "新能源": {
        "_id": "NEV", "_en": "New Energy",
        "光伏":         {"id": "PV",          "en": "PV · Solar",       "slug": "pv",          "kw": ["光伏"], "index": {"931528": "光伏设备行业"}},
        "储能/电池":    {"id": "BATTERY",     "en": "Battery · Storage","slug": "battery",     "kw": ["储能电池", "锂电池", "电池", "储能", "锂电"]},
        "新能源车":     {"id": "EV",          "en": "EV",               "slug": "ev",          "kw": ["新能源车", "新能源汽车", "智能电车", "智能电动车", "电动车", "新能车"], "codes": ["399976"], "index": {"399976": "新能源汽车"}},
        "碳中和/绿电":  {"id": "CARBON",      "en": "Carbon Neutral",   "slug": "carbon",      "kw": ["碳中和", "双碳", "低碳", "绿电", "绿色电力", "风电", "氢能", "核电"]},
        "泛新能源":     {"id": "NEV_GENERAL", "en": "Broad NEV",        "slug": "nev_general", "kw": ["新能源", "新能", "中证新能源"], "codes": ["399808"], "index": {"399808": "中证新能源"}},
    },
    "能源": {
        "_id": "ENG", "_en": "Energy",
        "石油":          {"id": "OIL",         "en": "Oil",                "slug": "oil",         "kw": ["石油"]},
        "原油":          {"id": "CRUDE",       "en": "Crude Oil",          "slug": "crude",       "kw": ["原油"]},
        "油气":          {"id": "OIL_GAS",     "en": "Oil & Gas",          "slug": "oil_gas",     "kw": ["油气", "全球油气"]},
        "天然气":        {"id": "NAT_GAS",     "en": "Natural Gas",        "slug": "nat_gas",     "kw": ["天然气"]},
        "煤炭":          {"id": "COAL",        "en": "Coal",               "slug": "coal",        "kw": ["煤炭", "中证煤炭"], "codes": ["399998"]},
        "电力/电网":     {"id": "POWER_GRID",  "en": "Power · Grid",       "slug": "power_grid",  "kw": ["绿色电力", "电网设备", "电网", "电力", "中证环保", "环保"], "codes": ["000827"], "index": {"000827": "中证环保", "H30199": "中证电力"}},
        "石化/能源化工": {"id": "PETROCHEM",   "en": "Petrochemical",      "slug": "petrochem",   "kw": ["石化", "能源化工"], "codes": ["H11057"], "index": {"H11057": "石化产业"}},
        "能源/资源":     {"id": "ENG_GENERAL", "en": "Broad Energy",      "slug": "eng_general", "kw": ["能源", "资源", "天然", "燃气"]},
    },
    "材料": {
        "_id": "MAT", "_en": "Materials",
        "黄金/贵金属":  {"id": "PRECIOUS",     "en": "Gold · Silver · PM",        "slug": "precious",    "kw": ["黄金", "金ETF", "上海金", "白银", "贵金属"], "index": {"931407": "全球金矿"}},
        "有色金属":     {"id": "METALS",       "en": "Non-Ferrous Metals",        "slug": "metals",      "kw": ["有色金属", "工业有色", "有色"], "index": {"000819": "有色金属"}},
        "稀土":         {"id": "REE",          "en": "Rare Earth",               "slug": "ree",         "kw": ["稀土"], "index": {"930598": "稀土产业"}},
        "稀有金属":     {"id": "RARE_METALS",  "en": "Rare Metals",              "slug": "rare_metals", "kw": ["稀有金属"]},
        "矿业":         {"id": "MINING",      "en": "Mining",                   "slug": "mining",      "kw": ["矿业", "有色矿业"]},
        "化工":         {"id": "CHEM",        "en": "Chemicals",                 "slug": "chem",        "kw": ["化工"]},
        "建材/钢铁":    {"id": "BLDG_STEEL",   "en": "Building Materials · Steel", "slug": "bldg_steel", "kw": ["建材", "钢铁"]},
        "新材料":       {"id": "NEW_MAT",     "en": "New Materials",            "slug": "new_mat",     "kw": ["新材料", "新材"]},
        "材料":         {"id": "MAT_GENERAL", "en": "Broad Materials",           "slug": "mat_general", "kw": ["材料", "金属", "铜", "铝", "化肥", "农药"]},
    },
    "工业": {
        "_id": "IND", "_en": "Industrials",
        "机器人":       {"id": "ROBOTICS",      "en": "Robotics",               "slug": "robotics",      "kw": ["机器人", "工业自动化", "人形机器人", "自动化", "机械臂", "减速器", "伺服电机"], "index": {"H30590": "中证机器人"}},
        "工业母机":     {"id": "MACHINE_TOOL", "en": "Machine Tool",           "slug": "machine_tool",  "kw": ["工业母机", "机床"]},
        "工程机械":     {"id": "ENG_MACHINERY","en": "Engineering Machinery",   "slug": "eng_machinery", "kw": ["工程机械", "机械"]},
        "高端制造":     {"id": "ADVMFG",       "en": "Advanced Manufacturing",  "slug": "advmfg",        "kw": ["高端制造", "高端装备", "智能制造", "工业4", "工业40", "产业升级", "新兴产业"], "index": {"399803": "工业4.0"}},
        "运输/物流":    {"id": "TRANSPORT",    "en": "Transport · Logistics",   "slug": "transport",     "kw": ["高铁", "交运", "运输", "物流", "港口", "高速", "铁路", "航运", "机场"], "codes": ["932536"], "index": {"932536": "航运发展"}},
    },
    "军工": {
        "_id": "MIL", "_en": "Defense & Aerospace",
        "国防装备": {"id": "DEFENSE",   "en": "Defense",   "slug": "defense",   "kw": ["军工", "国防", "军工龙头", "军工50", "军工指数", "中证军工"], "codes": ["399967"], "index": {"399967": "中证军工"}},
        "航空航天": {"id": "AEROSPACE", "en": "Aerospace", "slug": "aerospace", "kw": ["航天", "卫星", "空天", "通用航空", "航空", "中证航空航天", "航空航天"], "codes": ["931066"], "index": {"931066": "中证航空航天", "931594": "卫星产业"}},
    },
    "地产": {
        "_id": "RE", "_en": "Real Estate",
        "地产": {"id": "RE_REAL_ESTATE", "en": "Real Estate", "slug": "re_real_estate", "kw": ["地产", "物业", "开发", "装修", "房地产", "REITs"], "index": {"932076": "全指地产"}},
    },
    "基建": {
        "_id": "INFRA", "_en": "Infrastructure",
        "公用事业":   {"id": "INFRA_UTIL",   "en": "Utilities",            "slug": "infra_utilities",    "kw": ["公用事业", "公用"], "codes": ["000007"], "index": {"000007": "公用指数"}},
        "电力":       {"id": "INFRA_POWER",  "en": "Power",                "slug": "infra_power",        "kw": ["电力", "电网", "电网设备"]},
        "水务":       {"id": "INFRA_WATER",  "en": "Water Utilities",      "slug": "infra_water",        "kw": ["水务", "水利", "供水"]},
        "燃气":       {"id": "INFRA_GAS",    "en": "Gas",                  "slug": "infra_gas",          "kw": ["燃气", "供气"]},
        "公路/交通":  {"id": "INFRA_ROAD",   "en": "Road · Transport",     "slug": "infra_road",         "kw": ["公路", "高速", "路桥", "交通基建"]},
        "建筑/基建":  {"id": "INFRA_CONSTR", "en": "Construction · Infra", "slug": "infra_construction", "kw": ["建筑", "施工", "基建", "新基建", "基础设施"]},
    },

    # ── THEME SECTORS (ETF-specific cross-cutting classifications) ─────
    "创业板": {
        "_id": "GEM", "_en": "ChiNext",
        "创业板｜双创 Dual Board":            {"id": "GEM_DUAL",     "en": "Dual Board",       "slug": "gem_dual",        "kw": ["双创基金ETF", "双创50ETF", "双创ETF基金", "双创龙头ETF", "科创创业50ETF", "科创创业", "双创50", "双创ETF", "双创龙头"]},
        "创业板｜50/大盘 Large Cap":          {"id": "GEM_CORE_L",   "en": "Large Cap",        "slug": "gem_core_large",  "kw": ["创业板50ETF", "创业板50", "创50ETF富国", "创50ETF工银", "创业50ETF", "创50ETF", "创业大盘ETF", "创50"]},
        "创业板｜综指/200 Board-Wide":        {"id": "GEM_CORE_B",   "en": "Board-Wide",       "slug": "gem_core_board",  "kw": ["创业板综增强ETF", "创业板综指增强", "创业板综指ETF", "创业综指ETF", "创业板综ETF", "创业板200ETF", "创业板LOF基金", "创业板LOF", "创业板指数LOF", "创业板指数", "平安创业板ETF", "BOCI创业板ETF", "浦银创业板ETF", "创业板ETF增强", "创业板增强ETF", "创业板ETF东财", "创业板ETF华夏", "创业板ETF南方", "创业板ETF博时", "创业板ETF天弘", "创业板ETF富国", "创业板ETF工银", "创业板ETF广发", "创业板ETF建信", "创业板ETF汇添富", "创业板ETF融通", "创业板ETF万家", "创业板ETF华泰柏瑞", "创业板ETF嘉实", "创业板ETF国泰", "创业板ETF大成", "创业板ETF易方达", "创业板ETF招商", "创业板ETF"], "codes": ["399006"], "index": {"399006": "创业板指"}},
        "创业板｜人工智能 GEM · AI":           {"id": "GEM_AI",       "en": "GEM · AI",         "slug": "gem_ai",          "kw": ["创业板人工智能"]},
        "创业板｜增强策略 GEM · Enhanced":    {"id": "GEM_ENH",      "en": "GEM · Enhanced",   "slug": "gem_enhance",     "kw": ["创业板综增强", "创业板增强"]},
        "创业板｜行业细分 GEM · Sector":      {"id": "GEM_SECTOR",   "en": "GEM · Sector",     "slug": "gem_sector",      "kw": ["创业板医药ETF", "创业板软件ETF", "创业板成长ETF"]},
        "创业板｜定开/LOF GEM · Structured":  {"id": "GEM_STRUCT",   "en": "GEM · Structured", "slug": "gem_structured",  "kw": ["创业板2年定开", "创业板定开南方", "创业板博时定开", "华夏创业板定开", "广发创业板定开", "创业富国定开", "中欧创业定开", "创业定开", "创业板定开"]},
        "创业板｜策略/因子 GEM · Factor":     {"id": "GEM_FACTOR",   "en": "GEM · Factor",     "slug": "gem_factor",      "kw": ["创成长", "创蓝筹", "创信息", "创价值", "创科技", "创业板低波", "创业板动量", "创业板质量", "创业板成长", "创业板价值", "创业板增强", "创业板科技", "创业板消费", "创业板医药", "创业板制造", "创业板材料", "创业板新能源", "创业板金融", "创业板汽车", "创业板军工", "创业板ESG"]},
    },
    "科创板": {
        "_id": "STAR", "_en": "STAR Market",
        "宽基｜科创板 STAR Market": {"id": "BROAD_STAR", "en": "STAR Market", "slug": "broad_star", "kw": ["科创50ETF", "科创板", "科创50", "科创100", "科创200", "科创综指", "科创芯片", "科创新能", "科创信息", "北证50"], "codes": ["000688", "899050", "000680"], "index": {"000688": "科创50", "899050": "北证50", "000680": "科创综指"}},
    },
    "宽基": {
        "_id": "BROAD", "_en": "Broad Market",
        "宽基｜沪深300 CSI 300":             {"id": "BROAD_CSI300",  "en": "CSI 300",         "slug": "broad_csi300",  "kw": ["沪深300", "300ETF", "鹏华300LOF", "信诚300LOF", "巨潮100", "长盛中证800LOF", "中证90LOF", "中证800ETF", "中证800LOF", "中证800增强", "800增强ETF", "800增强", "中证800", "中证A100"], "codes": ["000300", "000510", "000903", "000905", "000906"], "index": {"000300": "沪深300", "000510": "中证A500"}},
        "宽基｜中证500/A500 CSI 500 & A500": {"id": "BROAD_CSI500",  "en": "CSI 500 & A500", "slug": "broad_csi500",  "kw": ["中证500", "500ETF", "中证A500", "A500", "500ETF联接LOF", "500增强LOF", "鹏华500LOF", "信诚500LOF", "泰达500增强", "500增强", "中证500增强", "500增强ETF", "中证1000"], "codes": ["000905"], "index": {"000905": "中证500"}},
        "宽基｜中证1000/2000 CSI 1000/2000": {"id": "BROAD_CSI1000", "en": "CSI 1000/2000",  "slug": "broad_csi1000", "kw": ["中证2000指数", "中证2000增强", "2000增强ETF", "2000指数ETF", "中证1000", "1000ETF", "1000增强", "国证2000", "2000ETF"], "codes": ["000852"], "index": {"000852": "中证1000"}},
        "宽基｜上证50 SSE 50":                {"id": "BROAD_SSE50",   "en": "SSE 50",          "slug": "broad_sse50",    "kw": ["上证50"], "codes": ["000016"], "index": {"000016": "上证50"}},
        "宽基｜上证/深证 SSE/SZSE Composite":{"id": "BROAD_SSE",     "en": "SSE/SZSE Comp",   "slug": "broad_sse_szse","kw": ["深主板50ETF", "深证50", "深证100", "深证300", "深50", "深100", "深300", "深证", "上证", "大盘", "中盘", "小盘", "全指", "A股", "中证100", "国证", "深创", "A50", "中创", "深成", "深F", "央视50", "央视", "新经济", "中小100ETF", "中小企业100LOF", "申万中小LOF", "申万100LOF", "海富通100LOF", "浙江100ETF", "湾区100ETF", "建信优势LOF", "南方天元LOF", "100ETF", "中小板", "中小100", "中小企业", "上证指数", "深成"], "codes": ["000001", "399001", "399004", "399100", "399106", "399305"], "index": {"000001": "上证指数", "000045": "上证小盘", "399001": "深证成指"}},
    },
    "货币": {
        "_id": "MMF", "_en": "Money Market",
        "货币市场｜现金管理 Money Market": {"id": "MMF", "en": "Money Market", "slug": "money_market", "kw": ["货币ETF", "货币", "同业存单", "货基", "短融券", "超短融"]},
    },
    "债券": {
        "_id": "BOND", "_en": "Bonds",
        "债券｜可转债 Convertible":      {"id": "BOND_CONVERTIBLE", "en": "Convertible",     "slug": "bond_convertible", "kw": ["可转债", "转债ETF", "转债", "可交债", "可交换债", "可交换"]},
        "债券｜利率债 Rate · Govt":       {"id": "BOND_RATE",        "en": "Rate · Govt",      "slug": "bond_rate",        "kw": ["科创债", "国债ETF", "国债", "国开债", "政金债", "利率债", "地方债", "地方政府债", "十年国债", "10年国债", "5年国债", "30年国债", "2年国债", "国债期货", "信诚双盈", "融通通福", "万家强化收益", "中海惠裕", "天弘丰利", "天弘同利", "富国天丰", "富国天盈", "富国天锋", "工银四季", "鹏华丰利", "鹏华丰和", "鹏华丰泽", "鹏华丰润", "鹏华丰锐", "金鹰持久增利", "南方金利", "建信丰裕", "招商丰泰", "汇添富季季红", "长信利众", "长信利鑫", "红土创新精选", "国寿精选"], "codes": ["000012"], "index": {"000012": "国债指数"}},
        "债券｜信用债 Credit · Corp":     {"id": "BOND_CREDIT",      "en": "Credit · Corp",    "slug": "bond_credit",      "kw": ["双债", "强债", "综债", "信用债", "公司债", "企业债", "中期票据", "高等级", "高收益", "城投债", "产业债", "鹏华增瑞", "广发聚利", "广发聚源"], "codes": ["000013"], "index": {"000013": "企债指数"}},
        "债券｜短债/纯债 Short-Duration": {"id": "BOND_DURATION",    "en": "Short-Duration",  "slug": "bond_duration",    "kw": ["短债ETF", "短债", "中短债", "超短债", "纯债", "债基", "债券ETF", "债券"]},
    },
    "混合": {
        "_id": "MIXED", "_en": "Mixed Allocation",
        "混合｜股债/平衡/FOF Balanced · FOF": {"id": "MIXED_ASSET", "en": "Balanced · FOF", "slug": "mixed_asset", "kw": ["混合LOF", "混合", "平衡", "偏债", "偏股", "股债", "固收+", "固收增强", "FOF", "联接LOF", "联接", "养老", "目标风险", "目标日期", "稳健配置", "优选配置", "优势回报", "积极配置", "信澳鑫安", "中欧恒利", "中欧瑞丰", "中欧盛世", "中欧趋势", "中欧远见", "东方红创优", "东方红恒阳", "东方红睿丰", "东方红睿华", "东方红睿满", "东方红睿轩", "东方红睿阳", "兴全合兴", "兴全合宜", "兴全合润", "兴全商业模式", "兴全趋势", "兴全轻资产", "华夏磐晟", "华夏磐泰", "华夏蓝筹", "华夏行业", "华安智增", "南方优势产业", "南方高增", "南方积配", "博时主题", "博时卓越", "博时睿利", "博时睿远", "博时研究优选", "博时优势企业", "嘉实瑞享", "嘉实惠泽", "国投瑞利", "国投瑞泰", "国投瑞盈", "国投瑞盛", "国泰估值", "国泰民益", "银华内需", "银华鑫锐", "鹏华优质治理", "鹏华动力", "鹏华盛世创新", "鹏华精选回报", "景顺鼎益", "长城久富", "长盛同智", "长盛同益", "长盛同盛", "中银中国", "九泰泰富", "九泰锐丰", "九泰锐富", "九泰锐智", "九泰锐益", "天治核心", "东海祥龙", "浙商鼎盈", "鼎弘LOF", "鼎泰LOF", "鼎越LOF"]},
    },
    "港股": {
        "_id": "HK", "_en": "Hong Kong",
        "港股｜宽基指数 HK Broad":        {"id": "HK_BROAD_INDEX", "en": "HK Broad",       "slug": "hk_broad_index", "kw": ["恒生港股通科技ETF", "恒生港股通ETF", "恒生50ETF", "恒生中小LOF", "恒生指数ETF", "恒生指数LOF", "恒生ETF港股通", "恒指ETF", "恒生ETF", "恒生LOF", "恒生指数", "沪深港300LOF", "H股ETF港股通", "H股ETF基金", "H股ETF", "H股LOF", "港股精选LOF", "港股通100ETF", "港股通50ETF南方", "港股通50ETF", "港股通ETF", "南方香港LOF", "香港本地LOF", "香港", "H股", "恒指"]},
        "港股｜恒生科技 HK Tech":         {"id": "HK_TECH_INET",   "en": "HK Tech",        "slug": "hk_hst_tech",    "kw": ["恒生科技指数ETF", "恒生科技ETF基金", "恒生科技ETF", "港股科技ETF天弘", "港股科技ETF基金", "港股科技ETF", "港股科技30ETF", "香港科技50ETF", "香港科技ETF", "港股通科技ETF前海开源", "港股通科技30ETF", "港股通科技ETF易方达", "港股通科技ETF招商", "港股通科技ETF平安", "港股通科技ETF基金", "港股通科技ETF南方", "港股通科技ETF博时", "港股通科技ETF", "恒生科技"]},
        "港股｜互联网 HK Internet":      {"id": "HK_INET_WEB",    "en": "HK Internet",    "slug": "hk_internet",    "kw": ["恒生互联网科技ETF", "恒生互联网ETF", "港股互联网ETF", "港股通互联网ETF工银", "港股通互联网ETF永赢", "港股通互联网ETF汇添富", "港股通互联网ETF鹏华", "港股通互联网ETF", "互联网龙头ETF", "互联网ETF沪港深", "互联网ETF", "互联网LOF", "互联网龙头", "互联网", "恒生互联网"]},
        "港股｜信息技术 HK IT · Cloud":  {"id": "HK_INFO",        "en": "HK IT · Cloud",  "slug": "hk_tech_info",   "kw": ["港股信息技术ETF", "港股通信息技术ETF富国", "港股通信息技术ETF易方达", "港股通信息技术ETF鹏华", "沪港深云计算ETF", "云计算ETF汇添富", "云计算ETF"]},
        "港股｜中概互联 China Internet": {"id": "HK_CHINA_INET",  "en": "China Internet", "slug": "hk_china_internet", "kw": ["中概互联ETF", "中概互联网ETF", "中概互联网LOF", "港美互联网LOF", "中概互联"]},
        "港股｜医药 HK Healthcare":      {"id": "HK_HC",          "en": "HK Healthcare",  "slug": "hk_healthcare",  "kw": ["恒生创新药ETF", "恒生医疗指数ETF", "恒生医疗ETF基金", "恒生医疗ETF", "恒生医药ETF", "港股创新药ETF鹏华", "港股创新药ETF", "港股医疗ETF", "港股医药ETF", "港股通创新药ETF南方", "港股通创新药ETF工银", "港股通创新药ETF", "港股通医疗ETF华宝", "港股通医疗ETF工银", "港股通医药ETF", "港股通生物科技ETF", "沪港深创新药ETF", "创新药ETF沪港深"]},
        "港股｜消费/汽车 HK Consumer":   {"id": "HK_CONS",        "en": "HK Consumer",    "slug": "hk_consumer",    "kw": ["港股通消费50ETF", "港股通消费ETF华安", "港股通消费ETF", "港股通汽车ETF鹏华", "港股通汽车ETF"]},
        "港股｜红利 HK Dividend":        {"id": "HK_DIVIDEND",    "en": "HK Dividend",    "slug": "hk_dividend",    "kw": ["港股通红利ETF南方", "港股通红利ETF富国", "港股通红利ETF", "港股红利"]},
        "港股｜其他 HK Misc":            {"id": "HK_GENERAL",     "en": "HK Misc",        "slug": "hk_general",      "kw": ["港股通", "港股", "沪深港", "沪港通", "深港通", "港美", "SSH", "SHS", "AH", "恒生"]},
    },
    "跨境": {
        "_id": "OVERSEAS", "_en": "Overseas",
        "跨境｜美国指数 US Indices":      {"id": "OV_US",     "en": "US Indices",      "slug": "overseas_us",     "kw": ["纳斯达克100ETF", "标普500ETF", "纳指100ETF", "纳斯达克100LOF", "标普500LOF", "纳斯达克指数ETF", "纳指ETF易方达", "标普消费ETF", "标普医疗保健LOF", "美国REIT精选LOF", "美国消费LOF", "美国50ETF", "纳斯达克ETF", "纳指ETF", "标普ETF", "纳斯达克", "纳指", "标普", "美国", "道琼斯", "道琼"]},
        "跨境｜其他海外 Non-US Overseas":  {"id": "OV_NONUS",  "en": "Non-US Overseas","slug": "overseas_nonus",  "kw": ["沙特ETF", "信诚四国LOF", "巴西ETF", "德国ETF", "印度基金LOF", "带路LOF", "亚太低碳ETF", "一带一路", "越南", "英国", "法国", "德国", "欧洲", "亚太", "全球", "海外", "东南亚", "印度", "巴西", "带路", "沙特", "四国", "日经ETF", "日经225ETF", "日经225", "东证ETF", "东京证券", "日股ETF", "日本ETF", "日经", "东京", "日本", "韩国ETF", "KOSPIETF", "KOSDAQETF", "韩国KOSPI", "韩国KOSDAQ", "韩股ETF", "韩国"]},
        "跨境｜宏基/波动率 Macro · VIX":   {"id": "OV_MACRO",  "en": "Macro · VIX",    "slug": "overseas_macro",  "kw": ["VIXETF", "VIX", "vix", "波动率ETF", "波动指数ETF", "恐慌指数", "恐慌ETF", "美元ETF", "美元指数ETF", "欧元ETF", "汇率ETF", "外汇ETF", "通胀挂钩ETF", "美债ETF", "美债20年", "美债10年", "美债长期", "信用违约ETF", "CDXETF", "波动率", "波动指数"]},
    },
    "策略": {
        "_id": "SMART", "_en": "Smart Beta & Factor",
        "策略｜红利 Dividend":          {"id": "SMART_DIV",   "en": "Dividend",       "slug": "smartbeta_dividend",  "kw": ["红利低波动", "红利低波", "红利质量", "央企红利", "国企红利", "港股红利", "红利港股", "红利", "股息", "高股息", "高息", "中证红利"], "codes": ["000922"], "index": {"000922": "中证红利", "000824": "国企红利", "000825": "央企红利", "930740": "300红利低波"}},
        "策略｜低波/价值 Low-Vol · Value":{"id": "SMART_LV",    "en": "Low-Vol · Value","slug": "smartbeta_lv_value",  "kw": ["低波动", "低波", "低贝", "价值", "质量", "基本面", "等权", "动量", "成长", "央企", "国企", "民企", "国企改革"], "index": {"399974": "国企改革"}},
        "策略｜现金流 Cashflow":        {"id": "SMART_CF",    "en": "Cashflow",       "slug": "smartbeta_cashflow",  "kw": ["中证现金流ETF", "自由现金流ETF基金", "自由现金流ETF工银", "自由现金流ETF广发", "自由现金流ETF易方达", "现金流100ETF", "自由现金流ETF", "现金流ETF南方", "现金流ETF基金", "现金流ETF永赢", "现金流ETF汇添富", "现金流ETF长城", "现金流ETF", "800现金流ETF", "现金流", "自由现金流"]},
        "策略｜量化 Multi-Factor":     {"id": "SMART_QUANT", "en": "Multi-Factor",   "slug": "smartbeta_quant",    "kw": ["多因子LOF", "多策略LOF", "申万量化LOF", "多因子", "多策略", "量化"]},
        "策略｜抗通胀 Inflation":       {"id": "SMART_INFL",  "en": "Inflation",      "slug": "smartbeta_inflation","kw": ["抗通胀LOF", "抗通胀"]},
        "策略｜优选 Preferred":         {"id": "SMART_PREF",  "en": "Preferred",      "slug": "smartbeta_preferred","kw": ["开放共赢ETF", "优选LOF", "万家行业优选", "中欧动力LOF", "中欧瑞丰", "鹏华动力", "信诚机遇", "信诚深度", "信诚鼎利", "优选行业", "行业优选", "九泰锐富", "九泰锐智", "九泰锐益", "东海祥龙", "天治核心", "开放共赢", "优选"]},
    },
    "另类": {
        "_id": "ALT", "_en": "Alternatives",
        "另类｜商品 Commodities": {"id": "ALT_COMMODITY", "en": "Commodities", "slug": "alts_commodities", "kw": ["商品", "大宗", "黑金", "豆粕"]},
    },
    "区域": {
        "_id": "REG", "_en": "Regional",
        "区域｜区域经济 Regional Economy": {"id": "REGIONAL", "en": "Regional Economy", "slug": "regional_economy", "kw": ["长三角", "粤港澳", "大湾区", "湾区100ETF", "成渝", "西部", "广东", "北京", "杭州", "安徽", "宁波", "常州", "佛山", "浦东", "张江", "苏州", "无锡", "G60", "湖北", "湾创", "湾区", "浙江", "海洋"], "index": {"932056": "海洋经济"}},
    },
    "ESG": {
        "_id": "ESG", "_en": "ESG & Green",
        "ESG｜绿色/环保/碳 ESG · Green": {"id": "ESG_GREEN", "en": "ESG · Green", "slug": "esg_green", "kw": ["ESG", "300ESG", "绿色", "环保", "可持续", "责任", "气候", "双碳", "碳"], "codes": ["931463"], "index": {"931463": "300ESG", "931800": "绿色生态"}},
    },
}


# ============================================================================
#  Map 2:  SYNONYMS  —  keyword variant → canonical keyword
#  Documents common abbreviation / variant relationships.  The kw lists in
#  TAXONOMY already include all variants, so this map serves as a reference
#  and can be used for future name-normalisation or deduplication.
# ============================================================================
SYNONYMS = {
    # English abbreviations → Chinese canonical
    "IC":       "集成电路",
    "AI":       "人工智能",
    "AIGC":     "人工智能",
    "ChatGPT":  "人工智能",
    "TMT":      "信息技术",
    "MEMS":     "传感器",
    "CMOS":     "传感器",
    "VR":       "元宇宙",
    "PCB":      "电路板",
    "CPO":      "共封装",
    "OLED":     "显示",
    "MiniLED":  "显示",
    "MicroLED": "显示",
    "REITs":    "地产",
    "FOF":      "混合",
    "VIX":      "波动率",
    # Chinese variant → canonical
    "生物医药":   "创新药",
    "生物科技":   "创新药",
    "新能车":     "新能源车",
    "电动车":     "新能源车",
    "绿色电力":   "绿电",
    "锂电池":     "电池",
    "储能电池":   "电池",
    "锂电":       "电池",
    "军工龙头":   "军工",
    "军工50":     "军工",
    "军工指数":   "军工",
    "工业有色":   "有色",
}


# ============================================================================
#  Derived lookup maps (built once at import time)
# ============================================================================
_SECTOR_BY_ID = {}           # sector_id → (sector_cn, sector_en)
_INDUSTRY_BY_NAME = {}       # industry_cn → (sector_id, sector_cn, industry_id, industry_en)
_ENTRIES = []                 # flat list of all entries for iteration
_THEME_ENTRIES = []           # entries in theme sectors (for classify_etf Phase 1)
_INDUSTRY_ENTRIES = []        # entries in industry sectors (for classify_etf Phase 2 / classify_industry_from_name)
_ENTRY_BY_ID = {}             # entry_id → entry dict (augmented with sector info)

for _sector_cn, _sector_data in TAXONOMY.items():
    _sid = _sector_data["_id"]
    _sen = _sector_data["_en"]
    _SECTOR_BY_ID[_sid] = (_sector_cn, _sen)
    _is_theme_sector = _sid in _THEME_SECTOR_IDS
    for _ind_cn, _entry in _sector_data.items():
        if _ind_cn.startswith("_"):
            continue
        _iid = _entry["id"]
        _ien = _entry["en"]
        _slug = _entry["slug"]
        _kw = _entry["kw"]
        _codes = _entry.get("codes", [])
        # Augmented entry for internal use
        _aug = {
            **_entry,
            "sector_id": _sid,
            "sector_cn": _sector_cn,
            "sector_en": _sen,
            "industry_cn": _ind_cn,
        }
        _ENTRY_BY_ID[_iid] = _aug
        _ENTRIES.append(_aug)
        _INDUSTRY_BY_NAME[_ind_cn] = (_sid, _sector_cn, _iid, _ien)
        if _is_theme_sector:
            _THEME_ENTRIES.append(_aug)
        else:
            _INDUSTRY_ENTRIES.append(_aug)

# Rule-order priority: position in _THEME_ENTRIES (earlier = higher priority)
_THEME_RULE_ORDER = {e["id"]: i for i, e in enumerate(_THEME_ENTRIES)}
_THEME_RULE_ORDER["OTHER"] = 9999

# OTHER catch-all entry
_OTHER_ENTRY = {
    "id": "OTHER", "en": "Unclassified", "slug": "other", "kw": [],
    "sector_id": "OTHER", "sector_cn": "其他", "sector_en": "Other",
    "industry_cn": "其他｜未分类  Unclassified",
}
_ENTRY_BY_ID["OTHER"] = _OTHER_ENTRY


# ============================================================================
#  Derived: ICONIC_INDEXES — code -> Chinese short name
#  Flattened from the optional 'index' field on each TAXONOMY entry.
# ============================================================================
ICONIC_INDEXES = {}
for _entry in _ENTRIES:
    for _code, _name in _entry.get("index", {}).items():
        ICONIC_INDEXES[_code] = _name


# ============================================================================
#  Derived: INDUSTRY_INDEX_MAP — industry_id -> (index_code, index_name)
#  The FIRST index in each industry's 'index' dict is treated as the primary
#  tracking index for that industry.  Used as a composition fallback when an
#  ETF has no holdings of its own (build_etf_classification.py writes the
#  index_code/index_name into stats.etf_meta so the TS backend can look it
#  up without duplicating the taxonomy).
# ============================================================================
INDUSTRY_INDEX_MAP = {}
for _entry in _ENTRIES:
    _idx = _entry.get("index", {}) or {}
    if _idx:
        _code, _name = next(iter(_idx.items()))
        INDUSTRY_INDEX_MAP[_entry["id"]] = (_code, _name)


def get_industry_index(industry_id):
    """Return (index_code, index_name) for the given industry_id.

    Returns (None, None) when the industry has no associated tracking index.
    The first index in the industry's 'index' dict is returned (treated as
    the primary tracking index for that industry).
    """
    return INDUSTRY_INDEX_MAP.get(industry_id, (None, None))


# ============================================================================
#  Classifier: ETF (two-phase keyword scoring)
# ============================================================================
def _score_kw_hits(name, kws):
    """Return (total_len, n_hits, longest_kw) for keywords found in name."""
    hits = [kw for kw in kws if kw and kw in name]
    if not hits:
        return None
    return (sum(len(k) for k in hits), len(hits), max(len(k) for k in hits))


def _synthetic_industry_theme(industry_entry):
    """Build (or fetch cached) a synthetic ETF theme from an INDUSTRY entry.

    Used when an ETF name matches an industry keyword (e.g. "半导体ETF",
    "医药ETF", "光伏ETF") but no ETF_THEME.  The synthesised theme_id is
    "IND_<industry_id>".
    """
    iid = industry_entry["id"]
    synthetic_tid = f"IND_{iid}"
    if synthetic_tid in _ENTRY_BY_ID:
        return _ENTRY_BY_ID[synthetic_tid]
    sid = industry_entry["sector_id"]
    sc = industry_entry["sector_cn"]
    ic = industry_entry["industry_cn"]
    ie = industry_entry["en"]
    label = f"{sc}｜{ic}  {ie}"
    entry = {
        "id": synthetic_tid, "en": ie, "slug": f"ind_{iid.lower()}",
        "kw": list(industry_entry["kw"]),
        "sector_id": sid, "sector_cn": sc, "sector_en": industry_entry["sector_en"],
        "industry_cn": label,
    }
    _ENTRY_BY_ID[synthetic_tid] = entry
    _THEME_RULE_ORDER[synthetic_tid] = 10000  # lower priority than real themes
    return entry


def classify_etf(name: str):
    """Classify an ETF by its name using keyword scoring.

    Returns (theme_id, theme_label, slug) — same tuple shape as the
    original _study_select_etf.classify_etf() for backward compatibility.

    Two-phase matching:
      Phase 1 — ETF theme keyword scoring (special themes: 创业板, 港股,
      跨境, 宽基, 债券, 策略, etc. that don't map to a single industry).
      Phase 2 — Industry keyword fallback.  When no theme matches, try
      each industry's keywords (e.g. "半导体", "医药", "光伏").  The ETF
      is then assigned a synthetic theme_id of the form "IND_<id>".

    Scoring (highest wins, rule-order breaks ties):
      1. sum(len(kw))  2. n_hits  3. longest_kw  4. -rule_order
    """
    s = str(name or "")

    # Phase 1: theme keyword scoring
    best = None
    best_score = None
    for entry in _THEME_ENTRIES:
        hits = _score_kw_hits(s, entry["kw"])
        if hits is None:
            continue
        rule_order = _THEME_RULE_ORDER.get(entry["id"], 9999)
        score = (*hits, -rule_order)
        if best_score is None or score > best_score:
            best_score = score
            best = (entry["id"], entry["industry_cn"], entry["slug"])
    if best is not None:
        return best

    # Phase 2: industry keyword fallback (longest keyword wins)
    best_ind = None
    best_ind_kw_len = 0
    for entry in _INDUSTRY_ENTRIES:
        for kw in entry["kw"]:
            if kw and kw in s:
                if len(kw) > best_ind_kw_len:
                    best_ind_kw_len = len(kw)
                    best_ind = entry
                break  # first hit in this industry is enough
    if best_ind is not None:
        synth = _synthetic_industry_theme(best_ind)
        return (synth["id"], synth["industry_cn"], synth["slug"])

    return ("OTHER", _OTHER_ENTRY["industry_cn"], _OTHER_ENTRY["slug"])


def classify_etf_full(name: str):
    """Classify an ETF and return (sector_id, sector_label, theme_id, theme_label, slug)."""
    tid, tlabel, tslug = classify_etf(name)
    entry = _ENTRY_BY_ID.get(tid, _OTHER_ENTRY)
    return (entry["sector_id"], entry["sector_cn"], tid, tlabel, tslug)


def compute_keyword_match_score(name: str, theme_id: str):
    """Score how well an ETF name matches its assigned theme's keywords.

    Returns a sortable tuple (total_len, n_hits, longest_kw) — higher = better.
    """
    s = str(name or "")
    entry = _ENTRY_BY_ID.get(theme_id)
    if entry is None or not entry.get("kw"):
        return (0, 0, 0)
    hits = _score_kw_hits(s, entry["kw"])
    return hits if hits is not None else (0, 0, 0)


# ============================================================================
#  Classifier: Stock (by industry name → sector/industry lookup)
# ============================================================================
def classify_stock(industry_name: str):
    """Classify a stock by its industry label.

    Returns (sector_id, sector_label, industry_id, industry_label).
    """
    if not industry_name or industry_name == "未分类":
        return ("OTHER", "其他", "OTHER", "未分类")

    # 1. Exact match on industry label
    if industry_name in _INDUSTRY_BY_NAME:
        sid, scn, iid, ien = _INDUSTRY_BY_NAME[industry_name]
        return (sid, scn, iid, industry_name)

    # 2. Keyword match against industry keywords
    best = None
    best_score = 0
    for entry in _INDUSTRY_ENTRIES:
        for kw in entry["kw"]:
            if kw in industry_name:
                if len(kw) > best_score:
                    best_score = len(kw)
                    best = (entry["sector_id"], entry["sector_cn"], entry["id"], entry["industry_cn"])
                break
    if best is not None:
        return best

    return ("OTHER", "其他", "OTHER", industry_name)


# ============================================================================
#  Classifier: Industry extraction from ETF/index name
#  (used by build_stock_industry.py to map ETFs → industries for stock mapping)
# ============================================================================
def classify_industry_from_name(name: str):
    """Find the best-matching INDUSTRY for an ETF/index name.

    Unlike classify_etf() (which checks themes first and only falls back
    to industries), this function ALWAYS searches industry keywords
    directly.  This is useful for stock-industry mapping: an ETF like
    "创业板新能源ETF" is primarily a GEM theme, but the stocks it holds
    are 新能源 stocks.

    Returns (sector_id, sector_label, industry_id, industry_label), or
    ("OTHER", "其他", "OTHER", "未分类") if no industry keyword matches.
    """
    s = str(name or "")
    best = None
    best_kw_len = 0
    for entry in _INDUSTRY_ENTRIES:
        for kw in entry["kw"]:
            if kw and kw in s:
                if len(kw) > best_kw_len:
                    best_kw_len = len(kw)
                    best = (entry["sector_id"], entry["sector_cn"], entry["id"], entry["industry_cn"])
                break
    if best is not None:
        return best
    return ("OTHER", "其他", "OTHER", "未分类")


# ============================================================================
#  Classifier: Index (by name + code)
# ============================================================================
def classify_index(name: str, code: str = ""):
    """Classify an index by its name and/or code.

    Returns (sector_id, sector_label, industry_id, industry_label).

    Matching priority:
      1. Exact code match (entries with 'codes' field)
      2. Keyword match on name (longest keyword wins)
      3. Fallback to classify_etf
    """
    name = str(name or "")
    code = str(code or "").strip()

    # 1. Exact code match (check both 'codes' list and 'index' dict keys)
    if code:
        for entry in _ENTRIES:
            if code in entry.get("codes", []) or code in entry.get("index", {}):
                return (entry["sector_id"], entry["sector_cn"], entry["id"], entry["industry_cn"])

    # 2. Keyword match on name (longest keyword wins)
    best = None
    best_score = 0
    for entry in _ENTRIES:
        for kw in entry["kw"]:
            if kw in name:
                if len(kw) > best_score:
                    best_score = len(kw)
                    best = (entry["sector_id"], entry["sector_cn"], entry["id"], entry["industry_cn"])
                break
    if best is not None:
        return best

    # 3. Fallback: try ETF classification
    sid, slab, tid, tlabel, _ = classify_etf_full(name)
    if tid != "OTHER":
        return (sid, slab, tid, tlabel)

    return ("BROAD", "宽基", "BROAD_GENERAL", "综合指数")


def classify_index_full(name: str, code: str = ""):
    """Classify an index and return (sector_id, sector_label, industry_id, industry_label, industry_slug).

    Same as classify_index() but also returns the industry_slug (URL-safe)
    for frontend routing — mirrors classify_etf_full().
    """
    sid, slab, iid, ilabel = classify_index(name, code)
    entry = _ENTRY_BY_ID.get(iid)
    if entry:
        islug = entry["slug"]
    elif iid == "BROAD_GENERAL":
        islug = "broad_general"
    else:
        islug = "other"
    return (sid, slab, iid, ilabel, islug)


# ============================================================================
#  Backward compatibility: ETF_THEME_RULES + ETF_THEMES_COMPAT
#  (derived from TAXONOMY for _study_select_etf.py)
# ============================================================================
# ETF_THEME_RULES: list of (theme_id, label, slug, [keywords])
ETF_THEME_RULES = [
    (e["id"], e["industry_cn"], e["slug"], list(e["kw"]))
    for e in _THEME_ENTRIES
]

# ETF_THEMES_COMPAT: OrderedDict with annotated taxonomy keys
ETF_THEMES_COMPAT = OrderedDict()
for _tid, _label, _slug, _kws in ETF_THEME_RULES:
    ETF_THEMES_COMPAT[_tid] = {
        "theme_label": _label,
        "slug": _slug,
        "kw": list(_kws),
    }
ETF_THEMES_COMPAT["OTHER"] = {
    "theme_label": "其他｜未分类  Unclassified",
    "slug": "other",
    "kw": [],
}


def _parse_taxonomy_label(label):
    """Parse '创业板｜双创 Dual Board' → ('创业板', '双创')."""
    lab = str(label or "").strip()
    if "｜" in lab:
        tg, ind = lab.split("｜", 1)
    else:
        tg, ind = lab, lab
    m = re.match(r"^(.*?)\s{2,}.*$", ind)
    if m:
        ind = m.group(1)
    return tg.strip(), ind.strip()


def _annotate_taxonomy():
    """Populate theme_group_id/theme_group_label/industry_id/industry_label."""
    for tid, cfg in ETF_THEMES_COMPAT.items():
        entry = _ENTRY_BY_ID.get(tid)
        if entry:
            tg_lab = entry["sector_cn"]
            ind_lab = entry["industry_cn"]
            # Parse the industry label to get the short form
            _, short_ind = _parse_taxonomy_label(ind_lab)
            if short_ind:
                ind_lab = short_ind
        else:
            tg_lab, ind_lab = _parse_taxonomy_label(cfg["theme_label"])
            if not ind_lab:
                ind_lab = tg_lab if tg_lab else cfg.get("slug", tid)
        cfg["theme_group_id"] = entry["sector_id"] if entry else "OTHER"
        cfg["theme_group_label"] = tg_lab
        cfg["industry_id"] = tid
        cfg["industry_label"] = ind_lab


_annotate_taxonomy()


def get_theme_taxonomy(theme_id):
    """Return (theme_group_id, theme_group_label, industry_id, industry_label)."""
    cfg = ETF_THEMES_COMPAT.get(theme_id) or ETF_THEMES_COMPAT["OTHER"]
    return (
        cfg.get("theme_group_id", "OTHER"),
        cfg.get("theme_group_label", "其他"),
        cfg.get("industry_id", theme_id),
        cfg.get("industry_label", cfg.get("theme_label", "")),
    )


# ============================================================================
#  Diagnostic / CLI
# ============================================================================
def _main():
    print("=" * 70)
    print("  SIMPLIFIED CLASSIFICATION (two maps)")
    print("=" * 70)

    n_sectors = sum(1 for k in TAXONOMY if not k.startswith("_"))
    n_entries = len(_ENTRIES)
    n_themes = len(_THEME_ENTRIES)
    n_industries = len(_INDUSTRY_ENTRIES)
    print(f"\n  TAXONOMY: {n_sectors} sectors, {n_entries} entries "
          f"({n_themes} themes + {n_industries} industries)")
    print(f"  SYNONYMS: {len(SYNONYMS)} synonym mappings")

    print(f"\n  Sectors (L1):")
    for sector_cn, sd in TAXONOMY.items():
        n = sum(1 for k in sd if not k.startswith("_"))
        tag = "theme" if sd["_id"] in _THEME_SECTOR_IDS else "industry"
        print(f"    {sd['_id']:8s}  {sector_cn:8s}  ({sd['_en']:30s})  {n} {tag}s")

    # Test classifiers
    print("\n" + "=" * 70)
    print("  CLASSIFIER TESTS")
    print("=" * 70)

    test_etfs = [
        ("159001.SZ", "深证100ETF易方达"),
        ("159338.SZ", "中证A500ETF国泰"),
        ("510050.SH", "上证50ETF华夏"),
        ("512480.SH", "半导体ETF"),
        ("512010.SH", "医药ETF"),
        ("515790.SH", "光伏ETF"),
        ("588000.SH", "科创50ETF华夏"),
        ("159915.SZ", "创业板ETF"),
        ("513050.SH", "中概互联ETF"),
    ]
    print("\n  ETF classification:")
    for code, name in test_etfs:
        sid, slab, tid, tlabel, tslug = classify_etf_full(name)
        print(f"    {code}  {name:20s}  → [{sid:8s}/{tid:20s}]  {tlabel[:40]}")

    test_stocks = [
        ("600036", "招商银行", "银行"),
        ("600519", "贵州茅台", "白酒"),
        ("300750", "宁德时代", "电池"),
        ("688981", "中芯国际", "半导体"),
    ]
    print("\n  Stock classification:")
    for code, name, industry in test_stocks:
        sid, slab, iid, ilabel = classify_stock(industry)
        print(f"    {code}  {name:10s}  industry={industry:8s}  → [{sid:6s}/{iid:15s}]  {slab}/{ilabel}")

    test_indices = [
        ("000001", "上证指数"),
        ("000300", "沪深300"),
        ("000688", "科创50"),
        ("399986", "中证银行"),
        ("931786", "中证半导体"),
        ("399997", "中证白酒"),
        ("399967", "中证军工"),
        ("000922", "中证红利"),
    ]
    print("\n  Index classification:")
    for code, name in test_indices:
        sid, slab, iid, ilabel = classify_index(name, code)
        print(f"    {code}  {name:12s}  → [{sid:8s}/{iid:15s}]  {slab}/{ilabel}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    _main()
