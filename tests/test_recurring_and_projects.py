"""Tests de create_project y create_events_recurring.

Cubren las dos capacidades que el bot no tenia: crear proyectos (no habia
escritura contra PROJECTS_DB) y crear series de eventos recurrentes (el
schema de create_event toma una sola fecha, y el tope de iteraciones del
agente hacia imposible emitir ~30 llamadas).
"""
import pytest


# ---------- expansion de fechas (pura, sin Notion) ----------

def test_expand_lunes_y_miercoles():
    import notion_ops as ops
    # 2026-08-17 es lunes; 2026-08-30 es domingo.
    r = ops.expand_weekdays("2026-08-17", "2026-08-30", ["lunes", "miercoles"])
    assert r["dates"] == [
        "2026-08-17", "2026-08-19",
        "2026-08-24", "2026-08-26",
    ]


def test_expand_bordes_inclusivos():
    import notion_ops as ops
    # Arranca y termina justo en un dia que corresponde: ambos entran.
    r = ops.expand_weekdays("2026-08-17", "2026-08-24", ["lunes"])
    assert r["dates"] == ["2026-08-17", "2026-08-24"]


def test_expand_acepta_acentos_y_mayusculas():
    import notion_ops as ops
    con = ops.expand_weekdays("2026-08-17", "2026-08-23", ["Miércoles"])
    sin = ops.expand_weekdays("2026-08-17", "2026-08-23", ["miercoles"])
    assert con["dates"] == sin["dates"] == ["2026-08-19"]


def test_expand_dia_desconocido_es_error():
    import notion_ops as ops
    r = ops.expand_weekdays("2026-08-17", "2026-08-30", ["lunes", "lunez"])
    assert "error" in r and "lunez" in r["error"]


def test_expand_sin_dias_es_error():
    import notion_ops as ops
    assert "error" in ops.expand_weekdays("2026-08-17", "2026-08-30", [])


def test_expand_rango_invertido_es_error():
    import notion_ops as ops
    r = ops.expand_weekdays("2026-08-30", "2026-08-17", ["lunes"])
    assert "error" in r and "invertido" in r["error"]


def test_expand_respeta_el_tope():
    import notion_ops as ops
    # Todos los dias durante 10 años supera MAX_RECURRING_EVENTS.
    r = ops.expand_weekdays(
        "2026-01-01", "2036-01-01",
        ["lunes", "martes", "miercoles", "jueves", "viernes"],
    )
    assert "error" in r
    assert str(ops.MAX_RECURRING_EVENTS) in r["error"]


def test_expand_fecha_impaseable_es_error():
    import notion_ops as ops
    r = ops.expand_weekdays("no soy una fecha ~~", "2026-08-30", ["lunes"])
    assert "error" in r


# ---------- create_events_recurring ----------

def test_recurring_crea_una_pagina_por_fecha(fake_notion):
    import notion_ops as ops
    r = ops.create_events_recurring(
        name="Teorico P2", weekdays=["lunes", "miercoles"],
        date_from="2026-08-17", date_until="2026-08-30",
        event_type="clase",
    )
    assert r["ok"]
    assert r["created"] == 4
    assert fake_notion.pages.create.call_count == 4

    props = fake_notion.pages.create.call_args_list[0].kwargs["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == "Teorico P2"
    assert props["Date"]["date"]["start"] == "2026-08-17"
    # "clase" se traduce al enum real de Notion
    assert props["Type"]["select"]["name"] == "Class"
    parent = fake_notion.pages.create.call_args_list[0].kwargs["parent"]
    assert parent == {"database_id": "db_events"}


def test_recurring_no_duplica_fechas_existentes(fake_notion):
    """Repetir el mismo pedido no debe crear la serie de nuevo."""
    import notion_ops as ops

    fake_notion.databases.query.return_value = {
        "results": [{
            "id": "evt_1",
            "properties": {
                "Name": {"type": "title",
                         "title": [{"plain_text": "Teorico P2"}]},
                "Date": {"date": {"start": "2026-08-17"}},
            },
        }],
        "has_more": False,
    }

    r = ops.create_events_recurring(
        name="Teorico P2", weekdays=["lunes"],
        date_from="2026-08-17", date_until="2026-08-24",
    )
    assert r["created"] == 1
    assert r["created_dates"] == ["2026-08-24"]
    assert r["skipped_existing"] == 1
    assert r["skipped_dates"] == ["2026-08-17"]
    assert fake_notion.pages.create.call_count == 1


def test_recurring_ignora_eventos_de_otro_nombre(fake_notion):
    import notion_ops as ops
    fake_notion.databases.query.return_value = {
        "results": [{
            "id": "evt_1",
            "properties": {
                "Name": {"type": "title",
                         "title": [{"plain_text": "Otra cosa"}]},
                "Date": {"date": {"start": "2026-08-17"}},
            },
        }],
        "has_more": False,
    }
    r = ops.create_events_recurring(
        name="Teorico P2", weekdays=["lunes"],
        date_from="2026-08-17", date_until="2026-08-17",
    )
    assert r["created"] == 1


def test_recurring_proyecto_inexistente_no_crea_nada(fake_notion):
    import notion_ops as ops
    r = ops.create_events_recurring(
        name="Teorico P2", weekdays=["lunes"],
        date_from="2026-08-17", date_until="2026-08-24",
        project="Proyecto Fantasma",
    )
    assert "error" in r
    assert fake_notion.pages.create.call_count == 0


def test_recurring_error_parcial_no_aborta(fake_notion):
    """Si una fecha falla, las demas se crean igual y se reporta cual fallo."""
    import httpx
    import notion_ops as ops
    from notion_client.errors import APIResponseError

    boom = APIResponseError(
        response=httpx.Response(400, json={"message": "nope"}),
        message="nope", code="validation_error")
    fake_notion.pages.create.side_effect = [
        {"id": "ok_1"}, boom, {"id": "ok_3"},
    ]
    r = ops.create_events_recurring(
        name="Teorico P2", weekdays=["lunes"],
        date_from="2026-08-17", date_until="2026-08-31",
    )
    assert r["created"] == 2
    assert len(r["failed"]) == 1
    assert r["failed"][0]["date"] == "2026-08-24"


# ---------- create_project ----------

def test_create_project_usa_la_propiedad_title_real(fake_notion):
    """El esquema de PROJECTS_DB no se asume: se descubre en runtime."""
    import notion_ops as ops
    fake_notion.databases.retrieve.return_value = {
        "properties": {
            "Proyecto": {"type": "title"},
            "Notas": {"type": "rich_text"},
        }
    }
    r = ops.create_project("P2")
    assert r["ok"] and r["project_id"] == "page_fake_id"
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    # usa "Proyecto", no el "Name" hardcodeado del resto de las DB
    assert "Proyecto" in props
    assert props["Proyecto"]["title"][0]["text"]["content"] == "P2"
    parent = fake_notion.pages.create.call_args.kwargs["parent"]
    assert parent == {"database_id": "db_projects"}


def test_create_project_aplica_status_si_existe(fake_notion):
    import notion_ops as ops
    fake_notion.databases.retrieve.return_value = {
        "properties": {
            "Name": {"type": "title"},
            "Estado": {"type": "select",
                       "select": {"options": [{"name": "Activo"},
                                              {"name": "Pausado"}]}},
        }
    }
    ops.create_project("P2", status="activo")  # case-insensitive
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert props["Estado"]["select"]["name"] == "Activo"


def test_create_project_ignora_status_invalido(fake_notion):
    import notion_ops as ops
    fake_notion.databases.retrieve.return_value = {
        "properties": {
            "Name": {"type": "title"},
            "Estado": {"type": "select",
                       "select": {"options": [{"name": "Activo"}]}},
        }
    }
    ops.create_project("P2", status="no existe")
    props = fake_notion.pages.create.call_args.kwargs["properties"]
    assert "Estado" not in props


def test_create_project_nombre_vacio_es_error(fake_notion):
    import notion_ops as ops
    assert "error" in ops.create_project("   ")
    assert fake_notion.pages.create.call_count == 0


def test_create_project_no_duplica(fake_notion):
    import notion_ops as ops
    fake_notion.databases.query.return_value = {
        "results": [{
            "id": "proj_existente",
            "properties": {"Name": {"type": "title",
                                    "title": [{"plain_text": "P2"}]}},
        }]
    }
    r = ops.create_project("p2")
    assert r["existing"] is True
    assert r["project_id"] == "proj_existente"
    assert fake_notion.pages.create.call_count == 0


def test_create_project_invalida_el_cache_de_proyectos(fake_notion):
    """Regresion del lru_cache: sin cache_clear, el proyecto nuevo era
    invisible para create_task/create_event por el resto del proceso."""
    import notion_ops as ops

    # 1) Al principio no hay proyectos: el cache queda poblado vacio.
    fake_notion.databases.query.return_value = {"results": []}
    assert ops.list_projects() == {"projects": []}

    # 2) Se crea P2; despues Notion ya lo devuelve.
    fake_notion.pages.create.return_value = {"id": "proj_p2"}
    ops.create_project("P2")
    fake_notion.databases.query.return_value = {
        "results": [{
            "id": "proj_p2",
            "properties": {"Name": {"type": "title",
                                    "title": [{"plain_text": "P2"}]}},
        }]
    }

    # 3) Sin cache_clear esto seguiria devolviendo [] y None.
    assert ops.list_projects() == {"projects": ["P2"]}
    assert ops._find_project_id("P2") == "proj_p2"


# ---------- registro de tools ----------

@pytest.mark.parametrize("tool_name", ["create_events_recurring",
                                       "create_project"])
def test_tool_registrada_y_despachable(tool_name):
    import tools
    names = {t["name"] for t in tools.TOOLS}
    assert tool_name in names, "falta en TOOLS: el modelo no la ve"
    assert tool_name in tools.DISPATCH, "falta en DISPATCH: execute_tool la rechaza"


def test_schemas_de_las_tools_nuevas():
    import tools
    by_name = {t["name"]: t for t in tools.TOOLS}

    rec = by_name["create_events_recurring"]["input_schema"]
    assert set(rec["required"]) == {"name", "weekdays", "date_from", "date_until"}
    assert rec["properties"]["weekdays"]["type"] == "array"

    proj = by_name["create_project"]["input_schema"]
    assert proj["required"] == ["name"]


def test_agentes_tienen_las_tools_nuevas():
    from agents import capture, planner
    assert "create_project" in capture.CaptureAgent.allowed_tools
    # las series son del planner: pasan por confirmacion bulk
    assert "create_events_recurring" in planner.PlannerAgent.allowed_tools
    assert "create_events_recurring" not in capture.CaptureAgent.allowed_tools


def test_intents_nuevos_ruteados_y_clasificados():
    import orchestrator
    import router

    # create_project ejecuta directo; bulk_create pide confirmacion.
    assert orchestrator.select_agent("create_project") == "capture_agent"
    assert "create_project" in orchestrator.SAFE_AGENT_INTENTS
    assert orchestrator.select_agent("bulk_create") == "planner_agent"
    assert "bulk_create" in orchestrator.BULK_INTENTS

    # el clasificador tiene que poder nombrarlos, si no son plumbing muerto
    assert "create_project" in router.CLASSIFIER_SYSTEM
    assert "bulk_create" in router.CLASSIFIER_SYSTEM
