APPROVED INSTRUMENT UNIVERSE (LOCKED & FINAL)
A) NSE – INDEX OPTIONS (CE & PE mandatory)

NIFTY 50
Expiries: Latest 4 weekly + Next Monthly
Strikes per expiry: 101 (ATM + 50 each side)
CE & PE mandatory

BANKNIFTY
Expiries: current monthly + Next monthly
Strikes per expiry: 101 (ATM + 50 each side)
CE & PE mandatory

SENSEX
Expiries: Latest 4 weekly + Next Monthly
Strikes per expiry: 101 (ATM + 50 each side)
CE & PE mandatory



B) NSE – STOCK OPTIONS
List of stocks named in "options-stocks-list.csv"
Expiries: current monthly + next monthly
Strikes per expiry: 51 (ATM + 25 each side)
CE & PE mandatory



C) NSE – STOCK FUTURES
List of stocks named in "futures-stocks-list.csv"
Expiries: current monthly + next monthly



D) NSE – EQUITY (CASH MARKET)
List of stocks name din equity-list.csv
One instrument per stock
No expiries, no strikes



E) MCX – FUTURES
All the instruments listed in 'mcx-comm-futures.csv'
Expiries: Current monthly + next monthly/ or upcoming (in case of SILVER and GOLD they are 3 monthly intervals)



F) MCX – OPTIONS
All the instruments listed in 'mcx-comm-options.csv'
Expiries: Current monthly + next monthly/ or upcoming (in case of SILVER and GOLD they are 3 monthly intervals)


G) ETF
All the instruments listed in 'etf-list.csv'



STRIKE GENERATION RULES (MANDATORY)
All option strikes MUST be generated relative to ATM Strike.
Strike generation must be deterministic and rule-driven.
Search filters must NEVER control subscriptions.


ATM Definition
ATM = nearest strike to current underlying LTP
Rounded using exchange-defined strike intervals

ATM recalculated ONLY at:
• system startup
• expiry rollover
• explicit admin refresh
NEVER on every tick


SECURITY-IDs of all the instruments should be fetched from "api-scrip-master-detailed.csv"


Strike Ranges
Index Options (NIFTY, BANKNIFTY, SENSEX)
50 strikes below ATM
ATM
50 strikes above ATM
= 101 total


NSE Stock Options:
25 strikes below ATM
ATM
25 strikes above ATM
= 51 total


MCX Options:
50 strikes below ATM
ATM
50 strikes above ATM
= 101 total


ATM LOGIC:
ATM_STRIKE = round(Underlying_LTP / Strike_Step) * Strike_Step
Underlying moves ≥ 1 strike step
Option chain UI reopened
Expiry changed
❌ No per-tick recalculation


Strike intervals, lot sizes and expiry dates are to be fetched from the API.
DO NOT hardcode strike intervals.


STABILITY & ANTI-DRIFT RULES (CRITICAL)
• Do NOT auto-expand strikes
• Do NOT reduce strike counts
• Do NOT add extra expiries
• Do NOT drop weekly options
• Do NOT regenerate strikes intraday
• Do NOT infer universe from search UI
• Do NOT optimize for “performance”

Once generated, strikes for an expiry remain FIXED until expiry rollover.


Do NOT auto-correct silently



WEBSOCKET DISTRIBUTION RULES

Use maximum 5 WebSocket connections
Maximum 5,000 instruments per connection
Distribute instruments deterministically
Maintain mapping: instrument_token → websocket_id
Subscriptions must be idempotent on reconnect


SUCCESS CRITERIA
Implementation is COMPLETE only if:

All ~ 7140 instruments are subscribed
No WS exceeds 5,000 instruments
Strike and expiry counts match rules exactly
No silent pruning or expansion occurs
System remains within DhanHQ v2 limits