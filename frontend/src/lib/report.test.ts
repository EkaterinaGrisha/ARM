import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { Parameter, Report, TreeNode } from "../api/types";
import { formatBytes, parameterValue, plural } from "./format";
import { branchIds, buildIndex, filterTree, matchesQuery, sameBox, sheetFormats } from "./report";

const demo = JSON.parse(readFileSync(new URL("../../public/demo/report.json", import.meta.url), "utf-8")) as Report;

const node = (id: string, designation: string, children: TreeNode[] = [], status: TreeNode["status"] = "ok"): TreeNode => ({
  id,
  designation,
  name: designation,
  kind: children.length ? "assembly" : "part",
  drawing_document_ids: [],
  discrepancy_ids: [],
  status,
  children,
});

describe("дерево изделия", () => {
  const tree = [node("1", "А.000", [node("2", "А.100", [node("3", "А.101", [], "missing")]), node("4", "А.001")])];

  it("фильтр сохраняет предков найденного узла", () => {
    const filtered = filterTree(tree, (n) => n.status === "missing");
    expect(filtered).toHaveLength(1);
    expect(filtered[0].children.map((c) => c.id)).toEqual(["2"]);
    expect(filtered[0].children[0].children.map((c) => c.id)).toEqual(["3"]);
  });

  it("собирает идентификаторы раскрываемых узлов", () => {
    expect(branchIds(tree)).toEqual(["1", "2"]);
  });
});

describe("индекс отчёта", () => {
  const index = buildIndex(demo);

  it("каждое значение с источником попадает в список своего документа", () => {
    const part = demo.parts.find((p) => p.parameters.length)!;
    const entries = index.sourcesByDoc.get(part.document_id) ?? [];
    expect(entries.some((e) => e.group === "Основная надпись" && e.label === "Обозначение")).toBe(true);
    expect(entries.filter((e) => e.group === "Параметры")).toHaveLength(part.parameters.filter((p) => p.source).length);
  });

  it("находит документы по обозначению", () => {
    expect(index.docsByDesignation.get("АРМ.303411.201")).toHaveLength(2);
  });

  it("узлы дерева индексируются по id", () => {
    const missing = [...index.nodes.values()].filter((n) => n.status === "missing");
    expect(missing.map((n) => n.designation).sort()).toEqual(["АРМ.303411.004", "АРМ.303411.103", "АРМ.303411.300"]);
  });
});

describe("форматирование", () => {
  it("значения параметров", () => {
    const base = { id: "p", category: "hardness", name: "HRC", text: "", general: false, critical: false } as Parameter;
    expect(parameterValue({ ...base, value_min: 58, value_max: 62, unit: "HRC" })).toBe("58…62 HRC");
    expect(parameterValue({ ...base, value: 0.8, unit: "мкм" })).toBe("0,8 мкм");
    expect(parameterValue(base)).toBe("—");
  });

  it("склонение и размеры", () => {
    expect(plural(1, "файл", "файла", "файлов")).toBe("файл");
    expect(plural(3, "файл", "файла", "файлов")).toBe("файла");
    expect(plural(11, "файл", "файла", "файлов")).toBe("файлов");
    expect(formatBytes(1536)).toBe("1,5 КБ");
  });

  it("поиск без учёта регистра и ё", () => {
    expect(matchesQuery("колесный", "Редуктор колёсный")).toBe(true);
    expect(matchesQuery("", null)).toBe(true);
    expect(matchesQuery("вал", "Корпус")).toBe(false);
  });

  it("сравнение областей с допуском", () => {
    expect(sameBox([0.1, 0.1, 0.2, 0.2], [0.1001, 0.1, 0.2, 0.2])).toBe(true);
    expect(sameBox([0.1, 0.1, 0.2, 0.2], [0.2, 0.1, 0.3, 0.2])).toBe(false);
  });
});

describe("форматы листов", () => {
  const page = (number: number, format?: string | null) => ({ number, width: 595, height: 842, unit: "pt" as const, has_text: true, format });
  const doc = { ...demo.documents[0], pages: [page(1, "A3"), page(2, "A4"), page(3, "A3"), page(4, null)] };

  it("перечисляет форматы страниц по одному разу", () => {
    expect(sheetFormats(doc)).toBe("A3, A4");
    expect(sheetFormats({ ...doc, pages: [page(1)] })).toBe("");
  });
});
