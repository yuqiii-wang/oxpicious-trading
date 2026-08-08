import json, sys, urllib.request
url = "http://127.0.0.1:3001/api/analysis/mov-ave-spread/chart?sec_type=index&code=000300"
try:
    with urllib.request.urlopen(url, timeout=10) as resp:
        d = json.load(resp)
except Exception as e:
    print("API error:", e)
    sys.exit(1)

r = d["pairs"][0]["rows"][-1]
print("date:", r["date"])
print("date_of_last_extreme:", r.get("date_of_last_extreme"))
print("gap_since_last_extreme:", r.get("gap_since_last_extreme"))
print("days_since_last_extreme:", r.get("days_since_last_extreme"))

# Count rows with date_of_last_extreme across pair 0
rows = d["pairs"][0]["rows"]
n_total = len(rows)
n_with = sum(1 for x in rows if x.get("date_of_last_extreme"))
print(f"\npair0 rows: {n_total} | with date_of_last_extreme: {n_with}")

# Show a few rows where date == date_of_last_extreme (the actual turning points)
turns = [x for x in rows if x.get("date_of_last_extreme") == x["date"]][-5:]
print("\nLast 5 turning-point rows (date == date_of_last_extreme):")
for x in turns:
    print(f"  {x['date']} | gap={x.get('gap_since_last_extreme')} | days={x.get('days_since_last_extreme')}")
