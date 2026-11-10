"""What speech-to-text hears a call with, and the money words it is told to expect in each language.

It misheard "the full list in a chart" as "funnel list in a charge" (live call, 2026-11-06). These
lists are general vocabulary. From the owner's book, only the names of their most used merchants go
to the speech provider (``service._own_terms``), and the product docs say so.
"""

from __future__ import annotations

# LiveKit's "auto" takes no word hints. Nova-3 hears every language a call can be in and takes
# them in each (LiveKit's and Deepgram's docs, 2026-11-06).
AUTOMATIC_MODEL = "deepgram/nova-3"

# Its default 25ms of silence ends a turn at any breath, and one sentence became two requests
# (owner, 2026-11-05, on the AssemblyAI equivalent).
DEEPGRAM_TURN_SILENCE_MS = 900

# Deepgram allows 100 terms and 500 tokens, and advises 20-50. A hint makes that word likelier for
# ANYTHING that sounds a bit like it: "Roth IRA" was heard for an ordinary reply about a purchase
# (owner, 2026-11-06), so no acronyms or account jargon a normal sentence can be mistaken for.
_MAX_TERMS = 50
_MAX_OWN_TERMS = 20

# fmt: off
_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "Lucius", "net worth", "budget", "envelope", "ready to assign", "transaction", "payee",
        "category", "uncategorized", "balance", "credit card", "checking account",
        "savings account", "brokerage", "portfolio", "dividend", "stock", "bond", "ETF",
        "vesting", "capital gains", "cash flow",
        "mortgage", "interest rate", "retirement", "runway", "statement", "subscription",
        "rebalance", "expenses", "income",
    ),
    "vi": (
        "Lucius", "giá trị tài sản ròng", "ngân sách", "tài khoản", "số dư", "giao dịch",
        "chi tiêu", "thu nhập", "tiết kiệm", "đầu tư", "cổ phiếu", "trái phiếu", "chứng chỉ quỹ",
        "danh mục đầu tư", "cổ tức", "lãi suất", "khoản vay", "thẻ tín dụng", "dư nợ", "tài sản",
        "hưu trí", "tiền mặt", "tỷ giá", "sao kê", "chuyển khoản", "lợi nhuận", "thua lỗ",
        "danh sách", "biểu đồ", "hạn mức", "tiền gửi", "trả góp",
    ),
    "zh": (
        "Lucius", "净资产", "预算", "账户", "余额", "交易", "支出", "收入", "储蓄", "投资",
        "股票", "债券", "基金", "投资组合", "股息", "利率", "贷款", "信用卡", "退休", "现金流",
        "汇率", "账单", "转账", "收益", "亏损", "房贷",
    ),
    "ja": (
        "Lucius", "純資産", "予算", "口座", "残高", "取引", "支出", "収入", "貯蓄", "投資",
        "株式", "債券", "投資信託", "ポートフォリオ", "配当", "金利", "ローン", "クレジットカード",
        "退職金", "キャッシュフロー", "為替", "明細", "振込", "利益", "損失", "住宅ローン",
    ),
    "ko": (
        "Lucius", "순자산", "예산", "계좌", "잔액", "거래", "지출", "수입", "저축", "투자",
        "주식", "채권", "펀드", "포트폴리오", "배당금", "금리", "대출", "신용카드", "은퇴",
        "현금 흐름", "환율", "명세서", "이체", "수익", "손실", "주택담보대출",
    ),
    "hi": (
        "Lucius", "कुल संपत्ति", "बजट", "खाता", "शेष राशि", "लेन-देन", "खर्च", "आय", "बचत", "निवेश",
        "शेयर", "बॉन्ड", "म्यूचुअल फंड", "पोर्टफोलियो", "लाभांश", "ब्याज दर", "ऋण", "क्रेडिट कार्ड",
        "सेवानिवृत्ति", "नकदी प्रवाह", "विनिमय दर", "स्टेटमेंट", "मुनाफ़ा", "घाटा",
    ),
    "id": (
        "Lucius", "kekayaan bersih", "anggaran", "rekening", "saldo", "transaksi", "pengeluaran",
        "pendapatan", "tabungan", "investasi", "saham", "obligasi", "reksa dana", "portofolio",
        "dividen", "suku bunga", "pinjaman", "kartu kredit", "pensiun", "arus kas", "nilai tukar",
        "mutasi rekening", "transfer", "keuntungan", "kerugian", "cicilan",
    ),
    "es": (
        "Lucius", "patrimonio neto", "presupuesto", "cuenta", "saldo", "transacción", "gastos",
        "ingresos", "ahorros", "inversión", "acciones", "bonos", "fondo de inversión", "cartera",
        "dividendo", "tipo de interés", "préstamo", "hipoteca", "tarjeta de crédito", "jubilación",
        "flujo de caja", "tipo de cambio", "extracto", "transferencia", "ganancias", "pérdidas",
    ),
    "fr": (
        "Lucius", "patrimoine net", "budget", "compte", "solde", "transaction", "dépenses",
        "revenus", "épargne", "investissement", "actions", "obligations", "fonds commun",
        "portefeuille", "dividende", "taux d'intérêt", "prêt", "prêt immobilier", "carte de crédit",
        "retraite", "flux de trésorerie", "taux de change", "relevé", "virement", "bénéfices",
        "pertes",
    ),
    "de": (
        "Lucius", "Nettovermögen", "Budget", "Konto", "Kontostand", "Buchung", "Ausgaben",
        "Einnahmen", "Ersparnisse", "Geldanlage", "Aktien", "Anleihen", "Fonds", "Depot",
        "Portfolio", "Dividende", "Zinssatz", "Kredit", "Hypothek", "Kreditkarte", "Rente",
        "Cashflow", "Wechselkurs", "Kontoauszug", "Überweisung", "Gewinn", "Verlust",
    ),
    "pt": (
        "Lucius", "patrimônio líquido", "orçamento", "conta", "saldo", "transação", "despesas",
        "receitas", "poupança", "investimento", "ações", "títulos", "fundo de investimento",
        "carteira", "dividendos", "taxa de juros", "empréstimo", "financiamento",
        "cartão de crédito", "aposentadoria", "fluxo de caixa", "taxa de câmbio", "extrato",
        "transferência", "lucro", "prejuízo",
    ),
}
# fmt: on


def hearing_model(stt_model: str) -> str:
    """The model a call really uses: "Automatic" is ours to choose, so it can take the hints."""
    return AUTOMATIC_MODEL if stt_model == "auto" else stt_model


def hint_kwargs(model: str, language: str, own_terms: tuple[str, ...] = ()) -> dict:
    """What this model is told beyond the language: its word hints, under its own option name, and
    how long it waits before calling a turn over. Nothing for a model that takes no hints. The
    owner's own merchants come first: no general model knows them, and "ACME" was heard as "top"
    (owner, 2026-11-06)."""
    general = _TERMS.get(language, _TERMS["en"])
    terms = list(dict.fromkeys((*own_terms[:_MAX_OWN_TERMS], *general)))[:_MAX_TERMS]
    if model.startswith("deepgram/nova-3"):
        return {"keyterm": terms, "endpointing": DEEPGRAM_TURN_SILENCE_MS}
    if model.startswith("assemblyai/"):
        return {"keyterms_prompt": terms}
    return {}
