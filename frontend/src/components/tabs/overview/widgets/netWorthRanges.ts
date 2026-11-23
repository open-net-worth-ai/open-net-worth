// The ranges the net-worth chart offers, in order. Each is a key of the server's precomputed
// `ranges` (services/networth_stats.py::RANGE_MONTHS), so a range can never be sliced two ways.
export const NET_WORTH_RANGES = ['1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', 'All'] as const
export const DEFAULT_NET_WORTH_RANGE = '3M'
