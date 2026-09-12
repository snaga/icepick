-- Sample Snowflake Nightly Batch Mart Query
WITH raw_events AS (
    SELECT 
        event_id,
        user_id,
        event_type,
        event_timestamp,
        payload
    FROM analytics.events.app_events
    WHERE event_timestamp >= '2026-08-01'
),
-- [Issue 1: SNOW-001 Non-Sargable Predicate] Breaks partition pruning!
daily_user_metrics AS (
    SELECT 
        user_id,
        COUNT(DISTINCT event_id) AS total_events,
        MAX(event_timestamp) AS last_active
    FROM raw_events
    WHERE DATE(event_timestamp) = '2026-09-01'
    GROUP BY user_id
),
-- [Issue 2: SNOW-003 Redundant Sort in Subquery] Memory waste in CTE!
filtered_customers AS (
    SELECT 
        customer_id,
        customer_tier,
        region
    FROM (
        SELECT 
            c.customer_id,
            c.customer_tier,
            c.region
        FROM analytics.crm.customers c
        WHERE c.is_active = TRUE
        ORDER BY c.customer_id ASC
    )
),
-- [Issue 3: SNOW-002 Correlated Subquery] Causes O(N^2) evaluation & Spill!
high_value_orders AS (
    SELECT 
        o.order_id,
        o.customer_id,
        o.order_amount,
        o.order_date
    FROM analytics.sales.orders o
    WHERE o.order_amount > (
        SELECT AVG(sub.order_amount)
        FROM analytics.sales.orders sub
        WHERE sub.customer_id = o.customer_id
          AND sub.order_date >= DATEADD('day', -30, o.order_date)
    )
)
SELECT 
    fc.customer_id,
    fc.customer_tier,
    fc.region,
    COALESCE(dum.total_events, 0) AS daily_event_count,
    hvo.order_id,
    hvo.order_amount
FROM filtered_customers fc
LEFT JOIN daily_user_metrics dum ON fc.customer_id = dum.user_id
LEFT JOIN high_value_orders hvo ON fc.customer_id = hvo.customer_id
WHERE hvo.order_id IS NOT NULL
ORDER BY hvo.order_amount DESC;
