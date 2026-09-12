import sqlglot
from sqlglot import exp

sql = """
SELECT 
    c.customer_name,
    sub.total_spent
FROM analytics.crm.customers AS c
JOIN (
    SELECT 
        customer_id,
        SUM(order_amount) AS total_spent,
        COUNT(*) AS order_count
    FROM analytics.sales.orders
    WHERE order_date >= '2026-01-01'
    GROUP BY customer_id
    HAVING SUM(order_amount) > 1000
) AS sub ON c.customer_id = sub.customer_id
WHERE c.is_active = TRUE;
""".strip()

ast = sqlglot.parse_one(sql, read='snowflake')
print("Original parsed.")

for join in list(ast.find_all(exp.Join)):
    sub = join.this
    if isinstance(sub, exp.Subquery):
        inner_select = sub.this
        cte_name = "customer_order_summary"
        
        # 1. Create new CTE node
        new_cte = sqlglot.parse_one(f"WITH {cte_name} AS ({inner_select.sql(dialect='snowflake')}) SELECT 1", read='snowflake').find(exp.CTE)
        
        # Add to with clause
        if not ast.args.get('with'):
            ast.set('with', exp.With(expressions=[new_cte]))
        else:
            ast.args['with'].append('expressions', new_cte)
            
        # 2. Replace the subquery in join with a simple table reference
        alias_name = sub.alias if sub.alias else cte_name
        new_table = sqlglot.parse_one(f"{cte_name} AS {alias_name}", read='snowflake')
        sub.replace(new_table)

print("\n--- Transformed SQL ---")
print(ast.sql(dialect='snowflake', pretty=True))
