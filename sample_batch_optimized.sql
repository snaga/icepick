/* Sample Snowflake Nightly Batch Mart Query */
WITH raw_events AS (
  SELECT
    event_id,
    user_id,
    event_type,
    event_timestamp,
    payload
  FROM analytics.events.app_events
  WHERE
    event_timestamp >= '2026-08-01'
), daily_user_metrics /* [Issue 1: SNOW-001 Non-Sargable Predicate] Breaks partition pruning! */ AS (
  SELECT
    user_id,
    COUNT(DISTINCT event_id) AS total_events,
    MAX(event_timestamp) AS last_active
  FROM raw_events
  WHERE
    event_timestamp >= '2026-09-01 00:00:00'
    AND event_timestamp < DATEADD(DAY, 1, '2026-09-01')
  GROUP BY
    user_id
), filtered_customers /* [Issue 2: SNOW-003 Redundant Sort in Subquery] Memory waste in CTE! */ AS (
  SELECT
    customer_id,
    customer_tier,
    region
  FROM (
    SELECT
      c.customer_id,
      c.customer_tier,
      c.region
    FROM analytics.crm.customers AS c
    WHERE
      c.is_active = TRUE
  )
), high_value_orders AS (
  SELECT
    o.order_id,
    o.customer_id,
    o.order_amount,
    o.order_date
  FROM (
    SELECT
      order_id,
      customer_id,
      order_amount,
      order_date,
      AVG(order_amount) OVER (
        PARTITION BY customer_id
        ORDER BY order_date
        RANGE BETWEEN INTERVAL '30 DAYS' PRECEDING AND CURRENT ROW
      ) AS rolling_avg_amount
    FROM analytics.sales.orders
  ) AS o
  WHERE
    o.order_amount > o.rolling_avg_amount
)
SELECT
  fc.customer_id,
  fc.customer_tier,
  fc.region,
  COALESCE(dum.total_events, 0) AS daily_event_count,
  hvo.order_id,
  hvo.order_amount
FROM filtered_customers AS fc
LEFT JOIN daily_user_metrics AS dum
  ON fc.customer_id = dum.user_id
LEFT JOIN high_value_orders AS hvo
  ON fc.customer_id = hvo.customer_id
WHERE
  NOT hvo.order_id IS NULL
ORDER BY
  hvo.order_amount DESC