"""Smoke tests para cada tool de Notion: arman el payload correcto."""
from datetime import date


def test_create_task(fake_notion):
    import notion_ops as ops
    r = ops.create_task(name="Comprar pan", due="2026-05-20", priority="High",
                        task_type="Task")
    assert r["ok"] and r["type"] == "Task"
    call = fake_notion.pages.create.call_args
    props = call.kwargs["properties"]
    assert call.kwargs["parent"] == {"database_id": "db_tasks"}
    assert props["Name"]["title"][0]["text"]["content"] == "Comprar pan"
    assert props["Status"]["select"]["name"] == "To Do"
    assert props["Type"]["select"]["name"] == "Task"
    assert props["Priority"]["select"]["name"] == "High"
    assert props["Due"]["date"]["start"] == "2026-05-20"


def test_create_reminder(fake_notion):
    import notion_ops as ops
    r = ops.create_task(name="Llamar dentista", task_type="Reminder")
    assert r["type"] == "Reminder"
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Type"]["select"]["name"] == "Reminder"


def test_add_note(fake_notion):
    import notion_ops as ops
    r = ops.add_note(content="idea para TP IA", note_type="Idea",
                     tags=["Tech"])
    assert r["ok"]
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Type"]["select"]["name"] == "Idea"
    assert props["Tags"]["multi_select"] == [{"name": "Tech"}]


def test_add_expense(fake_notion):
    import notion_ops as ops
    r = ops.add_expense(name="super", amount=450, category="Supermercado",
                        method="Débito")
    assert r["ok"] and r["amount"] == 450
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Amount"]["number"] == 450.0
    assert props["Category"]["select"]["name"] == "Supermercado"
    assert props["Method"]["select"]["name"] == "Débito"
    assert props["Date"]["date"]["start"] == date.today().isoformat()


def test_add_meal(fake_notion):
    import notion_ops as ops
    r = ops.add_meal(name="milanesa con pure", meal_type="Almuerzo")
    assert r["ok"]
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Meal type"]["select"]["name"] == "Almuerzo"


def test_log_habit(fake_notion):
    import notion_ops as ops
    # poblar el indice de habitos
    fake_notion.data_sources.query.return_value = {
        "results": [{
            "id": "habit_run",
            "properties": {
                "Name": {"type": "title",
                         "title": [{"plain_text": "Ejercicio"}]},
                "Active": {"checkbox": True},
            },
        }]
    }
    ops._habits_index.cache_clear()
    r = ops.log_habit(habit_name="ejercicio")
    assert r["ok"] and r["habit"] == "Ejercicio"
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Habit"]["relation"] == [{"id": "habit_run"}]
    assert props["Status"]["select"]["name"] == "Done"


def test_log_habit_unknown(fake_notion):
    import notion_ops as ops
    fake_notion.data_sources.query.return_value = {"results": []}
    ops._habits_index.cache_clear()
    r = ops.log_habit(habit_name="cosa_inexistente")
    assert "error" in r


def test_create_event(fake_notion):
    import notion_ops as ops
    r = ops.create_event(name="Parcial algebra", date="2026-05-22",
                         event_type="Exam")
    assert r["ok"] and r["type"] == "Exam"
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Date"]["date"]["start"] == "2026-05-22"


def test_create_event_spanish_type(fake_notion):
    import notion_ops as ops
    r = ops.create_event(name="Parcial", date="2026-05-22",
                         event_type="parcial")
    assert r["type"] == "Exam"


def test_create_event_bad_date(fake_notion):
    import notion_ops as ops
    r = ops.create_event(name="x", date="esto-no-es-fecha-xxx")
    assert "error" in r


def test_execute_tool_unknown():
    from tools import execute_tool
    r = execute_tool("herramienta_inexistente", {})
    assert "error" in r
