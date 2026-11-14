/** The portrait in a card's ring: an AI-generated illustration of the person behind the
 *  portfolio, made from a text description, cut out of its background and framed on the face
 *  (owner, 2026-11-12). Nothing is credited on screen. Each file's name carries a hash of its
 *  content, so a replaced portrait is a new address and no browser keeps showing the old one.
 *  Keyed `<kind>:<source_ref>`. */
export interface PortfolioPhoto {
  src: string
  person: string
  artist: string
  license: string
  source: string
  commons?: boolean
}

export const PHOTOS: Record<string, PortfolioPhoto> = {
  'fund_13f:1709323': {
    src: '/portfolio-photos/li-lu-72eb7d70.webp',
    person: 'Li Lu',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:921669': {
    src: '/portfolio-photos/carl-icahn-465897d7.webp',
    person: 'Carl Icahn',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1040273': {
    src: '/portfolio-photos/dan-loeb-2e240bac.webp',
    person: 'Dan Loeb',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1061768': {
    src: '/portfolio-photos/seth-klarman-89f79da7.webp',
    person: 'Seth Klarman',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1656456': {
    src: '/portfolio-photos/david-tepper-9bbc0278.webp',
    person: 'David Tepper',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1536411': {
    src: '/portfolio-photos/stanley-druckenmiller-837a298f.webp',
    person: 'Stanley Druckenmiller',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1336528': {
    src: '/portfolio-photos/bill-ackman-4977eb33.webp',
    person: 'Bill Ackman',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1423053': {
    src: '/portfolio-photos/ken-griffin-0cdc81d8.webp',
    person: 'Ken Griffin',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1067983': {
    src: '/portfolio-photos/warren-buffett-f42b2691.webp',
    person: 'Warren Buffett',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1350694': {
    src: '/portfolio-photos/ray-dalio-a49214d7.webp',
    person: 'Ray Dalio',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1697748': {
    src: '/portfolio-photos/cathie-wood-f0520acc.webp',
    person: 'Cathie Wood',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1029160': {
    src: '/portfolio-photos/george-soros-c0ab49eb.webp',
    person: 'George Soros',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1037389': {
    src: '/portfolio-photos/jim-simons-530de37d.webp',
    person: 'Jim Simons',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1791786': {
    src: '/portfolio-photos/paul-singer-businessman-a3bf70f3.webp',
    person: 'Paul Singer',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1166559': {
    src: '/portfolio-photos/bill-gates-57145f3d.webp',
    person: 'Bill Gates',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'fund_13f:1541617': {
    src: '/portfolio-photos/brad-gerstner-52f0bb72.webp',
    person: 'Brad Gerstner',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'house_member:pelosi|nancy': {
    src: '/portfolio-photos/nancy-pelosi-1f6ffc63.webp',
    person: 'Nancy Pelosi',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'house_member:gottheimer|josh': {
    src: '/portfolio-photos/josh-gottheimer-9e302981.webp',
    person: 'Josh Gottheimer',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'house_member:moskowitz|jared': {
    src: '/portfolio-photos/jared-moskowitz-2f930a0a.webp',
    person: 'Jared Moskowitz',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'house_member:hern|kevin': {
    src: '/portfolio-photos/kevin-hern-2ac08d9c.webp',
    person: 'Kevin Hern',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'house_member:wasserman schultz|debbie': {
    src: '/portfolio-photos/debbie-wasserman-schultz-83c1e653.webp',
    person: 'Debbie Wasserman Schultz',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'etf_daily:ARKK': {
    src: '/portfolio-photos/cathie-wood-f0520acc.webp',
    person: 'Cathie Wood',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'etf_daily:ARKW': {
    src: '/portfolio-photos/cathie-wood-f0520acc.webp',
    person: 'Cathie Wood',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'etf_daily:ARKQ': {
    src: '/portfolio-photos/cathie-wood-f0520acc.webp',
    person: 'Cathie Wood',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'etf_daily:ARKG': {
    src: '/portfolio-photos/cathie-wood-f0520acc.webp',
    person: 'Cathie Wood',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'etf_daily:ARKF': {
    src: '/portfolio-photos/cathie-wood-f0520acc.webp',
    person: 'Cathie Wood',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
  'etf_daily:ARKX': {
    src: '/portfolio-photos/cathie-wood-f0520acc.webp',
    person: 'Cathie Wood',
    artist: 'AI-generated',
    // Generated from a text description (no photo as input), owner 2026-11-12.
    license: 'illustration',
    source: '',
    commons: false,
  },
}

/** The firm's logo, for a portfolio with no portrait: from Wikimedia Commons, or the firm's own,
 *  trimmed and centred on white; the name carries a hash of its content. A logo whose licence
 *  asks for attribution is credited in public/licenses/portfolio-logos.txt, never on screen. */
export interface PortfolioLogo {
  src: string
  firm: string
  license: string
  source: string
}

export const LOGOS: Record<string, PortfolioLogo> = {
  'fund_13f:1045810': {
    src: '/portfolio-logos/nvidia-0227580a.webp',
    firm: 'NVIDIA',
    license: 'Apache License 2.0',
    source: 'https://commons.wikimedia.org/wiki/File:NVIDIA_logo.svg',
  },
  'fund_13f:1112520': {
    src: '/portfolio-logos/akre-capital-management-f80e7b12.webp',
    firm: 'Akre Capital Management',
    // The owner's choice (2026-11-12): the firm's own logo, from its website, not freely licensed.
    license: 'firm logo',
    source: '',
  },
  'fund_13f:1061165': {
    src: '/portfolio-logos/lone-pine-capital-7d864011.webp',
    firm: 'Lone Pine Capital',
    // The owner's choice (2026-11-13): the firm's own mark, supplied by the owner, not freely
    // licensed. Shrunk on its own green so the tree's base clears the ring's circular crop.
    license: 'firm logo',
    source: '',
  },
  'fund_13f:1798849': {
    src: '/portfolio-logos/durable-capital-partners-0c191d6b.webp',
    firm: 'Durable Capital Partners',
    // The owner's choice (2026-11-12): the firm's own logo, from its website, not freely licensed.
    license: 'firm logo',
    source: '',
  },
  'fund_13f:1056831': {
    src: '/portfolio-logos/fairholme-capital-027c5b5b.webp',
    firm: 'Fairholme Capital',
    // The owner's choice (2026-11-12): the firm's own logo, from its website, not freely licensed.
    license: 'firm logo',
    source: '',
  },
  'fund_13f:949509': {
    src: '/portfolio-logos/oaktree-capital-a186732f.webp',
    firm: 'Oaktree Capital',
    // The owner's choice (2026-11-12): the firm's own logo, not freely licensed.
    license: 'firm logo',
    source: '',
  },
  'fund_13f:1517137': {
    src: '/portfolio-logos/starboard-value-c5540c46.webp',
    firm: 'Starboard Value',
    // The owner's choice (2026-11-14): the firm's own mark, supplied by the owner, not freely
    // licensed. Shrunk on white so its round shape sits inside the ring's circular crop.
    license: 'firm logo',
    source: '',
  },
  'fund_13f:1067983': {
    src: '/portfolio-logos/berkshire-hathaway-602faf72.webp',
    firm: 'Berkshire Hathaway',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Berkshire_Hathaway.svg',
  },
  'fund_13f:1350694': {
    src: '/portfolio-logos/bridgewater-associates-960bc928.webp',
    firm: 'Bridgewater Associates',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Bridgewater_Associates.png',
  },
  'fund_13f:1697748': {
    src: '/portfolio-logos/ark-invest-947cd178.webp',
    firm: 'ARK Invest',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Ark-logo-1-1.svg',
  },
  'fund_13f:1040273': {
    src: '/portfolio-logos/third-point-49a8ff13.webp',
    firm: 'Third Point',
    license: 'CC BY-SA 4.0',
    source: 'https://commons.wikimedia.org/wiki/File:Third_Point_LLC_Logo.svg',
  },
  'fund_13f:1167483': {
    src: '/portfolio-logos/tiger-global-management-f5c1feed.webp',
    firm: 'Tiger Global Management',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Tiger_Global.png',
  },
  'fund_13f:1029160': {
    src: '/portfolio-logos/soros-fund-management-08a0dcc4.webp',
    firm: 'Soros Fund Management',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Soros_Fund_Mgmt_logo.svg',
  },
  'fund_13f:1709323': {
    src: '/portfolio-logos/himalaya-capital-28599c3e.webp',
    firm: 'Himalaya Capital',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Himalaya_Capital.png',
  },
  'fund_13f:1037389': {
    src: '/portfolio-logos/renaissance-technologies-3ad6b31c.webp',
    firm: 'Renaissance Technologies',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Renaissance_Technologies.png',
  },
  'fund_13f:1423053': {
    src: '/portfolio-logos/citadel-llc-d2a62b20.webp',
    firm: 'Citadel LLC',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Citadel_LLC_Logo.svg',
  },
  'fund_13f:1791786': {
    src: '/portfolio-logos/elliott-management-0d1da483.webp',
    firm: 'Elliott Management',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Elliott_Management_Corporation_logo.svg',
  },
  'fund_13f:1135730': {
    src: '/portfolio-logos/coatue-management-ac66edb4.webp',
    firm: 'Coatue Management',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Coatue_Logo.svg',
  },
  'fund_13f:1103804': {
    src: '/portfolio-logos/viking-global-investors-aaf5d84a.webp',
    firm: 'Viking Global Investors',
    license: 'CC BY-SA 4.0',
    source: 'https://commons.wikimedia.org/wiki/File:Viking_Global_Investors_logo.png',
  },
  'fund_13f:1096343': {
    src: '/portfolio-logos/markel-group-7d608429.webp',
    firm: 'Markel Group',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Markel_Logo.svg',
  },
  'fund_13f:1166559': {
    src: '/portfolio-logos/gates-foundation-460edf86.webp',
    firm: 'Gates Foundation',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:GatesFoundationlogo.svg',
  },
  'fund_13f:1345471': {
    src: '/portfolio-logos/trian-fund-management-e792ee47.webp',
    firm: 'Trian Fund Management',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Trian_Partners.png',
  },
  'fund_13f:1541617': {
    src: '/portfolio-logos/altimeter-capital-6e2b15fe.webp',
    firm: 'Altimeter Capital',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Altimeter_Capital.svg',
  },
  'fund_13f:1747057': {
    src: '/portfolio-logos/d1-capital-partners-22dc4843.webp',
    firm: 'D1 Capital Partners',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:D1_capital_partners.png',
  },
  'fund_13f:1021944': {
    src: '/portfolio-logos/temasek-holdings-9440d298.webp',
    firm: 'Temasek Holdings',
    license: 'Public domain',
    source: 'https://commons.wikimedia.org/wiki/File:Temasek_Holdings_logo.svg',
  },
}

let preloaded = false

/** Ask for every portrait and logo as soon as the gallery opens. A ring is drawn only once its
 *  card's figures arrive, so without this each picture waited for its card and then popped in. */
export function preloadPortfolioPictures(): void {
  if (preloaded || typeof Image === 'undefined') return
  preloaded = true
  for (const { src } of [...Object.values(PHOTOS), ...Object.values(LOGOS)]) {
    const img = new Image()
    img.decoding = 'async'
    img.src = src
  }
}
