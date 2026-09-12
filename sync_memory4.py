import json
import datetime

now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
today = datetime.datetime.now().strftime("%Y-%m-%d")

# 1. Update session_events.jsonl
events_path = 'ObsidianVault/99_Assets/Memory/session_events.jsonl'
event = {
    "session_id": "877ef803-6b23-491b-80f3-64c58a199480",
    "timestamp": now_iso,
    "intent": "Author complete SDD steering (product.md, tech.md, structure.md) and specifications (requirements.md, design.md, tasks.md) for snow-opt tool in Temp/Snowflake_Query_Optimizer_PoC/SPECS/",
    "actions": [
        "Created Temp/Snowflake_Query_Optimizer_PoC/SPECS/product.md",
        "Created Temp/Snowflake_Query_Optimizer_PoC/SPECS/tech.md",
        "Created Temp/Snowflake_Query_Optimizer_PoC/SPECS/structure.md",
        "Created Temp/Snowflake_Query_Optimizer_PoC/SPECS/requirements.md (strict EARS syntax)",
        "Created Temp/Snowflake_Query_Optimizer_PoC/SPECS/design.md (Mermaid diagrams, type models, component designs)",
        "Created Temp/Snowflake_Query_Optimizer_PoC/SPECS/tasks.md (Detroit/London test strategies across 5 phases)"
    ],
    "result": "Created 6 comprehensive SDD specification documents with zero encoding errors",
    "task_id": "348c35fc-b255-4052-a47a-b8685141b1c5"
}
with open(events_path, 'a', encoding='utf-8') as f:
    f.write(json.dumps(event, ensure_ascii=False) + '\n')

# 2. Update task_ledger.json
ledger_path = 'ObsidianVault/99_Assets/Memory/task_ledger.json'
with open(ledger_path, 'r', encoding='utf-8') as f:
    ledger = json.load(f)

ledger['last_memory_sync'] = today
completed = ledger.setdefault('completed_tasks', [])
completed.append({
    "task_id": "348c35fc-b255-4052-a47a-b8685141b1c5",
    "title": "SDD: Author Steering & SPEC for snow-opt (Snowflake Query Optimizer)",
    "completed_at": now_iso
})

with open(ledger_path, 'w', encoding='utf-8') as f:
    json.dump(ledger, f, ensure_ascii=False, indent=2)

print('Memory synchronized.')
