// Central icon set for the app.
//
// Every UI icon comes from lucide-react. Import icons from HERE — not from
// 'lucide-react' directly — so the underlying set lives behind one seam and can
// be swapped or restyled in a single place. Lucide's house style (thin stroke,
// round caps/joins) already matches the app's hand-drawn icons it replaces.
//
// What stays hand-drawn (deliberately NOT routed through here):
//   - The Connectors redesign's own glyph set (connectors/icons.tsx) — generic
//     CATEGORY icons, never brand marks (handoff spec §10: real logos are a
//     licensing question, not a design one), drawn to that prototype's geometry.
//   - Illustration-grade SVGs (OnboardingWizard badges, Overview empty-state,
//     the CostPanel sparkline) — these are art/data-viz, not icons.

import type { LucideProps } from 'lucide-react'

export type IconProps = LucideProps

export {
  Monitor,
  // ── App chrome ──
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ChevronDown,
  ChevronUp,
  Menu,
  MoreHorizontal,
  MoreVertical,
  Search,
  Bug,
  MessageSquare,
  ListChecks,
  Eye,
  EyeOff,
  Copy,
  Check,
  CheckCheck,
  Clock,
  ClockAlert,
  CircleCheck,
  AlertCircle,
  Info,
  RotateCcw,
  Eraser,
  LogOut,
  // ── Intel "markets desk" redesign ──
  ArrowRight,
  ArrowUpRight,
  CircleDot,
  Circle,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  RefreshCw,
  X,
  Link2,
  Bell,
  BellOff,
  Plus,
  Minus,
  Sparkles,
  Archive,
  Radar,
  // ── Lucius composer / dock ──
  Paperclip,
  AtSign,
  Slash,
  Ban,
  SquarePen,
  PanelRight,
  // ── Semantic / chips ──
  Brain,
  Cpu,
  DollarSign,
  Palette,
  WalletCards,
  LockKeyhole,
  Database,
  Code2,
  ExternalLink,
  FileSpreadsheet,
  Wrench,
  Shield,
  ShieldCheck,
  TriangleAlert,
  Zap,
  Landmark,
  Building2,
  // ── Chat artifact card ──
  Download,
  Upload,
  Maximize2,
  Minimize2,
  FileCode,
  // ── Sidebar nav tabs ──
  LayoutGrid,
  Grid2X2,
  Columns3,
  Box,
  Inbox,
  TrendingUp,
  TrendingDown,
  Wallet,
  FileText,
  Target,
  ArrowLeftRight,
  Receipt,
  StickyNote,
  Lightbulb,
  Bot,
  Workflow,
  Plug,
  Smartphone,
  User,
  Share2,
  Settings,
  Tags,
  // ── Asset-detail chart-type picker ──
  ChartLine,
  ChartArea,
  Baseline,
  ChartCandlestick,
  ChartBar,
  // ── Tool activity rail (chat/toolActivity) ── one per VERB CLASS the server emits
  //    (backend/ai/tools/display.py::DEFAULT_NOUNS), plus `error`. A class with no icon
  //    here renders the neutral Wrench, which is what "we don't recognise this" looks like.
  Table,
  Globe,
  Pencil,
  Trash2,
  Terminal,
  Camera,
  Send,
  KeyRound,
  AlertTriangle,
  // ── Extended-hours (pre/post-market) quote ──
  Sunrise,
  Sunset,
  // ── Asset-detail header (icon-only at narrow widths) ──
  Star,
  // ── Income tab: upcoming-payment date confidence ──
  CalendarCheck,
  CalendarClock,
  // ── Vault (Lucius tab) ──
  FolderLock,
  FolderMinus,
  Lock,
  Bookmark,
  BookmarkCheck,
  ArrowLeft,
  Image as ImageIcon,
} from 'lucide-react'

// Shared sizing for the sidebar nav icons. (.sidebar-icon svg in index.css also
// boxes them to 20×20; strokeWidth 1.5 matches the app's thin-line house style.)
export const NAV_ICON_PROPS = { size: 20, strokeWidth: 1.5 } as const
