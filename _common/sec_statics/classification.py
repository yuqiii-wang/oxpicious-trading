"""_common/classification.py — Security classification rules + engine.

Consolidates the classification DATA and the pure LOGIC that operates on
it into a single module.  The classification is split into TWO PARALLEL
rule sets, BOTH using the SAME tuple shape so they feed into a single
unified (sector_id, industry_id) column model in the DB:

  1. INDUSTRY_RULES (sector → industry):
     Real industries — FIN, HC, TECH, NEV, ENG, MIL, AERO, CONS, MAT,
     IND, INFRA, RE, ESG, DEBT.  Each entry is:
       (sector_id, sector_label, industry_id, industry_label, keywords)

  2. STRATEGY_RULES (sector → industry, strategy flavour):
     Strategy/theme indices — BROAD (宽基), DIV (红利), REGION (区域),
     STRATEGY (成长/因子/大盘/小盘), SOE (央企/国企).  Each entry is the
     SAME 5-tuple shape; for these rules sector_id holds the strategy
     (BROAD, DIV, …) and industry_id holds the theme (BROAD_CSI,
     DIV_SOE, …).  There is NO separate strategy_id/theme_id — a
     strategy IS a sector and a theme IS an industry in the unified
     model.

A security is classified against BOTH rule sets.  The
``is_industry_not_strategy`` flag (stored in sec_classification +
sec_index_tags) indicates which classification is PRIMARY for a given
security and therefore which (sector_id, industry_id) pair is written
to the DB:
  - TRUE  → industry is primary (e.g. "中证银行" → FIN/BANKS)
  - FALSE → strategy is primary (e.g. "沪深300" → BROAD/BROAD_CSI)

INDEX_RULES = INDUSTRY_RULES + STRATEGY_RULES is kept so existing
callers (e.g. study_select_etf.py) that iterate over INDEX_RULES
continue to see the full set, and so build_catalog() produces a single
catalog covering both industry and strategy sectors.

Rule order matters: lower index = higher priority for tie-breaking when
a name matches multiple industries/themes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# Industry classification rules (sector → industry)
#   Real industries only — strategy/theme sectors (BROAD, DIV, REGION,
#   STRATEGY, SOE) live in STRATEGY_RULES below.
# ============================================================================
INDUSTRY_RULES: List[Tuple[str, str, str, str, List[str]]] = [
    # --- DEBT (债券) ---
    ("DEBT", "债券", "DEBT_TREASURY", "国债", ["国债"]),
    ("DEBT", "债券", "DEBT_LOCAL", "地方债", ["地债"]),
    ("DEBT", "债券", "DEBT_POLICY", "政金债", ["政金债"]),
    ("DEBT", "债券", "DEBT_CONVERTIBLE", "转债", ["转债", "可交换债"]),
    ("DEBT", "债券", "DEBT_CORP", "信用债", ["城投债", "短融", "科创债", "公司债", "企债", "信用"]),
    # DEBT_GENERAL — catch-all for bond ETFs/LOFs whose name contains 债 but
    # doesn't match a specific debt type above (e.g. 综债, 双债, 强债, 粤债).
    # Safe: 0 stocks have 债 in their name.
    ("DEBT", "债券", "DEBT_GENERAL", "债券", ["债"]),

    # --- FIN (金融) ---
    ("FIN", "金融", "BANKS", "银行", ["银行"]),
    ("FIN", "金融", "BROKERS", "证券", ["证券", "证保", "券商"]),
    ("FIN", "金融", "INSURANCE", "保险", ["保险"]),
    ("FIN", "金融", "FINTECH", "金融科技", ["金融科技"]),
    ("FIN", "金融", "FIN_GENERAL", "金融", ["金融", "非银", "金地", "期货", "信托"]),

    # --- HC (医药) ---
    ("HC", "医药", "INNO_DRUG", "创新药", ["创新药"]),
    ("HC", "医药", "MED_DEVICES", "医疗器械", ["医疗器械"]),
    ("HC", "医药", "TCM", "中药", ["中药"]),
    ("HC", "医药", "VACCINE", "疫苗", ["疫苗"]),
    ("HC", "医药", "BIOTECH", "生物科技", ["生科", "生医", "生物"]),
    # HEALTH (健康) — placed BEFORE PHARMA_BROAD so "健康" (2 chars) wins over
    # "医药" (2 chars) for names containing both (e.g. 医药健康100 → HEALTH,
    # not PHARMA_BROAD).  Catches health-themed ETFs/stocks (健康A/B, 申万健康,
    # 泰康公卫健康, 美年健康, 卫宁健康, …).
    ("HC", "医药", "HEALTH", "健康", ["健康"]),
    ("HC", "医药", "PHARMA_BROAD", "医药", ["医药", "制药", "医疗", "医卫", "药业", "精准医"]),
    ("HC", "医药", "ELDER_CARE", "养老产业", ["养老"]),

    # --- TECH (科技) ---
    ("TECH", "科技", "SEMI", "半导体", ["半导体", "半导", "芯片", "集成电路"]),
    ("TECH", "科技", "AI", "人工智能", ["人工智", "AI"]),
    ("TECH", "科技", "COMPUTE", "算力", ["算力"]),
    ("TECH", "科技", "CLOUD", "云计算", ["云计算", "大数据"]),
    ("TECH", "科技", "SOFTWARE", "软件", ["软件"]),
    ("TECH", "科技", "COMPUTER", "计算机", ["计算机", "信息"]),
    ("TECH", "科技", "COMMS", "通信", ["5G", "通信", "电信"]),
    ("TECH", "科技", "IOT", "物联网", ["物联网", "工业互联", "车联网"]),
    ("TECH", "科技", "INFO_SEC", "信息安全", ["信息安全", "信创"]),
    ("TECH", "科技", "VR", "VR", ["VR"]),
    ("TECH", "科技", "INTERNET", "互联网", ["互联网"]),
    ("TECH", "科技", "CONSUMER_ELEC", "消费电子", ["消费电子", "电子"]),
    ("TECH", "科技", "DIGITAL", "数字经济", ["数字经济", "数据"]),
    ("TECH", "科技", "TMT", "TMT", ["TMT"]),
    # TECH_GENERAL — catch-all for names containing "科技" that don't match a
    # specific tech sub-industry.  Placed LAST in the TECH section so specific
    # rules (SEMI, AI, SOFTWARE, …) win first.  Also prevents "科技" from
    # falling through to the BROAD strategy rule (BROAD_TECH_INNOV), which is
    # designed for broad-market indices, not individual stocks.
    ("TECH", "科技", "TECH_GENERAL", "科技", ["科技"]),

    # --- NEV (新能源) ---
    ("NEV", "新能源", "PV", "光伏", ["光伏"]),
    ("NEV", "新能源", "EV", "新能源车", ["新能源车", "新能车", "智能电车", "电动汽车"]),
    ("NEV", "新能源", "BATTERY", "储能/电池", ["电池", "储能"]),
    ("NEV", "新能源", "CARBON", "碳中和/绿电", ["碳中和", "绿色电力", "绿电", "低碳"]),
    ("NEV", "新能源", "NEV_GENERAL", "新能源", ["新能源", "新能"]),

    # --- ENG (能源) ---
    ("ENG", "能源", "COAL", "煤炭", ["煤炭"]),
    ("ENG", "能源", "OIL_GAS", "油气", ["油气"]),
    ("ENG", "能源", "OIL", "石油", ["石油", "原油"]),
    ("ENG", "能源", "PETROCHEM", "石化", ["石化"]),
    ("ENG", "能源", "NAT_GAS", "天然气", ["天然气"]),
    ("ENG", "能源", "POWER_GRID", "电力/电网", ["电力", "电网"]),
    ("ENG", "能源", "ENG_GENERAL", "能源", ["能源", "资源", "商品", "矿业"]),

    # --- MIL (军工) — defense only; aerospace moved to separate AERO sector ---
    ("MIL", "军工", "MIL_DEFENSE", "国防装备", ["军工龙头", "军工", "国防"]),

    # --- AERO (航空航天) — NEW separate top-level sector ---
    ("AERO", "航空航天", "AERO_SPACE", "航天/卫星", ["卫星", "航天", "空天"]),
    ("AERO", "航空航天", "AERO_AVIATION", "航空", ["通用航空", "全指航空", "航空"]),

    # --- CONS (消费) ---
    ("CONS", "消费", "BAIJIU", "白酒", ["白酒"]),
    ("CONS", "消费", "FOOD_BEV", "食品饮料", ["食品", "酒", "饮料"]),
    ("CONS", "消费", "AGRI", "农业", ["农业", "农牧", "现代农", "粮食", "豆粕"]),
    ("CONS", "消费", "LIVESTOCK", "畜牧", ["畜牧"]),
    ("CONS", "消费", "TOURISM", "旅游", ["旅游", "文旅"]),
    ("CONS", "消费", "SPORTS", "体育", ["体育"]),
    ("CONS", "消费", "GAMES", "游戏", ["游戏"]),
    ("CONS", "消费", "MEDIA", "传媒", ["传媒", "影视", "动漫"]),
    ("CONS", "消费", "DISCRETIONARY", "可选消费", ["可选", "家电", "家用电器"]),
    ("CONS", "消费", "EDU", "教育", ["教育"]),
    ("CONS", "消费", "CONS_GENERAL", "消费", ["消费"]),
    ("CONS", "消费", "RETAIL", "百货/零售", ["百货", "零售", "超市", "商业"]),
    ("CONS", "消费", "HOTEL", "酒店", ["酒店", "饭店"]),

    # --- MAT (材料) ---
    ("MAT", "材料", "RARE_METALS", "稀有金属", ["稀金属", "稀有金属"]),
    ("MAT", "材料", "REE", "稀土", ["稀土"]),
    ("MAT", "材料", "PRECIOUS", "黄金/贵金属", ["黄金", "贵金属", "金矿", "白银", "金ETF"]),
    ("MAT", "材料", "METALS", "有色金属", ["有色"]),
    ("MAT", "材料", "CHEM", "化工", ["化工"]),
    ("MAT", "材料", "BLDG_STEEL", "建材/钢铁", ["钢铁", "建材", "建筑材料"]),
    ("MAT", "材料", "NEW_MAT", "新材料", ["新材料"]),
    ("MAT", "材料", "MAT_GENERAL", "材料", ["材料", "原料"]),

    # --- IND (工业) ---
    ("IND", "工业", "AUTO", "汽车", ["汽车"]),
    ("IND", "工业", "ROBOTICS", "机器人", ["机器人"]),
    ("IND", "工业", "ENG_MACHINERY", "工程机械", ["工程机械", "机械", "重工"]),
    ("IND", "工业", "MACHINE_TOOL", "机床", ["机床"]),
    ("IND", "工业", "TRANSPORT", "运输/物流", ["运输", "船舶", "航运", "物流", "高铁", "铁路", "交运"]),
    ("IND", "工业", "ADVMFG", "高端制造", ["智能制造", "高端制造", "高端装备", "高装", "装备产业", "工业4"]),
    # EXPRESSWAY (高速公路) — placed AFTER TRANSPORT so "高铁"/"铁路" wins for
    # high-speed-rail names; "高速"/"公路" catches expressway toll-road operators
    # (中原高速, 福建高速, 山东高速, 宁沪高速, …) and highway-themed indices.
    ("IND", "工业", "EXPRESSWAY", "高速公路", ["高速", "公路"]),
    ("IND", "工业", "ELEC_EQUIP", "电气设备", ["电气"]),
    ("IND", "工业", "PORT", "港口", ["港口"]),
    ("IND", "工业", "AIRPORT", "机场", ["机场"]),
    ("IND", "工业", "TEXTILE", "纺织服装", ["纺织", "服饰", "服装"]),
    ("IND", "工业", "PAPER", "造纸", ["纸业"]),
    ("IND", "工业", "PRINTING", "印刷", ["印刷"]),
    # IND_GENERAL — catch-all for industrial indices that don't match a
    # specific sub-industry (工程机械/汽车/机器人/…). Placed LAST in
    # the IND section so specific rules win first.  Matches indices like
    # 工业指数, 180工业, 380工业, 500工业, 优势制造, 沪投资品.
    ("IND", "工业", "IND_GENERAL", "工业",
     ["工业", "制造", "投资品", "持续产业",
      "工业制造", "工业4", "工业互联", "工业等权",
      "装备", "装备产业", "优势制造"]),

    # --- INFRA (基建) ---
    ("INFRA", "基建", "INFRA_CONSTR", "建筑/基建", ["基建", "建筑", "建工", "建设"]),
    ("INFRA", "基建", "INFRA_UTIL", "公用事业", ["公用"]),
    ("INFRA", "基建", "WATER", "水务", ["水务"]),
    ("INFRA", "基建", "GAS", "燃气", ["燃气"]),

    # --- RE (地产) ---
    ("RE", "地产", "RE_REAL_ESTATE", "地产", ["地产", "REIT", "置业"]),

    # --- ESG (ESG · Green · Responsibility) ---
    ("ESG", "ESG", "ESG_GENERAL", "ESG", ["ESG", "可持续", "持续发展", "长江保护", "气候"]),
    # 责任 (responsibility) — split from ESG_GENERAL as its own industry.
    # Covers "责任指数", "社会责任", 公司治理/责任 themed indices.
    ("ESG", "ESG", "ESG_RESPONSIBILITY", "责任", ["责任"]),
    ("ESG", "ESG", "GREEN", "绿色环保", ["绿色", "环保"]),
]


# ============================================================================
# Strategy classification rules (sector → industry, strategy flavour)
#   Strategy/theme indices — BROAD (宽基), DIV (红利), REGION (区域),
#   STRATEGY (成长/因子/大盘/小盘), SOE (央企/国企).
#   SAME 5-tuple shape as INDUSTRY_RULES: (sector_id, sector_label,
#   industry_id, industry_label, keywords).  For these rules sector_id
#   holds the strategy (BROAD, DIV, …) and industry_id holds the theme
#   (BROAD_CSI, DIV_SOE, …).  A security is classified against BOTH
#   INDUSTRY_RULES and STRATEGY_RULES; is_industry_not_strategy picks
#   which (sector_id, industry_id) pair is PRIMARY.
# ============================================================================
STRATEGY_RULES: List[Tuple[str, str, str, str, List[str]]] = [
    # --- BROAD (宽基) — broad-market indices, by flagship index series ---
    # Each broad index resolves to ONE flagship series theme.  Most-specific
    # series rules come FIRST; generic board-level catch-alls (上证 / 中证 /
    # 深证) come LAST so a specific match (e.g. "沪深300") wins on keyword
    # length over the catch-all ("中证"/"沪深").  classify_index_strategy_tags
    # additionally deduplicates BROAD tags to a single best, so an index
    # carries exactly ONE broad-market theme (the catch-all is dropped when
    # a specific series matches) — per the "one industry per index" rule.
    # is_broad_market is TRUE for ANY BROAD-primary index (sector_id='BROAD'),
    # regardless of which series theme — sector indices like "中证银行" get
    # BANKS as primary (industry) so their BROAD secondary tag does NOT set
    # is_broad_market.

    # SSE (上证) flagship series
    ("BROAD", "宽基", "BROAD_SSE50", "上证50", ["上证50"]),
    ("BROAD", "宽基", "BROAD_SSE180", "上证180", ["上证180"]),
    ("BROAD", "宽基", "BROAD_SSE380", "上证380", ["上证380"]),
    ("BROAD", "宽基", "BROAD_SSE", "上证", ["上证", "沪综", "综指", "Ａ股", "Ｂ股", "沪市"]),  # residual (上证指数, 上证580, …)

    # CSI (中证/沪深) flagship cross-market series — the core broad-market
    # size-stratified combos.  Each major 沪深/中证 benchmark gets its own
    # theme so ETFs/indices group by the actual tracked series.
    ("BROAD", "宽基", "BROAD_CSI300", "沪深300", ["沪深300"]),
    ("BROAD", "宽基", "BROAD_CSI500", "中证500", ["中证500"]),
    ("BROAD", "宽基", "BROAD_CSI800", "中证800", ["中证800"]),
    ("BROAD", "宽基", "BROAD_CSI1000", "中证1000", ["中证1000"]),
    ("BROAD", "宽基", "BROAD_CSI2000", "中证2000", ["中证2000"]),
    # CSI A-series (中证A股 / A50 / A100 / A500) — newer broad benchmarks
    ("BROAD", "宽基", "BROAD_CSI_A", "中证A系列",
     ["中证A股", "中证A500", "中证A50", "中证A100"]),
    # BROAD_TECH_INNOV placed BEFORE BROAD_CSI catch-all so "中证科技" resolves
    # to 科技创新 (specific) rather than 中证 (generic catch-all).
    ("BROAD", "宽基", "BROAD_TECH_INNOV", "科技创新", ["科技", "创新100"]),
    # CSI generic catch-all (residual 中证/沪深 indices not in a flagship series)
    # "中国" maps to 中证 because broad-market ETFs using "中国" in their name
    # (e.g. 中国A50, 中国50) track CSI (中证) indices.  Safe because BROAD has
    # generic_penalty=0: any industry match (互联网, 教育, 消费, …) wins first.
    ("BROAD", "宽基", "BROAD_CSI", "中证", ["中证", "沪深", "中国"]),

    # ChiNext (创业板) — its own board, split from SZSE.
    # "创业" abbreviation: many names use 创业 without 板 (e.g. 创业50, 创业蓝筹).
    ("BROAD", "宽基", "BROAD_GEM", "创业板", ["创业板", "创业"]),
    # SZSE (深证) residual — "深" as a single-char keyword catches 深100ETF,
    # 深成ETF, 深成指A/B etc. that use "深" as an abbreviation for 深证.
    # Safe because BROAD has generic_penalty=0: any industry match (金融, 能源,
    # 消费, …) always wins over BROAD regardless of keyword length.
    ("BROAD", "宽基", "BROAD_SZSE", "深证", ["深证", "深"]),

    # STAR (科创) board
    ("BROAD", "宽基", "BROAD_STAR", "科创", ["科创"]),

    # BSE (北证) — split from the CSI catch-all
    ("BROAD", "宽基", "BROAD_BSE", "北证", ["北证"]),

    # BROAD_BENCHMARK ("benchmark_broadmarket") — an EXPLICIT, hand-authored
    # tag (NOT auto-classified by name; empty keywords) grouping the
    # flagship broad-market indices used as live-data benchmarks
    # (000001 上证指数, 000016 上证50, 000688 科创50, 000300 沪深300).
    # Carried as a SECONDARY tag alongside each index's primary BROAD series
    # theme so the UI can group/filter "the broad-market benchmark set"
    # independently of which board series they track.  is_broad_market is
    # driven by the PRIMARY BROAD classification, so these still appear in
    # the Market Movements broad-market benchmark dropdown.
    ("BROAD", "宽基", "benchmark_broadmarket", "宽基基准", []),

    # CNI (国证) — China National Index (国证指数有限公司), a separate index
    # provider from CSI (中证).  Real index data is ingested from cnindex.com.cn
    # (国证2000 399303, 国证A50 399310, 国证1000 399311); ETFs tracking 国证
    # indices inherit from the real index.  Orphan ETFs (no CSV parent) fall
    # back to DUMMY_BROAD_CNI only when no real 国证 index matches their name.
    ("BROAD", "宽基", "BROAD_CNI", "国证", ["国证"]),

    # --- DIV (红利) — dividend-themed indices ---
    ("DIV", "红利", "DIV_LOW_VOL", "红利低波", ["红利低波", "红利低波动"]),
    ("DIV", "红利", "DIV_SOE", "央企/国企红利", ["央企红利", "国企红利"]),
    ("DIV", "红利", "DIV_QUALITY", "红利质量", ["红利质量"]),
    ("DIV", "红利", "DIV_VALUE", "红利价值/高息", ["红利价值", "高息策略", "高息精选", "高股息", "高息"]),
    ("DIV", "红利", "DIV_HK", "港股红利", ["港股通高股息", "港股通高息", "港股红利", "香港红利", "港股通央企红利"]),
    # 现金流 (free cash flow) — analytically related to dividends (high FCF
    # enables dividend payments). Grouped under DIV sector but kept as a
    # distinct theme so cash-flow ETFs/indices are not merged into generic
    # 红利. Placed before DIV_GENERAL so "现金流" wins over the catch-all
    # "红利" keyword (e.g. "中证现金流" must NOT match DIV_GENERAL).
    ("DIV", "红利", "DIV_CASHFLOW", "现金流", ["自由现金流", "现金流"]),
    ("DIV", "红利", "DIV_GENERAL", "红利", ["红利", "股东回报"]),

    # --- STRATEGY (策略/因子) — factor/strategy indices ---
    # Track style factors (价值/成长/质量/低波/等权) and size-stratified
    # subsets (中小盘/微小盘).
    ("STRATEGY", "策略", "LEVERAGED", "杠杆/反向",
     ["两倍", "反向", "杠杆"]),
    ("STRATEGY", "策略", "STRAT_THEMED", "主题",
     ["凤凰", "精选市场", "小康", "新兴成指", "结构调整", "济安"]),
    ("STRATEGY", "策略", "STRAT_GROWTH", "成长",
     ["成长"]),
    ("STRATEGY", "策略", "STRAT_LARGE", "大盘",
     ["大盘", "超大盘", "F60"]),
    # 中小盘 — merged from former STRAT_MID (中盘) + STRAT_SMALL (小盘).
    # Both are size-stratified subsets below 大盘; grouped together because
    # the distinction is rarely actionable for ETF selection.
    ("STRATEGY", "策略", "STRAT_MID_SMALL", "中小盘",
     ["中盘", "小盘", "中小", "F120"]),
    # 微小盘 — micro-cap size stratum, smaller than 小盘.
    ("STRATEGY", "策略", "STRAT_MICRO", "微小盘",
     ["微盘", "微小盘"]),
    # 价值 (value) — value-style factor indices. Split out from STRAT_FACTOR
    # so value-themed indices get their own industry. Placed BEFORE
    # STRAT_FACTOR so "价值" wins over the generic factor catch-all keywords.
    # Note: "红利价值" still resolves to DIV/DIV_VALUE because the longer
    # keyword "红利价值" (4 chars) out-scores "价值" (2 chars).
    ("STRATEGY", "策略", "STRAT_VALUE", "价值",
     ["价值"]),
    # 基本面 (fundamental) — fundamental-weighted indices (e.g. 基本面50, a
    # RAFI fundamental index weighted by revenue/cash flow/book value/
    # dividends). Split out from STRAT_FACTOR and pulled out of STRAT_LARGE
    # (基本面50 was formerly grouped under 大盘 via the "基本面50" keyword).
    ("STRATEGY", "策略", "STRAT_FUNDAMENTAL", "基本面",
     ["基本面", "基本"]),
    ("STRATEGY", "策略", "STRAT_FACTOR", "因子",
     ["质量", "低波", "等权", "研发创新",
      "战略新兴", "核心竞争力", "公司治理", "新动能",
      "SNLV", "治理", "基石", "蓝筹", "量化", "贝塔",
      "分层", "动态", "稳定", "波动", "高贝", "低贝",
      "非周期", "市值", "AH"]),
    # Belt and Road / 改革 / 重组 — theme strategies not tied to a sector.
    ("STRATEGY", "策略", "STRAT_THEMED", "主题",
     ["一带", "带路", "一带一路", "改革", "重组", "互联", "百发"]),
    # Brand-themed strategy indices — e.g. 央视500 (CCTV brand index),
    # 百强企业, 品牌工程.  These are brand-licensed strategy indices,
    # not industry-specific.
    ("STRATEGY", "策略", "STRAT_BRAND", "品牌",
     ["央视", "百强企业", "品牌"]),

    # --- SOE (央企/国企) — SOE-themed indices ---
    ("SOE", "央企/国企", "SOE_THEME", "央企/国企", ["央企", "国企", "国资", "民企", "内地国有"]),

    # --- REGION (区域概念) — regional/theme & cross-market access indices ---
    # Mainland regional concepts (长三角/大湾区/成渝…) and HK-connect / 沪港深
    # cross-market access indices that don't map to a single sector.
    ("REGION", "区域概念", "REGION_CN", "区域",
     ["长三角", "G60", "张江", "大湾区", "成渝", "杭州湾区", "湖北", "海洋经济", "丝路", "新丝路"]),
    ("REGION", "区域概念", "REGION_HK_LINK", "沪港深/港股通",
     ["沪港深", "港股通", "港股", "香港", "恒生", "恒生中国", "恒中企", "中华", "股通"]),

    # --- OVERSEAS (海外) — overseas/foreign-market access indices & ETFs ---
    # Cross-border QDII ETFs/LOFs tracking foreign equity markets (纳斯达克,
    # 标普500, 日经, 德国, 巴西, 沙特, 印度, 亚太精选, …).  Classified as a
    # STRATEGY (is_industry_not_strategy=FALSE) — the security's PRIMARY
    # dimension is the overseas market it tracks, not a domestic industry.
    # Sector ETFs tracking a foreign sector index (标普医药, 标普油气, 标普消费)
    # keep their INDUSTRY classification as primary (industry rules are tried
    # first and win); only pure market-access ETFs fall through to OVERSEAS.
    # Keywords are intentionally specific (no generic "全球"/"海外"/"美股"/"亚太")
    # to avoid false-matching stock company names (戎美股份→美股, 亚太实业→亚太)
    # and fund names containing 基金ETF (→金ETF).  "亚太精选" is used instead of
    # "亚太" for the same reason.
    ("OVERSEAS", "海外", "OVERSEAS_US", "美国",
     ["纳斯达克", "纳指", "标普", "美国", "道琼斯"]),
    ("OVERSEAS", "海外", "OVERSEAS_JAPAN", "日本",
     ["日经", "日本"]),
    ("OVERSEAS", "海外", "OVERSEAS_EUROPE", "欧洲",
     ["德国", "法国", "欧洲"]),
    ("OVERSEAS", "海外", "OVERSEAS_EMERGING", "新兴市场",
     ["巴西", "沙特", "印度", "越南"]),
    ("OVERSEAS", "海外", "OVERSEAS_ASIA", "亚太",
     ["亚太精选"]),
]


# ============================================================================
# Combined rules — backwards compatibility
#   INDEX_RULES = INDUSTRY_RULES + STRATEGY_RULES
#   Kept so existing callers (e.g. study_select_etf.py) that iterate over
#   INDEX_RULES continue to see the full set of rules, and so build_catalog()
#   produces a single catalog covering both industry and strategy sectors.
# ============================================================================
INDEX_RULES: List[Tuple[str, str, str, str, List[str]]] = INDUSTRY_RULES + STRATEGY_RULES

# Rule order lookup for tie-breaking (lower index = higher priority)
RULE_ORDER = {(r[0], r[2]): i for i, r in enumerate(INDEX_RULES)}

# Strategy rule order lookup (lower index = higher priority within STRATEGY_RULES)
STRATEGY_RULE_ORDER = {(r[0], r[2]): i for i, r in enumerate(STRATEGY_RULES)}


# ============================================================================
# Default classification for unmatched indices / stocks without a parent index
#   A single (sector_id, industry_id) pair of (OTHER, OTHER) is the fallback
#   for BOTH the industry and the strategy dimension — there is no separate
#   strategy/theme default.
# ============================================================================
DEFAULT_SECTOR_ID = "OTHER"
DEFAULT_SECTOR_LABEL = "其他"
DEFAULT_INDUSTRY_ID = "OTHER"
DEFAULT_INDUSTRY_LABEL = "未分类"

# Sectors that are now strategy-type (migrated from sector to strategy).
# Used by build_classification to determine is_industry_not_strategy and
# by stock-mapping logic to exclude broad-market indices.
STRATEGY_SECTOR_IDS = frozenset({"BROAD", "DIV", "REGION", "STRATEGY", "SOE", "OVERSEAS"})


# ============================================================================
# Classification engine (pure logic — no DB dependency)
# ============================================================================

def classify_index(name: str) -> Tuple[str, str, str, str]:
    """Classify an index by its name using INDUSTRY rules only.

    Returns (sector_id, sector_label, industry_id, industry_label).
    Falls back to (OTHER, 其他, OTHER, 未分类) if no industry rule matches.

    This function ONLY matches real industries (FIN, TECH, HC, ...).
    Strategy-type classifications (BROAD, DIV, REGION, STRATEGY, SOE) are
    handled by ``classify_index_strategy``.
    """
    s = str(name)
    best: Optional[Tuple[str, str, str, str]] = None
    best_score: Optional[Tuple[int, int, int, int, int]] = None

    for sector_id, sector_label, industry_id, industry_label, keywords in INDUSTRY_RULES:
        hits = [kw for kw in keywords if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(kw) for kw in hits)
        longest_kw = max(len(kw) for kw in hits)
        rule_order = RULE_ORDER.get((sector_id, industry_id), 9999)
        # Higher score wins; tie-break by rule order (lower = earlier = higher priority)
        score = (1, total_len, n_hits, longest_kw, -rule_order)
        if best_score is None or score > best_score:
            best_score = score
            best = (sector_id, sector_label, industry_id, industry_label)

    if best is not None:
        return best
    return (DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL,
            DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)


def classify_index_tags(name: str) -> List[Tuple[str, str, str, str]]:
    """Classify an index by name, returning ALL matching INDUSTRY classifications.

    Returns a list of (sector_id, sector_label, industry_id, industry_label)
    tuples sorted by score (highest first).  The first entry is the primary
    industry classification.  An index may match multiple industry rules.

    This function ONLY matches real industries.  Strategy-type tags
    (BROAD, DIV, REGION, ...) are returned by
    ``classify_index_strategy_tags``.

    Falls back to [(OTHER, 其他, OTHER, 未分类)] if no industry rule matches.
    """
    s = str(name)
    matches: List[Tuple[Tuple[int, int, int, int], Tuple[str, str, str, str]]] = []

    for sector_id, sector_label, industry_id, industry_label, keywords in INDUSTRY_RULES:
        hits = [kw for kw in keywords if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(kw) for kw in hits)
        longest_kw = max(len(kw) for kw in hits)
        rule_order = RULE_ORDER.get((sector_id, industry_id), 9999)
        score = (1, total_len, n_hits, longest_kw, -rule_order)
        matches.append((score, (sector_id, sector_label, industry_id, industry_label)))

    if not matches:
        return [(DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL,
                 DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)]

    # Sort by score descending (highest first)
    matches.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate by (sector_id, industry_id) keeping the highest score
    seen: set = set()
    result: List[Tuple[str, str, str, str]] = []
    for _, tag in matches:
        key = (tag[0], tag[2])  # (sector_id, industry_id)
        if key not in seen:
            seen.add(key)
            result.append(tag)

    return result


def classify_index_strategy(name: str) -> Tuple[str, str, str, str]:
    """Classify an index by its name using STRATEGY rules only.

    Returns (sector_id, sector_label, industry_id, industry_label) where, for
    strategy rules, sector_id holds the strategy (BROAD, DIV, …) and
    industry_id holds the theme (BROAD_CSI, DIV_SOE, …).  Falls back to
    (OTHER, 其他, OTHER, 未分类) if no strategy rule matches.
    """
    s = str(name)
    best: Optional[Tuple[str, str, str, str]] = None
    best_score: Optional[Tuple[int, int, int, int, int]] = None

    for sector_id, sector_label, industry_id, industry_label, keywords in STRATEGY_RULES:
        hits = [kw for kw in keywords if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(kw) for kw in hits)
        longest_kw = max(len(kw) for kw in hits)
        rule_order = STRATEGY_RULE_ORDER.get((sector_id, industry_id), 9999)
        # BROAD/REGION penalty: specific strategies (DIV, SOE, STRATEGY)
        # ALWAYS beat BROAD and REGION, regardless of keyword length.
        # Without this, "中证红利" would get BROAD_CSI (keyword "中证" = 2
        # chars) as primary over DIV_GENERAL (keyword "红利" = 2 chars).
        generic_penalty = 0 if sector_id in ("BROAD", "REGION") else 1
        score = (generic_penalty, total_len, n_hits, longest_kw, -rule_order)
        if best_score is None or score > best_score:
            best_score = score
            best = (sector_id, sector_label, industry_id, industry_label)

    if best is not None:
        return best
    return (DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL,
            DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)


def classify_index_strategy_tags(name: str) -> List[Tuple[str, str, str, str]]:
    """Classify an index by name, returning ALL matching STRATEGY classifications.

    Returns a list of (sector_id, sector_label, industry_id, industry_label)
    tuples sorted by score (highest first).  The first entry is the primary
    strategy classification.  For these tuples sector_id holds the strategy
    and industry_id holds the theme.  An index may match multiple strategy
    rules (e.g. "央企红利" matches both DIV/DIV_SOE and SOE/SOE_THEME).

    BROAD tags are deduplicated to a SINGLE best: an index carries exactly
    ONE broad-market theme.  When a specific flagship series (沪深300,
    中证500, 上证50, …) matches, the generic 中证/上证 catch-all is dropped
    so the index isn't double-tagged under BROAD.  Multi-tagging across
    OTHER strategy sectors (DIV + SOE for 央企红利) is preserved.

    Falls back to [(OTHER, 其他, OTHER, 未分类)] if no strategy rule matches.
    """
    s = str(name)
    matches: List[Tuple[Tuple[int, int, int, int], Tuple[str, str, str, str]]] = []

    for sector_id, sector_label, industry_id, industry_label, keywords in STRATEGY_RULES:
        hits = [kw for kw in keywords if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(kw) for kw in hits)
        longest_kw = max(len(kw) for kw in hits)
        rule_order = STRATEGY_RULE_ORDER.get((sector_id, industry_id), 9999)
        generic_penalty = 0 if sector_id in ("BROAD", "REGION") else 1
        score = (generic_penalty, total_len, n_hits, longest_kw, -rule_order)
        matches.append((score, (sector_id, sector_label, industry_id, industry_label)))

    if not matches:
        return [(DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL,
                 DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)]

    matches.sort(key=lambda x: x[0], reverse=True)

    seen: set = set()
    result: List[Tuple[str, str, str, str]] = []
    broad_kept = False
    for _, tag in matches:
        key = (tag[0], tag[2])  # (sector_id, industry_id)
        if key in seen:
            continue
        # BROAD: keep only the single highest-scoring broad-market theme.
        # result is score-sorted, so the first BROAD tag encountered is the
        # most specific flagship series; subsequent BROAD tags (catch-alls)
        # are dropped.  Per the "one industry per index" rule for broad market.
        if tag[0] == "BROAD":
            if broad_kept:
                continue
            broad_kept = True
        seen.add(key)
        result.append(tag)

    return result


def classify_index_all_tags(name: str) -> List[Tuple[str, str, str, str]]:
    """Classify an index by name, returning ALL matching classifications from
    BOTH industry and strategy rule sets.

    Merges ``classify_index_tags`` (industry) and ``classify_index_strategy_tags``
    (strategy) into a single deduplicated list.  Industry tags come first
    (sorted by score), then strategy tags (sorted by score).  The OTHER
    fallback is only appended when NEITHER dimension matched.

    This is the unified replacement for the legacy ``classify_index_tags``
    that iterated over ``INDEX_RULES`` (industry + strategy combined).  It
    populates the ``tags`` field in sec_classification.json and the
    stats.sec_index_tags table so both industry and strategy classifications
    are stored per index — all using the unified (sector_id, industry_id)
    column model.
    """
    ind_tags = classify_index_tags(name)
    strat_tags = classify_index_strategy_tags(name)

    # Strip OTHER fallbacks — only include them if BOTH dims are OTHER.
    ind_real = [t for t in ind_tags if t[0] != DEFAULT_SECTOR_ID]
    strat_real = [t for t in strat_tags if t[0] != DEFAULT_SECTOR_ID]

    if not ind_real and not strat_real:
        return [(DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL,
                 DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)]

    # Merge: industry first, then strategy; deduplicate by (sector_id, industry_id).
    seen: set = set()
    result: List[Tuple[str, str, str, str]] = []
    for tag in ind_real + strat_real:
        key = (tag[0], tag[2])
        if key not in seen:
            seen.add(key)
            result.append(tag)
    return result


def classify_index_both(
    name: str,
) -> Tuple[Tuple[str, str, str, str], Tuple[str, str, str, str], bool]:
    """Classify an index by name, returning BOTH industry and strategy classifications.

    Returns:
        (industry_tuple, strategy_tuple, is_industry_not_strategy)

    where EACH tuple is (sector_id, sector_label, industry_id, industry_label)
    — the strategy tuple's sector_id holds the strategy (BROAD, DIV, …) and
    its industry_id holds the theme (BROAD_CSI, DIV_SOE, …).  The caller
    picks the PRIMARY (sector_id, industry_id) pair from whichever tuple
    matched, using is_industry_not_strategy:
      - TRUE  → industry is primary (industry_tuple has a non-OTHER match)
      - FALSE → strategy is primary (industry_tuple is OTHER, strategy matched)
    """
    ind = classify_index(name)
    strat = classify_index_strategy(name)
    is_industry = ind[0] != DEFAULT_SECTOR_ID
    return (ind, strat, is_industry)


# Standard Chinese ETF legal-name suffix.  ETF names without this suffix are
# "IB names" — foreign-branded ETFs (iShares/安硕, Premia, Global X, TIGER,
# KINDEX, 元大, 富邦) listed in HK/Korea/Taiwan with English or non-standard
# names.  Their ETF names do NOT contain the Chinese index keywords, so they
# must be classified by their underlying index_name instead.
_ETF_CN_SUFFIX = "交易型开放式指数"

# LOF (Listed Open-End Fund) suffix variants — stripped from fund names
# before keyword classification so the theme/industry text is exposed.
# Example: '申万环保LOF' → '环保' → matches GREEN.
_LOF_MARKERS = ("LOF", "lof", "Lof", "上市开放式基金")


def _is_ib_name(etf_name: str) -> bool:
    """Return True if the ETF name is a foreign-branded 'IB name'.

    IB names lack the standard Chinese ETF suffix '交易型开放式指数'
    (e.g. 'Premia CSI Caixin China New Economy ETF', '元大沪深300单日正向2倍ETF').
    """
    return _ETF_CN_SUFFIX not in str(etf_name)


def classify_etf_by_name(
    etf_name: str,
    index_name: str = "",
) -> Tuple[str, str, str, str]:
    """Classify an ETF by its name when parent-index inheritance gives OTHER.

    ETF names typically embed the index name (with a fund-manager prefix and
    '交易型开放式指数证券投资基金' suffix), so keyword rules work on the ETF
    name directly — BUT the legal suffix must be stripped first because it
    contains '证券' (from '证券投资基金') which would falsely match
    FIN/BROKERS.

    Exception: 'IB names' (foreign-branded ETFs whose names lack the
    standard Chinese suffix) have English names — for these, classify by
    the underlying ``index_name`` (which is in Chinese) instead.  If the
    index_name also fails to match, fall back to the ETF name as a last
    resort (some IB names like 'iShares 安硕沪深300' contain enough
    Chinese text to match).

    Returns (sector_id, sector_label, industry_id, industry_label).
    Falls back to (OTHER, 其他, OTHER, 未分类) if no rule matches.
    """
    etf_name = str(etf_name or "")
    index_name = str(index_name or "")

    if _is_ib_name(etf_name):
        # IB name: try the Chinese index name first.
        if index_name:
            result = classify_index(index_name)
            if result[0] != DEFAULT_SECTOR_ID:
                return result
        # Fall back to the ETF name (may contain mixed Chinese/English).
        return classify_index(etf_name)

    # Standard Chinese ETF name: strip the legal suffix '交易型开放式指数
    # 证券投资基金' (and trailing '(QDII)') to avoid false matches on
    # '证券' from '证券投资基金', then classify by the remaining name.
    clean_name = etf_name.split("交易型")[0]
    # Also strip LOF suffix (上市开放式基金) so theme keywords are exposed.
    for marker in _LOF_MARKERS:
        lof_idx = clean_name.find(marker)
        if lof_idx > 0:
            clean_name = clean_name[:lof_idx]
            break
    return classify_index(clean_name)


def classify_etf_strategy_by_name(
    etf_name: str,
    index_name: str = "",
) -> Tuple[str, str, str, str]:
    """Classify an ETF's STRATEGY by name (parallel to classify_etf_by_name).

    Same logic as ``classify_etf_by_name`` but uses STRATEGY_RULES instead
    of INDUSTRY_RULES.  Returns (sector_id, sector_label, industry_id,
    industry_label) where sector_id holds the strategy and industry_id
    holds the theme.  Falls back to (OTHER, 其他, OTHER, 未分类) if no
    strategy rule matches.
    """
    etf_name = str(etf_name or "")
    index_name = str(index_name or "")

    if _is_ib_name(etf_name):
        if index_name:
            result = classify_index_strategy(index_name)
            if result[0] != DEFAULT_SECTOR_ID:
                return result
        return classify_index_strategy(etf_name)

    clean_name = etf_name.split("交易型")[0]
    # Also strip LOF suffix so strategy keywords are exposed.
    for marker in _LOF_MARKERS:
        lof_idx = clean_name.find(marker)
        if lof_idx > 0:
            clean_name = clean_name[:lof_idx]
            break
    return classify_index_strategy(clean_name)
