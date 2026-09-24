# Stellifi Fund I LP — dashboard + warehouse sync

**Dashboard:** `index.html` → https://storage.cloud.google.com/oth-dashboard/stellifi/index.html (portal tile "Stellifi Fund I LP").
Live data comes from the Apps Script over the "Stellifi Venture Capital - Stellifi Fund I" Google Sheet
(`1fbl-MmUZt5gGJR8KmdyjgMLymYUoHSNksifuG-0bwq8`; LP roster = 'Fronted LP Cash' tab, rows above the TOTAL row).
Investor PDF: Bridge card → pick investor → **Export investor PDF** (print → Save as PDF; fund-level data + that LP only).
`MARKS_ASOF` constant (top of the script) = fair-value marks date shown on the page and statement — bump each quarter,
and mirror it in `sync_to_bq.py` / the Cloud Run job env `STELLIFI_MARKS_ASOF`.

**Warehouse:** `sync_to_bq.py` lands a daily snapshot in `oth_silver.stellifi_fund_snapshot`, `stellifi_holdings`,
`stellifi_lps` (MERGE by natural key; history kept). `gold_views.sql` → `oth_gold.v_stellifi_fund_latest`,
`v_stellifi_holdings_latest`, `v_stellifi_lp_positions_latest` (LP MTM = paid_in × net TVPI).
Cloud Run job `stellifi-fund-sync`, Cloud Scheduler `stellifi-fund-sync-daily` 6:15am CT.

```
./deploy.sh              # publish dashboard
./deploy.sh --views      # (re)create gold views
./deploy.sh --sync       # sync now (needs: gcloud auth application-default login)
./deploy.sh --job        # build/deploy the Cloud Run job + scheduler
python3 sync_to_bq.py --dry-run
```
After a first load or schema change: descriptions are set by the script; refresh the catalog with
`python3 ~/oth-intacct/generate_bq_catalog.py`.

Known feed quirks handled: fees arrive negative (abs), the `irr` cell returns net TVPI (rejected → NULL / dashboard falls
back to the 2025-12-31 snapshot IRR), `capital_calls` returns one junk row (dashboard keeps its embedded schedule).
Fix at source = Apps Script cell mapping.
