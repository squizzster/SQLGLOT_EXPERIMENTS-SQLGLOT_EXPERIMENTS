PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS fraud_blacklist;
DROP TABLE IF EXISTS clickstream_events;
DROP TABLE IF EXISTS product_catalog;
DROP TABLE IF EXISTS categories;

CREATE TABLE categories (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    parent_id INTEGER REFERENCES categories(id)
);

CREATE TABLE clickstream_events (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    event_time TEXT NOT NULL,
    payload    TEXT NOT NULL
);

CREATE INDEX idx_clickstream_event_time
    ON clickstream_events(event_time);
CREATE INDEX idx_clickstream_user_time
    ON clickstream_events(user_id, event_time);

CREATE TABLE fraud_blacklist (
    user_id INTEGER PRIMARY KEY
);

CREATE TABLE product_catalog (
    id         INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name       TEXT NOT NULL,
    sales_rank INTEGER NOT NULL
);

INSERT INTO categories(id, name, parent_id) VALUES
    (1, 'Electronics', NULL),
    (2, 'Computers', 1),
    (3, 'Laptops', 2),
    (4, 'Accessories', 1),
    (5, 'Home', NULL),
    (6, 'Kitchen', 5);

INSERT INTO product_catalog(id, category_id, name, sales_rank) VALUES
    (101, 3, 'Pro Laptop', 100),
    (102, 3, 'Budget Laptop', 50),
    (103, 4, 'Wireless Mouse', 90),
    (104, 6, 'Chef Knife', 80);

INSERT INTO fraud_blacklist(user_id) VALUES (999);

-- July history verifies the August 7-day rolling average has lookback data.
INSERT INTO clickstream_events(id, user_id, event_time, payload) VALUES
    (1, 10, '2026-07-30 12:00:00', '{"cart_value": 50, "category_id": 3}'),
    (2, 10, '2026-08-01 10:00:00', '{"cart_value": 100, "category_id": 3}'),
    (3, 10, '2026-08-01 10:10:00', '{"cart_value": 150, "category_id": 3}'),
    (4, 10, '2026-08-01 11:00:01', '{"cart_value": 200, "category_id": 4}'),
    (5, 20, '2026-08-02 09:00:00', '{"cart_value": 80, "category_id": 6}'),
    (6, 20, '2026-08-02 09:20:00', '{"cart_value": 120, "category_id": 6}'),
    (7, 999, '2026-08-03 09:00:00', '{"cart_value": 9999, "category_id": 3}');
