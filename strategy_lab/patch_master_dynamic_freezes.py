from pathlib import Path
import py_compile


path = Path(
    r"C:\my\bottest\strategy_lab\master_handoff.py"
)

text = path.read_text(
    encoding="utf-8"
)


if "def discover_freeze_registry()" not in text:

    anchor = "def build_snapshot() -> dict[str, Any]:\n"

    if anchor not in text:
        raise RuntimeError(
            "MASTER_BUILD_SNAPSHOT_ANCHOR_NOT_FOUND"
        )


    function = '''def discover_freeze_registry() -> list[dict[str, Any]]:

    rows = []

    for path in RES.rglob("*freeze_latest.json"):

        obj = load_json(path)

        if not isinstance(obj, dict):
            continue

        status = obj.get(
            "freeze_status",
            obj.get("status"),
        )

        rows.append({
            "path": str(
                path.relative_to(ROOT)
            ),
            "schema": obj.get("schema"),
            "research_id": obj.get("research_id"),
            "axis": obj.get("axis"),
            "status": status,
            "decision": obj.get("decision"),
            "winner": obj.get("winner"),
            "selected": (
                obj.get("selected_cross_entry_gate_mode")
                or
                obj.get("selected")
            ),
            "search_trials": obj.get("search_trials"),
            "parameter_trials": obj.get("parameter_trials"),
            "next_action": obj.get("next_action"),
        })

    rows.sort(
        key=lambda row: row["path"].lower()
    )

    return rows


'''


    text = text.replace(
        anchor,
        function + anchor,
        1,
    )


snapshot_anchor = (
    '        "frozen_research":\n'
    '            FROZEN_RESEARCH,\n'
)


if (
    '"auto_discovered_freeze_registry"'
    not in text
):

    if snapshot_anchor not in text:
        raise RuntimeError(
            "MASTER_FROZEN_SNAPSHOT_ANCHOR_NOT_FOUND"
        )


    text = text.replace(
        snapshot_anchor,
        snapshot_anchor
        +
        '\n'
        '        "auto_discovered_freeze_registry":\n'
        '            discover_freeze_registry(),\n',
        1,
    )


render_anchor = (
    '    lines += [\n'
    '        "## 5. Research Roadmap",\n'
)


if (
    "### Auto-Discovered Freeze Registry"
    not in text
):

    if render_anchor not in text:
        raise RuntimeError(
            "MASTER_RENDER_ROADMAP_ANCHOR_NOT_FOUND"
        )


    insert = '''    lines += [
        "### Auto-Discovered Freeze Registry",
        "",
    ]

    for row in snapshot[
        "auto_discovered_freeze_registry"
    ]:
        lines.append(
            "- "
            + f"`{row['path']}`"
            + f" | research_id=`{row.get('research_id')}`"
            + f" | status=`{row.get('status')}`"
            + f" | decision=`{row.get('decision')}`"
            + f" | winner=`{row.get('winner')}`"
            + f" | next=`{row.get('next_action')}`"
        )

    lines.append("")

'''


    text = text.replace(
        render_anchor,
        insert + render_anchor,
        1,
    )


path.write_text(
    text,
    encoding="utf-8",
)


py_compile.compile(
    str(path),
    doraise=True,
)


print(
    "MASTER_DYNAMIC_FREEZE_REGISTRY_PATCH=PASS"
)