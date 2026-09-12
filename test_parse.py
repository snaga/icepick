import sqlglot
from sqlglot import exp

with open("Temp/Snowflake_Query_Optimizer_PoC/sample_batch.sql", "r", encoding="utf-8") as f:
    sql_text = f.read()

ast = sqlglot.parse_one(sql_text, read="snowflake")
print("Parsed AST successfully! Root type:", type(ast).__name__)

# Check CTEs
ctes = ast.find_all(exp.CTE)
for cte in ctes:
    print("CTE alias:", cte.alias)
