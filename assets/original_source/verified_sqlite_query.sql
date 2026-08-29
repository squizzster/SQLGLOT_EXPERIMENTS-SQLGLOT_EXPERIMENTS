WITH RECURSIVE
category_hierarchy AS (
    SELECT
        id AS category_id,
        name AS category_name,
        CAST(name AS TEXT) AS category_path
    FROM categories
    WHERE parent_id IS NULL

    UNION ALL

    SELECT
        c.id,
        c.name,
        ch.category_path || ' > ' || c.name
    FROM categories AS c
    JOIN category_hierarchy AS ch
      ON c.parent_id = ch.category_id
),

-- Read enough history to make the 7-day rolling window correct for August.
parsed_events AS (
    SELECT
        e.user_id,
        e.event_time,
        CAST(json_extract(e.payload, '$.cart_value') AS NUMERIC) AS cart_value,
        CAST(json_extract(e.payload, '$.category_id') AS INTEGER) AS category_id,
        unixepoch(e.event_time) AS event_epoch
    FROM clickstream_events AS e
    WHERE e.event_time >= '2026-07-25'
      AND e.event_time <  '2026-09-01'
      AND NOT EXISTS (
          SELECT 1
          FROM fraud_blacklist AS b
          WHERE b.user_id = e.user_id
      )
),

event_gaps AS (
    SELECT
        pe.*,
        pe.event_epoch - LAG(pe.event_epoch) OVER (
            PARTITION BY pe.user_id
            ORDER BY pe.event_epoch, pe.event_time
        ) AS seconds_since_last_event
    FROM parsed_events AS pe
),

sessionized_data AS (
    SELECT
        eg.*,
        1 + SUM(
            CASE
                WHEN eg.seconds_since_last_event > 1800 THEN 1
                ELSE 0
            END
        ) OVER (
            PARTITION BY eg.user_id
            ORDER BY eg.event_epoch, eg.event_time
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS session_id,
        AVG(eg.cart_value) OVER (
            PARTITION BY eg.user_id
            ORDER BY eg.event_epoch
            RANGE BETWEEN 604800 PRECEDING AND CURRENT ROW
        ) AS rolling_7d_avg_spend
    FROM event_gaps AS eg
),

top_products_per_category AS (
    SELECT
        category_id,
        name AS top_product_name,
        ROW_NUMBER() OVER (
            PARTITION BY category_id
            ORDER BY sales_rank DESC, name ASC
        ) AS rn
    FROM product_catalog
),

-- Report only August rows after calculating history-dependent windows.
august_detail AS (
    SELECT
        s.user_id,
        s.session_id,
        ch.category_path,
        tp.top_product_name,
        COUNT(*) AS total_events,
        MAX(s.rolling_7d_avg_spend) AS peak_rolling_spend,
        SUM(s.cart_value) AS session_category_revenue
    FROM sessionized_data AS s
    JOIN category_hierarchy AS ch
      ON s.category_id = ch.category_id
    LEFT JOIN top_products_per_category AS tp
      ON s.category_id = tp.category_id
     AND tp.rn = 1
    WHERE s.event_time >= '2026-08-01'
      AND s.event_time <  '2026-09-01'
    GROUP BY
        s.user_id,
        s.session_id,
        ch.category_path,
        tp.top_product_name
),

-- SQLite has no GROUP BY ROLLUP, so construct all ROLLUP levels explicitly.
rollup_rows AS (
    -- Detail: user, session, category, product
    SELECT
        user_id,
        session_id,
        category_path,
        top_product_name,
        total_events,
        peak_rolling_spend,
        session_category_revenue,
        0 AS rollup_level
    FROM august_detail

    UNION ALL

    -- Category subtotal: user, session, category
    SELECT
        user_id,
        session_id,
        category_path,
        NULL AS top_product_name,
        SUM(total_events),
        MAX(peak_rolling_spend),
        SUM(session_category_revenue),
        1 AS rollup_level
    FROM august_detail
    GROUP BY user_id, session_id, category_path

    UNION ALL

    -- Session subtotal: user, session
    SELECT
        user_id,
        session_id,
        NULL AS category_path,
        NULL AS top_product_name,
        SUM(total_events),
        MAX(peak_rolling_spend),
        SUM(session_category_revenue),
        2 AS rollup_level
    FROM august_detail
    GROUP BY user_id, session_id

    UNION ALL

    -- User subtotal: user
    SELECT
        user_id,
        NULL AS session_id,
        NULL AS category_path,
        NULL AS top_product_name,
        SUM(total_events),
        MAX(peak_rolling_spend),
        SUM(session_category_revenue),
        3 AS rollup_level
    FROM august_detail
    GROUP BY user_id

    UNION ALL

    -- Grand total
    SELECT
        NULL AS user_id,
        NULL AS session_id,
        NULL AS category_path,
        NULL AS top_product_name,
        SUM(total_events),
        MAX(peak_rolling_spend),
        SUM(session_category_revenue),
        4 AS rollup_level
    FROM august_detail
)

SELECT
    user_id,
    session_id,
    category_path,
    top_product_name,
    total_events,
    peak_rolling_spend,
    session_category_revenue
FROM rollup_rows
ORDER BY
    user_id IS NULL,
    user_id,
    session_id IS NULL,
    session_id,
    rollup_level,
    category_path,
    top_product_name;
