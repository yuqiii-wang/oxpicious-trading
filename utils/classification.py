"""utils/classification.py — Security classification rules + engine.

Consolidates the classification DATA (INDEX_RULES) and the pure LOGIC
that operates on it (classify_index, classify_index_tags,
classify_etf_by_name, _is_ib_name) into a single module.  Previously the
data lived in utils/classification_rules.py and the logic in
builds/classification/__main__.py — split only to break a cross-layer
import.  This consolidation puts data and logic together so any module
can classify securities without depending on the builds package.

Each INDEX_RULES entry is a 5-tuple:
    (sector_id, sector_label, industry_id, industry_label, keywords)

Rule order matters: lower index = higher priority for tie-breaking when
a name matches multiple industries.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# Classification rules (data)
# ============================================================================
INDEX_RULES: List[Tuple[str, str, str, str, List[str]]] = [
    # --- DEBT (债券) ---
    ("DEBT", "债券", "DEBT_TREASURY", "国债", ["国债"]),
    ("DEBT", "债券", "DEBT_LOCAL", "地方债", ["地债"]),
    ("DEBT", "债券", "DEBT_POLICY", "政金债", ["政金债"]),
    ("DEBT", "债券", "DEBT_CONVERTIBLE", "转债", ["转债", "可交换债"]),
    ("DEBT", "债券", "DEBT_CORP", "信用债", ["城投债", "短融", "科创债", "公司债", "企债"]),

    # --- FIN (金融) ---
    ("FIN", "金融", "BANKS", "银行", ["银行"]),
    ("FIN", "金融", "BROKERS", "证券", ["证券", "证保"]),
    ("FIN", "金融", "FINTECH", "金融科技", ["金融科技"]),
    ("FIN", "金融", "FIN_GENERAL", "金融", ["金融", "非银", "金地"]),

    # --- HC (医药) ---
    ("HC", "医药", "INNO_DRUG", "创新药", ["创新药"]),
    ("HC", "医药", "MED_DEVICES", "医疗器械", ["医疗器械"]),
    ("HC", "医药", "TCM", "中药", ["中药"]),
    ("HC", "医药", "VACCINE", "疫苗", ["疫苗"]),
    ("HC", "医药", "BIOTECH", "生物科技", ["生科", "生医", "生物"]),
    ("HC", "医药", "PHARMA_BROAD", "医药", ["医药", "制药", "医疗", "医卫"]),
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

    # --- NEV (新能源) ---
    ("NEV", "新能源", "PV", "光伏", ["光伏"]),
    ("NEV", "新能源", "EV", "新能源车", ["新能源车", "新能车", "智能电车", "电动汽车"]),
    ("NEV", "新能源", "BATTERY", "储能/电池", ["电池", "储能"]),
    ("NEV", "新能源", "CARBON", "碳中和/绿电", ["碳中和", "绿色电力", "绿电", "低碳"]),
    ("NEV", "新能源", "NEV_GENERAL", "新能源", ["新能源", "新能"]),

    # --- ENG (能源) ---
    ("ENG", "能源", "COAL", "煤炭", ["煤炭"]),
    ("ENG", "能源", "OIL_GAS", "油气", ["油气"]),
    ("ENG", "能源", "OIL", "石油", ["石油"]),
    ("ENG", "能源", "PETROCHEM", "石化", ["石化"]),
    ("ENG", "能源", "NAT_GAS", "天然气", ["天然气"]),
    ("ENG", "能源", "POWER_GRID", "电力/电网", ["电力", "电网"]),
    ("ENG", "能源", "ENG_GENERAL", "能源", ["能源", "资源", "商品"]),

    # --- MIL (军工) — defense only; aerospace moved to separate AERO sector ---
    ("MIL", "军工", "MIL_DEFENSE", "国防装备", ["军工龙头", "军工", "国防"]),

    # --- AERO (航空航天) — NEW separate top-level sector ---
    ("AERO", "航空航天", "AERO_SPACE", "航天/卫星", ["卫星", "航天", "空天"]),
    ("AERO", "航空航天", "AERO_AVIATION", "航空", ["通用航空", "全指航空", "航空"]),

    # --- DIV (红利) — NEW sector for dividend-themed indices ---
    ("DIV", "红利", "DIV_LOW_VOL", "红利低波", ["红利低波", "红利低波动"]),
    ("DIV", "红利", "DIV_SOE", "央企/国企红利", ["央企红利", "国企红利"]),
    ("DIV", "红利", "DIV_QUALITY", "红利质量", ["红利质量"]),
    ("DIV", "红利", "DIV_VALUE", "红利价值/高息", ["红利价值", "高息策略", "高息精选", "高股息", "高息"]),
    ("DIV", "红利", "DIV_HK", "港股红利", ["港股通高股息", "港股通高息", "港股红利", "香港红利", "港股通央企红利"]),
    ("DIV", "红利", "DIV_GENERAL", "红利", ["红利", "股东回报"]),

    # --- CONS (消费) ---
    ("CONS", "消费", "BAIJIU", "白酒", ["白酒"]),
    ("CONS", "消费", "FOOD_BEV", "食品饮料", ["食品", "酒"]),
    ("CONS", "消费", "AGRI", "农业", ["农业", "农牧", "现代农"]),
    ("CONS", "消费", "LIVESTOCK", "畜牧", ["畜牧"]),
    ("CONS", "消费", "TOURISM", "旅游", ["旅游"]),
    ("CONS", "消费", "SPORTS", "体育", ["体育"]),
    ("CONS", "消费", "GAMES", "游戏", ["游戏"]),
    ("CONS", "消费", "MEDIA", "传媒", ["传媒", "影视", "动漫"]),
    ("CONS", "消费", "DISCRETIONARY", "可选消费", ["可选", "家电", "家用电器"]),
    ("CONS", "消费", "EDU", "教育", ["教育"]),
    ("CONS", "消费", "CONS_GENERAL", "消费", ["消费", "品牌"]),

    # --- MAT (材料) ---
    ("MAT", "材料", "RARE_METALS", "稀有金属", ["稀金属", "稀有金属"]),
    ("MAT", "材料", "REE", "稀土", ["稀土"]),
    ("MAT", "材料", "PRECIOUS", "黄金/贵金属", ["黄金", "贵金属", "金矿"]),
    ("MAT", "材料", "METALS", "有色金属", ["有色"]),
    ("MAT", "材料", "CHEM", "化工", ["化工"]),
    ("MAT", "材料", "BLDG_STEEL", "建材/钢铁", ["钢铁", "建材", "建筑材料"]),
    ("MAT", "材料", "NEW_MAT", "新材料", ["新材料"]),
    ("MAT", "材料", "MAT_GENERAL", "材料", ["材料"]),

    # --- IND (工业) ---
    ("IND", "工业", "AUTO", "汽车", ["汽车"]),
    ("IND", "工业", "ROBOTICS", "机器人", ["机器人"]),
    ("IND", "工业", "ENG_MACHINERY", "工程机械", ["工程机械", "机械"]),
    ("IND", "工业", "MACHINE_TOOL", "机床", ["机床"]),
    ("IND", "工业", "TRANSPORT", "运输/物流", ["运输", "船舶", "航运", "物流"]),
    ("IND", "工业", "ADVMFG", "高端制造", ["智能制造", "高端制造", "高端装备", "高装", "装备产业", "工业4"]),

    # --- INFRA (基建) ---
    ("INFRA", "基建", "INFRA_CONSTR", "建筑/基建", ["基建", "建筑"]),
    ("INFRA", "基建", "INFRA_UTIL", "公用事业", ["公用"]),

    # --- RE (地产) ---
    ("RE", "地产", "RE_REAL_ESTATE", "地产", ["地产"]),

    # --- ESG (ESG · Green) ---
    ("ESG", "ESG", "ESG_GENERAL", "ESG", ["ESG", "可持续", "持续发展", "长江保护", "责任", "气候"]),
    ("ESG", "ESG", "GREEN", "绿色环保", ["绿色", "环保"]),

    # --- STRATEGY (策略/因子) — factor/strategy indices, NOT broad-market ---
    # These were formerly misclassified under BROAD as BROAD_FACTOR.
    # They track style factors (价值/成长/质量/低波/等权) and size-stratified
    # subsets (中盘/小盘) and are NOT representative of the overall market board.
    ("STRATEGY", "策略", "LEVERAGED", "杠杆/反向",
     ["两倍", "反向", "杠杆"]),
    ("STRATEGY", "策略", "STRAT_THEMED", "主题",
     ["品牌工程", "凤凰", "精选市场", "小康", "新兴成指", "结构调整"]),
    ("STRATEGY", "策略", "STRAT_GROWTH", "成长",
     ["成长"]),
    ("STRATEGY", "策略", "STRAT_LARGE", "大盘",
     ["大盘", "超大盘", "基本面50", "F60"]),
    ("STRATEGY", "策略", "STRAT_MID", "中盘",
     ["中盘", "F120"]),
    ("STRATEGY", "策略", "STRAT_SMALL", "小盘",
     ["小盘"]),
    ("STRATEGY", "策略", "STRAT_FACTOR", "因子",
     ["价值", "现金流", "质量", "低波", "等权", "基本面", "研发创新",
      "战略新兴", "核心竞争力", "公司治理", "新动能",
      "SNLV", "治理", "基石"]),

    # --- SOE (央企/国企) — SOE-themed indices, NOT broad-market ---
    # These were formerly misclassified under BROAD as BROAD_SOE.
    # They track SOE themes (央企/国企/国资) and are NOT market-board indices.
    ("SOE", "央企/国企", "SOE_THEME", "央企/国企", ["央企", "国企", "国资", "民企", "内地国有"]),

    # --- REGION (区域概念) — regional/theme & cross-market access indices ---
    # Mainland regional concepts (长三角/大湾区/成渝…) and HK-connect / 沪港深
    # cross-market access indices that don't map to a single sector.
    ("REGION", "区域概念", "REGION_CN", "区域",
     ["长三角", "G60", "张江", "大湾区", "成渝", "杭州湾区", "湖北", "海洋经济"]),
    ("REGION", "区域概念", "REGION_HK_LINK", "沪港深/港股通",
     ["沪港深", "港股通", "香港", "中华"]),

    # --- BROAD (宽基) — broad-market indices ---
    # Board-level sub-industries: CSI (cross-market), SSE (Shanghai),
    # SZSE (Shenzhen), STAR (科创).  Plus BROAD_TECH_INNOV (科技创新) —
    # cross-cutting tech-innovation themes (中证科技, 科技50, 科技龙头, …)
    # that span multiple TECH sub-industries and represent the broad tech
    # market rather than a single sub-industry.
    # Factor/strategy → STRATEGY, SOE-theme → SOE, HK/cross-border →
    #   tagged by their underlying sector (TECH/FIN/…) or OTHER.
    # is_broad_market is TRUE ONLY for BROAD primary tags — sector indices
    # like "中证银行" get BANKS as primary (higher rule priority) so their
    # BROAD_CSI secondary tag does NOT set is_broad_market.
    ("BROAD", "宽基", "BROAD_STAR", "科创", ["科创"]),
    ("BROAD", "宽基", "BROAD_SZSE", "深证", ["深证", "创业板"]),
    ("BROAD", "宽基", "BROAD_SSE", "上证", ["上证"]),
    # BROAD_TECH_INNOV placed BEFORE BROAD_CSI so "中证科技" resolves to
    # 科技创新 (more specific) rather than 中证 (generic catch-all).
    ("BROAD", "宽基", "BROAD_TECH_INNOV", "科技创新", ["科技", "创新100"]),
    ("BROAD", "宽基", "BROAD_CSI", "中证", ["中证", "沪深", "A股", "北证"]),
]

# Rule order lookup for tie-breaking (lower index = higher priority)
RULE_ORDER = {(r[0], r[2]): i for i, r in enumerate(INDEX_RULES)}


# ============================================================================
# Default classification for unmatched indices / stocks without a parent index
# ============================================================================
DEFAULT_SECTOR_ID = "OTHER"
DEFAULT_SECTOR_LABEL = "其他"
DEFAULT_INDUSTRY_ID = "OTHER"
DEFAULT_INDUSTRY_LABEL = "未分类"


# ============================================================================
# Classification engine (pure logic — no DB dependency)
# ============================================================================

def classify_index(name: str) -> Tuple[str, str, str, str]:
    """Classify an index by its name using keyword rules.

    Returns (sector_id, sector_label, industry_id, industry_label).
    Falls back to (OTHER, 其他, OTHER, 未分类) if no rule matches.
    """
    s = str(name)
    best: Optional[Tuple[str, str, str, str]] = None
    best_score: Optional[Tuple[int, int, int, int, int]] = None

    for sector_id, sector_label, industry_id, industry_label, keywords in INDEX_RULES:
        hits = [kw for kw in keywords if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(kw) for kw in hits)
        longest_kw = max(len(kw) for kw in hits)
        rule_order = RULE_ORDER.get((sector_id, industry_id), 9999)
        # BROAD/REGION penalty: sector-specific rules ALWAYS beat BROAD and
        # REGION, regardless of keyword length.  Without this, "中证酒" would
        # get BROAD_CSI (keyword "中证" = 2 chars) as primary over
        # CONS/FOOD_BEV (keyword "酒" = 1 char), and "港股通医药C" would get
        # REGION_HK_LINK (keyword "港股通" = 3 chars) over HC/PHARMA_BROAD
        # (keyword "医药" = 2 chars).  Higher score wins, so sector-specific
        # gets 1 and BROAD/REGION gets 0.
        generic_penalty = 0 if sector_id in ("BROAD", "REGION") else 1
        # Higher score wins; tie-break by rule order (lower = earlier = higher priority)
        score = (generic_penalty, total_len, n_hits, longest_kw, -rule_order)
        if best_score is None or score > best_score:
            best_score = score
            best = (sector_id, sector_label, industry_id, industry_label)

    if best is not None:
        return best
    return (DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL,
            DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)


def classify_index_tags(name: str) -> List[Tuple[str, str, str, str]]:
    """Classify an index by name, returning ALL matching classifications.

    Returns a list of (sector_id, sector_label, industry_id, industry_label)
    tuples sorted by score (highest first).  The first entry is the primary
    classification.  An index may match multiple rules (e.g. "央企红利" matches
    both DIV/DIV_SOE and BROAD/BROAD_SOE), enabling multi-faceted browsing.

    Falls back to [(OTHER, 其他, OTHER, 未分类)] if no rule matches.
    """
    s = str(name)
    matches: List[Tuple[Tuple[int, int, int, int], Tuple[str, str, str, str]]] = []

    for sector_id, sector_label, industry_id, industry_label, keywords in INDEX_RULES:
        hits = [kw for kw in keywords if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(kw) for kw in hits)
        longest_kw = max(len(kw) for kw in hits)
        rule_order = RULE_ORDER.get((sector_id, industry_id), 9999)
        broad_penalty = 0 if sector_id in ("BROAD", "REGION") else 1
        score = (broad_penalty, total_len, n_hits, longest_kw, -rule_order)
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


# Standard Chinese ETF legal-name suffix.  ETF names without this suffix are
# "IB names" — foreign-branded ETFs (iShares/安硕, Premia, Global X, TIGER,
# KINDEX, 元大, 富邦) listed in HK/Korea/Taiwan with English or non-standard
# names.  Their ETF names do NOT contain the Chinese index keywords, so they
# must be classified by their underlying index_name instead.
_ETF_CN_SUFFIX = "交易型开放式指数"


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
    return classify_index(clean_name)
