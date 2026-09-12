WITH cte_stores_apac AS (
  SELECT
    store_id,
    store_name,
    region
  FROM analytics.master.stores
  WHERE
    is_active = TRUE AND region = 'APAC'
), cte_sales_summary AS (
  SELECT
    store_id,
    SUM(sales_amount) AS total_sales,
    COUNT(DISTINCT order_id) AS total_orders
  FROM analytics.sales.orders
  WHERE
    order_date >= '2026-06-01'
  GROUP BY
    store_id
), cte_inventory_current AS (
  SELECT
    store_id,
    SUM(quantity * unit_cost) AS total_inventory_value
  FROM analytics.inventory.stock_levels
  WHERE
    stock_date = CURRENT_DATE
  GROUP BY
    store_id
)
SELECT
  m.store_id,
  m.store_name,
  s.total_sales,
  i.total_inventory_value
FROM cte_stores_apac AS m
JOIN cte_sales_summary AS s
  ON m.store_id = s.store_id
LEFT JOIN cte_inventory_current AS i
  ON m.store_id = i.store_id
WHERE
  s.total_sales > 100000