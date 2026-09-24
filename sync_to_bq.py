"""Stellifi Fund I LP → BigQuery sync.

Pulls the same Apps Script payload the Stellifi Fund I dashboard reads
(fund KPIs, portfolio holdings, LP roster) and lands a dated snapshot in
oth-data-warehouse.oth_silver:

  stellifi_fund_snapshot   one row per snapshot_date (fund-level KPIs)
  stellifi_holdings        one row per (snapshot_date, company)
  stellifi_lps             one row per (snapshot_date, lp_name)

MERGE by natural key — re-running the same day overwrites that day's rows,
never touches history. Gold views (created by deploy.sh) expose the latest
snapshot: oth_gold.v_stellifi_fund_latest, v_stellifi_holdings_latest,
v_stellifi_lp_positions_latest (LP MTM = paid_in × net TVPI, same as the dashboard).

Runs as a Cloud Run job (daily, Cloud Scheduler) or locally:
  python3 sync_to_bq.py            # sync
  python3 sync_to_bq.py --dry-run  # fetch + print, write nothing
Local runs use Application Default Credentials (gcloud auth application-default login).
"""
from __future__ import annotations
import datetime as dt, json, os, sys, urllib.request
from google.cloud import bigquery

PROJECT = os.environ.get("GCP_PROJECT", "oth-data-warehouse")
DS = f"{PROJECT}.oth_silver"
API_URL = ("https://script.google.com/macros/s/AKfycbwJ-JXyf26UsCbPIGeD4zXdXVTDxeJPXZcKb4NzLg_IKxsupB8qZwbFM9AP377pKlg7/exec")
API_KEY = "oth-stellifi-moonshots"
MARKS_ASOF = os.environ.get("STELLIFI_MARKS_ASOF", "2026-06-30")   # fair-value marks date (keep in step with index.html MARKS_ASOF)
SOURCE = "Stellifi Venture Capital - Stellifi Fund I workbook via Apps Script"

SCHEMAS = {
    "stellifi_fund_snapshot": [
        ("snapshot_date", "DATE", "REQUIRED"), ("marks_as_of", "DATE"), ("committed", "FLOAT64"), ("paid_in", "FLOAT64"),
        ("invested", "FLOAT64"), ("fair_value", "FLOAT64"), ("mgmt_fees_cumulative", "FLOAT64"), ("net_value", "FLOAT64"),
        ("moic", "FLOAT64"), ("gross_tvpi", "FLOAT64"), ("net_tvpi", "FLOAT64"), ("net_irr", "FLOAT64"),
        ("lp_count", "INT64"), ("holding_count", "INT64"), ("source", "STRING"), ("loaded_at", "TIMESTAMP")],
    "stellifi_holdings": [
        ("snapshot_date", "DATE", "REQUIRED"), ("company", "STRING", "REQUIRED"), ("sector", "STRING"), ("stage", "STRING"),
        ("invested", "FLOAT64"), ("fair_value", "FLOAT64"), ("moic", "FLOAT64"), ("status", "STRING"), ("loaded_at", "TIMESTAMP")],
    "stellifi_lps": [
        ("snapshot_date", "DATE", "REQUIRED"), ("lp_name", "STRING", "REQUIRED"), ("commitment", "FLOAT64"),
        ("paid_in", "FLOAT64"), ("pct_of_fund", "FLOAT64"), ("loaded_at", "TIMESTAMP")],
}
DESCRIPTIONS = {
    "stellifi_fund_snapshot": "Stellifi Fund I LP fund-level KPIs (committed, paid-in, invested, fair value, fees, MOIC, gross/net TVPI, IRR) — one row per daily snapshot. Source: Stellifi Venture Capital 'Stellifi Fund I' Google Sheet via the dashboard Apps Script. Refresh: daily (Cloud Run job stellifi-fund-sync). Owner: ~/oth-stellifi-dashboard/sync_to_bq.py.",
    "stellifi_holdings": "Stellifi Fund I LP portfolio companies (HelixIntel, Dextall, ResiDesk, PropUp, Tough Leaf, Leni, Incentifind, Soil Connect, Roomie, Arthur, Forty5Park, Doorkee) — invested, fair value, MOIC per company per daily snapshot. Source: Stellifi Fund I Google Sheet via Apps Script. Refresh: daily (stellifi-fund-sync).",
    "stellifi_lps": "Stellifi Fund I LP limited-partner roster — commitment, paid-in and % of fund per LP per daily snapshot (57 LPs incl. OTH Holdings, James C. Vaughan 2014 Trust, DAHG, Boero LLC, MM Seneca, Strategic Investment Trust). Source: 'Fronted LP Cash' tab of the Stellifi Fund I Google Sheet via Apps Script. Refresh: daily (stellifi-fund-sync).",
}


def fetch():
    req = urllib.request.Request(f"{API_URL}?key={API_KEY}", headers={"User-Agent": "stellifi-fund-sync/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    if not payload.get("ok"):
        raise RuntimeError(f"Apps Script returned not-ok: {payload}")
    return payload["data"]


def sanitize_fund(f, lps, holdings):
    """Same guards as the dashboard: fees are a cost (abs), IRR rejected when it is really a TVPI or absurd."""
    fees = abs(f.get("fees") or 0.0)
    irr = f.get("irr")
    if not isinstance(irr, (int, float)) or irr < -1 or irr > 1.5 or \
       any(abs(irr - (f.get(k) or 0)) < 1e-6 for k in ("net_tvpi", "tvpi")):
        irr = None
    return dict(committed=f.get("committed"), paid_in=f.get("paid_in"), invested=f.get("invested"),
                fair_value=f.get("fair_value"), mgmt_fees_cumulative=fees, net_value=f.get("net_value"),
                moic=f.get("moic"), gross_tvpi=f.get("tvpi"), net_tvpi=f.get("net_tvpi"), net_irr=irr,
                lp_count=len(lps), holding_count=len(holdings))


def rows(data, today):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    fund = sanitize_fund(data.get("fund", {}), data.get("lps", []), data.get("holdings", []))
    fund_row = dict(snapshot_date=today, marks_as_of=MARKS_ASOF, source=SOURCE, loaded_at=now, **fund)
    hold = []
    for h in data.get("holdings", []):
        inv, fv = h.get("invested") or 0.0, h.get("fair_value") if h.get("fair_value") is not None else h.get("fv")
        fv = fv or 0.0
        status = "written_off" if fv == 0 else ("flat" if inv and abs(fv / inv - 1) < 0.01 else "active")
        hold.append(dict(snapshot_date=today, company=h["name"], sector=(h.get("sector") or "").strip() or None,
                         stage=(h.get("stage") or "").strip() or None, invested=inv, fair_value=fv,
                         moic=(fv / inv) if inv else None, status=status, loaded_at=now))
    lps = []
    seen = {}
    for l in data.get("lps", []):
        name = (l.get("name") or "").strip()
        if not name:
            continue
        # the sheet carries duplicate names for split commitments (Matthew Muehe ×2) — aggregate on the natural key
        if name in seen:
            r = seen[name]; r["commitment"] += l.get("commitment") or 0; r["paid_in"] += l.get("paid_in") or 0; r["pct_of_fund"] += l.get("pct") or 0
            continue
        seen[name] = dict(snapshot_date=today, lp_name=name, commitment=l.get("commitment") or 0.0,
                          paid_in=l.get("paid_in") or 0.0, pct_of_fund=l.get("pct") or 0.0, loaded_at=now)
    lps = list(seen.values())
    return fund_row, hold, lps


def ensure_tables(bq):
    for name, cols in SCHEMAS.items():
        tid = f"{DS}.{name}"
        schema = [bigquery.SchemaField(c[0], c[1], mode=(c[2] if len(c) > 2 else "NULLABLE")) for c in cols]
        try:
            t = bq.get_table(tid)
            if not t.description:
                t.description = DESCRIPTIONS[name]; bq.update_table(t, ["description"])
        except Exception:
            t = bigquery.Table(tid, schema=schema); t.description = DESCRIPTIONS[name]
            t.time_partitioning = bigquery.TimePartitioning(field="snapshot_date")
            bq.create_table(t); print(f"[sync] created {tid}")


def merge(bq, name, keys, recs):
    if not recs:
        return 0
    tid = f"{DS}.{name}"
    cols = [c[0] for c in SCHEMAS[name]]
    tmp = f"{DS}._stg_{name}_{dt.datetime.now().strftime('%H%M%S%f')}"
    job = bq.load_table_from_json(recs, tmp, job_config=bigquery.LoadJobConfig(
        schema=[bigquery.SchemaField(c[0], c[1]) for c in SCHEMAS[name]], write_disposition="WRITE_TRUNCATE"))
    job.result()
    on = " AND ".join(f"T.{k} = S.{k}" for k in keys)
    upd = ", ".join(f"{c} = S.{c}" for c in cols if c not in keys)
    sql = f"""MERGE `{tid}` T USING `{tmp}` S ON {on}
              WHEN MATCHED THEN UPDATE SET {upd}
              WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) VALUES ({', '.join('S.' + c for c in cols)})"""
    bq.query(sql).result()
    bq.delete_table(tmp, not_found_ok=True)
    return len(recs)


def main():
    dry = "--dry-run" in sys.argv
    data = fetch()
    today = dt.date.today().isoformat()
    fund_row, hold, lps = rows(data, today)
    print(f"[sync] {today} fund: committed {fund_row['committed']:,.0f} paid-in {fund_row['paid_in']:,.0f} "
          f"FV {fund_row['fair_value']:,.0f} netTVPI {fund_row['net_tvpi']:.2f} IRR {fund_row['net_irr']} | "
          f"{len(hold)} holdings | {len(lps)} LPs ({sum(l['commitment'] for l in lps):,.0f} committed)")
    if dry:
        print(json.dumps(fund_row, indent=1, default=str)); return
    bq = bigquery.Client(project=PROJECT)
    ensure_tables(bq)
    n1 = merge(bq, "stellifi_fund_snapshot", ["snapshot_date"], [fund_row])
    n2 = merge(bq, "stellifi_holdings", ["snapshot_date", "company"], hold)
    n3 = merge(bq, "stellifi_lps", ["snapshot_date", "lp_name"], lps)
    print(f"[sync] merged fund {n1} · holdings {n2} · lps {n3} → {DS}")


if __name__ == "__main__":
    main()
