-- Conditional multi-table insert (INSERT ALL ... WHEN ... THEN ... ELSE)
INSERT ALL
  WHEN order_total <= 100000 THEN
    INTO small_orders
  WHEN order_total > 100000 AND order_total <= 200000 THEN
    INTO medium_orders (order_id, order_total)
    VALUES (order_id, order_total)
  ELSE
    INTO large_orders
  SELECT order_id, order_total, customer_id FROM orders;

-- Conditional multi-table insert (INSERT FIRST ... WHEN ... THEN ...)
INSERT FIRST
  WHEN ottl < 100000 THEN
    INTO small_orders VALUES (order_id, ottl)
  WHEN ottl > 100000 THEN
    INTO large_orders VALUES (order_id, ottl)
  SELECT order_id, order_total AS ottl FROM orders;

-- Conditional multi-table insert with several targets per WHEN
INSERT ALL
  WHEN order_total <= 100000 THEN
    INTO small_orders (order_id) VALUES (order_id)
    INTO archived_orders (order_id) VALUES (order_id)
  ELSE
    INTO large_orders (order_id) VALUES (order_id)
  SELECT order_id, order_total FROM orders;

-- Conditional multi-table insert without the ALL/FIRST keyword
INSERT WHEN order_total <= 100000 THEN
  INTO small_orders VALUES (order_id, order_total)
SELECT order_id, order_total FROM orders;

-- Conditional multi-table insert with error logging
INSERT ALL
  WHEN order_total <= 100000 THEN
    INTO small_orders
  ELSE
    INTO large_orders
  SELECT order_id, order_total FROM orders
  LOG ERRORS INTO err$_orders ('conditional') REJECT LIMIT 10;
