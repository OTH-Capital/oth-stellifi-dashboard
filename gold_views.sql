-- Latest-snapshot views for the Stellifi Fund I data (run by deploy.sh --views)
CREATE OR REPLACE VIEW `oth-data-warehouse.oth_gold.v_stellifi_fund_latest`
OPTIONS(description="Stellifi Fund I LP — latest daily fund KPI snapshot (committed, paid-in, fair value, gross/net TVPI, MOIC, IRR). Source oth_silver.stellifi_fund_snapshot; refreshed daily by stellifi-fund-sync.") AS
SELECT * FROM `oth-data-warehouse.oth_silver.stellifi_fund_snapshot`
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `oth-data-warehouse.oth_silver.stellifi_fund_snapshot`);

CREATE OR REPLACE VIEW `oth-data-warehouse.oth_gold.v_stellifi_holdings_latest`
OPTIONS(description="Stellifi Fund I LP portfolio companies at the latest snapshot — invested, fair value, MOIC, status, share of portfolio FV. Source oth_silver.stellifi_holdings; daily.") AS
WITH h AS (
  SELECT * FROM `oth-data-warehouse.oth_silver.stellifi_holdings`
  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `oth-data-warehouse.oth_silver.stellifi_holdings`))
SELECT h.*, SAFE_DIVIDE(h.fair_value, SUM(h.fair_value) OVER ()) AS pct_of_fv FROM h;

CREATE OR REPLACE VIEW `oth-data-warehouse.oth_gold.v_stellifi_lp_positions_latest`
OPTIONS(description="Stellifi Fund I LP limited partners at the latest snapshot with mark-to-market = paid_in × fund net TVPI (same method as the Stellifi dashboard / Partner NW bridge). Includes OTH Holdings LLC, Boero LLC, MM Seneca, Strategic Investment Trust. Source oth_silver.stellifi_lps + stellifi_fund_snapshot; daily.") AS
WITH f AS (SELECT * FROM `oth-data-warehouse.oth_gold.v_stellifi_fund_latest`),
     l AS (SELECT * FROM `oth-data-warehouse.oth_silver.stellifi_lps`
           WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `oth-data-warehouse.oth_silver.stellifi_lps`))
SELECT l.snapshot_date, l.lp_name, l.commitment, l.paid_in, l.pct_of_fund,
       l.commitment - l.paid_in AS unfunded,
       f.net_tvpi, f.gross_tvpi, f.marks_as_of,
       l.paid_in * f.net_tvpi AS mtm_net, l.paid_in * f.gross_tvpi AS mtm_gross
FROM l CROSS JOIN f;
