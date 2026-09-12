import sqlglot
from sqlglot import exp

with open('Temp/Snowflake_Query_Optimizer_PoC/sample_batch.sql', 'r', encoding='utf-8') as f:
    sql_text = f.read()

ast = sqlglot.parse_one(sql_text, read='snowflake')

print('--- Rule 1: Non-Sargable WHERE check ---')
for eq in ast.find_all(exp.EQ):
    for side in [eq.this, eq.expression]:
        if isinstance(side, (exp.Anonymous, exp.TsOrDsToDate, exp.Date)):
            print('Found Non-sargable node:', eq.sql(dialect='snowflake'))

print('\n--- Rule 3: Redundant Sort check ---')
for sub in ast.find_all(exp.Subquery):
    sel = sub.find(exp.Select)
    if sel and sel.find(exp.Order) and not sel.find(exp.Limit):
        print('Found redundant ORDER BY in subquery:', sel.find(exp.Order).sql(dialect='snowflake'))

print('\n--- Rule 2: Correlated Subquery check ---')
for where in ast.find_all(exp.Where):
    for sub in where.find_all(exp.Select):
        cols = [c.sql() for c in sub.find_all(exp.Column) if c.table == 'o']
        if cols:
            print('Found correlated subquery referencing outer alias o:', cols)
            print('Subquery snippet:', sub.sql(dialect='snowflake'))
