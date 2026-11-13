/** Who the featured portfolios are: a sentence or two each, written once and kept to what is
 *  widely documented about the investor or institution (their role and approach, never a holding
 *  or a figure, which the page reads from the filings). A portfolio not listed here gets no
 *  profile: the description then rests on its data alone, never on a guess. Keyed by
 *  "<kind>:<source_ref>", the follow's own identity. */
export const PROFILES: Record<string, string> = {
  'fund_13f:1067983':
    'Warren Buffett, often called the "Oracle of Omaha", built Berkshire Hathaway into a conglomerate whose stock portfolio is among the most closely followed in the world. It favours a few large, long-held stakes in businesses it understands.',
  'fund_13f:1336528':
    "Bill Ackman's Pershing Square is a concentrated, activist hedge fund: it typically holds around ten large companies and pushes some of them for change.",
  'fund_13f:1350694':
    "Bridgewater Associates, founded by Ray Dalio, is one of the world's largest hedge funds and is best known for global macro investing; its US stock holdings are only part of what it does.",
  'fund_13f:1697748':
    "Cathie Wood's ARK Invest backs what it calls disruptive innovation, such as AI, genomics, robotics and digital payments, and trades its positions actively.",
  'fund_13f:1536411':
    'Duquesne Family Office manages the fortune of Stanley Druckenmiller, the macro investor who once ran money alongside George Soros.',
  'fund_13f:1656456':
    "David Tepper's Appaloosa made its name buying distressed debt and is known for large, well-timed bets on recoveries.",
  'fund_13f:1061768':
    'Baupost Group is run by Seth Klarman, a value investor known for his patience, his cash holdings and his book "Margin of Safety".',
  'fund_13f:1040273':
    "Dan Loeb's Third Point is an event-driven hedge fund, and an activist one: it often presses companies publicly for change.",
  'fund_13f:1167483':
    'Tiger Global, founded by Chase Coleman, invests heavily in technology companies, public and private.',
  'fund_13f:1029160':
    "Soros Fund Management manages George Soros's family fortune; Soros made his name as a macro investor.",
  'fund_13f:921669':
    "Icahn Capital invests Carl Icahn's money. He is one of the best-known activist investors, taking large stakes and pushing companies for change.",
  'fund_13f:1709323':
    "Li Lu's Himalaya Capital is a concentrated value investor; Charlie Munger was one of its backers. Its US filings miss its holdings listed abroad.",
  'fund_13f:1021944':
    "Temasek is Singapore's state investment company, owned by its government; its US filings show only its US-listed stakes, a small part of what it owns worldwide.",
  'fund_13f:949509':
    'Howard Marks co-founded Oaktree Capital, a specialist in credit and distressed investing; its US stock holdings are a small part of what it manages.',
  'fund_13f:1037389':
    'Renaissance Technologies, founded by the late mathematician Jim Simons, trades by computer models across thousands of positions, so its holdings change constantly.',
  'fund_13f:1423053':
    "Ken Griffin's Citadel is a multi-strategy hedge fund with many independent teams; its filings list thousands of positions that turn over quickly.",
  'fund_13f:1791786':
    'Elliott Investment Management, founded by Paul Singer, is one of the most prominent activist investors in the world.',
  'fund_13f:1135730':
    "Philippe Laffont's Coatue Management focuses on technology companies, public and private.",
  'fund_13f:1103804':
    'Viking Global Investors, founded by Andreas Halvorsen, is a long/short hedge fund that picks stocks from fundamental research.',
  'fund_13f:1061165':
    'Lone Pine Capital, founded by Stephen Mandel, is a stock-picking hedge fund that holds a concentrated set of growth companies.',
  'fund_13f:1096343':
    'Markel Group is a specialty insurer whose stock portfolio, run by Tom Gayner, follows a Berkshire-like, buy-and-hold approach.',
  'fund_13f:1056831':
    "Bruce Berkowitz's Fairholme Capital is known for a very concentrated portfolio, sometimes just a handful of stocks.",
  'fund_13f:1166559':
    "The Gates Foundation Trust holds the Bill & Melinda Gates Foundation's endowment, the money that funds its charitable work.",
  'fund_13f:1112520':
    'Akre Capital Management, founded by Chuck Akre, holds a small number of high-quality compounding businesses for many years.',
  'fund_13f:1345471':
    "Nelson Peltz's Trian Fund Management is an activist investor that takes large stakes in consumer and industrial companies and seeks board seats.",
  'fund_13f:1517137':
    "Jeff Smith's Starboard Value is an activist hedge fund that pushes companies to improve their operations and margins.",
  'fund_13f:1541617':
    "Brad Gerstner's Altimeter Capital invests in technology companies, from private start-ups through to public markets.",
  'fund_13f:1747057':
    'Dan Sundheim, formerly of Viking Global, founded D1 Capital Partners, a hedge fund that invests in public and private companies.',
  'fund_13f:1798849':
    'Durable Capital Partners was founded by Henry Ellenbogen, a growth investor formerly at T. Rowe Price, to hold growing companies for the long term.',
  'fund_13f:1045810':
    'Not a fund: NVIDIA reports the stakes it holds in other public companies, mostly partners in AI.',
  'house_member:pelosi|nancy':
    'Nancy Pelosi represents San Francisco and has served as Speaker of the House. Most trades in her reports are made by her husband, Paul Pelosi, often with call options on large technology companies.',
  'house_member:gottheimer|josh':
    "Josh Gottheimer represents New Jersey's 5th district and files frequent trade reports.",
  'house_member:moskowitz|jared': "Jared Moskowitz represents Florida's 23rd district.",
  'house_member:hern|kevin': "Kevin Hern represents Oklahoma's 1st district.",
  'house_member:wasserman schultz|debbie':
    "Debbie Wasserman Schultz represents Florida's 25th district and has chaired the Democratic National Committee.",
  'etf_daily:ARKK':
    "ARK Innovation is Cathie Wood's flagship actively managed ETF, investing in companies ARK sees as leaders of disruptive innovation.",
  'etf_daily:ARKW':
    "ARK Next Generation Internet is Cathie Wood's actively managed ETF for internet-era companies, from cloud and e-commerce to digital assets.",
}
