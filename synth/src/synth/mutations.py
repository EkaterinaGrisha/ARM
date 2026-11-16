"""Mutations: errors planted into a synthetic kit, each with the discrepancies and problem files it should cause.

A mutation of the ``documents`` stage changes the documents before they are drawn, one of the ``files`` stage changes
the drawn files. Each picks a target no other mutation has used and writes into the truth what a correct check
reports: the discrepancy type of the report, the designation, the Block 1 rule when the core has one, and the
consequences, such as the drawings a lost specification leaves outside every specification. The rule ids are text,
since synth does not import the core (ADR-0010); a mutation with no rule yet measures a gap of the check. Group
documents of GOST 2.113 are no targets, but for the group drawing of a part with its variant, which goes whole: one of
them covers several designations, and a mutation plants an error in one. A mutation may plant no discrepancy at all
and test that the check stays quiet: an explanatory note of the kit is no specification without rows.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

from synth import degrade, vocab
from synth.model import Change, Container, Drawing, Output, Row
from synth.product import Part, Unit
from synth.render import draw_drawing, draw_foreign

if TYPE_CHECKING:
    from synth.kits import Kit

Stage = Literal["documents", "files"]


@dataclass(frozen=True)
class Mutation:
    id: str
    stage: Stage
    title: str  # what it does, for the corpus summary
    plant: Callable[[Kit], bool]  # False when the kit has no target for it


CATALOG: dict[str, Mutation] = {}


def _mutation(stage: Stage, title: str) -> Callable[[Callable[[Kit], bool]], Callable[[Kit], bool]]:
    def register(plant: Callable[[Kit], bool]) -> Callable[[Kit], bool]:
        CATALOG[plant.__name__] = Mutation(plant.__name__, stage, title, plant)
        return plant

    return register


def _beside(path: str, suffix: str) -> str:
    """A path next to ``path`` with ``suffix`` added to the stem: it sorts right after the original."""
    p = PurePosixPath(path)
    return p.with_name(f"{p.stem}{suffix}{p.suffix}").as_posix()


def _other_name(name: str, choices: tuple[str, ...]) -> str:
    """A name that differs from ``name`` in words, not only in case or punctuation."""
    others = [c for c in choices if c.lower() != name.lower()]
    return others[0] if others else f"{name} ступенчатый"


def _assembly_drawing(kit: Kit, unit_designation: str) -> Drawing:
    drawing = kit.drawing(f"{unit_designation} СБ")
    assert drawing is not None
    return drawing


# ---------------------------------------------------------------------------------------- documents


@_mutation("documents", "Удалить чертёж детали")
def drop_part_drawing(kit: Kit) -> bool:
    if not (free := kit.free_parts()):
        return False
    _, part, drawing = kit.rng.choice(free)
    kit.drawings.remove(drawing)
    kit.touched.add(part.designation)
    kit.expect("missing_drawing", part.designation, "B1.MISSING.001", "drop_part_drawing")
    return True


@_mutation("documents", "Удалить сборочный чертёж")
def drop_assembly_drawing(kit: Kit) -> bool:
    units = [u for u in kit.free_units() if kit.drawing(f"{u.designation} СБ")]
    if not units:
        return False
    unit = kit.rng.choice(units)
    drawing = _assembly_drawing(kit, unit.designation)
    kit.drawings.remove(drawing)
    kit.touched.add(unit.designation)
    kit.expect("missing_drawing", drawing.designation, "B1.MISSING.002", "drop_assembly_drawing")
    return True


@_mutation("documents", "Удалить спецификацию сборочной единицы")
def drop_assembly_spec(kit: Kit) -> bool:
    """The assembly has only parts, so what it leaves behind is its parts' drawings outside every specification."""
    units = [
        u
        for u in kit.free_units()
        if u.parent is not None and not u.units and not any(p.designation in kit.touched for p in u.parts)
    ]
    if not units:
        return False
    unit = kit.rng.choice(units)
    del kit.specs[unit.designation]
    kit.touched |= {unit.designation, *(p.designation for p in unit.parts)}
    kit.expect("missing_specification", unit.designation, "B1.SPEC.001", "drop_assembly_spec")
    for part in unit.parts:
        if kit.drawing(part.designation):
            kit.expect("orphan_drawing", part.designation, "B1.ORPHAN.001", "drop_assembly_spec")
    return True


@_mutation("documents", "Добавить чертёж, которого нет ни в одной спецификации")
def extra_drawing(kit: Kit) -> bool:
    unit = kit.rng.choice(kit.units())
    kind = kit.rng.choice(vocab.PART_KINDS)
    part = Part(kit.numbering.stray(kind), kit.rng.choice(kind.names), kind, 1)
    kit.drawings.append(kit.part_drawing(unit, part, kit.rng.choice(kind.formats)))
    kit.touched.add(part.designation)
    kit.expect("orphan_drawing", part.designation, "B1.ORPHAN.001", "extra_drawing")
    return True


@_mutation("documents", "Изменить наименование на чертеже детали")
def rename_on_drawing(kit: Kit) -> bool:
    if not (free := kit.free_parts()):
        return False
    _, part, drawing = kit.rng.choice(free)
    drawing.name = _other_name(part.name, part.kind.names)
    kit.touched.add(part.designation)
    kit.expect("name_mismatch", part.designation, "B1.NAME.001", "rename_on_drawing")
    return True


@_mutation("documents", "Изменить наименование в собственной спецификации сборочной единицы")
def rename_assembly_spec(kit: Kit) -> bool:
    units = [u for u in kit.free_units() if u.parent is not None]
    if not units:
        return False
    unit = kit.rng.choice(units)
    kit.specs[unit.designation].name = _other_name(unit.name, vocab.ASSEMBLY_NAMES)
    kit.touched.add(unit.designation)
    kit.expect("name_mismatch", unit.designation, "B1.NAME.002", "rename_assembly_spec")
    return True


@_mutation("documents", "Второй документ с тем же обозначением и другим содержимым")
def duplicate_designation(kit: Kit) -> bool:
    """Same name, litera and changes, another material and mass: only the designation is shared."""
    if not (free := kit.free_parts()):
        return False
    _, part, drawing = kit.rng.choice(free)
    materials = sorted({m.text for kind in vocab.PART_KINDS for m in kind.materials} - {drawing.material})
    twin = _copy(drawing, file=_beside(drawing.file, " (2)"), material=kit.rng.choice(materials))
    twin.mass = f"{float(drawing.mass.replace(',', '.')) * 1.5:.2f}".replace(".", ",")
    kit.drawings.append(twin)
    kit.touched.add(part.designation)
    kit.expect("duplicate_designation", part.designation, "B1.DUP.001", "duplicate_designation")
    return True


@_mutation("documents", "Обозначение детали в другой спецификации с другим наименованием")
def duplicate_position(kit: Kit) -> bool:
    """The other specification names the part otherwise, so its row also differs from the drawing."""
    free = kit.free_parts()
    pairs = [(part, other) for unit, part, _ in free for other in kit.free_units() if other is not unit]
    if not pairs:
        return False
    part, other = kit.rng.choice(pairs)
    drawing = kit.drawing(part.designation)
    assert drawing is not None
    row = Row(
        "Детали", vocab.FORMAT_NAMES[drawing.fmt], "", part.designation, _other_name(part.name, part.kind.names), "1"
    )
    _insert_part_row(kit.specs[other.designation].rows, row)
    kit.touched |= {part.designation, other.designation}
    kit.expect("duplicate_designation", part.designation, "B1.DUP.002", "duplicate_position")
    kit.expect("name_mismatch", part.designation, "B1.NAME.001", "duplicate_position")
    return True


def _copy(drawing: Drawing, **changes: str) -> Drawing:
    """Another drawing like ``drawing``, with lists of its own."""
    twin = dataclasses.replace(drawing, **changes)  # type: ignore[arg-type]
    twin.changes, twin.requirements = list(drawing.changes), list(drawing.requirements)
    twin.annotations, twin.expected_params = list(drawing.annotations), list(drawing.expected_params)
    return twin


def _insert_part_row(rows: list[Row], row: Row) -> None:
    """Put ``row`` last among the parts, give it the next position and move the later positions on by one."""
    sections = [r.section for r in rows]
    index = max(i for i, s in enumerate(sections) if s == "Детали") + 1 if "Детали" in sections else len(rows)
    numbered = [int(r.pos) for r in rows[:index] if r.pos]
    row.pos = str(max(numbered, default=0) + 1)
    for later in rows[index:]:
        if later.pos:
            later.pos = str(int(later.pos) + 1)
    rows.insert(index, row)


@_mutation("documents", "Литера сборочного чертежа ниже литеры спецификации")
def litera_assembly_drawing(kit: Kit) -> bool:
    units = [u for u in kit.free_units() if kit.drawing(f"{u.designation} СБ")]
    if not units:
        return False
    unit = kit.rng.choice(units)
    _assembly_drawing(kit, unit.designation).litera = vocab.lower_litera(kit.litera)
    kit.touched.add(unit.designation)
    kit.expect("revision_mismatch", unit.designation, "B1.LITERA.001", "litera_assembly_drawing")
    return True


@_mutation("documents", "Литера детали ниже литеры сборочной единицы")
def litera_component(kit: Kit) -> bool:
    if not (free := kit.free_parts()):
        return False
    _, part, drawing = kit.rng.choice(free)
    drawing.litera = vocab.lower_litera(kit.litera)
    kit.touched.add(part.designation)
    kit.expect("revision_mismatch", part.designation, "B1.LITERA.002", "litera_component")
    return True


@_mutation("documents", "Вторая версия чертежа с другим номером изменения")
def second_version(kit: Kit) -> bool:
    if not (free := kit.free_parts()):
        return False
    _, part, drawing = kit.rng.choice(free)
    number = max((c.number for c in drawing.changes), default=0) + 1
    change = Change(number, f"{kit.org}.И.{kit.rng.randint(100, 999):04d}-25", f"{kit.rng.randint(1, 12):02d}.25")
    newer = _copy(drawing, file=_beside(drawing.file, f" изм.{number}"))
    newer.changes = [change, *drawing.changes]
    kit.drawings.append(newer)
    kit.touched.add(part.designation)
    kit.expect("duplicate_designation", part.designation, "B1.DUP.001", "second_version")
    kit.expect("revision_mismatch", part.designation, "B1.VERSION.001", "second_version")
    return True


@_mutation("documents", "Формат в спецификации не совпадает с форматом чертежа")
def format_in_spec(kit: Kit) -> bool:
    if not (free := kit.free_parts()):
        return False
    unit, part, _ = kit.rng.choice(free)
    row = next(r for r in kit.specs[unit.designation].rows if r.designation == part.designation)
    row.fmt = "А3" if row.fmt == "А4" else "А4"
    kit.touched.add(part.designation)
    kit.expect("format_mismatch", part.designation, "B1.FORMAT.001", "format_in_spec")
    return True


@_mutation("documents", "Удалить последний лист многолистового чертежа")
def drop_sheet(kit: Kit) -> bool:
    """The title block still counts the sheet, and the parameters drawn on it are gone with it. Of a drawing kept one
    sheet per file the file of sheet 2 goes, as a sheet of ARM gets lost: with three sheets or more the numbering of
    the rest has a gap."""
    free = [(part, d) for _, part, d in kit.free_parts(vector=True, sheet_files=True) if d.more_sheets]
    if not free:
        return False
    part, drawing = kit.rng.choice(free)
    if drawing.output.sheet_files:
        drawing.dropped_sheet = 2
    else:
        drawing.missing_sheets = 1
    gone = f"sheet{drawing.dropped_sheet or drawing.sheets}:"
    kept = [(p, k) for p, k in zip(drawing.expected_params, drawing.param_keys, strict=True) if not k.startswith(gone)]
    drawing.expected_params, drawing.param_keys = [p for p, _ in kept], [k for _, k in kept]
    kit.touched.add(part.designation)
    kit.expect("sheets_missing", part.designation, "B1.SHEETS.001", "drop_sheet")
    return True


def _vector_units(kit: Kit) -> list[Unit]:
    """Free assemblies whose specification is drawn as a vector: the rules of positions and of the first use read
    the rows of a text layer only (``positions.methods`` of the core)."""
    return [
        u for u in kit.free_units() if kit.specs[u.designation].output.kind == "vector" and not _rows_touched(kit, u)
    ]


def _rows_touched(kit: Kit, unit: Unit) -> bool:
    """Whether a mutation has used a row of the specification of ``unit``."""
    return any(r.designation in kit.touched for r in kit.specs[unit.designation].rows if r.designation)


@_mutation("documents", "Записать сборочную единицу в раздел «Детали»")
def section_of_assembly(kit: Kit) -> bool:
    """The row of an assembly with a specification of its own moves to the parts of its parent (GOST 2.106, 3.5)."""
    units = [u for u in kit.free_units() if u.parent is not None and u.parent.designation not in kit.touched]
    if not units:
        return False
    unit = kit.rng.choice(units)
    rows = kit.specs[unit.parent.designation].rows  # type: ignore[union-attr]
    row = next(r for r in rows if r.designation == unit.designation and r.section == "Сборочные единицы")
    rows.remove(row)
    row.section = "Детали"
    _insert_part_row(rows, row)
    kit.touched.add(unit.designation)
    kit.expect("section_mismatch", unit.designation, "B1.SECTION.001", "section_of_assembly")
    return True


@_mutation("documents", "Повторить номер позиции в спецификации")
def repeat_position(kit: Kit) -> bool:
    units = [u for u in _vector_units(kit) if sum(1 for r in kit.specs[u.designation].rows if r.pos) >= 2]
    if not units:
        return False
    unit = kit.rng.choice(units)
    numbered = [r for r in kit.specs[unit.designation].rows if r.pos]
    first, second = kit.rng.sample(numbered, 2)
    second.pos = first.pos
    kit.touched.add(unit.designation)
    kit.expect("position_anomaly", unit.designation, "B1.POS.001", "repeat_position")
    return True


@_mutation("documents", "Убрать количество у строки спецификации")
def drop_quantity(kit: Kit) -> bool:
    """A purchased item or a material: no other document depends on the quantity of its row."""
    components = ("Стандартные изделия", "Прочие изделия", "Материалы")
    units = [u for u in _vector_units(kit) if any(r.section in components for r in kit.specs[u.designation].rows)]
    if not units:
        return False
    unit = kit.rng.choice(units)
    row = kit.rng.choice([r for r in kit.specs[unit.designation].rows if r.section in components])
    row.qty = ""
    kit.touched.add(unit.designation)
    kit.expect("position_anomaly", unit.designation, "B1.POS.002", "drop_quantity")
    return True


@_mutation("documents", "Убрать обозначение у строки детали")
def drop_row_designation(kit: Kit) -> bool:
    """The drawing of the part is then named by no specification."""
    free = [(u, p, d) for u, p, d in kit.free_parts() if u in _vector_units(kit)]
    if not free:
        return False
    unit, part, _ = kit.rng.choice(free)
    row = next(r for r in kit.specs[unit.designation].rows if r.designation == part.designation)
    row.designation = ""
    kit.touched |= {unit.designation, part.designation}
    kit.expect("position_anomaly", unit.designation, "B1.POS.003", "drop_row_designation")
    kit.expect("orphan_drawing", part.designation, "B1.ORPHAN.001", "drop_row_designation")
    return True


@_mutation("documents", "«Перв. примен.» чертежа детали называет другую сборочную единицу")
def first_use_elsewhere(kit: Kit) -> bool:
    """The other assembly does not list the part: its specification is drawn as a vector, so its rows are read
    whole."""
    vector = _vector_units(kit)
    pairs = [
        (part, drawing, other)
        for unit, part, drawing in kit.free_parts(vector=True)
        if drawing.output.kind == "vector"
        for other in vector
        if other is not unit and all(r.designation != part.designation for r in kit.specs[other.designation].rows)
    ]
    if not pairs:
        return False
    part, drawing, other = kit.rng.choice(pairs)
    drawing.first_use = other.designation
    kit.touched |= {part.designation, other.designation}
    kit.expect("first_use_mismatch", part.designation, "B1.FIRSTUSE.001", "first_use_elsewhere")
    return True


@_mutation("documents", "Вторая спецификация с тем же обозначением")
def duplicate_spec(kit: Kit) -> bool:
    """A newer version of the specification of an assembly beside the older one: another change, the same rows."""
    units = [
        u
        for u in kit.free_units()
        if u.parent is not None and not _rows_touched(kit, u) and not kit.specs[u.designation].output.sheet_files
    ]
    if not units:
        return False
    unit = kit.rng.choice(units)
    spec = kit.specs[unit.designation]
    number = max((c.number for c in spec.changes), default=0) + 1
    change = Change(number, f"{kit.org}.И.{kit.rng.randint(100, 999):04d}-25", f"{kit.rng.randint(1, 12):02d}.25")
    newer = dataclasses.replace(spec, file=_beside(spec.file, f" изм.{number}"), changes=[change, *spec.changes])
    newer.rows = [dataclasses.replace(r, quantities=dict(r.quantities)) for r in spec.rows]
    kit.more_specs.append(newer)
    kit.touched.add(unit.designation)
    kit.expect("duplicate_designation", unit.designation, "B1.DUP.003", "duplicate_spec")
    return True


@_mutation("documents", "Чертёж детали с обозначением сборочной единицы")
def part_drawing_of_unit(kit: Kit) -> bool:
    """The assembly keeps its specification and its assembly drawing; a part drawing gets its designation too."""
    units = [u for u in kit.free_units() if u.parent is not None]
    if not units:
        return False
    unit = kit.rng.choice(units)
    kind = kit.rng.choice(vocab.PART_KINDS)
    part = Part(unit.designation, kit.rng.choice(kind.names), kind, 1)
    drawing = kit.part_drawing(unit, part, kit.rng.choice(kind.formats))
    taken = {s.file for s in kit.specs.values()} | {d.file for d in kit.drawings}
    while drawing.file in taken:
        drawing.file = _beside(drawing.file, " деталь")
    kit.drawings.append(drawing)
    kit.touched.add(unit.designation)
    kit.expect("duplicate_designation", unit.designation, "B1.DUP.004", "part_drawing_of_unit")
    return True


@_mutation("documents", "Удалить групповой чертёж детали и её исполнения")
def drop_group_drawing(kit: Kit) -> bool:
    """The drawing of a part and its variant «-01» in one, GOST 2.113: both positions lose their drawing."""
    found = []
    for unit in kit.units():
        if unit.variants:
            continue
        for part in unit.parts:
            members = [p for p in unit.parts if p.group == part.designation]
            if part.group != part.designation or any(p.designation in kit.touched for p in members):
                continue
            if (drawing := kit.drawing(part.designation)) is not None and drawing.variants:
                found.append((members, drawing))
    if not found:
        return False
    members, drawing = kit.rng.choice(found)
    kit.drawings.remove(drawing)
    for part in members:
        kit.touched.add(part.designation)
        kit.expect("missing_drawing", part.designation, "B1.MISSING.001", "drop_group_drawing")
    return True


@_mutation("documents", "Положить пояснительную записку из раздела «Документация»")
def text_document(kit: Kit) -> bool:
    """An explanatory note on form 2, written in «Документация» of its assembly: no discrepancy. A check that takes a
    document on form 2 for a specification reports its row as missing and the drawings outside every specification
    as preliminary (ADR-0029)."""
    units = [u for u in kit.free_units() if not _rows_touched(kit, u)]
    if not units:
        return False
    unit = kit.rng.choice(units)
    spec = kit.specs[unit.designation]
    designation, name = f"{unit.designation} ПЗ", "Пояснительная записка"
    lines = [
        "1 Назначение и область применения",
        f"{unit.name} {unit.designation} входит в изделие {kit.root.designation} «{kit.root.name}».",
        "2 Техническая характеристика",
        "Конструкция и параметры приведены на сборочном чертеже и в спецификации.",
    ]
    note = dataclasses.replace(
        spec,
        file=kit.path(unit, designation, name, ".pdf"),
        designation=designation,
        name=name,
        rows=[],
        output=Output(),
        group="",
        variants=[],
        text=lines,
    )
    after = next(i for i, r in enumerate(spec.rows) if r.section == "Документация") + 1
    spec.rows.insert(after, Row("Документация", "А4", "", designation, name))
    kit.texts.append(note)
    kit.touched.add(unit.designation)
    return True


# ---------------------------------------------------------------------------------------- files


@_mutation("files", "Подложить посторонний документ")
def foreign_document(kit: Kit) -> bool:
    path = "Акт приёмки КД.pdf"
    if path in kit.files:
        return False
    kit.files[path] = draw_foreign(kit.root.designation, kit.root.name)
    kit.problem_files[path] = "foreign"
    return True


def _unread_drawing(
    kit: Kit, drawing: Drawing, data: bytes, status: str, mutation: str, path: str | None = None
) -> None:
    """The drawing's file becomes ``data`` the check cannot read: a problem file, and the part has no drawing."""
    del kit.files[drawing.file]
    drawing.file = path or drawing.file
    kit.files[drawing.file] = data
    kit.unread.add(drawing.file)
    kit.problem_files[drawing.file] = status
    kit.touched.add(drawing.designation)
    kit.expect("missing_drawing", drawing.designation, "B1.MISSING.001", mutation)


@_mutation("files", "Повредить файл чертежа: оборвать на середине")
def damage_file(kit: Kit) -> bool:
    if not (free := kit.free_parts(vector=True)):
        return False
    _, _, drawing = kit.rng.choice(free)
    data = kit.files[drawing.file]
    _unread_drawing(kit, drawing, data[: len(data) // 2], "damaged", "damage_file")
    return True


@_mutation("files", "Защитить чертёж паролем")
def encrypt_file(kit: Kit) -> bool:
    if not (free := kit.free_parts(vector=True)):
        return False
    _, _, drawing = kit.rng.choice(free)
    _unread_drawing(kit, drawing, draw_drawing(drawing, password="kd"), "encrypted", "encrypt_file")
    return True


@_mutation("files", "Заменить чертёж нечитаемым сканом")
def unreadable_scan(kit: Kit) -> bool:
    if not (free := kit.free_parts()):
        return False
    _, _, drawing = kit.rng.choice(free)
    container: Container = "jpg" if drawing.sheets - drawing.missing_sheets == 1 else "pdf"
    output = Output("scan", container, "unreadable")
    data, _ = degrade.capture(draw_drawing(drawing), output, kit.scan_rng.getrandbits(32))
    path = PurePosixPath(drawing.file).with_suffix(f".{container}").as_posix()
    _unread_drawing(kit, drawing, data, "unreadable", "unreadable_scan", path)
    return True


@_mutation("files", "Положить побайтовую копию документа")
def byte_copy(kit: Kit) -> bool:
    """The copy sorts after the original, so the check keeps the original and marks the copy."""
    free = kit.free_parts()
    if not free:
        return False
    _, part, drawing = kit.rng.choice(free)
    copy = _beside(drawing.file, "_копия")
    kit.files[copy] = kit.files[drawing.file]
    kit.problem_files[copy] = "duplicate_file"
    kit.touched.add(part.designation)
    return True


@_mutation("files", "Положить файл неподдерживаемого формата")
def unsupported_file(kit: Kit) -> bool:
    # a CAD file of the kit; a service file of the system, as «Thumbs.db», is no document of the kit at all
    name, head = kit.rng.choice((("Сборка.step", b"ISO-10303-21;"), ("Сборка.dwg", b"AC1027")))
    if name in kit.files:
        return False
    kit.files[name] = head + kit.rng.randbytes(2000)
    kit.problem_files[name] = "unsupported"
    return True
