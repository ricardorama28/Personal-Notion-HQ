"""Tests del flujo Inbox: idempotencia y cierre con Detected Type correcto."""


def test_create_inbox_entry_idempotent(fake_notion):
    import notion_ops as ops
    # primera llamada: no encuentra existing → crea
    fake_notion.data_sources.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_new"}
    r1 = ops.create_inbox_entry("hola", "whatsapp:+54900", "SMabc")
    assert r1 == {"page_id": "inbox_new", "existing": False}

    # segunda llamada con mismo SID: encuentra existing → no crea
    fake_notion.data_sources.query.return_value = {"results": [{"id": "inbox_new"}]}
    r2 = ops.create_inbox_entry("hola", "whatsapp:+54900", "SMabc")
    assert r2 == {"page_id": "inbox_new", "existing": True}


def test_finalize_inbox_with_write(fake_notion):
    """Si hubo write detectado, marca Auto-processed con el tipo."""
    import notion_ops as ops
    ops.set_inbox("inbox_xyz")
    ops._record_write("Expense")
    r = ops.finalize_inbox("inbox_xyz", "✓ gasto creado")
    assert r["ok"] and r["status"] == "Auto-processed" and r["detected"] == "Expense"
    props = fake_notion.pages.update.call_args.kwargs["properties"]
    assert props["Processing Status"]["select"]["name"] == "Auto-processed"
    assert props["Detected Type"]["select"]["name"] == "Expense"


def test_finalize_inbox_unknown_when_no_writes(fake_notion):
    """Sin writes → Needs review + Unknown."""
    import notion_ops as ops
    ops.set_inbox("inbox_zzz")
    # no llamamos _record_write
    r = ops.finalize_inbox("inbox_zzz", "no se que hacer con esto")
    assert r["status"] == "Needs review" and r["detected"] == "Unknown"
    props = fake_notion.pages.update.call_args.kwargs["properties"]
    assert props["Processing Status"]["select"]["name"] == "Needs review"
    assert props["Detected Type"]["select"]["name"] == "Unknown"


def test_inbox_context_links_creation(fake_notion):
    """Cuando hay inbox activo, create_task agrega la relacion Inbox."""
    import notion_ops as ops
    ops.set_inbox("inbox_active")
    ops.create_task(name="t1")
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Inbox"]["relation"] == [{"id": "inbox_active"}]
    ops.clear_inbox()


def test_inbox_context_skipped_when_inactive(fake_notion):
    import notion_ops as ops
    ops.clear_inbox()
    ops.create_task(name="t2")
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert "Inbox" not in props
