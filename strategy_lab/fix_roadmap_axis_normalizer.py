from pathlib import Path
import py_compile


path = Path(
    r"C:\my\bottest\strategy_lab\roadmap_manager.py"
)

text = path.read_text(
    encoding="utf-8"
)

old = 'r"\\bR(\\d+)([A-Z]?)\\b"'

new = (
    'r"(?:^|[^A-Z0-9])'
    'R(\\d+)([A-Z]?)'
    '(?=$|[^A-Z0-9])"'
)

if old in text:

    text = text.replace(
        old,
        new,
        1,
    )

    path.write_text(
        text,
        encoding="utf-8",
    )

    print(
        "ROADMAP_AXIS_NORMALIZER_PATCH=PASS"
    )

elif new in text:

    print(
        "ROADMAP_AXIS_NORMALIZER_ALREADY_FIXED=True"
    )

else:

    raise RuntimeError(
        "ROADMAP_NORMALIZER_PATTERN_NOT_FOUND"
    )


py_compile.compile(
    str(path),
    doraise=True,
)

print(
    "ROADMAP_MANAGER_SYNTAX=PASS"
)