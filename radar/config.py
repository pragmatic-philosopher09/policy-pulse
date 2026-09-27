"""Topic definitions and scoring constants.

A *topic* is a citizen-facing theme (e.g. "Gig workers & social security").
Each topic is matched against PRS items via keyword patterns (case-insensitive
regex fragments). PRS sector headings are used as a secondary hint.

Topics are intentionally framed for an 18-30 audience: jobs, exams, taxes,
digital life, gig work. Add or edit freely; the pipeline is topic-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topic:
    slug: str
    name: str
    tagline: str
    keywords: tuple[str, ...]
    sectors: tuple[str, ...] = field(default_factory=tuple)
    color: str = "#2563eb"
    name_hi: str = ""
    tagline_hi: str = ""
    exclude: str = ""   # regex on the title that vetoes this domain (e.g. macro data leaking into energy)

    def label(self, lang: str) -> str:
        return self.name_hi if lang == "hi" and self.name_hi else self.name

    def tag(self, lang: str) -> str:
        return self.tagline_hi if lang == "hi" and self.tagline_hi else self.tagline


# ---------------------------------------------------------------------------
# Taxonomy (v2): DOMAINS are the scored signals and follow ministry lines, so they do not
# overlap by construction. IMPACTS are a lens ("who does this reach") that cuts across domains;
# an item can carry several. STAGE lives on each item (action type) and on journeys.
# The seven original topic slugs are kept as redirects (OLD_TOPICS) so links keep working.

TOPICS: list[Topic] = [
    Topic(
        slug="digital-and-ai",
        name="Digital & AI",
        tagline="Data, platforms, telecom, online gaming and how India decides to govern AI.",
        keywords=(
            r"data protection", r"\bDPDP\b", r"digital personal data", r"privacy",
            r"\bIT rules\b", r"information technology rules", r"intermediary guidelines",
            r"social media", r"online gaming", r"cyber ?security", r"\bCERT-?In\b",
            r"telecom", r"\bTRAI\b", r"broadcasting", r"\bOTT\b", r"digital india",
            r"aadhaar", r"encryption", r"content (?:blocking|takedown)", r"(?:digital|online) platform",
            r"interception", r"artificial intelligence", r"\bAI\b", r"deepfake",
            r"synthetic(?:ally generated)? (?:media|content|information)", r"algorithm", r"machine learning",
            r"large language model", r"\bLLMs?\b", r"generative", r"IndiaAI", r"AI (?:governance|mission|summit|impact|safety|model)",
            r"frontier model", r"automated decision", r"facial recognition", r"\bGPUs?\b", r"compute capacity", r"AI[- ]generated",
            r"cyber ?crime",
        ),
        sectors=("Information Technology", "Electronics", "Communications", "Communication", "Telecom"),
        color="#5b4bff",
        name_hi="डिजिटल और AI",
        tagline_hi="डेटा, प्लेटफ़ॉर्म, टेलीकॉम, ऑनलाइन गेमिंग और भारत AI को कैसे संभालेगा।",
    ),
    Topic(
        slug="work",
        name="Work, wages & skills",
        tagline="Labour codes, gig work, EPF, hiring incentives, skilling and rural employment.",
        keywords=(
            r"employment", r"unemploy", r"\bjobs?\b", r"apprentice", r"skill(?:ing| development)",
            r"\bPLFS\b", r"labour force", r"internship", r"\bELI scheme\b", r"employment linked incentive",
            r"\bMGNREG", r"rozgar", r"workforce", r"\bPM ?Vishwakarma\b",
            r"gig work", r"platform work", r"labour codes?", r"code on wages", r"industrial relations code",
            r"social security code", r"code on social security", r"occupational safety", r"\bOSH\b",
            r"minimum wage", r"\bESIC?\b", r"contract labour", r"trade union", r"working hours",
            r"aggregator", r"delivery (?:worker|partner)", r"maternity", r"\bwages?\b", r"\blabour\b",
            r"\bEPF", r"provident fund", r"placement agenc",
        ),
        sectors=("Labour and Employment", "Labour", "Skill Development", "Rural Development"),
        color="#0f766e",
        name_hi="काम, मज़दूरी और कौशल",
        tagline_hi="श्रम संहिताएँ, गिग वर्क, EPF, भर्ती प्रोत्साहन, कौशल और ग्रामीण रोज़गार।",
    ),
    Topic(
        slug="money",
        name="Money, markets & tax",
        tagline="Income tax, GST, RBI, SEBI, insurance, pensions and the fine print that hits your wallet.",
        keywords=(
            r"income[- ]tax", r"\bGST\b", r"goods and services tax", r"\bTDS\b", r"\bTCS\b",
            r"\bUPI\b", r"digital payment", r"\bRBI\b", r"reserve bank", r"\bSEBI\b",
            r"mutual fund", r"insurance", r"\bIRDAI\b", r"pension", r"\bNPS\b", r"unified pension",
            r"credit card", r"lending", r"\bNBFC\b", r"crypto", r"virtual digital asset", r"banking",
            r"deposit insurance", r"finance bill", r"union budget", r"repo rate", r"securities",
            r"stock broker", r"portfolio manager", r"\bPFRDA\b", r"loan", r"taxation",
        ),
        sectors=("Finance", "Macroeconomic Development"),
        color="#c8371c",
        name_hi="पैसा, बाज़ार और टैक्स",
        tagline_hi="इनकम टैक्स, GST, RBI, SEBI, बीमा, पेंशन और वह छोटा प्रिंट जो आपकी जेब पर असर डालता है।",
    ),
    Topic(
        slug="education",
        name="Education & exams",
        tagline="Entrance exams, paper leaks, universities, curricula and who regulates them.",
        keywords=(
            r"\bNEET\b", r"\bJEE\b", r"\bUGC\b", r"\bNTA\b", r"\bCUET\b", r"paper leak",
            r"unfair means", r"public examinations?", r"entrance exam", r"\bexams?\b",
            r"higher education", r"universit", r"\bNEP\b", r"national education policy",
            r"school", r"student", r"scholarship", r"\bAICTE\b", r"\bNCERT\b", r"foreign universit", r"coaching",
            r"curriculum", r"anganwadi",
        ),
        sectors=("Education",),
        color="#b8860b",
        name_hi="शिक्षा और परीक्षाएँ",
        tagline_hi="प्रवेश परीक्षाएँ, पेपर लीक, विश्वविद्यालय, पाठ्यक्रम और उन्हें कौन नियंत्रित करता है।",
    ),
    Topic(
        slug="justice",
        name="Law, courts & policing",
        tagline="Criminal codes, bail, prisons, police forces, the Bar and how fast courts move.",
        keywords=(
            r"Bharatiya Nyaya Sanhita", r"\bBNS\b", r"Bharatiya Nagarik Suraksha", r"\bBNSS\b",
            r"Bharatiya Sakshya", r"\bBSA\b", r"criminal (?:law|procedure|justice|code|offence)",
            r"\bIPC\b", r"Indian Penal Code", r"\bCrPC\b", r"\bbail\b", r"undertrial",
            r"\bprisons?\b", r"\bjails?\b", r"\bpolic(?:e|ing)\b", r"custodial", r"sedition",
            r"death penalty", r"capital punishment", r"\bFIRs?\b", r"forensic", r"\bNIA\b",
            r"\bUAPA\b", r"\bPMLA\b", r"money laundering", r"witness protection",
            r"judicial (?:appointment|vacanc|infrastructure|reform)", r"pendency", r"case backlog",
            r"fast[- ]track court", r"legal aid", r"advocates? (?:\(amendment\) )?bill", r"e-?courts",
            r"\bCBI\b", r"enforcement directorate", r"\bPOCSO\b", r"number of (?:supreme court|high court) judges",
            r"armed police", r"\bCAPFs?\b", r"tribunals? reform",
        ),
        sectors=("Law and Justice", "Home Affairs"),
        color="#1f2bff",
        name_hi="क़ानून, अदालत और पुलिस",
        tagline_hi="आपराधिक संहिताएँ, ज़मानत, जेल, पुलिस बल, बार और अदालतें कितनी तेज़ चलती हैं।",
    ),
    Topic(
        slug="health",
        name="Health & medicine",
        tagline="Medical education, pharmacy, drugs regulation and public health law.",
        keywords=(
            r"\bNMC\b", r"national medical commission", r"medical (?:college|institution|education|council)",
            r"pharmac", r"hospital", r"\bhealth\b", r"\bAYUSH\b", r"\bdrugs?\b", r"\bCDSCO\b",
            r"clinical", r"nursing", r"\bMBBS\b", r"public health", r"\bpatients?\b", r"vaccin",
            r"mental health", r"\bdoctors?\b",
        ),
        sectors=("Health and Family Welfare", "Health", "Pharmaceuticals"),
        exclude=r"\b(GDP|inflation|repo rate)\b",
        color="#0e7490",
        name_hi="स्वास्थ्य और चिकित्सा",
        tagline_hi="चिकित्सा शिक्षा, फ़ार्मेसी, दवा नियमन और सार्वजनिक स्वास्थ्य क़ानून।",
    ),
    Topic(
        slug="business",
        name="Business & startups",
        tagline="Starting, running and funding a company: MSMEs, decriminalisation, trade, corporate law.",
        keywords=(
            r"startup", r"\bMSMEs?\b", r"jan vishwas", r"decriminalis", r"ease of doing business",
            r"companies act", r"\bLLP\b", r"corporate", r"\btrade\b", r"exports?\b", r"\bFDI\b",
            r"foreign (?:direct )?investment", r"credit guarantee", r"\bDPIIT\b", r"commerce",
            r"industrial (?:policy|development)", r"business reforms?", r"venture capital", r"fund of funds",
        ),
        sectors=("Commerce and Industry", "Corporate Affairs", "Industry"),
        exclude=r"\b(GDP|inflation|industrial production|IIP|CPI|repo rate|bilateral|visits?|seeds?)\b",
        color="#7c3aed",
        name_hi="व्यवसाय और स्टार्टअप",
        tagline_hi="कंपनी शुरू करना, चलाना और फ़ंड करना: MSME, ग़ैर-आपराधीकरण, व्यापार, कॉरपोरेट क़ानून।",
    ),
    Topic(
        slug="energy-environment",
        name="Energy & environment",
        tagline="Electricity, nuclear, mining, pollution and climate rules.",
        keywords=(
            r"electricity", r"\bpower\b", r"renewable", r"solar", r"nuclear", r"\bSHANTI\b", r"atomic energy",
            r"\bcoal\b", r"\bmining\b", r"minerals?\b", r"environment", r"pollution", r"climate",
            r"\bforests?\b", r"\bEIA\b", r"end-of-life vehicles", r"emission", r"petroleum", r"\bLPG\b",
            r"\bfuel\b", r"\bCERC\b", r"transmission", r"green hydrogen", r"\bwildlife\b",
        ),
        sectors=("Energy", "Power", "Mining", "Mining and Coal", "Coal and Mining", "Coal", "Environment",
                 "Environment and Water", "Petroleum and Natural Gas"),
        exclude=r"\b(GDP|inflation|industrial production|IIP|CPI|repo rate|bilateral|visits?)\b",
        color="#5f7a3a",
        name_hi="ऊर्जा और पर्यावरण",
        tagline_hi="बिजली, परमाणु, खनन, प्रदूषण और जलवायु नियम।",
    ),
]

# Old topic slugs -> domain (kept as redirect pages so existing links keep working)
OLD_TOPICS = {
    "digital-and-data": "digital-and-ai", "ai-regulation": "digital-and-ai",
    "jobs-and-employment": "work", "gig-work-and-labour-codes": "work",
    "personal-finance-and-tax": "money", "education-and-exams": "education",
    "criminal-law-and-justice": "justice",
}


@dataclass(frozen=True)
class Impact:
    slug: str
    name: str
    name_hi: str
    emoji: str
    keywords: tuple[str, ...]
    personas: tuple[str, ...] = ()       # persona lines that imply this impact
    domains: tuple[str, ...] = ()        # domains whose items carry this impact by default


IMPACTS: list[Impact] = [
    Impact("jobs", "Jobs & income", "नौकरी और आय", "💼",
           (r"employment", r"\bworkers?\b", r"\bwages?\b", r"hiring", r"\bjobs?\b", r"skill", r"apprentice",
            r"\bgig\b", r"layoff", r"\blabour\b", r"placement", r"\bEPF", r"provident"),
           personas=("gig",), domains=("work",)),
    Impact("money", "Your money", "आपका पैसा", "💰",
           (r"\btax", r"\bGST\b", r"\bloans?\b", r"\bEMI", r"deposit", r"insurance", r"pension", r"mutual fund",
            r"\bUPI\b", r"payment", r"\bprices?\b", r"tariff", r"repo rate", r"interest rate", r"subsid"),
           domains=("money",)),
    Impact("education", "Education & exams", "शिक्षा और परीक्षा", "🎓",
           (r"student", r"\bexams?\b", r"examination", r"universit", r"college", r"school", r"scholarship",
            r"curriculum", r"degree", r"coaching", r"\bUGC\b", r"\bNEET\b"),
           personas=("student",), domains=("education",)),
    Impact("digital", "Digital life & privacy", "डिजिटल ज़िंदगी और निजता", "📱",
           (r"personal data", r"privacy", r"online", r"platform", r"internet", r"telecom", r"\bapps?\b", r"\bAI\b",
            r"digital", r"cyber", r"social media", r"\bSIM\b", r"deepfake"),
           domains=("digital-and-ai",)),
    Impact("safety", "Rights & safety", "अधिकार और सुरक्षा", "⚖️",
           (r"\bpolic(?:e|ing)\b", r"\bcrime", r"\bcourts?\b", r"\bbail\b", r"prison", r"harass", r"\bfraud",
            r"consumer protection", r"\bsafety\b", r"offence", r"criminal"),
           domains=("justice",)),
    Impact("business", "Running a business", "व्यवसाय चलाना", "🏭",
           (r"startup", r"\bMSME", r"compan(?:y|ies)", r"business", r"compliance", r"licen[cs]", r"exports?\b",
            r"\btrade\b", r"industry\b", r"aggregator", r"employers?\b"),
           personas=("founder",), domains=("business",)),
    Impact("health", "Health", "स्वास्थ्य", "🩺",
           (r"\bhealth\b", r"medical", r"hospital", r"pharma", r"\bdrugs?\b", r"patient", r"doctor", r"nurs",
            r"mental health", r"vaccin", r"insur(?:ance|er)s? .*health"),
           domains=("health",)),
]
IMPACT_BY_SLUG = {i.slug: i for i in IMPACTS}

TOPIC_BY_SLUG = {t.slug: t for t in TOPICS}

# Public-chatter search phrases per domain (radar/chatter.py). Deliberately specific: these go to
# Reddit / Bluesky / GDELT search boxes, so "GST" alone would drown a topic in noise while
# "GST council" or "GST rate" finds the policy conversation. Keep 3-6 per domain.
CHATTER_QUERIES: dict[str, tuple[str, ...]] = {
    "digital-and-ai": ("DPDP rules", "IT Rules deepfake", "online gaming bill", "IndiaAI mission", "MeitY AI governance", "Aadhaar authentication rules"),
    "work": ("labour codes", "gig workers social security", "EPFO rules", "employment linked incentive", "minimum wage india"),
    "money": ("income tax bill", "GST council", "RBI draft directions", "SEBI consultation paper", "UPI rules NPCI", "unified pension scheme"),
    "education": ("NEET NTA", "UGC draft regulations", "paper leak law", "CUET exam", "national education policy"),
    "justice": ("Bharatiya Nyaya Sanhita", "BNSS bail", "PMLA supreme court", "UAPA bail", "prison reform india", "police custody law"),
    "health": ("NMC regulations", "NEET PG counselling", "CDSCO drugs rules", "pharmacy council india", "mental health act india"),
    "business": ("Jan Vishwas bill", "MSME payment rules", "startup india fund of funds", "companies act amendment", "ease of doing business india"),
    "energy-environment": ("Electricity Amendment Bill", "SHANTI nuclear bill", "forest conservation rules", "EIA notification", "green hydrogen mission"),
}

# Weight of an item by what *kind* of government action it represents.
# Enacted law counts more than a draft; a draft more than a committee note.
ACTION_WEIGHTS: dict[str, float] = {
    "enacted": 3.0,      # passed by Parliament / received assent
    "introduced": 2.0,   # bill introduced in Parliament
    "rules": 2.0,        # rules/notification issued (binding)
    "cabinet": 1.8,      # cabinet approval
    "consultation": 1.5, # draft released for public comment
    "committee": 1.2,    # standing committee report
    "scheme": 1.2,       # scheme launched/approved
    "court": 1.5,        # Supreme Court / High Court judgement
    "other": 1.0,
}

# Momentum windows (in months)
RECENT_WINDOW = 3
BASELINE_WINDOW = 6

# PRS attribution (CC BY 4.0). Must be visible on every generated page.
PRS_ATTRIBUTION = (
    "Source data © PRS Legislative Research (prsindia.org), "
    "licensed under CC BY 4.0. Summaries and scores are our own."
)
PRS_BASE = "https://prsindia.org"
# Public Telegram channel (set POLICY_PULSE_CHANNEL_URL in CI once the channel exists)
import os
CHANNEL_URL = os.environ.get("POLICY_PULSE_CHANNEL_URL", "")
USER_AGENT = "PolicyPulse/0.2 (+https://github.com; civic research, CC BY reuse)"
CRAWL_DELAY_SECONDS = 10  # matches prsindia.org robots.txt
